"""Dynamics, derivatives, mode requirements, authority and envelope qualification.

The published 747 case checks the equations against independently published
numbers. Linear fixtures check implementation paths only; none of these cases
qualifies an aircraft family, its stability or its authority.
"""
import copy
import math

import numpy as np
import pytest
from pydantic import ValidationError

from cem.aero.polars import PolarCoverageError
from cem.core import canonical_json, digest
from cem.glider.families import archive, build
from cem.glider.families.physics import (assessment, authority, derivatives, dynamics, envelope, fixtures,
                                         frames, interfaces, mass, qualification, stability, trim, wrench)
from tests.test_cem_family_physics import _mass_at, _problem

LIMITS = [dict(name='elevator', limit_deg=20.), dict(name='aileron', limit_deg=20.)]
FIXTURE_TOPOLOGY = dict(source='fixture', channels=[dict(name='elevator', role='pitch', limit_deg=20.),
                                                    dict(name='aileron', role='roll', limit_deg=20.)],
    segments=[dict(surface='tail', side=s, eta=(0., 1.), mixing={'elevator': 1.}, limit_deg=(-20., 20.),
                   hinge_chord_fractions={'elevator': .7}) for s in (-1, 1)] +
             [dict(surface='wing', side=s, eta=(.5, 1.), mixing={'aileron': float(s)}, limit_deg=(-20., 20.),
                   hinge_chord_fractions={'aileron': .75}) for s in (-1, 1)])


def _topo(revision, **changes):
    return authority.ControlTopology.model_validate_json(canonical_json(dict(FIXTURE_TOPOLOGY,
        revision_sha256=revision, **changes)))


def _good_fixture(**changes):
    return fixtures.LinearAircraft(**dict(dict(Cm0=0., Cma=-1.5, Cmq=-4.), **changes))


def _trimmed(fx, m=None):
    m = m or _mass_at()
    out = trim.solve(_problem(fx), fx, m)
    assert out['state'] == 'trimmed'
    return out, m


def _reference(fx):
    return dict(area_m2=fx.area_m2, span_m=fx.span_m, chord_m=fx.chord_m, density_kg_m3=fx.density_kg_m3)


# --------------------------------------------------------------- 747 reference
def _747(**kw):
    r = fixtures.published_747()
    eq = trim.verify_equilibrium(revision_sha256=r['model'].revision, required=('reference_aero',),
        model=r['model'], mass=r['mass'], flight_state=r['flight_state'], controls={}, reference_span_m=59.64,
        reference_chord_m=8.32, gravity_m_s2=r['g_m_s2'], source='published reference')
    assert eq['state'] == 'trimmed'
    out = dynamics.linearize(eq, model=r['model'], mass=r['mass'], required=('reference_aero',), control_limits=[],
        reference_span_m=59.64, reference_chord_m=8.32, **dict(dict(unsteady=r['unsteady']), **kw))
    return r, eq, out


def test_published_747_longitudinal_and_lateral_matrices_and_modes():
    r, _, out = _747()
    published = r['published']
    assert out['state'] == 'computed' and out['unsteady_aerodynamics'] == 'included' and out['decoupled']
    A = np.asarray(out['A'])
    lon = A[np.ix_([0, 2, 4, 7], [0, 2, 4, 7])]
    # Printed to four decimals (and 235.8928 to four decimals).
    assert lon == pytest.approx(np.asarray(published['A_longitudinal']), abs=5.1e-5)
    modes = {m['mode']: m for m in out['modes']}
    for name in ('short_period', 'phugoid', 'dutch_roll'):
        ref = published[name]
        assert modes[name]['real_per_s'] == pytest.approx(ref['real'], abs=5.1e-5)
        assert modes[name]['imag_rad_s'] == pytest.approx(ref['imag'], abs=5.1e-5)
    for name in ('short_period', 'phugoid'):
        assert modes[name]['natural_frequency_rad_s'] == pytest.approx(published[name]['natural_frequency'],
                                                                      abs=5.1e-4)
        assert modes[name]['damping_ratio'] == pytest.approx(published[name]['damping_ratio'], abs=5.1e-4)
    assert modes['roll']['real_per_s'] == pytest.approx(published['roll']['real'], abs=5.1e-5)
    assert modes['spiral']['real_per_s'] == pytest.approx(published['spiral']['real'], abs=5.1e-5)
    # The lateral block is exactly decoupled for a symmetric aircraft at beta = phi = 0.
    assert out['longitudinal_lateral_coupling'] < 1e-9


def test_747_without_unsteady_terms_is_quasi_steady_and_blocks_requirements():
    r, _, out = _747(unsteady=None)
    assert out['unsteady_aerodynamics'] == 'unsupported_missing_unsteady_derivatives'
    sp = next(m for m in out['modes'] if m['mode'] == 'short_period')
    # Z_wdot/M_wdot materially change the short period: static/rate terms do not replace them.
    assert abs(sp['real_per_s']-r['published']['short_period']['real']) > 1e-3
    req = stability.StabilityRequirement(derivation='analytic_test', artifact_sha256=digest('747'), source='t',
        short_period_min_damping=.1, phugoid_min_damping=.01, dutch_roll_min_damping=.01,
        roll_max_time_constant_s=5., spiral_min_time_to_double_s=10.)
    assert stability.assess(out, req)['state'] == 'blocked_missing_unsteady_terms'
    _, _, full = _747()
    assert stability.assess(full, req)['state'] == 'requirements_met'


def test_747_detects_wrong_frame_sign_gravity_and_inertia_conventions():
    r, eq, good = _747()
    # Product of inertia with the wrong sign changes the lateral roots measurably.
    i = r['model'].reference['inertia']
    flipped = mass.reference_rigid_body(r['model'].mass_kg, (0., 0., 0.),
        [[i['Ix'], 0., i['Izx']], [0., i['Iy'], 0.], [i['Izx'], 0., i['Iz']]], source='sign error')
    eq2 = trim.verify_equilibrium(revision_sha256=r['model'].revision, required=('reference_aero',),
        model=r['model'], mass=flipped, flight_state=r['flight_state'], controls={}, reference_span_m=59.64,
        reference_chord_m=8.32, gravity_m_s2=r['g_m_s2'])
    bad = dynamics.linearize(eq2, model=r['model'], mass=flipped, required=('reference_aero',),
        control_limits=[], reference_span_m=59.64, reference_chord_m=8.32, unsteady=r['unsteady'])
    dr = next(m for m in bad['modes'] if m['mode'] == 'dutch_roll')
    assert abs(dr['real_per_s']-r['published']['dutch_roll']['real']) > 1e-3
    # Gravity inconsistent with the published weight is not an equilibrium.
    wrong_g = trim.verify_equilibrium(revision_sha256=r['model'].revision, required=('reference_aero',),
        model=r['model'], mass=r['mass'], flight_state=r['flight_state'], controls={}, reference_span_m=59.64,
        reference_chord_m=8.32, gravity_m_s2=frames.STANDARD_GRAVITY_M_S2)
    assert wrong_g['state'] == 'not_an_equilibrium'
    # A model that reports body-axis vectors as geometry axes (missing 180-degree
    # rotation) cannot even balance gravity: the equilibrium check rejects it.
    class BodyAsGeometry(fixtures.DimensionalAircraft):
        def __call__(self, state):
            c = super().__call__(state)[0]
            return [c.model_copy(update=dict(force_n=tuple(frames.geometry_to_body(c.force_n)),
                                             moment_nm=tuple(frames.geometry_to_body(c.moment_nm))))]
    unrotated = BodyAsGeometry()
    assert trim.verify_equilibrium(revision_sha256=unrotated.revision, required=('reference_aero',),
        model=unrotated, mass=r['mass'], flight_state=r['flight_state'], controls={}, reference_span_m=59.64,
        reference_chord_m=8.32, gravity_m_s2=r['g_m_s2'])['state'] == 'not_an_equilibrium'

    # Converting forces but not moments (roll/yaw pseudovector signs) passes trim
    # yet reverses lateral damping: the published lateral roots are not reproduced.
    class MomentSignError(fixtures.DimensionalAircraft):
        def __call__(self, state):
            c = super().__call__(state)[0]
            return [c.model_copy(update=dict(moment_nm=tuple(frames.geometry_to_body(c.moment_nm))))]
    wrong = MomentSignError()
    eq3 = trim.verify_equilibrium(revision_sha256=wrong.revision, required=('reference_aero',), model=wrong,
        mass=r['mass'], flight_state=r['flight_state'], controls={}, reference_span_m=59.64,
        reference_chord_m=8.32, gravity_m_s2=r['g_m_s2'])
    assert eq3['state'] == 'trimmed'
    swapped = dynamics.linearize(eq3, model=wrong, mass=r['mass'], required=('reference_aero',), control_limits=[],
        reference_span_m=59.64, reference_chord_m=8.32, unsteady=r['unsteady'].model_copy(
            update=dict(revision_sha256=wrong.revision)))
    roots = np.sort_complex(np.linalg.eigvals(np.asarray(swapped['A'])[np.ix_([1, 3, 5, 6], [1, 3, 5, 6])]))
    assert max(r_.real for r_ in roots) > 0  # roll damping sign reversed: divergent


def test_unsteady_records_are_explicit_revision_bound_and_domain_limited():
    r, eq, _ = _747()
    with pytest.raises(ValidationError, match='explicit'):
        dynamics.UnsteadyDerivatives(revision_sha256=r['model'].revision, evidence='published_reference',
            source='s', artifact_sha256=digest('a'), derivatives=dict(Z=(0., 0., 1.)),
            speed_range_m_s=(1., 2.), alpha_range_deg=(0., 0.))
    other = r['unsteady'].model_copy(update=dict(revision_sha256=digest('other')))
    with pytest.raises(ValueError, match='another revision'):
        dynamics.linearize(eq, model=r['model'], mass=r['mass'], required=('reference_aero',), control_limits=[],
                           reference_span_m=59.64, reference_chord_m=8.32, unsteady=other)
    outside = r['unsteady'].model_copy(update=dict(speed_range_m_s=(100., 200.)))
    out = dynamics.linearize(eq, model=r['model'], mass=r['mass'], required=('reference_aero',),
        control_limits=[], reference_span_m=59.64, reference_chord_m=8.32, unsteady=outside)
    assert out['unsteady_aerodynamics'] == 'unsupported_outside_unsteady_domain'


def test_nondimensional_unsteady_chain_rule_matches_dimensional_definition():
    rev = digest('u')
    u = dynamics.unsteady_from_coefficients(revision_sha256=rev, evidence='analytic_fixture', source='t',
        artifact_sha256=rev, density_kg_m3=1.2, speed_m_s=8., alpha_deg=0., area_m2=.3, chord_m=.2, span_m=1.5,
        CL_alphadot=2., Cm_alphadot=-5., CY_betadot=.1, Cl_betadot=.02, Cn_betadot=-.03,
        speed_range_m_s=(8., 8.), alpha_range_deg=(0., 0.))
    q, V = .5*1.2*64, 8.
    d = u.matrix()
    # alpha = 0: lift along -z_B, so Z_wdot = -q S CL_alphadot c/(2 V^2).
    assert d[2, 2] == pytest.approx(-q*.3*2.*.2/(2*V*V))
    assert d[4, 2] == pytest.approx(q*.3*.2*-5.*.2/(2*V*V))
    assert d[1, 1] == pytest.approx(q*.3*.1*1.5/(2*V*V))
    assert d[5, 1] == pytest.approx(q*.3*1.5*-.03*1.5/(2*V*V))
    assert d[0, 2] == pytest.approx(0., abs=1e-15)


# ------------------------------------------------------- analytical limits
class _Balanced:
    """Aerodynamic force exactly cancelling gravity at the CG; zero moment."""
    def __init__(self, revision, weight_n, force_scale=1.):
        self.revision, self.weight, self.scale = revision, weight_n, force_scale

    def __call__(self, state):
        w = frames.gravity_force_geometry(self.weight/frames.STANDARD_GRAVITY_M_S2, state['bank_deg'],
                                          state['pitch_deg'])
        return [wrench.supported('aero', 'other', self.revision, -self.scale*w, (0., 0., 0.), (0., 0., 0.),
                                 evidence='analytic_fixture')]


def _body():
    tilt = np.array([[math.cos(.3), 0, math.sin(.3)], [0, 1, 0], [-math.sin(.3), 0, math.cos(.3)]])
    return mass.combine([mass.solid_box('b', .4, (0., 0., 0.), (.5, .2, .1), rotation=tilt,
                                        evidence='calculated_screening', source='t')])


def test_torque_free_rigid_body_conserves_momentum_and_energy_with_products_of_inertia():
    m = _body()
    rev = digest('torque-free')
    model = _Balanced(rev, m['mass_kg']*frames.STANDARD_GRAVITY_M_S2)
    I = np.asarray(m['inertia_body_axes_kg_m2'])
    assert abs(I[0, 2]) > 1e-4
    x = np.array([5., .2, .3, 1.3, -.7, 2.1, math.radians(10), math.radians(5)])
    xdot = dynamics.derivative(x, {}, model=model, mass=m, revision=rev, required=('aero',))
    omega, wdot = x[3:6], xdot[3:6]
    H = I@omega
    assert H@(I@wdot) == pytest.approx(0., abs=1e-12)   # |H| constant in body frame
    assert omega@(I@wdot) == pytest.approx(0., abs=1e-12)  # rotational energy constant
    # Pure roll moment couples into yaw through Jxz exactly as the closed form.
    class Roll(_Balanced):
        def __call__(self, state):
            base = super().__call__(state)[0]
            return [wrench.supported('aero', 'other', self.revision, base.force_n,
                                     frames.body_to_geometry([.01, 0., 0.]), (0., 0., 0.), evidence='analytic_fixture')]
    still = np.array([5., 0., 0., 0., 0., 0., 0., 0.])
    xr = dynamics.derivative(still, {}, model=Roll(rev, m['mass_kg']*frames.STANDARD_GRAVITY_M_S2), mass=m,
                             revision=rev, required=('aero',))
    jxz = -I[0, 2]
    det = I[0, 0]*I[2, 2]-jxz**2
    assert xr[3] == pytest.approx(.01*I[2, 2]/det)
    assert xr[5] == pytest.approx(.01*jxz/det)


def test_free_fall_and_euler_kinematics_match_independent_dcm_rates():
    m = _body()
    rev = digest('fall')
    zero = _Balanced(rev, m['mass_kg']*frames.STANDARD_GRAVITY_M_S2, force_scale=0.)
    phi, theta = math.radians(25), math.radians(-15)
    x = np.array([6., 0., 0., .4, -.3, .2, phi, theta])
    d = dynamics.derivative(x, {}, model=zero, mass=m, revision=rev, required=('aero',))
    g = frames.STANDARD_GRAVITY_M_S2
    omega = x[3:6]
    gravity = np.array([-g*math.sin(theta), g*math.cos(theta)*math.sin(phi), g*math.cos(theta)*math.cos(phi)])
    assert d[:3] == pytest.approx(gravity-np.cross(omega, x[:3]))
    # Independent check of the Euler-angle rates: dR_BE/dt = -[omega x] R_BE.
    psi_dot = (omega[1]*math.sin(phi)+omega[2]*math.cos(phi))/math.cos(theta)
    h = 1e-6
    R0 = frames.body_from_earth(math.degrees(phi), math.degrees(theta), 0.)
    R1 = frames.body_from_earth(math.degrees(phi+h*d[6]), math.degrees(theta+h*d[7]), math.degrees(h*psi_dot))
    skew = np.array([[0, -omega[2], omega[1]], [omega[2], 0, -omega[0]], [-omega[1], omega[0], 0]])
    assert (R1-R0)/h == pytest.approx(-skew@R0, abs=1e-5)


def test_invalid_altered_inertia_and_mismatched_trim_revisions_are_rejected():
    with pytest.raises(ValueError, match='triangle'):
        mass.reference_rigid_body(1., (0, 0, 0), np.diag([1., 1., 3.]), source='t')
    with pytest.raises(ValueError, match='positive definite'):
        mass.reference_rigid_body(1., (0, 0, 0), np.diag([1., -1., 1.]), source='t')
    with pytest.raises(ValueError, match='symmetric'):
        mass.reference_rigid_body(1., (0, 0, 0), [[1., .1, 0.], [0., 1., 0.], [0., 0., 1.5]], source='t')
    fx = _good_fixture()
    t, m = _trimmed(fx)
    altered = dict(m, inertia_body_axes_kg_m2=(np.asarray(m['inertia_body_axes_kg_m2'])*1.1).tolist())
    with pytest.raises(ValueError, match='Mass properties differ'):
        dynamics.linearize(t, model=fx, mass=altered, required=('fixture_aero',), control_limits=LIMITS,
                           reference_span_m=1.5, reference_chord_m=.2)
    with pytest.raises(ValueError, match='Mass properties differ'):
        derivatives.compute(t, model=fx, mass=altered, required=('fixture_aero',), reference=_reference(fx))
    other = dict(t, revision_sha256=digest('another revision'))
    with pytest.raises(ValueError, match='another revision'):
        dynamics.linearize(other, model=fx, mass=m, required=('fixture_aero',), control_limits=LIMITS,
                           reference_span_m=1.5, reference_chord_m=.2)
    with pytest.raises(ValueError, match='gravity'):
        dynamics.trim_gravity(dict(t, problem={}))


# --------------------------------------------------------------- derivatives
def test_derivatives_reproduce_declared_coefficients_with_explicit_units():
    fx = _good_fixture()
    t, m = _trimmed(fx)
    d = derivatives.compute(t, model=fx, mass=m, required=('fixture_aero',), reference=_reference(fx),
                            control_limits=dict(elevator=20., aileron=20.))
    assert d['state'] == 'complete' and d['complete'] and not d['missing_or_failed']
    v = {k: r['value'] for k, r in d['derivatives'].items()}
    alpha0 = math.radians(t['flight_state']['alpha_deg'])
    assert abs(alpha0) < 1e-6
    assert v['alpha']['CL'] == pytest.approx(fx.CLa, rel=1e-6)
    assert v['alpha']['Cm'] == pytest.approx(fx.Cma, rel=1e-6)
    assert v['q_hat']['Cm'] == pytest.approx(fx.Cmq, rel=1e-6)
    assert v['q_hat']['CL'] == pytest.approx(fx.CLq, rel=1e-6)
    assert v['p_hat']['Cl'] == pytest.approx(fx.Clp, rel=1e-6)
    assert v['r_hat']['Cn'] == pytest.approx(fx.Cnr, rel=1e-6)
    assert v['beta']['Cl'] == pytest.approx(fx.Clb, rel=1e-6)
    assert v['beta']['Cn'] == pytest.approx(fx.Cnb, rel=1e-6)
    # Control derivatives are per radian (a per-degree value would be 57x smaller).
    assert v['control:elevator']['Cm'] == pytest.approx(fx.controls['elevator']['Cm'], rel=1e-6)
    assert v['control:aileron']['Cl'] == pytest.approx(fx.controls['aileron']['Cl'], rel=1e-6)
    # Reference-q0 convention: dCL/du_hat = 2 CL at constant coefficients.
    assert v['u_hat']['CL'] == pytest.approx(2*d['coefficients_at_equilibrium']['CL'], rel=1e-6)
    # Second-order central differences: error ratio 4 when the drag polar curves.
    assert d['derivatives']['alpha']['difference_ratio'] == pytest.approx(4., rel=1e-3)


