import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from types import MappingProxyType

from pydantic import ConfigDict, TypeAdapter, ValidationError
import pytest

from engineering.plate_spec import (
    CHECK_IDS,
    EDGE_CONDITIONS,
    FAMILY,
    FAMILY_VERSION,
    GUARD_IDS,
    PLATE_NUMERICAL_V1,
    PlateBrief,
    PlateMaterialReference,
    PlateRequirements,
    PlateSpec,
    canonical_spec,
    evaluate_static_guards,
    first_failed_guard,
    specification_digest,
)


def plate_data(**overrides):
    values = dict(
        family='rectangular_plate',
        units='mm_N_MPa',
        length_a_mm=600.0,
        width_b_mm=400.0,
        thickness_mm=10.0,
        edge_condition='simply_supported',
        pressure_mpa=0.01,
        youngs_modulus_mpa=200000.0,
        poissons_ratio=0.3,
        allowable_stress_mpa=150.0,
        max_deflection_mm=2.0,
        dimensional_tolerance_mm=0.01,
        material_reference={
            'source_kind': 'illustrative',
            'reference': 'Structural steel, illustrative',
        },
        assumptions='kirchhoff_uniform_pressure_uniform_edges_linear_elastic',
    )
    values.update(overrides)
    return values


def brief_data(thicknesses=(10.0,), mode='bounded_thickness', **overrides):
    requirements = plate_data(**overrides)
    del requirements['thickness_mm']
    return dict(mode=mode, requirements=requirements, candidate_thicknesses_mm=thicknesses)


def test_numerical_policy_matches_signed_contract_and_is_immutable():
    assert isinstance(PLATE_NUMERICAL_V1, MappingProxyType)
    assert PLATE_NUMERICAL_V1 == {
        'version': 'plate_numerical_v1',
        'length_a_mm_gt': 20.0,
        'length_a_mm_le': 3000.0,
        'width_b_mm_gt': 20.0,
        'width_b_mm_le': 3000.0,
        'thickness_mm_gt': 0.1,
        'thickness_mm_le': 150.0,
        'pressure_mpa_gt': 0.0,
        'pressure_mpa_le': 10.0,
        'youngs_modulus_mpa_gt': 0.0,
        'youngs_modulus_mpa_le': 1e7,
        'poissons_ratio_min': 0.1,
        'poissons_ratio_max': 0.45,
        'allowable_stress_mpa_gt': 0.0,
        'allowable_stress_mpa_le': 1e5,
        'max_deflection_mm_gt': 0.0,
        'max_deflection_mm_le': 100.0,
        'max_deflection_width_ratio': 0.02,
        'dimensional_tolerance_mm_min': 1e-5,
        'dimensional_tolerance_mm_max': 0.1,
        'tolerance_thickness_ratio': 0.01,
        'material_reference_max_chars': 240,
        'aspect_ratio_min': 1.0,
        'aspect_ratio_max': 5.0,
        'thin_ratio_min': 20.0,
        'small_deflection_max_ratio': 0.2,
        'max_candidates': 4,
        'mesh_levels_n_b': (4, 8, 16),
        'max_dof': 6000,
        'stress_gauss_points_per_direction': 4,
        'mesh_convergence_deflection': 0.002,
        'mesh_convergence_stress': 0.01,
        'equilibrium': 1e-9,
        'benchmark_deflection': 0.005,
        'benchmark_stress': 0.015,
        'shear_allowance_coefficient': 20.0,
        'geometry_volume_relative': 1e-6,
        'navier_relative_stop': 1e-10,
        'navier_term_cap': 401,
        'fd_grid_multipliers': (1, 2, 4),
        'strip_limit_relative': 0.02,
        'strip_limit_aspect_ratio': 5.0,
        'r3_relative_tolerance': 0.005,
        'r3_nu': 0.3,
    }
    with pytest.raises(TypeError):
        PLATE_NUMERICAL_V1['version'] = 'changed'
    for value in PLATE_NUMERICAL_V1.values():
        hash(value)


def test_family_constants_match_signed_contract():
    assert FAMILY == 'rectangular_plate'
    assert FAMILY_VERSION == '1.0.0'
    assert EDGE_CONDITIONS == ('simply_supported', 'clamped')
    assert GUARD_IDS == ('thin_ratio', 'small_deflection', 'aspect_ratio', 'material')
    assert CHECK_IDS == (
        'geometry', 'mesh_convergence_deflection', 'mesh_convergence_stress',
        'equilibrium', 'symmetry', 'benchmark_deflection', 'benchmark_stress',
        'deflection_limit', 'stress_limit',
    )


