"""Hardware checks (D6): wheel envelopes, clearance, stock containment, body mass.

Pure tests need no CAD; the CAD tests use the real OpenCASCADE kernel (no mocks, no skips).
"""
import inspect
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.dragsters import hardware
from backend.dragsters.profiles import (
    interpolate, max_half_width_over, narrow_waist_stations, teardrop_stations,
)
from backend.dragsters.spec_v2 import parse_v2
from backend.dragsters.styles import load_preset
from backend.dragsters.tolerances import VOID_TOLERANCE_MM3, WALL_DISTANCE_TOL_MM

REPO = Path(__file__).parents[2]
STYLES = ('wedge', 'teardrop', 'narrow_waist')
SIGNATURES = {'wheel_envelopes': ['spec'], 'cad_checks': ['body', 'spec'],
              'summary': ['spec', 'volume_mm3', 'bounds']}


def variant(style, **changes):
    """Preset with overrides (``wheels.x`` dotted keys, ``stock``, ``material``), re-validated."""
    data = json.loads(load_preset(style).model_dump_json())
    for key, value in changes.items():
        if key.startswith('wheels.'):
            data['wheels'][key.split('.', 1)[1]] = value
        else:
            data[key] = value
    return parse_v2(json.dumps(data))


def by_key(envs):
    return {(e['axle'], e['side']): e for e in envs}


# ------------------------------------------------------------------------------- pure: contract

def test_public_signatures_are_frozen():
    for name, params in SIGNATURES.items():
        assert list(inspect.signature(getattr(hardware, name)).parameters) == params


# ------------------------------------------------------------------------------ pure: envelopes

def test_wedge_envelopes_hand_checked():
    # a = W/2 = 21; y_c = 21 + 1.5 + 6/2 = 25.5; z = d/2 = 9; x = axle x.
    envs = hardware.wheel_envelopes(load_preset('wedge'))
    assert len(envs) == 4
    got = by_key(envs)
    assert set(got) == {(a, s) for a in ('front', 'rear') for s in ('left', 'right')}
    for (axle, side), env in got.items():
        x = 40.0 if axle == 'front' else 175.0
        y = -25.5 if side == 'left' else 25.5
        assert env['center_mm'] == pytest.approx([x, y, 9.0], abs=1e-9)
        assert env['diameter_mm'] == 18.0 and env['width_mm'] == 6.0
        assert env['axis'] == [0, 1, 0]
        assert set(env) == {'axle', 'side', 'center_mm', 'diameter_mm', 'width_mm', 'axis'}


def test_teardrop_envelopes_follow_the_law_maximum_under_the_wheel():
    spec = load_preset('teardrop')
    got = by_key(hardware.wheel_envelopes(spec))
    # Front wheel x in [25, 55]: half-width rises ease_out (nose 8 -> max 28 at x=77), so the
    # envelope uses the value at the leading edge x=55: 8 + 20*sin(pi/2 * 55/77).
    a_front = 8 + 20 * math.sin(math.pi / 2 * 55 / 77)
    assert got[('front', 'right')]['center_mm'][1] == pytest.approx(a_front + 1.5 + 3, abs=1e-6)
    assert got[('front', 'right')]['center_mm'][2] == pytest.approx(15.0)
    # Rear wheel x in [168, 202] sits on the smoothstep from 28 (x=77) to 22 (x=220), which
    # decreases, so the widest point is the front edge x=168.
    t = (168 - 77) / (220 - 77)
    a_rear = 28 + (22 - 28) * (3 * t * t - 2 * t ** 3)
    assert got[('rear', 'left')]['center_mm'][1] == pytest.approx(-(a_rear + 1.5 + 3), abs=2e-3)
    assert got[('rear', 'left')]['center_mm'][2] == pytest.approx(17.0)


def test_narrow_waist_envelopes_hand_checked():
    got = by_key(hardware.wheel_envelopes(load_preset('narrow_waist')))
    # Rear wheel [178, 212] is entirely inside the housing (starts at 150): a = 44/2 = 22.
    assert got[('rear', 'right')]['center_mm'] == pytest.approx([195.0, 22 + 1.5 + 3, 17.0])
    # Front wheel [25, 55] peaks at the front section station x=0.15*230=34.5, a = 40/2 = 20.
    assert got[('front', 'right')]['center_mm'] == pytest.approx([40.0, 20 + 1.5 + 3, 15.0], abs=1e-3)


