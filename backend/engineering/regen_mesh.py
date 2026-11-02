"""Mesh verifier for the PicoGK regen-chamber slice. numpy/scipy only.

Everything here measures the mesh against the typed spec. It does not evaluate gas
dynamics, thermal behaviour, or structure. Wall measurements are taken in z planes, so hot
and cold wall values are in-plane distances, not surface-normal ones (on a sloped wall the
in-plane value is larger than the normal thickness).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .regen_geometry import RegenGeometry, _ri as _ref_ri, _seg_dist as _ref_seg_dist
from .regen_spec import RegenChamberSpec, inner_radius, specification_digest

SPECK_VOLUME_MM3 = 1e-3          # components with |signed volume| below this are removed and recorded
VOLUME_TOLERANCE = 0.02          # relative, vs the analytic estimate. Not tuned; see docs.
BBOX_TOLERANCE_VOXELS = 2.0
STATION_COUNT = 7
SAMPLES_PER_SEGMENT = 5
CLEAN_HEADER = b'mirum regen_chamber cleaned mesh'.ljust(80, b'\0')

_STL_DTYPE = np.dtype([('n', '<f4', (3,)), ('v', '<f4', (3, 3)), ('a', '<u2')])


# --------------------------------------------------------------------------- STL io
def read_stl(path) -> np.ndarray:
    data = Path(path).read_bytes()
    if len(data) < 84:
        raise ValueError('STL too short')
    n = int(np.frombuffer(data, dtype='<u4', count=1, offset=80)[0])
    if len(data) != 84 + 50 * n:
        raise ValueError(f'Binary STL size mismatch: header says {n} triangles, file has {len(data)} bytes')
    arr = np.frombuffer(data, dtype=_STL_DTYPE, count=n, offset=84)
    tris = np.ascontiguousarray(arr['v'])
    if not np.isfinite(tris).all():
        raise ValueError('STL contains non-finite coordinates')
    return tris


def stl_bytes(tris: np.ndarray) -> bytes:
    arr = np.zeros(len(tris), dtype=_STL_DTYPE)
    arr['v'] = tris
    return CLEAN_HEADER + np.uint32(len(tris)).tobytes() + arr.tobytes()


def write_stl(path, tris: np.ndarray) -> None:
    Path(path).write_bytes(stl_bytes(tris))


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- weld / clean
def weld(tris: np.ndarray):
    """Weld by exact vertex equality (-0.0 normalised to 0.0). Returns (verts float32, faces int64)."""
    flat = np.ascontiguousarray(tris.reshape(-1, 3), dtype=np.float32) + np.float32(0.0)
    u = flat.view(np.uint32).astype(np.uint64)
    m1, m2, m3 = np.uint64(0x9E3779B97F4A7C15), np.uint64(0xC2B2AE3D27D4EB4F), np.uint64(0x165667B19E3779F9)
    key = (u[:, 0] * m1) ^ ((u[:, 1] * m2) >> np.uint64(7)) ^ (u[:, 2] * m3)
    key ^= key >> np.uint64(29)
    _, first, inverse = np.unique(key, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    verts = flat[first]
    if not np.array_equal(verts[inverse].view(np.uint32), flat.view(np.uint32)):
        # hash collision: exact (slow) path
        rows = flat.view(np.dtype((np.void, 12))).reshape(-1)
        _, first, inverse = np.unique(rows, return_index=True, return_inverse=True)
        inverse = inverse.reshape(-1)
        verts = flat[first]
    return verts, inverse.reshape(-1, 3).astype(np.int64)


@dataclass
class MeshResult:
    verts: np.ndarray
    faces: np.ndarray
    checks: dict
    failures: list
    removed: list
    dropped_degenerate: int
    volume: float
    input_triangles: int = 0
    components_found: int = 0


def _component_labels(faces, nv):
    a = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    b = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    g = coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(nv, nv))
    n, lab = connected_components(g, directed=False)
    return n, lab


def _signed_volumes(verts64, faces, face_label, ncomp):
    p0, p1, p2 = verts64[faces[:, 0]], verts64[faces[:, 1]], verts64[faces[:, 2]]
    v6 = np.einsum('ij,ij->i', p0, np.cross(p1, p2)) / 6.0
    return np.bincount(face_label, weights=v6, minlength=ncomp)


def clean_and_check(tris: np.ndarray, speck_volume_mm3: float = SPECK_VOLUME_MM3) -> MeshResult:
    failures: list = []
    checks: dict = {}
    verts, faces = weld(tris)
    n_in = len(faces)
    # degenerates: repeated vertex index or exactly zero area
    dup = (faces[:, 0] == faces[:, 1]) | (faces[:, 1] == faces[:, 2]) | (faces[:, 0] == faces[:, 2])
    v64 = verts.astype(np.float64)
    cr = np.cross(v64[faces[:, 1]] - v64[faces[:, 0]], v64[faces[:, 2]] - v64[faces[:, 0]])
    zero_area = ~np.any(cr != 0.0, axis=1)
    bad = dup | zero_area
    dropped = int(bad.sum())
    faces = faces[~bad]
    if len(faces) == 0:
        checks['components'] = {'state': 'fail', 'detail': 'no triangles'}
        return MeshResult(verts, faces, checks, ['mesh has no usable triangles'], [], dropped, 0.0, n_in, 0)
    # components + signed volume (about the bbox centre to limit cancellation)
    centre = (v64.min(axis=0) + v64.max(axis=0)) / 2
    ncomp_all, lab = _component_labels(faces, len(verts))
    face_label_raw = lab[faces[:, 0]]
    used, face_label = np.unique(face_label_raw, return_inverse=True)
    face_label = face_label.reshape(-1)
    ncomp = len(used)
    vols = _signed_volumes(v64 - centre, faces, face_label, ncomp)
    tri_counts = np.bincount(face_label, minlength=ncomp)
    main = int(np.argmax(np.abs(vols)))
    removed, significant = [], []
    for c in range(ncomp):
        if c == main:
            continue
        if abs(vols[c]) < speck_volume_mm3:
            fc = faces[face_label == c]
            pts = v64[np.unique(fc)]
            removed.append({'triangles': int(tri_counts[c]), 'signed_volume_mm3': float(vols[c]),
                            'centroid_mm': [round(float(x), 4) for x in pts.mean(axis=0)]})
        else:
            significant.append({'triangles': int(tri_counts[c]), 'signed_volume_mm3': float(vols[c])})
    keep_comp = np.ones(ncomp, dtype=bool)
    for c in range(ncomp):
        if c != main and abs(vols[c]) < speck_volume_mm3:
            keep_comp[c] = False
    kept = keep_comp[face_label]
    faces = faces[kept]
    removed.sort(key=lambda r: (r['centroid_mm'], r['triangles']))
    comp_ok = not significant
    checks['components'] = {'state': 'pass' if comp_ok else 'fail', 'found': ncomp,
                            'removed_specks': removed, 'unexpected_components': significant,
                            'speck_volume_threshold_mm3': speck_volume_mm3}
    if not comp_ok:
        failures.append(f'{len(significant)} unexpected extra component(s); a sealed void means a blocked channel')
    # edge manifold + winding on the kept mesh
    nv = np.int64(len(verts))
    a = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    b = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    ukey = np.minimum(a, b) * nv + np.maximum(a, b)
    _, ucnt = np.unique(ukey, return_counts=True)
    bad_edges = int((ucnt != 2).sum())
    checks['edge_manifold'] = {'state': 'pass' if bad_edges == 0 else 'fail', 'edges': int(len(ucnt)),
                               'edges_not_used_twice': bad_edges}
    if bad_edges:
        failures.append(f'{bad_edges} edge(s) not used exactly twice (mesh is not closed 2-manifold)')
    dkey = a * nv + b
    _, dcnt = np.unique(dkey, return_counts=True)
    conflicts = int((dcnt > 1).sum())
    total_vol = float(vols[keep_comp].sum())
    wind_ok = conflicts == 0 and total_vol > 0
    checks['winding'] = {'state': 'pass' if wind_ok else 'fail', 'directed_edge_conflicts': conflicts,
                         'signed_volume_mm3': total_vol}
    if not wind_ok:
        failures.append(f'winding: {conflicts} directed-edge conflict(s), signed volume {total_vol:.3f} mm3 (must be > 0, outward normals)')
    return MeshResult(verts, faces, checks, failures, removed, dropped, total_vol, n_in, ncomp)


# --------------------------------------------------------------------------- plane slices
def slice_station(verts: np.ndarray, faces: np.ndarray, z: float, channel_count: int) -> dict:
    """Intersect the mesh with plane z, group crossed edges into closed loops, classify by radius."""
    zf = verts[:, 2].astype(np.float64)
    side = zf >= z  # symbolic perturbation: vertices exactly on the plane count as above
    s = side[faces]
    cross_f = ~(s.all(axis=1) | (~s).all(axis=1))
    F = faces[cross_f]
    S = s[cross_f]
    out = {'z_mm': float(z), 'channel_loops': 0}
    if len(F) == 0:
        out['error'] = 'plane does not intersect the mesh'
        return out
    e0 = np.stack([F[:, 0], F[:, 1], F[:, 2]], axis=1)
    e1 = np.stack([F[:, 1], F[:, 2], F[:, 0]], axis=1)
    differ = S != np.roll(S, -1, axis=1)
    if not (differ.sum(axis=1) == 2).all():
        out['error'] = 'triangle crossing inconsistent'
        return out
    nv = np.int64(len(verts))
    ea, eb = e0[differ], e1[differ]
    keys = np.minimum(ea, eb) * nv + np.maximum(ea, eb)
    ukeys, first, inv = np.unique(keys, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    pa, pb = ea[first], eb[first]
    za, zb = zf[pa], zf[pb]
    t = np.clip((z - za) / (zb - za), 0.0, 1.0)
    P = verts[pa, :2].astype(np.float64) * (1 - t)[:, None] + verts[pb, :2].astype(np.float64) * t[:, None]
    seg = inv.reshape(-1, 2)
    g = coo_matrix((np.ones(len(seg), dtype=np.int8), (seg[:, 0], seg[:, 1])), shape=(len(P), len(P)))
    nl, lab = connected_components(g, directed=False)
    out['loops'] = int(nl)
    rad = np.hypot(P[:, 0], P[:, 1])
    ang = np.arctan2(P[:, 1], P[:, 0])
    enclosing, others = [], []
    for k in range(nl):
        idx = np.nonzero(lab == k)[0]
        aa = np.sort(ang[idx])
        gaps = np.diff(np.concatenate([aa, [aa[0] + 2 * math.pi]]))
        (enclosing if gaps.max() < math.pi else others).append((k, float(rad[idx].mean())))
    if len(enclosing) < 2:
        out['error'] = f'cannot identify inner and outer wall loops ({len(enclosing)} loop(s) enclose the axis)'
        out['channel_loops'] = len(others)
        return out
    enclosing.sort(key=lambda kr: kr[1])
    inner_k, outer_k = enclosing[0][0], enclosing[-1][0]
    chan = [k for k, _ in others] + [k for k, _ in enclosing[1:-1]]
    out['channel_loops'] = len(chan)
    out['inner_radius_mm'] = enclosing[0][1]
    out['outer_radius_mm'] = enclosing[-1][1]
    # densify segments for point-set distances
    tt = np.linspace(0.0, 1.0, SAMPLES_PER_SEGMENT)
    pts = (P[seg[:, 0]][:, None, :] * (1 - tt)[None, :, None] + P[seg[:, 1]][:, None, :] * tt[None, :, None]).reshape(-1, 2)
    plab = np.repeat(lab[seg[:, 0]], SAMPLES_PER_SEGMENT)
    chan_mask = np.isin(plab, chan)
    cpts, clab = pts[chan_mask], plab[chan_mask]
    if len(cpts) == 0:
        return out
    ctree = cKDTree(cpts)
    out['hot_wall_mm'] = float(ctree.query(pts[plab == inner_k])[0].min())
    out['cold_wall_mm'] = float(ctree.query(pts[plab == outer_k])[0].min())
    if len(chan) > 1:
        best = math.inf
        for k in chan:
            m = clab == k
            d = cKDTree(cpts[~m]).query(cpts[m])[0].min()
            best = min(best, float(d))
        out['rib_mm'] = best
    return out


def _station_issues(meas: dict, channel_count: int, min_mm: float, voxel_mm: float) -> dict:
    z = meas.get('z_mm')
    issues = {}
    if 'error' in meas and meas.get('channel_loops') != channel_count:
        issues['slice'] = f'z={z:.2f}: {meas["error"]}'
    if meas.get('channel_loops') != channel_count:
        issues['channel_count'] = f'z={z:.2f}: {meas.get("channel_loops")} channel loops, expected {channel_count}'
    floor = min_mm - voxel_mm
    for key, label in (('hot_wall_mm', 'hot wall'), ('cold_wall_mm', 'cold wall'), ('rib_mm', 'rib')):
        v = meas.get(key)
        if v is None:
            if 'channel_count' not in issues and 'slice' not in issues and key != 'rib_mm':
                issues[key] = f'z={z:.2f}: {label} not measurable'
        elif v < floor:
            issues[key] = f'z={z:.2f}: {label} {v:.3f} mm < {floor:.3f} mm (min {min_mm:.3f} - 1 voxel)'
    return issues


def station_failures(meas: dict, channel_count: int, min_mm: float, voxel_mm: float) -> list:
    return list(_station_issues(meas, channel_count, min_mm, voxel_mm).values())


# --------------------------------------------------------------------------- analytic estimates
def _contour_grid(spec: RegenChamberSpec, step: float = 0.01):
    L = spec.total_length_mm
    n = int(round(L / step)) + 1
    z = np.linspace(0.0, L, n)
    r = np.array([inner_radius(spec, float(x)) for x in z])
    return z, r


def expected_bbox(spec: RegenChamberSpec):
    _, r = _contour_grid(spec)
    ro = float(r.max()) + spec.wall_thickness_mm
    return (np.array([-ro, -ro, 0.0]), np.array([ro, ro, spec.total_length_mm]))


def analytic_shell_volume(spec: RegenChamberSpec) -> float:
    z, r = _contour_grid(spec)
    w = spec.wall_thickness_mm
    return float(np.trapezoid(math.pi * (2 * r * w + w * w), z))


def analytic_volume(spec: RegenChamberSpec) -> float:
    """Shell revolve minus helical channels, manifold tori and wall ports.

    Overlaps (channel ends inside manifold rings, port inside ring) and end caps are neglected;
    they are well under 1 percent of the part volume for supported specs.
    """
    z, r = _contour_grid(spec)
    w = spec.wall_thickness_mm
    shell = analytic_shell_volume(spec)
    z1, z2 = spec.manifold_z_mm
    m = (z >= z1) & (z <= z2)
    rm_ = r + w / 2
    drdz = np.gradient(r, z)
    ds = np.sqrt(1 + drdz ** 2 + (rm_ * spec.channel_twist_rad_per_mm) ** 2)
    length = float(np.trapezoid(ds[m], z[m]))
    channels = spec.channel_count * math.pi * spec.channel_radius_mm ** 2 * length
    mr = spec.manifold_radius_mm
    rings = sum(2 * math.pi * (inner_radius(spec, zm) + w / 2) * math.pi * mr ** 2 for zm in (z1, z2))
    ports = 2 * math.pi * mr ** 2 * (w / 2)
    return shell - channels - rings - ports


def check_bbox(lo, hi, spec: RegenChamberSpec) -> dict:
    elo, ehi = expected_bbox(spec)
    tol = BBOX_TOLERANCE_VOXELS * spec.voxel_size_mm
    err = float(max(np.abs(np.asarray(lo) - elo).max(), np.abs(np.asarray(hi) - ehi).max()))
    return {'state': 'pass' if err <= tol else 'fail', 'max_error_mm': err, 'tolerance_mm': tol,
            'measured_min': [float(x) for x in lo], 'measured_max': [float(x) for x in hi],
            'expected_min': [float(x) for x in elo], 'expected_max': [float(x) for x in ehi]}


def check_volume(measured: float, spec: RegenChamberSpec) -> dict:
    est = analytic_volume(spec)
    rel = (measured - est) / est
    return {'state': 'pass' if abs(rel) <= VOLUME_TOLERANCE else 'fail', 'measured_mm3': float(measured),
            'analytic_mm3': est, 'relative_error': rel, 'tolerance': VOLUME_TOLERANCE}


def station_zs(spec: RegenChamberSpec) -> list:
    z1, z2 = spec.manifold_z_mm
    zs = list(np.linspace(z1, z2, STATION_COUNT + 1)[1:-1]) + [spec.throat_z_mm]
    return sorted(float(x) for x in zs)


# --------------------------------------------------------------------------- whole-part verification
# Compares the mesh with the reference geometry (regen_geometry.RegenGeometry) over the WHOLE surface:
#   circuit_fidelity  mesh -> reference: every surface patch lies on a surface the spec defines
#   circuit_coverage  reference -> mesh: every specified branch/ring/port is an open, connected void
#   wall_thickness    3D facing-surface distance for hot/cold walls, ribs, manifold clearances, end regions
# Geometry only. Unresolved / ambiguous / budget-exhausted outcomes reject; nothing is skipped.
WP_REFINE_FRACTION = 0.5     # leaf radius target, as a fraction of the surface tolerance
WP_MAX_SPLIT_DEPTH = 6       # uniform 4-way splits to reach the leaf radius target
WP_FAIL_SPLIT_DEPTH = 3      # extra adaptive splits for leaves that do not conform
WP_MAX_ADAPT_LEAVES = 50_000
WP_LEAF_BUDGET = 40_000_000
WP_PAIR_BUDGET = 60_000_000
WP_PAIR_MAX_SPLITS = 16
WP_CHUNK = 250_000
WP_BLOCK = 300_000
WP_FACING_MARGIN = 0.2       # |cos| of the angle between the pair direction and each surface normal
WP_THICKNESS_ALLOWANCE_VOXELS = 0.5   # combined iso-surface placement allowance for a surface pair
WP_REPORT_LOCATIONS = 8

L_HOT, L_COLD, L_CAP0, L_CAPL, L_CH, L_RING, L_PORT = range(7)
LABEL_NAMES = ('hot_wall', 'cold_wall', 'end_cap_forward', 'end_cap_aft', 'channel', 'manifold_ring', 'port')
R_OK, R_OFF, R_COVER, R_AIR = 0, 1, 2, 3
REASON_NAMES = {R_OK: 'ok', R_OFF: 'surface_not_on_specified_geometry',
                R_COVER: 'surface_covers_specified_opening', R_AIR: 'coolant_surface_outside_part'}


class _Exhausted(Exception):
    pass


def _tri_stats(T):
    c = T.mean(axis=1)
    return c, np.linalg.norm(T - c[:, None, :], axis=2).max(axis=1)


def _tri_normals(T):
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(n, axis=1)
    return n / np.where(ln > 0, ln, 1.0)[:, None]


def _split4(T):
    v0, v1, v2 = T[:, 0], T[:, 1], T[:, 2]
    m01, m12, m20 = (v0 + v1) / 2, (v1 + v2) / 2, (v2 + v0) / 2
    kids = np.stack([np.stack([v0, m01, m20], 1), np.stack([m01, v1, m12], 1),
                     np.stack([m20, m12, v2], 1), np.stack([m01, m12, m20], 1)], 1)
    return kids.reshape(-1, 3, 3)


class _Ctx:
    def __init__(self, spec: RegenChamberSpec):
        self.spec = spec
        self.g = RegenGeometry(spec)
        g = self.g
        self.tau = g.surface_tolerance_mm
        self.rho_target = WP_REFINE_FRACTION * self.tau
        self.void_depth = min(self.tau, 0.5 * g.rm)
        self.md = g.default_max_distance_mm
        self.eps_band = g.v + self.tau


def _shell_proximity(g, P):
    """Upper bound of the distance to the nearest shell surface (hot, cold, end caps) and which one."""
    rho = np.hypot(P[:, 0], P[:, 1])
    z = P[:, 2]
    zc = np.clip(z, 0.0, g.L)
    ri = _ref_ri(g.spec, zc)
    dz = np.abs(z - zc)
    ri0, riL = float(_ref_ri(g.spec, 0.0)), float(_ref_ri(g.spec, g.L))
    rad0 = np.maximum(np.maximum(ri0 - rho, rho - ri0 - g.wall), 0.0)
    radL = np.maximum(np.maximum(riL - rho, rho - riL - g.wall), 0.0)
    D = np.stack([np.hypot(np.abs(rho - ri), dz), np.hypot(np.abs(rho - ri - g.wall), dz),
                  np.hypot(np.abs(z), rad0), np.hypot(np.abs(z - g.L), radL)], axis=1)
    return D.min(axis=1), D.argmin(axis=1)


def _in_band(g, P, eps):
    """Conservative: False means the point is farther than the tube radius from every coolant axis."""
    rho = np.hypot(P[:, 0], P[:, 1])
    z = P[:, 2]
    ri = _ref_ri(g.spec, np.clip(z, 0.0, g.L))
    m = ((np.abs(rho - ri - g.wall / 2) <= g.rc * (1 + g.max_slope) + eps)
         & (z >= g.z0 - g.rc - eps) & (z <= g.z1 + g.rc + eps))
    for j, mz in enumerate(g.ring_z):
        nz = np.abs(z - mz) <= g.rm + eps
        m |= nz & (np.abs(rho - g.ring_r[j]) <= g.rm + eps)
        m |= (nz & (np.abs(P[:, 1]) <= g.rm + eps) & (P[:, 0] >= g.ring_r[j] - g.rm - eps)
              & (P[:, 0] <= g.port_ends[j] + g.rm + eps))
    return m


def _classify(ctx, P, rho):
    g, tau = ctx.g, ctx.tau
    n = len(P)
    u, which = _shell_proximity(g, P)
    tp = tau - rho
    shell_near = u <= tp
    need = (~shell_near) | _in_band(g, P, ctx.eps_band)
    cmin = np.full(n, np.inf)
    fam = np.zeros(n, dtype=np.int64)
    idx = np.nonzero(need)[0]
    if len(idx):
        parts = g.coolant_sdf_parts(P[idx], ctx.md)
        A = np.stack([parts['channels'], parts['rings'], parts['ports']], axis=1)
        cmin[idx] = A.min(axis=1)
        fam[idx] = np.abs(A).argmin(axis=1)
    sh = g.shell_sdf_lower(P)
    covered = shell_near & (cmin < -ctx.void_depth)
    on_cool = (~shell_near) & (np.abs(cmin) <= tp)
    cool_ok = on_cool & (sh <= tp)
    conform = (shell_near & ~covered) | cool_ok
    reason = np.where(conform, R_OK, np.where(covered, R_COVER, np.where(on_cool, R_AIR, R_OFF))).astype(np.int8)
    lb = np.minimum(np.abs(sh), np.where(np.isfinite(cmin), np.abs(cmin), np.inf))
    cert = (~conform) & (lb - rho > tau)
    label = np.where(shell_near, which, L_CH + fam).astype(np.int8)
    return {'conform': conform, 'reason': reason, 'label': label, 'cert': cert}


def _build_leaves(ctx, verts, faces):
    """Adaptive leaf triangles: every mesh point lies within `rho` of a leaf centroid. Returns arrays + stats."""
    store = {k: [] for k in ('T', 'cen', 'rho', 'nrm', 'label', 'conform', 'reason', 'cert')}
    stats = {'input_triangles': int(len(faces)), 'unsplit_over_target': 0, 'adaptive_leaves': 0,
             'adaptive_capped': 0}
    total = 0

    def record(T, nrm, c, r, res, sel=None):
        nonlocal total
        if sel is not None:
            T, nrm, c, r = T[sel], nrm[sel], c[sel], r[sel]
            res = {k: v[sel] for k, v in res.items()}
        total += len(T)
        if total > WP_LEAF_BUDGET:
            raise _Exhausted(f'leaf budget {WP_LEAF_BUDGET} exceeded')
        store['T'].append(T.astype(np.float32))
        store['cen'].append(c)
        store['rho'].append(r.astype(np.float32))
        store['nrm'].append(nrm.astype(np.float32))
        for k in ('label', 'conform', 'reason', 'cert'):
            store[k].append(res[k])

    for s in range(0, len(faces), WP_CHUNK):
        T0 = verts[faces[s:s + WP_CHUNK]].astype(np.float64)
        N0 = _tri_normals(T0)
        parts_T, parts_N = [], []
        cur, curn = T0, N0
        for depth in range(WP_MAX_SPLIT_DEPTH + 1):
            c, r = _tri_stats(cur)
            big = r > ctx.rho_target
            if depth == WP_MAX_SPLIT_DEPTH:
                stats['unsplit_over_target'] += int(big.sum())
                big = np.zeros_like(big)
            parts_T.append(cur[~big])
            parts_N.append(curn[~big])
            if not big.any():
                break
            cur, curn = _split4(cur[big]), np.repeat(curn[big], 4, axis=0)
        T = np.concatenate(parts_T)
        N = np.concatenate(parts_N)
        c, r = _tri_stats(T)
        res = _classify(ctx, c, r)
        good = res['conform']
        record(T, N, c, r, res, good)
        bad = ~good
        cur, curn = T[bad], N[bad]
        cres = {k: v[bad] for k, v in res.items()}
        if len(cur) > WP_MAX_ADAPT_LEAVES:
            stats['adaptive_capped'] += int(len(cur))
            cc, cr = _tri_stats(cur)
            record(cur, curn, cc, cr, cres)
            continue
        for _ in range(WP_FAIL_SPLIT_DEPTH):
            if not len(cur):
                break
            kids, kn = _split4(cur), np.repeat(curn, 4, axis=0)
            kc, kr = _tri_stats(kids)
            kres = _classify(ctx, kc, kr)
            stats['adaptive_leaves'] += int(len(kids))
            record(kids, kn, kc, kr, kres, kres['conform'])
            bad = ~kres['conform']
            cur, curn, cres = kids[bad], kn[bad], {k: v[bad] for k, v in kres.items()}
        if len(cur):
            cc, cr = _tri_stats(cur)
            record(cur, curn, cc, cr, cres)
    if not store['T']:
        raise _Exhausted('no leaves')
    out = {k: np.concatenate(v) for k, v in store.items()}
    out['stats'] = stats
    return out


def _loc(p):
    return [round(float(x), 4) for x in p]


def _order_rows(cen, extra=None):
    """Deterministic ordering of locations: z, then x, then y (rounded), then extra keys."""
    keys = [np.round(cen[:, 1], 4), np.round(cen[:, 0], 4), np.round(cen[:, 2], 4)]
    if extra is not None:
        keys.append(extra)
    return np.lexsort(tuple(keys))


# ----- circuit fidelity (mesh -> reference)
def _fidelity(ctx, L):
    bad = np.nonzero(~L['conform'])[0]
    by_reason = {}
    for rc_ in (R_OFF, R_COVER, R_AIR):
        m = L['reason'][bad] == rc_
        by_reason[REASON_NAMES[rc_]] = int(m.sum())
    ordr = bad[_order_rows(L['cen'][bad], L['reason'][bad])][:WP_REPORT_LOCATIONS]
    locs = [{'at_mm': _loc(L['cen'][i]), 'reason': REASON_NAMES[int(L['reason'][i])],
             'certified_deviation': bool(L['cert'][i]), 'patch_radius_mm': round(float(L['rho'][i]), 4)}
            for i in ordr]
    unresolved = int((~L['cert'][bad]).sum())
    st = L['stats']
    ok = len(bad) == 0 and st['unsplit_over_target'] == 0
    return {'state': 'pass' if ok else 'fail', 'surface_tolerance_mm': ctx.tau,
            'sample_radius_target_mm': ctx.rho_target, 'sample_radius_max_mm': float(L['rho'].max()),
            'leaf_patches': int(len(L['rho'])), 'nonconforming_patches': int(len(bad)),
            'uncertified_nonconforming_patches': unresolved, 'unsplit_over_radius_target': st['unsplit_over_target'],
            'by_reason': by_reason, 'locations': locs,
            'void_cover_depth_mm': ctx.void_depth,
            'error_bound': 'every point of every mesh triangle lies within sample_radius_max_mm of a checked centroid; '
                           'each checked centroid lies within surface_tolerance_mm - patch radius of a specified surface'}


# ----- circuit coverage (reference -> mesh)
def _coverage(ctx, L):
    g = ctx.g
    cen, nrm = L['cen'], L['nrm']
    rho_max = float(L['rho'].max())
    tree = cKDTree(cen)
    r_of = {'channel': g.rc, 'ring': g.rm, 'port': g.rm}
    tau_f = {k: min(ctx.tau, 0.5 * r) for k, r in r_of.items()}
    need_f = {k: r_of[k] - tau_f[k] for k in r_of}
    gap = min(g.v, min(need_f.values()))
    fams, pts = [], []
    for fam, P, _r in g.centerline_samples(gap):
        pts.append(P)
        fams.append(np.full(len(P), {'channel': 0, 'ring': 1, 'port': 2}[fam], dtype=np.int8))
    Q = np.concatenate(pts)
    F = np.concatenate(fams)
    names = ('channel', 'ring', 'port')
    need = np.array([need_f[n] for n in names])[F]
    rad = np.array([r_of[n] for n in names])[F]
    d, ix = tree.query(Q)
    side = np.einsum('ij,ij->i', Q - cen[ix], nrm[ix].astype(np.float64))
    outside = side > 0
    rcert = d - rho_max
    ok = outside & (rcert >= need)
    bad = np.nonzero(~ok)[0]
    ordr = bad[_order_rows(Q[bad], F[bad])][:WP_REPORT_LOCATIONS]
    locs = [{'at_mm': _loc(Q[i]), 'family': names[int(F[i])],
             'reason': 'inside_material' if not outside[i] else 'void_narrower_than_specified',
             'certified_clearance_mm': round(float(rcert[i]), 4), 'required_mm': round(float(need[i]), 4)}
            for i in ordr]
    # connectivity: certified empty balls around consecutive samples must chain into one void
    good = np.nonzero(outside & (rcert > 0))[0]
    comps = 0
    if len(good):
        rb = np.minimum(rcert[good], rad[good])
        t2 = cKDTree(Q[good])
        pr = t2.query_pairs(2 * float(rb.max()), output_type='ndarray')
        if len(pr):
            dd = np.linalg.norm(Q[good][pr[:, 0]] - Q[good][pr[:, 1]], axis=1)
            pr = pr[dd <= rb[pr[:, 0]] + rb[pr[:, 1]]]
        nn = len(good)
        if len(pr):
            gm = coo_matrix((np.ones(len(pr), dtype=np.int8), (pr[:, 0], pr[:, 1])), shape=(nn, nn))
        else:
            gm = coo_matrix((nn, nn), dtype=np.int8)
        comps, _lab = connected_components(gm, directed=False)
    connected = comps == 1 and len(good) == len(Q)
    per = {n: {'samples': int((F == i).sum()), 'failed': int(((F == i) & ~ok).sum())} for i, n in enumerate(names)}
    state = 'pass' if (len(bad) == 0 and connected) else 'fail'
    return {'state': state, 'samples': int(len(Q)), 'per_family': per, 'failed_samples': int(len(bad)),
            'locations': locs, 'void_components': int(comps), 'connected_single_circuit': bool(connected),
            'centerline_sample_gap_mm': gap, 'centerline_error_bound_mm': g.coverage_error_bound_mm(gap),
            'required_void_radius_mm': {n: need_f[n] for n in names},
            'clearance_error_bound_mm': rho_max,
            'surface_tolerance_mm': ctx.tau,
            'min_certified_clearance_mm': (float(rcert[outside].min()) if outside.any() else None)}


# ----- continuous 3D thickness
def _local_lip(g, z0, z1):
    zs = np.linspace(max(z0, 0.0), min(z1, g.L), 2001)
    r = _ref_ri(g.spec, zs)
    s = float(np.abs(np.diff(r) / np.diff(zs)).max()) if len(zs) > 1 else 0.0
    return math.sqrt(1.0 + s * s) * 1.001


def _nominal_rib(g):
    a, b = g.channel_nodes(0), g.channel_nodes(1 % g.n_ch)
    best = math.inf
    for off in range(-3, 4):
        k = np.arange(len(a))
        j = k + off
        ok = (j >= 0) & (j < g.steps)
        if ok.any():
            best = min(best, float(_ref_seg_dist(a[k[ok]], b[j[ok]], b[j[ok] + 1]).min()))
    return best - 2 * g.rc


def _thresholds(ctx):
    g, s = ctx.g, ctx.spec
    m = s.min_feature_mm
    allowance = WP_THICKNESS_ALLOWANCE_VOXELS * g.v
    nom = {'wall': g.wall / 2 / g._lip - g.rc,
           'manifold': min(g.wall / 2 / _local_lip(g, mz - g.rm - g.wall, mz + g.rm + g.wall) - g.rm
                           for mz in g.ring_z),
           'end': s.end_margin_mm - g.rm,
           'rib': _nominal_rib(g)}
    out = {}
    for k, v in nom.items():
        req = min(m, v)
        out[k] = {'min_feature_mm': m, 'nominal_lower_bound_mm': float(v), 'required_mm': float(req),
                  'allowance_mm': allowance, 'effective_threshold_mm': float(max(req - allowance, allowance))}
    return out


def _rim_points(g, spacing):
    n = max(64, int(math.ceil(2 * math.pi * g.rm / spacing)))
    phi = np.arange(n) * (2 * math.pi / n)
    pts = []
    for mz in g.ring_z:
        y = g.rm * np.cos(phi)
        zz = mz + g.rm * np.sin(phi)
        ro = _ref_ri(g.spec, np.clip(zz, 0.0, g.L)) + g.wall
        pts.append(np.stack([np.sqrt(np.maximum(ro * ro - y * y, 0.0)), y, zz], axis=1))
    return np.concatenate(pts), 2 * math.pi * g.rm / n / 2


def _split_rows(B, sel, side):
    out = {}
    for k, v in B.items():
        if k == ('VA' if side == 'A' else 'VB'):
            out[k] = _split4(v[sel])
        else:
            out[k] = np.repeat(v[sel], 4, axis=0)
    return out


def _run_pairs(L, ii, jj, thr, leaf_tol, rim, state):
    """Branch and bound over leaf-triangle pairs. Records violations/unresolved in state."""
    T, nrm, lab = L['T'], L['nrm'], L['label']
    blocks = []
    for s in range(0, len(ii), WP_BLOCK):
        a, b = ii[s:s + WP_BLOCK], jj[s:s + WP_BLOCK]
        blocks.append({'VA': T[a].astype(np.float64), 'VB': T[b].astype(np.float64),
                       'NA': nrm[a].astype(np.float64), 'NB': nrm[b].astype(np.float64),
                       'la': lab[a], 'lb': lab[b], 'ia': a, 'ib': b, 'k': np.zeros(len(a), dtype=np.int16)})
    while blocks:
        B = blocks.pop()
        state['evaluated'] += len(B['ia'])
        if state['evaluated'] > WP_PAIR_BUDGET:
            state['exhausted'] = True
            return
        ca, ra = _tri_stats(B['VA'])
        cb, rb = _tri_stats(B['VB'])
        lb_ = np.linalg.norm(ca - cb, axis=1) - ra - rb
        open_ = lb_ < thr
        if rim is not None:
            # intended port opening: port-side patches lying wholly inside the rim collar are exempt
            rtree, zone = rim
            a_port = (B['la'] == L_PORT) & (B['lb'] == L_COLD)
            b_port = (B['lb'] == L_PORT) & (B['la'] == L_COLD)
            sel = np.nonzero(open_ & (a_port | b_port))[0]
            if len(sel):
                pc = np.where(a_port[sel, None], ca[sel], cb[sel])
                pr_ = np.where(a_port[sel], ra[sel], rb[sel])
                inside = rtree.query(pc)[0] + pr_ <= zone
                open_[sel[inside]] = False
        keep = np.nonzero(open_)[0]
        if not len(keep):
            continue
        B = {k: v[keep] for k, v in B.items()}
        ca, cb, ra, rb = ca[keep], cb[keep], ra[keep], rb[keep]
        PA = np.concatenate([B['VA'], ca[:, None, :]], axis=1)
        PB = np.concatenate([B['VB'], cb[:, None, :]], axis=1)
        D = np.linalg.norm(PA[:, :, None, :] - PB[:, None, :, :], axis=3).reshape(len(keep), 16)
        am = D.argmin(axis=1)
        rows = np.arange(len(keep))
        ub = D[rows, am]
        pa = PA[rows, am // 4]
        pb = PB[rows, am % 4]
        dv = pb - pa
        face = ((np.einsum('ij,ij->i', dv, B['NA']) < -WP_FACING_MARGIN * ub)
                & (np.einsum('ij,ij->i', dv, B['NB']) > WP_FACING_MARGIN * ub)) | (ub <= 1e-6)
        leaf = (ra + rb) <= leaf_tol
        exempt = np.zeros(len(keep), dtype=bool)
        if rim is not None:
            # leaf patches only partly inside the collar: decide by the closest sample on the port side
            port_pt = np.where((B['la'] == L_PORT)[:, None], pa, pb)
            is_pair = ((B['la'] == L_PORT) & (B['lb'] == L_COLD)) | ((B['lb'] == L_PORT) & (B['la'] == L_COLD))
            if is_pair.any():
                near = np.zeros(len(keep), dtype=bool)
                near[is_pair] = rtree.query(port_pt[is_pair])[0] <= zone
                exempt = is_pair & near
        at_leaf = leaf & ~exempt & face
        viol = at_leaf & (ub < thr)
        gray = at_leaf & (ub >= thr)
        if viol.any():
            state['violations'].append((ub[viol], (pa[viol] + pb[viol]) / 2, B['la'][viol], B['lb'][viol]))
        if gray.any():
            state['marginal'].append(((pa[gray] + pb[gray]) / 2, ub[gray], B['la'][gray], B['lb'][gray]))
        rest = ~leaf
        too_deep = rest & (B['k'] >= WP_PAIR_MAX_SPLITS)
        if too_deep.any():
            state['too_deep'].append((ca[too_deep], B['la'][too_deep], B['lb'][too_deep]))
        rest &= ~too_deep
        for side, pick in (('A', rest & (ra >= rb)), ('B', rest & (ra < rb))):
            sel = np.nonzero(pick)[0]
            if not len(sel):
                continue
            ch = _split_rows(B, sel, side)
            ch['k'] = ch['k'] + 1
            for s in range(0, len(ch['ia']), WP_BLOCK):
                blocks.append({k: v[s:s + WP_BLOCK] for k, v in ch.items()})


def _candidates(cen, qidx, tree, tidx, r):
    empty = np.zeros(0, dtype=np.int64)
    if not len(qidx) or tree is None:
        return empty, empty
    d, _ = tree.query(cen[qidx], k=1, distance_upper_bound=r)
    q = qidx[np.isfinite(d)]
    if not len(q):
        return empty, empty
    lists = tree.query_ball_point(cen[q], r)
    counts = np.fromiter((len(x) for x in lists), dtype=np.int64, count=len(lists))
    if counts.sum() > WP_PAIR_BUDGET:
        raise _Exhausted(f'{int(counts.sum())} candidate pairs exceed the pair budget')
    jj = tidx[np.concatenate([np.asarray(x, dtype=np.int64) for x in lists])]
    return np.repeat(q, counts), jj


def _channel_index(g, cen, nrm, sel):
    ax = cen[sel] + nrm[sel].astype(np.float64) * g.rc
    delta = 2 * math.pi / g.n_ch
    ang = np.arctan2(ax[:, 1], ax[:, 0]) - ax[:, 2] * g.twist
    return np.mod(np.rint(ang / delta).astype(np.int64), g.n_ch)


def _thickness(ctx, L):
    g = ctx.g
    thr = _thresholds(ctx)
    cen, lab, rho = L['cen'], L['label'], L['rho']
    leaf_tol = WP_THICKNESS_ALLOWANCE_VOXELS * g.v / 4
    rmax = float(rho.max())
    hc = np.nonzero((lab == L_HOT) | (lab == L_COLD))[0]
    caps = np.nonzero((lab == L_CAP0) | (lab == L_CAPL))[0]
    chn = np.nonzero(lab == L_CH)[0]
    mp = np.nonzero((lab == L_RING) | (lab == L_PORT))[0]
    allc = np.nonzero(lab >= L_CH)[0]
    trees = {'hc': cKDTree(cen[hc]) if len(hc) else None, 'caps': cKDTree(cen[caps]) if len(caps) else None}
    rim_pts, rim_sp = _rim_points(g, 0.05 * thr['manifold']['effective_threshold_mm'])
    rim_tree = cKDTree(rim_pts)
    rim_zone = thr['manifold']['effective_threshold_mm'] + ctx.tau + rim_sp
    classes = {}

    def run(name, ii, jj, key, rim=None):
        t = thr[key]['effective_threshold_mm']
        st = {'evaluated': 0, 'exhausted': False, 'violations': [], 'marginal': [], 'too_deep': []}
        _run_pairs(L, ii, jj, t, leaf_tol, rim, st)
        c = classes.setdefault(name, {'candidate_pairs': 0, 'pairs_evaluated': 0, 'violations': [],
                                      'marginal': [], 'too_deep': 0, 'exhausted': False,
                                      'threshold_mm': t, 'required_mm': thr[key]['required_mm']})
        c['candidate_pairs'] += int(len(ii))
        c['pairs_evaluated'] += st['evaluated']
        c['exhausted'] |= st['exhausted']
        c['violations'] += st['violations']
        c['marginal'] += st['marginal']
        c['too_deep'] += sum(len(x[0]) for x in st['too_deep'])

    def reach(key):
        return thr[key]['effective_threshold_mm'] + 2 * rmax

    for name, qi, tr, tidx, key, rim in (
            ('hot_cold_wall_vs_channels', chn, trees['hc'], hc, 'wall', None),
            ('hot_cold_wall_vs_manifold_and_ports', mp, trees['hc'], hc, 'manifold', (rim_tree, rim_zone)),
            ('end_regions', allc, trees['caps'], caps, 'end', None)):
        ii, jj = _candidates(cen, qi, tr, tidx, reach(key))
        run(name, ii, jj, key, rim)
    if len(chn):
        cidx = _channel_index(g, cen, L['nrm'], chn)
        groups = [chn[cidx == c] for c in range(g.n_ch)]
        gtrees = [cKDTree(cen[x]) if len(x) else None for x in groups]
        for c in range(g.n_ch):
            nb = (c + 1) % g.n_ch
            ii, jj = _candidates(cen, groups[c], gtrees[nb], groups[nb], reach('rib'))
            run('ribs_between_channels', ii, jj, 'rib')
    out_cls, nviol = {}, 0
    for name, c in classes.items():
        v = c.pop('violations')
        mg = c.pop('marginal')
        if v:
            dist = np.concatenate([x[0] for x in v])
            mid = np.concatenate([x[1] for x in v])
            la = np.concatenate([x[2] for x in v])
            lb = np.concatenate([x[3] for x in v])
            o = _order_rows(mid, np.round(dist, 4))[:WP_REPORT_LOCATIONS]
            c['violation_count'] = int(len(dist))
            c['violations'] = [{'at_mm': _loc(mid[i]), 'distance_mm': round(float(dist[i]), 4),
                                'surfaces': [LABEL_NAMES[int(la[i])], LABEL_NAMES[int(lb[i])]]} for i in o]
            c['min_distance_found_mm'] = round(float(dist.min()), 4)
        else:
            c['violation_count'] = 0
            c['violations'] = []
        if mg:
            mid = np.concatenate([x[0] for x in mg])
            c['marginal_count'] = int(len(mid))
            o = _order_rows(mid)[:WP_REPORT_LOCATIONS]
            c['marginal_locations'] = [_loc(mid[i]) for i in o]
        else:
            c['marginal_count'] = 0
            c['marginal_locations'] = []
        c['state'] = ('fail' if (c['violation_count'] or c['marginal_count'] or c['too_deep'] or c['exhausted'])
                      else 'pass')
        nviol += c['violation_count']
        out_cls[name] = c
    state = 'pass' if all(c['state'] == 'pass' for c in out_cls.values()) else 'fail'
    return {'state': state, 'classes': out_cls, 'thresholds': thr, 'leaf_tolerance_mm': leaf_tol,
            'patch_radius_max_mm': rmax, 'facing_cosine_margin': WP_FACING_MARGIN,
            'port_rim_exemption_zone_mm': rim_zone, 'violations': nviol,
            'numerical_error_mm': leaf_tol,
            'method': '3D branch-and-bound over triangle pairs: pairs with sphere-bound distance >= threshold are '
                      'proven; the rest are split until the bound resolves to violation (surface points closer than '
                      'the threshold, facing across material), non-facing contact, or unresolved (rejected)'}


def whole_part_checks(spec: RegenChamberSpec, verts: np.ndarray, faces: np.ndarray):
    """Returns (checks, failures, measurements). Any failure to resolve the geometry is a failure."""
    names = ('circuit_fidelity', 'circuit_coverage', 'wall_thickness')
    try:
        ctx = _Ctx(spec)
        L = _build_leaves(ctx, verts, faces)
        fid = _fidelity(ctx, L)
        cov = _coverage(ctx, L)
        thk = _thickness(ctx, L)
    except _Exhausted as e:
        msg = f'unresolved: refinement limit exhausted ({e})'
        return ({n: {'state': 'fail', 'detail': msg} for n in names}, [f'{n}: {msg}' for n in names], {})
    checks = {'circuit_fidelity': fid, 'circuit_coverage': cov, 'wall_thickness': thk}
    failures = []
    if fid['state'] != 'pass':
        failures.append(f'circuit_fidelity: {fid["nonconforming_patches"]} mesh patch(es) not on the specified geometry '
                        f'({fid["by_reason"]}); first at {[loc["at_mm"] for loc in fid["locations"][:3]]}; '
                        f'{fid["unsplit_over_radius_target"]} patch(es) could not be refined')
    if cov['state'] != 'pass':
        failures.append(f'circuit_coverage: {cov["failed_samples"]} specified centerline sample(s) not an open void, '
                        f'{cov["void_components"]} void component(s); first at {[loc["at_mm"] for loc in cov["locations"][:3]]}')
    if thk['state'] != 'pass':
        for name, c in thk['classes'].items():
            if c['state'] == 'pass':
                continue
            where = [v['at_mm'] for v in c['violations'][:3]] or c['marginal_locations'][:3]
            failures.append(f'wall_thickness[{name}]: {c["violation_count"]} violation(s) below {c["threshold_mm"]:.3f} mm '
                            f'(min found {c.get("min_distance_found_mm")}), {c["marginal_count"]} marginal, '
                            f'{c["too_deep"]} unresolved, exhausted={c["exhausted"]}; at {where}')
    meas = {'patch_stats': L['stats'], 'surface_tolerance_mm': ctx.tau,
            'reference_capsules': int(ctx.g.segment_count)}
    return checks, failures, meas


# --------------------------------------------------------------------------- full verification
def verify(spec: RegenChamberSpec, tris: np.ndarray, *, raw_stl_sha256: str = '', runtime_so_sha256: str = '',
           builder_stats: dict | None = None):
    """Returns (report, cleaned_tris). report['passed'] is True only if every check passed."""
    r = clean_and_check(tris)
    checks = dict(r.checks)
    failures = list(r.failures)
    measurements: dict = {'input_triangles': r.input_triangles, 'cleaned_triangles': int(len(r.faces)),
                          'dropped_degenerate': r.dropped_degenerate,
                          'removed_specks': r.removed, 'signed_volume_mm3': r.volume,
                          'wall_measurement_note': 'in-plane (z-plane) distances, not surface-normal'}
    if builder_stats:
        measurements['builder'] = builder_stats
    cleaned = np.zeros((0, 3, 3), dtype=np.float32)
    if len(r.faces):
        cleaned = r.verts[r.faces]
        used = np.unique(r.faces)
        pts = r.verts[used]
        bb = check_bbox(pts.min(axis=0), pts.max(axis=0), spec)
        vol = check_volume(r.volume, spec)
        checks['bbox'], checks['volume'] = bb, vol
        for name, c in (('bbox', bb), ('volume', vol)):
            if c['state'] != 'pass':
                failures.append(f'{name} check failed: ' + ', '.join(f'{k}={v}' for k, v in c.items() if k in ('max_error_mm', 'relative_error', 'tolerance_mm', 'tolerance')))
        stations = []
        cats = {'channel_count': [], 'hot_wall_mm': [], 'cold_wall_mm': [], 'rib_mm': [], 'slice': []}
        for z in station_zs(spec):
            m = slice_station(r.verts, r.faces, z, spec.channel_count)
            stations.append(m)
            for k, msg in _station_issues(m, spec.channel_count, spec.min_feature_mm, spec.voxel_size_mm).items():
                cats[k].append(msg)
        measurements['stations'] = stations
        measurements['min_feature_mm'] = spec.min_feature_mm
        for name, key, label in (('channel_count', 'channel_count', 'channel count'), ('hot_wall', 'hot_wall_mm', 'hot wall'),
                                 ('cold_wall', 'cold_wall_mm', 'cold wall'), ('rib', 'rib_mm', 'rib')):
            msgs = cats[key] + (cats['slice'] if key == 'channel_count' else [])
            vals = [s[key] for s in stations if key in s] if key != 'channel_count' else [s['channel_loops'] for s in stations]
            checks[name] = {'state': 'pass' if not msgs else 'fail', 'per_station': vals,
                            'minimum': (min(vals) if vals else None),
                            'threshold_mm': (None if key == 'channel_count' else spec.min_feature_mm - spec.voxel_size_mm)}
            failures.extend(msgs)
        wp_checks, wp_failures, wp_meas = whole_part_checks(spec, r.verts, r.faces)
        checks.update(wp_checks)
        failures.extend(wp_failures)
        measurements['whole_part'] = wp_meas
    else:
        for name in ('bbox', 'volume', 'channel_count', 'hot_wall', 'cold_wall', 'rib',
                     'circuit_fidelity', 'circuit_coverage', 'wall_thickness'):
            checks[name] = {'state': 'fail', 'detail': 'no mesh'}
    passed = not failures and all(c['state'] == 'pass' for c in checks.values())
    report = {
        'family': spec.family,
        'spec_sha256': specification_digest(spec),
        'stl_sha256': hashlib.sha256(stl_bytes(cleaned)).hexdigest(),
        'raw_stl_sha256': raw_stl_sha256,
        'runtime_so_sha256': runtime_so_sha256,
        'measurements': measurements,
        'checks': checks,
        'failures': failures,
        'passed': bool(passed),
        'design_intent': {'state': 'geometry_matches_spec' if passed else 'geometry_rejected'},
        'physics': {'state': 'not_evaluated'},
        'standards': {'state': 'not_evaluated'},
        'engineering_certified': False,
        'step_export': 'not_available',
    }
    return report, cleaned
