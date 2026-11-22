"""K37 native timing of the inspect STL reread against the real regen builder.

Runs only in the picogk-runtime Docker job (REGEN_NATIVE=1, REGEN_BUILDER present). Self-contained:
imports only engineering.* and the standard library, because that job runs pytest with -c /dev/null
and no other test module on the path. It pins picogk.INSPECT_DEADLINE_S: the measured reread time must
leave at least a 5x margin. Run with -rA so the printed timing reaches the log.
"""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

BUILDER = Path(os.environ.get('REGEN_BUILDER', '/opt/regen/app/builder'))
EXAMPLES = Path(__file__).resolve().parents[2] / 'picogk-builder' / 'examples'

pytestmark = pytest.mark.skipif(
    os.environ.get('REGEN_NATIVE') != '1' or not BUILDER.exists(),
    reason='needs the native PicoGK builder (REGEN_NATIVE=1, REGEN_BUILDER)')


def run(coro):
    return asyncio.run(coro)


def context(deadline_s=600):
    from engineering.capabilities import REGEN
    from engineering.program.kernel import ExecutionContext
    return ExecutionContext(deadline_s=deadline_s, max_artifact_bytes=REGEN.resources.run_bytes,
                            workspace='k37-native')


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
        record_id='k37-record', created_at='2026-11-17T12:00:00Z', input_mode='form',
        field_origins=[dict(field=f, origin='user_supplied', reference='Typed input')
                       for f in content.review_fields])))
    draft = BriefDraft(draft_id='k37-draft', revision=1, content=content, clarifications=(),
                       provenance=provenance)
    review = ReviewAcknowledgement.model_validate_json(json.dumps(dict(
        draft_id='k37-draft', draft_revision=1, brief_sha256=content.brief_sha256,
        acknowledged_fields=list(content.review_fields),
        reviewed_at='2026-11-17T12:05:00Z', reviewed_by='local-user')))
    brief = EngineeringBrief.from_draft(draft, review, brief_id='k37-brief')
    found = RegenAdapter().candidates(brief)
    assert len(found) == 1
    return found[0]


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    """One real adapter build of small.json shared by the timing and the inspect checks."""
    from engineering.program import kernel
    from engineering.program.adapters.picogk import PicogkAdapter
    from engineering.regen_web import JobRunner
    runner = JobRunner(tmp_path_factory.mktemp('k37') / 'jobs')
    adapter = PicogkAdapter(runner=lambda: runner)
    plan = kernel.plan_for('regen_chamber', '1.0.0', 'picogk', candidate_for('small.json'), ('stl',))
    result = run(adapter.build(plan, context()))
    assert result.result.outcome == 'completed', result.result.reasons
    assert len(result.artifacts) == 1
    yield adapter, plan, result
    for job in list(runner.jobs.values()):
        runner.cancel(job['id'])


def test_reread_time_leaves_a_5x_margin_under_the_inspect_deadline(built):
    from engineering.program.adapters import picogk
    _, _, b = built
    data = b.artifacts[0].data
    t0 = time.monotonic()
    mesh = picogk._reread_mesh(data)
    seconds = time.monotonic() - t0
    print(f'K37 reread seconds: {seconds:.3f}')
    print(f'K37 stl bytes: {len(data)}')
    print(f'K37 inspect deadline seconds: {picogk.INSPECT_DEADLINE_S}')
    assert mesh['volume_mm3'] > 0
    assert picogk.INSPECT_DEADLINE_S >= 5 * seconds, (seconds, picogk.INSPECT_DEADLINE_S)


def test_inspect_through_the_bounded_path_is_consistent(built):
    adapter, plan, b = built
    inspected = run(adapter.inspect(b.artifacts[0], plan, report=b.evaluation['report']))
    assert inspected.outcome == 'consistent', (inspected.reasons, inspected.deviations)
    assert inspected.artifact_sha256 == b.artifacts[0].ref.sha256