def test_narrow_waist_rear_wheel_over_the_housing_step_uses_the_housing_half_width():
    # Wheel [143, 177] straddles the housing start (x=150): the waist is narrower ahead of it,
    # the housing (a=22) governs.
    spec = variant('narrow_waist', rear_axle_x_mm=160.0)
    assert max_half_width_over(spec, 143.0, 149.0) < 22.0
    got = by_key(hardware.wheel_envelopes(spec))
    assert got[('rear', 'left')]['center_mm'] == pytest.approx([160.0, -(22 + 1.5 + 3), 17.0], abs=1e-3)


def test_left_and_right_mirror_about_y0():
    for style in STYLES:
        got = by_key(hardware.wheel_envelopes(load_preset(style)))
        for axle in ('front', 'rear'):
            left, right = got[(axle, 'left')], got[(axle, 'right')]
            assert left['center_mm'][1] < 0 < right['center_mm'][1]
            assert left['center_mm'][1] == -right['center_mm'][1]
            assert left['center_mm'][0] == right['center_mm'][0]
            assert left['center_mm'][2] == right['center_mm'][2]


def test_envelopes_are_clipped_to_the_body_at_nose_and_rear():
    # Front wheel [-1, 21] and rear wheel [193, 227] hang off the body (0 .. 220): the
    # interval is clipped, and the evaluation must not raise outside [0, L].
    spec = variant('teardrop', front_axle_x_mm=10.0, rear_axle_x_mm=210.0, side_height_frac=0.8,
                   **{'wheels.front_diameter_mm': 22.0})
    got = by_key(hardware.wheel_envelopes(spec))
    assert got[('front', 'right')]['center_mm'][1] == pytest.approx(
        max_half_width_over(spec, 0.0, 21.0) + 1.5 + 3)
    assert got[('rear', 'right')]['center_mm'][1] == pytest.approx(
        max_half_width_over(spec, 193.0, 220.0) + 1.5 + 3)
    # Hand check of the nose side (a rises ease_out): a(21) = 8 + 20*sin(pi/2 * 21/77).
    assert got[('front', 'right')]['center_mm'][1] == pytest.approx(
        8 + 20 * math.sin(math.pi / 2 * 21 / 77) + 4.5, abs=1e-6)


# --------------------------------------------------------------------------------- pure: summary

BOUNDS = {'xmin': 0.0, 'xmax': 200.0, 'ymin': -21.0, 'ymax': 21.0, 'zmin': 3.0, 'zmax': 35.0}


def test_summary_hardware_block_for_each_preset():
    for style in STYLES:
        spec = load_preset(style)
        hw = hardware.summary(spec, 1000.0, BOUNDS)['hardware']
        got = by_key(hw['wheel_envelopes'])
        assert hw['wheel_envelopes'] == hardware.wheel_envelopes(spec)
        assert hw['track_width_front_mm'] == pytest.approx(2 * got[('front', 'right')]['center_mm'][1])
        assert hw['track_width_rear_mm'] == pytest.approx(2 * got[('rear', 'right')]['center_mm'][1])
        assert hw['wheelbase_mm'] == pytest.approx(spec.rear_axle_x_mm - spec.front_axle_x_mm)
        assert hw['stock_margin_mm'] is None
    wedge = hardware.summary(load_preset('wedge'), 1.0, BOUNDS)['hardware']
    assert wedge['track_width_front_mm'] == 51.0 and wedge['wheelbase_mm'] == 135.0


def test_summary_stock_margin_uses_measured_extents():
    spec = variant('wedge', stock={'length_mm': 210.0, 'width_mm': 50.0, 'height_mm': 40.0})
    margin = hardware.summary(spec, 1.0, BOUNDS)['hardware']['stock_margin_mm']
    assert margin == {'length': pytest.approx(10.0), 'width': pytest.approx(8.0), 'height': pytest.approx(8.0)}


