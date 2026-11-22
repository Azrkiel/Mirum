"""F03 PicoGK kernel adapter conformance against the real regen builder.

Runs only in the picogk-runtime Docker job (REGEN_NATIVE=1, REGEN_BUILDER present). Self-contained:
imports only engineering.* and the standard library, because that job runs pytest with -c /dev/null
and no other test module on the path.
"""
import asyncio
import json
import os
import re
import time
from pathlib import Path

import pytest

BUILDER = Path(os.environ.get('REGEN_BUILDER', '/opt/regen/app/builder'))
EXAMPLES = Path(__file__).resolve().parents[2] / 'picogk-builder' / 'examples'

pytestmark = pytest.mark.skipif(
    os.environ.get('REGEN_NATIVE') != '1' or not BUILDER.exists(),
    reason='needs the native PicoGK builder (REGEN_NATIVE=1, REGEN_BUILDER)')

HEX64 = re.compile(r'^[0-9a-f]{64}$')
MIB = 1024 * 1024


def run(coro):
    return asyncio.run(coro)


def context(deadline_s=600):
    from engineering.program.kernel import ExecutionContext
    return ExecutionContext(deadline_s=deadline_s, max_artifact_bytes=64 * MIB, workspace='f03-native')


def candidate_for(spec_name='small.json'):
    """The GeneratorCandidate the existing regen path builds for an example spec."""
    from engineering.capabilities import get_generator
    from engineering.contracts import (BriefDraft, EngineeringBrief, GeneratorBriefContent, Provenance,
                                       ReviewAcknowledgement)
    from engineering.regen_adapter import RegenAdapter
    spec = json.loads((EXAMPLES / spec_name).read_text(encoding='utf-8'))
    descriptor = get_generator('regen_chamber')
    payload = dict(
        schema_version='engineering_generator_brief_v1',
        capability=dict(id=descriptor.id, version=descriptor.version,
                        numerical_policy_version=descriptor.numerical_version,
                        resource_policy_version=descriptor.resources.version),
        specification=descriptor.parse_spec(spec).model_dump(mode='json'),
        required_checks=[])
    payload['required_checks'] = list(descriptor.required_checks(payload['specification']))
    content = GeneratorBriefContent.model_validate_json(json.dumps(payload))
    provenance = Provenance.model_validate_json(json.dumps(dict(
        record_id='f03-record', created_at='2026-11-17T12:00:00Z', input_mode='form',
        field_origins=[dict(field=f, origin='user_supplied', reference='Typed input')
                       for f in content.review_fields])))
    draft = BriefDraft(draft_id='f03-draft', revision=1, content=content, clarifications=(),
                       provenance=provenance)
    review = ReviewAcknowledgement.model_validate_json(json.dumps(dict(
        draft_id='f03-draft', draft_revision=1, brief_sha256=content.brief_sha256,
        acknowledged_fields=list(content.review_fields),
        reviewed_at='2026-11-17T12:05:00Z', reviewed_by='local-user')))
    brief = EngineeringBrief.from_draft(draft, review, brief_id='f03-brief')
    found = RegenAdapter().candidates(brief)
    assert len(found) == 1
    return found[0]


def plan_of(candidate, exports=('stl',)):
    from engineering.program import kernel
    return kernel.plan_for('regen_chamber', '1.0.0', 'picogk', candidate, exports)


def make_runner(tmp_path):
    from engineering.regen_web import JobRunner
    return JobRunner(tmp_path / 'jobs')


def make_adapter(runner):
    from engineering.program.adapters.picogk import PicogkAdapter
    return PicogkAdapter(runner=lambda: runner)


@pytest.fixture
def runner(tmp_path):
    r = make_runner(tmp_path)
    yield r
    for job in list(r.jobs.values()):
        r.cancel(job['id'])


@pytest.fixture(scope='module')
def candidate():
    return candidate_for('small.json')


@pytest.fixture
def built(runner, candidate):
    adapter = make_adapter(runner)
    plan = plan_of(candidate)
    return adapter, plan, run(adapter.build(plan, context()))