def test_derivative_steps_leaving_coverage_are_not_replaced_by_smaller_steps():
    fx = _good_fixture()
    t, m = _trimmed(fx)

    class Narrow:
        """Same revision, evidence covering only |alpha| <= 0.3 deg."""
        def __call__(self, state):
            if abs(state['alpha_deg']) > .3:
                raise PolarCoverageError('outside sampled alpha rows')
            return fx(state)
    d = derivatives.compute(t, model=Narrow(), mass=m, required=('fixture_aero',), reference=_reference(fx),
                            variables=['alpha', 'q_hat'])
    alpha = d['derivatives']['alpha']
    assert alpha['state'] == 'step_leaves_coverage' and alpha['value'] is None
    assert alpha['failed_step'] == pytest.approx(math.radians(.5))
    assert d['derivatives']['q_hat']['state'] == 'converged'
    assert d['state'] == 'incomplete' and 'alpha' in d['missing_or_failed']


def test_rate_terms_missing_remain_unsupported_and_static_do_not_substitute():
    fx = _good_fixture()
    t, m = _trimmed(fx)

    class NoRates:
        def __call__(self, state):
            if any(state[k] for k in ('p_rad_s', 'q_rad_s', 'r_rad_s')):
                return [wrench.unsupported('fixture_aero', 'aerodynamic_lifting', fx.revision, 'no rate model')]
            return fx(state)
    d = derivatives.compute(t, model=NoRates(), mass=m, required=('fixture_aero',), reference=_reference(fx),
                            control_limits=dict(elevator=20., aileron=20.))
    assert {k for k, r in d['derivatives'].items() if r['state'] == 'unsupported'} == {'p_hat', 'q_hat', 'r_hat'}
    assert d['derivatives']['alpha']['state'] == 'converged'
    assert d['complete'] is False and d['derivatives']['q_hat']['value'] is None


def test_quantized_output_fails_step_convergence_and_control_steps_respect_limits():
    fx = _good_fixture()
    t, m = _trimmed(fx)

    class Quantized:
        def __call__(self, state):
            c = fx(state)[0]
            return [c.model_copy(update=dict(moment_nm=tuple(round(v, 4) for v in c.moment_nm)))]
    d = derivatives.compute(t, model=Quantized(), mass=m, required=('fixture_aero',), reference=_reference(fx),
                            variables=['p_hat'])
    assert d['derivatives']['p_hat']['state'] == 'not_converged' and d['derivatives']['p_hat']['value'] is None
    near = derivatives.compute(t, model=fx, mass=m, required=('fixture_aero',), reference=_reference(fx),
        variables=['control:elevator'], control_limits=dict(elevator=abs(t['flight_state']['controls']['elevator'])+.6,
                                                         aileron=20.))
    assert near['derivatives']['control:elevator']['state'] == 'step_exceeds_control_limit'
    with pytest.raises(ValidationError, match='decreasing'):
        derivatives.Settings(alpha_steps_deg=(.1, .2))


# --------------------------------------------------------------- modes
def _lin(fx, unsteady=True, m=None):
    t, m = _trimmed(fx, m)
    u = None
    if unsteady:
        u = dynamics.unsteady_from_coefficients(revision_sha256=fx.revision, evidence='analytic_fixture',
            source='fixture', artifact_sha256=digest('u'), density_kg_m3=fx.density_kg_m3,
            speed_m_s=t['flight_state']['speed_m_s'], alpha_deg=t['flight_state']['alpha_deg'], area_m2=fx.area_m2,
            chord_m=fx.chord_m, span_m=fx.span_m, CL_alphadot=1.5, Cm_alphadot=-4., CY_betadot=0.,
            Cl_betadot=0., Cn_betadot=0., speed_range_m_s=(2., 40.), alpha_range_deg=(-6., 12.))
    return dynamics.linearize(t, model=fx, mass=m, required=('fixture_aero',), control_limits=LIMITS,
                              reference_span_m=fx.span_m, reference_chord_m=fx.chord_m, unsteady=u)


REQ = stability.StabilityRequirement(derivation='analytic_test', artifact_sha256=digest('req'), source='test',
    short_period_min_damping=.3, phugoid_min_damping=.01, dutch_roll_min_damping=.05, roll_max_time_constant_s=1.,
    spiral_min_time_to_double_s=8.)


def test_stable_weakly_damped_unstable_and_degenerate_mode_cases():
    good = stability.assess(_lin(_good_fixture()), REQ)
    assert good['state'] == 'requirements_met' and not good['family_policy_failures']
    weak = stability.assess(_lin(_good_fixture()), REQ.model_copy(update=dict(dutch_roll_min_damping=.5)))
    assert weak['state'] == 'rejected' and weak['modes']['dutch_roll']['state'] == 'insufficient_damping'
    unstable = stability.assess(_lin(_good_fixture(Cma=.3)), REQ)
    assert unstable['state'] == 'rejected' and unstable['family_policy_failures']
    # Overdamped short period: two real roots and one pair cannot be identified safely.
    degenerate = _lin(_good_fixture(Cma=-.8, Cmq=-12.))
    names = [m['mode'] for m in degenerate['modes']]
    assert 'short_period' not in names and 'longitudinal_oscillatory_ambiguous' in names
    out = stability.assess(degenerate, REQ)
    assert out['state'] == 'rejected' and out['modes']['short_period']['state'] == 'not_identified'


def test_spiral_divergence_mission_limit_and_family_policy_are_both_preserved():
    fx = _good_fixture(Clb=-.004, Cnb=.12)
    lin = _lin(fx)
    spiral = next(m for m in lin['modes'] if m['mode'] == 'spiral')
    assert spiral['real_per_s'] > 0
    mission = stability.mission_limits_requirement()
    assert mission.dutch_roll_min_damping == .05 and mission.spiral_min_time_to_double_s == 8.
    assert mission.short_period_min_damping is None  # not declared in the mission limits
    out = stability.assess(lin, REQ.model_copy(update=dict(spiral_min_time_to_double_s=1e-3)))
    # A slow divergence may meet a lenient mission limit, but the unchanged
    # family admission rule (real part < -1e-4) still rejects it.
    assert out['modes']['spiral']['state'] == 'met'
    assert out['state'] == 'rejected' and any('spiral' in f for f in out['family_policy_failures'])
    fast = stability.assess(lin, REQ.model_copy(update=dict(spiral_min_time_to_double_s=1e4)))
    assert fast['modes']['spiral']['state'] == 'divergence_too_fast'


def test_absent_requirements_and_unsupported_linearization_never_pass():
    lin = _lin(_good_fixture())
    assert stability.assess(lin, None)['state'] == 'blocked_missing_requirement'
    mission = stability.assess(lin, stability.mission_limits_requirement())
    assert mission['state'] == 'blocked_missing_requirement'
    assert set(mission['unassessed']) == {'short_period', 'phugoid', 'roll'}
    assert stability.assess(dict(state='unsupported'), REQ)['state'] == 'unsupported'
    assert stability.assess(_lin(_good_fixture(), unsteady=False), REQ)['state'] == \
        'blocked_missing_unsteady_terms'


# --------------------------------------------------------------- authority
def _requirement(revision, **changes):
    base = dict(revision_sha256=revision, derivation='analytic_test', artifact_sha256=digest('auth'),
        source='test', cases=[dict(name='roll', kind='manoeuvre', moments_nm={'L': .3}),
                              dict(name='pitch', kind='disturbance', moments_nm={'M': .05})],
        reserve_deg={'elevator': 2., 'aileron': 2.}, exemptions={'N': digest('yaw exemption')})
    base.update(changes)
    return authority.AuthorityRequirementSet.model_validate_json(canonical_json(base))


def _hardware(name, travel=(-20., 20.), rate=300., torque=.5):
    return interfaces.ControlHardware(control=name, hinge_inboard_m=(0., 0., 0.), hinge_outboard_m=(0., .5, 0.),
        deflection_limits_deg=travel, actuator_torque_limit_nm=torque, actuator_rate_deg_s=rate, linkage_ratio=1.,
        backlash_deg=0., exposed_hardware_drag='none_flush_internal',
        mass=(mass.point_mass(name+'_servo', .009, (.2, 0., 0.), evidence='declared_hardware', source='t'),),
        evidence='declared', artifact_sha256=digest(name))


def _hinge(name, ch=-.4):
    return interfaces.HingeMomentEvidence(control=name, geometry_revision_sha256=digest('g'),
        artifact_sha256=digest('h'+name), method='fixture', evidence='development_fixture', control_area_m2=.01,
        control_chord_m=.04, speed_range_m_s=(2., 40.), alpha_deg=(-6., 12.), deflection_deg=(-20., 20.),
        hinge_coefficient=((ch*-20/57.3, ch*20/57.3), (ch*-20/57.3, ch*20/57.3)))


def test_authority_met_with_hardware_rate_and_hinge_torque_evidence():
    fx = _good_fixture()
    t, m = _trimmed(fx)
    req = _requirement(t['revision_sha256'], cases=[
        dict(name='roll', kind='manoeuvre', moments_nm={'L': .3}, onset_time_s=.5),
        dict(name='pitch', kind='disturbance', moments_nm={'M': .05}, onset_time_s=.2)])
    out = authority.assess(t, model=fx, mass=m, required=('fixture_aero',), topology=_topo(t['revision_sha256']),
        requirement=req, hardware=[_hardware('elevator'), _hardware('aileron')],
        hinge_moments=[_hinge('elevator'), _hinge('aileron')], density_kg_m3=fx.density_kg_m3)
    assert out['state'] == 'requirements_met', out['blockers']
    assert out['axes']['N']['state'] == 'exempted_by_declared_requirement'
    roll = next(c for c in out['cases'] if c['case'] == 'roll' and c['direction'] == '-')
    assert roll['achieved_nm'][0] <= -.3*(1-1e-9) and roll['rate']['state'] == 'met'
    assert roll['cross_coupling_nm']['N'] != 0  # adverse yaw reported, not counted as directional authority
    assert out['qualified_control_authority'] is False
    slow = authority.assess(t, model=fx, mass=m, required=('fixture_aero',), topology=_topo(t['revision_sha256']),
        requirement=req, hardware=[_hardware('elevator', rate=5.), _hardware('aileron', rate=5.)],
        hinge_moments=[_hinge('elevator'), _hinge('aileron')], density_kg_m3=fx.density_kg_m3)
    assert slow['state'] == 'blocked' and any('actuator_rate_insufficient' in b for b in slow['blockers'])
    weak = authority.assess(t, model=fx, mass=m, required=('fixture_aero',), topology=_topo(t['revision_sha256']),
        requirement=req, hardware=[_hardware('elevator', torque=1e-6), _hardware('aileron', torque=1e-6)],
        hinge_moments=[_hinge('elevator'), _hinge('aileron')], density_kg_m3=fx.density_kg_m3)
    assert any('actuator_torque_insufficient' in b for b in weak['blockers'])
    nohinge = authority.assess(t, model=fx, mass=m, required=('fixture_aero',),
        topology=_topo(t['revision_sha256']), requirement=req,
        hardware=[_hardware('elevator'), _hardware('aileron')], density_kg_m3=fx.density_kg_m3)
    assert nohinge['state'] == 'blocked' and any('hinge_moment_evidence' in b for b in nohinge['blockers'])


def test_insufficient_authority_missing_yaw_hardware_and_absent_requirements():
    fx = _good_fixture()
    t, m = _trimmed(fx)
    kw = dict(model=fx, mass=m, required=('fixture_aero',), topology=_topo(t['revision_sha256']))
    assert authority.assess(t, **kw)['state'] == 'unassessed_missing_requirement'
    big = authority.assess(t, requirement=_requirement(t['revision_sha256'], cases=[
        dict(name='roll', kind='manoeuvre', moments_nm={'L': 50.})]), **kw)
    assert big['state'] == 'insufficient_authority'
    assert any(c['state'] == 'combined_surface_saturated' for c in big['cases'])
    yaw = authority.assess(t, requirement=_requirement(t['revision_sha256'], exemptions={}, cases=[
        dict(name='roll', kind='manoeuvre', moments_nm={'L': .3}),
        dict(name='pitch', kind='disturbance', moments_nm={'M': .05}),
        dict(name='gust yaw', kind='disturbance', moments_nm={'N': .001})]), **kw)
    assert yaw['axes']['N']['state'] == 'blocked_missing_directional_hardware'
    assert yaw['state'] == 'blocked' and yaw['directional_control'] == 'absent_no_declared_yaw_channel'
    silent = authority.assess(t, requirement=_requirement(t['revision_sha256'], exemptions={}), **kw)
    assert silent['axes']['N']['state'] == 'blocked_missing_requirement' and silent['state'] == 'blocked'
    no_reserve = authority.assess(t, requirement=_requirement(t['revision_sha256'], reserve_deg={}), **kw)
    assert no_reserve['reserves']['elevator']['state'] == 'unassessed_missing_requirement'
    with pytest.raises(ValidationError, match='both required and exempted'):
        _requirement(t['revision_sha256'], exemptions={'L': digest('x')})
    with pytest.raises(ValidationError, match='artifact digest'):
        _requirement(t['revision_sha256'], exemptions={'N': 'not a digest'})
    with pytest.raises(ValueError, match='another revision'):
        authority.assess(t, requirement=_requirement(digest('stale')), **kw)


def test_actual_tailless_elevons_share_physical_surfaces_and_saturate_together():
    aircraft = build('tailless_swept')
    topo = authority.topology(aircraft)
    roles = {c.name: c.role for c in topo.channels}
    assert roles == {'elevon_pitch': 'pitch', 'elevon_roll': 'roll'}
    shared = [s for s in topo.segments if set(s.mixing) == {'elevon_pitch', 'elevon_roll'}]
    assert shared and {s.side for s in shared} == {-1, 1}
    assert all(s.mixing['elevon_roll'] == s.side and s.mixing['elevon_pitch'] == 1. for s in shared)
    # With 15 degrees of pitch trim only 5 degrees of roll remain on the physical elevons.
    assert authority.channel_range(topo, dict(elevon_pitch=15., elevon_roll=0.), 'elevon_roll') == \
        pytest.approx((-5., 5.))
    assert authority.violations(topo, dict(elevon_pitch=15., elevon_roll=6.))
    # Model with the actual channel names: combined pitch + roll demand saturates.
    fx = _good_fixture(controls={'elevon_pitch': {'CL': .4, 'Cm': -1.2}, 'elevon_roll': {'Cl': .25, 'Cn': -.01}})
    m = _mass_at()
    problem = _problem(fx)
    problem['controls'] = [dict(name='elevon_pitch', limit_deg=20.), dict(name='elevon_roll', limit_deg=20.)]
    problem['variables'] = [v if v['name'] != 'control:elevator' else dict(v, name='control:elevon_pitch')
                            for v in problem['variables']]
    rename = {'control:aileron': 'control:elevon_roll', 'control:elevator': 'control:elevon_pitch'}
    problem['fixed'] = {rename.get(k, k): v for k, v in problem['fixed'].items()}
    t = trim.solve(problem, fx, m)
    assert t['state'] == 'trimmed'
    pitch_trim = t['flight_state']['controls']['elevon_pitch']
    combined = authority.assess(t, model=fx, mass=m, required=('fixture_aero',), topology=topo,
        requirement=_requirement(t['revision_sha256'], reserve_deg={'elevon_pitch': 1., 'elevon_roll': 1.},
            exemptions={'N': digest('n')}, cases=[dict(name='pull and roll', kind='manoeuvre',
                                                       moments_nm={'L': 1.5, 'M': -1.5})]))
    case = combined['cases'][0]
    assert case['state'] == 'combined_surface_saturated' and 0 < case['largest_feasible_fraction'] < 1
    assert combined['state'] == 'insufficient_authority'
    # A trim state already beyond the combined physical limit is reported, not evaluated.
    saturated = dict(t, flight_state=dict(t['flight_state'], controls=dict(elevon_pitch=15., elevon_roll=8.)))
    assert authority.assess(saturated, model=fx, mass=m, required=('fixture_aero',), topology=topo)['state'] == \
        'trim_saturated'
    assert abs(pitch_trim) < 20.


def test_conflicting_hardware_on_shared_elevon_is_rejected():
    aircraft = build('tailless_swept')
    good = []
    for c in aircraft.controls:
        surf = next(s for s in aircraft.surfaces if s.name == c.surface)
        span = surf.sections[-1].y
        eta = [s.y/span for s in surf.sections]
        points = []
        for e in (c.eta_start, c.eta_end):
            x, y, z, chord = (float(np.interp(e, eta, [getattr(s, k) for s in surf.sections]))
                              for k in ('x', 'y', 'z', 'chord'))
            points.append((x+c.hinge_chord_fraction*chord, y, z))
        good.append(_hardware(c.name).model_copy(update=dict(hinge_inboard_m=points[0], hinge_outboard_m=points[1])))
    topo = authority.topology(aircraft, good)
    assert topo.hardware == 'declared'
    conflicting = [good[0], good[1].model_copy(update=dict(deflection_limits_deg=(-10., 15.)))]
    with pytest.raises(ValueError, match='Conflicting physical controls'):
        authority.topology(aircraft, conflicting)


