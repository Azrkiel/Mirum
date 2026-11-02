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


# ---- closed-mesh adversarial regressions -----------------------------------------------------
# Deterministic synthetic parts: material = shell - (helical channels + 2 manifold rings + 2 wall ports),
# the construction the spec defines. The part is meshed by marching tetrahedra on a fixed grid, so every
# mesh is closed, 2-manifold and outward wound by construction. Mutations change the void/solid field in a
# narrow z window that contains no station plane, so the seven-station checks cannot see them.
# Geometry only: nothing here says anything about flow, heat or strength.
import functools
import json

from engineering.regen_spec import PORT_OVERSHOOT_MM, RegenChamberSpec, inner_radius

_ADV_H = 0.25
_ADV_ORIGIN = np.array([-17.0113, -17.0071, -0.5317])
_ADV_NODES = (139, 139, 167)
_ADV_BAND = 1.0
_ADV_TWO_PI = 2 * math.pi


def adv_spec():
    """Spec-valid by the normal validator (no model_construct); steepest wall slope is ~0.67 (tan 35 = 0.70)."""
    return RegenChamberSpec.model_validate(dict(
        family='regen_chamber', units='mm', chamber_radius_mm=10.0, chamber_length_mm=12.0,
        convergent_length_mm=14.0, throat_radius_mm=6.0, divergent_length_mm=14.0,
        exit_radius_mm=12.0, wall_thickness_mm=4.4, channel_count=16, channel_radius_mm=0.6,
        channel_twist_rad_per_mm=0.02, manifold_radius_mm=1.0, end_margin_mm=3.5,
        voxel_size_mm=0.25, assumptions='geometry_only_no_gas_dynamics_thermal_or_structural_analysis'))


def _adv_ri(spec, z):
    z = np.asarray(z, dtype=np.float64)
    cl, zt, big_l = spec.chamber_length_mm, spec.throat_z_mm, spec.total_length_mm
    rt, rc, re = spec.throat_radius_mm, spec.chamber_radius_mm, spec.exit_radius_mm
    t1 = np.clip((z - cl) / spec.convergent_length_mm, 0.0, 1.0)
    t2 = np.clip((z - zt) / spec.divergent_length_mm, 0.0, 1.0)
    conv = rt + (rc - rt) * 0.5 * (1 + np.cos(np.pi * t1))
    div = rt + (re - rt) * 0.5 * (1 - np.cos(np.pi * t2))
    return np.where(z <= cl, rc, np.where(z <= zt, conv, np.where(z <= big_l, div, re)))


def _adv_bump(z, z0, hw):
    u = (np.asarray(z, dtype=np.float64) - z0) / hw
    return np.where(np.abs(u) < 1.0, (1.0 - u * u) ** 2, 0.0)


def _adv_centre(spec, c, z, dents):
    """Channel centreline (radius, angle) at z; `dents` displace it locally (dr radial mm, dt tangential mm)."""
    base = _adv_ri(spec, z) + spec.wall_thickness_mm / 2
    rho = base.copy()
    th = c * (_ADV_TWO_PI / spec.channel_count) + z * spec.channel_twist_rad_per_mm
    for d in dents:
        g = _adv_bump(z, d['z'], d['hw']) * (c == d['ch'])
        rho = rho + d.get('dr', 0.0) * g
        th = th + d.get('dt', 0.0) * g / base
    return rho, th


def _adv_xyz(rho, th, z):
    return np.stack([rho * np.cos(th), rho * np.sin(th), np.broadcast_to(z, rho.shape)], axis=-1)


def _adv_seg_dist(p, a, b):
    ab = b - a
    ap = p - a
    den = np.maximum(np.einsum('...k,...k->...', ab, ab), 1e-18)
    t = np.clip(np.einsum('...k,...k->...', ap, ab) / den, 0.0, 1.0)
    d = ap - t[..., None] * ab
    return np.sqrt(np.einsum('...k,...k->...', d, d))


