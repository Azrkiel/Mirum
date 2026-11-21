import json

import pytest

from engineering.plate_spec import CHECK_IDS, GUARD_IDS, PlateBrief, PlateSpec


GATE_ORDER = (
    'thin_ratio', 'aspect_ratio', 'material', 'geometry', 'small_deflection',
    'equilibrium', 'mesh_convergence_deflection', 'mesh_convergence_stress',
    'symmetry', 'benchmark_deflection', 'benchmark_stress', 'deflection_limit',
    'stress_limit',
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


def assert_accepted(report):
    assert report['passed'] is True, report
    assert report['outcome'] == 'accepted'
    assert report['rejection'] is None
    assert all(report['guards'][gate]['state'] == 'passed' for gate in GUARD_IDS)
    assert all(report['checks'][gate]['state'] == 'passed' for gate in CHECK_IDS)


def assert_rejected(report, expected_gate):
    assert report['passed'] is False, report
    assert report['outcome'] == 'rejected'
    assert report['rejection']['id'] == expected_gate, report['rejection']
    gates = report['guards'] | report['checks']
    failed_at = GATE_ORDER.index(expected_gate)
    for gate in GATE_ORDER[:failed_at]:
        assert gates[gate]['state'] == 'passed', gate
    assert gates[expected_gate]['state'] == 'failed'
    for gate in GATE_ORDER[failed_at + 1:]:
        assert gates[gate]['state'] == 'not_evaluated', gate


@pytest.fixture
def baseline_step(tmp_path):
    from engineering.plate_geometry import export_step

    spec = PlateSpec.model_validate(plate_data())
    return spec, export_step(spec, tmp_path / 'plate.step')


@pytest.fixture
def baseline_report(baseline_step):
    from engineering.plate_verify import verify_plate_step

    spec, path = baseline_step
    report = verify_plate_step(path, spec)
    assert_accepted(report)
    return report


@pytest.mark.parametrize('edge_condition', ['simply_supported', 'clamped'])
def test_exported_plate_passes_native_verification(tmp_path, edge_condition):
    from engineering.plate_geometry import export_step
    from engineering.plate_verify import verify_plate_step

    spec = PlateSpec.model_validate(plate_data(edge_condition=edge_condition))
    path = export_step(spec, tmp_path / 'plate.step')
    report = verify_plate_step(path, spec)

    assert_accepted(report)
    assert report['design_intent']['state'] == 'passed'
    assert report['physics']['state'] == 'passed'
    assert report['engineering_certified'] is False
    assert report['standards']['state'] == 'not_evaluated'
    if edge_condition == 'simply_supported':
        assert report['corner_forces']
    assert [level['n_b'] for level in report['mesh_history']] == [4, 8, 16]
    assert set(report['implementation_sha256']) >= {
        'plate_spec.py', 'plate_fea.py', 'plate_reference.py',
        'plate_geometry.py', 'plate_verify.py',
    }
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('adversary', ['hole', 'fillet', 'two_solids', 'offset', 'too_long'])
def test_step_geometry_rejections(tmp_path, adversary):
    import cadquery as cq
    from engineering.plate_verify import verify_plate_step

    spec = PlateSpec.model_validate(plate_data())
    a, b, h = spec.length_a_mm, spec.width_b_mm, spec.thickness_mm
    tolerance = spec.dimensional_tolerance_mm
    if adversary == 'too_long':
        a += 2 * tolerance
    shape = cq.Workplane('XY').box(a, b, h, centered=(False, False, False))
    if adversary == 'hole':
        bore = cq.Workplane('XY').workplane(offset=-1).center(a / 2, b / 2).circle(5).extrude(h + 2)
        shape = shape.cut(bore)
    elif adversary == 'fillet':
        shape = shape.edges('|Z').fillet(2)
    elif adversary == 'two_solids':
        second = cq.Workplane('XY').box(10, 10, 10).translate((a + 20, b / 2, h / 2))
        shape = cq.Compound.makeCompound([shape.val(), second.val()])
    elif adversary == 'offset':
        shape = shape.translate((10 * tolerance, 0, 0))

    path = tmp_path / f'{adversary}.step'
    cq.exporters.export(shape, str(path))
    report = verify_plate_step(str(path), spec)

    assert_rejected(report, 'geometry')
    assert report['design_intent']['state'] == 'failed'
    assert report['physics']['state'] == 'not_evaluated'


@pytest.mark.parametrize('guard,overrides', [
    ('thin_ratio', {'thickness_mm': 25.0}),
    ('small_deflection', {
        'thickness_mm': 2.0,
        'pressure_mpa': 0.1,
        'youngs_modulus_mpa': 70000.0,
        'allowable_stress_mpa': 1e5,
        'max_deflection_mm': 8.0,
    }),
])
def test_validity_guard_rejections(tmp_path, guard, overrides):
    from engineering.plate_geometry import export_step
    from engineering.plate_verify import verify_plate_step

    spec = PlateSpec.model_validate(plate_data(**overrides))
    path = export_step(spec, tmp_path / 'plate.step')
    report = verify_plate_step(path, spec)

    assert_rejected(report, guard)
    if guard == 'thin_ratio':
        assert report['design_intent']['state'] == 'not_evaluated'
        assert report['physics']['state'] == 'not_evaluated'


@pytest.mark.parametrize('gate,quantity,factor', [
    ('mesh_convergence_deflection', 'w_max', 1.01),
    ('mesh_convergence_stress', 'vm_max_mpa', 1.05),
])
def test_mesh_nonconvergence_is_rejected(baseline_step, gate, quantity, factor):
    from engineering.plate_fea import solve_levels
    from engineering.plate_verify import verify_plate_step

    def unconverged_solver(**kwargs):
        levels = solve_levels(**kwargs)
        levels[-1] = {**levels[-1], quantity: factor * levels[-1][quantity]}
        return levels

    spec, path = baseline_step
    report = verify_plate_step(path, spec, fe_solver=unconverged_solver)

    assert_rejected(report, gate)


@pytest.mark.parametrize('gate,quantity,factor', [
    ('benchmark_deflection', 'w_max', 1.02),
    ('benchmark_stress', 'vm_max', 1.05),
])
def test_benchmark_mismatch_is_rejected(baseline_step, baseline_report, gate, quantity, factor):
    from engineering.plate_verify import verify_plate_step

    finest = baseline_report['mesh_history'][-1]

    def mismatched_reference(a, b, h, q, E, nu, edge_condition):
        values = {'w_max': finest['w_max'], 'vm_max': finest['vm_max_mpa']}
        values[quantity] *= factor
        return {**values, 'method': 'test_mismatched_reference'}

    spec, path = baseline_step
    report = verify_plate_step(path, spec, reference=mismatched_reference)

    assert_rejected(report, gate)


@pytest.mark.parametrize('gate,field', [
    ('stress_limit', 'allowable_stress_mpa'),
    ('deflection_limit', 'max_deflection_mm'),
])
def test_design_limits_reject_a_plate_just_over_the_limit(baseline_step, baseline_report, gate, field):
    from engineering.plate_verify import verify_plate_step

    _, path = baseline_step
    limit = 0.99 * baseline_report['checks'][gate]['quantity']
    spec = PlateSpec.model_validate(plate_data(**{field: limit}))
    report = verify_plate_step(path, spec)

    assert_rejected(report, gate)


def test_missing_step_raises_engineering_unavailable(tmp_path):
    from engineering.plate_verify import verify_plate_step
    from engineering.service import EngineeringUnavailable

    spec = PlateSpec.model_validate(plate_data())
    with pytest.raises(EngineeringUnavailable):
        verify_plate_step(str(tmp_path / 'missing.step'), spec)


@pytest.mark.parametrize('missing_evidence', ['solver_error', 'nonfinite_deflection'])
def test_missing_solver_evidence_raises_engineering_unavailable(baseline_step, missing_evidence):
    from engineering.plate_fea import solve_levels
    from engineering.plate_verify import verify_plate_step
    from engineering.service import EngineeringUnavailable

    def broken_solver(**kwargs):
        if missing_evidence == 'solver_error':
            raise RuntimeError('test solver failure')
        levels = solve_levels(**kwargs)
        levels[-1] = {**levels[-1], 'w_max': float('nan')}
        return levels

    spec, path = baseline_step
    with pytest.raises(EngineeringUnavailable):
        verify_plate_step(path, spec, fe_solver=broken_solver)


@pytest.mark.parametrize('selected,rejections', [
    pytest.param(6.0, ('stress_limit', None), id='first-pass'),
    pytest.param(None, ('stress_limit', 'stress_limit', 'stress_limit', 'thin_ratio'), id='all-fail'),
])
def test_bounded_thickness_selection(tmp_path, selected, rejections):
    from engineering.plate_geometry import export_step
    from engineering.plate_verify import select_smallest_passing, verify_plate_step

    # Lower the pressure so the 4 mm candidate reaches the stress limit gate.
    requirements = plate_data(pressure_mpa=0.003)
    del requirements['thickness_mm']
    calibration_spec = PlateSpec.model_validate(dict(requirements, thickness_mm=4.0))
    calibration_path = export_step(calibration_spec, tmp_path / 'calibration.step')
    calibration = verify_plate_step(calibration_path, calibration_spec)
    assert_accepted(calibration)
    stress_at_4 = calibration['checks']['stress_limit']['quantity']

    # At fixed pressure and dimensions, Kirchhoff bending stress scales with 1/h**2.
    if selected is None:
        allowable = 0.5 * stress_at_4 * (4 / 10)**2
    else:
        stress_at_6 = stress_at_4 * (4 / 6)**2
        allowable = (stress_at_4 + stress_at_6) / 2
    brief = PlateBrief.model_validate(dict(
        mode='bounded_thickness',
        requirements=dict(requirements, allowable_stress_mpa=allowable),
        candidate_thicknesses_mm=(4.0, 6.0, 10.0, 25.0),
    ))
    workdir = tmp_path / 'selection'
    workdir.mkdir()
    result = select_smallest_passing(brief, workdir=workdir)

    assert result['outcome'] == ('rejected' if selected is None else 'accepted')
    assert result['selected_thickness_mm'] == selected
    assert len(result['attempts']) == len(rejections)
    for report, rejection in zip(result['attempts'], rejections, strict=True):
        if rejection is None:
            assert_accepted(report)
        else:
            assert_rejected(report, rejection)


@pytest.mark.parametrize('overrides', [
    pytest.param({'length_a_mm': 399.0}, id='a-less-than-b'),
    pytest.param({'length_a_mm': 2001.0}, id='aspect-ratio-over-five'),
    pytest.param({'pressure_mpa': float('nan')}, id='nonfinite-pressure'),
    pytest.param({'poissons_ratio': 0.5}, id='poissons-ratio-out-of-range'),
    pytest.param({'edge_condition': 'free'}, id='unsupported-edge'),
])
def test_invalid_plate_spec_is_rejected_before_verification(overrides):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlateSpec.model_validate(plate_data(**overrides))
