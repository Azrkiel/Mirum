"""Nondimensional stability and control derivatives with a step-convergence study.

Evaluated about an established equilibrium of a fixed revision, using only the
revision's required aerodynamic contributions (gravity excluded). Units:

- Coefficients use the *reference* dynamic pressure q0 = rho V0^2 / 2 of the
  equilibrium (Etkin convention): C_F = F/(q0 S), C_l = L/(q0 S b),
  C_m = M/(q0 S c), C_n = N/(q0 S b). Body-axis forces/moments are in B
  (x forward, y starboard, z down); wind-axis lift/drag/side force use W.
- Variables: ``alpha`` and ``beta`` in rad; ``u_hat`` = dV/V0;
  ``p_hat`` = p b/(2V0), ``q_hat`` = q c/(2V0), ``r_hat`` = r b/(2V0);
  ``control:<name>`` in rad of the declared channel deflection.

Each variable is differenced centrally at a declared decreasing step sequence.
A derivative is ``converged`` only when the two smallest steps agree within
tolerance. A perturbed state outside the aerodynamic coverage, a control step
beyond its physical limit, or an unsupported contribution (for example missing
rate dependence) yields that explicit state and *no value*. Static derivatives
never stand in for rate or unsteady derivatives; ``complete`` is false unless
every requested variable converged.
"""
import math

import numpy as np
from pydantic import Field, model_validator

from cem.aero.polars import PolarCoverageError
from cem.core import StrictModel, canonical_json, digest
from . import frames, wrench
from .trim import UnsupportedContribution

COEFFICIENTS = ('CX', 'CY', 'CZ', 'Cl', 'Cm', 'Cn', 'CL', 'CD', 'CY_wind')
STATIC = ('alpha', 'beta', 'u_hat')
RATES = ('p_hat', 'q_hat', 'r_hat')


class Settings(StrictModel):
    """Declared before evaluation; part of the report identity."""
    alpha_steps_deg: tuple[float, ...] = (.5, .25, .125)
    beta_steps_deg: tuple[float, ...] = (.5, .25, .125)
    u_hat_steps: tuple[float, ...] = (.02, .01, .005)
    rate_hat_steps: tuple[float, ...] = (.01, .005, .0025)
    control_steps_deg: tuple[float, ...] = (1., .5, .25)
    rtol: float = Field(2e-3, gt=0, le=.1)
    atol: float = Field(1e-5, gt=0, le=1e-2)

    @model_validator(mode='after')
    def decreasing(self):
        for steps in (self.alpha_steps_deg, self.beta_steps_deg, self.u_hat_steps, self.rate_hat_steps,
                      self.control_steps_deg):
            if len(steps) < 2 or any(not (s > 0 and math.isfinite(s)) for s in steps) or \
                    any(b >= a for a, b in zip(steps, steps[1:])):
                raise ValueError('Each step sequence needs >= 2 strictly decreasing positive steps')
        return self


def _aero(model, state, *, required, revision, about):
    items = list(model(state))
    total = wrench.resultant(items, required=required, revision=revision, about_m=about)
    if total['state'] != 'complete':
        raise UnsupportedContribution(total)
    return np.asarray(total['force_n']), np.asarray(total['moment_nm'])


def coefficients(model, state, *, required, revision, about, q0, area, span, chord):
    force_g, moment_g = _aero(model, state, required=required, revision=revision, about=about)
    f, m = frames.geometry_to_body(force_g), frames.geometry_to_body(moment_g)
    wind = frames.aerodynamic_components(force_g, state['alpha_deg'], state['beta_deg'])
    values = np.array([f[0]/(q0*area), f[1]/(q0*area), f[2]/(q0*area), m[0]/(q0*area*span),
                       m[1]/(q0*area*chord), m[2]/(q0*area*span), wind['lift_n']/(q0*area),
                       wind['drag_n']/(q0*area), wind['side_n']/(q0*area)])
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite aerodynamic coefficients')
    return values


def _perturb(state, variable, h, *, speed, span, chord):
    s = dict(state, controls=dict(state['controls']))
    if variable == 'alpha':
        s['alpha_deg'] = state['alpha_deg']+math.degrees(h)
    elif variable == 'beta':
        s['beta_deg'] = state['beta_deg']+math.degrees(h)
    elif variable == 'u_hat':
        s['speed_m_s'] = speed*(1+h)
    elif variable == 'p_hat':
        s['p_rad_s'] = h*2*speed/span
    elif variable == 'q_hat':
        s['q_rad_s'] = h*2*speed/chord
    elif variable == 'r_hat':
        s['r_rad_s'] = h*2*speed/span
    else:
        name = variable.split(':', 1)[1]
        s['controls'][name] = state['controls'][name]+math.degrees(h)
    return s


