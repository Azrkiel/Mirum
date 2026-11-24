"""F07 delivered-geometry verifier and evidence store.

``DeliveredVerifier.verify`` re-reads the EXACT bytes a published beam job delivered (STEP and GLB),
measures them in the native worker, and binds the result to the check plan that the reviewed IR, the
lock and the reviewed candidates fixed BEFORE any measurement. It is a monotone gate: it runs only for a
job that policy.assess already accepted, and it can only withhold promotion, never create acceptance.

Every problem (rejected job, bad bundle, over-cap file, tampered report, mock backend, timeout, native
failure) becomes a stored non-verified record; ``verify`` never raises for bad artifacts.

``EvidenceStore`` keeps one program_evidence row per revision. ``match_problem`` is the fail-closed check
the revision settle path runs inside its own transaction.

Importing this module loads no native code: the adapter comes from ``adapter_factory`` at call time.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import math
import time
from pathlib import Path

from pydantic import ValidationError

from engineering.contracts import EngineeringRun, Lifecycle, Outcome
from engineering.spec import canonical_spec, structural_inputs
from vendor.mirum_kernel.structural import MODEL, result_state

from .adapters import adapter_for
from .beam_bridge import brief_content_from_program
from .errors import ProgramError
from .evidence import (
    UNKNOWN_SHA256, CheckEvidence, CheckLimit, CheckPlan, DeliveredEvidence, ErrorBound, FaceTable,
    Measurement, ReportInputs,
    build_check_plan, build_delivered_evidence, check_plan_sha256, derive_verdict,
)
from .hashing import canonical_json
from .kernel import Artifact, ArtifactRef
from .planning import _candidates_from_content, _plans

__all__ = [
    'DELIVERED_UNVERIFIED', 'DELIVERED_EVIDENCE_STALE', 'DELIVERED_CHECK_FAILED',
    'DELIVERED_BINDING_AMBIGUOUS', 'DELIVERED_EVIDENCE_UNAVAILABLE', 'DELIVERED_REASONS',
    'MAX_BINARY_BYTES', 'MAX_JSON_BYTES', 'MAX_JSON_DEPTH', 'MEASURE_TIMEOUT_S', 'METHOD_SOURCES',
    'EvidenceRowInvalid', 'EvidenceStore', 'DeliveredVerifier', 'method_version',
    'compute_method_version', 'read_capped', 'parse_json_bounded',
]

# D7 reason vocabulary (the revision service stores these as the failed-revision reason).
DELIVERED_UNVERIFIED = 'delivered_unverified'
DELIVERED_EVIDENCE_STALE = 'delivered_evidence_stale'
DELIVERED_CHECK_FAILED = 'delivered_check_failed'
DELIVERED_BINDING_AMBIGUOUS = 'delivered_binding_ambiguous'
DELIVERED_EVIDENCE_UNAVAILABLE = 'delivered_evidence_unavailable'
DELIVERED_REASONS = (DELIVERED_UNVERIFIED, DELIVERED_EVIDENCE_STALE, DELIVERED_CHECK_FAILED,
                     DELIVERED_BINDING_AMBIGUOUS, DELIVERED_EVIDENCE_UNAVAILABLE)
_VERDICT_REASON = {'failed': DELIVERED_CHECK_FAILED, 'ambiguous': DELIVERED_BINDING_AMBIGUOUS,
                   'unavailable': DELIVERED_EVIDENCE_UNAVAILABLE}

# Read budgets for untrusted delivered files (D5): STEP and GLB 32 MiB, JSON 1 MiB, bounded depth.
MAX_BINARY_BYTES = 32 * 1024 ** 2
MAX_JSON_BYTES = 1024 ** 2
MAX_JSON_DEPTH = 32
MEASURE_TIMEOUT_S = 60.0

# --------------------------------------------------------------------------- method version (D6)

_PROGRAM_DIR = Path(__file__).resolve().parent
# occt.py holds OcctAdapter.measure, the stdout parser, the worker limits and the declared mesh bound, so a change
# there is a method change too (security review of PR #142).
METHOD_SOURCES = (_PROGRAM_DIR / 'evidence.py', _PROGRAM_DIR / 'delivered.py',
                  _PROGRAM_DIR / 'adapters' / '_occt_measure.py', _PROGRAM_DIR / 'adapters' / 'occt.py',
                  _PROGRAM_DIR / 'adapters' / '_editable_plate_worker.py',
                  _PROGRAM_DIR.parent / 'editable_plate_adapter.py',
                  _PROGRAM_DIR.parent.parent / 'vendor' / 'mirum_editable_plate.py',
                  _PROGRAM_DIR.parent.parent / 'vendor' / 'mirum_kernel' / 'structural.py',
                  _PROGRAM_DIR.parent.parent / 'vendor' / 'mirum_kernel' / 'beam_fea.py')


def compute_method_version(paths) -> str:
    """sha256 over the name-sorted (file name, sha256 of its bytes) pairs, as verify.py hashes its sources."""
    digest = hashlib.sha256()
    for path in sorted((Path(p) for p in paths), key=lambda p: p.name):
        digest.update(f'{path.name}:{hashlib.sha256(path.read_bytes()).hexdigest()}\n'.encode('ascii'))
    return digest.hexdigest()


@functools.lru_cache(maxsize=1)
def method_version() -> str:
    """The verifier's method version: evidence.py + delivered.py + _occt_measure.py + occt.py, once per process."""
    return compute_method_version(METHOD_SOURCES)


