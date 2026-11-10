"""Verification v2: style-agnostic checks of a reimported STEP against a v2 spec.

Measures the file only. It may use ``spec_v2``, ``profiles`` (the mathematical definition of each
body), ``tolerances``, ``hardware`` and the style *registry* (for ``generator_version``); it never
imports ``kernel``, ``geometry`` or any style builder (master plan invariant 5; AST-tested).
Fails closed: a check that raises is a failed check, and any failure raises
``ValueError('STEP checks failed: <ids>')`` exactly like v1.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.TopAbs import TopAbs_REVERSED

from . import hardware, profiles
from .spec_v2 import canonical_spec_v2, parse_v2, specification_digest_v2
from .styles import REGISTRY
from .tolerances import (LOFT_BOUNDS_TOL_MM, LOFT_VOLUME_REL_TOL, VOID_TOLERANCE_MM3,
                         WALL_DISTANCE_TOL_MM, WEDGE_BOUNDS_TOL_MM)

CENTROID_Y_TOL_MM = 0.01
PROBE_MARGIN_MM = 0.05
AXIS_TOL_MM = 1e-3
RADIUS_TOL_MM = 1e-4
PLANE_TOL_MM = 1e-4
PARALLEL_TOL = 1e-9
SAMPLE_STEP_MM = 0.25

WALL_METHOD_LIMITS = ('min distance from feature axis to non-feature faces; sampled nowhere; '
                      'excludes rear face; does not certify whole-body thickness away from the '
                      'pocket and bores')
BORE_METHOD_LIMITS = WALL_METHOD_LIMITS + ' (bore walls: the rear face is one of the measured faces)'

REPORT_METHOD_LIMITS = {
    'absolute_bounds': 'Hmax and Ymax come from the profile law sampled every 0.25 mm and at the '
                       'control stations (analytic for the wedge).',
    'reference_volume': 'Bore volume uses 2*a(x_axle) as the bored length; the loft tolerance '
                        '(tolerances.LOFT_VOLUME_REL_TOL) is far larger than a single bore, so a '
                        'missing bore on a lofted body is caught by the axle void probe, not by volume.',
    'pocket_wall': WALL_METHOD_LIMITS,
    'front_bore_wall': BORE_METHOD_LIMITS,
    'rear_bore_wall': BORE_METHOD_LIMITS,
}


# ---------------------------------------------------------------------------- face helpers

def _vec(p):
    return (p.X(), p.Y(), p.Z())


def face_infos(shape) -> list[dict]:
    """Classify every face: kind, cylinder radius/axis, plane normal/position, vertices."""
    infos = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped, True)
        kind = surface.GetType()
        info = {'face': face, 'kind': 'other'}
        if kind == GeomAbs_Cylinder:
            cyl = surface.Cylinder()
            info.update(kind='cylinder', radius=cyl.Radius(), loc=_vec(cyl.Axis().Location()),
                        dir=_vec(cyl.Axis().Direction()))
        elif kind == GeomAbs_Plane:
            plane = surface.Plane()
            nx, ny, nz = _vec(plane.Axis().Direction())
            if face.wrapped.Orientation() == TopAbs_REVERSED:
                nx, ny, nz = -nx, -ny, -nz
            info.update(kind='plane', normal=(nx, ny, nz), plane_x=plane.Location().X(), vertices=None)
        infos.append(info)
    return infos


def _plane_vertices(info):
    if info['vertices'] is None:
        info['vertices'] = [v.toTuple() for v in info['face'].Vertices()]
    return info['vertices']


def _plane_center(info):
    c = info['face'].Center()
    return c.y, c.z


def _is_cyl(info, radius, axis_index):
    return (info['kind'] == 'cylinder' and abs(info['radius'] - radius) <= RADIUS_TOL_MM
            and abs(info['dir'][axis_index]) >= 1 - PARALLEL_TOL)


def is_pocket_face(info, *, L, D, rp, zp, y=0.0, any_depth=False) -> bool:
    """Pocket cylinder (axis ∥ X through (·, y, zp)) or its bottom disk at x = L - D."""
    if _is_cyl(info, rp, 0):
        _, ly, lz = info['loc']
        return math.hypot(ly - y, lz - zp) <= AXIS_TOL_MM
    if info['kind'] == 'plane' and abs(info['normal'][0]) >= 1 - PARALLEL_TOL:
        if not any_depth and abs(info['plane_x'] - (L - D)) > PLANE_TOL_MM:
            return False
        # A disk bottom: vertices on the pocket circle (+-1e-3) and the face centre on the axis.
        cy, cz = _plane_center(info)
        radii = [math.hypot(vy - y, vz - zp) for _, vy, vz in _plane_vertices(info)]
        return (math.hypot(cy - y, cz - zp) <= AXIS_TOL_MM and bool(radii)
                and max(radii) <= rp + 1e-3 and max(radii) >= rp - 1e-3)
    return False


def is_bore_face(info, *, x, z, ra) -> bool:
    """Bore cylinder (axis ∥ Y through (x, ·, z))."""
    if not _is_cyl(info, ra, 1):
        return False
    lx, _, lz = info['loc']
    return math.hypot(lx - x, lz - z) <= AXIS_TOL_MM


def is_rear_face(info, *, L) -> bool:
    """Planar face with outward normal +X at x = L."""
    return (info['kind'] == 'plane' and info['normal'][0] >= 1 - PARALLEL_TOL
            and abs(info['plane_x'] - L) <= PLANE_TOL_MM)


def _min_distance(edge: cq.Edge, faces: list[dict]) -> float:
    if not faces:
        raise ValueError('no faces to measure against')
    compound = cq.Compound.makeCompound([f['face'] for f in faces])
    dss = BRepExtrema_DistShapeShape(edge.wrapped, compound.wrapped)
    if not dss.IsDone():
        raise ValueError('distance query failed')
    value = dss.Value()
    if not math.isfinite(value):
        raise ValueError('distance query returned a non-finite value')
    return value


def _segment(p, q) -> cq.Edge:
    return cq.Edge.makeLine(cq.Vector(*p), cq.Vector(*q))


# ---------------------------------------------------------------------------- spec helpers

def _xs(spec):
    xs = profiles.sample_xs(0.0, spec.length_mm, SAMPLE_STEP_MM)
    if spec.style != 'wedge':
        xs += [st.x for st in profiles.stations_for(spec)]
    return xs


def _outer_volume(spec) -> float:
    if spec.style == 'wedge':
        L, W, H, N = spec.length_mm, spec.width_mm, spec.height_mm, spec.nose_height_mm
        return W * L * (N + 3 * H) / 4
    return profiles.law_volume(profiles.stations_for(spec), spec.crown_exponent)


def _a_min(spec, x0, x1):
    return profiles.min_half_width_over(spec, max(0.0, x0), min(spec.length_mm, x1))


def _bore_axes(spec):
    return (('front', spec.front_axle_x_mm, spec.front_axle_z_mm),
            ('rear', spec.rear_axle_x_mm, spec.rear_axle_z_mm))


# ---------------------------------------------------------------------------- checks

def _bounds_check(spec, bounds):
    xs = _xs(spec)
    ymax = max(profiles.half_width_at(spec, x) for x in xs)
    hmax = max(profiles.top_at(spec, x, 0.0) for x in xs)
    g, L = spec.ground_clearance_mm, spec.length_mm
    expected = dict(zip(bounds, (0.0, L, -ymax, ymax, g, g + hmax)))
    tol = WEDGE_BOUNDS_TOL_MM if spec.style == 'wedge' else LOFT_BOUNDS_TOL_MM
    deviation = {k: bounds[k] - expected[k] for k in bounds}
    passed = all(math.isfinite(bounds[k]) and abs(deviation[k]) <= tol for k in bounds)
    return passed, dict(measured=dict(bounds), expected=expected, deviation_mm=deviation, threshold=tol,
                        tolerance_mm=tol)


def _symmetry_check(spec, bounds, body):
    tol = WEDGE_BOUNDS_TOL_MM if spec.style == 'wedge' else LOFT_BOUNDS_TOL_MM
    centroid_y = body.Center().y
    offset = bounds['ymin'] + bounds['ymax']
    passed = (math.isfinite(offset) and math.isfinite(centroid_y)
              and abs(offset) <= tol and abs(centroid_y) <= CENTROID_Y_TOL_MM)
    return passed, dict(measured={'ymin_plus_ymax_mm': offset, 'centroid_y_mm': centroid_y},
                        threshold={'ymin_plus_ymax_mm': tol, 'centroid_y_mm': CENTROID_Y_TOL_MM})


def _volume_check(spec, volume):
    ra, rp = spec.axle_bore_diameter_mm / 2, spec.pocket_diameter_mm / 2
    bores = sum(math.pi * ra ** 2 * 2 * profiles.half_width_at(spec, x)
                for _, x, _ in _bore_axes(spec))
    expected = _outer_volume(spec) - math.pi * rp ** 2 * spec.pocket_depth_mm - bores
    tol = (max(0.1, 1e-6 * expected) if spec.style == 'wedge' else LOFT_VOLUME_REL_TOL * expected)
    passed = math.isfinite(volume) and volume > 0 and abs(volume - expected) <= tol
    return passed, dict(measured=volume, expected_mm3=expected, deviation_mm3=volume - expected,
                        threshold=tol, tolerance_mm3=tol)


def _residual(body, radius, length, origin, axis):
    probe = cq.Solid.makeCylinder(radius, length, cq.Vector(*origin), cq.Vector(*axis))
    return body.intersect(probe).Volume()


def _void_check(residual, **evidence):
    passed = math.isfinite(residual) and abs(residual) <= VOID_TOLERANCE_MM3
    return passed, dict(measured=residual, threshold=VOID_TOLERANCE_MM3, residual_body_mm3=residual,
                        tolerance_mm3=VOID_TOLERANCE_MM3, **evidence)


def _pocket_void(spec, body):
    L, D, zp, rp = spec.length_mm, spec.pocket_depth_mm, spec.pocket_center_z_mm, spec.pocket_diameter_mm / 2
    origin = (L - D, 0.0, zp)
    return _void_check(_residual(body, rp, D, origin, (1, 0, 0)), origin_mm=list(origin),
                       axis=[1, 0, 0], radius_mm=rp, length_mm=D)


def _axle_void(spec, body, x, z):
    ra = spec.axle_bore_diameter_mm / 2
    ylim = _a_min(spec, x - ra, x + ra) - PROBE_MARGIN_MM
    if not ylim > 0:
        raise ValueError('axle probe has no span inside the body')
    origin = (x, -ylim, z)
    return _void_check(_residual(body, ra, 2 * ylim, origin, (0, 1, 0)), origin_mm=list(origin),
                       axis=[0, 1, 0], radius_mm=ra, length_mm=2 * ylim)


def _pocket_wall(spec, infos):
    L, D, zp = spec.length_mm, spec.pocket_depth_mm, spec.pocket_center_z_mm
    rp, t = spec.pocket_diameter_mm / 2, spec.min_wall_mm
    axes = {(0.0, zp): False}      # (y, z) -> is a stray (not the specified) axis
    for info in infos:
        if _is_cyl(info, rp, 0):
            key = (round(info['loc'][1], 3), round(info['loc'][2], 3))
            if math.hypot(key[0], key[1] - zp) > AXIS_TOL_MM:
                axes[key] = True
    measured = []
    for (y, z), stray in axes.items():
        obstacles = [i for i in infos
                     if not is_pocket_face(i, L=L, D=D, rp=rp, zp=z, y=y, any_depth=stray)
                     and not is_rear_face(i, L=L)]
        dist = _min_distance(_segment((L - D, y, z), (L, y, z)), obstacles)
        measured.append({'axis_y_mm': y, 'axis_z_mm': z, 'stray_axis': stray, 'wall_mm': dist - rp})
    return _wall_result(measured, t)


def _bore_wall(spec, infos, which):
    t, ra = spec.min_wall_mm, spec.axle_bore_diameter_mm / 2
    axes = {name: (x, z) for name, x, z in _bore_axes(spec)}
    x0, z0 = axes[which]
    candidates = {(x0, z0): False}
    for info in infos:
        if _is_cyl(info, ra, 1):
            key = (round(info['loc'][0], 3), round(info['loc'][2], 3))
            at_spec = any(math.hypot(key[0] - x, key[1] - z) <= AXIS_TOL_MM for x, z in axes.values())
            nearest = min(axes, key=lambda n: abs(axes[n][0] - key[0]))
            if not at_spec and nearest == which:
                candidates[key] = True
    measured = []
    for (x, z), stray in candidates.items():
        y_in = _a_min(spec, x - (ra + t), x + (ra + t)) - ra - t
        if not y_in > 0:
            raise ValueError('bore axis has no inner span')
        obstacles = [i for i in infos if not is_bore_face(i, x=x, z=z, ra=ra)]
        dist = _min_distance(_segment((x, -y_in, z), (x, y_in, z)), obstacles)
        measured.append({'axis_x_mm': x, 'axis_z_mm': z, 'stray_axis': stray, 'axis_half_span_mm': y_in,
                         'wall_mm': dist - ra})
    return _wall_result(measured, t, bore=True)


def _wall_result(measured, t, bore=False):
    worst = min(m['wall_mm'] for m in measured)
    threshold = t - WALL_DISTANCE_TOL_MM
    limits = BORE_METHOD_LIMITS if bore else WALL_METHOD_LIMITS
    return (math.isfinite(worst) and worst >= threshold,
            dict(measured={'min_wall_mm': worst, 'axes': measured}, threshold=threshold,
                 min_wall_mm=t, tolerance_mm=WALL_DISTANCE_TOL_MM, method_limits=limits))


# ---------------------------------------------------------------------------- entry point

def verify_step_v2(path: Path, spec, *, provenance: dict | None = None) -> dict:
    path = Path(path)
    try:
        step_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        imported = cq.importers.importStep(str(path))
        shape = imported.val()
        solids = shape.Solids()
    except Exception as exc:
        raise ValueError(f'STEP reimport failed: {exc}') from exc
    checks: dict[str, dict] = {}

    def record(name, fn):
        try:
            passed, evidence = fn()
            checks[name] = {'passed': bool(passed), **evidence}
        except Exception as exc:   # fail closed: an unavailable check is a failed check
            checks[name] = {'passed': False, 'error': f'{type(exc).__name__}: {exc}'}

    valid = len(solids) == 1 and bool(shape.isValid())
    checks['single_valid_solid'] = {'passed': valid, 'solid_count': len(solids)}
    if not valid:
        raise ValueError('STEP checks failed: single_valid_solid')
    body = solids[0]
    b = body.BoundingBox()
    bounds = {key: getattr(b, key) for key in ('xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax')}
    volume = body.Volume()

    record('absolute_bounds', lambda: _bounds_check(spec, bounds))
    record('symmetry', lambda: _symmetry_check(spec, bounds, body))
    record('reference_volume', lambda: _volume_check(spec, volume))
    record('pocket_void', lambda: _pocket_void(spec, body))
    for name, x, z in _bore_axes(spec):
        record(f'{name}_axle_void', lambda x=x, z=z: _axle_void(spec, body, x, z))
    try:
        infos = face_infos(body)
    except Exception as exc:
        infos = None
        for name in ('pocket_wall', 'front_bore_wall', 'rear_bore_wall'):
            checks[name] = {'passed': False, 'error': f'face classification failed: {exc}'}
    if infos is not None:
        record('pocket_wall', lambda: _pocket_wall(spec, infos))
        record('front_bore_wall', lambda: _bore_wall(spec, infos, 'front'))
        record('rear_bore_wall', lambda: _bore_wall(spec, infos, 'rear'))

    def revalidate():
        parse_v2(canonical_spec_v2(spec))
        return True, {'scope': 'canonical v2 spec re-parsed with the strict model'}
    record('spec_revalidation', revalidate)

    try:
        extra = hardware.cad_checks(body, spec)
        if not isinstance(extra, dict):
            raise TypeError('cad_checks must return a dict')
        for name, result in extra.items():
            if name in checks:
                raise ValueError(f'hardware check id {name!r} shadows a generic check')
            if not isinstance(result, dict) or type(result.get('passed')) is not bool:
                checks[name] = {'passed': False, 'error': 'malformed hardware check result'}
            else:
                checks[name] = dict(result)
    except Exception as exc:
        checks['hardware_checks'] = {'passed': False, 'error': f'{type(exc).__name__}: {exc}'}

    summary = {}
    try:
        summary = hardware.summary(spec, volume, bounds)
        if not (isinstance(summary, dict) and isinstance(summary.get('hardware'), dict)
                and isinstance(summary.get('mass'), dict)):
            raise TypeError("hardware.summary must return {'hardware': {...}, 'mass': {...}}")
    except Exception as exc:
        checks['hardware_summary'] = {'passed': False, 'error': f'{type(exc).__name__}: {exc}'}

    failures = [name for name, result in checks.items() if not result['passed']]
    if failures:
        raise ValueError('STEP checks failed: ' + ', '.join(failures))
    report = {
        'schema_version': 2, 'style': spec.style, 'spec_sha256': specification_digest_v2(spec),
        'step_sha256': step_sha256, 'generator_version': REGISTRY[spec.style].generator_version,
        'cadquery_version': cq.__version__, 'solid_count': len(solids), 'bounds_mm': bounds,
        'volume_mm3': volume, 'checks': checks, 'hardware': summary['hardware'], 'mass': summary['mass'],
        'method_limits': dict(REPORT_METHOD_LIMITS), 'passed': True, 'engineering_certified': False,
    }
    if provenance is not None:
        report['provenance'] = dict(provenance)
    return report
