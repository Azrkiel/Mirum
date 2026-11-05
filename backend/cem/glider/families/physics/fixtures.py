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
