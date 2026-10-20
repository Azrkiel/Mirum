import importlib.util
import pytest
from backend.tests.test_engineering_spec import spec_data


def test_reimported_step_measures_design_intent_before_physics(tmp_path):
    assert importlib.util.find_spec('engineering.verify') is not None, 'Independent geometry/physics verifier is missing'
    import cadquery as cq
    from engineering.spec import CantileverSpec
    from engineering.verify import verify_step
    spec = CantileverSpec.model_validate(spec_data())
    path = tmp_path / 'beam.step'
    cq.exporters.export(cq.Workplane('XY').box(200, 10, 10, centered=(False, False, False)), str(path))
    report = verify_step(str(path), spec)
    assert report['passed'] is True, report
    assert report['design_intent']['state'] == 'passed'
    assert report['measurements']['section_iy_mm4'] == pytest.approx(10 * 10**3 / 12)
    assert report['physics']['state'] == 'passed'
    assert report['physics']['method'] == 'euler_bernoulli_beam_fem_v1'
    assert report['physics']['mesh_converged'] is True
    assert report['physics']['analytic_benchmark_passed'] is True
    assert report['standards']['state'] == 'not_evaluated'
    assert report['engineering_certified'] is False
    assert report['versions']['numpy']
    assert set(report['implementation_sha256']) >= {'beam_fea.py', 'verify.py', 'spec.py', 'solid_set.py'}


@pytest.mark.parametrize('adversary', ['wrong_size', 'offset', 'hole', 'two_bodies', 'rotated', 'oversize_centered'])
def test_valid_brep_is_not_sufficient_for_design_intent(tmp_path, adversary):
    import cadquery as cq
    from engineering.spec import CantileverSpec
    from engineering.verify import verify_step
    shape = cq.Workplane('XY').box(200,10,10,centered=(False,False,False))
    if adversary == 'wrong_size':
        shape = cq.Workplane('XY').box(200,10,9,centered=(False,False,False))
    elif adversary == 'offset':
        shape = shape.translate((1,0,0))
    elif adversary == 'hole':
        shape = shape.cut(cq.Workplane('XY').center(100,5).circle(1).extrude(10))
    elif adversary == 'two_bodies':
        shape = cq.Compound.makeCompound([shape.val(), shape.translate((0,30,0)).val()])
    elif adversary == 'rotated':
        shape = shape.rotate((0,0,0),(1,0,0),15)
    else:
        shape = cq.Workplane('XY').box(200.015,10,10,centered=(False,False,False)).translate((-.0075,0,0))
    path = tmp_path / 'wrong.step'
    cq.exporters.export(shape, str(path))
    report = verify_step(str(path), CantileverSpec.model_validate(spec_data()))
    assert report['native_geometry']['passed'] is True, report
    assert report['passed'] is False
    assert report['design_intent']['state'] == 'failed'
    assert report['physics']['state'] == 'not_evaluated'


@pytest.mark.parametrize('changes, expected', [
    ({'tip_force_n': 1000.0}, 'small_deflection'),
    ({'allowable_stress_mpa': 1.0}, 'stress_allowable'),
    ({'max_tip_deflection_mm': 0.01}, 'displacement_requirement'),
])
def test_valid_geometry_with_failed_physics_is_rejected(tmp_path, changes, expected):
    import cadquery as cq
    from engineering.spec import CantileverSpec
    from engineering.verify import verify_step
    path = tmp_path / 'unsafe.step'
    cq.exporters.export(cq.Workplane('XY').box(200,10,10,centered=(False,False,False)),str(path))
    report = verify_step(str(path),CantileverSpec.model_validate(spec_data(**changes)))
    assert report['passed'] is False
    assert report['design_intent']['state'] == 'passed'
    assert report['checks'][expected]['state'] == 'failed'


def test_native_uncertainty_cannot_exceed_dimensional_evidence_budget(tmp_path, monkeypatch):
    """Reader-report seam: no claim that STEP preserves an inflated tolerance."""
    import cadquery as cq
    import verification.solid_set as native
    from engineering.spec import CantileverSpec
    from engineering.verify import verify_step
    path = tmp_path / 'beam.step'
    cq.exporters.export(cq.Workplane('XY').box(200,10,10,centered=(False,False,False)),str(path))
    real = native.inspect_step
    def inflated_report(path):
        result = real(path)
        result['max_topology_tolerance_mm'] = 0.001
        return result
    monkeypatch.setattr(native, 'inspect_step', inflated_report)
    report = verify_step(str(path), CantileverSpec.model_validate(spec_data(dimensional_tolerance_mm=0.00001)))
    assert report['passed'] is False, 'Coarse native uncertainty cannot substantiate a fine dimension check'
    assert report['checks']['dimensional_evidence_budget']['state'] == 'failed'

