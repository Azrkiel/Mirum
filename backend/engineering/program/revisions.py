"""Pure F06 records for transactional program revisions.

Revision records, the state-machine table, journal events, typed refusals and bounded payloads.
Nothing here touches a store, job, bundle, native kernel, web framework or provider: it imports
only pydantic, the stdlib and the pure program/contracts modules. Every validator fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, ConfigDict, Field, StringConstraints, field_validator, model_validator,
)

from engineering.contracts import BundleIdentity, ContractModel
from engineering.contracts import Sha256 as _Sha256

from .hashing import canonical_json
from .ir import ProgramIR
from .kernel import ExecutionLockV0_2
from .versions import PROGRAM_JOURNAL_VERSION, PROGRAM_REVISION_VERSION

__all__ = [
    'REVISION_KINDS', 'REVISION_STATES', 'TERMINAL_STATES', 'TRANSITIONS', 'JOURNAL_EVENTS',
    'REFUSAL_REASONS', 'MAX_SOURCE_BYTES', 'MAX_IR_JSON_BYTES', 'MAX_LOCK_JSON_BYTES',
    'MAX_DETAIL_KEYS', 'MAX_DETAIL_VALUE_CHARS', 'can_transition', 'RevisionRefusal',
    'ProgramRevision', 'RevisionPayload', 'JournalEntry', 'RevisionView', 'build_payload',
]

REVISION_KINDS = ('edit', 'restore', 'rebuild')
REVISION_STATES = ('staged', 'running', 'published', 'rejected', 'failed', 'cancelled', 'conflict')
TERMINAL_STATES = ('published', 'rejected', 'failed', 'cancelled', 'conflict')
_NONE: frozenset[str] = frozenset()
# D5. Restore rows are inserted directly as published and never transitioned.
TRANSITIONS: dict[str, frozenset[str]] = {
    'staged': frozenset({'running', 'cancelled', 'failed', 'conflict'}),
    'running': frozenset({'published', 'rejected', 'failed', 'cancelled', 'conflict'}),
    'published': _NONE, 'rejected': _NONE, 'failed': _NONE, 'cancelled': _NONE, 'conflict': _NONE,
}
JOURNAL_EVENTS = ('staged', 'admit_intent', 'linked', 'cancel_requested', 'published', 'rejected',
                  'failed', 'cancelled', 'conflict', 'restored', 'compacted', 'recovered')
REFUSAL_REASONS = ('stale_parent', 'not_restorable', 'already_current', 'history_expired',
                   'artifacts_expired', 'artifacts_missing', 'artifacts_altered', 'record_altered',
                   'review_expired', 'capacity')

MAX_SOURCE_BYTES = 65536
MAX_IR_JSON_BYTES = 262144
MAX_LOCK_JSON_BYTES = 262144
MAX_DETAIL_KEYS = 16
MAX_DETAIL_VALUE_CHARS = 240
_MAX_INT = 2 ** 53
_TEXT_CHARS = 240

Id32 = Annotated[str, StringConstraints(min_length=32, max_length=32, pattern=r'^[0-9a-f]{32}$')]
Hash64 = _Sha256
Seq = Annotated[int, Field(ge=1, le=_MAX_INT)]
DetailKey = Annotated[str, StringConstraints(min_length=1, max_length=32, pattern=r'^[a-z_]{1,32}$')]
DetailScalar = str | int | bool | None


def can_transition(src: str, dst: str) -> bool:
    allowed = TRANSITIONS.get(src)
    return allowed is not None and dst in allowed


def _ascii_text(value: str, limit: int, what: str) -> str:
    if len(value) > limit or not value.isascii():
        raise ValueError(f'{what} must be ASCII and at most {limit} characters')
    return value


@dataclass(frozen=True)
class RevisionRefusal:
    reasons: tuple[str, ...]
    detail: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'reasons', tuple(self.reasons))
        if not self.reasons or any(r not in REFUSAL_REASONS for r in self.reasons):
            raise ValueError('reasons must be a non-empty subset of REFUSAL_REASONS')
        if self.detail is not None:
            if not isinstance(self.detail, str):
                raise ValueError('detail must be a string or None')
            _ascii_text(self.detail, _TEXT_CHARS, 'detail')


class ProgramRevision(ContractModel):
    schema_version: Literal['mirum_program_revision_v0_1'] = PROGRAM_REVISION_VERSION
    revision_id: Id32
    program_id: Id32
    parent: Id32 | None = None
    kind: Literal['edit', 'restore', 'rebuild']
    derived_from: Id32 | None = None
    evidence_revision_id: Id32 | None = None
    state: Literal['staged', 'running', 'published', 'rejected', 'failed', 'cancelled', 'conflict']
    reason: str | None = None
    source_sha256: Hash64
    semantic_sha256: Hash64
    brief_sha256: Hash64
    fixed_requirements_sha256: Hash64
    lock_sha256: Hash64
    lock: ExecutionLockV0_2
    draft_id: Id32
    draft_revision: Seq
    brief_id: Id32 | None = None
    job_id: Id32 | None = None
    bundle: BundleIdentity | None = None
    published_seq: Seq | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator('reason')
    @classmethod
    def _reason(cls, v):
        return v if v is None else _ascii_text(v, _TEXT_CHARS, 'reason')

    @model_validator(mode='after')
    def _consistent(self):
        if self.updated_at < self.created_at:
            raise ValueError('updated_at precedes created_at')
        if self.parent == self.revision_id or self.derived_from == self.revision_id:
            raise ValueError('a revision cannot be its own parent or source')
        st = self.state
        if st == 'published':
            if self.published_seq is None or self.bundle is None or self.job_id is None:
                raise ValueError('published needs published_seq, bundle and job_id')
        else:
            if self.published_seq is not None:
                raise ValueError('only published carries published_seq')
        if st in ('staged', 'running') and self.bundle is not None:
            raise ValueError(f'{st} carries no bundle')
        if st == 'staged' and (self.job_id is not None or self.brief_id is not None):
            raise ValueError('staged carries no job_id or brief_id')
        if st == 'running' and (self.job_id is None or self.brief_id is None):
            raise ValueError('running needs job_id and brief_id')
        if self.kind == 'restore':
            if st != 'published':
                raise ValueError('restore is published-only')
            if self.derived_from is None or self.evidence_revision_id is None or self.parent is None:
                raise ValueError('restore needs parent, derived_from and evidence_revision_id')
        else:
            if self.evidence_revision_id != self.revision_id:
                raise ValueError('edit and rebuild evidence is their own revision')
            if self.kind == 'edit' and self.derived_from is not None:
                raise ValueError('edit has no derived_from')
            if self.kind == 'rebuild' and (self.derived_from is None or self.parent is None):
                raise ValueError('rebuild needs parent and derived_from')
        return self


class RevisionPayload(ContractModel):
    source_id: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    source: str
    ir_json: str
    lock_json: str

    @staticmethod
    def _bounded(value: str, limit: int, what: str, *, allow_empty: bool) -> str:
        try:
            size = len(value.encode('utf-8'))
        except UnicodeEncodeError:
            raise ValueError(f'{what} is not valid UTF-8') from None
        if size > limit or (size == 0 and not allow_empty):
            raise ValueError(f'{what} must be {"" if allow_empty else "1 to "}{limit} UTF-8 bytes at most')
        return value

    @field_validator('source')
    @classmethod
    def _source(cls, v):
        return cls._bounded(v, MAX_SOURCE_BYTES, 'source', allow_empty=True)

    @field_validator('ir_json')
    @classmethod
    def _ir(cls, v):
        return cls._bounded(v, MAX_IR_JSON_BYTES, 'ir_json', allow_empty=False)

    @field_validator('lock_json')
    @classmethod
    def _lock(cls, v):
        return cls._bounded(v, MAX_LOCK_JSON_BYTES, 'lock_json', allow_empty=False)


def build_payload(source_id: str, source: str, ir: ProgramIR, lock: ExecutionLockV0_2) -> RevisionPayload:
    """Payload with canonical IR and lock JSON (deterministic for equal inputs)."""
    return RevisionPayload(source_id=source_id, source=source, ir_json=canonical_json(ir),
                           lock_json=canonical_json(lock))


class JournalEntry(ContractModel):
    schema_version: Literal['mirum_program_journal_v0_1'] = PROGRAM_JOURNAL_VERSION
    seq: Seq
    program_id: Id32
    revision_id: Id32 | None = None
    event: Literal['staged', 'admit_intent', 'linked', 'cancel_requested', 'published', 'rejected',
                   'failed', 'cancelled', 'conflict', 'restored', 'compacted', 'recovered']
    detail: dict[DetailKey, DetailScalar]
    created_at: AwareDatetime

    @field_validator('detail')
    @classmethod
    def _detail(cls, v):
        if len(v) > MAX_DETAIL_KEYS:
            raise ValueError(f'detail has more than {MAX_DETAIL_KEYS} keys')
        for value in v.values():
            if isinstance(value, str):
                _ascii_text(value, MAX_DETAIL_VALUE_CHARS, 'detail value')
            elif isinstance(value, int) and not isinstance(value, bool) and abs(value) > _MAX_INT:
                raise ValueError('detail integer out of range')
        return v


class RevisionView(ContractModel):
    revision: ProgramRevision
    artifacts: Literal['available', 'expired', 'missing', 'none']
    is_current: bool


# ---------------------------------------------------------------------------------------- D8: diff

from .versions import REVISION_DIFF_VERSION  # noqa: E402

MAX_DIFF_CHANGES = 512
MAX_DIFF_VALUE_CHARS = 2048
DIFF_SECTIONS = ('requirements', 'candidates', 'analysis', 'evidence', 'execution', 'lock', 'backend_lock')
HASH_NAMES = ('source', 'semantic', 'brief', 'fixed_requirements', 'lock')
_MAX_ATTEMPTS = 4
_MAX_CHECKS = 64
_MAX_PAYLOADS = 64
_CODE = Annotated[str, StringConstraints(min_length=1, max_length=64)]
_TXT = Annotated[str, StringConstraints(min_length=1, max_length=240)]
_FLAT_FIELD = 'value'
_MODE_KEY = '#mode'
_WHOLE = '*'

__all__ += ['MAX_DIFF_CHANGES', 'MAX_DIFF_VALUE_CHARS', 'DIFF_SECTIONS', 'HASH_NAMES', 'CheckSummary',
            'AttemptSummary', 'PayloadDigest', 'BundleDigests', 'EvidenceSummary', 'HashEquality',
            'DiffEntry', 'RevisionDiff', 'evidence_from_run', 'diff']


class CheckSummary(ContractModel):
    check_id: _TXT
    state: _CODE


class AttemptSummary(ContractModel):
    ordinal: Annotated[int, Field(ge=0, lt=_MAX_ATTEMPTS)]
    candidate_sha256: Hash64
    outcome: _CODE
    failure_count: Annotated[int, Field(ge=0, le=_MAX_INT)]
    checks: Annotated[tuple[CheckSummary, ...], Field(max_length=_MAX_CHECKS)]


class PayloadDigest(ContractModel):
    path: _TXT
    sha256: Hash64


class BundleDigests(ContractModel):
    manifest_sha256: Hash64
    zip_sha256: Hash64
    payloads: Annotated[tuple[PayloadDigest, ...], Field(max_length=_MAX_PAYLOADS)]


class EvidenceSummary(ContractModel):
    lifecycle: _CODE
    outcome: _CODE
    attempts: Annotated[tuple[AttemptSummary, ...], Field(max_length=_MAX_ATTEMPTS)]
    selected_candidate_sha256: Hash64 | None = None
    bundle: BundleDigests | None = None
    expired: bool = False


def _plain(value) -> str:
    return str(getattr(value, 'value', value))


def evidence_from_run(run) -> EvidenceSummary:
    """Summary of an EngineeringRun or an ExpiredRun (duck-typed: an expired run has no attempts/bundle).

    Reads only contract fields; engineering.jobs is not imported."""
    expired = bool(getattr(run, 'expired', False))
    attempts = () if expired else tuple(
        AttemptSummary(
            ordinal=a.ordinal, candidate_sha256=a.candidate.candidate_sha256, outcome=_plain(a.outcome),
            failure_count=len(a.failures),
            checks=tuple(CheckSummary(check_id=c.check_id, state=_plain(c.state)) for c in a.checks))
        for a in run.attempts)
    bundle = None if expired else getattr(run, 'bundle', None)
    return EvidenceSummary(
        lifecycle=_plain(run.lifecycle), outcome=_plain(run.outcome), attempts=attempts,
        selected_candidate_sha256=None if expired else getattr(run, 'selected_candidate_sha256', None),
        bundle=None if bundle is None else BundleDigests(
            manifest_sha256=bundle.manifest_sha256, zip_sha256=bundle.zip_sha256,
            payloads=tuple(PayloadDigest(path=p.path, sha256=p.sha256) for p in bundle.payloads)),
        expired=expired)


class HashEquality(ContractModel):
    source: bool
    semantic: bool
    brief: bool
    fixed_requirements: bool
    lock: bool


_DiffText = Annotated[str, StringConstraints(max_length=MAX_DIFF_VALUE_CHARS)]


class DiffEntry(ContractModel):
    section: Literal['requirements', 'candidates', 'analysis', 'evidence', 'execution', 'lock',
                     'backend_lock']
    key: _TXT
    field: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    before: _DiffText | None = None
    after: _DiffText | None = None


class RevisionDiff(ContractModel):
    schema_version: Literal['mirum_revision_diff_v0_1'] = REVISION_DIFF_VERSION
    a: Id32
    b: Id32
    hashes: HashEquality
    requirements_changed: bool
    changes: Annotated[tuple[DiffEntry, ...], Field(max_length=MAX_DIFF_CHANGES)] = ()
    change_count: Annotated[int, Field(ge=0, le=_MAX_INT)] = 0
    changes_truncated: bool = False
    detail_unavailable: Literal['history_expired'] | None = None
    evidence_a: EvidenceSummary | None = None
    evidence_b: EvidenceSummary | None = None


def _text_sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _capped(value) -> str | None:
    """Canonical JSON text, or a fixed marker plus the text's sha256 when over the cap."""
    if value is None:
        return None
    text = canonical_json(value)
    if len(text) <= MAX_DIFF_VALUE_CHARS:
        return text
    return f'<over_limit sha256={_text_sha(text)}>'