def test_mass_with_material_is_volume_times_density():
    spec = variant('wedge', material={'name': 'basswood', 'density_g_cm3': 0.42})
    mass = hardware.summary(spec, 25000.0, BOUNDS)['mass']
    assert mass == {'value_g': 10.5, 'basis': 'body_only', 'density_g_cm3': 0.42,
                    'material': 'basswood', 'reason': None}
    # rounded to 2 decimals: 12345.678 mm3 * 0.42 g/cm3 / 1000 = 5.1851...
    assert hardware.summary(spec, 12345.678, BOUNDS)['mass']['value_g'] == 5.19


def test_mass_without_material_is_none_and_never_uses_a_default_density():
    mass = hardware.summary(load_preset('wedge'), 25000.0, BOUNDS)['mass']
    assert mass == {'value_g': None, 'basis': 'body_only', 'density_g_cm3': None,
                    'material': None, 'reason': 'no material density supplied'}


def test_summary_is_json_serialisable():
    spec = variant('wedge', material={'name': 'ABS', 'density_g_cm3': 1.05},
                   stock={'length_mm': 210.0, 'width_mm': 50.0, 'height_mm': 40.0})
    json.dumps(hardware.summary(spec, 25000.0, BOUNDS), allow_nan=False)


def test_importing_hardware_does_not_need_cadquery():
    code = (
        "import sys\n"
        "sys.modules['cadquery'] = None\n"
        "sys.modules['OCP'] = None\n"
        "from backend.dragsters import hardware\n"
        "from backend.dragsters.styles import load_preset\n"
        "spec = load_preset('narrow_waist')\n"
        "assert len(hardware.wheel_envelopes(spec)) == 4\n"
        "out = hardware.summary(spec, 1000.0, {'xmin': 0, 'xmax': 230, 'ymin': -22, 'ymax': 22,"
        " 'zmin': 3, 'zmax': 49})\n"
        "assert out['mass']['value_g'] is None\n"
    )
    subprocess.run([sys.executable, '-c', code], check=True, cwd=REPO)


# ------------------------------------------------------------------------------------- real CAD

@pytest.fixture(scope='module')
def cq():
    import cadquery
    return cadquery


def build_body(spec):
    """Body for any style: wedge via the registry; lofted styles via kernel + profile laws
    (their style builders land in D3/D4)."""
    from backend.dragsters.kernel import cut_pocket_and_bores, loft_body
    from backend.dragsters.styles import resolve_build
    if spec.style == 'wedge':
        return resolve_build('wedge')(spec)
    stations = teardrop_stations(spec) if spec.style == 'teardrop' else narrow_waist_stations(spec)
    outer = loft_body(stations, spec.crown_exponent, spec.ground_clearance_mm, spec.length_mm)
    y_extent = max(interpolate(stations, st.x)[0] for st in stations)
    bores = [(spec.front_axle_x_mm, spec.wheels.front_diameter_mm / 2, spec.axle_bore_diameter_mm),
             (spec.rear_axle_x_mm, spec.wheels.rear_diameter_mm / 2, spec.axle_bore_diameter_mm)]
    return cut_pocket_and_bores(outer, length=spec.length_mm, pocket_diameter=spec.pocket_diameter_mm,
                                pocket_depth=spec.pocket_depth_mm, pocket_center_z=spec.pocket_center_z_mm,
                                bores=bores, y_extent=y_extent)


def step_round_trip(cq, body, tmp_path):
    path = tmp_path / 'body.step'
    cq.exporters.export(cq.Workplane('XY').newObject([body]), str(path))
    return cq.importers.importStep(str(path)).val().Solids()[0]


@pytest.mark.parametrize('style', STYLES)
def test_presets_pass_wheel_clearance_and_ground_checks_on_the_reimported_step(cq, style, tmp_path):
    spec = load_preset(style)
    body = step_round_trip(cq, build_body(spec), tmp_path)
    checks = hardware.cad_checks(body, spec)
    assert set(checks) == {'wheel_clearance_front', 'wheel_clearance_rear', 'body_above_ground'}
    for name, result in checks.items():
        assert result['passed'] is True, (name, result)
    json.dumps(checks, allow_nan=False)
    required = spec.wheels.side_clearance_mm - WALL_DISTANCE_TOL_MM
    for name in ('wheel_clearance_front', 'wheel_clearance_rear'):
        ev = checks[name]
        assert ev['required_distance_mm'] == pytest.approx(required)
        for side in ('left', 'right'):
            assert ev['measured_distance_mm'][side] >= required
            assert ev['intersection_volume_mm3'][side] <= VOID_TOLERANCE_MM3
    zmin = body.BoundingBox().zmin
    assert checks['body_above_ground']['zmin_mm'] == pytest.approx(zmin)
    assert zmin == pytest.approx(spec.ground_clearance_mm, abs=0.05) and zmin > 0
    assert 'stock_containment' not in checks


