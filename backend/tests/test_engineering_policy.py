"""Native evidence plus explicitly mutated fixtures; never physical proof."""
import asyncio
import copy
import hashlib

import pytest

from backend.tests.test_engineering_contracts import finalized


@pytest.fixture(scope='module')
def native():
    from engineering.cantilever import CantileverAdapter
    adapter = CantileverAdapter()
    return [asyncio.run(adapter.evaluate(c)) for c in adapter.candidates(finalized())]


def test_native_acceptance_and_conclusive_rejection(native):
    from engineering.policy import assess
    from engineering.contracts import Outcome
    decisions = [assess(e['candidate'], e) for e in native]
    assert [d.outcome for d in decisions] == [Outcome.REJECTED, Outcome.REJECTED, Outcome.ACCEPTED]
    assert all(d.available for d in decisions)
    assert decisions[0].reasons and decisions[1].reasons
    assert decisions[-1].checks == native[-1]['checks']


@pytest.mark.parametrize('mutation', [
    'missing_check', 'not_evaluated', 'unavailable', 'bare_pass', 'stale_brief',
    'stale_candidate', 'step_bytes', 'preview_bytes', 'source_bytes', 'report_identity',
    'report_missing', 'nonfinite', 'inflated_limit', 'wrong_method', 'wrong_verifier',
    'geometry_only', 'certified', 'standards', 'manufacturing', 'changed_measurement',
    'changed_solution', 'wrong_state', 'missing_native_check', 'mesh_flag', 'analytic_flag',
    'missing_native_metrics', 'manufacturing_uncertainty', 'missing_numerical_policy',
])
def test_mutated_or_incomplete_evidence_cannot_pass(native, mutation):
    from engineering.policy import assess, report_digest
    from engineering.contracts import CheckState, Outcome
    evidence = copy.deepcopy(native[-1])
    checks = list(evidence['checks'])
    report = evidence['report']
    if mutation == 'missing_check': checks.pop()
    if mutation in ('not_evaluated', 'unavailable'):
        checks[0] = checks[0].model_copy(update={'state': CheckState(mutation), 'reason': 'Injected missing evidence'})
    if mutation == 'bare_pass': checks[0] = checks[0].model_copy(update={'measurements': (), 'limits': ()})
    if mutation == 'stale_brief': checks[0] = checks[0].model_copy(update={'brief_sha256': 'f'*64})
    if mutation == 'stale_candidate': checks[0] = checks[0].model_copy(update={'candidate_sha256': 'e'*64})
    if mutation in ('step_bytes', 'preview_bytes', 'source_bytes'):
        key = {'step_bytes': 'model.step', 'preview_bytes': 'preview.glb', 'source_bytes': 'model.py'}[mutation]
        evidence['artifacts'][key] += b'changed'
    if mutation == 'report_identity': report['limitations'].append('changed after sealing')
    if mutation == 'report_missing': del report['physics']['solution']
    if mutation == 'nonfinite': report['physics']['solution']['tip_displacement_mm'] = float('nan')
    if mutation == 'inflated_limit':
        report['checks']['displacement_requirement']['limits'][0]['value'] = 1e9
        checks[-1] = checks[-1].model_copy(update={'limits': (checks[-1].limits[0].model_copy(update={'value': 1e9}),)})
    if mutation == 'wrong_method': checks[0] = checks[0].model_copy(update={'method': 'geometry_only'})
    if mutation == 'wrong_verifier': report['implementation_sha256']['verify.py'] = 'a'*64
    if mutation == 'geometry_only': report['scope'] = 'structural_solid_set_v1'
    if mutation == 'certified': report['engineering_certified'] = True
    if mutation == 'standards': report['standards']['state'] = 'passed'
    if mutation == 'manufacturing': report['manufacturing_release'] = True
    if mutation == 'changed_measurement': report['measurements']['height_mm'] += 1
    if mutation == 'changed_solution': report['physics']['solution']['root_bending_stress_mpa'] += 1
    if mutation == 'wrong_state': report['passed'] = False
    if mutation == 'missing_native_check': report['native_geometry']['checks'].pop('brep_valid_geometry')
    if mutation == 'mesh_flag': report['physics']['mesh_converged'] = False
    if mutation == 'analytic_flag': report['physics']['analytic_benchmark_passed'] = False
    if mutation == 'missing_native_metrics': report['native_geometry']['bounding_box']['x_mm'] = None
    if mutation == 'manufacturing_uncertainty': report['numerical_policy']['is_manufacturing_uncertainty'] = True
    if mutation == 'missing_numerical_policy': del report['numerical_policy']
    evidence['checks'] = tuple(checks)
    if mutation not in ('report_identity', 'nonfinite'):
        evidence['report_sha256'] = report_digest(report)
    decision = assess(evidence['candidate'], evidence)
    assert not decision.available and decision.outcome == Outcome.NOT_EVALUATED
    assert decision.reasons
    if mutation == 'inflated_limit':
        assert decision.reasons == ('Missing measurements or unsupported limits',)


def test_missing_prefix_check_is_unavailable_even_when_later_check_failed(native):
    from engineering.policy import assess, report_digest
    evidence = copy.deepcopy(native[0])
    del evidence['report']['checks']['native_geometry']
    evidence['report_sha256'] = report_digest(evidence['report'])
    assert not assess(evidence['candidate'], evidence).available


def test_valid_but_wrong_native_geometry_rejects_before_beam(tmp_path):
    import cadquery as cq
    from engineering.cantilever import report_checks
    from engineering.contracts import CandidateSpec, Outcome
    from engineering.policy import assess, report_digest
    from engineering.verify import verify_step
    candidate = CandidateSpec.from_brief(finalized(), 15.0)
    path = tmp_path/'wrong-size.step'
    cq.exporters.export(cq.Workplane('XY').box(200, 10, 14, centered=(False, False, False)), str(path))
    report = verify_step(str(path), candidate.specification)
    checks = report_checks(candidate, report)
    evidence = dict(candidate=candidate, outcome=Outcome.REJECTED, checks=checks,
                    report=report, report_sha256=report_digest(report),
                    step_identity=checks[0].artifacts[0], artifacts={})
    decision = assess(candidate, evidence)
    assert decision.available and decision.outcome == Outcome.REJECTED
    assert report['physics']['state'] == 'not_evaluated'


def test_search_enforces_policy_even_for_false_passing_adapter(native):
    from engineering.search import search
    from engineering.cantilever import CantileverAdapter
    class Forged(CantileverAdapter):
        async def evaluate(self, candidate):
            evidence = copy.deepcopy(native[0])
            evidence['outcome'] = 'accepted'
            return evidence
    result = asyncio.run(search(finalized(), adapter=Forged()))
    assert result.state == 'unavailable' and result.selected is None