FLOAT_BOUNDS = [
    ('length_a_mm', 20.0, 3000.0, False),
    ('width_b_mm', 20.0, 3000.0, False),
    ('thickness_mm', 0.1, 150.0, False),
    ('pressure_mpa', 0.0, 10.0, False),
    ('youngs_modulus_mpa', 0.0, 1e7, False),
    ('poissons_ratio', 0.1, 0.45, True),
    ('allowable_stress_mpa', 0.0, 1e5, False),
    ('max_deflection_mm', 0.0, 100.0, False),
    ('dimensional_tolerance_mm', 1e-5, 0.1, True),
]


@pytest.mark.parametrize('field,lower,upper,inclusive', FLOAT_BOUNDS)
def test_scalar_bounds(field, lower, upper, inclusive):
    # Isolate each scalar bound from the coupled bounds on valid plate dimensions.
    adapter = TypeAdapter(
        PlateSpec.model_fields[field].rebuild_annotation(),
        config=ConfigDict(strict=True, allow_inf_nan=False),
    )
    with pytest.raises(ValidationError):
        adapter.validate_python(math.nextafter(lower, -math.inf))
    if inclusive:
        assert adapter.validate_python(lower) == lower
    else:
        with pytest.raises(ValidationError):
            adapter.validate_python(lower)
    inside = math.nextafter(lower, upper)
    assert adapter.validate_python(inside) == inside
    assert adapter.validate_python(upper) == upper
    with pytest.raises(ValidationError):
        adapter.validate_python(math.nextafter(upper, math.inf))


@pytest.mark.parametrize('field', [bound[0] for bound in FLOAT_BOUNDS])
@pytest.mark.parametrize('invalid', [math.nan, math.inf, -math.inf])
def test_every_float_rejects_nonfinite_values(field, invalid):
    with pytest.raises(ValidationError) as caught:
        PlateSpec.model_validate(plate_data(**{field: invalid}))
    assert caught.value.errors()[0]['loc'] == (field,)


@pytest.mark.parametrize('field', [bound[0] for bound in FLOAT_BOUNDS])
def test_every_float_rejects_bools_and_numeric_strings(field):
    for invalid in (True, False, str(plate_data()[field])):
        with pytest.raises(ValidationError) as caught:
            PlateSpec.model_validate(plate_data(**{field: invalid}))
        error = caught.value.errors()[0]
        assert error['loc'] == (field,)
        assert error['type'] == 'float_type'


def test_strict_float_fields_accept_integers():
    defaults = plate_data()
    integers = {field: int(defaults[field]) for field in (
        'length_a_mm', 'width_b_mm', 'thickness_mm', 'youngs_modulus_mpa',
        'allowable_stress_mpa', 'max_deflection_mm',
    )}
    spec = PlateSpec.model_validate(plate_data(**integers))
    for field, value in integers.items():
        assert type(getattr(spec, field)) is float
        assert getattr(spec, field) == value


@pytest.mark.parametrize('overrides,rule', [
    ({'length_a_mm': 399.0}, 'length_a_mm >= width_b_mm'),
    ({'length_a_mm': math.nextafter(2000.0, math.inf)}, 'aspect_ratio'),
    ({'max_deflection_mm': math.nextafter(8.0, math.inf)}, 'max_deflection_mm'),
    ({'dimensional_tolerance_mm': math.nextafter(0.05, math.inf), 'thickness_mm': 5.0},
     'dimensional_tolerance_mm'),
])
def test_spec_rejects_values_exceeding_cross_field_limits(overrides, rule):
    with pytest.raises(ValidationError, match=rule):
        PlateSpec.model_validate(plate_data(**overrides))


@pytest.mark.parametrize('overrides', [
    {'length_a_mm': 400.0},
    {'length_a_mm': 2000.0},
    {'max_deflection_mm': 8.0},
    {'length_a_mm': 3000.0, 'width_b_mm': 3000.0, 'max_deflection_mm': 60.0},
    {'dimensional_tolerance_mm': 0.05, 'thickness_mm': 5.0},
])
def test_spec_accepts_exact_cross_field_limits(overrides):
    spec = PlateSpec.model_validate(plate_data(**overrides))
    for field, value in overrides.items():
        assert getattr(spec, field) == value


def test_deflection_ratio_caps_widest_plate_below_scalar_maximum():
    with pytest.raises(ValidationError, match='max_deflection_mm'):
        PlateSpec.model_validate(plate_data(
            length_a_mm=3000.0, width_b_mm=3000.0, max_deflection_mm=100.0,
        ))


