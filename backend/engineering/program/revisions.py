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
