"""F05 planning: plan -> lock -> review -> run gate. Offline; the fake native adapter never builds."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from engineering.briefs import BriefService, MemoryBriefRepository
from engineering.contracts import REVIEW_FIELDS, ReviewAcknowledgement
from engineering.language import MelError
from engineering.language.semantic import compile_source
from engineering.program import kernel
from engineering.program.adapters import MockKernelAdapter, adapter_for
from engineering.program.hashing import execution_lock_v0_2_sha256
from engineering.program.ir import execution_lock
from engineering.program.kernel import (
    BuildPlan, ExecutionContext, ExecutionLockV0_2, NativeVersion, ProbeResult, plan_for,
)
from engineering.program.planning import (
    PlanRefusal, ProgramPlan, ProgramReview, RunAuthorization, authorize_run, lock_program,
    open_review, plan_program, run_program,
)
from engineering.program.registry import OpStream, OpStreamEntry

ROOT = Path(__file__).resolve().parents[2]
MIR = ROOT / 'backend' / 'tests' / 'fixtures' / 'program' / 'p03' / 'beam.mir'
CTX = ExecutionContext(deadline_s=5, max_artifact_bytes=1000, workspace='planning-tests')


def run(coro):
    return asyncio.run(coro)


def source() -> str:
    return MIR.read_text(encoding='utf-8')


def edited(old: str, new: str) -> str:
    text = source()
    assert old in text
    return text.replace(old, new, 1)


class FakeNative:
    backend = 'occt'
    mode = 'native'

    def __init__(self, occt_version='7.8.1'):
        self.occt_version = occt_version
        self.probes = 0
        self.builds: list[BuildPlan] = []

    def describe(self):
        return kernel.make_description('occt', mode='native', adapter_version='fake-0.1.0')

    async def probe(self):
        self.probes += 1
        return ProbeResult(backend='occt', mode='native', available=True,
                           native_versions=(NativeVersion(name='occt', version=self.occt_version),))

    def supports(self, plan):
        return kernel.check_plan(self.describe(), plan)

    async def build(self, plan, context):
        self.builds.append(plan)
        raise AssertionError('the fake build must never be reached by a test that expects refusal')


class RecordingFake(FakeNative):
    """Spy build: records the call and returns a bare mock-shaped result."""
    async def build(self, plan, context):
        self.builds.append(plan)
        return kernel.Built(kernel.BuildResult(
            backend='occt', adapter_version='fake-0.1.0', mode='mock',
            capability_id=plan.capability_id, capability_version=plan.capability_version,
            outcome='planned', op_stream=plan.op_stream))


def finalize(service, review: ProgramReview):
    ack = ReviewAcknowledgement(
        draft_id=review.draft_id, draft_revision=review.draft_revision, brief_sha256=review.brief_sha256,
        acknowledged_fields=REVIEW_FIELDS, reviewed_at=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
        reviewed_by='local-user')
    return service.finalize(ack)


def reviewed(src: str | None = None, adapter=None):
    compiled = compile_source(src if src is not None else source())
    adapter = adapter or RecordingFake()
    plan = plan_program(compiled, adapter_factory=lambda backend, mode: adapter)
    assert isinstance(plan, ProgramPlan)
    lock = run(lock_program(plan, adapter=adapter))
    assert isinstance(lock, ExecutionLockV0_2)
    service = BriefService(MemoryBriefRepository())
    review = open_review(plan, lock, service)
    return compiled, adapter, plan, lock, service, review


# ------------------------------------------------------------------ happy path

def test_happy_path_plan_lock_review_authorize_run():
    calls = []

    def factory(backend, mode):
        calls.append((backend, mode))
        return adapter

    adapter = RecordingFake()
    compiled = compile_source(source())
    plan = plan_program(compiled, adapter_factory=factory)
    assert calls == [('occt', 'native')]
    assert isinstance(plan, ProgramPlan) and plan.mode == 'native' and plan.backend == 'occt'
    assert len(plan.plans) == 3
    assert [p.candidate.specification.height_mm for p in plan.plans] == [5.0, 10.0, 15.0]
    for p in plan.plans:
        assert [e.operation for e in p.op_stream.entries] == [
            'capability.build', 'capability.inspect', 'capability.export', 'capability.export']
        assert all(e.span is not None for e in p.op_stream.entries)
    assert adapter.probes == 0

    lock = run(lock_program(plan, adapter=adapter))
    assert isinstance(lock, ExecutionLockV0_2) and lock.schema_version == 'mirum_execution_lock_v0_2'
    assert execution_lock_v0_2_sha256(lock) == execution_lock_v0_2_sha256(
        run(lock_program(plan, adapter=adapter)))

    service = BriefService(MemoryBriefRepository())
    review = open_review(plan, lock, service)
    assert review.brief_sha256 == compiled.brief_sha256
    assert review.semantic_sha256 == compiled.semantic_sha256
    assert review.source_sha256 == compiled.source_sha256
    assert not service.is_finalized(review.draft_id, review.draft_revision)
    brief = finalize(service, review)

    auth = run(authorize_run(review, compiled, service, brief.brief_id, adapter=adapter))
    assert isinstance(auth, RunAuthorization) and len(auth.plans) == 3
    assert auth.backend_lock == lock.backend_lock and adapter.builds == []

    built = run(run_program(review, compiled, service, brief.brief_id, adapter=adapter, context=CTX))
    assert len(built) == 3 and len(adapter.builds) == 3
    assert calls == [('occt', 'native')]


def test_unsupported_decision_is_returned_and_no_other_backend_is_asked():
    seen = []

    class Refusing(FakeNative):
        def supports(self, plan):
            return kernel.SupportDecision(backend='occt', mode='native', supported=False,
                                          reasons=('export_not_supported',), diagnostic_code='X1')

    adapter = Refusing()

    def factory(backend, mode):
        seen.append(backend)
        return adapter

    refusal = plan_program(compile_source(source()), adapter_factory=factory)
    assert isinstance(refusal, PlanRefusal) and refusal.outcome == 'unsupported'
    assert refusal.reasons == ('export_not_supported',) and refusal.diagnostic_code == 'X1'
    assert refusal.decision.supported is False
    assert seen == ['occt'] and adapter.probes == 0


def test_refusal_reasons_are_allowlisted():
    with pytest.raises(ValueError):
        PlanRefusal('refused', ('runtime_missing',))
    with pytest.raises(ValueError):
        PlanRefusal('unsupported', ())
    with pytest.raises(ValueError):
        PlanRefusal('unsupported', ('not_finalized',))
    assert PlanRefusal('unavailable', ('lock_mismatch',)).outcome == 'unavailable'


# ------------------------------------------------------------------ mock

def test_mock_plans_but_never_locks_authorizes_or_runs():
    compiled = compile_source(source())
    plan = plan_program(compiled, mode='mock')
    assert isinstance(plan, ProgramPlan) and plan.mode == 'mock' and len(plan.plans) == 3
    mock = adapter_for('occt', mode='mock')
    assert isinstance(mock, MockKernelAdapter)
    refusal = run(lock_program(plan, adapter=mock))
    assert refusal == PlanRefusal('unavailable', ('mock_backend',))
    # also refused for a native-mode plan handed a mock adapter
    native_plan = plan_program(compiled, adapter_factory=lambda b, mode: FakeNative())
    assert run(lock_program(native_plan, adapter=mock)).reasons == ('mock_backend',)

    _, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)
    spy = MockKernelAdapter('occt')
    built = []
    spy_build = spy.build

    async def counting(*a, **k):
        built.append(1)
        return await spy_build(*a, **k)

    spy.build = counting
    out = run(run_program(review, compiled, service, brief.brief_id, adapter=spy, context=CTX))
    assert isinstance(out, PlanRefusal) and out.outcome == 'unavailable' and out.reasons == ('mock_backend',)
    assert built == []


def test_construct_operation_is_refused_by_supports_before_any_probe():
    compiled = compile_source(source())
    adapter = FakeNative()
    base = plan_program(compiled, adapter_factory=lambda b, mode: adapter).plans[0]
    bad = BuildPlan(
        capability_id=base.capability_id, capability_version=base.capability_version, backend='occt',
        candidate=base.candidate, exports=base.exports,
        op_stream=OpStream(schema_version='mirum_op_stream_v0_1', entries=(
            OpStreamEntry(seq=0, operation='construct.extrude', operation_version='0.1.0'),)))
    decision = adapter.supports(bad)
    assert not decision.supported and decision.diagnostic_code == 'IR009'
    assert adapter.probes == 0 and adapter.builds == []

    class OnlyConstruct(FakeNative):
        def supports(self, plan):
            return super().supports(bad)

    refusal = plan_program(compiled, adapter_factory=lambda b, mode: OnlyConstruct())
    assert isinstance(refusal, PlanRefusal) and refusal.diagnostic_code == 'IR009'


# ------------------------------------------------------------------ invalidation (D8)

@pytest.mark.parametrize('old,new', [
    ('tip_force = 50 N', 'tip_force = 60 N'),
    ('youngs_modulus = 70000 MPa', 'youngs_modulus = 71000 MPa'),
    ('candidate_heights = [5 mm, 10 mm, 15 mm]', 'candidate_heights = [5 mm, 10 mm]'),
])
def test_changed_program_invalidates_old_review(old, new):
    _, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)
    changed = compile_source(edited(old, new))
    out = run(run_program(review, changed, service, brief.brief_id, adapter=adapter, context=CTX))
    assert isinstance(out, PlanRefusal) and out.outcome == 'refused'
    assert out.reasons[0] in ('review_mismatch', 'semantic_mismatch')
    assert adapter.builds == []


@pytest.mark.parametrize('old,new', [
    ('numerical_policy = "beam_numerical_v1"', 'numerical_policy = "beam_numerical_v2"'),
    ('resource_policy = "engineering_resources_v1"', 'resource_policy = "engineering_resources_v2"'),
])
def test_changed_policy_never_reaches_planning(old, new):
    _, adapter, _, _, service, review = reviewed()
    with pytest.raises(MelError):
        compile_source(edited(old, new))
    assert adapter.builds == []


def test_semantic_mismatch_when_review_hash_differs():
    compiled, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)
    forged = ProgramReview(**{**review.__dict__, 'semantic_sha256': '0' * 64})
    out = run(authorize_run(forged, compiled, service, brief.brief_id, adapter=adapter))
    assert out.reasons == ('semantic_mismatch',) and out.outcome == 'refused'


def test_review_lock_must_match_its_hash():
    # Security review LOW-3 (PR #138): a hand-built review cannot carry a lock other than the hashed one.
    _, _, _, _, _, review = reviewed()
    with pytest.raises(ValueError):
        ProgramReview(**{**review.__dict__, 'lock_sha256': '0' * 64})


def test_ir_swapped_during_the_probe_is_refused_before_build():
    # Security review LOW-2 (PR #138): the probe awaits; an IR changed meanwhile must not be planned.
    compiled, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)
    other = compile_source(edited('tip_force = 50 N', 'tip_force = 60 N')).ir

    class Swapping(RecordingFake):
        async def probe(self):
            result = await super().probe()
            object.__setattr__(compiled, 'ir', other)  # bypass the frozen record on purpose
            return result

    swapping = Swapping()
    out = run(run_program(review, compiled, service, brief.brief_id, adapter=swapping, context=CTX))
    assert isinstance(out, PlanRefusal) and out.reasons == ('semantic_mismatch',)
    assert swapping.builds == []


def test_backend_change_makes_lock_mismatch_and_zero_builds():
    compiled, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)
    other = RecordingFake(occt_version='7.9.0')
    out = run(run_program(review, compiled, service, brief.brief_id, adapter=other, context=CTX))
    assert isinstance(out, PlanRefusal) and out.outcome == 'unavailable' and out.reasons == ('lock_mismatch',)
    assert other.probes == 1 and other.builds == []


def test_unavailable_probe_refuses_with_its_reasons():
    compiled, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)

    class Down(RecordingFake):
        async def probe(self):
            self.probes += 1
            return ProbeResult(backend='occt', mode='native', available=False, reasons=('runtime_missing',))

    down = Down()
    out = run(run_program(review, compiled, service, brief.brief_id, adapter=down, context=CTX))
    assert out == PlanRefusal('unavailable', ('runtime_missing',)) and down.builds == []


def test_v0_1_lock_is_readable_but_cannot_authorize():
    compiled, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)
    old = ProgramReview(**{**review.__dict__, 'lock': execution_lock(compiled.ir)})
    out = run(run_program(old, compiled, service, brief.brief_id, adapter=adapter, context=CTX))
    assert out == PlanRefusal('refused', ('lock_version_unsupported',)) and adapter.builds == []


def test_unfinalized_draft_is_refused():
    compiled, adapter, _, _, service, review = reviewed()
    out = run(run_program(review, compiled, service, 'no-such-brief', adapter=adapter, context=CTX))
    assert out == PlanRefusal('refused', ('not_finalized',))
    other = ProgramReview(**{**review.__dict__, 'draft_revision': 2})
    brief = finalize(service, review)
    out = run(run_program(other, compiled, service, brief.brief_id, adapter=adapter, context=CTX))
    assert out.reasons == ('not_finalized',) and adapter.builds == [] and adapter.probes == 1


def test_whitespace_and_comment_edit_keeps_review_valid_and_records_new_source():
    compiled, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)
    again = compile_source('// a new comment\n' + source().replace('{\n', '{\n\n', 1))
    assert again.source_sha256 != compiled.source_sha256
    assert again.semantic_sha256 == compiled.semantic_sha256
    auth = run(authorize_run(review, again, service, brief.brief_id, adapter=adapter))
    assert isinstance(auth, RunAuthorization)
    assert auth.source_sha256 == again.source_sha256 != review.source_sha256


def test_invalid_programs_never_compile_so_nothing_can_be_planned():
    adapter = RecordingFake()
    for bad in (edited('  tip_force = 50 N;\n', ''),               # missing requirement
                edited('capability_version = "1.0.0"', 'capability_version = "9.9.9"'),  # unsupported
                edited('tip_force = 50 N', 'tip_force = 50 mm')):  # unit error
        with pytest.raises(MelError):
            compile_source(bad)
    assert adapter.builds == [] and adapter.probes == 0


def test_candidate_mismatch_is_refused_before_build(monkeypatch):
    compiled, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)
    import engineering.cantilever as cantilever
    real = cantilever.CantileverAdapter.candidates
    monkeypatch.setattr(cantilever.CantileverAdapter, 'candidates',
                        lambda self, b: real(self, b)[:2])
    out = run(run_program(review, compiled, service, brief.brief_id, adapter=adapter, context=CTX))
    assert out == PlanRefusal('refused', ('candidate_mismatch',)) and adapter.builds == []


def test_mismatched_compiled_program_is_refused_before_build():
    compiled, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)
    other = compile_source(edited('length = 200 mm', 'length = 210 mm'))
    out = run(run_program(review, other, service, brief.brief_id, adapter=adapter, context=CTX))
    assert isinstance(out, PlanRefusal) and adapter.builds == []


def test_adapter_for_wrong_backend_is_refused():
    compiled, adapter, _, _, service, review = reviewed()
    brief = finalize(service, review)

    class Wrong(RecordingFake):
        backend = 'picogk'

    out = run(authorize_run(review, compiled, service, brief.brief_id, adapter=Wrong()))
    assert out == PlanRefusal('refused', ('lock_mismatch',))


def test_import_loads_no_native_provider_or_web_module():
    code = (
        'import sys, json\n'
        'import engineering.program.planning\n'
        'print(json.dumps(sorted(m for m in ("cadquery", "OCP", "google.genai", "fastapi", "httpx")'
        ' if m in sys.modules)))\n')
    env = dict(os.environ, PYTHONPATH=f'{ROOT / "backend"}{os.pathsep}{ROOT}')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env,
                         timeout=120, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []
