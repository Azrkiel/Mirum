"""CEM-V: physics-based verification of the glider CEM's analytic layer.

Every check compares the code under test against an *independent* oracle:
a closed-form law, a textbook reference value, or a separate numerical
integration done inside the test. No oracle calls the function it checks.
Pure Python (numpy, pydantic, trimesh); no AeroSandbox, no geometry kernel,
no provider. Runs in seconds (test tier T0).

Plan: docs/plans/2026-11-16-remaining-work-plan.md, workstream CEM-V.
"""
import math

import numpy as np
import pytest

from cem.glider.calibration import initial
from cem.glider.feasibility import feasibility
from cem.glider.physics import G, atmosphere, launch_state, trajectory
from cem.glider.processes import CARBON, LAUNCH_LOAD_FACTORS, RODS_M, spar_size
from cem.glider.requirements import Environment, GliderMissionRequirements as Mission
from cem.glider.structure import bending_moment, converged_spar, verify_spar
from cem.glider.uncertainty import uncertainty

# ---------------------------------------------------------------------------
# CEM-V1  Standard atmosphere: ideal gas + Sutherland viscosity
# ---------------------------------------------------------------------------
# Reference values: ISO 2533 / US Standard Atmosphere 1976 tables.


def _isa(altitude_m):
    # Independent troposphere model: T = T0 - L h, p = p0 (T/T0)^(g M / (R* L)).
    t0, lapse, p0 = 288.15, 0.0065, 101325.0
    g0, molar, r_star = 9.80665, 0.0289644, 8.31447
    t = t0 - lapse * altitude_m
    return t, p0 * (t / t0) ** (g0 * molar / (r_star * lapse))


@pytest.mark.parametrize('altitude, temperature_c, rho_table', [
    (0., 15., 1.2250),       # ISA sea level
    (1000., 8.5, 1.1117),    # ISA 1000 m
    (2000., 2.0, 1.0066),    # ISA 2000 m
])
def test_v1_isa_density_matches_standard_tables(altitude, temperature_c, rho_table):
    rho = atmosphere(Environment(altitude_m=altitude, temperature_c=temperature_c))['density_kg_m3']
    assert rho == pytest.approx(rho_table, rel=5e-4)
    t, p = _isa(altitude)
    assert rho == pytest.approx(p / (287.053 * t), rel=5e-4)


def test_v1_density_follows_ideal_gas_temperature_scaling_at_fixed_pressure():
    cold = atmosphere(Environment(temperature_c=-20.))['density_kg_m3']
    hot = atmosphere(Environment(temperature_c=40.))['density_kg_m3']
    assert cold / hot == pytest.approx((40. + 273.15) / (-20. + 273.15), rel=1e-12)


@pytest.mark.parametrize('temperature_c, mu_ref', [
    (0., 1.716e-5),          # Sutherland reference point (by definition)
    (15., 1.7894e-5),        # ISA sea level
    (-30., 1.5618e-5),       # Sutherland law, evaluated independently below
])
def test_v1_sutherland_viscosity(temperature_c, mu_ref):
    mu = atmosphere(Environment(temperature_c=temperature_c))['dynamic_viscosity_pa_s']
    t = temperature_c + 273.15
    sutherland = 1.458e-6 * t ** 1.5 / (t + 110.4)   # alternative (C1, S) form
    assert mu == pytest.approx(mu_ref, rel=2e-3)
    assert mu == pytest.approx(sutherland, rel=2e-3)


# ---------------------------------------------------------------------------
# CEM-V2  Glide energy balance: potential energy is spent at the sink rate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('lift_to_drag', [5., 12.5, 25.])
@pytest.mark.parametrize('speed', [4., 7.5, 12.])
def test_v2_still_air_range_equals_height_times_lift_to_drag(lift_to_drag, speed):
    gamma = math.atan(1 / lift_to_drag)
    sink = speed * math.sin(gamma)
    out = trajectory(10., speed, sink)
    assert out['state'] == 'descending'
    assert out['duration_s'] * sink == pytest.approx(10.)          # dE/dt = m g w
    assert out['distance_m'] == pytest.approx(10. * lift_to_drag)  # R = h L/D