def test_probe_reports_runtime_identities(runner):
    probe = run(make_adapter(runner).probe())
    assert probe.available is True and probe.mode == 'native' and probe.reasons == ()
    versions = {v.name: v.version for v in probe.native_versions}
    for name in ('picogk_runtime_so_sha256', 'regen_builder_sha256'):
        assert HEX64.match(versions[name]), (name, versions.get(name))


def test_build_small_is_geometry_only(built):
    from engineering.capabilities import REGEN
    _, _, b = built
    result = b.result
    assert result.outcome == 'completed' and result.mode == 'native'
    assert result.domain_outcome == 'accepted'
    assert result.claim_scope == 'geometry_checks_only'
    assert result.claim_scope != 'checked_design'
    assert REGEN.claim == 'geometry_checks_only' and result.claim_scope == REGEN.claim
    assert len(result.artifacts) == 1
    ref = result.artifacts[0]
    assert (ref.format, ref.representation, ref.node_id) == ('stl', 'mesh', 'regen.chamber')
    assert not any(a.format in ('step', 'glb') for a in result.artifacts)
    assert len(b.artifacts) == 1 and b.artifacts[0].ref == ref
    assert [e.operation_version for e in result.op_stream.entries] == ['0.2.0'] * len(result.op_stream.entries)
    assert all(e.operation.startswith('capability.') for e in result.op_stream.entries)


def test_evaluation_is_the_existing_geometry_only_evaluation(built):
    from engineering.capabilities import REGEN
    _, _, b = built
    assert b.evaluation is not None
    report = b.evaluation['report']
    assert report['passed'] is True
    assert report['physics']['state'] == 'not_evaluated'
    assert report['engineering_certified'] is False
    assert b.result.claim_scope == REGEN.claim == 'geometry_checks_only'


def test_reread_is_consistent(built):
    adapter, plan, b = built
    inspected = run(adapter.inspect(b.artifacts[0], plan, report=b.evaluation['report']))
    assert inspected.outcome == 'consistent', (inspected.reasons, inspected.deviations)
    assert inspected.artifact_sha256 == b.artifacts[0].ref.sha256
    assert inspected.mode == 'native'


def test_export_stl_only_never_step(built):
    adapter, _, b = built
    stl = b.artifacts[0]
    ok = adapter.export(stl, 'stl')
    assert ok.outcome == 'exported' and ok.artifact == stl.ref
    step = adapter.export(stl, 'step')
    assert step.outcome == 'unsupported' and step.artifact is None
    assert 'export_not_supported' in step.reasons


def test_unsupported_plan_submits_no_job(runner, candidate):
    adapter = make_adapter(runner)
    b = run(adapter.build(plan_of(candidate, exports=('step',)), context()))
    assert b.result.outcome == 'unsupported'
    assert 'export_not_supported' in b.result.reasons
    assert b.artifacts == ()
    assert len(runner.jobs) == 0


def test_missing_runtime_is_unavailable_and_submits_no_job(runner, candidate, tmp_path, monkeypatch):
    monkeypatch.setenv('REGEN_BUILDER', str(tmp_path / 'missing' / 'builder'))
    adapter = make_adapter(runner)
    probe = run(adapter.probe())
    assert probe.available is False and probe.reasons == ('runtime_missing',)
    b = run(adapter.build(plan_of(candidate), context()))
    assert b.result.outcome == 'unavailable' and b.result.outcome != 'unsupported'
    assert 'runtime_missing' in b.result.reasons
    assert len(runner.jobs) == 0


def test_cancellation_frees_the_slot(runner, candidate):
    adapter = make_adapter(runner)
    plan = plan_of(candidate)

    async def go():
        task = asyncio.create_task(adapter.build(plan, context()))
        deadline = time.monotonic() + 30
        while len(runner.jobs) < 1 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert len(runner.jobs) == 1, 'build never submitted a job'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 60)
        # The discard runs the runner's cancel; wait for no queued/running job.
        deadline = time.monotonic() + 30
        while (any(j['status'] in ('queued', 'running') for j in list(runner.jobs.values()))
               and time.monotonic() < deadline):
            await asyncio.sleep(0.1)
        assert not any(j['status'] in ('queued', 'running') for j in list(runner.jobs.values()))
        return await adapter.build(plan, context())

    again = run(go())
    assert again.result.outcome == 'completed'
    assert again.result.domain_outcome == 'accepted'
