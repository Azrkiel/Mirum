"""Nonlinear horseshoe lifting lines driven by section polars, independent of _aero.

Rigid, attached, steady flow only. Quarter-chord lifting lines and straight
freestream wakes are a low-order model; AVL agreement and refinement are gates.
This module never interprets AVL far-wake `ai` as local induced incidence.
"""
import math

import numpy as np

from .contracts import LiftingSurfaceLoads, StripLoad
from .numerics import bounded_newton


def panels(geometry, spanwise=12):
    if spanwise not in (8,12,16,24,32):
        raise ValueError('Unsupported lifting-line resolution')
    values=[]
    for surface in geometry.surfaces:
        sections=surface.sections
        lengths=np.array([np.linalg.norm(np.subtract(b.leading_edge_m,a.leading_edge_m)[1:])
                          for a,b in zip(sections,sections[1:])])
        if min(lengths)<=1e-9:
            raise ValueError('Degenerate lifting span')
        for i,(a,b) in enumerate(zip(sections,sections[1:])):
            count=max(1,int(round(spanwise*lengths[i]/sum(lengths))))
            for j in range(count):
                endpoints=[]
                for t in (j/count,(j+1)/count):
                    c=a.chord_m+(b.chord_m-a.chord_m)*t
                    twist=math.radians(a.incidence_deg+(b.incidence_deg-a.incidence_deg)*t)
                    chord=np.array([math.cos(twist),0.,-math.sin(twist)])
                    le=(1-t)*np.array(a.leading_edge_m)+t*np.array(b.leading_edge_m)
                    endpoints.append((le+.25*c*chord,c,chord))
                (p0,c0,d0),(p1,c1,d1)=endpoints
                tangent=p1-p0
                # Strip span normal to chord, excluding sweep.
                chord=(d0+d1)/np.linalg.norm(d0+d1)
                span=tangent-chord*np.dot(tangent,chord)
                width=np.linalg.norm(span); span/=width
                if width<=1e-8 or abs(np.dot(tangent/np.linalg.norm(tangent),chord))>.5:
                    raise ValueError('Unsupported degenerate/high-sweep lifting surface')
                normal=np.cross(chord,span)
                value=dict(surface=surface.name,naca=surface.naca,a=p0,b=p1,
                    center=.5*(p0+p1),chord=.5*(c0+c1),chord_direction=chord,
                    span=span,normal=normal,area=width*.5*(c0+c1))
                values.append(value)
                if surface.mirror_y:
                    mirror=np.array([1.,-1.,1.])
                    other=dict(value,a=p1*mirror,b=p0*mirror,center=value['center']*mirror,
                        span=-span*mirror,normal=normal*mirror)
                    values.append(other)
    if len(values)>256:
        raise ValueError('Lifting mesh exceeds load contract budget')
    return values


def segment_velocity(points, a, b):
    """Unit-strength finite vortex; zero on its own singular straight line."""
    r1=points[:,None,:]-a[None,:,:]; r2=points[:,None,:]-b[None,:,:]
    cross=np.cross(r1,r2); cross2=np.sum(cross*cross,axis=2)
    l1=np.linalg.norm(r1,axis=2); l2=np.linalg.norm(r2,axis=2)
    unit1=r1/np.maximum(l1[:,:,None],1e-12); unit2=r2/np.maximum(l2[:,:,None],1e-12)
    factor=np.sum((b-a)[None,:,:]*(unit1-unit2),axis=2)/(4*math.pi*np.maximum(cross2,1e-20))
    factor=np.where((cross2>1e-18)&(l1>1e-9)&(l2>1e-9),factor,0.)
    return cross*factor[:,:,None]