def test_v2_wind_adds_drift_and_uplift_reduces_net_sink():
    base = trajectory(10., 8., .5)
    wind = trajectory(10., 8., .5, wind=2.)
    lift = trajectory(10., 8., .5, uplift=.2)
    assert wind['duration_s'] == pytest.approx(base['duration_s'])
    assert wind['distance_m'] - base['distance_m'] == pytest.approx(2. * base['duration_s'])
    assert lift['duration_s'] == pytest.approx(10. / (.5 - .2))


def test_v2_headwind_faster_than_airspeed_makes_no_forward_progress():
    out = trajectory(10., 8., .5, wind=-9.)
    assert out['state'] == 'no_forward_progress' and out['distance_m'] == 0.


@pytest.mark.parametrize('uplift', [.5, .8])
def test_v2_uplift_at_or_above_sink_has_no_finite_bound(uplift):
    out = trajectory(10., 8., .5, uplift=uplift)
    assert out['state'] == 'sustained_lift_limited'
    assert out['duration_s'] is None and out['distance_m'] is None


@pytest.mark.parametrize('args', [(10., .4, .5), (-1., 8., .5), (10., 8., float('nan'))])
def test_v2_nonphysical_glide_fails_closed(args):
    with pytest.raises(ValueError):
        trajectory(*args)


# ---------------------------------------------------------------------------
# CEM-V3  Launch: kinetic energy converted to height, eta * V^2 / (2 g)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('method', ['hand_toss', 'discus', 'catapult'])
def test_v3_launch_zoom_conserves_energy(method):
    cal = initial()
    req = Mission.model_validate({'launch': {'method': method, 'height_m': 2., 'launch_speed_m_s': 20.}})
    state = launch_state(req.launch, cal)
    eta = cal['eta_launch'][method]['median']
    assert state['effective_height_m'] - 2. == pytest.approx(eta * 20. ** 2 / (2 * 9.81))
    assert G == pytest.approx(9.80665, rel=5e-4)


def test_v3_drop_adds_no_height():
    req = Mission.model_validate({'launch': {'method': 'drop', 'height_m': 10.}})
    assert launch_state(req.launch, initial())['effective_height_m'] == 10.


# ---------------------------------------------------------------------------
# CEM-V4  Elliptic-lift cantilever statics (half wing, root clamped)
# ---------------------------------------------------------------------------

def _elliptic_load(n_w, half_span, y):
    # q(y) = (2 n W / (pi L)) sqrt(1 - (y/L)^2); integral over [0, L] = n W / 2.
    return 2 * n_w / (math.pi * half_span) * np.sqrt(np.clip(1 - (y / half_span) ** 2, 0, None))


@pytest.mark.parametrize('method', sorted(LAUNCH_LOAD_FACTORS))
def test_v4_bending_moment_matches_direct_integration(method):
    mass, span = .25, 1.4
    half, n_w = span / 2, LAUNCH_LOAD_FACTORS[method] * mass * 9.81
    s = np.linspace(0, half, 200001)
    q = _elliptic_load(n_w, half, s)
    assert np.trapezoid(q, s) == pytest.approx(n_w / 2, rel=1e-5)      # half the lift per wing
    for y in (0., .1 * half, .37 * half, .8 * half):
        mask = s >= y
        direct = np.trapezoid(q[mask] * (s[mask] - y), s[mask])          # M(y) = int q (s - y) ds
        assert bending_moment(mass, span, method, y) == pytest.approx(direct, rel=1e-4)
    assert bending_moment(mass, span, method, 0.) == pytest.approx(n_w * span / (3 * math.pi))
    assert bending_moment(mass, span, method, half) == 0.


def test_v4_shear_and_load_are_moment_derivatives():
    mass, span, method = .2, 1.2, 'discus'
    half, n_w = span / 2, LAUNCH_LOAD_FACTORS[method] * mass * 9.81
    h = 1e-5
    m = lambda y: bending_moment(mass, span, method, y)
    root_shear = -(m(h) - m(0.)) / h
    assert root_shear == pytest.approx(n_w / 2, rel=1e-3)                # -dM/dy = V
    for y in (.1, .25, .45):
        curvature = (m(y + h) - 2 * m(y) + m(y - h)) / h ** 2
        assert curvature == pytest.approx(float(_elliptic_load(n_w, half, np.array(y))), rel=1e-3)  # M'' = q


# ---------------------------------------------------------------------------
# CEM-V5/V6  Spar sizing: closed form, polygon section, fixed point
# ---------------------------------------------------------------------------

