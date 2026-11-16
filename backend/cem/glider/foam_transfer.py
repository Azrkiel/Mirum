"""Measured bonded foam interfaces under a declared nominal traction model.

No inference of adhesive peel strength or core elasticity follows. Supported
checks can reject a load, but nominal passing results cannot qualify the path.
"""
import copy
import math

import numpy as np

from . import segmented_geometry as geo
from .attachment import _polygon

PRIORS = dict(foam_normal_pa=100e3, foam_shear_pa=80e3,
              adhesive_normal_pa=1e6, adhesive_shear_pa=8e6, safety_factor=2.)


def _contours(mesh, point):
    loops = geo.section_loops(mesh, point, np.array([0., 1., 0.]))
    if len(loops) != 2:
        raise ValueError('Missing material annulus or foam bore')
    return sorted(loops, key=lambda loop: _polygon(loop, [0, 2])[0])


def _support_match(a, b):
    # Both contours are convex regular polygons with possible collinear edge
    # subdivisions. Their support planes certify whole boundaries, not sampled
    # rays. All face-normal directions from BOTH polygons are checked.
    for source in (a, b):
        edge = np.diff(source[:, [0, 2]], axis=0)
        normals = np.column_stack([-edge[:, 1], edge[:, 0]])
        lengths = np.linalg.norm(normals, axis=1)
        if np.any(lengths < 1e-12):
            raise ValueError('Degenerate mating material contour')
        normals /= lengths[:, None]
        aa = np.max(a[:, [0, 2]]@normals.T, axis=0)
        bb = np.max(b[:, [0, 2]]@normals.T, axis=0)
        if np.max(np.abs(aa-bb)) > geo.TOL:
            raise ValueError('Unbonded gap or overlapping foam transfer interface')


def measure(assembly, meshes):
    interfaces = []
    for side in (-1, 1):
        for panel in range(4):
            name = f'foam_{side}_{panel}'
            foam = meshes[name]
            bounds = foam.bounds[:, 1]
            point = assembly['point'].copy(); point[1] = float(np.mean(bounds))
            bore = _contours(foam, point)[0]
            bond_name = f'wing_collar_bond_{side}' if panel == 0 else 'adhesive'
            inner, outer = _contours(meshes[bond_name], point)
            _support_match(bore, outer)
            # The inner adhesive surface must meet either the collar or rod.
            stock = meshes['clamp' if panel == 0 else 'spar']
            loops = geo.section_loops(stock, point, np.array([0., 1., 0.]))
            stock_outer = max(loops, key=lambda loop: _polygon(loop, [0, 2])[0])
            _support_match(inner, stock_outer)
            area, center = _polygon(bore, [0, 2])
            if np.linalg.norm(center-assembly['point'][[0, 2]]) > geo.TOL:
                raise ValueError('Foam transfer bore is off the installed spar')
            perimeter = float(np.linalg.norm(np.diff(bore[:, [0, 2]], axis=0), axis=1).sum())
            interfaces.append(dict(name=name, bond_name=bond_name, side=side,
                bounds_y_m=bounds.tolist(), perimeter_m=perimeter, bore_area_m2=area))
    return interfaces


def traction_resultants(contour, force_n_m, torque_nm_m):
    """Reference resultant of uniform Fz traction plus constant tangent torque.

    On a regular polygon, sum(length*apothem)=2A. A tangential traction T/(2A)
    therefore transfers exactly T per unit span, including the polygon geometry.
    """
    xy = np.asarray(contour, dtype=float)
    if (xy.ndim != 2 or xy.shape[1] != 2 or len(xy) < 4 or not np.isfinite(xy).all()
            or not np.allclose(xy[0], xy[-1], atol=1e-12, rtol=0)
            or not math.isfinite(force_n_m) or not math.isfinite(torque_nm_m)):
        raise ValueError('Invalid closed traction contour')
    a, b = xy[:-1], xy[1:]
    cross = a[:, 0]*b[:, 1]-b[:, 0]*a[:, 1]
    signed_area = float(np.sum(cross)/2)
    edge = b-a; lengths = np.linalg.norm(edge, axis=1)
    if abs(signed_area) < 1e-16 or np.any(lengths <= 0):
        raise ValueError('Degenerate traction contour')
    # Clockwise tangents in the X/Z projection give positive GLOBAL My.
    tangent = -np.sign(signed_area)*edge/lengths[:, None]
    traction = tangent*torque_nm_m/(2*abs(signed_area))
    traction[:, 1] += force_n_m/float(lengths.sum())
    f = traction*lengths[:, None]
    center = np.sum((a+b)*cross[:, None], axis=0)/(6*signed_area)
    middle = (a+b)/2-center
    return dict(fx_n_m=float(f[:, 0].sum()), fz_n_m=float(f[:, 1].sum()),
                my_nm_m=float(np.sum(middle[:, 1]*f[:, 0]-middle[:, 0]*f[:, 1])))


def analyze(left, right, interfaces):
    evidence, checks = [], []
    for interface in interfaces:
        case = left if interface['side'] < 0 else right
        lo, hi = sorted(abs(y) for y in interface['bounds_y_m'])
        ys = [lo, hi]+[s.y_m for s in case.stations if lo < s.y_m < hi]
        knots = [s.y_m for s in case.stations]
        force = float(np.max(np.abs(np.interp(ys, knots, [s.fz_n_m for s in case.stations]))))
        torque = float(np.max(np.abs(np.interp(ys, knots, [s.my_nm_m for s in case.stations]))))
        area, perimeter = interface['bore_area_m2'], interface['perimeter_m']
        if not math.isfinite(area) or not math.isfinite(perimeter) or area <= 0 or perimeter <= 0:
            raise ValueError('Missing measured wetted interface')
        normal = force/perimeter
        shear = force/perimeter+torque/(2*area)
        sf = PRIORS['safety_factor']
        passed = (normal <= min(PRIORS['foam_normal_pa'], PRIORS['adhesive_normal_pa'])/sf
                  and shear <= min(PRIORS['foam_shear_pa'], PRIORS['adhesive_shear_pa'])/sf)
        checks.append(bool(passed))
        evidence.append(dict(**interface, nominal_normal_pa=normal, nominal_shear_pa=shear,
                             supported_check_state='passed' if passed else 'failed'))
    if len(interfaces) != 8:
        raise ValueError('Missing foam transfer interfaces')
    return dict(state='incomplete' if all(checks) else 'failed',
        nominal_checks_state='passed' if all(checks) else 'failed', interfaces=evidence,
        model='uniform vector traction plus constant polygon-tangent torque per unit span',
        material_priors=copy.deepcopy(PRIORS), manufacturing_ready=False,
        unsupported=['end peel/concentration', 'foam/core elasticity and chordwise load spread',
                     'bond defects, fracture, process variability and fatigue'])
