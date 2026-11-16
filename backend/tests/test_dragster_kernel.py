"""Lofted-body kernel (D2): real OpenCASCADE sections, lofts, cuts and STEP round trips."""
import math
from pathlib import Path

import cadquery as cq
import pytest

from backend.dragsters import tolerances as tol
from backend.dragsters.kernel import cut_pocket_and_bores, loft_body, section_wire, section_xs
from backend.dragsters.profiles import (
    Station, crown_height_at, interpolate, law_volume, narrow_waist_stations, sample_xs,
    teardrop_stations,
)
from backend.dragsters.styles import load_preset

GROUND = 3.0


def _preset_law(style):
    spec = load_preset(style)
    stations = teardrop_stations(spec) if style == 'teardrop' else narrow_waist_stations(spec)
    return spec, stations


def _law_bounds(stations, n, ground):
    xs = sample_xs(0.0, stations[-1].x, 0.25) + [st.x for st in stations]
    secs = [interpolate(stations, x) for x in xs]
    a_max = max(a for a, _, _ in secs)
    top = max(s + c for _, s, c in secs)
    return (0.0, -a_max, ground), (stations[-1].x, a_max, ground + top)


# ----------------------------------------------------------------------------- section_wire

def test_section_wire_is_closed_planar_and_at_x():
    wire = section_wire(37.5, 20.0, 15.0, 10.0, 3.0, GROUND)
    assert wire.IsClosed()
    xs = [v.X for v in wire.Vertices()]
    assert all(abs(x - 37.5) < 1e-9 for x in xs)
    face = cq.Face.makeFromWires(wire)
    assert face.isValid()
    normal = face.normalAt()
    assert abs(abs(normal.x) - 1) < 1e-9
    bb = wire.BoundingBox()
    assert bb.xmin == pytest.approx(37.5, abs=1e-6) and bb.xmax == pytest.approx(37.5, abs=1e-6)
    assert bb.ymin == pytest.approx(-20.0, abs=1e-6) and bb.ymax == pytest.approx(20.0, abs=1e-6)
    assert bb.zmin == pytest.approx(GROUND, abs=1e-6)
    assert bb.zmax == pytest.approx(GROUND + 25.0, abs=0.05)


