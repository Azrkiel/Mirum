"""Coordinate-polar nonlinear lifting-line development model for Task C.

Both physical halves and every installed lifting member participate in the
circulation solve. No body, nonlinear aircraft stall, dynamics or admission is
inferred from this rigid attached-flow calculation.
"""
import hashlib
import math
import inspect
from importlib.metadata import version
from pathlib import Path

import numpy as np
from cem.aero.numerics import bounded_newton

from cem.aero.lifting import segment_velocity
from cem.core import digest, canonical_json
from . import reconstruct
from .installed import freeze, validate
from .coordinate_polars import coordinates_payload


def panels(installed, spanwise):
    if type(spanwise) is not int or spanwise not in (2,4,8,12):
        raise ValueError('Unsupported coordinate lifting-line resolution')
    plane=validate(installed)
    mesh=[]
    for wing in plane.wings:
        surface=wing.name.rsplit('_',2)[0]
        for a,b in zip(wing.xsecs,wing.xsecs[1:]):
            foil=digest(coordinates_payload(a.airfoil.coordinates))
            if foil!=digest(coordinates_payload(b.airfoil.coordinates)):
                raise ValueError('Spanwise airfoil blending is not qualified')
            for j in range(spanwise):
                endpoints=[]
                for t in (j/spanwise,(j+1)/spanwise):
                    chord=float((1-t)*a.chord+t*b.chord)
                    twist=math.radians(float((1-t)*a.twist+t*b.twist))
                    direction=np.array([math.cos(twist),0.,-math.sin(twist)])
                    le=(1-t)*np.array(a.xyz_le)+t*np.array(b.xyz_le)
                    endpoints.append((le+.25*chord*direction,chord,direction))
                (p0,c0,d0),(p1,c1,d1)=endpoints
                tangent=p1-p0
                chord_direction=(d0+d1)/np.linalg.norm(d0+d1)
                span=tangent-chord_direction*np.dot(tangent,chord_direction)
                width=float(np.linalg.norm(span))
                if width<1e-8 or abs(np.dot(tangent/np.linalg.norm(tangent),chord_direction))>.5:
                    raise ValueError('Degenerate or high-sweep coordinate lifting panel')
                span/=width
                mesh.append(dict(surface=surface,foil=foil,a=p0,b=p1,center=.5*(p0+p1),
                    chord=.5*(c0+c1),chord_direction=chord_direction,span=span,
                    normal=np.cross(chord_direction,span),area=width*.5*(c0+c1)))
    if len(mesh)>256:
        raise ValueError('Coordinate lifting mesh exceeds 256-panel budget')
    return mesh


