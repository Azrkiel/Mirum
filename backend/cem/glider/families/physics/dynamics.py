"""Full-inertia rigid-body linearization, modes and control limits.

Nonlinear 6-DOF rigid-body equations in standard body axes B (x forward,
y starboard, z down) about the frozen CG, with the complete inertia tensor
including all products of inertia:

    m (v_dot + w x v) = F_aero + W            (velocity v = [u, v, w], m/s)
    I w_dot + w x (I w) = M_aero               (rates w = [p, q, r], rad/s)
    phi_dot = p + (q sin phi + r cos phi) tan theta
    theta_dot = q cos phi - r sin phi

State x = [u, v, w, p, q, r, phi, theta]; heading and position are ignorable.
Aerodynamic contributions come from the same revision-bound ledger as trim,
so a missing contribution (including missing rate dependence) makes the whole
linearization ``unsupported``. Jacobians are central differences at two step
sizes; disagreement beyond tolerance is reported, not averaged away. Control
perturbations never exceed the declared physical limits.

Nothing here establishes actual-family stability or authority; fixture and
screening evidence classes are carried through unchanged.
"""
import math

import numpy as np

from cem.aero.polars import PolarCoverageError
from cem.core import digest
from . import frames, wrench
from .trim import UnsupportedContribution, _mass_evidence

STATES = ('u', 'v', 'w', 'p', 'q', 'r', 'phi', 'theta')
LONGITUDINAL = (0, 2, 4, 7)
LATERAL = (1, 3, 5, 6)


def _flight(x):
    u, v, w = x[:3]
    speed = float(np.linalg.norm(x[:3]))
    if speed <= 0:
        raise ValueError('Zero airspeed state')
    return speed, math.degrees(math.atan2(w, u)), math.degrees(math.asin(max(-1., min(1., v/speed))))


def derivative(x, controls, *, model, mass, revision, required, g_m_s2=frames.STANDARD_GRAVITY_M_S2):
    """State derivative of the nonlinear equations (requires complete ledger)."""
    x = np.asarray(x, dtype=float)
    speed, alpha, beta = _flight(x)
    p, q, r, phi, theta = x[3:]
    state = dict(speed_m_s=speed, alpha_deg=alpha, beta_deg=beta, pitch_deg=math.degrees(theta),
                 bank_deg=math.degrees(phi), p_rad_s=float(p), q_rad_s=float(q), r_rad_s=float(r),
                 controls=dict(controls))
    contributions = list(model(state))
    contributions.append(wrench.gravity(revision, mass['mass_kg'], mass['cg_m'], state['bank_deg'],
                                        state['pitch_deg'], evidence=_mass_evidence(mass), g_m_s2=g_m_s2))
    total = wrench.resultant(contributions, required=(*required, 'gravity'), revision=revision,
                             about_m=mass['cg_m'])
    if total['state'] != 'complete':
        raise UnsupportedContribution(total)
    force = frames.geometry_to_body(total['force_n'])
    moment = frames.geometry_to_body(total['moment_nm'])
    inertia = np.asarray(mass['inertia_body_axes_kg_m2'])
    velocity, omega = x[:3], x[3:6]
    vdot = force/mass['mass_kg']-np.cross(omega, velocity)
    wdot = np.linalg.solve(inertia, moment-np.cross(omega, inertia@omega))
    out = np.concatenate([vdot, wdot, [p+(q*math.sin(phi)+r*math.cos(phi))*math.tan(theta),
                                       q*math.cos(phi)-r*math.sin(phi)]])
    if not np.isfinite(out).all():
        raise ValueError('Nonfinite rigid-body derivative')
    return out


def trim_state(trim):
    s = trim['flight_state']
    a, b = math.radians(s['alpha_deg']), math.radians(s['beta_deg'])
    v = s['speed_m_s']
    return np.array([v*math.cos(a)*math.cos(b), v*math.sin(b), v*math.sin(a)*math.cos(b),
                     0., 0., 0., math.radians(s['bank_deg']), math.radians(s['pitch_deg'])])


def _jacobian(function, x0, steps):
    columns = []
    for j, h in enumerate(steps):
        e = np.zeros(len(x0)); e[j] = h
        columns.append((function(x0+e)-function(x0-e))/(2*h))
    return np.column_stack(columns)