@pytest.mark.parametrize('changes', [
    {'family': 'rectangular_cantilever'},
    {'units': 'inch'},
    {'edge_condition': 'free'},
    {'edge_condition': 'mixed'},
    {'assumptions': 'large_deflection'},
    {'cutouts': []},
    {'material_reference': {'source_kind': 'documented', 'reference': 'Table 1', 'extra': 1}},
])
def test_spec_rejects_unknown_fields_and_unsupported_literals(changes):
    with pytest.raises(ValidationError):
        PlateSpec.model_validate(plate_data(**changes))


@pytest.mark.parametrize('kind', ['illustrative', 'user_supplied', 'documented'])
@pytest.mark.parametrize('size', [1, 240])
def test_material_reference_strips_text_and_accepts_length_limits(kind, size):
    text = 'x' * size
    material = PlateMaterialReference(source_kind=kind, reference=f'  {text}\t')
    assert material.reference == text
    assert material.source_kind == kind


@pytest.mark.parametrize('reference', ['', ' \t\n ', 'x' * 241, 123, b'steel'])
def test_material_reference_rejects_invalid_text(reference):
    with pytest.raises(ValidationError):
        PlateMaterialReference(source_kind='documented', reference=reference)


def test_material_reference_rejects_unknown_source_kind():
    with pytest.raises(ValidationError):
        PlateMaterialReference(source_kind='certified', reference='Table 1')


def test_models_are_frozen():
    spec = PlateSpec.model_validate(plate_data())
    brief = PlateBrief.model_validate(brief_data())
    for model, field, value in (
        (spec, 'thickness_mm', 20.0),
        (spec.material_reference, 'reference', 'New reference'),
        (brief.requirements, 'length_a_mm', 800.0),
        (brief, 'mode', 'fixed_design'),
    ):
        with pytest.raises(ValidationError, match='frozen'):
            setattr(model, field, value)


def test_spec_calculates_aspect_ratio_and_flexural_rigidity():
    spec = PlateSpec.model_validate(plate_data(edge_condition='clamped'))
    assert spec.aspect_ratio == 1.5
    assert spec.flexural_rigidity_n_mm == pytest.approx(18_315_018.315018315)


def test_static_guards_report_quantities_and_limits_in_order():
    spec = PlateSpec.model_validate(plate_data(thickness_mm=20.0))
    assert evaluate_static_guards(spec) == (
        {'guard': 'thin_ratio', 'quantity': 20.0, 'limit': 20.0, 'passed': True},
        {'guard': 'aspect_ratio', 'quantity': 1.5, 'limit': (1.0, 5.0), 'passed': True},
        {'guard': 'material', 'quantity': 0.3, 'limit': (0.1, 0.45), 'passed': True},
    )
    assert first_failed_guard(spec) is None


@pytest.mark.parametrize('a,nu', [(400.0, 0.1), (2000.0, 0.45)])
def test_static_aspect_and_material_guards_include_endpoints(a, nu):
    spec = PlateSpec.model_validate(plate_data(length_a_mm=a, poissons_ratio=nu))
    assert first_failed_guard(spec) is None


def test_thin_ratio_just_below_limit_is_a_measured_failure():
    spec = PlateSpec.model_validate(plate_data(thickness_mm=math.nextafter(20.0, math.inf)))
    guard, *_ = evaluate_static_guards(spec)
    assert guard == {
        'guard': 'thin_ratio',
        'quantity': 400.0 / spec.thickness_mm,
        'limit': 20.0,
        'passed': False,
    }
    assert first_failed_guard(spec) == 'thin_ratio'


@pytest.mark.parametrize('changes,failed', [
    ({'length_a_mm': 399.0}, 'aspect_ratio'),
    ({'length_a_mm': 2001.0}, 'aspect_ratio'),
    ({'youngs_modulus_mpa': 0.0}, 'material'),
    ({'poissons_ratio': 0.09}, 'material'),
    ({'poissons_ratio': 0.46}, 'material'),
    ({'thickness_mm': 21.0, 'youngs_modulus_mpa': 0.0}, 'thin_ratio'),
])
def test_static_guards_detect_invalid_measurements_in_precedence_order(changes, failed):
    # Unvalidated copies exercise the guard conditions independently of spec validation.
    spec = PlateSpec.model_validate(plate_data()).model_copy(update=changes)
    assert first_failed_guard(spec) == failed


