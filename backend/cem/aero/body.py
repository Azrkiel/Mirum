"""Independent explicit slender-body increments; no primary _aero calls.

Flat-plate friction with Hoerner fineness form factor, base drag and cylinder
crossflow; Munk's closed-body potential pitching couple. This is a rigid,
attached-flow engineering model, not measured interference calibration.
"""
import math

import numpy as np

from .contracts import BodyIncrement

LIMITS = ('Slender circular sections only; no slab/cavity/exposed hardware drag.',
          'No wing-body junction flow, shielding, separation or flexible-body model.',
          'Body drag bound is an engineering allowance, not a flight confidence interval.')


def increments(geometry, op):
    stations, ref = geometry.body, geometry.reference
    x = np.array([s.x_m for s in stations])
    r = np.array([s.width_m/2 for s in stations])
    length = float(x[-1]-x[0]); diameter = float(2*max(r)); fineness=length/diameter
    re = op.density_kg_m3*op.velocity_m_s*length/op.viscosity_pa_s
    if (not 6 <= fineness <= 30 or not 100000 <= re <= 3000000 or abs(op.alpha_deg)>8
            or any(abs(s.width_m-s.height_m)>1e-8 or s.exponent!=2 for s in stations)):
        raise ValueError('Body outside slender circular Re/angle/fineness applicability')
    a = math.radians(op.alpha_deg); q=.5*op.density_kg_m3*op.velocity_m_s**2
    dx, dr = np.diff(x), np.diff(r)
    area = math.pi*(r[1:]+r[:-1])*np.hypot(dx,dr)
    volume = float(np.sum(math.pi*dx/3*(r[:-1]**2+r[:-1]*r[1:]+r[1:]**2)))
    cf_lam = 1.328/math.sqrt(re); cf_turb=.455/math.log10(re)**2.58
    factor=1+60/fineness**3+fineness/400
    friction=q*max(cf_lam,cf_turb)*factor*float(sum(area))
    base=q*.12*math.pi*r[-1]**2
    wind=np.array([math.cos(a),0.,math.sin(a)])
    cross=q*1.2*(r[:-1]+r[1:])*dx*math.sin(a)*abs(math.sin(a))
    force=(friction+base)*wind+np.array([0.,0.,float(sum(cross))])
    # Positive My is nose-up: M_y = r_z*Fx - r_x*Fz.
    # Exact centroid of a linearly varying radius. Subdividing a conical
    # segment must not change its force or first moment.
    centers=.5*(x[1:]+x[:-1])+dx*(r[1:]-r[:-1])/(6*(r[1:]+r[:-1]))
    friction_segments=q*max(cf_lam,cf_turb)*factor*area
    moment=float(np.sum(-(centers-ref.cg_m[0])*cross))
    moment+=float(np.sum(-ref.cg_m[2]*friction_segments*wind[0]
                         -(centers-ref.cg_m[0])*friction_segments*wind[2]))
    moment+=-ref.cg_m[2]*base*wind[0]-(x[-1]-ref.cg_m[0])*base*wind[2]
    moment+=q*2*volume*math.sin(a)*math.cos(a)
    drag=float(force@wind)
    error=q*abs(cf_turb-cf_lam)*factor*float(sum(area))+.3*(friction+base)+abs(float(sum(cross)))
    return BodyIncrement(binding=geometry.binding,operating_point=op,force_n=tuple(force),
        moment_cg_nm=(0.,moment,0.),drag_n=drag,drag_error_bound_n=error,limitations=LIMITS)