def linearize(trim, *, model, mass, required, control_limits, reference_span_m, reference_chord_m,
              relative_step=1e-3, control_step_deg=.5, rtol=1e-4, atol=1e-7, equilibrium_tolerance=1e-6):
    """A, B matrices about an established trim, with step-convergence checks."""
    base = dict(scope='rigid_body_small_perturbation_dynamics', revision_sha256=trim['revision_sha256'],
        mass_properties_sha256=digest(mass), axes=frames.AXES['body'], states=list(STATES),
        units='u,v,w m/s; p,q,r rad/s; phi,theta rad; control columns per rad',
        production_admission=False, qualified_aircraft=False, qualified_control_authority=False)
    if trim.get('state') != 'trimmed':
        return dict(base, state='unsupported', reason='Linearization requires an established trim')
    if trim['mass_properties_sha256'] != digest(mass):
        raise ValueError('Mass properties differ from the trim revision')
    revision = trim['revision_sha256']
    controls = {k: float(v) for k, v in trim['flight_state']['controls'].items()}
    limits = {c['name']: c['limit_deg'] for c in control_limits}
    if set(limits) != set(controls):
        raise ValueError('Explicit limit required for every installed control')
    x0 = trim_state(trim)
    speed = trim['flight_state']['speed_m_s']
    f = lambda x, c=controls: derivative(x, c, model=model, mass=mass, revision=revision, required=required)
    try:
        f0 = f(x0)
        scale = np.array([speed]*3+[2*speed/reference_span_m, 2*speed/reference_chord_m,
                                     2*speed/reference_span_m, 1., 1.])
        if np.max(np.abs(f0)/np.maximum(scale, 1.)) > equilibrium_tolerance:
            return dict(base, state='not_an_equilibrium', residual=f0.tolist())
        steps = relative_step*scale
        coarse, fine = _jacobian(f, x0, steps), _jacobian(f, x0, steps/2)
        cnames = sorted(controls)
        cb, fb = [], []
        for name in cnames:
            if abs(controls[name])+control_step_deg > limits[name]:
                return dict(base, state='control_at_limit', control=name,
                            reason='Derivative step would exceed the physical control limit')
            for h, out in ((control_step_deg, cb), (control_step_deg/2, fb)):
                plus = f(x0, dict(controls, **{name: controls[name]+h}))
                minus = f(x0, dict(controls, **{name: controls[name]-h}))
                out.append((plus-minus)/(2*math.radians(h)))
    except UnsupportedContribution as exc:
        return dict(base, state='unsupported', resultant=exc.record)
    except PolarCoverageError as exc:
        return dict(base, state='out_of_coverage', reason=str(exc))
    B = np.column_stack(fb) if fb else np.zeros((8, 0))
    Bc = np.column_stack(cb) if cb else np.zeros((8, 0))
    difference = max(float(np.max(np.abs(fine-coarse)-(rtol*np.abs(fine)+atol))),
                     float(np.max(np.abs(B-Bc)-(rtol*np.abs(B)+atol))) if fb else -1.)
    if difference > 0 or not np.isfinite(fine).all():
        return dict(base, state='derivative_not_converged', excess=difference)
    modes = classify(fine, speed=speed, span=reference_span_m, chord=reference_chord_m)
    unstable = [m['mode'] for m in modes if m['real_per_s'] >= 0]
    evidence = sorted({c['evidence'] for c in trim['contributions'] if c['state'] == 'supported'})
    return dict(base, state='computed', A=fine.tolist(), B=B.tolist(), control_order=cnames,
        control_limits_deg=limits, trim_controls_deg=controls, step_convergence_excess=difference,
        inertia_body_axes_kg_m2=mass['inertia_body_axes_kg_m2'], modes=modes,
        unstable_modes=unstable, stability='all_modes_stable' if not unstable else 'unstable_mode_present',
        evidence=evidence, limitation='Linear rigid-body modes about one trim; no nonlinear stall, '
        'aeroelastic, actuator or gust dynamics.')