def _adv_channel_void(spec, P, dents, exact):
    """min over nearby channels of (distance to the swept centreline - channel radius)."""
    n_ch, rc = spec.channel_count, spec.channel_radius_mm
    delta = _ADV_TWO_PI / n_ch
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    k0 = np.rint((np.arctan2(y, x) - z * spec.channel_twist_rad_per_mm) / delta).astype(np.int64)
    best = np.full(len(P), 10.0)
    eps = 1e-3
    for j in (-1, 0, 1):
        c = (k0 + j) % n_ch
        rho, th = _adv_centre(spec, c, z, dents)
        rho_p, th_p = _adv_centre(spec, c, z + eps, dents)
        rho_m, th_m = _adv_centre(spec, c, z - eps, dents)
        dr_dz = (rho_p - rho_m) / (2 * eps)
        dth_dz = (th_p - th_m) / (2 * eps)
        xc, yc = rho * np.cos(th), rho * np.sin(th)
        off_r = (x - xc) * np.cos(th) + (y - yc) * np.sin(th)
        off_t = -(x - xc) * np.sin(th) + (y - yc) * np.cos(th)
        tan = np.stack([dr_dz, rho * dth_dz, np.ones_like(z)], axis=-1)
        off = np.stack([off_r, off_t, np.zeros_like(z)], axis=-1)
        t2 = np.einsum('ij,ij->i', tan, tan)
        d2 = np.einsum('ij,ij->i', off, off) - np.einsum('ij,ij->i', off, tan) ** 2 / t2
        d = np.sqrt(np.maximum(d2, 0.0))
        if exact:   # distance to the polyline of the displaced centreline (no tangent-line approximation)
            near = np.nonzero(d < rc + 0.8)[0]
            for s in range(0, len(near), 20000):
                idx = near[s:s + 20000]
                zs = z[idx, None] + np.linspace(-1.5, 1.5, 31)[None, :]
                cc = np.broadcast_to(c[idx, None], zs.shape)
                r_s, t_s = _adv_centre(spec, cc, zs, dents)
                nodes = _adv_xyz(r_s, t_s, zs)
                dd = _adv_seg_dist(P[idx, None, None, :], nodes[:, None, :-1], nodes[:, None, 1:])
                d[idx] = dd.reshape(len(idx), -1).min(axis=1)
        best = np.minimum(best, d - rc)
    return best


def _adv_ring_port_void(spec, P, ports, bridges):
    w, rm = spec.wall_thickness_mm, spec.manifold_radius_mm
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    rho = np.hypot(x, y)
    out = np.full(len(P), 10.0)
    for j, mz in enumerate(spec.manifold_z_mm):
        r_ring = float(inner_radius(spec, mz)) + w / 2
        out = np.minimum(out, np.hypot(rho - r_ring, z - mz) - rm)
        ang = ports.get(j, 0.0)
        if ang is None:
            continue
        xr = x * math.cos(ang) + y * math.sin(ang)
        yr = -x * math.sin(ang) + y * math.cos(ang)
        r_end = float(inner_radius(spec, mz)) + w + PORT_OVERSHOOT_MM
        tt = np.clip(xr, r_ring, r_end)
        out = np.minimum(out, np.sqrt((xr - tt) ** 2 + yr ** 2 + (z - mz) ** 2) - rm)
    for b in bridges:
        ra, ta = _adv_centre(spec, b['a'], np.float64(b['z']), [])
        rb, tb = _adv_centre(spec, b['b'], np.float64(b['z']), [])
        a_pt = _adv_xyz(ra, ta, np.float64(b['z']))
        b_pt = _adv_xyz(rb, tb, np.float64(b['z']))
        out = np.minimum(out, _adv_seg_dist(P, a_pt, b_pt) - b['radius'])
    return out


