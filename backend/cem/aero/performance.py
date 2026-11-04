"""Independent fixed-hardware equilibrium and numerical gain criterion."""
import math

import numpy as np
from scipy.optimize import least_squares

from cem.core import digest
from cem.glider.physics import G, atmosphere, launch_state, trajectory
from .contracts import OperatingPoint, StructuralLoadCase
from .body import increments
from .lifting import solve


def equilibrium(geometry, bank, req, calibration, initial, spanwise=12):
    air=atmosphere(req.environment); ref=geometry.reference; weight=ref.mass_kg*G
    extent=max(max(s.leading_edge_m[0]+s.chord_m for surface in geometry.surfaces for s in surface.sections),
               geometry.body[-1].x_m)-min(geometry.body[0].x_m,0.)
    if (ref.mass_kg>req.constraints.max_mass_kg or ref.span_m>req.constraints.max_span_m
            or extent>req.constraints.max_length_m):
        raise ValueError('Installed independent geometry violates mission mass/dimension constraints')
    if req.environment.kind not in ('still_air','wind','uniform_uplift'):
        raise ValueError('Independent trajectory environment unsupported')

    def evaluate(x):
        speed,alpha,gamma=x
        op=OperatingPoint(velocity_m_s=float(speed),alpha_deg=float(alpha),
            density_kg_m3=float(air['density_kg_m3']),viscosity_pa_s=float(air['dynamic_viscosity_pa_s']))
        lifting=solve(geometry,op,bank,spanwise); body=increments(geometry,op)
        force=np.array(lifting.force_n)+body.force_n
        moment=np.array(lifting.moment_cg_nm)+body.moment_cg_nm
        a=math.radians(alpha); wind=np.array([math.cos(a),0.,math.sin(a)])
        up=np.array([-math.sin(a),0.,math.cos(a)])
        lift,drag=float(force@up),float(force@wind)
        residual=np.array([(lift-weight*math.cos(gamma))/weight,
                           (drag-weight*math.sin(gamma))/weight,moment[1]/(weight*ref.chord_m)])
        return residual,lifting,body,lift,drag

    x0=np.array([initial['glide_speed_m_s'],initial['alpha_trim_deg'],math.radians(initial['glide_angle_deg'])])
    # Keep optimizer steps inside the qualified Re table rather than querying
    # arbitrary low speeds on its way to an otherwise covered equilibrium.
    # Local induced/perpendicular velocities are still checked at every strip.
    minimum_speed=1.; maximum_speed=req.constraints.max_speed_m_s
    for naca,group in bank.groups.items():
        chords=[s.chord_m for surface in geometry.surfaces if surface.naca==naca for s in surface.sections]
        minimum_speed=max(minimum_speed,group[0].reynolds*air['dynamic_viscosity_pa_s']/(
            air['density_kg_m3']*min(chords)*.95))
        maximum_speed=min(maximum_speed,group[-1].reynolds*air['dynamic_viscosity_pa_s']/(
            air['density_kg_m3']*max(chords)*1.1))
    if minimum_speed>=maximum_speed:
        raise ValueError('No common independently covered speed interval')
    lower=[minimum_speed,-3.,.001]; upper=[maximum_speed,8.,.4]
    x0=np.clip(x0,np.array(lower)+1e-5,np.array(upper)-1e-5)
    fit=least_squares(lambda x:evaluate(x)[0],x0,bounds=(lower,upper),
                      xtol=1e-7,ftol=1e-7,gtol=1e-7,max_nfev=35,diff_step=1e-4)
    residual,loads,body,lift,drag=evaluate(fit.x)
    if not fit.success or max(abs(residual))>1e-4:
        raise ValueError('Fixed-hardware independent force/moment equilibrium failed')
    full_force=np.array(loads.force_n)+body.force_n
    full_moment=np.array(loads.moment_cg_nm)+body.moment_cg_nm
    if (abs(full_force[1])>weight*1e-4
            or max(abs(full_moment[[0,2]]))>weight*ref.span_m*1e-4):
        raise ValueError('Independent symmetric flight has unresolved side force/roll/yaw moment')
    speed,alpha,gamma=map(float,fit.x)
    flight=trajectory(launch_state(req.launch,calibration)['effective_height_m'],speed,speed*math.sin(gamma),
                      req.environment.wind_m_s,req.environment.uplift_m_s)
    objective=flight[req.objective]
    if objective is None or objective<=0:
        raise ValueError('Independent mission has no bounded positive objective')
    loadcase=StructuralLoadCase(name='independent-steady-glide',binding=geometry.binding,
        aerodynamic_loads_sha256=digest(loads),load_factor=1.,loads=loads)
    return dict(binding=geometry.binding.model_dump(mode='json'),speed_m_s=speed,alpha_deg=alpha,
        glide_angle_deg=math.degrees(gamma),lift_n=lift,drag_n=drag,
        moment_nm=loads.moment_cg_nm[1]+body.moment_cg_nm[1],
        equilibrium_residual=residual.tolist(),trajectory=flight,objective_value=objective,
        loads=loads.model_dump(mode='json'),body=body.model_dump(mode='json'),
        structural_load_case=loadcase.model_dump(mode='json'))


def numerical_error(coarse, fine, objective):
    """Engineering model allowances + numerical refinement; never flight statistics.

    No cancellation of paired errors is assumed. Sum both arms' bounds for the
    gain criterion. Body friction/transition ambiguity is explicitly included.
    """
    value=fine['objective_value']
    refinement=abs(value-coarse['objective_value'])
    if refinement/max(value,1e-9)>.02:
        raise ValueError('Independent mesh objective failed 2% refinement tolerance')
    body=fine['body']['drag_error_bound_n']/fine['drag_n']
    # 5% lifting/body-interference model allowance and 5% profile/coupling
    # allowance, plus native/mesh discrepancy. These are declared bounds for
    # this model comparison, not validated physical uncertainty distributions.
    relative=.10+body+2*refinement/value
    return dict(objective=objective,absolute_bound=relative*value,
        mesh_difference=refinement,relative_model_allowance=.10,body_drag_relative_bound=body,
        criterion='shaped - baseline > sum of both absolute bounds',
        scope='Rigid attached-flow numerical model; not empirical flight validation')


def gain(arms):
    if len(arms)!=2 or any(a.get('state')!='passed' for a in arms):
        return dict(verified_gain=False,objective_delta=None,error_bound=None,state='inconclusive')
    delta=arms[1]['performance']['objective_value']-arms[0]['performance']['objective_value']
    bound=sum(a['error']['absolute_bound'] for a in arms)
    if not all(math.isfinite(x) for x in (delta,bound)) or bound<0:
        raise ValueError('Invalid numerical gain evidence')
    return dict(verified_gain=bool(delta>bound),objective_delta=delta,error_bound=bound,
        state='verified_numerical_gain' if delta>bound else 'difference_within_error_criterion')
