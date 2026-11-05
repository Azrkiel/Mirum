"""Bounded steady force/moment equilibrium with frozen geometry, hardware and mass.

Scope: steady, unaccelerated, non-rotating flight (p = q = r = 0) in uniform
gravity, without propulsion unless a declared contribution supplies it.
All six residuals are evaluated in geometry axes about the frozen CG:

    sum(F_aero) + W = 0,  sum(M_aero about CG) = 0   (gravity acts at the CG)

Only justified state/control variables may be solved for: airspeed, angle of
attack, sideslip, pitch and bank attitude, and declared control deflections.
Geometry, hardware, mass properties and CG are inputs, never variables.
Equations not solved for must still close within tolerance at the solution.

Every required aerodynamic contribution must be supported at every evaluated
state; a missing one yields ``unsupported`` and no equilibrium. Trial states
outside the declared bounds or the aerodynamic evidence domain are rejected
without substituted coefficients.
"""
import itertools
import math

import numpy as np
from pydantic import Field, ValidationError, model_validator

from cem.aero.numerics import NumericalConvergenceError, bounded_newton
from cem.aero.polars import PolarCoverageError
from cem.core import StrictModel, canonical_json, digest
from . import frames, wrench

EQUATIONS = ('Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz')
STATE = ('speed_m_s', 'alpha_deg', 'beta_deg', 'pitch_deg', 'bank_deg')
BOUNDS = dict(speed_m_s=(.5, 60.), alpha_deg=(-20., 25.), beta_deg=(-20., 20.),
              pitch_deg=(-80., 80.), bank_deg=(-80., 80.))


class UnsupportedContribution(ValueError):
    """A required force/moment contribution has no supported evidence."""

    def __init__(self, record):
        super().__init__('Required force/moment contribution unsupported')
        self.record = record