_current_method_version = method_version


# --------------------------------------------------------------------------- bounded reads


class _Problem(Exception):
    """An internal stop: the record is non-verified. kind is 'unavailable' or 'failed'."""

    def __init__(self, kind: str, reason: str):
        super().__init__(f'{kind}: {reason}')
        self.kind, self.reason = kind, reason


def _unavailable(reason: str = 'measurement_unavailable') -> _Problem:
    return _Problem('unavailable', reason)


def _mismatch() -> _Problem:
    return _Problem('failed', 'report_mismatch')


def read_capped(stream, cap: int) -> bytes:
    """Read a binary stream with an explicit byte cap; over the cap raises (unavailable)."""
    out = bytearray()
    while True:
        block = stream.read(min(1 << 20, cap + 1 - len(out)))
        if not block:
            return bytes(out)
        out += block
        if len(out) > cap:
            raise _unavailable()


def _reject_constant(_name):
    raise ValueError('non-finite JSON constant')


def parse_json_bounded(data: bytes, cap: int = MAX_JSON_BYTES, max_depth: int = MAX_JSON_DEPTH):
    """Parse untrusted JSON: size cap, no NaN/Infinity, bounded nesting depth. Raises _Problem (unavailable)."""
    if len(data) > cap:
        raise _unavailable()
    try:
        value = json.loads(data.decode('utf-8'), parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise _unavailable() from None
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, (dict, list)):
            if depth > max_depth:
                raise _unavailable()
            stack.extend((child, depth + 1) for child in (node.values() if isinstance(node, dict) else node))
    return value


# --------------------------------------------------------------------------- evidence store (D8)


class EvidenceRowInvalid(ValueError):
    """A program_evidence row whose record fails validation or disagrees with its columns."""


_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS program_evidence('
    'revision_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, step_sha256 TEXT, '
    'check_plan_sha256 TEXT NOT NULL, method_version TEXT NOT NULL, '
    "verdict TEXT NOT NULL CHECK(verdict IN ('verified','failed','ambiguous','unavailable')), "
    'record TEXT NOT NULL, created REAL NOT NULL)')
_COLUMNS = ('job_id', 'manifest_sha256', 'step_sha256', 'check_plan_sha256', 'method_version', 'verdict')