def compute(equilibrium, *, model, mass, required, reference, settings=None, variables=None,
            control_limits=None, feasible=None):
    """Derivatives about ``equilibrium`` (a ``trim.solve``/``verify_equilibrium`` record).

    ``reference`` = dict(area_m2, span_m, chord_m, density_kg_m3). ``feasible``
    is an optional callable(controls) -> bool for combined physical-surface
    limits; ``control_limits`` maps channel -> limit (deg).
    """
    cfg = settings if isinstance(settings, Settings) else Settings.model_validate_json(
        canonical_json(settings or {}))
    if equilibrium.get('state') != 'trimmed':
        return dict(state='unsupported', reason='Derivatives require an established equilibrium')
    if equilibrium['mass_properties_sha256'] != digest(mass):
        raise ValueError('Mass properties differ from the equilibrium revision')
    s0 = dict(equilibrium['flight_state'])
    if any(s0[k] for k in ('p_rad_s', 'q_rad_s', 'r_rad_s')):
        raise ValueError('Derivatives are defined about a non-rotating equilibrium')
    revision = equilibrium['revision_sha256']
    area, span, chord, rho = (float(reference[k]) for k in ('area_m2', 'span_m', 'chord_m', 'density_kg_m3'))
    speed = float(s0['speed_m_s'])
    q0 = .5*rho*speed*speed
    controls = sorted(s0['controls'])
    requested = list(variables) if variables is not None else [*STATIC, *RATES, *('control:'+c for c in controls)]
    unknown = [v for v in requested if v not in (*STATIC, *RATES) and v.removeprefix('control:') not in controls]
    if unknown:
        raise ValueError('Unknown derivative variable(s): '+', '.join(unknown))
    limits = dict(control_limits or {})
    if any(v.startswith('control:') for v in requested) and set(limits) != set(controls):
        raise ValueError('Explicit limit required for every control derivative')
    kw = dict(required=required, revision=revision, about=mass['cg_m'], q0=q0, area=area, span=span,
              chord=chord)
    base_values = coefficients(model, s0, **kw)
    results = {}
    for variable in requested:
        if variable in ('alpha',):
            steps = [math.radians(h) for h in cfg.alpha_steps_deg]
        elif variable == 'beta':
            steps = [math.radians(h) for h in cfg.beta_steps_deg]
        elif variable == 'u_hat':
            steps = list(cfg.u_hat_steps)
        elif variable in RATES:
            steps = list(cfg.rate_hat_steps)
        else:
            steps = [math.radians(h) for h in cfg.control_steps_deg]
        record = dict(steps=steps, unit='per rad' if variable in ('alpha', 'beta') or variable.startswith(
            'control:') else 'per unit nondimensional variable')
        estimates = []
        try:
            for h in steps:
                plus = _perturb(s0, variable, h, speed=speed, span=span, chord=chord)
                minus = _perturb(s0, variable, -h, speed=speed, span=span, chord=chord)
                if variable.startswith('control:'):
                    name = variable.split(':', 1)[1]
                    if any(abs(x['controls'][name]) > limits[name] for x in (plus, minus)) or (
                            feasible is not None and not all(feasible(x['controls']) for x in (plus, minus))):
                        raise _ControlLimit(math.degrees(h))
                estimates.append((coefficients(model, plus, **kw)-coefficients(model, minus, **kw))/(2*h))
        except UnsupportedContribution as exc:
            results[variable] = dict(record, state='unsupported', value=None,
                unsupported=exc.record.get('unsupported'), missing=exc.record.get('missing'))
            continue
        except PolarCoverageError as exc:
            results[variable] = dict(record, state='step_leaves_coverage', value=None,
                failed_step=steps[len(estimates)], reason=str(exc)[:300])
            continue
        except _ControlLimit as exc:
            results[variable] = dict(record, state='step_exceeds_control_limit', value=None,
                failed_step_deg=exc.args[0])
            continue
        estimates = np.array(estimates)
        last, previous = estimates[-1], estimates[-2]
        excess = np.abs(last-previous)-(cfg.rtol*np.abs(last)+cfg.atol)
        ratio = None
        if len(estimates) >= 3:
            # Observed central-difference error ratio for the leading coefficient change.
            d1, d2 = np.abs(estimates[-3]-estimates[-2]), np.abs(estimates[-2]-estimates[-1])
            mask = d2 > cfg.atol
            ratio = float(np.median(d1[mask]/d2[mask])) if mask.any() else None
        richardson = (4*last-previous)/3
        state = 'converged' if float(np.max(excess)) <= 0 else 'not_converged'
        results[variable] = dict(record, state=state,
            value=dict(zip(COEFFICIENTS, last.tolist())) if state == 'converged' else None,
            estimates=[dict(zip(COEFFICIENTS, e.tolist())) for e in estimates],
            richardson=dict(zip(COEFFICIENTS, richardson.tolist())), max_excess=float(np.max(excess)),
            difference_ratio=ratio)
    complete = all(r['state'] == 'converged' for r in results.values()) and \
        set(requested) >= {*STATIC, *RATES, *('control:'+c for c in controls)}
    record = dict(scope='nondimensional_stability_and_control_derivatives',
        revision_sha256=revision, equilibrium=dict(flight_state=s0, mass_properties_sha256=digest(mass)),
        reference=dict(area_m2=area, span_m=span, chord_m=chord, density_kg_m3=rho, q0_pa=q0),
        settings=cfg.model_dump(mode='json'), conventions=__doc__.split('\n\n')[1].strip(),
        coefficients_at_equilibrium=dict(zip(COEFFICIENTS, base_values.tolist())), derivatives=results,
        complete=complete, missing_or_failed=sorted(k for k, r in results.items() if r['state'] != 'converged'),
        state='complete' if complete else 'incomplete', qualified_aircraft=False, production_admission=False)
    record['record_sha256'] = digest(record)
    return record


class _ControlLimit(Exception):
    pass