class Variable(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    lower: float
    upper: float
    initial: float

    @model_validator(mode='after')
    def bounded(self):
        if not self.lower < self.initial < self.upper:
            raise ValueError('Initial value must lie strictly inside declared bounds')
        return self


class ControlLimit(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    limit_deg: float = Field(gt=0, le=45)
    # Required remaining deflection for manoeuvre/disturbance authority. None
    # means the authority requirement is missing and cannot be assessed.
    reserve_deg: float | None = Field(None, ge=0)


class Problem(StrictModel):
    revision_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    required: tuple[str, ...] = Field(min_length=1)
    variables: tuple[Variable, ...] = Field(min_length=1, max_length=6)
    equations: tuple[str, ...]
    fixed: dict[str, float]
    controls: tuple[ControlLimit, ...]
    reference_chord_m: float = Field(gt=0)
    reference_span_m: float = Field(gt=0)
    gravity_m_s2: float = Field(frames.STANDARD_GRAVITY_M_S2, gt=0)
    tolerance: float = Field(1e-8, gt=0, le=1e-4)
    closure_tolerance: float = Field(1e-6, gt=0, le=1e-3)
    grid_points: int = Field(5, ge=3, le=9)

    @model_validator(mode='after')
    def justified(self):
        names = [v.name for v in self.variables]
        controls = {c.name for c in self.controls}
        if len(set(names)) != len(names) or len(set(self.equations)) != len(self.equations):
            raise ValueError('Duplicate trim variable or equation')
        if any(e not in EQUATIONS for e in self.equations) or len(self.equations) != len(names):
            raise ValueError('Solve exactly one listed residual equation per variable')
        for v in self.variables:
            if v.name.startswith('control:'):
                control = v.name.split(':', 1)[1]
                if control not in controls:
                    raise ValueError('Trim control is not a declared installed control')
                limit = next(c.limit_deg for c in self.controls if c.name == control)
                if v.lower < -limit or v.upper > limit:
                    raise ValueError('Control variable bounds exceed the physical limit')
            elif v.name in STATE:
                lo, hi = BOUNDS[v.name]
                if v.lower < lo or v.upper > hi:
                    raise ValueError('State variable bounds exceed the supported envelope')
            else:
                raise ValueError('Unjustified trim variable: '+v.name)
        fixed_controls = {k.split(':', 1)[1] for k in self.fixed if k.startswith('control:')}
        solved_controls = {v.name.split(':', 1)[1] for v in self.variables if v.name.startswith('control:')}
        if fixed_controls & solved_controls or fixed_controls | solved_controls != controls:
            raise ValueError('Every declared control must be either fixed or solved, exactly once')
        state = set(self.fixed)-{'control:'+c for c in fixed_controls}
        if state | (set(names)-{'control:'+c for c in solved_controls}) != set(STATE) or state & set(names):
            raise ValueError('Every flight state must be explicitly fixed or solved')
        for name in state:
            lo, hi = BOUNDS[name]
            if not lo <= self.fixed[name] <= hi:
                raise ValueError('Fixed flight state outside supported envelope')
        for name in fixed_controls:
            limit = next(c.limit_deg for c in self.controls if c.name == name)
            if abs(self.fixed['control:'+name]) > limit:
                raise ValueError('Fixed control exceeds its physical limit')
        return self


def _state(problem, values):
    merged = dict(problem.fixed)
    merged.update({v.name: float(x) for v, x in zip(problem.variables, values)})
    state = {k: merged[k] for k in STATE}
    state.update(p_rad_s=0., q_rad_s=0., r_rad_s=0.,
                 controls={c.name: merged['control:'+c.name] for c in problem.controls})
    return state


def _residual(problem, model, mass, state):
    contributions = list(model(state))
    contributions.append(wrench.gravity(problem.revision_sha256, mass['mass_kg'], mass['cg_m'],
        state['bank_deg'], state['pitch_deg'], evidence=_mass_evidence(mass), g_m_s2=problem.gravity_m_s2))
    total = wrench.resultant(contributions, required=(*problem.required, 'gravity'),
                             revision=problem.revision_sha256, about_m=mass['cg_m'])
    if total['state'] != 'complete':
        raise UnsupportedContribution(total)
    weight = mass['mass_kg']*problem.gravity_m_s2
    scale = np.array([weight]*3+[weight*problem.reference_span_m, weight*problem.reference_chord_m,
                                 weight*problem.reference_span_m])
    vector = np.concatenate([total['force_n'], total['moment_nm']])/scale
    if not np.isfinite(vector).all():
        raise ValueError('Nonfinite trim residual')
    return vector, total, contributions


def _mass_evidence(mass):
    if mass.get('state') == 'measured_delivered_components':
        return 'measured'
    if mass.get('state') == 'reference_rigid_body_not_aircraft':
        return 'analytic_fixture'
    return 'calculated_screening'


def solve(problem, model, mass):
    """Return an explicit outcome; never a substituted or relabelled equilibrium."""
    p = problem if isinstance(problem, Problem) else Problem.model_validate_json(canonical_json(problem))
    frozen = digest(mass)
    index = [EQUATIONS.index(e) for e in p.equations]
    lower = np.array([v.lower for v in p.variables]); upper = np.array([v.upper for v in p.variables])
    scale = np.maximum(upper-lower, 1e-9)
    trace = dict(evaluations=0, rejected_states=0)
    base = dict(scope='steady_unaccelerated_force_moment_equilibrium',
        revision_sha256=p.revision_sha256, mass_properties_sha256=frozen, problem=p.model_dump(mode='json'),
        axes=frames.AXES, residual_scaling='forces/W; Mx,Mz/(W b); My/(W c)', production_admission=False,
        qualified_aircraft=False, verified_gain=False, physical_construction=False)

    def covered(x):
        values = lower+x*scale
        if np.any(values < lower) or np.any(values > upper):
            trace['rejected_states'] += 1
            raise PolarCoverageError('Trim trial outside declared variable bounds')
        trace['evaluations'] += 1
        try:
            return _residual(p, model, mass, _state(p, values))[0][index]
        except UnsupportedContribution as exc:
            trace['rejected_states'] += 1
            raise PolarCoverageError('Required contribution unsupported at trial state') from exc

    initial = np.array([(v.initial-v.lower)/(v.upper-v.lower) for v in p.variables])
    try:
        _residual(p, model, mass, _state(p, lower+initial*scale))
    except UnsupportedContribution as exc:
        return dict(base, state='unsupported', resultant=exc.record, trace=trace)
    except PolarCoverageError as exc:
        return dict(base, state='out_of_coverage', reason=str(exc), trace=trace)
    except (ValidationError, ValueError) as exc:
        return dict(base, state='nonfinite_or_invalid', reason=str(exc)[:600], trace=trace)
    try:
        fit = bounded_newton(covered, initial, tolerance=p.tolerance, iterations=60)
    except NumericalConvergenceError as exc:
        return dict(base, **_diagnose(p, model, mass, lower, upper, index), solver_failure=str(exc), trace=trace)
    except (ValidationError, ValueError) as exc:
        return dict(base, state='nonfinite_or_invalid', reason=str(exc)[:600], trace=trace)
    values = lower+fit['value']*scale
    state = _state(p, values)
    residual, total, contributions = _residual(p, model, mass, state)
    if digest(mass) != frozen:
        raise ValueError('Mass properties changed during trim')
    unsolved = {e: float(residual[i]) for i, e in enumerate(EQUATIONS) if e not in p.equations}
    lift = frames.aerodynamic_components(np.asarray(total['force_n'])-np.asarray(
        frames.gravity_force_geometry(mass['mass_kg'], state['bank_deg'], state['pitch_deg'], p.gravity_m_s2)),
        state['alpha_deg'], state['beta_deg'])
    controls, saturated, unassessed = {}, [], []
    for c in p.controls:
        value = state['controls'][c.name]
        controls[c.name] = dict(deflection_deg=value, limit_deg=c.limit_deg, reserve_deg=c.reserve_deg,
            remaining_deg=c.limit_deg-abs(value))
        if c.reserve_deg is None:
            unassessed.append(c.name)
        elif c.limit_deg-abs(value) < c.reserve_deg:
            saturated.append(c.name)
    if any(abs(v) > p.closure_tolerance for v in unsolved.values()):
        outcome = 'unbalanced_unsolved_residuals'
    elif saturated:
        outcome = 'control_saturated'
    else:
        outcome = 'trimmed'
    return dict(base, state=outcome, solution={v.name: float(x) for v, x in zip(p.variables, values)},
        flight_state=state, residual=dict(zip(EQUATIONS, residual.tolist())), unsolved_residuals=unsolved,
        flight_path_angle_deg=frames.flight_path_angle_deg(state['alpha_deg'], state['beta_deg'],
                                                           state['bank_deg'], state['pitch_deg']),
        aerodynamic_wind_components=lift, controls=controls, saturated_controls=saturated,
        authority=('unassessed_missing_requirement' if unassessed else 'reserve_checked_at_trim_only'),
        authority_unassessed=unassessed, resultant=total,
        contributions=[c.model_dump(mode='json') for c in contributions],
        solver=dict(method='covered-damped-Newton-v1', **{k: v for k, v in fit.items() if k != 'value'}),
        trace=trace)


def _diagnose(p, model, mass, lower, upper, index):
    """Sign-definite residual over the full declared grid, or no conclusion."""
    if p.grid_points**len(p.variables) > 729:
        return dict(state='no_equilibrium_established', diagnosis='grid_too_large')
    axes = [np.linspace(lo, hi, p.grid_points) for lo, hi in zip(lower, upper)]
    samples, uncovered = [], 0
    for point in itertools.product(*axes):
        try:
            samples.append(_residual(p, model, mass, _state(p, np.array(point)))[0][index])
        except (UnsupportedContribution, PolarCoverageError):
            uncovered += 1
    if uncovered or not samples:
        return dict(state='no_equilibrium_established', diagnosis='grid_not_fully_covered',
                    grid_uncovered=uncovered)
    samples = np.array(samples)
    definite = [p.equations[i] for i in range(len(index))
                if np.all(samples[:, i] > p.tolerance) or np.all(samples[:, i] < -p.tolerance)]
    if not definite:
        return dict(state='no_equilibrium_established', diagnosis='no_sign_definite_residual')
    controls = any(v.name.startswith('control:') for v in p.variables)
    moments = any(e.startswith('M') for e in definite)
    return dict(state='control_saturated' if controls and moments else 'untrimmable_on_declared_grid',
        diagnosis='sign_definite_residual_on_full_declared_grid', sign_definite_equations=definite,
        grid_points=p.grid_points, grid_samples=len(samples),
        limitation='Grid sign evidence, not a proof between samples; bounds and limits were not exceeded.')


def verify_equilibrium(*, revision_sha256, required, model, mass, flight_state, controls, reference_span_m,
                       reference_chord_m, gravity_m_s2=frames.STANDARD_GRAVITY_M_S2, tolerance=1e-8,
                       source='declared'):
    """Check a declared steady equilibrium instead of solving for one.

    Used for published reference states (whose speed may lie outside the glider
    trim envelope) and for replaying a recorded trim. All six scaled residuals
    must close; a label alone never establishes equilibrium. The returned
    record has the same shape as a ``solve`` result.
    """
    state = {k: float(flight_state[k]) for k in STATE}
    state.update(p_rad_s=0., q_rad_s=0., r_rad_s=0., controls={k: float(v) for k, v in controls.items()})
    problem = dict(revision_sha256=revision_sha256, required=list(required), gravity_m_s2=gravity_m_s2,
                   reference_chord_m=reference_chord_m, reference_span_m=reference_span_m,
                   closure_tolerance=tolerance)
    shim = type('Declared', (), dict(revision_sha256=revision_sha256, required=tuple(required),
        gravity_m_s2=gravity_m_s2, reference_span_m=reference_span_m, reference_chord_m=reference_chord_m))
    base = dict(scope='declared_steady_equilibrium_check', source=source, revision_sha256=revision_sha256,
        mass_properties_sha256=digest(mass), problem=problem, axes=frames.AXES, production_admission=False,
        qualified_aircraft=False, verified_gain=False, physical_construction=False)
    try:
        residual, total, contributions = _residual(shim, model, mass, state)
    except UnsupportedContribution as exc:
        return dict(base, state='unsupported', resultant=exc.record)
    except PolarCoverageError as exc:
        return dict(base, state='out_of_coverage', reason=str(exc))
    worst = float(np.max(np.abs(residual)))
    if worst > tolerance:
        return dict(base, state='not_an_equilibrium', residual=dict(zip(EQUATIONS, residual.tolist())))
    return dict(base, state='trimmed', flight_state=state, residual=dict(zip(EQUATIONS, residual.tolist())),
        unsolved_residuals={}, flight_path_angle_deg=frames.flight_path_angle_deg(
            state['alpha_deg'], state['beta_deg'], state['bank_deg'], state['pitch_deg']),
        controls={k: dict(deflection_deg=v) for k, v in state['controls'].items()}, saturated_controls=[],
        authority='unassessed_missing_requirement', resultant=total,
        contributions=[c.model_dump(mode='json') for c in contributions])