class EvidenceStore:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock
        with store.transaction() as db:
            db.execute(_SCHEMA)

    def put(self, record: DeliveredEvidence) -> DeliveredEvidence:
        """Commit the row in its own transaction and return the authoritative stored record.

        A verified row is never overwritten (a re-verification after a method change needs a new
        revision); any other row, including an invalid or tampered one, is replaced. When a verified
        row is kept, that row is returned."""
        text = canonical_json(record)
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM program_evidence WHERE revision_id=?',
                             (record.revision_id,)).fetchone()
            if row is not None:
                try:
                    existing = self._decode(row, record.revision_id)
                except EvidenceRowInvalid:
                    existing = None
                if existing is not None and existing.verdict == 'verified':
                    return existing
            db.execute('INSERT OR REPLACE INTO program_evidence VALUES (?,?,?,?,?,?,?,?,?)', (
                record.revision_id, record.job_id, record.manifest_sha256, record.step_sha256,
                record.check_plan_sha256, record.method_version, record.verdict, text, self.clock()))
        return record

    @staticmethod
    def _decode(row, revision_id: str) -> DeliveredEvidence:
        try:
            record = DeliveredEvidence.model_validate_json(row['record'])
        except (ValidationError, ValueError, TypeError) as exc:
            raise EvidenceRowInvalid('stored evidence record fails validation') from exc
        if record.revision_id != revision_id or any(
                row[name] != getattr(record, name) for name in _COLUMNS):
            raise EvidenceRowInvalid('stored evidence columns disagree with the record')
        return record

    def get(self, db, revision_id: str) -> DeliveredEvidence | None:
        """The re-validated record, None when there is no row; EvidenceRowInvalid for a tampered row.

        ``db`` is an open store transaction, or None to open one."""
        if db is None:
            with self.store.transaction() as own:
                return self.get(own, revision_id)
        row = db.execute('SELECT * FROM program_evidence WHERE revision_id=?', (revision_id,)).fetchone()
        return None if row is None else self._decode(row, revision_id)

    def match_problem(self, db, revision_id: str, *, job_id, bundle, method_version=None) -> str | None:
        """None only for a verified row bound to this job, this bundle and the current method version.

        Usable inside an existing store transaction. Otherwise one of DELIVERED_REASONS."""
        try:
            record = self.get(db, revision_id)
        except EvidenceRowInvalid:
            return DELIVERED_EVIDENCE_STALE
        if record is None:
            return DELIVERED_UNVERIFIED
        wanted = _current_method_version() if method_version is None else method_version
        # A non-verified record made before any bundle was read names no manifest (UNKNOWN_SHA256):
        # it has nothing to compare, and its own verdict is the more useful reason.
        unbound = record.manifest_sha256 == UNKNOWN_SHA256 and record.verdict != 'verified'
        if (record.job_id != job_id or record.method_version != wanted or (not unbound and (
                bundle is None or record.manifest_sha256 != bundle.manifest_sha256))):
            return DELIVERED_EVIDENCE_STALE
        return _VERDICT_REASON.get(record.verdict)


# --------------------------------------------------------------------------- the verifier (D5)

# (lease format, published name, byte cap)
_PAYLOADS = (('step', 'model.step', MAX_BINARY_BYTES), ('glb', 'preview.glb', MAX_BINARY_BYTES),
             ('report', 'report.json', MAX_JSON_BYTES), ('spec', 'selected_spec.json', MAX_JSON_BYTES),
             ('attempts', 'attempts.json', MAX_JSON_BYTES))


class _Context:
    """What is known so far; drives the identity fields of a non-verified record."""

    def __init__(self):
        self.plan: CheckPlan | None = None
        self.claim_scope = 'checked_design'
        self.candidates = {}
        self.candidate_sha256: str | None = None
        self.manifest_sha256: str | None = None
        self.step_sha256: str | None = None
        self.glb_sha256: str | None = None


def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _mismatch()
    return float(value)


