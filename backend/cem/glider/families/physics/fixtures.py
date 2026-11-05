"""Analytic linear-coefficient aircraft fixture. NEVER family or physical evidence.

Coefficients are declared constants used to verify trim, derivative and
dynamics machinery against closed-form references. Contributions carry the
``analytic_fixture`` evidence class, which no admission path accepts.

Coefficient model (angles/deflections in radians, hats p b/2V, q c/2V, r b/2V,
rates in body axes B):

    CL = CL0 + CLa a + CLq q^ + sum CL_d d          CD = CD0 + k CL^2
    CY = CYb b + CYp p^ + CYr r^
    Cl = Clb b + Clp p^ + Clr r^ + sum Cl_d d       (body-axis roll, right-wing-down +)
    Cm = Cm0 + Cma a + Cmq q^ + sum Cm_d d
    Cn = Cnb b + Cnp p^ + Cnr r^ + sum Cn_d d
"""
import math

import numpy as np
from pydantic import Field

from cem.core import StrictModel, digest
from . import frames, wrench


class LinearAircraft(StrictModel):
    density_kg_m3: float = Field(1.225, gt=0)
    area_m2: float = Field(.3, gt=0)
    chord_m: float = Field(.2, gt=0)
    span_m: float = Field(1.5, gt=0)
    reference_point_m: tuple[float, float, float] = (.25, 0., 0.)
    CL0: float = .3
    CLa: float = 5.
    CLq: float = 6.
    CD0: float = .02
    k: float = .05
    Cm0: float = .05
    Cma: float = -.8
    Cmq: float = -12.
    CYb: float = -.3
    CYp: float = 0.
    CYr: float = .15
    Clb: float = -.08
    Clp: float = -.45
    Clr: float = .1
    Cnb: float = .07
    Cnp: float = -.03
    Cnr: float = -.08
    controls: dict[str, dict[str, float]] = Field(default_factory=lambda: {
        'elevator': {'CL': .4, 'Cm': -1.2}, 'aileron': {'Cl': .25, 'Cn': -.01}})
    # Contribution names emitted as explicitly unsupported (missing evidence).
    unsupported: tuple[str, ...] = ()
    nonfinite: bool = False
    # alpha/speed applicability; outside raises PolarCoverageError.
    alpha_range_deg: tuple[float, float] = (-6., 12.)
    speed_range_m_s: tuple[float, float] = (2., 40.)

    @property
    def revision(self):
        return digest(dict(fixture='linear-aircraft-v1', parameters=self.model_dump(mode='json')))

    def coefficients(self, state):
        from cem.aero.polars import PolarCoverageError
        V, a_deg, b_deg = state['speed_m_s'], state['alpha_deg'], state['beta_deg']
        if not (self.alpha_range_deg[0] <= a_deg <= self.alpha_range_deg[1]
                and self.speed_range_m_s[0] <= V <= self.speed_range_m_s[1]):
            raise PolarCoverageError('Fixture state outside declared applicability')
        a, b = math.radians(a_deg), math.radians(b_deg)
        ph = state['p_rad_s']*self.span_m/(2*V)
        qh = state['q_rad_s']*self.chord_m/(2*V)
        rh = state['r_rad_s']*self.span_m/(2*V)
        d = {name: math.radians(value) for name, value in state['controls'].items()}
        if set(d) != set(self.controls):
            raise ValueError('Fixture requires every declared control state')
        add = lambda key: sum(self.controls[n].get(key, 0.)*v for n, v in d.items())
        cl = self.CL0+self.CLa*a+self.CLq*qh+add('CL')
        return dict(CL=cl, CD=self.CD0+self.k*cl*cl, CY=self.CYb*b+self.CYp*ph+self.CYr*rh,
                    Cl=self.Clb*b+self.Clp*ph+self.Clr*rh+add('Cl'),
                    Cm=self.Cm0+self.Cma*a+self.Cmq*qh+add('Cm'),
                    Cn=self.Cnb*b+self.Cnp*ph+self.Cnr*rh+add('Cn'))

    def __call__(self, state):
        c = self.coefficients(state)
        q = .5*self.density_kg_m3*state['speed_m_s']**2
        if self.nonfinite:
            q = float('nan')
        force = frames.force_from_wind(q*self.area_m2*c['CD'], q*self.area_m2*c['CY'],
                                       q*self.area_m2*c['CL'], state['alpha_deg'], state['beta_deg']) \
            if math.isfinite(q) else np.full(3, np.nan)
        moment_b = q*self.area_m2*np.array([self.span_m*c['Cl'], self.chord_m*c['Cm'], self.span_m*c['Cn']])
        moment = frames.body_to_geometry(moment_b) if np.isfinite(moment_b).all() else moment_b
        out = [wrench.Contribution(name='fixture_aero', kind='aerodynamic_lifting', state='supported',
            revision_sha256=self.revision, evidence='analytic_fixture',
            force_n=tuple(map(float, force)), moment_nm=tuple(map(float, moment)),
            reference_point_m=self.reference_point_m, scope='analytic linear fixture')]
        out += [wrench.unsupported(name, 'aerodynamic_body', self.revision,
                                   'fixture declares this contribution unsupported') for name in self.unsupported]
        return out