def solve(record, controls, bank, *, speed_m_s=6., alpha_deg=4., altitude_m=0., spanwise=4,
          initial_circulation_m2_s=None):
    """Use actual station polars; reject every uncovered or post-peak query."""
    import aerosandbox as asb
    for value,lo,hi in ((speed_m_s,1,15),(alpha_deg,-10,12),(altitude_m,0,5000)):
        if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
            raise ValueError('Unsupported coupled aerodynamic operating point')
    aircraft=reconstruct(record)
    installed=freeze(record,controls)
    mesh=panels(installed,spanwise)
    air=asb.Atmosphere(altitude=altitude_m)
    rho=float(air.density()); mu=float(air.dynamic_viscosity())
    a=np.array([p['a'] for p in mesh]); b=np.array([p['b'] for p in mesh])
    centers=np.array([p['center'] for p in mesh]); spans=np.array([p['span'] for p in mesh])
    normals=np.array([p['normal'] for p in mesh]); chords=np.array([p['chord_direction'] for p in mesh])
    chord=np.array([p['chord'] for p in mesh]); area=np.array([p['area'] for p in mesh])
    alpha=math.radians(alpha_deg); wind=np.array([math.cos(alpha),0.,math.sin(alpha)])
    wake=wind*installed['payload']['reference']['span_m']*1000
    influence=(segment_velocity(centers,a+wake,a)+segment_velocity(centers,a,b)+
               segment_velocity(centers,b,b+wake))

    def local(gamma):
        induced=np.einsum('ijk,j->ik',influence,gamma)
        velocity=speed_m_s*wind+induced
        perpendicular=velocity-np.sum(velocity*spans,axis=1)[:,None]*spans
        speed=np.linalg.norm(perpendicular,axis=1)
        effective=np.degrees(np.arctan2(np.sum(perpendicular*normals,axis=1),
                                       np.sum(perpendicular*chords,axis=1)))
        reynolds=rho*speed*chord/mu
        coeff=np.array([bank.coefficients(p['foil'],float(re),float(al))
                        for p,re,al in zip(mesh,reynolds,effective)])
        return induced,perpendicular,speed,effective,reynolds,coeff

    def residual(gamma):
        _,_,speed,_,_,coeff=local(gamma)
        return (gamma-.5*speed*chord*coeff[:,0])/(speed_m_s*chord)

    initial=np.zeros(len(mesh)) if initial_circulation_m2_s is None else np.asarray(initial_circulation_m2_s)
    if initial.shape!=(len(mesh),) or initial.dtype.kind not in 'fi' or not np.isfinite(initial).all():
        raise ValueError('Invalid initial circulation for coupled mesh')
    fit=bounded_newton(residual,initial,tolerance=1e-9)
    gamma=fit['value']
    error=fit['residual_norm']
    induced,velocity,speed,effective,reynolds,coeff=local(gamma)
    lift=rho*gamma[:,None]*np.cross(velocity,b-a)
    profile=.5*rho*speed[:,None]*area[:,None]*coeff[:,1,None]*velocity
    force=lift+profile
    moments=(np.cross(centers-np.asarray(aircraft.mass_summary()['cg_m']),force)+
             .5*rho*speed[:,None]**2*area[:,None]*chord[:,None]*coeff[:,2,None]*spans)
    induced_drag=float(lift.sum(axis=0)@wind); profile_drag=float(profile.sum(axis=0)@wind)
    if induced_drag < -1e-7 or profile_drag<=0:
        raise ValueError('Nonphysical coupled lifting drag')
    names=sorted({p['surface'] for p in mesh})
    source_velocity={name:np.einsum('ijk,j->ik',influence,
        np.where([p['surface']==name for p in mesh],gamma,0.)) for name in names}
    if not np.allclose(sum(source_velocity.values()),induced,rtol=0,atol=1e-10):
        raise ValueError('Source-resolved induced velocity does not conserve total')
    strips=[]
    for i,p in enumerate(mesh):
        interactions={}
        for name in names:
            without=speed_m_s*wind+induced[i]-source_velocity[name][i]
            angle=math.degrees(math.atan2(float(without@normals[i]),float(without@chords[i])))
            interactions[name]=dict(induced_velocity_m_s=source_velocity[name][i].tolist(),
                effective_angle_increment_deg=float(effective[i]-angle))
        strips.append(dict(surface=p['surface'],foil_sha256=p['foil'],center_m=p['center'].tolist(),
            area_m2=float(area[i]),chord_m=float(chord[i]),velocity_m_s=velocity[i].tolist(),
            induced_velocity_m_s=induced[i].tolist(),effective_alpha_deg=float(effective[i]),
            reynolds=float(reynolds[i]),section_cl_cd_cm=coeff[i].tolist(),
            circulation_m2_s=float(gamma[i]),
            force_n=force[i].tolist(),moment_cg_nm=moments[i].tolist(),interaction_sources=interactions))
    report=dict(scope='attached_coordinate_lifting_surfaces_development',state='computed',
        geometry=record,installed_sha256=installed['installed_sha256'],controls=controls,
        conditions=dict(speed_m_s=speed_m_s,alpha_deg=alpha_deg,altitude_m=altitude_m,spanwise=spanwise,
            initial_circulation_m2_s=None if initial_circulation_m2_s is None else initial.tolist()),
        polar_bank_sha256=digest(bank.reports),
        implementation_sha256=digest(dict(model=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            polar_interpolation=hashlib.sha256(Path(inspect.getfile(type(bank))).read_bytes()).hexdigest(),
            covered_solver=hashlib.sha256(Path(inspect.getfile(bounded_newton)).read_bytes()).hexdigest(),
            kernel=hashlib.sha256(Path(inspect.getfile(segment_velocity)).read_bytes()).hexdigest())),
        runtime={name:version(name) for name in ('numpy','scipy','aerosandbox')},
        axes='geometry x aft, y right, z up; moments about screening inventory CG',
        force_n=force.sum(axis=0).tolist(),moment_cg_nm=moments.sum(axis=0).tolist(),
        induced_drag_n=max(0.,induced_drag),profile_drag_n=profile_drag,
        lifting_surface_drag_n=float(force.sum(axis=0)@wind),whole_aircraft_drag_n=None,
        body_drag_n=None,circulation_residual=error,
        solver_diagnostics={k:v for k,v in fit.items() if k != 'value'},strips=strips,
        production_admission=False,qualified_aircraft_stall=False,qualified_control_authority=False,
        independent_numerical_performance=False,physical_construction=False,verified_gain=False,
        missing=['body OML and body/junction drag','qualified nonlinear interaction/stall envelope',
            'full force/moment equilibrium','full inertia and dynamic modes','directional control',
            'required control moments and authority envelope','independent family reference validation'])
    # Enforce finite portable output; make returned input structures independent.
    import json
    return json.loads(canonical_json(report))