class DeliveredVerifier:
    def __init__(self, store, publisher, adapter_factory=adapter_for, *, evidence_store=None,
                 timeout_s: float = MEASURE_TIMEOUT_S):
        self.store, self.publisher, self.adapter_factory = store, publisher, adapter_factory
        self.evidence = evidence_store if evidence_store is not None else EvidenceStore(store)
        self.timeout_s = timeout_s
        self._semaphore = asyncio.Semaphore(1)

    async def verify(self, revision, ir) -> DeliveredEvidence:
        """Verify one revision's delivered bytes; always returns (and stores) a record."""
        ctx = _Context()
        try:
            # D5: 60 s overall (validate, leases, cross-checks and the native measurement); timeout -> unavailable.
            record = await asyncio.wait_for(self._verify(revision, ir, ctx), self.timeout_s)
        except _Problem as problem:
            record = self._problem_record(revision, ctx, problem)
        except Exception:  # fail closed: any unexpected error is an unavailable record
            record = self._problem_record(revision, ctx, _unavailable())
        return self.evidence.put(record)

    # ---- the flow

    async def _verify(self, revision, ir, ctx: _Context) -> DeliveredEvidence:
        if ir.capability.id == 'editable_plate':
            ctx.claim_scope = 'geometry_checks_only'
            return await self._verify_editable_plate(revision, ir, ctx)
        if ir.capability.id == 'equipment_bay':
            ctx.claim_scope = 'geometry_checks_only'
            return await self._verify_bay(revision, ir, ctx)
        if ir.capability.id == 'regen_chamber':
            ctx.claim_scope = 'geometry_checks_only'
            return await self._verify_regen(revision, ir, ctx)
        self._plan(revision, ir, ctx)
        run = self._read_run(revision)
        bundle = run.bundle
        ctx.manifest_sha256 = bundle.manifest_sha256
        try:
            await asyncio.to_thread(self.publisher.validate, revision.job_id, bundle)
        except Exception:
            raise _unavailable() from None
        blobs = await asyncio.to_thread(self._lease_all, revision.job_id, bundle)
        ctx.step_sha256 = hashlib.sha256(blobs['model.step']).hexdigest()
        ctx.glb_sha256 = hashlib.sha256(blobs['preview.glb']).hexdigest()
        ctx.candidate_sha256 = run.selected_candidate_sha256
        spec = ctx.candidates.get(run.selected_candidate_sha256)
        if spec is None:
            raise _mismatch()
        report_inputs = self._cross_check(blobs, run, revision, spec)
        adapter = self._adapter()
        build_plan = _plans(ir, (spec,))[0]
        step = Artifact(ArtifactRef(name='model.step', format='step', representation='brep',
                                    sha256=ctx.step_sha256, size=len(blobs['model.step']),
                                    node_id='beam.solid'), blobs['model.step'])
        glb = Artifact(ArtifactRef(name='preview.glb', format='glb', representation='mesh',
                                   sha256=ctx.glb_sha256, size=len(blobs['preview.glb']),
                                   node_id='beam.preview'), blobs['preview.glb'])
        async with self._semaphore:
            try:
                outcome = await asyncio.wait_for(adapter.measure(step, glb, build_plan), self.timeout_s)
            except Exception:  # includes TimeoutError; cancellation still propagates
                raise _unavailable() from None
        table = getattr(outcome, 'table', None)
        if not isinstance(table, FaceTable):
            raise _unavailable()
        try:
            return build_delivered_evidence(
                ctx.plan, run.selected_candidate_sha256, table, getattr(outcome, 'mesh', None),
                report_inputs, revision.lock.backend_lock.native_versions, revision_id=revision.revision_id,
                job_id=revision.job_id, manifest_sha256=ctx.manifest_sha256, step_sha256=ctx.step_sha256,
                glb_sha256=ctx.glb_sha256, method_version=method_version())
        except (ValidationError, ValueError):
            raise _unavailable() from None

    async def _verify_regen(self, revision, ir, ctx):
        from engineering.capabilities import REGEN
        from engineering.regen_adapter import REPORT, SPEC, STL
        from engineering.regen_spec import canonical_spec
        from .evidence import evidence_from_picogk_inspect
        from .hashing import execution_lock_v0_2_sha256, semantic_sha256
        from .planning import generator_candidates
        from .regen_bridge import content_from_program

        content = content_from_program(ir)
        if (content.brief_sha256 != revision.brief_sha256
                or content.fixed_requirements_sha256 != revision.fixed_requirements_sha256
                or semantic_sha256(ir) != revision.semantic_sha256
                or execution_lock_v0_2_sha256(revision.lock) != revision.lock_sha256):
            raise _unavailable('lock_mismatch')
        run = self._read_run(revision)
        candidate, = generator_candidates(content)
        if run.selected_candidate_sha256 != candidate.candidate_sha256 or run.attempts[-1].candidate != candidate:
            raise _mismatch()
        ctx.candidate_sha256 = candidate.candidate_sha256
        ctx.manifest_sha256 = run.bundle.manifest_sha256
        await asyncio.to_thread(self.publisher.validate, revision.job_id, run.bundle)
        payloads = (('generator-cleaned-stl', STL, REGEN.resources.run_bytes),
                    ('generator-report', REPORT, MAX_JSON_BYTES),
                    ('generator-spec-canonical', SPEC, MAX_JSON_BYTES))
        blobs = await asyncio.to_thread(self._lease_all, revision.job_id, run.bundle, payloads)
        spec = REGEN.parse_spec(content.specification)
        if blobs[SPEC] != canonical_spec(spec).encode():
            raise _mismatch()
        stl_sha = hashlib.sha256(blobs[STL]).hexdigest()
        report = parse_json_bounded(blobs[REPORT], MAX_JSON_BYTES)
        if report['stl_sha256'] != stl_sha or report['spec_sha256'] != candidate.candidate_sha256:
            raise _mismatch()
        adapter = self.adapter_factory('picogk')
        if getattr(adapter, 'mode', None) != 'native':
            raise _unavailable('mock_backend')
        plan = _plans(ir, (candidate,))[0]
        artifact = Artifact(ArtifactRef(name=STL, format='stl', representation='mesh', sha256=stl_sha,
                                       size=len(blobs[STL]), node_id='regen.geometry'), blobs[STL])
        async with self._semaphore:
            measured = await adapter.inspect(artifact, plan, report=report)
        return evidence_from_picogk_inspect(
            measured, revision_id=revision.revision_id, job_id=revision.job_id,
            candidate_sha256=candidate.candidate_sha256, manifest_sha256=ctx.manifest_sha256,
            method_version=method_version(), voxel_size_mm=spec.voxel_size_mm,
            report_volume_mm3=report['checks']['volume']['measured_mm3'],
            native_versions=revision.lock.backend_lock.native_versions)

    async def _verify_editable_plate(self, revision, ir, ctx):
        from engineering.editable_plate import AMBIGUITY, KERNEL_REVISION
        from engineering.editable_plate_adapter import (
            MAX_STEP_BYTES, METHOD, REPORT, STEP, bound_report, diagnostic_groups,
        )
        from vendor.mirum_editable_plate import LINEAR_TOL
        from .editable_plate_bridge import content_from_program
        from .bay_bridge import check_plan_record
        from .hashing import execution_lock_v0_2_sha256, semantic_sha256
        from .planning import generator_candidates

        content = content_from_program(ir)
        if (content.brief_sha256 != revision.brief_sha256
                or content.fixed_requirements_sha256 != revision.fixed_requirements_sha256
                or semantic_sha256(ir) != revision.semantic_sha256
                or execution_lock_v0_2_sha256(revision.lock) != revision.lock_sha256
                or revision.lock.backend_lock.adapter_version != KERNEL_REVISION):
            raise _unavailable('lock_mismatch')
        run = self._read_run(revision)
        candidate, = generator_candidates(content)
        if (run.selected_candidate_sha256 != candidate.candidate_sha256
                or run.attempts[-1].candidate != candidate):
            raise _mismatch()
        bundle = run.bundle
        ctx.candidate_sha256 = candidate.candidate_sha256
        ctx.manifest_sha256 = bundle.manifest_sha256
        check_plan = check_plan_record(revision.semantic_sha256, revision.lock_sha256, candidate)
        await asyncio.to_thread(self.publisher.validate, revision.job_id, bundle)
        payloads = (('generator-body-step', STEP, MAX_STEP_BYTES), ('generator-report', REPORT, MAX_JSON_BYTES))
        blobs = await asyncio.to_thread(self._lease_all, revision.job_id, bundle, payloads)
        step = blobs[STEP]
        ctx.step_sha256 = hashlib.sha256(step).hexdigest()
        adapter = self.adapter_factory('occt', capability='editable_plate')
        if getattr(adapter, 'mode', None) != 'native':
            raise _unavailable('mock_backend')
        plan, = _plans(ir, (candidate,))
        async with self._semaphore:
            outcome = await adapter.measure_plate(step, plan)
        report = bound_report(outcome['report'], step, candidate)
        checks = []
        for check, codes in diagnostic_groups(report).items():
            if not codes and 'envelope_mm' not in report:
                checks.append(CheckEvidence(
                    check_id='plate.' + check, category='geometry', required=True, method=METHOD,
                    method_version=method_version(), coverage='not_evaluated', representation='none',
                    state='unavailable', reason='measurement_unavailable'))
                continue
            checks.append(CheckEvidence(
                check_id='plate.' + check, category='geometry', required=True, method=METHOD,
                method_version=method_version(), coverage='measured', representation='brep',
                state='failed' if codes else 'passed',
                reason=('binding_ambiguous' if AMBIGUITY.intersection(codes) else 'limit_exceeded') if codes else None,
                error_bound=ErrorBound(value=LINEAR_TOL, unit='mm', source='editable plate length tolerance'),
                measurements=(Measurement(
                    name='diagnostics', unit='count', value=len(codes), representation='brep', satisfied=not codes,
                    limit=CheckLimit(name='diagnostics', unit='count', value=0, comparison='eq',
                                     source='policy:editable_plate.diagnostics')),)))
            for code in sorted(set(codes)):
                checks.append(CheckEvidence(
                    check_id='plate.' + code, category='geometry', required=True, method=METHOD,
                    method_version=method_version(), coverage='measured', representation='brep', state='failed',
                    reason='binding_ambiguous' if code in AMBIGUITY else 'limit_exceeded'))
        checks = tuple(checks)
        verdict = derive_verdict(checks)
        if verdict == 'verified' and canonical_json(report).encode() != blobs[REPORT]:
            raise _mismatch()
        return DeliveredEvidence(
            revision_id=revision.revision_id, job_id=revision.job_id, candidate_sha256=ctx.candidate_sha256,
            claim_scope='geometry_checks_only', manifest_sha256=ctx.manifest_sha256, step_sha256=ctx.step_sha256,
            check_plan_sha256=hashlib.sha256(canonical_json(check_plan).encode()).hexdigest(),
            method_version=method_version(), native_versions=revision.lock.backend_lock.native_versions,
            checks=checks, verdict=verdict)

    async def _verify_bay(self, revision, ir, ctx):
        from engineering.equipment_bay_adapter import BOM, METHOD, REPORT, STEP, EquipmentBayAdapter
        from engineering.equipment_bay import KERNEL_REVISION
        from .bay_bridge import check_plan_record, content_from_program
        from .hashing import execution_lock_v0_2_sha256, semantic_sha256
        from .planning import generator_candidates

        content = content_from_program(ir)
        if (content.brief_sha256 != revision.brief_sha256
                or content.fixed_requirements_sha256 != revision.fixed_requirements_sha256
                or semantic_sha256(ir) != revision.semantic_sha256
                or execution_lock_v0_2_sha256(revision.lock) != revision.lock_sha256
                or revision.lock.backend_lock.adapter_version != KERNEL_REVISION):
            raise _unavailable('lock_mismatch')
        run = self._read_run(revision)
        candidate, = generator_candidates(content)
        if (run.selected_candidate_sha256 != candidate.candidate_sha256
                or run.attempts[-1].candidate != candidate):
            raise _mismatch()
        bundle = run.bundle
        ctx.candidate_sha256 = candidate.candidate_sha256
        ctx.manifest_sha256 = bundle.manifest_sha256
        await asyncio.to_thread(self.publisher.validate, revision.job_id, bundle)
        payloads = (('generator-body-step', STEP, MAX_BINARY_BYTES), ('generator-bom', BOM, MAX_JSON_BYTES),
                    ('generator-report', REPORT, MAX_JSON_BYTES))
        blobs = await asyncio.to_thread(self._lease_all, revision.job_id, bundle, payloads)
        ctx.step_sha256 = hashlib.sha256(blobs[STEP]).hexdigest()
        adapter = self.adapter_factory('occt', capability='equipment_bay')
        if getattr(adapter, 'mode', None) != 'native':
            raise _unavailable('mock_backend')
        plan = _plans(ir, (candidate,))[0]
        async with self._semaphore:
            outcome = await adapter.measure_bay(blobs[STEP], plan)
        report = outcome['report']
        report.update(search=outcome['search'], kernel_revision=KERNEL_REVISION)
        if (canonical_json(outcome['bom']).encode() != blobs[BOM]
                or canonical_json(report).encode() != blobs[REPORT]
                or report['step']['sha256'] != ctx.step_sha256):
            raise _mismatch()

        measured = EquipmentBayAdapter.measurements(blobs[REPORT])
        limits = content.descriptor.expected_limits(content.specification)
        sources = {'length': 'reviewed:bay.length', 'width': 'reviewed:bay.width',
                   'max_offset': 'policy:bay_alignment', 'correspondences': 'reviewed:bay.module_count',
                   'diagnostics': 'policy:bay_bindings'}
        checks = []
        for check, limit_rows in limits.items():
            measurements = []
            for limit in limit_rows:
                number = _number(measured[check][limit.name])
                passed = number <= limit.value if limit.comparison == 'le' else number == limit.value
                measurements.append(Measurement(
                    name=limit.name, unit=limit.unit, value=number, representation='brep', satisfied=passed,
                    limit=CheckLimit(name=limit.name, unit=limit.unit, value=limit.value,
                                     comparison=limit.comparison, source=sources[limit.name])))
            passed = all(row.satisfied for row in measurements)
            checks.append(CheckEvidence(
                check_id='bay.' + check, category='geometry', required=True, coverage='measured',
                method=METHOD, method_version=method_version(), representation='brep',
                state='passed' if passed else 'failed', reason=None if passed else 'limit_exceeded',
                error_bound=ErrorBound(value=1e-6, unit='mm', source='equipment bay geometric roundoff'),
                measurements=tuple(measurements)))
        checks = tuple(checks)
        verdict = derive_verdict(checks)
        return DeliveredEvidence(
            revision_id=revision.revision_id, job_id=revision.job_id, candidate_sha256=ctx.candidate_sha256,
            claim_scope='geometry_checks_only', manifest_sha256=ctx.manifest_sha256, step_sha256=ctx.step_sha256,
            check_plan_sha256=hashlib.sha256(canonical_json(
                check_plan_record(revision.semantic_sha256, revision.lock_sha256, candidate)).encode()).hexdigest(),
            method_version=method_version(), native_versions=revision.lock.backend_lock.native_versions,
            checks=checks, verdict=verdict)

    def _plan(self, revision, ir, ctx: _Context) -> None:
        """The check plan, computed from reviewed inputs before any measurement."""
        try:
            content = brief_content_from_program(ir, design=ir.design)
            candidates = _candidates_from_content(content)
            plan = build_check_plan(ir, revision.lock, candidates, mesh_convention_pinned=True)
        except (ProgramError, ValidationError, ValueError):
            raise _unavailable() from None
        ctx.plan = plan
        ctx.candidates = {c.candidate_sha256: c for c in candidates}
        if (plan.brief_sha256 != revision.brief_sha256
                or plan.fixed_requirements_sha256 != revision.fixed_requirements_sha256
                or plan.semantic_sha256 != revision.semantic_sha256
                or plan.lock_sha256 != revision.lock_sha256):
            raise _unavailable('lock_mismatch')

    def _read_run(self, revision) -> EngineeringRun:
        from engineering.jobs import parse_run
        if revision.job_id is None:
            raise _unavailable()
        with self.store.transaction() as db:
            row = db.execute('SELECT record FROM jobs WHERE id=?', (revision.job_id,)).fetchone()
        if row is None:
            raise _unavailable()
        try:
            run = parse_run(row[0])
        except (ValidationError, ValueError):
            raise _unavailable() from None
        if (not isinstance(run, EngineeringRun) or run.job_id != revision.job_id
                or run.lifecycle != Lifecycle.COMPLETED or run.outcome != Outcome.ACCEPTED
                or run.bundle is None or run.selected_candidate_sha256 is None):
            raise _unavailable()
        if revision.bundle is not None and revision.bundle != run.bundle:
            raise _unavailable()
        if (run.brief_sha256 != revision.brief_sha256
                or run.fixed_requirements_sha256 != revision.fixed_requirements_sha256):
            raise _unavailable('lock_mismatch')
        return run

    def _lease_all(self, job_id, bundle, payloads=None) -> dict[str, bytes]:
        """Blocking: lease and read each payload with its cap; check length and sha256 against the bundle."""
        identities = {p.path: p for p in bundle.payloads}
        out = {}
        for fmt, name, cap in (_PAYLOADS if payloads is None else payloads):
            identity = identities.get(name)
            if identity is None or identity.bytes > cap:
                raise _unavailable()
            download = self.publisher.open_lease(job_id, fmt, envelope=bundle)
            try:
                if download.size > cap:
                    raise _unavailable()
                data = read_capped(download.file, cap)
            finally:
                download.close()
            if len(data) != identity.bytes or hashlib.sha256(data).hexdigest() != identity.sha256:
                raise _unavailable()
            out[name] = data
        return out

    def _cross_check(self, blobs, run, revision, spec) -> ReportInputs:
        """selected_spec, attempts and report must agree with the reviewed candidate and the jobs row."""
        selected = run.selected_candidate_sha256
        try:
            if parse_json_bounded(blobs['selected_spec.json']) != json.loads(canonical_spec(spec.specification)):
                raise _mismatch()
            attempts = parse_json_bounded(blobs['attempts.json'])
            if not isinstance(attempts, list) or not 1 <= len(attempts) <= 4:
                raise _mismatch()
            *earlier, last = attempts
            if (last['outcome'] != 'accepted' or last['candidate']['candidate_sha256'] != selected
                    or any(a['outcome'] != 'rejected' for a in earlier)):
                raise _mismatch()
            report = parse_json_bounded(blobs['report.json'])
            if (report['brief_sha256'] != revision.brief_sha256
                    or report['fixed_requirements_sha256'] != revision.fixed_requirements_sha256):
                raise _mismatch()
            found = [e for e in report['evaluations'] if e['candidate_sha256'] == selected]
            if len(found) != 1:
                raise _mismatch()
            native = found[0]['native_report']
            if native['physics']['state'] != 'passed':
                raise _mismatch()
            if spec.specification.assumptions == MODEL:
                analysis = native['structural_analysis']
                if (result_state(structural_inputs(spec.specification), analysis) != 'passed'
                        or analysis['samples'] != native['physics']['mesh_history']
                        or native['spec_sha256'] != spec.candidate_sha256
                        or native['step_sha256'] != hashlib.sha256(blobs['model.step']).hexdigest()):
                    raise _mismatch()
            measured = native['measurements']
            return ReportInputs(physics_passed=True, section_iy_mm4=_number(measured['section_iy_mm4']),
                                length_mm=_number(measured['length_mm']),
                                height_mm=_number(measured['height_mm']))
        except _Problem as problem:
            if problem.kind == 'unavailable':
                raise _mismatch() from None  # unparsable or over-budget JSON in an accepted bundle
            raise
        except (KeyError, TypeError, IndexError, ValueError, ValidationError, AttributeError):
            raise _mismatch() from None

    def _adapter(self):
        try:
            adapter = self.adapter_factory('occt')
        except Exception:
            raise _unavailable() from None
        if getattr(adapter, 'mode', None) == 'mock' or not callable(getattr(adapter, 'measure', None)):
            raise _unavailable('mock_backend')
        return adapter

    # ---- non-verified records

    def _problem_record(self, revision, ctx: _Context, problem: _Problem) -> DeliveredEvidence:
        """A stored non-verified record: every plan entry unavailable (or the report cross-bind failed)."""
        plan = ctx.plan
        checks: tuple[CheckEvidence, ...] = ()
        if plan is not None:
            cand = next((c for c in plan.candidates if c.candidate_sha256 == ctx.candidate_sha256),
                        plan.candidates[0])
            checks = tuple(self._problem_check(e, problem) for e in cand.entries)
        return DeliveredEvidence(
            revision_id=revision.revision_id, job_id=revision.job_id or '0' * 32,
            candidate_sha256=ctx.candidate_sha256 or UNKNOWN_SHA256, claim_scope=ctx.claim_scope,
            manifest_sha256=ctx.manifest_sha256 or UNKNOWN_SHA256, step_sha256=ctx.step_sha256,
            glb_sha256=ctx.glb_sha256,
            check_plan_sha256=check_plan_sha256(plan) if plan is not None else UNKNOWN_SHA256,
            method_version=method_version(), native_versions=revision.lock.backend_lock.native_versions,
            bindings=(), checks=checks, verdict=derive_verdict(checks), reasons=(problem.reason,))

    @staticmethod
    def _problem_check(entry, problem: _Problem) -> CheckEvidence:
        common = dict(check_id=entry.check_id, category=entry.category, required=entry.required,
                      method_version=method_version(), representation='none')
        if entry.category in ('manufacturing', 'standards'):
            return CheckEvidence(state='not_evaluated', coverage='not_evaluated', method='none', **common)
        if problem.kind == 'failed' and entry.check_id == 'physics.report_inputs':
            return CheckEvidence(state='failed', coverage='not_evaluated', method='report_cross_bind',
                                 reason='report_mismatch', **common)
        reason = problem.reason if problem.kind == 'unavailable' else 'measurement_unavailable'
        return CheckEvidence(state='unavailable' if entry.required else 'not_evaluated',
                             coverage='not_evaluated', method='none', reason=reason, **common)
