import importlib.util
import pytest


def spec_data(**changes):
    values = dict(family='rectangular_cantilever', units='mm_N_MPa',
                  length_mm=200.0, width_mm=10.0, height_mm=10.0,
                  dimensional_tolerance_mm=0.01, youngs_modulus_mpa=70000.0,
                  allowable_stress_mpa=120.0, tip_force_n=10.0,
                  max_tip_deflection_mm=1.0,
                  material_reference='User-approved illustrative aluminium allowable; not a certified stock value',
                  assumptions='ideal_clamp_end_force_linear_elastic')
    values.update(changes)
    return values


def test_typed_spec_emits_identical_source_and_digest_independent_of_key_order():
    assert importlib.util.find_spec('engineering') is not None, 'Typed engineering package is missing'
    from engineering.spec import CantileverSpec, build_source, specification_digest
    a = CantileverSpec.model_validate(spec_data())
    b = CantileverSpec.model_validate(dict(reversed(list(spec_data().items()))))
    assert build_source(a) == build_source(b)
    assert specification_digest(a) == specification_digest(b)
    assert '200.0' in build_source(a)
    assert a.family == 'rectangular_cantilever'


@pytest.mark.parametrize('changes', [
    {'tip_force_n': 0.0}, {'height_mm': -1.0}, {'youngs_modulus_mpa': float('nan')},
    {'tip_force_n': float('inf')}, {'width_mm': 21.0}, {'units': 'inch'},
    {'material_reference': ' '}, {'material_reference': ''}, {'length_mm': True},
    {'length_mm': '200'}, {'standard': 'ASME certified'}, {'max_tip_deflection_mm': 5.0},
    {'dimensional_tolerance_mm': 0.2},
])
def test_spec_rejects_missing_unsupported_or_nonphysical_evidence(changes):
    from engineering.spec import CantileverSpec
    with pytest.raises(ValueError):
        CantileverSpec.model_validate(spec_data(**changes))