def test_brief_sorts_and_deduplicates_before_counting_candidates():
    brief = PlateBrief.model_validate(brief_data((25.0, 10, 15.0, 10.0, 20.0, 25.0)))
    assert brief.candidate_thicknesses_mm == (10.0, 15.0, 20.0, 25.0)
    candidates = brief.candidates()
    assert isinstance(candidates, tuple)
    assert tuple(candidate.thickness_mm for candidate in candidates) == brief.candidate_thicknesses_mm
    for candidate in candidates:
        assert isinstance(candidate, PlateSpec)
        assert candidate.model_dump(exclude={'thickness_mm'}) == brief.requirements.model_dump()
    assert first_failed_guard(candidates[-1]) == 'thin_ratio'


def test_fixed_design_accepts_one_distinct_thickness():
    brief = PlateBrief.model_validate(brief_data((10.0, 10), mode='fixed_design'))
    assert brief.candidates() == (PlateSpec.model_validate(plate_data()),)


@pytest.mark.parametrize('data', [
    brief_data(()),
    brief_data((10.0, 11.0, 12.0, 13.0, 14.0)),
    brief_data((10.0, 20.0), mode='fixed_design'),
    brief_data((10.0,), mode='fixed_thickness'),
    brief_data([10.0]),
    brief_data((0.1, 10.0)),
    brief_data((math.nextafter(150.0, math.inf),)),
    brief_data((1.0, True)),
    brief_data(('10',)),
    brief_data((math.nan,)),
    brief_data((math.inf,)),
    brief_data((-math.inf,)),
    brief_data((10.0, 5.0), dimensional_tolerance_mm=0.06),
    dict(brief_data(), extra=True),
])
def test_brief_rejects_invalid_candidate_sets_or_fields(data):
    with pytest.raises(ValidationError):
        PlateBrief.model_validate(data)


def test_brief_accepts_candidate_thickness_boundaries():
    inside = math.nextafter(0.1, math.inf)
    brief = PlateBrief.model_validate(brief_data((inside, 150.0), dimensional_tolerance_mm=0.001))
    assert tuple(candidate.thickness_mm for candidate in brief.candidates()) == (inside, 150.0)


@pytest.mark.parametrize('changes', [
    {'length_a_mm': 399.0},
    {'length_a_mm': math.nextafter(2000.0, math.inf)},
    {'max_deflection_mm': math.nextafter(8.0, math.inf)},
    {'pressure_mpa': math.nextafter(10.0, math.inf)},
    {'poissons_ratio': '0.3'},
    {'edge_condition': 'free'},
    {'thickness_mm': 10.0},
])
def test_requirements_enforce_shared_rules_and_reject_thickness(changes):
    requirements = brief_data()['requirements'] | changes
    with pytest.raises(ValidationError):
        PlateRequirements.model_validate(requirements)


def test_nested_prebuilt_models_are_revalidated():
    invalid_material = PlateMaterialReference.model_construct(source_kind='documented', reference=' ')
    with pytest.raises(ValidationError, match='reference'):
        PlateSpec.model_validate(plate_data(material_reference=invalid_material))
    data = brief_data()
    data['requirements'] = PlateRequirements.model_validate(data['requirements']).model_copy(
        update={'pressure_mpa': 20.0},
    )
    with pytest.raises(ValidationError, match='pressure_mpa'):
        PlateBrief.model_validate(data)


def test_canonical_spec_and_digest_are_deterministic():
    data = plate_data()
    spec = PlateSpec.model_validate(data)
    reordered = dict(reversed(list(data.items())))
    reordered['material_reference'] = dict(reversed(list(data['material_reference'].items())))
    other = PlateSpec.model_validate(reordered)
    canonical = canonical_spec(spec)
    assert canonical == canonical_spec(other)
    assert canonical == json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
    digest = specification_digest(spec)
    assert re.fullmatch(r'[0-9a-f]{64}', digest)
    assert digest == hashlib.sha256(canonical.encode()).hexdigest() == specification_digest(other)
    thickened = PlateSpec.model_validate(plate_data(thickness_mm=11.0))
    assert digest != specification_digest(thickened)


def test_module_import_does_not_load_geometry_or_solver_dependencies():
    result = subprocess.run(
        [sys.executable, '-c',
         "import sys; sys.path.insert(0, 'backend'); import engineering.plate_spec; "
         "blocked = {'cadquery', 'OCP', 'scipy', 'numpy'}; "
         "loaded = {name for name in sys.modules if name.split('.')[0] in blocked}; "
         "assert not loaded, sorted(loaded)"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
