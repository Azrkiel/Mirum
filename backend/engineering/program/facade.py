"""F08 ProgramInterface: the in-process Python SDK over one RevisionService (D1, D8, D9, D10, D12).

One async method per registry operation (interface.OPERATIONS names, including ``propose``). Every
method takes the request model (and raw MEL bytes for the source operations) plus a client key, returns
the interface response model, or raises ONE exception type, ``InterfaceFailure``, that carries an
``InterfaceError`` envelope. Nothing returned or logged carries a draft id, a brief id, a job id,
exception text or a path (D7, D8).

Single owner (D3): a ProgramInterface owns one RevisionService over one store; transports in other
processes are HTTP clients. This module may import the store, so it is never imported by the CLI.

The stateless operations (validate, format, plan) share offline.py with the CLI --offline path, run
under a non-queuing Semaphore(2) and compile off the event loop. stage, propose and rebuild share one
non-queuing slot; the per-client ProgramQuota check (K46) and the PreparationQuota draft check run
inside it. Every mutating operation first expires dead staged revisions (D10, K47). Review (D8, K48)
fills the draft from the client-checked revision; run resolves the finalized brief of the revision's own
draft read-only and never takes a brief id from the caller.

ProgramQuota environment overrides (each is clamped to at most its default, so they can only tighten;
an unparsable value fails closed to 0 = refuse):
  MIRUM_ENGINEERING_PROGRAM_MAX_PROGRAMS_PER_CLIENT      default 8
  MIRUM_ENGINEERING_PROGRAM_MAX_STAGED_PER_CLIENT        default 4 (all programs of the client)
  MIRUM_ENGINEERING_PROGRAM_MAX_STAGE_OPS_PER_CLIENT_DAY default 32 (stage and rebuild, rolling 24 h)
  MIRUM_ENGINEERING_PROGRAM_MAX_PAYLOAD_BYTES_PER_CLIENT default 2097152 (stored payload bytes)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError

from engineering.admission import AdmissionBusy
from engineering.briefs import RevisionConflict
from engineering.contracts import ReviewAcknowledgement
from engineering.language.diagnostics import MelError
from engineering.store import ArtifactExpired, parse_ai_client_cap

from . import interface as api
from . import offline
from .interface import InterfaceFailure
from .planning import PlanRefusal
from .revisions import BundleDigests, PayloadDigest, ProgramRevision, RevisionRefusal

__all__ = ['ProgramInterface', 'ProgramQuota', 'ExportResult', 'InterfaceFailure', 'map_exception',
           'DEFAULT_SOURCE_ID', 'PROGRAM_QUOTA_DEFAULTS', 'PROGRAM_QUOTA_ENV', 'MAX_EXPORT_BYTES']

_LOG = logging.getLogger('mirum.program.interface')
DEFAULT_SOURCE_ID = 'program.mir'
MAX_EXPORT_BYTES = api.MAX_EXPORT_BYTES
_CHUNK = 1024 * 1024
_STATELESS_SLOTS = 2
_ID_KEYS = frozenset({'draft_id', 'brief_id', 'job_id'})
_REASON_RE = re.compile(r'^[a-z0-9_]{1,64}$')
_CHECK_REASON_RE = re.compile(r'^[a-z_]{1,64}$')

PROGRAM_QUOTA_DEFAULTS = {'programs': 8, 'staged': 4, 'operations': 32, 'payload_bytes': 2 * 1024 * 1024}
PROGRAM_QUOTA_ENV = {
    'programs': 'MIRUM_ENGINEERING_PROGRAM_MAX_PROGRAMS_PER_CLIENT',
    'staged': 'MIRUM_ENGINEERING_PROGRAM_MAX_STAGED_PER_CLIENT',
    'operations': 'MIRUM_ENGINEERING_PROGRAM_MAX_STAGE_OPS_PER_CLIENT_DAY',
    'payload_bytes': 'MIRUM_ENGINEERING_PROGRAM_MAX_PAYLOAD_BYTES_PER_CLIENT',
}
_WINDOW_SECONDS = 86400.0


# ------------------------------------------------------------------------------------- program quota

class ProgramQuota:
    """Per-client limits of the program runtime (D9, K46), derived from SQL on every check.

    No table of its own: programs.client owns every row. A limit above its default is clamped down to the
    default (tighten only). ``check_*`` return None when the client is under every limit, else the
    ERROR_TABLE code of the first limit reached (nothing is written either way)."""

    def __init__(self, store, *, programs=None, staged=None, operations=None, payload_bytes=None,
                 window_seconds=_WINDOW_SECONDS, clock=time.time):
        self.store = store
        given = {'programs': programs, 'staged': staged, 'operations': operations,
                 'payload_bytes': payload_bytes}
        self.limits = {name: self._clamp(name, given[name]) for name in PROGRAM_QUOTA_DEFAULTS}
        self.window = window_seconds
        self.clock = clock

    @staticmethod
    def _clamp(name, value) -> int:
        default = PROGRAM_QUOTA_DEFAULTS[name]
        if value is None:
            return default
        if type(value) is not int or value < 0:
            return 0
        return min(value, default)

    @classmethod
    def from_env(cls, store, *, environ=None, **kw):
        env = os.environ if environ is None else environ
        values = {}
        for name, var in PROGRAM_QUOTA_ENV.items():
            values[name] = parse_ai_client_cap(env.get(var))  # None unset, 0 on any bad value
        return cls(store, **values, **kw)

    def check_create(self, client) -> str | None:
        with self.store.transaction() as db:
            used = db.execute('SELECT COUNT(*) FROM programs WHERE client=?', (str(client),)).fetchone()[0]
        return 'quota_programs' if used >= self.limits['programs'] else None

    def check_stage(self, client, size) -> str | None:
        """Staged revisions, stage/rebuild operations in the rolling window, then payload bytes."""
        client = str(client)
        cutoff = self.clock() - self.window
        base = 'FROM program_revisions r JOIN programs p ON p.id=r.program_id WHERE p.client=?'
        with self.store.transaction() as db:
            staged = db.execute("SELECT COUNT(*) " + base + " AND r.state='staged'", (client,)).fetchone()[0]
            ops = db.execute("SELECT COUNT(*) " + base + " AND r.kind IN ('edit','rebuild') AND r.created>?",
                             (client, cutoff)).fetchone()[0]
            used = db.execute('SELECT COALESCE(SUM(LENGTH(CAST(r.payload AS BLOB))),0) ' + base
                              + ' AND r.payload IS NOT NULL', (client,)).fetchone()[0]
        if staged >= self.limits['staged']:
            return 'quota_staged'
        if ops >= self.limits['operations']:
            return 'quota_operations'
        if used + max(0, int(size)) > self.limits['payload_bytes']:
            return 'quota_bytes'
        return None


# ----------------------------------------------------------------------------------- error mapping

def _retry_after(exc) -> int | None:
    value = getattr(exc, 'retry_after', None)
    if type(value) is int or type(value) is float:
        return max(1, min(86400, int(value)))
    return None


def map_exception(exc: BaseException, *, source_id=None) -> api.InterfaceError:
    """The ONE exception -> envelope mapping. Subclasses are tested before their bases: AdmissionBusy is a
    RuntimeError; MelError, ValidationError, RevisionConflict, ArtifactExpired and BoundsError are
    ValueErrors. The text of ``exc`` is never read."""
    if isinstance(exc, InterfaceFailure):
        return exc.error
    if isinstance(exc, ValidationError):
        return api.from_validation_error(exc)
    if isinstance(exc, MelError):
        return api.from_mel(exc, source_id=None if exc.diagnostic.code == 'MEL-S016' else source_id)
    if isinstance(exc, AdmissionBusy):
        return api.busy('admission_busy', retry_after=_retry_after(exc))
    if isinstance(exc, RevisionConflict):
        return api.error('revision_conflict')
    if isinstance(exc, ArtifactExpired):
        return api.error('artifacts_expired')
    if isinstance(exc, api.BoundsError):
        return api.error(exc.code)
    if isinstance(exc, KeyError):
        return api.not_found()
    if isinstance(exc, ValueError):
        return api.error('invalid_field')
    err = api.unavailable()
    _LOG.warning('program interface error code=%s', err.code)  # one fixed line: never the exception text
    return err


def _fail(err: api.InterfaceError) -> InterfaceFailure:
    return InterfaceFailure(err)


def _guarded(method):
    """Run an operation; every failure leaves as InterfaceFailure(envelope)."""
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except InterfaceFailure:
            raise
        except Exception as exc:  # CancelledError and friends are BaseException and pass through
            raise InterfaceFailure(map_exception(exc)) from None
    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


# --------------------------------------------------------------------------------------- conversions

def _bundle_digests(bundle) -> BundleDigests | None:
    if bundle is None:
        return None
    return BundleDigests(
        manifest_sha256=bundle.manifest_sha256, zip_sha256=bundle.zip_sha256,
        payloads=tuple(PayloadDigest(path=p.path, sha256=p.sha256) for p in bundle.payloads))


def revision_status(view) -> api.RevisionStatus:
    """RevisionView -> RevisionStatus. Draft, brief and job ids are never copied."""
    rev = view.revision
    reason = rev.reason if rev.reason is not None and _REASON_RE.match(rev.reason) else None
    return api.RevisionStatus(
        revision_id=rev.revision_id, program_id=rev.program_id, parent=rev.parent, kind=rev.kind,
        derived_from=rev.derived_from, evidence_revision_id=rev.evidence_revision_id, state=rev.state,
        reason=reason, result_kind=api.result_kind(rev.state, rev.reason), source_sha256=rev.source_sha256,
        semantic_sha256=rev.semantic_sha256, brief_sha256=rev.brief_sha256,
        fixed_requirements_sha256=rev.fixed_requirements_sha256, lock_sha256=rev.lock_sha256,
        bundle=_bundle_digests(rev.bundle), published_seq=rev.published_seq,
        created_at=api.iso_utc(rev.created_at), updated_at=api.iso_utc(rev.updated_at),
        artifacts=view.artifacts, is_current=view.is_current)


def _journal_line(entry) -> api.JournalLine:
    detail = {k: v for k, v in entry.detail.items() if k not in _ID_KEYS}
    return api.JournalLine(seq=entry.seq, revision_id=entry.revision_id, event=entry.event, detail=detail,
                           created_at=api.iso_utc(entry.created_at))


def _evidence_view(record) -> api.EvidenceView:
    if record is None:
        return api.EvidenceView(status='none')
    checks = tuple(api.EvidenceCheck(
        check_id=c.check_id, category=c.category, required=c.required, state=c.state, coverage=c.coverage,
        reason=c.reason if c.reason is not None and _CHECK_REASON_RE.match(c.reason) else None)
        for c in record.checks)
    return api.EvidenceView(
        status='present', verdict=record.verdict, claim_scope=record.claim_scope,
        reasons=tuple(record.reasons), checks=checks, candidate_sha256=record.candidate_sha256,
        manifest_sha256=record.manifest_sha256, step_sha256=record.step_sha256, glb_sha256=record.glb_sha256,
        check_plan_sha256=record.check_plan_sha256, method_version=record.method_version)


@dataclass(frozen=True)
class ExportResult:
    metadata: api.ExportMetadata
    data: bytes


_SOURCE_EXPORTS = {
    'program-source': ('program.mir', api.MEL_MEDIA_TYPE, lambda p: p.source),
    'program-ir': ('program-ir.json', 'application/json', lambda p: p.ir_json),
    'execution-lock': ('execution-lock.json', 'application/json', lambda p: p.lock_json),
}


# ------------------------------------------------------------------------------------------ facade

class ProgramInterface:
    def __init__(self, service, briefs, preparation_quota, program_quota=None, *, clock=None):
        self.service = service
        self.briefs = briefs
        self.preparation_quota = preparation_quota
        self.program_quota = program_quota if program_quota is not None else ProgramQuota.from_env(service.store)
        self.clock = clock if clock is not None else service.clock
        self._stateless = asyncio.Semaphore(_STATELESS_SLOTS)
        self._slot = asyncio.Lock()

    # -------------------------------------------------------------------------------- common helpers

    def _prelude(self) -> None:
        """Run before every mutating operation: release dead drafts, fail dead staged revisions (D10)."""
        self.preparation_quota.expire()
        self.service.expire_staged(self.clock(), ttl_seconds=self.preparation_quota.ttl)
        cleanup = getattr(self.service.publisher, 'cleanup', None)
        if cleanup is not None:
            cleanup()

    def _view(self, revision_id, client):
        return self.service.view(revision_id, client=client)

    def _response(self, revision_id, client) -> api.RevisionResponse:
        return api.RevisionResponse(revision=revision_status(self._view(revision_id, client)))

    @staticmethod
    def _refusal(result):
        """Raise the envelope of a refusal; return anything else unchanged."""
        if isinstance(result, RevisionRefusal):
            raise _fail(api.from_revision_refusal(result.reasons))
        if isinstance(result, PlanRefusal):
            raise _fail(api.from_plan_refusal(result.outcome, result.reasons,
                                              diagnostic_code=result.diagnostic_code))
        return result

    def _revision_result(self, result, client) -> api.RevisionResponse:
        result = self._refusal(result)
        return self._response(result.revision_id, client)

    @staticmethod
    def _source_bytes(source) -> bytes:
        if type(source) is not bytes:
            raise _fail(api.invalid_request('invalid_field', field='source'))
        return source

    # ----------------------------------------------------------------------- stateless MEL operations

    async def _stateless_call(self, fn, source, source_id):
        if self._stateless.locked():
            raise _fail(api.busy('slot_busy'))
        async with self._stateless:
            return await asyncio.to_thread(fn, source, source_id)

    @_guarded
    async def validate(self, source: bytes, request: api.ValidateRequest | None = None, *, client=None):
        return await self._stateless_call(offline.validate_source, source,
                                          None if request is None else request.source_id)

    @_guarded
    async def format(self, source: bytes, request: api.FormatRequest | None = None, *, client=None):
        return await self._stateless_call(offline.format_source, source,
                                          None if request is None else request.source_id)

    @_guarded
    async def plan(self, source: bytes, request: api.PlanRequest | None = None, *, client=None):
        return await self._stateless_call(offline.plan_source, source,
                                          None if request is None else request.source_id)

    # ----------------------------------------------------------------------------------- programs

    @_guarded
    async def program_create(self, request: api.ProgramCreateRequest | None = None, *, client):
        self._prelude()
        code = self.program_quota.check_create(client)
        if code is not None:
            raise _fail(api.busy(code))
        return api.ProgramCreated(program_id=self.service.create_program(client))

    # ------------------------------------------------------------------------- stage, propose, rebuild

    def _requirements_guard(self, head_view, required_sha256) -> None:
        """D12: refuse unless the program has a head whose fixed requirements hash equals the guard."""
        if head_view is None or required_sha256 is None:
            raise _fail(api.error('requirements_changed'))
        if head_view.revision.fixed_requirements_sha256 != required_sha256:
            raise _fail(api.error('requirements_changed'))

    async def _staging(self, work, source_len, client):
        """The one non-queuing stage slot: expiry, then ProgramQuota, then the draft quota, then ``work``."""
        if self._slot.locked():
            raise _fail(api.busy('slot_busy'))
        async with self._slot:
            self._prelude()
            code = self.program_quota.check_stage(client, source_len)
            if code is not None:
                raise _fail(api.busy(code))
            try:
                self.preparation_quota.admit_draft(client, size=source_len)
            except AdmissionBusy:
                raise _fail(api.busy('quota_drafts')) from None
            result = await work()
            result = self._refusal(result)
            if isinstance(result, ProgramRevision):
                self.preparation_quota.record_draft(result.draft_id, client, source_len)
            return result

    async def _stage_common(self, source, program_id, source_id, expected_parent, guard, require_guard, client):
        source = self._source_bytes(source)
        head = self.service.current(program_id, client=client)  # unknown or foreign -> KeyError -> not_found
        if require_guard or guard is not None:
            self._requirements_guard(head, guard)

        async def work():
            if guard is not None:
                from engineering.language.semantic import compile_source
                compiled = await asyncio.to_thread(compile_source, source, source_id=source_id or DEFAULT_SOURCE_ID)
                if compiled.brief_content.fixed_requirements_sha256 != guard:
                    raise _fail(api.error('requirements_changed'))
            return await self.service.stage(program_id, source, source_id=source_id or DEFAULT_SOURCE_ID,
                                            expected_parent=expected_parent, client=client)

        revision = await self._staging(work, len(source), client)
        view = self._view(revision.revision_id, client)
        from engineering.contracts import GeneratorBriefContent, REVIEW_FIELDS
        content = self.briefs.repository.read(revision.draft_id).draft.content if (
            revision.lock.capability_id in ('equipment_bay', 'regen_chamber')) else None
        fields = content.review_fields if isinstance(content, GeneratorBriefContent) else REVIEW_FIELDS
        return api.StageResponse(revision=revision_status(view), review_fields=tuple(fields))

    @_guarded
    async def stage(self, source: bytes, request: api.StageRequest, *, client):
        return await self._stage_common(source, request.program_id, request.source_id,
                                        request.expected_parent, request.require_requirements_sha256, False,
                                        client)

    @_guarded
    async def propose(self, source: bytes, request: api.ProposeRequest, *, client,
                      require_requirements_sha256: str | None = None):
        """MCP propose (D12): stage with the requirements guard. The guard hash comes from the transport
        (X-Mirum-Require-Requirements); a program without a head, or without a guard, is refused."""
        return await self._stage_common(source, request.program_id, request.source_id,
                                        request.expected_parent, require_requirements_sha256, True, client)

    @_guarded
    async def rebuild(self, request: api.RebuildRequest, *, client):
        payload = self._refusal(self.service.source(request.revision_id, client=client))
        if payload is None:
            raise _fail(api.not_found())
        size = len(payload.source.encode('utf-8'))

        async def work():
            return await self.service.rebuild(request.revision_id, expected_parent=request.expected_parent,
                                              client=client)

        revision = await self._staging(work, size, client)
        return api.RevisionResponse(revision=revision_status(self._view(revision.revision_id, client)))

    # --------------------------------------------------------------------------------- review and run

    @_guarded
    async def review(self, request: api.ReviewRequest, *, client):
        """D8: the server reads the client-checked revision and fills draft_id and draft_revision itself;
        the caller supplies only the brief hash, the acknowledged fields and the reviewer name."""
        self._prelude()
        view = self._view(request.revision_id, client)
        rev = view.revision
        if rev.state == 'failed' and rev.reason == 'review_expired':
            raise _fail(api.error('review_expired'))
        if rev.state != 'staged':
            raise _fail(api.error('review_mismatch'))
        if request.brief_sha256 != rev.brief_sha256:
            raise _fail(api.error('review_mismatch'))
        try:
            ack = ReviewAcknowledgement(
                draft_id=rev.draft_id, draft_revision=rev.draft_revision, brief_sha256=rev.brief_sha256,
                acknowledged_fields=tuple(request.acknowledged_fields),
                reviewed_at=datetime.now(timezone.utc), reviewed_by=request.reviewed_by)
        except ValidationError:
            raise _fail(api.invalid_request('invalid_field', field='acknowledged_fields')) from None
        try:
            if not self.briefs.is_finalized(rev.draft_id, rev.draft_revision):  # a second review is a no-op
                try:
                    self.preparation_quota.admit_brief(rev.draft_id)
                except AdmissionBusy:
                    raise _fail(api.busy('capacity')) from None
                self.briefs.finalize(ack)
        except (ArtifactExpired, KeyError):
            raise _fail(api.error('review_expired')) from None
        except RevisionConflict:
            raise _fail(api.error('review_mismatch')) from None
        except ValidationError:
            raise _fail(api.invalid_request('invalid_field', field='acknowledged_fields')) from None
        return self._response(rev.revision_id, client)

    def _brief_for(self, rev) -> str | None:
        """The finalized brief of this revision's own draft and draft revision, read-only."""
        with self.service.store.transaction() as db:
            row = db.execute('SELECT id FROM briefs WHERE draft=? AND revision=?',
                             (rev.draft_id, rev.draft_revision)).fetchone()
        return None if row is None else row[0]

    @_guarded
    async def run(self, request: api.RunRequest, *, client):
        """No brief id from the caller (D8). A revision that already left staged returns its record
        unchanged, so running the same revision twice admits one job (D14)."""
        self._prelude()
        rev = self._view(request.revision_id, client).revision
        if rev.state != 'staged':
            return self._response(rev.revision_id, client)
        brief_id = self._brief_for(rev)
        if brief_id is None:
            raise _fail(api.from_plan_refusal('refused', ('not_finalized',)))
        result = await self.service.run(rev.revision_id, brief_id, client=client)
        return self._revision_result(result, client)

    @_guarded
    async def cancel(self, request: api.CancelRequest, *, client):
        self._prelude()
        result = await self.service.cancel(request.revision_id, client=client)
        return self._revision_result(result, client)

    @_guarded
    async def restore(self, request: api.RestoreRequest, *, client):
        self._prelude()
        result = await self.service.restore(request.revision_id, expected_head=request.expected_head,
                                            client=client)
        return self._revision_result(result, client)

    # ------------------------------------------------------------------------------------- read-only

    @_guarded
    async def status(self, request: api.StatusRequest, *, client):
        return self._response(request.revision_id, client)

    @_guarded
    async def inspect(self, request: api.InspectRequest, *, client):
        view = self._view(request.revision_id, client)
        evidence = self.service.evidence(request.revision_id, client=client)
        journal = self.service.journal(view.revision.program_id, client=client)
        lines = tuple(_journal_line(e) for e in journal)[-1024:]
        return api.InspectBundle(revision=revision_status(view), evidence=_evidence_view(evidence),
                                 journal=lines)

    @_guarded
    async def journal(self, request: api.HistoryRequest, *, client):
        """GET /programs/{pid}/journal: the program journal lines (ids of bearer routes stripped)."""
        entries = self.service.journal(request.program_id, client=client)
        return tuple(_journal_line(e) for e in entries)[-1024:]

    def _program_row(self, program_id, client):
        with self.service.store.transaction() as db:
            row = self.service.revision_store.program(db, program_id) if type(program_id) is str else None
            if row is None or row['client'] != client:
                raise KeyError('Unknown program')
            return dict(row)

    @_guarded
    async def program(self, request: api.HistoryRequest, *, client):
        """GET /programs/{pid}: the program record with its current revision."""
        row = self._program_row(request.program_id, client)
        head = self.service.current(request.program_id, client=client)
        return api.ProgramView(
            program_id=row['id'], head_revision_id=row['head'], head_seq=int(row['head_seq']),
            created_at=api.iso_utc(datetime.fromtimestamp(row['created'], tz=timezone.utc)),
            updated_at=api.iso_utc(datetime.fromtimestamp(row['updated'], tz=timezone.utc)),
            current=None if head is None else revision_status(head))

    @_guarded
    async def history(self, request: api.HistoryRequest, *, client):
        revisions = self.service.history(request.program_id, client=client)
        head = self.service.current(request.program_id, client=client)
        head_id = None if head is None else head.revision.revision_id
        statuses = tuple(revision_status(self.service.view(r.revision_id, client=client)) for r in revisions)
        return api.HistoryView(program_id=request.program_id, head_revision_id=head_id, revisions=statuses)

    @_guarded
    async def diff(self, request: api.DiffRequest, *, client):
        return api.DiffResponse(diff=self.service.compare(request.revision_id, request.other_revision_id,
                                                          client=client))

    @_guarded
    async def export(self, request: api.ExportRequest, *, client) -> ExportResult:
        """Allowlisted formats only. Source formats come from the stored payload (None or a foreign
        revision -> not_found, compacted -> expired); bundle formats from this revision's own bundle."""
        fmt = request.fmt
        if not api.is_export_format(fmt):
            raise _fail(api.not_found())
        if fmt in _SOURCE_EXPORTS:
            filename, media, pick = _SOURCE_EXPORTS[fmt]
            payload = self._refusal(self.service.source(request.revision_id, client=client))
            if payload is None:
                raise _fail(api.not_found())
            data = pick(payload).encode('utf-8')
            return self._export(request.revision_id, fmt, filename, media, data)
        download = await asyncio.to_thread(self.service.open_artifact, request.revision_id, fmt, client=client)
        try:
            limit = api.export_byte_limit(fmt)
            if download.size > limit:
                raise _fail(api.too_large('response_too_large'))
            data = await asyncio.to_thread(self._read_all, download, limit)
            return self._export(request.revision_id, fmt, download.filename, download.mime_type, data,
                                sha256=download.sha256 or None)
        finally:
            download.close()

    @staticmethod
    def _read_all(download, limit) -> bytes:
        parts, total = [], 0
        while True:
            block = download.file.read(_CHUNK)
            if not block:
                break
            total += len(block)
            if total > limit:
                raise _fail(api.too_large('response_too_large'))
            parts.append(block)
        return b''.join(parts)

    @staticmethod
    def _export(revision_id, fmt, filename, media, data, *, sha256=None) -> ExportResult:
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        if sha256 is not None and sha256 != digest:
            raise _fail(api.error('artifacts_altered'))
        meta = api.ExportMetadata(revision_id=revision_id, fmt=fmt, filename=filename, content_type=media,
                                  size_bytes=len(data), sha256=digest)
        return ExportResult(metadata=meta, data=data)