# ----------------------------------------------------------------------------
# Published numerical reference: Boeing 747, 40,000 ft, Mach 0.8, stability axes
# (body = stability axes at the alpha = 0, theta = 0 reference state).
#
# Dimensional derivatives, mass and inertias: the 747 example of B. Etkin and
# L. D. Reid, "Dynamics of Flight: Stability and Control", 3rd ed., Wiley,
# 1996, Ch. 6 (values as reproduced in course material; this set reproduces
# the separately published matrices below to their printed precision, which
# is the acceptance check, not the book itself). The independently published
# state matrices and eigenvalues used as acceptance values are from
# J. How, MIT 16.61 Aerospace Dynamics, Spring 2003, Lectures AC-2
# (longitudinal) and AC-3 (lateral):
#   ocw.mit.edu/courses/16-61-aerospace-dynamics-spring-2003/ (lecture17.pdf,
#   lecture18.pdf). Values are quoted at their printed precision.
# This is an equations check on a transport aircraft; it is never family,
# glider or physical evidence.
# ----------------------------------------------------------------------------
B747 = dict(
    source='Etkin & Reid (1996) 747 cruise derivatives; How, MIT 16.61 (2003) AC-2/AC-3 published A and eigenvalues',
    speed_m_s=235.9, weight_n=2.83176e6, g_m_s2=9.81,
    inertia=dict(Ix=.247e8, Iy=.449e8, Iz=.673e8, Izx=-.212e7),  # Izx = integral(x z dm), stability axes
    X=dict(u=-1.982e3, w=4.025e3, q=0.), Z=dict(u=-2.595e4, w=-9.030e4, q=-4.524e5),
    M=dict(u=1.593e4, w=-1.563e5, q=-1.521e7),
    Y=dict(v=-1.610e4, p=0., r=0.), L=dict(v=-3.062e5, p=-1.076e7, r=9.925e6),
    N=dict(v=2.131e5, p=-1.330e6, r=-8.934e6),
    unsteady=dict(X_wdot=0., Z_wdot=1.909e3, M_wdot=-1.702e4),
    published=dict(
        longitudinal_states=['u', 'w', 'q', 'theta'],
        A_longitudinal=[[-.0069, .0139, 0., -9.81], [-.0905, -.3149, 235.8928, 0.],
                        [.0004, -.0034, -.4282, 0.], [0., 0., 1., 0.]],
        short_period=dict(real=-.3717, imag=.8869, natural_frequency=.962, damping_ratio=.387),
        phugoid=dict(real=-.0033, imag=.0672, natural_frequency=.067, damping_ratio=.049),
        lateral_states=['v', 'p', 'r', 'phi'],
        dutch_roll=dict(real=-.0331, imag=.9470), roll=dict(real=-.5633), spiral=dict(real=-.0073)))


class DimensionalAircraft(StrictModel):
    """Linear dimensional body-axis derivatives about a reference state.

    Forces/moments are F0 + D (u - u0, v, w, p, q, r) in B, with F0 balancing
    gravity at theta = phi = 0. Rate terms are genuine model terms here; the
    acceleration terms are supplied separately as ``UnsteadyDerivatives``.
    """
    reference: dict = Field(default_factory=lambda: B747)

    @property
    def revision(self):
        return digest(dict(fixture='dimensional-aircraft-v1', reference=self.reference))

    @property
    def mass_kg(self):
        return self.reference['weight_n']/self.reference['g_m_s2']

    def __call__(self, state):
        r = self.reference
        V, a, b = state['speed_m_s'], math.radians(state['alpha_deg']), math.radians(state['beta_deg'])
        u, v, w = V*math.cos(a)*math.cos(b), V*math.sin(b), V*math.sin(a)*math.cos(b)
        p, q, rr = state['p_rad_s'], state['q_rad_s'], state['r_rad_s']
        du = u-r['speed_m_s']
        force_b = np.array([r['X']['u']*du+r['X']['w']*w+r['X']['q']*q,
                            r['Y']['v']*v+r['Y']['p']*p+r['Y']['r']*rr,
                            -r['weight_n']+r['Z']['u']*du+r['Z']['w']*w+r['Z']['q']*q])
        moment_b = np.array([r['L']['v']*v+r['L']['p']*p+r['L']['r']*rr,
                             r['M']['u']*du+r['M']['w']*w+r['M']['q']*q,
                             r['N']['v']*v+r['N']['p']*p+r['N']['r']*rr])
        return [wrench.supported('reference_aero', 'aerodynamic_lifting', self.revision,
            frames.body_to_geometry(force_b), frames.body_to_geometry(moment_b), (0., 0., 0.),
            evidence='analytic_fixture', scope='published 747 dimensional derivatives (equations check)')]


def published_747():
    """Model, mass, equilibrium inputs and unsteady terms of the 747 reference."""
    from . import dynamics, mass as massprops
    model = DimensionalAircraft()
    r = model.reference
    i = r['inertia']
    # Body-axes tensor: off-diagonal is minus the product of inertia.
    tensor = [[i['Ix'], 0., -i['Izx']], [0., i['Iy'], 0.], [-i['Izx'], 0., i['Iz']]]
    m = massprops.reference_rigid_body(model.mass_kg, (0., 0., 0.), tensor, source=r['source'])
    u = r['unsteady']
    unsteady = dynamics.UnsteadyDerivatives(revision_sha256=model.revision, evidence='published_reference',
        source=r['source'], artifact_sha256=digest(r),
        derivatives=dict(X=(0., 0., u['X_wdot']), Y=(0., 0., 0.), Z=(0., 0., u['Z_wdot']),
                         L=(0., 0., 0.), M=(0., 0., u['M_wdot']), N=(0., 0., 0.)),
        speed_range_m_s=(r['speed_m_s'], r['speed_m_s']), alpha_range_deg=(0., 0.))
    flight = dict(speed_m_s=r['speed_m_s'], alpha_deg=0., beta_deg=0., pitch_deg=0., bank_deg=0.)
    return dict(model=model, mass=m, flight_state=flight, unsteady=unsteady, g_m_s2=r['g_m_s2'],
                published=r['published'])
