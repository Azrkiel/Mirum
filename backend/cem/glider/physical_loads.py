"""Task B prototype contract and mechanics; not Task A aerodynamic evidence.

SI, installed aircraft axes: X aft, Y right, Z up. A right half-wing is a
cantilever along +Y. Positive distributed Fz gives positive internal Mx;
positive distributed My is positive spanwise torque. Reactions oppose loads.
No total-aircraft elliptical load or implicit symmetry/load-factor is invented.
Task A owns the eventual shared contract; this adapter remains fixture-only.
"""
import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from cem.core import StrictModel, digest


class StationLoad(StrictModel):
    y_m: float = Field(ge=0, le=.7)
    fz_n_m: float
    my_nm_m: float


class InstalledLoadCase(StrictModel):
    version: Literal['task-b-fixture-loads-v1'] = 'task-b-fixture-loads-v1'
    source: Literal['analytical_fixture'] = 'analytical_fixture'
    geometry_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    operating_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    stations: tuple[StationLoad, ...] = Field(min_length=2, max_length=129)

    @model_validator(mode='after')
    def ordered(self):
        ys = [s.y_m for s in self.stations]
        if ys[0] != 0 or any(b <= a for a, b in zip(ys, ys[1:])):
            raise ValueError('Load stations must cover root to tip in strictly increasing order')
        return self


class BeamMaterial(StrictModel):
    youngs_modulus_pa: float = Field(gt=0)
    shear_modulus_pa: float = Field(gt=0)
    allowable_normal_pa: float = Field(gt=0)
    allowable_shear_pa: float = Field(gt=0)
    safety_factor: float = Field(ge=1)


class RootSupport(StrictModel):
    kind: Literal['bonded_sleeve_fixed_fixture'] = 'bonded_sleeve_fixed_fixture'
    overlap_m: float = Field(gt=0, le=.2)
    radius_m: float = Field(gt=0, le=.02)
    bond_allowable_shear_pa: float = Field(gt=0)
    safety_factor: float = Field(ge=1)


def _integrate(case, start, end, function, subdivisions=1):
    # All integrations split at load knots; four-point Gauss is exact for
    # these piecewise polynomials, including the unit-load deflection integral.
    nodes, weights = np.polynomial.legendre.leggauss(4)
    result = 0.
    for a, b in zip(case.stations, case.stations[1:]):
        lo, hi = max(start, a.y_m), min(end, b.y_m)
        if hi <= lo:
            continue
        cuts = np.linspace(lo, hi, subdivisions+1)
        for left, right in zip(cuts, cuts[1:]):
            x = (nodes+1)*(right-left)/2+left
            fraction = (x-a.y_m)/(b.y_m-a.y_m)
            f = a.fz_n_m+fraction*(b.fz_n_m-a.fz_n_m)
            t = a.my_nm_m+fraction*(b.my_nm_m-a.my_nm_m)
            result += float(np.dot(weights, function(x, f, t)))*(right-left)/2
    return float(result)


def resultants(case, y):
    tip = case.stations[-1].y_m
    if not math.isfinite(y) or not 0 <= y <= tip:
        raise ValueError('Section outside the installed half-wing')
    return dict(shear_n=_integrate(case, y, tip, lambda x, f, t: f),
                bending_nm=_integrate(case, y, tip, lambda x, f, t: f*(x-y)),
                torque_nm=_integrate(case, y, tip, lambda x, f, t: t))