def replay(report, bank):
    if report.get('scope') != 'attached_coordinate_lifting_surfaces_development':
        raise ValueError('Unknown coupled development scope')
    expected=solve(report['geometry'],report['controls'],bank,**report['conditions'])
    if canonical_json(expected)!=canonical_json(report):
        raise ValueError('Coupled development evidence differs from reconstruction')
    return False


def static_derivatives(record, controls, bank, *, alpha_step_deg=.25, **conditions):
    """Fixed-state attached-flow alpha derivatives, never dynamic stability.

    Derivatives are per radian at fixed speed and installed hardware. Neither
    alpha perturbation retrims controls or relocates mass to rescue a design.
    """
    if type(alpha_step_deg) not in (int,float) or not .1 <= alpha_step_deg <= 1.:
        raise ValueError('Static derivative step must be 0.1..1 degree')
    alpha=conditions.get('alpha_deg',4.)
    lower=solve(record,controls,bank,**dict(conditions,alpha_deg=alpha-alpha_step_deg))
    # Warm-start the adjacent angle with a real covered solution. Zero
    # circulation at the new angle may lie outside the polar table even when
    # the coupled solution is covered. Every residual still enforces coverage.
    upper=solve(record,controls,bank,**dict(conditions,alpha_deg=alpha+alpha_step_deg,
        initial_circulation_m2_s=[s['circulation_m2_s'] for s in lower['strips']]))
    installed=freeze(record,controls)
    ref=installed['payload']['reference']
    import aerosandbox as asb
    q=.5*float(asb.Atmosphere(altitude=conditions.get('altitude_m',0.)).density())*conditions.get('speed_m_s',6.)**2
    def coefficients(report):
        a=math.radians(report['conditions']['alpha_deg'])
        up=np.array([-math.sin(a),0.,math.cos(a)])
        return np.array([np.asarray(report['force_n'])@up/(q*ref['area_m2']),
                         report['moment_cg_nm'][1]/(q*ref['area_m2']*ref['chord_m'])])
    cla,cma=(coefficients(upper)-coefficients(lower))/(2*math.radians(alpha_step_deg))
    return dict(scope='attached_lifting_only_static_derivative_development',
        alpha_step_deg=alpha_step_deg,lower=lower,upper=upper,
        cl_alpha_per_rad=float(cla),cm_alpha_per_rad=float(cma),
        static_margin=float(-cma/cla) if cla>0 else None,
        dynamic_stability_qualified=False,production_admission=False,
        missing=['body derivatives','full-aircraft trim','dynamic modes and full inertia',
                 'independent family validation'])
