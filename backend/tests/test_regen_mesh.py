"""Mesh verifier tests on synthetic binary STLs. No PicoGK needed."""
import math
import struct

import numpy as np
import pytest

from engineering import regen_mesh as rm


def box(x0=0, y0=0, z0=0, x1=1, y1=1, z1=1):
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=np.float32)
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]  # outward
    return v[np.array(f)]


def tetra(cx, cy, cz, s):
    p = np.array([[0, 0, 0], [s, 0, 0], [0, s, 0], [0, 0, s]], dtype=np.float32) + np.float32([cx, cy, cz])
    f = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]  # outward
    return p[np.array(f)]


def write_raw(path, tris):
    with open(path, 'wb') as fh:
        fh.write(b'test'.ljust(80, b'\0'))
        fh.write(struct.pack('<I', len(tris)))
        for t in tris:
            fh.write(struct.pack('<12fH', 0, 0, 0, *t.reshape(-1), 0))


def test_stl_roundtrip(tmp_path):
    t = box()
    write_raw(tmp_path / 'a.stl', t)
    got = rm.read_stl(tmp_path / 'a.stl')
    assert got.shape == (12, 3, 3) and np.array_equal(got, t)
    rm.write_stl(tmp_path / 'b.stl', got)
    assert rm.read_stl(tmp_path / 'b.stl').tolist() == t.tolist()


def test_read_stl_rejects_truncated(tmp_path):
    write_raw(tmp_path / 'a.stl', box())
    data = (tmp_path / 'a.stl').read_bytes()[:-7]
    (tmp_path / 'c.stl').write_bytes(data)
    with pytest.raises(ValueError):
        rm.read_stl(tmp_path / 'c.stl')


def test_closed_box_passes():
    r = rm.clean_and_check(box(0, 0, 0, 2, 3, 4))
    assert r.failures == []
    assert r.checks['edge_manifold']['state'] == 'pass'
    assert r.checks['winding']['state'] == 'pass'
    assert r.checks['components']['state'] == 'pass'
    assert r.volume == pytest.approx(24.0)
    assert r.removed == []


def test_open_box_fails_manifold():
    r = rm.clean_and_check(box()[:-2])
    assert r.checks['edge_manifold']['state'] == 'fail'
    assert r.failures


def test_flipped_winding_fails():
    r = rm.clean_and_check(box()[:, ::-1, :])
    assert r.checks['winding']['state'] == 'fail'


def test_one_flipped_triangle_fails_winding():
    t = box().copy()
    t[3] = t[3][::-1]
    r = rm.clean_and_check(t)
    assert r.checks['winding']['state'] == 'fail'


def test_second_significant_solid_fails():
    r = rm.clean_and_check(np.concatenate([box(), box(5, 5, 5, 6, 6, 6)]))
    assert r.checks['components']['state'] == 'fail'
    assert r.removed == []


def test_tiny_speck_removed_and_recorded():
    base = box()
    speck = tetra(5, 5, 5, 0.05)  # volume 2.1e-5 mm3
    r = rm.clean_and_check(np.concatenate([base, speck]))
    assert r.failures == []
    assert len(r.removed) == 1
    assert r.removed[0]['triangles'] == 4
    assert abs(r.removed[0]['signed_volume_mm3']) < 1e-3
    assert len(r.faces) == 12
    assert r.checks['components']['state'] == 'pass'


def test_speck_just_over_threshold_fails():
    r = rm.clean_and_check(np.concatenate([box(), tetra(5, 5, 5, 0.2)]))  # 1.3e-3 mm3
    assert r.checks['components']['state'] == 'fail'


def test_degenerates_dropped_and_counted():
    degenerate = np.array([[[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 0, 0], [0, 0, 0], [1, 1, 1]]], dtype=np.float32)
    r = rm.clean_and_check(np.concatenate([box(), degenerate]))
    assert r.dropped_degenerate == 2
    assert r.failures == []


def test_weld_negative_zero_equals_zero():
    t = box().copy()
    t[t == 0] = -0.0
    r = rm.clean_and_check(t)
    assert r.failures == []


# ---- plane slices ----------------------------------------------------------
def ring_wall(radius, cx, cy, n=96, z0=0.0, z1=10.0):
    """Side wall of a prism over a circle. Open ends are fine for slicing."""
    a = np.arange(n) * 2 * math.pi / n
    b = np.roll(a, -1)
    tri = []
    for i in range(n):
        p0 = (cx + radius * math.cos(a[i]), cy + radius * math.sin(a[i]))
        p1 = (cx + radius * math.cos(b[i]), cy + radius * math.sin(b[i]))
        tri.append([[*p0, z0], [*p1, z0], [*p1, z1]])
        tri.append([[*p0, z0], [*p1, z1], [*p0, z1]])
    return np.array(tri, dtype=np.float32)


def chamber_wall(n_channels=8, rc=1.5, r_in=20.0, r_out=27.0, r_ch=23.5, skip=(), ch_radius_override=None):
    parts = [ring_wall(r_in, 0, 0, 180), ring_wall(r_out, 0, 0, 180)]
    for c in range(n_channels):
        if c in skip:
            continue
        th = c * 2 * math.pi / n_channels
        r_c = ch_radius_override.get(c, r_ch) if ch_radius_override else r_ch
        parts.append(ring_wall(rc, r_c * math.cos(th), r_c * math.sin(th), 48))
    return np.concatenate(parts)