def test_tandem_overlapping_flaps_on_different_hinges_are_mutually_exclusive():
    topo = authority.topology(build('tandem'))
    exclusive = [s for s in topo.segments if s.exclusive]
    assert exclusive and all(set(s.mixing) == {'rear_elevator', 'aileron'} for s in exclusive)
    assert authority.violations(topo, dict(front_elevator=0., rear_elevator=4., aileron=2.))
    assert not authority.violations(topo, dict(front_elevator=0., rear_elevator=4., aileron=0.))
    # The aileron is locked while the overlapping rear elevator is deflected.
    assert authority.channel_range(topo, dict(front_elevator=0., rear_elevator=4., aileron=0.), 'aileron') == (0., 0.)
    assert authority.channel_range(topo, dict(front_elevator=0., rear_elevator=0., aileron=0.), 'aileron') == \
        (-20., 20.)


# --------------------------------------------------------------- envelope
def _fixture_provider(fx, m=None, hardware=True):
    m = m or _mass_at()
    topo = _topo(fx.revision, hardware='declared' if hardware else 'undeclared')
    return lambda: dict(model=fx, mass=m, required=('fixture_aero',), revision=fx.revision, topology=topo,
        reference=_reference(fx), gravity_m_s2=frames.STANDARD_GRAVITY_M_S2, bindings=dict(fixture='linear-v1'),
        hardware=[_hardware('elevator'), _hardware('aileron')] if hardware else None,
        hinge_moments=[_hinge('elevator'), _hinge('aileron')] if hardware else None)


def _declaration(fx, m=None, speeds=(9., 10.5, 12.), **changes):
    m = m or _mass_at()
    base = dict(name='fixture envelope', family='analytic_fixture', purpose='machinery validation only',
        configurations=[dict(name='nominal', revision_sha256=fx.revision,
                             mass_properties_sha256=m['mass_properties_sha256'], description='fixture')],
        speeds_m_s=list(speeds), alpha_bounds_deg=[-4., 10.], reynolds_bounds=[1e4, 1e6], altitude_m=0.,
        trim_control='elevator', fixed_controls_deg={'aileron': 0.}, stability_requirement=REQ.model_dump(mode='json'),
        authority_requirements={'nominal': _requirement(fx.revision, cases=[
            dict(name='roll', kind='manoeuvre', moments_nm={'L': .05}, onset_time_s=.5),
            dict(name='pitch', kind='disturbance', moments_nm={'M': .02}, onset_time_s=.5)]).model_dump(mode='json')},
        unsteady={'nominal': dict(revision_sha256=fx.revision, evidence='analytic_fixture', source='fixture',
            artifact_sha256=digest('u'), CL_alphadot=1.5, Cm_alphadot=-4., CY_betadot=0., Cl_betadot=0.,
            Cn_betadot=0., speed_range_m_s=[2., 40.], alpha_range_deg=[-6., 12.])})
    base.update(changes)
    return base


def test_fixture_envelope_evaluates_every_declared_point_but_never_qualifies(tmp_path):
    fx = _good_fixture()
    decl = _declaration(fx)
    path = tmp_path/'declaration.json'
    path.write_text(canonical_json(envelope.EnvelopeDeclaration.model_validate_json(
        canonical_json(decl)).model_dump(mode='json')))
    report = envelope.run(decl, {'nominal': _fixture_provider(fx)}, declared_file=path)
    assert report['summary'] == dict(points=3, qualified=3, rejected=0, blocked=0)
    assert report['state'] == 'fixture_machinery_only' and report['numerically_qualified'] is False
    assert report['evidence_admissible'] is False and report['aerodynamic_evidence'] == ['analytic_fixture']
    assert all(report[f] is False for f in envelope.FLAGS)
    assert report['declared_file']['sha256']
    assert envelope.replay(report, {'nominal': _fixture_provider(fx)}) is False
    path.write_text(path.read_text().replace('machinery validation only', 'other'))
    with pytest.raises(ValueError, match='Declaration file differs'):
        envelope.run(decl, {'nominal': _fixture_provider(fx)}, declared_file=path)


def test_envelope_boundaries_rejections_and_blocked_points_are_reported_not_narrowed():
    fx = _good_fixture()
    # 45 m/s lies outside the fixture's aerodynamic applicability (2..40 m/s).
    report = envelope.run(_declaration(fx, speeds=(9., 45.)), {'nominal': _fixture_provider(fx)})
    assert report['summary']['points'] == 2
    boundary = report['points'][1]
    assert boundary['verdict'] == 'blocked' and boundary['trim']['state'] in ('out_of_coverage',
                                                                             'no_equilibrium_established')
    assert report['state'] == 'blocked'
    # The low-speed boundary trims at higher CL where the spiral slowly diverges:
    # the mission limit (8 s) is met but the preserved family rule rejects it.
    low = envelope.run(_declaration(fx, speeds=(7., 9.)), {'nominal': _fixture_provider(fx)})
    assert [p['verdict'] for p in low['points']] == ['rejected', 'qualified_point']
    spiral = low['points'][0]['stability']
    assert spiral['modes']['spiral']['state'] == 'met' and spiral['modes']['spiral']['real_per_s'] > 0
    assert any('spiral' in f for f in spiral['family_policy_failures'])
    assert low['state'] == 'rejected_points_present'
    unstable = _good_fixture(Cma=.3)
    rejected = envelope.run(_declaration(unstable), {'nominal': _fixture_provider(unstable)})
    assert rejected['state'] == 'rejected_points_present'
    assert all(p['verdict'] != 'qualified_point' for p in rejected['points'])
    unhardware = envelope.run(_declaration(fx), {'nominal': _fixture_provider(fx, hardware=False)})
    assert unhardware['state'] == 'blocked' and any('authority' in b for b in unhardware['blockers'])
    nounsteady = envelope.run(_declaration(fx, unsteady={}), {'nominal': _fixture_provider(fx)})
    assert any('blocked_missing_unsteady_terms' in b for b in nounsteady['blockers'])


