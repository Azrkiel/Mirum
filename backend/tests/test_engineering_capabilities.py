"""V1 metadata/preparation contract; native execution belongs to B04."""
import inspect
import subprocess
import sys

import pytest

from backend.tests.test_engineering_spec import spec_data


def test_registry_pins_one_bounded_capability_and_all_required_evidence():
    from engineering.capabilities import CAPABILITY, get_capability, list_capabilities
    descriptor = get_capability(CAPABILITY.id, CAPABILITY.version)
    assert list_capabilities() == (descriptor,)
    assert descriptor.units == 'mm_N_MPa'
    assert descriptor.exports == ('step', 'glb', 'source', 'brief', 'specification',
                                  'attempts', 'report_json', 'report_markdown', 'manifest', 'zip')
    assert descriptor.geometry_scope == 'single_solid_axis_aligned_rectangular_prism_positive_xyz'
    assert descriptor.physics_method == 'euler_bernoulli_beam_fem_v1'
    assert descriptor.engineering_certified is False
    assert descriptor.standards == 'not_evaluated'
    assert set(descriptor.required_checks) == {
        'native_geometry', 'dimensional_evidence_budget', 'independent_step_read',
        'independent_step_transfer', 'single_rectangular_solid', 'planar_faces',
        'finite_measurements', 'size_requirements', 'datum_frame', 'axis_aligned_faces',
        'rectangular_face_pairs', 'complete_prism_metrics', 'measured_root_section',
        'mesh_convergence', 'analytic_benchmark', 'equilibrium', 'small_deflection',
        'small_strain', 'stress_allowable', 'displacement_requirement'}
    assert descriptor.resources.max_candidates == 4
    assert descriptor.resources.native_stage_seconds == 30
    assert descriptor.resources.native_memory_mib == 1536
    assert descriptor.resources.overall_seconds == 420
    assert descriptor.resources.run_bytes == 32 * 1024**2
    assert descriptor.numerical.max_displacement_span_ratio == 0.02
    assert descriptor.numerical.max_strain == 0.005


@pytest.mark.parametrize('versions', [
    {'capability_id': 'glider'}, {'version': '2.0.0'},
    {'numerical_policy_version': 'unknown'}, {'resource_policy_version': 'unknown'},
])
def test_unknown_versions_require_resolution_without_fallback(versions):
    from engineering.capabilities import CAPABILITY, UnsupportedCapability, get_capability
    request = dict(capability_id=CAPABILITY.id, version=CAPABILITY.version)
    request.update(versions)
    with pytest.raises(UnsupportedCapability) as caught:
        get_capability(**request)
    assert caught.value.as_dict()['state'] == 'unsupported'


@pytest.mark.parametrize('feature', [
    'holes', 'fillets', 'extra_bodies', 'varying_cross_section', 'other_supports',
    'other_loads', 'shear_deformation', 'fatigue', 'buckling', 'contact', 'thermal',
    'general_3d_stress', 'physical_material_qualification', 'manufacturing_variation',
    'standards_certification', 'unregistered_feature',
])
def test_each_excluded_feature_has_an_explicit_unsupported_response(feature):
    from engineering.capabilities import UnsupportedCapability, validate_beam_request
    with pytest.raises(UnsupportedCapability, match=feature) as caught:
        validate_beam_request(spec_data(), features=(feature,))
    assert caught.value.as_dict()['reason']


@pytest.mark.parametrize('case', [
    {'support': 'simple_support'}, {'load': 'uniform_load'},
    {'specification': spec_data(width_mm=21.0)},
    {'specification': spec_data(holes=1)},
])
def test_unsupported_support_load_non_slender_and_unknown_fields_fail(case):
    from engineering.capabilities import UnsupportedCapability, validate_beam_request
    args = dict(specification=spec_data())
    args.update(case)
    with pytest.raises(UnsupportedCapability):
        validate_beam_request(**args)


def test_reviewed_modes_sort_deduplicate_and_do_not_change_legacy_order():
    from engineering.capabilities import validate_beam_request
    from engineering.service import approved_candidates
    from engineering.spec import CantileverSpec
    spec = CantileverSpec.model_validate(spec_data(tip_force_n=50.0))
    choices = validate_beam_request(spec.model_dump(), mode='bounded_thickness',
                                   candidate_heights_mm=[15.0, 5.0, 10.0, 5.0])
    assert [s.height_mm for s in choices] == [5.0, 10.0, 15.0]
    assert [s.height_mm for s in approved_candidates(spec, [15.0, 5.0, 10.0])] == [15, 5, 10]
    assert validate_beam_request(spec.model_dump())[0] == spec


@pytest.mark.parametrize('changes', [
    {'mode': 'optimize'}, {'mode': 'bounded_thickness'},
    {'candidate_heights_mm': [5.0, 10.0]},
    {'mode': 'bounded_thickness', 'candidate_heights_mm': [True]},
    {'mode': 'bounded_thickness', 'candidate_heights_mm': [float('nan')]},
    {'mode': 'bounded_thickness', 'candidate_heights_mm': [5.0, 10.0, 15.0, 18.0, 19.0]},
])
def test_ambiguous_or_invalid_choices_cannot_be_reviewed(changes):
    from engineering.capabilities import UnsupportedCapability, validate_beam_request
    with pytest.raises(UnsupportedCapability):
        validate_beam_request(spec_data(), **changes)


def test_metadata_import_does_not_initialize_native_or_ai_runtime():
    code = '''import sys
from engineering.capabilities import CAPABILITY, list_capabilities
assert list_capabilities() == (CAPABILITY,)
assert not any(m in sys.modules for m in ['google.generativeai', 'agents', 'cadquery', 'OCP', 'numpy'])
'''
    subprocess.run([sys.executable, '-c', code], check=True)


def test_existing_cem_contracts_keep_their_signatures_and_identity():
    from cem.core import CEM, canonical_json, digest, output_id
    from cem.glider.model import configuration
    from cem.glider.service import build_design
    assert list(inspect.signature(CEM.size).parameters) == ['self', 'req', 'budget']
    assert list(inspect.signature(configuration).parameters) == ['req', 'calibration', 'process']
    assert list(inspect.signature(build_design).parameters) == ['req', 'calibration', 'elite', 'directory']
    assert len(digest({'beam': 1})) == 64
    assert canonical_json({'b': 1, 'a': 2}) == '{"a":2,"b":1}'
    assert callable(output_id)
