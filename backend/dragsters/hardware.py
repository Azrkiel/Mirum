"""Hardware checks: wheel envelopes, stock containment, body mass.

Signatures are the D6 contract. ``wheel_envelopes`` and ``summary`` stay pure (the frontend
imports them); CadQuery/OCP may only be imported inside ``cad_checks``. Wheels are display/check
envelopes only and never part of the STEP.
"""
from __future__ import annotations

from .profiles import max_half_width_over
from .tolerances import (
    LOFT_BOUNDS_TOL_MM, VOID_TOLERANCE_MM3, WALL_DISTANCE_TOL_MM, WEDGE_BOUNDS_TOL_MM,
)

STOCK_TOLERANCE_MM = 0.01
FACE_MARGIN_MM = 1.0
OVERLAP_DISTANCE_MM = 1e-6   # distances above this are disjoint
NO_DENSITY_REASON = 'no material density supplied'


def _axles(spec) -> list[tuple[str, float, float]]:
    """``(axle, x, diameter)`` front then rear."""
    return [('front', spec.front_axle_x_mm, spec.wheels.front_diameter_mm),
            ('rear', spec.rear_axle_x_mm, spec.wheels.rear_diameter_mm)]


def _centre_offset(spec, x: float, diameter: float) -> float:
    """``y_c = a_env + side_clearance + width/2`` with the wheel x-interval clipped to ``[0, L]``."""
    x0, x1 = max(0.0, x - diameter / 2), min(spec.length_mm, x + diameter / 2)
    a_env = max_half_width_over(spec, x0, x1)
    return a_env + spec.wheels.side_clearance_mm + spec.wheels.width_mm / 2


def wheel_envelopes(spec) -> list[dict]:
    """Four wheel envelopes (front-left, front-right, rear-left, rear-right); left is -Y."""
    out = []
    for axle, x, d in _axles(spec):
        y_c = _centre_offset(spec, x, d)
        for side, sign in (('left', -1.0), ('right', 1.0)):
            out.append({'axle': axle, 'side': side, 'center_mm': [x, sign * y_c, d / 2],
                        'diameter_mm': d, 'width_mm': spec.wheels.width_mm, 'axis': [0, 1, 0]})
    return out


class _FaceIndex:
    """Exact body-to-wheel distance from only the faces near the wheel (speed on ~800-face lofts).

    The nearest body face to a wheel lies within the reported distance, so a face whose bounding
    box misses the wheel's box inflated by ``reach`` is at least ``reach`` away. If the nearest
    candidate is within ``reach`` the answer is exact; otherwise the true distance is >= ``reach``
    (> the required clearance) and the candidate distance is returned as an upper bound.
    With no candidate the full body is queried.
    """

    def __init__(self, body, reach: float):
        self.body, self.reach = body, reach
        self.faces = [(f, f.BoundingBox()) for f in body.Faces()]

    def distance(self, wheel, dist_cls) -> float:
        wb, r = wheel.BoundingBox(), self.reach
        picked = [f.wrapped for f, b in self.faces
                  if b.xmax >= wb.xmin - r and b.xmin <= wb.xmax + r
                  and b.ymax >= wb.ymin - r and b.ymin <= wb.ymax + r
                  and b.zmax >= wb.zmin - r and b.zmin <= wb.zmax + r]
        if picked:
            import cadquery as cq
            target = cq.Compound.makeCompound([cq.Shape.cast(f) for f in picked]).wrapped
        else:
            target = self.body.wrapped
        dist = dist_cls(target, wheel.wrapped)
        dist.Perform()
        if not dist.IsDone():
            raise ValueError('wheel distance query failed')
        return float(dist.Value())


def _bounds_tol(spec) -> float:
    return WEDGE_BOUNDS_TOL_MM if spec.style == 'wedge' else LOFT_BOUNDS_TOL_MM


