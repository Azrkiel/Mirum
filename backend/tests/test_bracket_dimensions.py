"""Defect A: make_bracket must deliver the documented dimensions, not just a valid B-rep.

Measurements are taken from the native solid (planar-face datums, bounding box,
volume, STEP reimport) rather than from the builder's own arithmetic.
"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Native OCP geometry runs in Linux Docker")

TOL = 1e-6  # mm; analytic box/cylinder geometry, far above OCC double noise


def _up_faces(solid):
    import cadquery as cq
    faces = []
    for face in solid.Faces():
        if face.geomType() != "PLANE":
            continue
        normal = face.normalAt(face.Center())
        if normal.z > 1 - 1e-9:
            faces.append(face)
    return faces


def measure(solid):
    """Independent datums: base top = lowest upward planar face, wall top = highest."""
    up = sorted(_up_faces(solid), key=lambda f: f.Center().z)
    bb = solid.BoundingBox()
    base_top, wall_top = up[0], up[-1]
    wall_bb = wall_top.BoundingBox()
    return {
        "base_top_z": base_top.Center().z,
        "wall_top_z": wall_top.Center().z,
        "bottom_z": bb.zmin,
        "length": bb.xlen,
        "width": bb.ylen,
        "wall_thickness": wall_bb.xlen,
        "wall_x": (wall_bb.xmin, wall_bb.xmax),
        "volume": solid.Volume(),
    }


def holes(solid):
    found = []
    for face in solid.Faces():
        if face.geomType() == "CYLINDER":
            bb = face.BoundingBox()
            found.append(((bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2, bb.xlen, bb.zmin, bb.zmax))
    return sorted(found)


def test_recorded_case_wall_height_is_measured_above_base_top():
    from cad_utils import make_bracket
    solid = make_bracket(40, 60, 5, 30, 4, n_mounting_holes=0).val()
    m = measure(solid)
    assert solid.isValid()
    assert m["wall_top_z"] - m["base_top_z"] == pytest.approx(30.0, abs=TOL), m
    assert m["base_top_z"] - m["bottom_z"] == pytest.approx(5.0, abs=TOL)


@pytest.mark.parametrize("dims,n,d", [
    ((40, 60, 5, 30, 4), 0, 5.0),
    ((40, 60, 5, 30, 4), 2, 5.0),          # current default hole mode
    ((25, 90, 3, 12, 7), 1, 6.0),
    ((55, 70, 8, 45, 2.5), 3, 4.0),
    ((10, 20, 1.5, 80, 1.0), 2, 2.0),
])
def test_dimensions_volume_holes_and_step_reimport(tmp_path, dims, n, d):
    import cadquery as cq
    from cad_utils import make_bracket
    width, length, base_t, wall_h, wall_t = dims
    wp = make_bracket(width, length, base_t, wall_h, wall_t, n_mounting_holes=n, hole_diameter=d)
    assert len(wp.solids().vals()) == 1, "bracket must be one body"
    solid = wp.val()
    assert solid.isValid()
    m = measure(solid)
    assert m["wall_top_z"] - m["base_top_z"] == pytest.approx(wall_h, abs=TOL)
    assert m["base_top_z"] - m["bottom_z"] == pytest.approx(base_t, abs=TOL)
    assert m["length"] == pytest.approx(length, abs=TOL)
    assert m["width"] == pytest.approx(width, abs=TOL)
    assert m["wall_thickness"] == pytest.approx(wall_t, abs=TOL)
    expected = length * width * base_t + wall_t * width * wall_h - n * math.pi * (d / 2) ** 2 * base_t
    assert m["volume"] == pytest.approx(expected, rel=1e-9), "a hole must not cut into the wall"

    found = holes(solid)
    assert len(found) == n
    wall_xmax = m["wall_x"][1]
    for cx, cy, dia, zmin, zmax in found:
        assert dia == pytest.approx(d, abs=TOL)
        assert cx - dia / 2 > wall_xmax, "hole must lie in the free base flange"
        assert cy == pytest.approx(0.0, abs=TOL)
        assert zmin == pytest.approx(m["bottom_z"], abs=TOL)
        assert zmax == pytest.approx(m["base_top_z"], abs=TOL), "hole goes through the base only"

    path = tmp_path / "bracket.step"
    cq.exporters.export(wp, str(path))
    back = cq.importers.importStep(str(path)).val()
    assert back.isValid()
    r = measure(back)
    for key in ("base_top_z", "wall_top_z", "bottom_z", "length", "width", "wall_thickness"):
        assert r[key] == pytest.approx(m[key], abs=TOL)
    assert r["volume"] == pytest.approx(m["volume"], rel=1e-9)
    assert len(holes(back)) == n


@pytest.mark.parametrize("kwargs", [
    dict(base_width=0), dict(base_length=-1), dict(base_thickness=float("nan")),
    dict(wall_height=float("inf")), dict(wall_thickness=0), dict(wall_thickness=60),
    dict(wall_height=True), dict(n_mounting_holes=-1), dict(n_mounting_holes=2.5),
    dict(n_mounting_holes=True), dict(hole_diameter=0), dict(hole_diameter=-5),
    dict(hole_diameter=float("nan")), dict(hole_diameter=40),
    dict(n_mounting_holes=12, hole_diameter=5),  # cannot fit in the free flange
])
def test_invalid_dimensions_are_rejected_before_the_kernel(kwargs):
    from cad_utils import make_bracket
    args = dict(base_width=40, base_length=60, base_thickness=5, wall_height=30, wall_thickness=4)
    args.update(kwargs)
    with pytest.raises(ValueError):
        make_bracket(**args)