def _regular_polygon_ix(n, radius=1.):
    # Second moment of area about the centroidal x axis via the polygon
    # (Green's theorem) formula; vertex 0 at angle 0, a vertex on the z axis.
    a = 2 * np.pi * np.arange(n) / n
    x, z = radius * np.cos(a), radius * np.sin(a)
    x1, z1 = np.roll(x, -1), np.roll(z, -1)
    cross = x * z1 - x1 * z
    return float(np.sum(cross * (z * z + z * z1 + z1 * z1)) / 12)


def test_v5_spar_size_root_stress_is_solid_circle_flexure():
    mass, span = .3, 1.5
    out = spar_size(mass, span, 'catapult')
    d = out['diameter_m']
    moment = bending_moment(mass, span, 'catapult', 0.)
    assert out['root_moment_nm'] == pytest.approx(moment)
    assert out['stress_pa'] == pytest.approx(moment * (d / 2) / (math.pi * d ** 4 / 64))  # sigma = M c / I
    assert out['mass_kg'] == pytest.approx(CARBON['density_kg_m3'] * math.pi * d ** 2 / 4 * span)
    smaller = [r for r in RODS_M if r < d]
    if smaller:   # minimality: the next smaller catalogue rod is overstressed
        r = smaller[-1]
        assert 32 * moment / (math.pi * r ** 3) > CARBON['allowable_pa'] / CARBON['safety_factor']


def test_v6_48_gon_section_ratio_used_for_sizing_matches_polygon_integration():
    theta = 2 * math.pi / 48
    code_ratio = 48 * math.sin(theta) * (2 + math.cos(theta)) / (6 * math.pi)   # structure.converged_spar
    assert _regular_polygon_ix(48) / (math.pi / 4) == pytest.approx(code_ratio, rel=1e-12)


@pytest.mark.parametrize('non_spar, span, method', [
    (.15, 1., 'drop'), (.25, 1.4, 'hand_toss'), (.3, 1.5, 'discus'), (.2, 1.2, 'catapult')])
def test_v5_converged_spar_is_a_self_consistent_fixed_point(non_spar, span, method):
    out = converged_spar(non_spar, span, method)
    d = out['diameter_m']
    rod_mass = CARBON['density_kg_m3'] * math.pi * d ** 2 / 4 * span
    total = non_spar + rod_mass
    assert out['sizing_mass_kg'] == pytest.approx(total)          # rod carries its own weight
    # Delivered 48-gon (circumradius d/2, vertex on the bending axis extreme).
    ix = _regular_polygon_ix(48, d / 2)
    sigma = bending_moment(total, span, method, 0.) * (d / 2) / ix
    allowable = CARBON['allowable_pa'] / CARBON['safety_factor']
    assert sigma <= allowable * (1 + 1e-9)


def test_v5_converged_rod_passes_measured_mesh_check():
    trimesh = pytest.importorskip('trimesh')
    out = converged_spar(.25, 1.4, 'discus')
    d, span = out['diameter_m'], 1.4
    transform = trimesh.geometry.align_vectors([0, 0, 1], [0, 1, 0])
    mesh = trimesh.creation.cylinder(radius=d / 2, height=span, sections=48, transform=transform)
    report = verify_spar(mesh, out['sizing_mass_kg'], span, 'discus')
    assert report['root_section_inertia_m4'] == pytest.approx(_regular_polygon_ix(48, d / 2), rel=1e-6)
    assert report['state'] == 'passed'


# ---------------------------------------------------------------------------
# CEM-V7  Feasibility: analytic limits and lever closure (roadmap section 2)
# ---------------------------------------------------------------------------

def test_v7_required_ratios_match_closed_form():
    cal = initial()
    dist = feasibility(Mission.model_validate({'launch': {'method': 'drop'}, 'reference_target': 1200.}), cal)
    dur = feasibility(Mission.model_validate({'launch': {'method': 'drop'}, 'objective': 'duration_s',
                                              'reference_target': 1200.}), cal)
    assert dist['required_lift_to_drag'] == pytest.approx(1200. / 10.)
    assert dur['required_sink_m_s'] == pytest.approx(10. / 1200.)
    assert dist['verdict'] == dur['verdict'] == 'infeasible'