def cad_checks(body, spec) -> dict[str, dict]:
    import cadquery as cq
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    checks: dict[str, dict] = {}
    required = spec.wheels.side_clearance_mm - WALL_DISTANCE_TOL_MM
    envs = wheel_envelopes(spec)
    near = _FaceIndex(body, required + FACE_MARGIN_MM)
    for axle in ('front', 'rear'):
        distances, volumes = {}, {}
        for env in (e for e in envs if e['axle'] == axle):
            x, y, z = env['center_mm']
            wheel = cq.Solid.makeCylinder(env['diameter_mm'] / 2, env['width_mm'],
                                          cq.Vector(x, y - env['width_mm'] / 2, z), cq.Vector(0, 1, 0))
            distances[env['side']] = near.distance(wheel, BRepExtrema_DistShapeShape)
            # Positive distance means disjoint shapes (intersection volume exactly 0); the slow
            # boolean runs only when the shapes touch or overlap.
            volumes[env['side']] = 0.0 if distances[env['side']] > OVERLAP_DISTANCE_MM                 else float(body.intersect(wheel).Volume())
        passed = (all(d >= required for d in distances.values())
                  and all(v <= VOID_TOLERANCE_MM3 for v in volumes.values()))
        checks[f'wheel_clearance_{axle}'] = {
            'passed': bool(passed), 'measured_distance_mm': distances, 'required_distance_mm': required,
            'intersection_volume_mm3': volumes, 'tolerance_mm3': VOID_TOLERANCE_MM3,
            'side_clearance_mm': spec.wheels.side_clearance_mm}

    bb = body.BoundingBox()
    tol = _bounds_tol(spec)
    g = spec.ground_clearance_mm
    checks['body_above_ground'] = {
        'passed': bool(bb.zmin >= g - tol and bb.zmin > 0), 'zmin_mm': bb.zmin,
        'ground_clearance_mm': g, 'tolerance_mm': tol}

    if spec.stock is not None:
        margin = _stock_margin(spec, {'xmin': bb.xmin, 'xmax': bb.xmax, 'ymin': bb.ymin, 'ymax': bb.ymax,
                                      'zmin': bb.zmin, 'zmax': bb.zmax})
        checks['stock_containment'] = {
            'passed': all(m >= -STOCK_TOLERANCE_MM for m in margin.values()), 'margin_mm': margin,
            'tolerance_mm': STOCK_TOLERANCE_MM,
            'extent_mm': {'length': bb.xmax - bb.xmin, 'width': bb.ymax - bb.ymin,
                          'height': bb.zmax - bb.zmin}}
    return checks


def _stock_margin(spec, bounds) -> dict[str, float]:
    s = spec.stock
    return {'length': s.length_mm - (bounds['xmax'] - bounds['xmin']),
            'width': s.width_mm - (bounds['ymax'] - bounds['ymin']),
            'height': s.height_mm - (bounds['zmax'] - bounds['zmin'])}


def summary(spec, volume_mm3, bounds) -> dict:
    envs = wheel_envelopes(spec)
    track = {e['axle']: 2 * abs(e['center_mm'][1]) for e in envs}
    hardware = {
        'track_width_front_mm': track['front'], 'track_width_rear_mm': track['rear'],
        'wheelbase_mm': spec.rear_axle_x_mm - spec.front_axle_x_mm,
        'wheel_envelopes': envs,
        'stock_margin_mm': _stock_margin(spec, bounds) if spec.stock is not None else None,
    }
    material = spec.material
    if material is None:
        mass = {'value_g': None, 'basis': 'body_only', 'density_g_cm3': None, 'material': None,
                'reason': NO_DENSITY_REASON}
    else:
        mass = {'value_g': round(volume_mm3 * material.density_g_cm3 / 1000, 2), 'basis': 'body_only',
                'density_g_cm3': material.density_g_cm3, 'material': material.name, 'reason': None}
    return {'hardware': hardware, 'mass': mass}
