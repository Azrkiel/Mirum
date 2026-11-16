"""Actual OpenCASCADE construction, STEP round trips, and negative geometry."""
import hashlib
import math
from pathlib import Path

import cadquery as cq
import pytest

from backend.dragsters.geometry import build_body
from backend.dragsters.spec import DragsterSpec, specification_digest
from backend.dragsters.validation import verify_step


@pytest.fixture
def spec():
    return DragsterSpec.model_validate_json(
        (Path(__file__).parents[1] / 'dragsters/examples/wedge.json').read_text())


def raw_wedge(s):
    L, W, g, H, N = s.length_mm, s.width_mm, s.ground_clearance_mm, s.height_mm, s.nose_height_mm
    points = [(0,-W/2,g), (L,-W/2,g), (L,-W/2,g+H), (L/2,-W/2,g+H),
              (0,-W/2,g+N), (0,-W/2,g)]
    return cq.Solid.extrudeLinear(cq.Wire.makePolygon([cq.Vector(*p) for p in points]), [], cq.Vector(0,W,0))


def cut_body(s, *, depth=None, bores=2):
    body = raw_wedge(s).cut(cq.Solid.makeCylinder(s.pocket_diameter_mm/2,
        (s.pocket_depth_mm if depth is None else depth) + 1,
        cq.Vector(s.length_mm-(s.pocket_depth_mm if depth is None else depth), 0, s.pocket_center_z_mm), cq.Vector(1,0,0)))
    for x in (s.front_axle_x_mm, s.rear_axle_x_mm)[:bores]:
        body = body.cut(cq.Solid.makeCylinder(s.axle_bore_diameter_mm/2, s.width_mm+2,
            cq.Vector(x,-s.width_mm/2-1,s.axle_z_mm), cq.Vector(0,1,0)))
    return body


def export(body, path):
    cq.exporters.export(body, str(path), exportType='STEP')
    return path


def test_native_body(spec):
    body = build_body(spec)
    assert body.isValid() and len(body.Solids()) == 1
    assert raw_wedge(spec).Volume() == pytest.approx(226800)
    expected = 226800 - math.pi * 10**2 * 52 - 2 * math.pi * 1.5**2 * 42
    assert body.Volume() == pytest.approx(expected)
    b = body.BoundingBox()
    assert (b.xmin,b.xmax,b.ymin,b.ymax,b.zmin,b.zmax) == pytest.approx((0,200,-21,21,3,35),abs=0.01)
    for point in [(199,0,23),(149,0,23),(40,0,9),(175,0,9)]:
        assert not body.isInside(cq.Vector(*point), 1e-6)
    assert body.isInside(cq.Vector(147,0,23), 1e-6)  # blind end


def test_step_report_and_reproducibility(spec, tmp_path):
    reports = [verify_step(export(build_body(spec), tmp_path/f'{i}.step'), spec) for i in range(2)]
    for i, r in enumerate(reports):
        assert r['passed'] is True and r['engineering_certified'] is False
        assert r['spec_sha256'] == specification_digest(spec)
        assert r['step_sha256'] == hashlib.sha256((tmp_path/f'{i}.step').read_bytes()).hexdigest()
        assert r['generator_version'] == 'dragster_wedge_v1'
        assert r['solid_count'] == 1
        assert all(c['passed'] for c in r['checks'].values())
    assert reports[0]['bounds_mm'] == pytest.approx(reports[1]['bounds_mm'], abs=0.01)
    assert reports[0]['volume_mm3'] == pytest.approx(reports[1]['volume_mm3'], abs=0.1)


@pytest.mark.parametrize('wrong', ['uncut','one_bore','wrong_depth','shifted','disconnected','equal_volume_wrong_cuts'])
def test_reject_wrong_step(spec, tmp_path, wrong):
    if wrong == 'uncut':
        body = raw_wedge(spec)
    elif wrong == 'one_bore':
        body = cut_body(spec, bores=1)
    elif wrong == 'wrong_depth':
        body = cut_body(spec, depth=spec.pocket_depth_mm-5)
    elif wrong == 'shifted':
        body = build_body(spec).translate((1,0,0))
    elif wrong == 'disconnected':
        body = cq.Compound.makeCompound([build_body(spec), cq.Solid.makeBox(1,1,1,cq.Vector(300,0,0))])
    else:
        other = spec.model_copy(update={'front_axle_x_mm': 60, 'pocket_center_z_mm': 22})
        body = cut_body(other)  # same bounds/volume; probes must catch misplaced cuts
    reason = 'pocket_void' if wrong == 'equal_volume_wrong_cuts' else 'STEP checks failed'
    with pytest.raises(ValueError, match=reason):
        verify_step(export(body, tmp_path/'bad.step'), spec)


@pytest.mark.parametrize('contents', [None, b'', b'not STEP'])
def test_bad_step(spec, tmp_path, contents):
    path = tmp_path/'bad.step'
    if contents is not None:
        path.write_bytes(contents)
    with pytest.raises((ValueError, OSError)):
        verify_step(path, spec)