def test_section_wire_starts_at_bottom_left_and_crown_follows_the_law():
    a, s, c, n = 20.0, 15.0, 10.0, 3.0
    wire = section_wire(0.0, a, s, c, n, GROUND)
    first = wire.Edges()[0].startPoint()
    assert (first.y, first.z) == pytest.approx((-a, GROUND), abs=1e-9)
    m = tol.CROWN_POINTS
    assert len(wire.Edges()) == m + 2
    # Crown vertices: everything at or above the side height, ordered from y=+a to y=-a.
    crown = sorted((v for v in wire.Vertices() if v.Z >= GROUND + s - 1e-9), key=lambda v: -v.Y)
    crown = [cq.Vector(v.X, v.Y, v.Z) for v in crown]
    assert len(crown) == m
    assert (crown[0].y, crown[-1].y) == pytest.approx((a, -a), abs=1e-12)
    ys = [p.y for p in crown]
    assert all(y1 < y0 for y0, y1 in zip(ys, ys[1:]))
    assert 0.0 in ys                                   # odd count: apex is a vertex
    for p in crown:                                    # every vertex lies on the law curve
        assert p.z == pytest.approx(GROUND + crown_height_at(a, s, c, n, p.y), abs=1e-9)
    # Cosine spacing clusters vertices near the vertical ends.
    assert abs(ys[0] - ys[1]) < abs(ys[m // 2 - 1] - ys[m // 2]) / 5


def test_section_wire_rejects_degenerate_sections():
    for bad in ((0.0, 10.0, 5.0), (10.0, 0.0, 5.0), (10.0, 10.0, 0.0), (10.0, math.nan, 5.0)):
        with pytest.raises(ValueError):
            section_wire(0.0, *bad, 3.0, GROUND)
    for bad_points in (1, 2, 16):
        with pytest.raises(ValueError):
            section_wire(0.0, 10.0, 10.0, 5.0, 3.0, GROUND, crown_points=bad_points)


@pytest.mark.parametrize('style', ['teardrop', 'narrow_waist'])
def test_same_edge_count_and_start_vertex_across_a_law(style):
    spec, stations = _preset_law(style)
    counts, starts = set(), set()
    for x in section_xs(stations, tol.STATION_STEP_MM):
        wire = section_wire(x, *interpolate(stations, x), spec.crown_exponent, spec.ground_clearance_mm)
        counts.add(len(wire.Edges()))
        p = wire.Edges()[0].startPoint()
        starts.add(round(p.z, 9))
        assert p.y < 0
    assert counts == {tol.CROWN_POINTS + 2}
    assert starts == {spec.ground_clearance_mm}


# ----------------------------------------------------------------------------- section_xs

def test_section_xs_contains_stations_and_respects_step():
    stations = [Station(0, 5, 5, 5, 'ease_out'), Station(33.3, 9, 9, 9), Station(101.0, 7, 7, 7, 'hold'),
                Station(140.0, 7, 7, 7)]
    xs = section_xs(stations, 7.5)
    assert xs[0] == 0 and xs[-1] == 140.0
    assert all(st.x in xs for st in stations)
    assert all(b > a for a, b in zip(xs, xs[1:]))
    assert max(b - a for a, b in zip(xs, xs[1:])) <= 7.5 + 1e-12
    with pytest.raises(ValueError):
        section_xs(stations, 0.0)


# ----------------------------------------------------------------------------- loft_body

def test_loft_of_constant_section_equals_prism_volume():
    a, s, c, n, L = 18.0, 12.0, 9.0, 3.0, 150.0
    stations = [Station(0.0, a, s, c, 'hold'), Station(L, a, s, c)]
    body = loft_body(stations, n, GROUND, L)
    area = cq.Face.makeFromWires(section_wire(0.0, a, s, c, n, GROUND)).Area()
    assert body.Volume() == pytest.approx(area * L, rel=1e-6)


def test_loft_body_rejects_a_length_that_disagrees_with_the_stations():
    stations = [Station(0.0, 10, 10, 5, 'hold'), Station(100.0, 10, 10, 5)]
    with pytest.raises(ValueError):
        loft_body(stations, 3.0, GROUND, 120.0)


@pytest.mark.parametrize('style', ['teardrop', 'narrow_waist'])
def test_preset_laws_loft_to_one_valid_solid_matching_the_law(style):
    spec, stations = _preset_law(style)
    n, g = spec.crown_exponent, spec.ground_clearance_mm
    body = loft_body(stations, n, g, spec.length_mm)
    assert isinstance(body, cq.Solid)
    assert body.isValid() and len(body.Solids()) == 1
    law = law_volume(stations, n)
    assert abs(body.Volume() - law) / law <= tol.LOFT_VOLUME_REL_TOL
    (x0, y0, z0), (x1, y1, z1) = _law_bounds(stations, n, g)
    bb = body.BoundingBox()
    measured = (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)
    expected = (x0, y0, z0, x1, y1, z1)
    assert max(abs(m - e) for m, e in zip(measured, expected)) <= tol.LOFT_BOUNDS_TOL_MM
    assert bb.ymin == pytest.approx(-bb.ymax, abs=1e-6)


@pytest.mark.parametrize('style', ['teardrop', 'narrow_waist'])
def test_preset_body_survives_a_step_round_trip(style, tmp_path):
    spec, stations = _preset_law(style)
    body = loft_body(stations, spec.crown_exponent, spec.ground_clearance_mm, spec.length_mm)
    path = tmp_path / 'body.step'
    cq.exporters.export(body, str(path), exportType='STEP')
    back = cq.importers.importStep(str(path)).val()
    solids = back.Solids()
    assert len(solids) == 1 and back.isValid()
    assert solids[0].Volume() == pytest.approx(body.Volume(), rel=1e-6)


# ----------------------------------------------------------------------------- cuts

def _box(L=200.0, W=40.0, H=40.0, g=GROUND):
    return cq.Solid.makeBox(L, W, H, cq.Vector(0, -W / 2, g))


def test_cut_removes_exactly_the_pocket_and_bores_from_a_box():
    L, W, H = 200.0, 40.0, 40.0
    box = _box(L, W, H)
    d, D, zp, bd = 20.0, 50.0, 25.0, 3.0
    bores = [(40.0, 12.0, bd), (170.0, 13.0, bd)]
    cut = cut_pocket_and_bores(box, length=L, pocket_diameter=d, pocket_depth=D,
                               pocket_center_z=zp, bores=bores, y_extent=W / 2)
    assert isinstance(cut, cq.Solid) and cut.isValid() and len(cut.Solids()) == 1
    removed = math.pi * (d / 2) ** 2 * D + sum(math.pi * (b / 2) ** 2 * W for _, _, b in bores)
    assert box.Volume() - cut.Volume() == pytest.approx(removed, rel=1e-6)


def test_pocket_breaking_out_of_the_body_still_yields_one_solid():
    # Kernel does not judge walls (D5 does): a pocket that breaks the top is still one solid.
    box = _box(H=20.0)
    cut = cut_pocket_and_bores(box, length=200.0, pocket_diameter=24.0, pocket_depth=60.0,
                               pocket_center_z=GROUND + 18.0, bores=[], y_extent=20.0)
    assert cut.isValid() and len(cut.Solids()) == 1
    assert cut.Volume() < box.Volume()


def test_bore_outside_the_body_is_a_volume_no_op():
    # Documented: a bore placed above the body removes nothing; D5's checks catch it.
    box = _box(H=20.0)
    cut = cut_pocket_and_bores(box, length=200.0, pocket_diameter=12.0, pocket_depth=40.0,
                               pocket_center_z=GROUND + 10.0, bores=[(40.0, 60.0, 3.0)], y_extent=20.0)
    expected = box.Volume() - math.pi * 6.0 ** 2 * 40.0
    assert cut.Volume() == pytest.approx(expected, rel=1e-6)


def test_cut_rejects_a_result_that_is_not_one_solid():
    # A pocket wider than the body slices the rear off into a separate piece.
    box = _box(W=10.0, H=10.0)
    with pytest.raises(ValueError):
        cut_pocket_and_bores(box, length=200.0, pocket_diameter=24.0, pocket_depth=60.0,
                             pocket_center_z=GROUND + 5.0, bores=[(100.0, GROUND + 5.0, 30.0)],
                             y_extent=5.0)


@pytest.mark.parametrize('style', ['teardrop', 'narrow_waist'])
def test_preset_loft_with_cuts_is_one_valid_solid_after_step_round_trip(style, tmp_path):
    spec, stations = _preset_law(style)
    g = spec.ground_clearance_mm
    body = loft_body(stations, spec.crown_exponent, g, spec.length_mm)
    a_max = max(st.a for st in stations)
    bores = [(spec.front_axle_x_mm, spec.wheels.front_diameter_mm / 2, spec.axle_bore_diameter_mm),
             (spec.rear_axle_x_mm, spec.wheels.rear_diameter_mm / 2, spec.axle_bore_diameter_mm)]
    cut = cut_pocket_and_bores(body, length=spec.length_mm, pocket_diameter=spec.pocket_diameter_mm,
                               pocket_depth=spec.pocket_depth_mm,
                               pocket_center_z=spec.pocket_center_z_mm, bores=bores, y_extent=a_max)
    pocket = math.pi * (spec.pocket_diameter_mm / 2) ** 2 * spec.pocket_depth_mm
    assert body.Volume() - cut.Volume() > pocket
    path = tmp_path / 'body.step'
    cq.exporters.export(cut, str(path), exportType='STEP')
    back = cq.importers.importStep(str(path)).val()
    assert len(back.Solids()) == 1 and back.isValid()


def test_tolerances_module_is_pure_and_within_hard_maxima():
    import subprocess
    import sys
    src = Path(tol.__file__).read_text(encoding='utf-8')
    assert 'import cadquery' not in src and 'from cadquery' not in src
    assert 0 < tol.LOFT_VOLUME_REL_TOL <= 0.020 and tol.LOFT_VOLUME_REL_TOL >= 0.010
    assert 0.05 <= tol.LOFT_BOUNDS_TOL_MM <= 0.25
    assert tol.WEDGE_BOUNDS_TOL_MM == 0.01 and tol.VOID_TOLERANCE_MM3 == 0.001
    assert tol.WALL_DISTANCE_TOL_MM == 0.01
    assert isinstance(tol.LOFT_RULED, bool) and tol.CROWN_POINTS >= 3 and tol.STATION_STEP_MM > 0
    code = ('import sys; import backend.dragsters.tolerances; '
            'bad = [m for m in ("cadquery", "OCP", "numpy", "trimesh") if m in sys.modules]; '
            'assert not bad, bad')
    subprocess.run([sys.executable, '-c', code], check=True, cwd=Path(__file__).parents[2])
