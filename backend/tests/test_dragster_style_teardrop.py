"""Teardrop style builder (D3): real OpenCASCADE, law bounds/volume, void probes, boundary matrix."""
import json
import math
import subprocess
import sys
from pathlib import Path

import cadquery as cq
import pytest

from backend.dragsters import profiles
from backend.dragsters.spec_v2 import TeardropV2, parse_v2
from backend.dragsters.styles import REGISTRY, load_preset, resolve_build
from backend.dragsters.styles.teardrop import build
from backend.dragsters.tolerances import LOFT_BOUNDS_TOL_MM, LOFT_VOLUME_REL_TOL, VOID_TOLERANCE_MM3

REPO = Path(__file__).parents[2]


def _variant(**changes) -> TeardropV2:
    data = json.loads(load_preset('teardrop').model_dump_json())
    for key, value in changes.items():
        if key.startswith('wheels.'):
            data['wheels'][key.split('.', 1)[1]] = value
        else:
            data[key] = value
    return parse_v2(json.dumps(data))


def _law_bounds(spec):
    stations = profiles.teardrop_stations(spec)
    xs = profiles.sample_xs(0.0, spec.length_mm, 0.25) + [st.x for st in stations]
    secs = [profiles.interpolate(stations, x) for x in xs]
    a_max = max(a for a, _, _ in secs)
    top = max(s + c for _, s, c in secs)
    g = spec.ground_clearance_mm
    return (0.0, -a_max, g, spec.length_mm, a_max, g + top)


def _expected_volume(spec):
    stations = profiles.teardrop_stations(spec)
    law = profiles.law_volume(stations, spec.crown_exponent)
    pocket = math.pi * (spec.pocket_diameter_mm / 2) ** 2 * spec.pocket_depth_mm
    ra = spec.axle_bore_diameter_mm / 2
    bores = sum(math.pi * ra ** 2 * 2 * profiles.half_width_at(spec, x)
                for x in (spec.front_axle_x_mm, spec.rear_axle_x_mm))
    return law - pocket - bores


def _step_round_trip(body, path):
    cq.exporters.export(body, str(path), exportType='STEP')
    back = cq.importers.importStep(str(path)).val()
    assert len(back.Solids()) == 1 and back.isValid()
    return back


def _void_residuals(body, spec):
    L, D = spec.length_mm, spec.pocket_depth_mm
    ra, rp = spec.axle_bore_diameter_mm / 2, spec.pocket_diameter_mm / 2
    a_max = max(st.a for st in profiles.teardrop_stations(spec))
    probes = {
        'pocket_void': cq.Solid.makeCylinder(rp, D, cq.Vector(L - D, 0, spec.pocket_center_z_mm), cq.Vector(1, 0, 0)),
        'front_axle_void': cq.Solid.makeCylinder(ra, 2 * a_max + 2, cq.Vector(
            spec.front_axle_x_mm, -a_max - 1, spec.wheels.front_diameter_mm / 2), cq.Vector(0, 1, 0)),
        'rear_axle_void': cq.Solid.makeCylinder(ra, 2 * a_max + 2, cq.Vector(
            spec.rear_axle_x_mm, -a_max - 1, spec.wheels.rear_diameter_mm / 2), cq.Vector(0, 1, 0)),
    }
    return {name: body.intersect(probe).Volume() for name, probe in probes.items()}


# Boundary matrix. The D2 spike cases first (same nudges), then range extremes with the other fields
# moved only as far as the D1 rules need (comments say why).
BOUNDARY = {
    'n2.0': dict(crown_exponent=2.0),
    'n4.0': dict(crown_exponent=4.0),
    'side0.3': {'side_height_frac': 0.3, 'wheels.front_diameter_mm': 16.0, 'wheels.rear_diameter_mm': 26.0},
    'side0.8': dict(side_height_frac=0.8),
    'nose8-max64': dict(nose_width_mm=8.0, max_width_mm=64.0),
    'tail=max': dict(tail_width_mm=56.0),
    'maxx0.25': dict(max_section_x_frac=0.25),
    'maxx0.6': {'max_section_x_frac': 0.6, 'wheels.front_diameter_mm': 28.0},
    # length 160: axles and pocket depth moved inside the shorter body
    'length160': dict(length_mm=160.0, front_axle_x_mm=30.0, rear_axle_x_mm=125.0, pocket_depth_mm=40.0),
    'length300': dict(length_mm=300.0, front_axle_x_mm=50.0, rear_axle_x_mm=250.0),
    'wheels16': {'wheels.front_diameter_mm': 16.0, 'wheels.rear_diameter_mm': 16.0},
    # 45 mm wheels: bores need a tall side (frac 0.8) and the rear axle must clear the pocket axially
    'wheels45': {'wheels.front_diameter_mm': 45.0, 'wheels.rear_diameter_mm': 45.0, 'side_height_frac': 0.8,
                 'front_axle_x_mm': 70.0, 'rear_axle_x_mm': 160.0},
    'depth30': dict(pocket_depth_mm=30.0),
    'depth80': dict(pocket_depth_mm=80.0),
}