def slice_of(tris, z=5.0, n=8):
    verts, faces = rm.weld(tris)
    return rm.slice_station(verts, faces, z, n)


def test_slice_counts_and_measures_channels():
    m = slice_of(chamber_wall())
    assert m['channel_loops'] == 8
    assert m['hot_wall_mm'] == pytest.approx(2.0, abs=0.1)
    assert m['cold_wall_mm'] == pytest.approx(2.0, abs=0.1)
    assert m['rib_mm'] == pytest.approx(2 * 23.5 * math.sin(math.pi / 8) - 3.0, abs=0.05)
    assert rm.station_failures(m, 8, 1.0, 0.25) == []


def test_missing_or_merged_channel_fails_count():
    m = slice_of(chamber_wall(skip=(3,)))
    assert m['channel_loops'] == 7
    assert any('channel loops' in f for f in rm.station_failures(m, 8, 1.0, 0.25))


def test_extra_loop_fails_count():
    t = np.concatenate([chamber_wall(), ring_wall(0.8, 0, 23.5 + 2.0, 24)])
    m = slice_of(t)
    assert m['channel_loops'] == 9
    assert any('channel loops' in f for f in rm.station_failures(m, 8, 1.0, 0.25))


def test_thin_hot_wall_fails():
    m = slice_of(chamber_wall(ch_radius_override={0: 21.6}))  # hot wall 0.1 mm
    assert m['hot_wall_mm'] < 0.3
    assert any('hot wall' in f for f in rm.station_failures(m, 8, 1.0, 0.25))


def test_thin_cold_wall_fails():
    m = slice_of(chamber_wall(ch_radius_override={2: 25.4}))
    assert m['cold_wall_mm'] < 0.3
    assert any('cold wall' in f for f in rm.station_failures(m, 8, 1.0, 0.25))


def test_thin_rib_fails():
    # Replace channel 1 by one very close to channel 0: centre distance 3.4, radii 1.5 -> rib 0.4.
    t = chamber_wall(skip=(1,))
    r = 23.5
    ang = 3.4 / r
    extra = ring_wall(1.5, r * math.cos(ang), r * math.sin(ang), 48)
    m = slice_of(np.concatenate([t, extra]))
    assert m['channel_loops'] == 8
    assert m['rib_mm'] < 0.6
    assert any('rib' in f for f in rm.station_failures(m, 8, 1.0, 0.25))


def test_slice_plane_through_vertices_is_robust():
    verts, faces = rm.weld(chamber_wall())
    m = rm.slice_station(verts, faces, 0.0, 8)  # plane exactly on the bottom vertices
    assert 'channel_loops' in m or m.get('error')


def test_concentric_loops_are_not_channels():
    t = np.concatenate([ring_wall(20, 0, 0, 180), ring_wall(27, 0, 0, 180),
                        ring_wall(22.5, 0, 0, 180), ring_wall(24.5, 0, 0, 180)])
    m = slice_of(t)
    assert any('channel loops' in f for f in rm.station_failures(m, 8, 1.0, 0.25))


# ---- bbox / volume helpers --------------------------------------------------
def example_spec():
    from engineering.regen_spec import RegenChamberSpec
    return RegenChamberSpec.model_validate(dict(
        family='regen_chamber', units='mm', chamber_radius_mm=35.0, chamber_length_mm=40.0,
        convergent_length_mm=50.0, throat_radius_mm=20.0, divergent_length_mm=80.0,
        exit_radius_mm=45.0, wall_thickness_mm=7.0, channel_count=32, channel_radius_mm=1.0,
        channel_twist_rad_per_mm=0.012, manifold_radius_mm=1.5, end_margin_mm=8.0,
        voxel_size_mm=0.4, assumptions='geometry_only_no_gas_dynamics_thermal_or_structural_analysis'))


def test_expected_bbox_and_analytic_volume():
    s = example_spec()
    lo, hi = rm.expected_bbox(s)
    assert hi[2] == pytest.approx(170.0) and lo[2] == pytest.approx(0.0)
    assert hi[0] == pytest.approx(52.0, abs=0.01) and lo[0] == pytest.approx(-52.0, abs=0.01)
    v = rm.analytic_volume(s)
    shell = rm.analytic_shell_volume(s)
    assert 0 < v < shell
    assert (shell - v) / shell < 0.2


def test_bbox_and_volume_checks_state_tolerance():
    s = example_spec()
    lo, hi = rm.expected_bbox(s)
    assert rm.check_bbox(lo, hi, s)['state'] == 'pass'
    assert rm.check_bbox(lo, hi + np.array([1.0, 0, 0]), s)['state'] == 'fail'
    v = rm.analytic_volume(s)
    assert rm.check_volume(v * 1.019, s)['state'] == 'pass'
    assert rm.check_volume(v * 1.03, s)['state'] == 'fail'
    assert rm.check_volume(v * 0.97, s)['state'] == 'fail'
    assert rm.VOLUME_TOLERANCE == 0.02