def classify(A, *, speed, span, chord):
    values, vectors = np.linalg.eig(np.asarray(A))
    weights = np.array([1/speed]*3+[span/(2*speed), chord/(2*speed), span/(2*speed), 1., 1.])
    records = []
    for value, vector in zip(values, vectors.T):
        energy = np.abs(vector*weights)**2
        fraction = float(energy[list(LONGITUDINAL)].sum()/energy.sum())
        records.append(dict(value=complex(value), longitudinal_fraction=fraction))
    out = []
    for group, kind in ((LONGITUDINAL, 'longitudinal'), (LATERAL, 'lateral')):
        members = [r for r in records if (r['longitudinal_fraction'] >= .95) == (kind == 'longitudinal')
                   and (r['longitudinal_fraction'] >= .95 or r['longitudinal_fraction'] <= .05)]
        oscillatory = sorted([r for r in members if r['value'].imag > 1e-9], key=lambda r: abs(r['value']))
        real = sorted([r for r in members if abs(r['value'].imag) <= 1e-9], key=lambda r: abs(r['value']))
        if kind == 'longitudinal':
            names = {id(oscillatory[-1]): 'short_period'} if oscillatory else {}
            if len(oscillatory) >= 2:
                names[id(oscillatory[0])] = 'phugoid'
            for r in real:
                names[id(r)] = 'longitudinal_real'
        else:
            names = {id(r): 'dutch_roll' for r in oscillatory}
            if real:
                names[id(real[-1])] = 'roll'
                if len(real) > 1:
                    names[id(real[0])] = 'spiral'
        for r in oscillatory+real:
            out.append(_mode(names.get(id(r), kind+'_unclassified'), r))
    for r in records:
        if .05 < r['longitudinal_fraction'] < .95 and r['value'].imag >= -1e-9:
            out.append(_mode('coupled_unclassified', r))
    return out


def _mode(name, record):
    z = record['value']
    magnitude = abs(z)
    return dict(mode=name, real_per_s=float(z.real), imag_rad_s=float(z.imag),
        natural_frequency_rad_s=float(magnitude), damping_ratio=float(-z.real/magnitude) if magnitude else None,
        time_to_double_or_half_s=float(math.log(2)/abs(z.real)) if z.real else None,
        longitudinal_fraction=record['longitudinal_fraction'])


def control_authority(trim, *, model, mass, required, control_limits, requirement=None):
    """Moment increments at the physical limits, evaluated (never extrapolated).

    ``requirement`` maps body-axis names ``L``, ``M``, ``N`` to required
    magnitudes in N m. Without it, authority is explicitly unassessed.
    """
    if trim.get('state') != 'trimmed':
        return dict(state='unsupported', reason='Authority requires an established trim')
    revision = trim['revision_sha256']
    controls = {k: float(v) for k, v in trim['flight_state']['controls'].items()}
    s = trim['flight_state']
    def body_moment(states):
        point = dict(s, controls=states)
        items = list(model(point))+[wrench.gravity(revision, mass['mass_kg'], mass['cg_m'], s['bank_deg'],
                                                   s['pitch_deg'], evidence=_mass_evidence(mass))]
        total = wrench.resultant(items, required=(*required, 'gravity'), revision=revision, about_m=mass['cg_m'])
        if total['state'] != 'complete':
            raise UnsupportedContribution(total)
        return frames.geometry_to_body(total['moment_nm'])
    channels = {}
    try:
        neutral = body_moment(controls)
        for c in control_limits:
            name, limit = c['name'], c['limit_deg']
            values = {}
            for sign in (-1, 1):
                values['+' if sign > 0 else '-'] = (body_moment(dict(controls, **{name: sign*limit}))-neutral).tolist()
            channels[name] = dict(limit_deg=limit, moment_increment_body_nm=values)
    except UnsupportedContribution as exc:
        return dict(state='unsupported', resultant=exc.record)
    except PolarCoverageError as exc:
        return dict(state='unsupported_out_of_coverage_at_limit', reason=str(exc))
    report = dict(scope='fixed_trim_state_control_moment_at_physical_limits', channels=channels,
        axes=frames.AXES['body'], qualified_control_authority=False)
    if requirement is None:
        return dict(report, state='unassessed_missing_requirement')
    axes = dict(L=0, M=1, N=2)
    shortfall = []
    for axis, needed in requirement.items():
        if axis not in axes or not needed > 0:
            raise ValueError('Requirement axes are L, M, N with positive magnitudes')
        values = [v[k][axes[axis]] for v in (ch['moment_increment_body_nm'] for ch in channels.values())
                  for k in ('+', '-')]
        # Authority is required in both directions about each listed axis.
        if max(values, default=0.) < needed or -min(values, default=0.) < needed:
            shortfall.append(axis)
    return dict(report, state='requirement_met_at_fixed_state' if not shortfall else 'insufficient_authority',
                shortfall=shortfall, requirement_nm=dict(requirement))