def _adv_void_field(spec, P, mut, exact):
    mut = mut or {}
    c = np.minimum(_adv_channel_void(spec, P, mut.get('dents', []), exact),
                   _adv_ring_port_void(spec, P, mut.get('ports', {}), mut.get('bridges', [])))
    z = P[:, 2]
    phi = np.arctan2(P[:, 1], P[:, 0])
    rho = np.hypot(P[:, 0], P[:, 1])
    for pl in mut.get('plugs', []):   # solid plug inside one channel
        r_c, t_c = _adv_centre(spec, pl['ch'], z, [])
        dist = np.hypot(P[:, 0] - r_c * np.cos(t_c), P[:, 1] - r_c * np.sin(t_c))
        c = np.where((np.abs(z - pl['z']) <= pl['hw']) & (dist < spec.channel_radius_mm + 0.4), 1.0, c)
    for gp in mut.get('ring_gaps', []):   # solid bridge across one manifold ring
        mz = spec.manifold_z_mm[gp['ring']]
        r_ring = float(inner_radius(spec, mz)) + spec.wall_thickness_mm / 2
        dang = np.abs((phi - gp['angle'] + math.pi) % _ADV_TWO_PI - math.pi)
        hit = ((np.abs(z - mz) < spec.manifold_radius_mm + 0.4)
               & (np.abs(rho - r_ring) < spec.manifold_radius_mm + 0.3) & (dang < gp['half_angle']))
        c = np.where(hit, 1.0, c)
    return c


def _adv_shell(spec, xs, ys, zs):
    w = spec.wall_thickness_mm
    rho = np.hypot(xs[:, None], ys[None, :])[:, :, None]
    ri = _adv_ri(spec, np.clip(zs, 0.01, spec.total_length_mm - 0.01))
    cosa = 1.0 / np.sqrt(1.0 + np.gradient(ri, zs) ** 2)
    radial = np.maximum(ri[None, None, :] - rho, rho - ri[None, None, :] - w) * cosa[None, None, :]
    axial = np.maximum(-zs, zs - spec.total_length_mm)[None, None, :]
    return np.maximum(radial, axial)


@functools.lru_cache(maxsize=1)
def _adv_base():
    spec = adv_spec()
    nx, ny, nz = _ADV_NODES
    xs = _ADV_ORIGIN[0] + _ADV_H * np.arange(nx)
    ys = _ADV_ORIGIN[1] + _ADV_H * np.arange(ny)
    zs = _ADV_ORIGIN[2] + _ADV_H * np.arange(nz)
    shell = _adv_shell(spec, xs, ys, zs).astype(np.float32)
    cool = np.full(shell.shape, 10.0, dtype=np.float32)
    ii, jj, kk = np.nonzero(shell < _ADV_BAND)
    for s in range(0, len(ii), 400000):
        sl = slice(s, s + 400000)
        P = np.stack([xs[ii[sl]], ys[jj[sl]], zs[kk[sl]]], axis=-1)
        cool[ii[sl], jj[sl], kk[sl]] = _adv_void_field(spec, P, None, False)
    return spec, xs, ys, zs, shell, cool


def _adv_field(mut):
    spec, xs, ys, zs, shell, cool = _adv_base()
    if mut:
        cool = cool.copy()
        lo, hi = mut['window']
        k0 = max(0, int((lo - _ADV_ORIGIN[2]) / _ADV_H))
        k1 = min(len(zs), int((hi - _ADV_ORIGIN[2]) / _ADV_H) + 2)
        ii, jj, kk = np.nonzero(shell[:, :, k0:k1] < _ADV_BAND)
        P = np.stack([xs[ii], ys[jj], zs[kk + k0]], axis=-1)
        cool[ii, jj, kk + k0] = _adv_void_field(spec, P, mut, True)
    return np.maximum(shell, -cool).astype(np.float64)