def solve(geometry, op, bank, spanwise=12, *, diagnostics=None):
    if op.mach!=0.:
        raise ValueError('Compressible lifting-line analysis is not qualified')
    if geometry.family not in ('conventional','t_tail','v_tail'):
        raise ValueError('Aircraft family lacks independent aerodynamic qualification')
    if bank.binding!=geometry.binding:
        raise ValueError('Polar bank geometry/context mismatch')
    mesh=panels(geometry,spanwise)
    a=np.array([p['a'] for p in mesh]); b=np.array([p['b'] for p in mesh])
    centers=np.array([p['center'] for p in mesh]); spans=np.array([p['span'] for p in mesh])
    normals=np.array([p['normal'] for p in mesh]); chords=np.array([p['chord_direction'] for p in mesh])
    c=np.array([p['chord'] for p in mesh]); area=np.array([p['area'] for p in mesh])
    alpha=math.radians(op.alpha_deg); wind=np.array([math.cos(alpha),0.,math.sin(alpha)])
    wake=wind*geometry.reference.span_m*1000
    influence=(segment_velocity(centers,a+ wake,a)+segment_velocity(centers,a,b)
               +segment_velocity(centers,b,b+wake))
    rho,nu,V=op.density_kg_m3,op.viscosity_pa_s,op.velocity_m_s

    def local(gamma):
        induced=np.einsum('ijk,j->ik',influence,gamma)
        velocity=V*wind+induced
        perpendicular=velocity-np.sum(velocity*spans,axis=1)[:,None]*spans
        speed=np.linalg.norm(perpendicular,axis=1)
        effective=np.degrees(np.arctan2(np.sum(perpendicular*normals,axis=1),np.sum(perpendicular*chords,axis=1)))
        re=rho*speed*c/nu
        coeff=np.array([bank.coefficients(p['naca'],float(r),float(al)) for p,r,al in zip(mesh,re,effective)])
        return induced,perpendicular,speed,effective,re,coeff

    def residual(gamma):
        _,_,speed,_,_,coeff=local(gamma)
        return (gamma-.5*speed*c*coeff[:,0])/(V*c)

    scaling=V*c
    solution=bounded_newton(lambda value:residual(value*scaling),np.zeros(len(mesh)),tolerance=1e-10)
    circulation=solution['value']*scaling
    error=float(np.max(np.abs(residual(circulation))))
    if error>1e-6:
        raise ValueError('Nonlinear lifting-line circulation failed to converge')
    if diagnostics is not None:
        diagnostics.update({k:v for k,v in solution.items() if k!='value'})
    induced,velocity,speed,effective,re,coeff=local(circulation)
    lift=rho*circulation[:,None]*np.cross(velocity,b-a)
    profile=.5*rho*speed[:,None]*area[:,None]*coeff[:,1,None]*velocity
    forces=lift+profile
    section_moments=.5*rho*speed[:,None]**2*area[:,None]*c[:,None]*coeff[:,2,None]*spans
    moments=np.cross(centers-np.array(geometry.reference.cg_m),forces)+section_moments
    di=float(np.sum(lift,axis=0)@wind); dp=float(np.sum(profile,axis=0)@wind)
    if di < -1e-7 or dp<=0:
        raise ValueError('Nonphysical independent drag decomposition')
    strips=tuple(StripLoad(surface=p['surface'],center_m=tuple(p['center']),area_m2=float(ar),
        chord_m=float(ch),velocity_m_s=tuple(v),induced_velocity_m_s=tuple(iv),
        effective_alpha_deg=float(al),reynolds=float(r),force_n=tuple(f),moment_cg_nm=tuple(m))
        for p,ar,ch,v,iv,al,r,f,m in zip(mesh,area,c,velocity,induced,effective,re,forces,moments))
    return LiftingSurfaceLoads(binding=geometry.binding,operating_point=op,strips=strips,
        force_n=tuple(np.sum(forces,axis=0)),moment_cg_nm=tuple(np.sum(moments,axis=0)),
        induced_drag_n=max(0.,di),profile_drag_n=dp,circulation_residual=error,
        force_closure_n=float(np.linalg.norm(np.sum([s.force_n for s in strips],axis=0)-np.sum(forces,axis=0))),
        moment_closure_nm=float(np.linalg.norm(np.sum([s.moment_cg_nm for s in strips],axis=0)-np.sum(moments,axis=0))))