def test_registry_resolves_the_teardrop_builder():
    assert resolve_build('teardrop') is build
    assert REGISTRY['teardrop'].generator_version == 'dragster_teardrop_v2'


def test_importing_the_teardrop_style_does_not_import_cadquery():
    code = ('import sys; import backend.dragsters.styles.teardrop, backend.dragsters.styles; '
            'bad = [m for m in ("cadquery", "OCP") if m in sys.modules]; assert not bad, bad')
    subprocess.run([sys.executable, '-c', code], check=True, cwd=REPO)


def test_preset_builds_one_solid_within_law_bounds_and_volume(tmp_path):
    spec = load_preset('teardrop')
    body = build(spec)
    assert isinstance(body, cq.Solid) and body.isValid() and len(body.Solids()) == 1
    back = _step_round_trip(body, tmp_path / 'td.step')
    bb = back.BoundingBox()
    got = (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)
    assert max(abs(a - b) for a, b in zip(got, _law_bounds(spec))) <= LOFT_BOUNDS_TOL_MM
    expected = _expected_volume(spec)
    assert abs(back.Volume() - expected) / expected <= LOFT_VOLUME_REL_TOL
    assert abs(body.Volume() - expected) / expected <= LOFT_VOLUME_REL_TOL


def test_pocket_and_both_bores_are_open_voids(tmp_path):
    spec = load_preset('teardrop')
    back = _step_round_trip(build(spec), tmp_path / 'td.step')
    residuals = _void_residuals(back, spec)
    assert all(v <= VOID_TOLERANCE_MM3 for v in residuals.values()), residuals


def test_each_axle_bore_sits_at_its_own_wheel_radius():
    spec = _variant(**{'wheels.front_diameter_mm': 22.0, 'wheels.rear_diameter_mm': 18.0})
    body = build(spec)
    fx, rx = spec.front_axle_x_mm, spec.rear_axle_x_mm
    assert not body.isInside(cq.Vector(fx, 0, 11.0))
    assert not body.isInside(cq.Vector(rx, 0, 9.0))
    assert body.isInside(cq.Vector(fx, 0, 9.0))
    assert body.isInside(cq.Vector(rx, 0, 11.0))


def test_build_is_deterministic():
    spec = load_preset('teardrop')
    a, b = build(spec), build(spec)
    assert abs(a.Volume() - b.Volume()) <= 1e-9 * a.Volume()
    ba, bb = a.BoundingBox(), b.BoundingBox()
    assert (ba.xmin, ba.ymin, ba.zmin, ba.xmax, ba.ymax, ba.zmax) == (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)


def test_build_rejects_other_styles_and_versions():
    with pytest.raises(ValueError):
        build(load_preset('wedge'))
    with pytest.raises(ValueError):
        build(load_preset('narrow_waist'))
    with pytest.raises(ValueError):
        build(object())


def test_boundary_matrix_has_at_least_twelve_cases():
    assert len(BOUNDARY) >= 12


@pytest.mark.parametrize('name', list(BOUNDARY))
def test_boundary_case_is_one_valid_solid_with_open_voids(name, tmp_path):
    spec = _variant(**BOUNDARY[name])           # raises if the case is not a valid TeardropV2
    body = build(spec)
    assert isinstance(body, cq.Solid) and body.isValid() and len(body.Solids()) == 1
    back = _step_round_trip(body, tmp_path / f'{name}.step')
    bb = back.BoundingBox()
    got = (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)
    assert max(abs(a - b) for a, b in zip(got, _law_bounds(spec))) <= LOFT_BOUNDS_TOL_MM
    expected = _expected_volume(spec)
    assert abs(back.Volume() - expected) / expected <= LOFT_VOLUME_REL_TOL
    residuals = _void_residuals(back, spec)
    assert all(v <= VOID_TOLERANCE_MM3 for v in residuals.values()), residuals