def _adv_marching_tets(f, origin, h):
    """Closed, 2-manifold, outward-wound triangles of {f < 0}; vertices sit on grid edges and are shared by id."""
    nx, ny, nz = f.shape
    inside = f < 0
    s = inside.astype(np.int8)
    acc = np.zeros((nx - 1, ny - 1, nz - 1), dtype=np.int8)
    for di in (0, 1):
        for dj in (0, 1):
            for dk in (0, 1):
                acc += s[di:nx - 1 + di, dj:ny - 1 + dj, dk:nz - 1 + dk]
    ci, cj, ck = np.nonzero((acc > 0) & (acc < 8))
    flat_f = f.reshape(-1)
    flat_in = inside.reshape(-1)
    total = nx * ny * nz

    def pos(ids):
        i, j, k = ids // (ny * nz), (ids // nz) % ny, ids % nz
        return origin + h * np.stack([i, j, k], axis=-1)

    def ekey(u, v):
        return np.minimum(u, v) * total + np.maximum(u, v)

    axes = np.eye(3, dtype=np.int64)
    tri_keys, refs = [], []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        o1 = axes[perm[0]]
        offs = [np.zeros(3, dtype=np.int64), o1, o1 + axes[perm[1]], np.ones(3, dtype=np.int64)]
        t = np.stack([((ci + o[0]) * ny + (cj + o[1])) * nz + (ck + o[2]) for o in offs], axis=1)
        ins = flat_in[t]
        cnt = ins.sum(axis=1)
        srt = np.take_along_axis(t, np.argsort(~ins, axis=1, kind='stable'), axis=1)   # inside nodes first
        for case in (1, 2, 3):
            n = srt[cnt == case]
            if not len(n):
                continue
            a, b, c, d = n[:, 0], n[:, 1], n[:, 2], n[:, 3]
            if case == 1:
                groups = [[(a, b), (a, c), (a, d)]]
                p_in, p_out = pos(a), (pos(b) + pos(c) + pos(d)) / 3
            elif case == 3:
                groups = [[(d, a), (d, b), (d, c)]]
                p_in, p_out = (pos(a) + pos(b) + pos(c)) / 3, pos(d)
            else:
                groups = [[(a, c), (a, d), (b, d)], [(a, c), (b, d), (b, c)]]
                p_in, p_out = (pos(a) + pos(b)) / 2, (pos(c) + pos(d)) / 2
            for g in groups:
                tri_keys.append(np.stack([ekey(u, v) for u, v in g], axis=1))
                refs.append(p_out - p_in)
    keys = np.concatenate(tri_keys)
    ref = np.concatenate(refs)
    ukeys, inv = np.unique(keys.reshape(-1), return_inverse=True)
    faces = inv.reshape(-1, 3).astype(np.int64)
    lo, hi = ukeys // total, ukeys % total
    f_lo, f_hi = flat_f[lo], flat_f[hi]
    tcut = np.clip(f_lo / (f_lo - f_hi), 0.01, 0.99)
    p_lo = pos(lo)
    verts = p_lo + tcut[:, None] * (pos(hi) - p_lo)
    v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    flip = np.einsum('ij,ij->i', np.cross(v1 - v0, v2 - v0), ref) < 0
    faces[flip] = faces[flip][:, [0, 2, 1]]
    return verts[faces].astype(np.float32)


def adv_mesh(name):
    """Triangles (float32, shape (n,3,3)) for a named, fixed mutation of the positive control."""
    return _adv_marching_tets(_adv_field(ADV_MUTATIONS[name]), _ADV_ORIGIN, _ADV_H)



def _adv_win(z, half):
    return (z - half - 2.5, z + half + 2.5)