def _keyed(items) -> dict[str, dict]:
    return {i['id']: {k: v for k, v in i.items() if k != 'id'} for i in items}


def _item_entries(section: str, a: dict[str, dict], b: dict[str, dict]):
    for key in sorted(set(a) | set(b)):
        if key not in a or key not in b:
            side = a.get(key, b.get(key))
            yield DiffEntry(section=section, key=key, field=_WHOLE,
                            before=_capped(side) if key in a else None,
                            after=_capped(side) if key in b else None)
            continue
        for field in sorted(set(a[key]) | set(b[key])):
            va, vb = a[key].get(field), b[key].get(field)
            if canonical_json(va) != canonical_json(vb):
                yield DiffEntry(section=section, key=key, field=field, before=_capped(va),
                                after=_capped(vb))


def _flat_entries(section: str, a: dict, b: dict):
    for key in sorted(set(a) | set(b)):
        va, vb = a.get(key), b.get(key)
        if key not in a or key not in b or canonical_json(va) != canonical_json(vb):
            yield DiffEntry(section=section, key=key, field=_FLAT_FIELD,
                            before=_capped(va) if key in a else None,
                            after=_capped(vb) if key in b else None)


def diff(a: ProgramRevision, a_ir: ProgramIR | None, b: ProgramRevision, b_ir: ProgramIR | None, *,
         evidence_a: EvidenceSummary | None = None,
         evidence_b: EvidenceSummary | None = None) -> RevisionDiff:
    """Pure diff keyed by id at a fixed depth (section -> key -> field); values are canonical JSON text.

    Flat sections (execution, lock, backend_lock) use the field name as key and field 'value'. An item
    present on one side only is one entry with field '*'. A missing IR on either side (compacted payload)
    gives a hash-only diff. Entries beyond MAX_DIFF_CHANGES are dropped; change_count keeps the total."""
    from .hashing import semantic_payload

    hashes = HashEquality(
        source=a.source_sha256 == b.source_sha256, semantic=a.semantic_sha256 == b.semantic_sha256,
        brief=a.brief_sha256 == b.brief_sha256,
        fixed_requirements=a.fixed_requirements_sha256 == b.fixed_requirements_sha256,
        lock=a.lock_sha256 == b.lock_sha256)
    common = dict(a=a.revision_id, b=b.revision_id, hashes=hashes,
                  requirements_changed=not hashes.fixed_requirements, evidence_a=evidence_a,
                  evidence_b=evidence_b)
    if a_ir is None or b_ir is None:
        return RevisionDiff(detail_unavailable='history_expired', **common)
    pa, pb = semantic_payload(a_ir), semantic_payload(b_ir)
    entries: list[DiffEntry] = []
    entries.extend(_item_entries('requirements', _keyed(pa['requirements']), _keyed(pb['requirements'])))
    entries.extend(_item_entries('candidates', _keyed(pa['candidates']['parameters']),
                                 _keyed(pb['candidates']['parameters'])))
    if pa['candidates']['mode'] != pb['candidates']['mode']:
        entries.append(DiffEntry(section='candidates', key=_MODE_KEY, field=_FLAT_FIELD,
                                 before=_capped(pa['candidates']['mode']),
                                 after=_capped(pb['candidates']['mode'])))
    entries.extend(_item_entries('analysis', _keyed(pa['analysis']), _keyed(pb['analysis'])))
    entries.extend(_item_entries('evidence', _keyed(pa['evidence']), _keyed(pb['evidence'])))
    entries.extend(_flat_entries('execution', a_ir.execution.model_dump(mode='json'),
                                 b_ir.execution.model_dump(mode='json')))
    la, lb = a.lock.model_dump(mode='json'), b.lock.model_dump(mode='json')
    bla, blb = la.pop('backend_lock'), lb.pop('backend_lock')
    entries.extend(_flat_entries('lock', la, lb))
    entries.extend(_flat_entries('backend_lock', bla, blb))
    return RevisionDiff(changes=tuple(entries[:MAX_DIFF_CHANGES]), change_count=len(entries),
                        changes_truncated=len(entries) > MAX_DIFF_CHANGES, **common)