def test_v7_minimum_sink_formula_and_bounds_are_consistent():
    cal = initial()
    rep = feasibility(Mission.model_validate({'launch': {'method': 'drop'}, 'objective': 'duration_s',
                                              'reference_target': 1200.}), cal)
    rho = rep['atmosphere']['density_kg_m3']
    sink = math.sqrt(2 * rep['wing_loading_lower_bound_n_m2'] / rho) / rep['optimistic_cl_1_5_over_cd']
    assert rep['optimistic_sink_m_s'] == pytest.approx(sink)
    assert rep['optimistic_bound'] == pytest.approx(10. / sink)


@pytest.mark.parametrize('objective, target', [
    ('distance_m', 400.), ('duration_s', 60.), ('distance_m', 1200.), ('duration_s', 1200.)])
def test_v7_levers_close_the_gap_when_applied(objective, target):
    cal = initial()
    rep = feasibility(Mission.model_validate({'launch': {'method': 'drop'}, 'objective': objective,
                                              'reference_target': target}), cal)
    ld, sink, eff = rep['optimistic_lift_to_drag'], rep['optimistic_sink_m_s'], rep['optimistic_cl_1_5_over_cd']
    rho = rep['atmosphere']['density_kg_m3']
    levers = {lever['change']: lever['value'] for lever in rep['levers']}
    h_needed = levers['effective_launch_height']
    assert (h_needed * ld if objective == 'distance_m' else h_needed / sink) == pytest.approx(target)
    w = levers['mean_uplift']
    assert w >= 0
    if w > 0:   # uplift lever: net sink (sink - w) stretches the flight to the target
        t = 10. / (sink - w)
        assert (t * ld * sink if objective == 'distance_m' else t) == pytest.approx(target)
    loading = levers['wing_loading_for_duration']
    desired_time = target / (ld * sink) if objective == 'distance_m' else target
    assert math.sqrt(2 * loading / rho) / eff == pytest.approx(10. / desired_time)


# ---------------------------------------------------------------------------
# CEM-V8  Uncertainty propagation: degenerate cases with known answers
# ---------------------------------------------------------------------------

def _result(cl=.8, cd=.05, profile=.03, speed=7.):
    return dict(CL_trim=cl, CD_trim=cd, cd_profile=profile, aspect_ratio=10., glide_speed_m_s=speed,
                CLmax_est=1.4)


def _calibration(**sigmas):
    cal = initial()
    for key in ('cd0_factor', 'cl_max_factor', 'oswald_factor'):
        cal[key] = dict(cal[key], log_sigma=sigmas.get(key, 0.))
    cal['mass_factor'] = {p: dict(v, log_sigma=sigmas.get('mass', 0.)) for p, v in cal['mass_factor'].items()}
    return cal


def test_v8_zero_sigma_band_collapses_to_nominal_glide():
    req = Mission.model_validate({'launch': {'method': 'drop'}, 'process': 'sheet_foam'})
    cal = _calibration()
    out = uncertainty(req, _result(), cal, 'sheet_foam', launch_state(req.launch, cal))
    gamma = math.atan2(.05, .8)
    for key, nominal in (('distance_m', 10. * .8 / .05), ('duration_s', 10. / (7. * math.sin(gamma)))):
        band = out['bands'][key]
        assert band['p10'] == pytest.approx(nominal) and band['p90'] == pytest.approx(nominal)


def test_v8_weight_changes_duration_but_not_still_air_range():
    # Glide ratio L/D does not depend on weight; sink (and duration) scale with sqrt(W).
    req = Mission.model_validate({'launch': {'method': 'drop'}, 'process': 'sheet_foam'})
    cal = _calibration(mass=.2)
    out = uncertainty(req, _result(), cal, 'sheet_foam', launch_state(req.launch, cal))
    dist, dur = out['bands']['distance_m'], out['bands']['duration_s']
    assert dist['p10'] == pytest.approx(dist['p90']) == pytest.approx(10. * .8 / .05)
    # Mass ~ lognormal(sigma=0.2) and duration ~ 1/sqrt(mass), so ln(duration) has
    # sigma 0.1 and ln(P90/P10) = 2 * 1.2816 * 0.1 = 0.256 (200-sample tolerance).
    # A speed law V ~ W (instead of sqrt(W)) would double this to ~0.51.
    assert 0.20 < math.log(dur['p90'] / dur['p10']) < 0.32