def _adv_mutations():
    spec = adv_spec()
    n = spec.channel_count
    delta = _ADV_TWO_PI / n
    tw = spec.channel_twist_rad_per_mm
    z_rib = 20.0
    rho_rib = float(_adv_ri(spec, z_rib)) + spec.wall_thickness_mm / 2
    rib_shift = _ADV_TWO_PI * rho_rib / n - 2 * spec.channel_radius_mm - 0.35   # leaves ~0.35 mm of rib
    zf = spec.manifold_z_mm[0]
    # in-plane hot-wall gap on the steepest wall is G - rc/cos; aim at ~0.8 mm in plane (floor is 0.75)
    cos_a = math.cos(math.atan(spec.max_slope))
    in_plane = spec.wall_thickness_mm / 2 - spec.channel_radius_mm / cos_a
    slope_dr = -(in_plane - 0.74)

    def dent(ch, z, hw, **kw):
        return {'window': _adv_win(z, hw), 'dents': [dict(ch=ch, z=z, hw=hw, **kw)]}

    return {
        'control': None,
        # blockage: solid plug inside one channel, 1.0 mm and 0.5 mm long
        'blocked_channel': {'window': _adv_win(10.57, 0.5), 'plugs': [dict(ch=3, z=10.57, hw=0.5)]},
        'blocked_channel_narrow': {'window': _adv_win(10.57, 0.25), 'plugs': [dict(ch=3, z=10.57, hw=0.25)]},
        # unintended connection: a void tube joins two neighbouring channels
        'channels_joined': {'window': _adv_win(20.0, 0.8), 'bridges': [dict(a=6, b=7, z=20.0, radius=0.6)]},
        # path deviation: channel 8 wanders 1.0 mm sideways (walls and ribs stay legal)
        'channel_detour': dent(8, 29.43, 1.6, dt=1.0),
        # missing / wrong manifold + port geometry (all still one closed surface)
        'forward_port_missing': {'window': _adv_win(zf, 1.5), 'ports': {0: None}},
        'aft_port_wrong_angle': {'window': _adv_win(spec.manifold_z_mm[1], 1.5), 'ports': {1: math.pi}},
        'forward_ring_broken': {'window': _adv_win(zf, 1.5), 'ring_gaps': [
            dict(ring=0, angle=(4.5 * delta + zf * tw), half_angle=0.05),
            dict(ring=0, angle=(9.5 * delta + zf * tw), half_angle=0.05)]},
        # local wall defects
        'thin_hot_wall': dent(5, 10.57, 2.0, dr=-1.25),
        'thin_hot_wall_narrow': dent(5, 10.57, 0.7, dr=-1.25),
        'thin_cold_wall': dent(9, 15.29, 2.0, dr=1.25),
        'thin_rib': dent(2, z_rib, 2.0, dt=rib_shift),
        'sloped_wall_normal_thin': dent(12, 33.4, 1.3, dr=slope_dr),
        'wall_breach': dent(5, 10.57, 2.0, dr=-2.0),
    }


ADV_MUTATIONS = _adv_mutations()


# ---- the adversarial cases ----------------------------------------------------------------
# name -> (defect centre z, half support in z). Every support lies strictly between two station planes.
ADV_DEFECT_Z = {
    'blocked_channel': (10.57, 0.5), 'blocked_channel_narrow': (10.57, 0.25), 'channels_joined': (20.0, 0.8),
    'channel_detour': (29.43, 1.6), 'forward_port_missing': (None, None), 'aft_port_wrong_angle': (None, None),
    'forward_ring_broken': (None, None), 'thin_hot_wall': (10.57, 2.0), 'thin_hot_wall_narrow': (10.57, 0.7),
    'thin_cold_wall': (15.29, 2.0), 'thin_rib': (20.0, 2.0), 'sloped_wall_normal_thin': (33.4, 1.3),
    'wall_breach': (10.57, 2.0),
}
ADV_CIRCUIT = ['blocked_channel', 'blocked_channel_narrow', 'channels_joined', 'channel_detour',
               'forward_port_missing', 'aft_port_wrong_angle', 'forward_ring_broken']
ADV_WALLS = ['thin_hot_wall', 'thin_hot_wall_narrow', 'thin_cold_wall', 'thin_rib', 'sloped_wall_normal_thin']
ADV_OLD_CHECKS = ('components', 'edge_manifold', 'winding', 'bbox', 'volume', 'channel_count', 'hot_wall',
                  'cold_wall', 'rib')


