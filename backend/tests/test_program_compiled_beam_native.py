"""P07 native: the compiled examples/beam.mir drives the real OCCT adapter through the review gate.

Native shard only: skipped when cadquery is absent. The program is compiled, planned (occt, native),
locked, reviewed through BriefService, authorized and built by one OcctAdapter instance. The verdicts
must equal the form-route verdicts (policy.assess) for the same brief; none is hard-coded as a pass.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

cadquery = pytest.importorskip('cadquery')

# process_runner.ProcessRunner returns PROCESS_TREE_UNAVAILABLE off posix, so the native path (probe,
# worker) cannot run on a Windows host even when cadquery imports. The native shards are posix.
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='ProcessRunner needs posix process groups')

ROOT = Path(__file__).resolve().parents[2]
MIR = (ROOT / 'docs' / 'plans' / 'checked-engineering-v1' / 'phase-3-framework-language'
       / 'examples' / 'beam.mir')
FORM = ROOT / 'backend' / 'tests' / 'fixtures' / 'program' / 'p03' / 'form.json'
HEIGHTS = [5.0, 10.0, 15.0]
MIB = 1024 * 1024


def run(coro):
    return asyncio.run(coro)


def finalize(service, review):
    from engineering.contracts import REVIEW_FIELDS, ReviewAcknowledgement
    ack = ReviewAcknowledgement(
        draft_id=review.draft_id, draft_revision=review.draft_revision, brief_sha256=review.brief_sha256,
        acknowledged_fields=REVIEW_FIELDS, reviewed_at=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
        reviewed_by='local-user')
    return service.finalize(ack)


@pytest.fixture(scope='module')
def native():
    """Compile, plan, lock, review, authorize and build once (native work is slow)."""
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.language.semantic import compile_source
    from engineering.program.adapters.occt import OcctAdapter
    from engineering.program.kernel import ExecutionContext, ExecutionLockV0_2
    from engineering.program.planning import (
        ProgramPlan, lock_program, open_review, plan_program, run_program,
    )

    compiled = compile_source(MIR.read_bytes(), source_id='examples/beam.mir')
    adapter = OcctAdapter()
    plan = plan_program(compiled, mode='native', adapter_factory=lambda backend, mode: adapter)
    assert isinstance(plan, ProgramPlan), plan
    lock = run(lock_program(plan, adapter=adapter))
    assert isinstance(lock, ExecutionLockV0_2), lock
    assert lock.backend_lock.mode == 'native'
    service = BriefService(MemoryBriefRepository())
    review = open_review(plan, lock, service)
    brief = finalize(service, review)
    fixed_before = brief.fixed_requirements_sha256
    context = ExecutionContext(deadline_s=300, max_artifact_bytes=64 * MIB, workspace='f05-native')
    results = run(run_program(review, compiled, service, brief.brief_id, adapter=adapter, context=context))
    assert isinstance(results, tuple), results
    return dict(compiled=compiled, adapter=adapter, plan=plan, lock=lock, service=service,
                review=review, brief=brief, fixed_before=fixed_before, results=results)


@pytest.fixture(scope='module')
def form_route():
    """The same brief through the form path: validate, create, finalize, then policy.assess."""
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.cantilever import CantileverAdapter
    from engineering.policy import assess
    service = BriefService(MemoryBriefRepository())
    payload = json.loads(FORM.read_text(encoding='utf-8'))
    content, issues = service.validate(payload)
    assert content is not None and not issues
    prepared = service.create(payload)
    assert prepared.draft.content is not None and not prepared.issues
    ack_review = SimpleNamespace(
        draft_id=prepared.draft.draft_id, draft_revision=prepared.draft.revision,
        brief_sha256=prepared.draft.content.brief_sha256)
    brief = finalize(service, ack_review)
    adapter = CantileverAdapter()
    candidates = adapter.candidates(brief)
    out = []
    for candidate in candidates:
        evaluation = run(adapter.evaluate(candidate))
        out.append((candidate, evaluation, assess(candidate, evaluation)))
    return dict(brief=brief, candidates=candidates, decisions=out)


def test_compiled_brief_and_candidates_match_the_form_route(native, form_route):
    compiled = native['compiled']
    assert compiled.brief_sha256 == form_route['brief'].brief_sha256
    planned = [p.candidate.candidate_sha256 for p in native['plan'].plans]
    assert planned == [c.candidate_sha256 for c in form_route['candidates']]


def test_p07_compiled_beam_verdicts_on_the_real_kernel(native):
    results = native['results']
    assert len(results) == 3
    assert [r.result.domain_outcome for r in results] == ['rejected', 'rejected', 'accepted']
    plans = native['plan'].plans
    assert [p.candidate.specification.height_mm for p in plans] == HEIGHTS
    for p in plans:
        assert p.candidate.specification.length_mm == 200.0
        assert p.candidate.specification.width_mm == 10.0
        assert p.candidate.specification.tip_force_n == 50.0
    assert native['brief'].content.requirements.tip_force_n == 50.0
    for r in results:
        assert r.result.mode == 'native'
        assert r.result.outcome == 'completed'


def test_compiled_verdicts_identical_to_the_form_route(native, form_route):
    compiled_outcomes = [r.result.domain_outcome for r in native['results']]
    form_outcomes = [decision.outcome.value for _, _, decision in form_route['decisions']]
    assert form_outcomes == compiled_outcomes
    form_heights = [c.specification.height_mm for c, _, _ in form_route['decisions']]
    assert form_heights == HEIGHTS
    planned = [p.candidate.candidate_sha256 for p in native['plan'].plans]
    for (candidate, evaluation, _), sha in zip(form_route['decisions'], planned):
        assert evaluation['candidate'].candidate_sha256 == sha
        assert candidate.candidate_sha256 == sha


def test_reviewed_requirements_unchanged_by_the_run(native):
    brief = native['brief']
    compiled = native['compiled']
    assert brief.content == compiled.brief_content
    assert brief.fixed_requirements_sha256 == native['fixed_before']
    assert brief.fixed_requirements_sha256 == compiled.brief_content.fixed_requirements_sha256
    reread = native['service'].read_brief(brief.brief_id)
    assert reread.fixed_requirements_sha256 == native['fixed_before']
    assert reread.brief_sha256 == compiled.brief_sha256


def test_native_gate_refuses_an_altered_lock_before_any_build(native):
    from engineering.program.planning import PlanRefusal, ProgramReview, authorize_run
    review = native['review']
    forged = ProgramReview(**{**review.__dict__, 'lock_sha256': '0' * 64})
    out = run(authorize_run(forged, native['compiled'], native['service'], native['brief'].brief_id,
                            adapter=native['adapter']))
    assert isinstance(out, PlanRefusal)
    assert (out.outcome, out.reasons) == ('unavailable', ('lock_mismatch',))
