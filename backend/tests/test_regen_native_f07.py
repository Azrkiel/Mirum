"""F07 task 6 (P05): one real PicoGK build -> PicogkAdapter.inspect -> geometry_checks_only evidence.

Runs only in the picogk-runtime Docker job (REGEN_NATIVE=1, REGEN_BUILDER present). Self-contained like
test_regen_native_adapter.py: that job runs pytest with -c /dev/null, so this module imports only
engineering.* and the standard library. One native build (module fixture) serves both tests.
"""
import asyncio
import json
import os
import re
from pathlib import Path

import pytest

BUILDER = Path(os.environ.get('REGEN_BUILDER', '/opt/regen/app/builder'))
EXAMPLES = Path(__file__).resolve().parents[2] / 'picogk-builder' / 'examples'

pytestmark = pytest.mark.skipif(
    os.environ.get('REGEN_NATIVE') != '1' or not BUILDER.exists(),
    reason='needs the native PicoGK builder (REGEN_NATIVE=1, REGEN_BUILDER)')

HEX64 = re.compile(r'^[0-9a-f]{64}$')
RID, JID = '1' * 32, '2' * 32


def run(coro):
    return asyncio.run(coro)


def context(deadline_s=600):
    from engineering.capabilities import REGEN
    from engineering.program.kernel import ExecutionContext
    return ExecutionContext(deadline_s=deadline_s, max_artifact_bytes=REGEN.resources.run_bytes,
                            workspace='f07-native')


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
        record_id='f07-record', created_at='2026-11-22T12:00:00Z', input_mode='form',
        field_origins=[dict(field=f, origin='user_supplied', reference='Typed input')
                       for f in content.review_fields])))
    draft = BriefDraft(draft_id='f07-draft', revision=1, content=content, clarifications=(),
                       provenance=provenance)
    review = ReviewAcknowledgement.model_validate_json(json.dumps(dict(
        draft_id='f07-draft', draft_revision=1, brief_sha256=content.brief_sha256,
        acknowledged_fields=list(content.review_fields),
        reviewed_at='2026-11-22T12:05:00Z', reviewed_by='local-user')))
    brief = EngineeringBrief.from_draft(draft, review, brief_id='f07-brief')
    found = RegenAdapter().candidates(brief)
    assert len(found) == 1
    return found[0]


@pytest.fixture(scope='module')
def evidence_bundle(tmp_path_factory):
    """One real adapter build of small.json, its reread, and the mapped evidence."""
    from engineering.program import kernel
    from engineering.program.adapters.picogk import PicogkAdapter
    from engineering.program.evidence import evidence_from_picogk_inspect
    from engineering.regen_web import JobRunner
    candidate = candidate_for('small.json')
    runner = JobRunner(tmp_path_factory.mktemp('f07') / 'jobs')
    try:
        adapter = PicogkAdapter(runner=lambda: runner)
        plan = kernel.plan_for('regen_chamber', '1.0.0', 'picogk', candidate, ('stl',))
        built = run(adapter.build(plan, context()))
        assert built.result.outcome == 'completed' and built.result.domain_outcome == 'accepted', (
            built.result.outcome, built.result.reasons)
        assert len(built.artifacts) == 1
        report = built.evaluation['report']
        inspected = run(adapter.inspect(built.artifacts[0], plan, report=report))
        probe = run(adapter.probe())
    finally:
        for job in list(runner.jobs.values()):
            runner.cancel(job['id'])
    voxel = float(candidate.content.specification['voxel_size_mm'])
    volume = float(report['checks']['volume']['measured_mm3'])
    evidence = evidence_from_picogk_inspect(
        inspected, revision_id=RID, job_id=JID, candidate_sha256=candidate.candidate_sha256,
        manifest_sha256=built.artifacts[0].ref.sha256, method_version='f07-native',
        voxel_size_mm=voxel, report_volume_mm3=volume, native_versions=probe.native_versions)
    return inspected, evidence, voxel, volume


def test_real_picogk_inspect_maps_to_geometry_checks_only_evidence(evidence_bundle):
    from engineering.capabilities import REGEN
    from engineering.program.evidence import DeliveredEvidence
    inspected, evidence, _, _ = evidence_bundle
    assert inspected.outcome == 'consistent' and inspected.mode == 'native', (
        inspected.reasons, inspected.deviations)
    assert evidence.claim_scope == 'geometry_checks_only' == REGEN.claim
    assert evidence.verdict != 'accepted' and evidence.verdict == 'verified', (
        evidence.verdict, evidence.reasons)
    by_id = {c.check_id: c for c in evidence.checks}
    assert {'geometry.bounds', 'geometry.volume'} <= set(by_id)
    for cat in ('physics', 'manufacturing', 'standards'):
        c = by_id[f'{cat}.not_evaluated']
        assert (c.state, c.coverage, c.required) == ('not_evaluated', 'not_evaluated', False)
    dump = evidence.model_dump_json().lower()
    for label in ('beam.', 'delivered.', 'checked_design'):
        assert label not in dump, label
    assert evidence.bindings == ()
    # the record validates again from its own JSON (every validator runs)
    assert DeliveredEvidence.model_validate_json(evidence.model_dump_json()) == evidence


def test_every_real_measurement_carries_representation_and_error_bound(evidence_bundle):
    from engineering.capabilities import REGEN
    _, evidence, voxel, _ = evidence_bundle
    declared = {r.name: r.value for r in REGEN.numerical}
    measured = [c for c in evidence.checks if c.measurements]
    assert {c.check_id for c in measured} == {'geometry.bounds', 'geometry.volume'}
    for c in measured:
        assert c.state == 'passed' and c.coverage == 'measured' and c.representation == 'mesh'
        assert c.error_bound is not None and c.error_bound.value > 0
        for m in c.measurements:
            assert m.representation == 'mesh' and m.satisfied
    by_id = {c.check_id: c for c in evidence.checks}
    vol = by_id['geometry.volume']
    assert vol.error_bound.value == declared['VOLUME_TOLERANCE']
    assert vol.measurements[0].limit.value == declared['VOLUME_TOLERANCE']
    bounds = by_id['geometry.bounds']
    assert bounds.error_bound.value == pytest.approx(declared['BBOX_TOLERANCE_VOXELS'] * voxel)
    assert all(m.limit.value == declared['BBOX_TOLERANCE_VOXELS'] for m in bounds.measurements)
    assert all(HEX64.match(v.version) for v in evidence.native_versions
               if v.name.endswith('_sha256'))