def _adv_old_gate_errors(spec, tris):
    """Re-run every pre-existing gate through the public low-level functions; [] means all of them pass."""
    errs = []
    r = rm.clean_and_check(tris)
    errs += list(r.failures)
    for k in ('components', 'edge_manifold', 'winding'):
        if r.checks[k]['state'] != 'pass':
            errs.append(f'{k} failed')
    pts = r.verts[np.unique(r.faces)]
    if rm.check_bbox(pts.min(axis=0), pts.max(axis=0), spec)['state'] != 'pass':
        errs.append('bbox failed')
    vol = rm.check_volume(r.volume, spec)
    if vol['state'] != 'pass' or abs(vol['relative_error']) > 0.02:
        errs.append(f'volume failed ({vol["relative_error"]:.4f})')
    zs = rm.station_zs(spec)
    if len(zs) != 7:
        errs.append('expected seven stations')
    for z in zs:
        m = rm.slice_station(r.verts, r.faces, z, spec.channel_count)
        errs += rm.station_failures(m, spec.channel_count, spec.min_feature_mm, spec.voxel_size_mm)
    return errs


@functools.lru_cache(maxsize=None)
def _adv_eval(name):
    spec = adv_spec()
    tris = adv_mesh(name)
    report, _ = rm.verify(spec, tris)
    probe = None
    zc, _hw = ADV_DEFECT_Z.get(name, (None, None))
    if zc is not None:
        verts, faces = rm.weld(tris)
        probe = rm.slice_station(verts, faces, zc, spec.channel_count)
    return {'report': report, 'old_gate_errors': _adv_old_gate_errors(spec, tris), 'probe': probe,
            'triangles': len(tris)}


def _assert_safety_fields(report):
    assert report['engineering_certified'] is False
    assert report['physics'] == {'state': 'not_evaluated'}
    assert report['standards'] == {'state': 'not_evaluated'}


def test_adv_spec_is_valid_without_bypass():
    spec = adv_spec()
    assert RegenChamberSpec.model_validate(spec.model_dump()) == spec
    assert len(rm.station_zs(spec)) == 7
    assert spec.max_slope <= math.tan(math.radians(35.0))
    for z in (0.0, 5.0, 12.0, 20.0, 26.0, 33.0, 40.0):   # local field model matches the spec contour
        assert float(_adv_ri(spec, z)) == pytest.approx(inner_radius(spec, z), abs=1e-12)


def test_adv_positive_control_passes_complete_verifier():
    got = _adv_eval('control')
    rep = got['report']
    assert got['old_gate_errors'] == []
    assert rep['failures'] == [] and rep['passed'] is True
    assert rep['design_intent'] == {'state': 'geometry_matches_spec'}
    assert all(c['state'] == 'pass' for c in rep['checks'].values())
    assert abs(rep['checks']['volume']['relative_error']) < 0.02
    _assert_safety_fields(rep)


@pytest.mark.parametrize('name', sorted(n for n, v in ADV_DEFECT_Z.items() if v[0] is not None))
def test_adv_defects_sit_between_station_planes(name):
    zc, hw = ADV_DEFECT_Z[name]
    for z in rm.station_zs(adv_spec()):
        assert abs(zc - z) - hw > 0.1, f'{name}: defect support reaches station z={z:.3f}'


@pytest.mark.parametrize('name', ADV_CIRCUIT + ADV_WALLS + ['wall_breach'])
def test_adv_fixtures_pass_every_existing_gate(name):
    """Closed, one component, wound outward, bbox + volume (<=2 %) + all seven stations clean."""
    got = _adv_eval(name)
    assert got['old_gate_errors'] == []
    rep = got['report']
    for k in ADV_OLD_CHECKS:
        assert rep['checks'][k]['state'] == 'pass', k
    assert rep['checks']['components']['found'] == 1
    assert abs(rep['checks']['volume']['relative_error']) <= 0.02


def _adv_expect_defect_present(name, got):
    """The mesh really contains the defect: a slice through the defect plane (not a station) shows it."""
    spec = adv_spec()
    floor = spec.min_feature_mm - spec.voxel_size_mm
    p = got['probe']
    if name in ('blocked_channel', 'blocked_channel_narrow', 'channels_joined', 'wall_breach'):
        assert p['channel_loops'] == spec.channel_count - 1
    elif name in ('thin_hot_wall', 'thin_hot_wall_narrow'):
        assert p['hot_wall_mm'] < floor
    elif name == 'sloped_wall_normal_thin':
        assert p['hot_wall_mm'] < 1.0
    elif name == 'thin_cold_wall':
        assert p['cold_wall_mm'] < floor
    elif name == 'thin_rib':
        assert p['rib_mm'] < floor
    elif name == 'channel_detour':
        assert p['channel_loops'] == spec.channel_count and p['rib_mm'] < 1.6   # nominal rib here is ~2.3 mm