def analyze(case, *, geometry_sha256, operating_sha256, half_span_m, section, material,
            support, max_deflection_m, max_twist_rad, subdivisions=1):
    """Uniform round-section Euler beam with measured conservative section bounds.

    The root joint uses sinusoidal bending transfer plus direct/transverse and
    torsional bond shear, summed conservatively. This assumes perfect adhesion
    and a fixed fixture; peel, core failure, mount compliance and fatigue remain
    unsupported. It cannot qualify a fuselage attachment or aerodynamic loads.
    """
    if case.geometry_sha256 != geometry_sha256 or case.operating_sha256 != operating_sha256:
        raise ValueError('Stale geometry or operating load revision')
    if (not math.isfinite(half_span_m) or half_span_m <= 0
            or abs(case.stations[-1].y_m-half_span_m) > 2e-7):
        raise ValueError('Loads do not cover the delivered half-span')
    if support is None:
        raise ValueError('Missing installed root support')
    if type(subdivisions) is not int or not 1 <= subdivisions <= 16:
        raise ValueError('Integration budget outside 1..16')
    keys = ('area_m2', 'min_inertia_m4', 'polar_inertia_m4', 'extreme_radius_m')
    if any(not math.isfinite(section[k]) or section[k] <= 0 for k in keys):
        raise ValueError('Missing measured section properties')
    if not all(math.isfinite(x) and x > 0 for x in (max_deflection_m, max_twist_rad)):
        raise ValueError('Explicit finite stiffness limits required')
    r = section['extreme_radius_m']
    if abs(r-support.radius_m) > 2e-7:
        raise ValueError('Root bond radius differs from delivered spar')
    length = case.stations[-1].y_m
    root = resultants(case, 0.)
    # Absolute distributed loads bound all internal resultants, including
    # sign-changing cases whose root resultant can cancel. Split at zeroes
    # explicitly for exact integration of |q| and |t|.
    absolute = []
    for a, b in zip(case.stations, case.stations[1:]):
        cuts = [a.y_m, b.y_m]
        for key in ('fz_n_m', 'my_nm_m'):
            v, w = getattr(a, key), getattr(b, key)
            if v*w < 0:
                cuts.append(a.y_m+(b.y_m-a.y_m)*v/(v-w))
        for y in sorted(set(cuts))[:-1]:
            u = (y-a.y_m)/(b.y_m-a.y_m)
            absolute.append(StationLoad(y_m=float(y), fz_n_m=abs(a.fz_n_m+u*(b.fz_n_m-a.fz_n_m)),
                                        my_nm_m=abs(a.my_nm_m+u*(b.my_nm_m-a.my_nm_m))))
    a = case.stations[-1]
    absolute.append(StationLoad(y_m=a.y_m, fz_n_m=abs(a.fz_n_m), my_nm_m=abs(a.my_nm_m)))
    bound_case = case.model_copy(update={'stations': tuple(absolute)})
    bounds = resultants(bound_case, 0.)
    normal = bounds['bending_nm']*r/section['min_inertia_m4']
    # Conservative circular-section stress bounds. Polygon torsion needs its
    # own warping solution; 1.1 is an explicit screening allowance only.
    shear = 4/3*bounds['shear_n']/section['area_m2']+1.1*bounds['torque_nm']*r/section['polar_inertia_m4']
    ei = material.youngs_modulus_pa*section['min_inertia_m4']
    gj = material.shear_modulus_pa*section['polar_inertia_m4']
    deflection = _integrate(bound_case, 0., length,
        lambda x, f, t: f*x*x*(3*length-x)/(6*ei), subdivisions)
    twist = _integrate(bound_case, 0., length, lambda x, f, t: t*x/gj, subdivisions)
    bond = (bounds['bending_nm']/(math.pi*r*r*support.overlap_m)
            + bounds['shear_n']/(2*math.pi*r*support.overlap_m)
            + bounds['torque_nm']/(2*math.pi*r*r*support.overlap_m))
    checks = dict(bending=normal <= material.allowable_normal_pa/material.safety_factor,
                  shear_torsion=shear <= material.allowable_shear_pa/material.safety_factor,
                  root_bond=bond <= support.bond_allowable_shear_pa/support.safety_factor,
                  deflection=deflection <= max_deflection_m, twist=twist <= max_twist_rad)
    return dict(state='passed' if all(checks.values()) else 'failed', checks=checks,
                load_sha256=digest(case), geometry_sha256=geometry_sha256,
                analysis_input_sha256=digest(dict(loads=case.model_dump(mode='json'),
                    half_span_m=half_span_m, section=section, material=material.model_dump(mode='json'),
                    support=support.model_dump(mode='json'), max_deflection_m=max_deflection_m,
                    max_twist_rad=max_twist_rad)),
                operating_sha256=operating_sha256, root_resultants=root,
                reaction=dict(fz_n=-root['shear_n'], mx_nm=-root['bending_nm'], my_nm=-root['torque_nm']),
                stress_bounds_pa=dict(normal=normal, shear=shear, root_bond=bond),
                tip_deflection_bound_m=deflection, tip_twist_bound_rad=twist,
                qualification_state='analytical_fixture_only', manufacturing_ready=False,
                unsupported=['aerodynamic load qualification', 'fuselage attachment compliance/strength',
                             'foam crushing/shear', 'bond peel/defects', 'buckling', 'fatigue',
                             'polygon torsion warping', 'delivered aircraft trim'])
