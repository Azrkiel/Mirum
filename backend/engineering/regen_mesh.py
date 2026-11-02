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
    else:
        for name in ('bbox', 'volume', 'channel_count', 'hot_wall', 'cold_wall', 'rib'):
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