def test_envelope_declaration_and_provenance_reject_stale_altered_forged_and_incomplete_evidence(monkeypatch):
    fx = _good_fixture()
    provider = {'nominal': _fixture_provider(fx)}
    report = envelope.run(_declaration(fx, speeds=(8., 10.)), provider)
    forged = copy.deepcopy(report)
    forged['production_admission'] = True
    with pytest.raises(ValueError, match='Promoted'):
        envelope.replay(forged, provider)
    promoted = copy.deepcopy(report)
    promoted['state'] = 'numerically_qualified'
    promoted['numerically_qualified'] = True
    with pytest.raises(ValueError, match='digest mismatch'):
        envelope.replay(promoted, provider)
    promoted.pop('report_sha256')
    promoted['report_sha256'] = digest(promoted)  # rehashing a forged result cannot grant it
    with pytest.raises(ValueError, match='independent recomputation'):
        envelope.replay(promoted, provider)
    incomplete = {k: v for k, v in report.items() if k != 'points'}
    with pytest.raises(ValueError, match='Incomplete'):
        envelope.replay(incomplete, provider)
    stale = dict(envelope.implementation_identity(), implementation_sha256=digest('old'))
    monkeypatch.setattr(envelope, 'implementation_identity', lambda: stale)
    with pytest.raises(ValueError, match='Stale'):
        envelope.replay(report, provider)
    monkeypatch.undo()
    other = _good_fixture(Cmq=-5.)
    with pytest.raises(ValueError, match='revision differs'):
        envelope.run(_declaration(fx), {'nominal': _fixture_provider(other)})
    heavier = mass.combine([mass.point_mass('m', .6, (.25, 0., 0.), evidence='calculated_screening', source='t'),
                            mass.lamina('w', .1, (.25, 0., 0.), (1., 0., 0.), (0., 1., 0.), .2, 1.5,
                                        evidence='calculated_screening', source='t')])
    with pytest.raises(ValueError, match='mass properties differ'):
        envelope.run(_declaration(fx), {'nominal': _fixture_provider(fx, m=heavier)})
    with pytest.raises(ValidationError, match='strictly increasing'):
        envelope.EnvelopeDeclaration.model_validate_json(canonical_json(_declaration(fx, speeds=(9., 7.))))
    with pytest.raises(ValidationError, match='another revision'):
        envelope.EnvelopeDeclaration.model_validate_json(canonical_json(_declaration(
            fx, authority_requirements={'nominal': _requirement(digest('x')).model_dump(mode='json')})))


def test_admissible_evidence_requires_qualifying_aerodynamics_hardware_and_mass():
    fx = _good_fixture()
    report = envelope.run(_declaration(fx, speeds=(8., 10.)), {'nominal': _fixture_provider(fx)})
    assert all(p['verdict'] == 'qualified_point' for p in report['points'])
    # Every point passes, yet fixture aerodynamics and screening mass keep it unqualified.
    assert report['state'] == 'fixture_machinery_only'
    assert {b['mass_state'] for b in report['bindings'].values()} == {'calculated_screening'}


# --------------------------------------------------------------- actual families
NEUTRAL = dict(tailless_swept=dict(elevon_pitch=0., elevon_roll=0.),
               canard=dict(foreplane_elevator=0., aileron=0.),
               tandem=dict(front_elevator=0., rear_elevator=0., aileron=0.))


@pytest.mark.parametrize('family', sorted(NEUTRAL))
def test_actual_family_qualification_inputs_record_every_missing_dependency(family):
    record = archive(build(family))
    out = qualification.inventory(record, controls=NEUTRAL[family],
        operating_point=dict(speed_m_s=6., alpha_deg=4., beta_deg=0., pitch_deg=0., bank_deg=0.))
    assert out['equilibrium']['state'] == 'unsupported' and out['equilibrium']['lifting_only'] is None
    assert {'section_bank', 'body_oml', 'junctions', 'control_hardware', 'hinge_moments',
            'rate_and_sideslip_aerodynamics', 'measured_mass_properties', 'authority_requirement',
            'stall_envelope', 'unsteady_derivatives', 'stability_requirement',
            'directional_control_hardware'} == set(out['missing'])
    assert out['mass_properties']['state'] == 'calculated_screening'
    assert np.asarray(out['mass_properties']['inertia_body_axes_kg_m2']).shape == (3, 3)
    assert out['control_topology']['hardware'] == 'undeclared'
    assert {c['role'] for c in out['control_topology']['channels']} <= {'pitch', 'roll'}
    for flag in ('qualified_trim', 'qualified_dynamics', 'qualified_control_authority', 'production_admission',
                 'physical_construction', 'verified_gain'):
        assert out[flag] is False


@pytest.mark.parametrize('family', sorted(NEUTRAL))
def test_actual_family_envelope_is_blocked_and_replays(family):
    record = archive(build(family))
    provider = {'baseline': lambda: assessment.envelope_provider(record, controls=NEUTRAL[family])}
    prepared = provider['baseline']()
    pitch = 'elevon_pitch' if family == 'tailless_swept' else (
        'foreplane_elevator' if family == 'canard' else 'front_elevator')
    decl = dict(name=family+' screening envelope', family=family, purpose='declared before evaluation',
        configurations=[dict(name='baseline', revision_sha256=prepared['revision'],
                             mass_properties_sha256=prepared['mass']['mass_properties_sha256'],
                             description='default screening archive, neutral controls')],
        speeds_m_s=[5., 6., 8.], alpha_bounds_deg=[-2., 8.], reynolds_bounds=[3e4, 1.5e5], altitude_m=0.,
        trim_control=pitch, fixed_controls_deg={k: 0. for k in NEUTRAL[family] if k != pitch})
    report = envelope.run(decl, provider)
    assert report['state'] == 'blocked' and report['summary']['blocked'] == 3
    assert all(p['trim']['state'] == 'unsupported' for p in report['points'])
    missing = {u['name'] for p in report['points'] for u in p['trim']['unsupported']}
    assert {'lifting_surfaces', 'body', 'control_hardware_drag'} <= missing
    assert report['numerically_qualified'] is False
    assert envelope.replay(report, provider) is False
    altered = copy.deepcopy(record)
    altered['parameters']['inventory']['equipment_x_m'] += .01
    with pytest.raises(ValueError):
        assessment.envelope_provider(altered, controls=NEUTRAL[family])


def test_capability_metadata_keeps_dynamics_unqualified():
    from cem.glider.families import capabilities
    for name, record in capabilities().items():
        assert record['qualified_dynamics'] is False and record['qualified_envelopes'] == []
        assert record['qualified_control_authority'] is False
        assert record['directional_control'] == 'absent_no_declared_yaw_hardware'
        assert record['production_search'] is False and record['physical_construction'] is False