@pytest.mark.parametrize('name', ADV_CIRCUIT + ['wall_breach'])
def test_adv_coolant_circuit_defect_off_station_is_rejected(name):
    got = _adv_eval(name)
    assert got['old_gate_errors'] == []
    if got['probe'] is not None:
        _adv_expect_defect_present(name, got)
    rep = got['report']
    assert rep['passed'] is False, f'{name}: coolant-circuit defect accepted'
    assert rep['failures']
    assert rep['design_intent'] == {'state': 'geometry_rejected'}
    _assert_safety_fields(rep)


@pytest.mark.parametrize('name', ADV_WALLS)
def test_adv_local_thin_wall_off_station_is_rejected(name):
    got = _adv_eval(name)
    assert got['old_gate_errors'] == []
    _adv_expect_defect_present(name, got)
    rep = got['report']
    assert rep['passed'] is False, f'{name}: locally thin wall accepted'
    assert rep['failures']
    assert rep['design_intent'] == {'state': 'geometry_rejected'}
    _assert_safety_fields(rep)


def test_adv_sloped_wall_in_plane_distance_conceals_thin_normal_wall():
    spec = adv_spec()
    got = _adv_eval('sloped_wall_normal_thin')
    zc, _ = ADV_DEFECT_Z['sloped_wall_normal_thin']
    floor = spec.min_feature_mm - spec.voxel_size_mm
    dz = 1e-3
    slope = (inner_radius(spec, zc + dz) - inner_radius(spec, zc - dz)) / (2 * dz)
    cos_a = 1.0 / math.sqrt(1.0 + slope ** 2)
    in_plane = got['probe']['hot_wall_mm']
    assert in_plane >= floor, 'in-plane hot wall must look acceptable'
    assert in_plane * cos_a < floor, 'surface-normal thickness must be below the minimum'
    assert got['report']['passed'] is False


@pytest.mark.parametrize('name', ['blocked_channel_narrow', 'thin_hot_wall_narrow'])
def test_adv_narrow_defect_between_sampling_locations_is_rejected(name):
    zc, hw = ADV_DEFECT_Z[name]
    zs = rm.station_zs(adv_spec())
    below = max(z for z in zs if z < zc)
    above = min(z for z in zs if z > zc)
    assert zc - hw - below > 1.0 and above - (zc + hw) > 1.0
    assert 2 * hw <= 1.4   # the whole defect is narrower than the clear gap on each side of it
    got = _adv_eval(name)
    assert got['old_gate_errors'] == []
    assert got['report']['passed'] is False


def test_adv_wall_breach_cannot_be_covered_and_is_rejected():
    """Channel 5 breaks into the bore between stations: the plane has 15 channel loops and the inner wall
    loop swallows one, so wall/rib coverage there cannot be established. A verifier must reject, not skip."""
    got = _adv_eval('wall_breach')
    assert got['old_gate_errors'] == []
    assert got['probe']['channel_loops'] == adv_spec().channel_count - 1
    rep = got['report']
    assert rep['passed'] is False and rep['failures']
    _assert_safety_fields(rep)


def test_adv_reports_are_deterministic():
    spec = adv_spec()
    name = 'blocked_channel_narrow'
    first = _adv_eval(name)['report']
    tris = adv_mesh(name)            # rebuilt from scratch
    again, _ = rm.verify(spec, tris)
    again2, _ = rm.verify(spec, tris.copy())
    assert json.dumps(first, sort_keys=True, default=str) == json.dumps(again, sort_keys=True, default=str)
    assert json.dumps(again, sort_keys=True, default=str) == json.dumps(again2, sort_keys=True, default=str)
    assert first['stl_sha256'] == again['stl_sha256']
    assert first['passed'] is False and again['passed'] is False