def test_wedge_with_half_millimetre_clearance_measures_about_half_a_millimetre(cq):
    spec = variant('wedge', **{'wheels.side_clearance_mm': 0.5})
    checks = hardware.cad_checks(build_body(spec), spec)
    for name in ('wheel_clearance_front', 'wheel_clearance_rear'):
        assert checks[name]['passed'] is True
        for d in checks[name]['measured_distance_mm'].values():
            assert d == pytest.approx(0.5, abs=0.01)


def test_body_widened_by_two_millimetres_fails_wheel_clearance(cq):
    spec = variant('wedge')
    wide = build_body(variant('wedge', width_mm=spec.width_mm + 2.0))
    checks = hardware.cad_checks(wide, spec)
    for name in ('wheel_clearance_front', 'wheel_clearance_rear'):
        assert checks[name]['passed'] is False
        for d in checks[name]['measured_distance_mm'].values():
            assert d == pytest.approx(0.5, abs=0.01)    # 1.5 clearance - 1.0 growth per side
    assert checks['body_above_ground']['passed'] is True


def test_body_overlapping_the_wheel_envelope_reports_intersection_volume(cq):
    spec = variant('wedge')
    wide = build_body(variant('wedge', width_mm=spec.width_mm + 8.0))   # +4 mm per side > 1.5 gap
    checks = hardware.cad_checks(wide, spec)
    for name in ('wheel_clearance_front', 'wheel_clearance_rear'):
        assert checks[name]['passed'] is False
        for side in ('left', 'right'):
            assert checks[name]['measured_distance_mm'][side] == pytest.approx(0.0, abs=1e-6)
            assert checks[name]['intersection_volume_mm3'][side] > VOID_TOLERANCE_MM3


def test_stock_containment_margins(cq):
    body = build_body(variant('wedge'))                                    # 200 x 42 x 32
    roomy = variant('wedge', stock={'length_mm': 210.0, 'width_mm': 50.0, 'height_mm': 40.0})
    res = hardware.cad_checks(body, roomy)['stock_containment']
    assert res['passed'] is True
    assert res['margin_mm'] == {'length': pytest.approx(10.0, abs=0.01),
                                'width': pytest.approx(8.0, abs=0.01),
                                'height': pytest.approx(8.0, abs=0.01)}
    exact = variant('wedge', stock={'length_mm': 200.0, 'width_mm': 42.0, 'height_mm': 32.0})
    assert hardware.cad_checks(body, exact)['stock_containment']['passed'] is True


def test_stock_one_millimetre_smaller_than_the_body_fails_with_negative_margins(cq):
    body = build_body(variant('wedge'))
    tight = variant('wedge', stock={'length_mm': 199.0, 'width_mm': 41.0, 'height_mm': 31.0})
    res = hardware.cad_checks(body, tight)['stock_containment']
    assert res['passed'] is False
    assert all(m == pytest.approx(-1.0, abs=0.01) for m in res['margin_mm'].values())


def test_stock_none_omits_the_check(cq):
    spec = variant('wedge')
    assert spec.stock is None
    assert 'stock_containment' not in hardware.cad_checks(build_body(spec), spec)


def test_body_sunk_below_ground_fails_body_above_ground(cq):
    spec = variant('wedge')
    sunk = build_body(spec).translate(cq.Vector(0, 0, -spec.ground_clearance_mm - 0.5))
    assert hardware.cad_checks(sunk, spec)['body_above_ground']['passed'] is False


@pytest.mark.parametrize('style', STYLES)
def test_cad_checks_timing_per_preset(cq, style):
    spec = load_preset(style)
    body = build_body(spec)
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        hardware.cad_checks(body, spec)
        times.append(time.perf_counter() - t0)
    print(f'cad_checks timing {style}: ' + ', '.join(f'{t:.3f}s' for t in times))
    assert sorted(times)[1] <= 1.0
