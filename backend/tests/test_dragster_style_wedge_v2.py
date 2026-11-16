"""v2 wedge builder (D2): real OpenCASCADE, analytic bounds/volume, v1 equivalence."""
import json
import math
import subprocess
import sys
from pathlib import Path

import cadquery as cq
import pytest

from backend.dragsters.geometry import build_body
from backend.dragsters.spec import DragsterSpec
from backend.dragsters.spec_v2 import WedgeV2, upgrade_v1
from backend.dragsters.styles import load_preset, resolve_build
from backend.dragsters.styles.wedge import build
from backend.dragsters.tolerances import WEDGE_BOUNDS_TOL_MM

REPO = Path(__file__).parents[2]
V1_EXAMPLE = REPO / 'backend/dragsters/examples/wedge.json'


def _expected_volume(s):
    L, W, H, N = s.length_mm, s.width_mm, s.height_mm, s.nose_height_mm
    rp, D, ra = s.pocket_diameter_mm / 2, s.pocket_depth_mm, s.axle_bore_diameter_mm / 2
    return W * L * (N + 3 * H) / 4 - math.pi * rp ** 2 * D - 2 * math.pi * ra ** 2 * W


def _variant(**changes):
    data = json.loads(load_preset('wedge').model_dump_json())
    for key, value in changes.items():
        if key.startswith('wheels.'):
            data['wheels'][key.split('.', 1)[1]] = value
        else:
            data[key] = value
    return WedgeV2.model_validate_json(json.dumps(data))


def test_registry_resolves_the_wedge_builder():
    assert resolve_build('wedge') is build


def test_importing_the_wedge_style_does_not_import_cadquery():
    code = ('import sys; import backend.dragsters.styles.wedge, backend.dragsters.styles; '
            'bad = [m for m in ("cadquery", "OCP") if m in sys.modules]; assert not bad, bad')
    subprocess.run([sys.executable, '-c', code], check=True, cwd=REPO)


@pytest.mark.parametrize('spec', [
    load_preset('wedge'),
    _variant(**{'wheels.front_diameter_mm': 22.0, 'wheels.rear_diameter_mm': 18.0}),
], ids=['preset', 'unequal-wheels'])
def test_wedge_builds_one_solid_with_exact_bounds_and_volume(spec):
    body = build(spec)
    assert isinstance(body, cq.Solid) and body.isValid() and len(body.Solids()) == 1
    L, W, H, g = spec.length_mm, spec.width_mm, spec.height_mm, spec.ground_clearance_mm
    bb = body.BoundingBox()
    got = (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)
    want = (0, -W / 2, g, L, W / 2, g + H)
    assert max(abs(a - b) for a, b in zip(got, want)) <= WEDGE_BOUNDS_TOL_MM
    expected = _expected_volume(spec)
    assert abs(body.Volume() - expected) <= max(0.1, 1e-6 * expected)


def test_each_axle_bore_sits_at_its_own_wheel_radius():
    spec = _variant(**{'wheels.front_diameter_mm': 22.0, 'wheels.rear_diameter_mm': 18.0})
    body = build(spec)
    fx, rx = spec.front_axle_x_mm, spec.rear_axle_x_mm
    assert not body.isInside(cq.Vector(fx, 0, 11.0))    # front bore axis z = 22/2
    assert not body.isInside(cq.Vector(rx, 0, 9.0))     # rear bore axis z = 18/2
    assert body.isInside(cq.Vector(fx, 0, 9.0))         # no bore at the rear height up front
    assert body.isInside(cq.Vector(rx, 0, 11.0))        # nor at the front height at the rear


def test_v1_equivalent_spec_matches_v1_build_body_volume():
    v1 = DragsterSpec.model_validate_json(V1_EXAMPLE.read_text(encoding='utf-8'))
    v2, _ = upgrade_v1(v1)
    assert v2.wheels.front_diameter_mm == v2.wheels.rear_diameter_mm == 2 * v1.axle_z_mm
    assert build(v2).Volume() == pytest.approx(build_body(v1).Volume(), rel=1e-6)


def test_wedge_survives_a_step_round_trip(tmp_path):
    body = build(load_preset('wedge'))
    path = tmp_path / 'wedge.step'
    cq.exporters.export(body, str(path), exportType='STEP')
    back = cq.importers.importStep(str(path)).val()
    assert len(back.Solids()) == 1 and back.isValid()
    assert back.Volume() == pytest.approx(body.Volume(), rel=1e-6)


def test_build_rejects_a_non_wedge_spec():
    with pytest.raises(ValueError):
        build(load_preset('teardrop'))
