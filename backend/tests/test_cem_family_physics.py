"""Analytical/reference validation of the Task C aircraft-physics machinery.

Fixtures and closed-form references validate implementation only. None of
these cases qualifies an aircraft family, its stability or its authority.
"""
import math

import numpy as np
import pytest
from pydantic import ValidationError

from cem.core import digest
from cem.glider.families.physics import dynamics, fixtures, frames, mass, trim, wrench

REV = digest('physics-test-revision')
G = frames.STANDARD_GRAVITY_M_S2


# ------------------------------------------------------------------ frames
@pytest.mark.parametrize('alpha,beta', [(0., 0.), (4., 0.), (-7., 3.), (12., -9.)])
def test_frames_are_proper_rotations_and_match_shared_convention(alpha, beta):
    for r in (frames.body_from_wind(alpha, beta), frames.geometry_from_wind(alpha, beta),
              frames.body_from_earth(20., -35., 70.), frames.G_FROM_B):
        assert r@r.T == pytest.approx(np.eye(3), abs=1e-14)
        assert np.linalg.det(r) == pytest.approx(1.)
    a = math.radians(alpha)
    if beta == 0:
        # Shared mirum-aero-1 convention used by cem.aero.performance.
        assert frames.freestream_direction(alpha) == pytest.approx([math.cos(a), 0., math.sin(a)])
    force = frames.force_from_wind(1.5, -.4, 9., alpha, beta)
    parts = frames.aerodynamic_components(force, alpha, beta)
    assert (parts['drag_n'], parts['side_n'], parts['lift_n']) == pytest.approx((1.5, -.4, 9.))
    assert np.linalg.norm(force) == pytest.approx(math.sqrt(1.5**2+.4**2+81))


def test_gravity_moment_signs_and_flight_path():
    assert frames.gravity_force_geometry(2., 0., 0.) == pytest.approx([0., 0., -2*G])
    theta = 10.
    t = math.radians(theta)
    assert frames.gravity_force_geometry(1., 0., theta) == pytest.approx([G*math.sin(t), 0., -G*math.cos(t)])
    # Starboard-wing-down bank pulls gravity toward +y in body axes.
    assert frames.gravity_force_geometry(1., 30., 0.)[1] == pytest.approx(G*.5)
    # Moment pseudovectors: pitch shared, roll and yaw reverse between G and B.
    assert frames.geometry_to_body([1., 2., 3.]) == pytest.approx([-1., 2., -3.])
    # A lift force behind the CG produces nose-down (negative My in G).
    assert frames.transfer_moment([0, 0, 0], [0, 0, 10.], [.1, 0, 0], [0, 0, 0])[1] == pytest.approx(-1.)
    # Wings-level, zero sideslip: climb angle = theta - alpha.
    assert frames.flight_path_angle_deg(5., 0., 0., 1.) == pytest.approx(-4.)


def test_moment_transfer_is_exact_and_path_independent():
    rng = np.random.default_rng(3)
    f, m, a, b, c = (rng.normal(size=3) for _ in range(5))
    direct = frames.transfer_moment(m, f, a, c)
    via = frames.transfer_moment(frames.transfer_moment(m, f, a, b), f, b, c)
    assert direct == pytest.approx(via, abs=1e-14)
    # A pure couple is the same about every point.
    assert frames.transfer_moment(m, [0, 0, 0], a, c) == pytest.approx(m)
    with pytest.raises(ValueError, match='Finite'):
        frames.transfer_moment([np.nan, 0, 0], f, a, c)


# ------------------------------------------------------------------ ledger
def test_contribution_application_point_and_unsupported_rules():
    good = wrench.applied('wing', 'aerodynamic_lifting', REV, (0., 0., 5.), (.3, .2, 0.),
                          reference_point_m=(.1, 0., 0.), evidence='analytic_fixture')
    assert good.moment_nm == pytest.approx(np.cross([.2, .2, 0.], [0, 0, 5.]))
    with pytest.raises(ValidationError, match='stated point'):
        wrench.Contribution(**dict(good.model_dump(), moment_nm=(0., 0., 0.)))
    with pytest.raises(ValidationError, match='no substituted'):
        wrench.Contribution(name='body', kind='aerodynamic_body', state='unsupported', revision_sha256=REV,
                            reason='missing', force_n=(0., 0., 0.))
    with pytest.raises(ValidationError, match='evidence class'):
        wrench.Contribution(name='body', kind='aerodynamic_body', state='supported', revision_sha256=REV,
                            force_n=(0., 0., 0.), moment_nm=(0., 0., 0.), reference_point_m=(0., 0., 0.))
    with pytest.raises(ValueError, match='Finite'):
        wrench.supported('x', 'other', REV, (np.nan, 0, 0), (0, 0, 0), (0, 0, 0), evidence='analytic_fixture')


def test_resultant_conservation_and_unsupported_is_never_zero():
    w = wrench.applied('wing', 'aerodynamic_lifting', REV, (-.2, 0., 4.), (.25, .1, .02),
                       reference_point_m=(0., 0., 0.), couple_nm=(0., -.05, 0.), evidence='analytic_fixture')
    t = wrench.applied('tail', 'aerodynamic_lifting', REV, (-.05, 0., -.3), (.7, 0., .05),
                       reference_point_m=(.7, 0., .05), evidence='analytic_fixture')
    g = wrench.gravity(REV, .4, (.3, 0., .01), 0., -2., evidence='calculated_screening')
    req = ('wing', 'tail', 'gravity')
    a = wrench.resultant([w, t, g], required=req, revision=REV, about_m=(.3, 0., .01))
    b = wrench.resultant([w, t, g], required=req, revision=REV, about_m=(-1., .4, 2.))
    assert a['state'] == b['state'] == 'complete'
    assert a['force_n'] == pytest.approx(np.add(np.add(w.force_n, t.force_n), g.force_n))
    assert np.asarray(b['moment_nm']) == pytest.approx(frames.transfer_moment(
        a['moment_nm'], a['force_n'], (.3, 0., .01), (-1., .4, 2.)), abs=1e-14)
    body = wrench.unsupported('body', 'aerodynamic_body', REV, 'missing body OML')
    blocked = wrench.resultant([w, t, g, body], required=(*req, 'body'), revision=REV, about_m=(0, 0, 0))
    assert blocked['state'] == 'unsupported' and blocked['force_n'] is None and blocked['moment_nm'] is None
    assert blocked['unsupported'][0]['name'] == 'body'
    missing = wrench.resultant([w, g], required=req, revision=REV, about_m=(0, 0, 0))
    assert missing['state'] == 'unsupported' and missing['missing'] == ['tail']
    with pytest.raises(ValueError, match='Undeclared'):
        wrench.resultant([w, t, g, body], required=req, revision=REV, about_m=(0, 0, 0))
    with pytest.raises(ValueError, match='another revision'):
        wrench.resultant([w, t, g], required=req, revision=digest('other'), about_m=(0, 0, 0))
    partial = wrench.partial_supported([w, body], about_m=(0, 0, 0))
    assert partial['equilibrium_evidence'] is False and partial['excluded'] == ['body']
    # Dict input (JSON-shaped) is revalidated with the same rules.
    again = wrench.resultant([c.model_dump(mode='json') for c in (w, t, g)], required=req,
                             revision=REV, about_m=(.3, 0., .01))
    assert again['record_sha256'] == a['record_sha256']


# ------------------------------------------------------------------ mass
def test_primitive_tensors_rotation_and_parallel_axis_are_analytic():
    box = mass.solid_box('box', 2., (0., 0., 0.), (.4, .2, .1), evidence='calculated_screening', source='t')
    assert np.diag(box.inertia_cg_kg_m2) == pytest.approx([2/12*(.04+.01), 2/12*(.16+.01), 2/12*(.16+.04)])
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    ry = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    tilted = mass.solid_box('tilted', 2., (0., 0., 0.), (.4, .2, .1), rotation=ry,
                            evidence='calculated_screening', source='t')
    i1, i3 = 2/12*(.04+.01), 2/12*(.16+.04)
    # Product of inertia of a principal body rotated about y.
    jxz = -np.asarray(tilted.inertia_cg_kg_m2)[0, 2]
    assert jxz == pytest.approx((i1-i3)*s*c)
    cyl = mass.solid_cylinder('rod', 1., (0., 0., 0.), (0., 1., 0.), .01, .5,
                              evidence='calculated_screening', source='t')
    assert np.diag(cyl.inertia_cg_kg_m2) == pytest.approx([(3e-4+.25)/12, 5e-5, (3e-4+.25)/12])
    p1 = mass.point_mass('a', 1., (1., 2., 0.), evidence='calculated_screening', source='t')
    p2 = mass.point_mass('b', 1., (-1., -2., 0.), evidence='calculated_screening', source='t')
    p3 = mass.point_mass('c', 2., (0., 0., 1.), evidence='calculated_screening', source='t')
    total = mass.combine([p1, p2, p3])
    # cg = (0,0,0.5); about it: points (1,2,-.5),(-1,-2,-.5),(0,0,.5).
    expected = np.zeros((3, 3))
    for m, r in ((1., (1, 2, -.5)), (1., (-1, -2, -.5)), (2., (0, 0, .5))):
        r = np.array(r, float)
        expected += m*(r@r*np.eye(3)-np.outer(r, r))
    assert total['cg_m'] == pytest.approx([0, 0, .5])
    assert np.asarray(total['inertia_cg_kg_m2']) == pytest.approx(expected)
    assert total['products_of_inertia_kg_m2']['Jxy'] == pytest.approx(4.)
    about = mass.combine([p1, p2, p3], about_m=(0, 0, 0))
    assert np.asarray(about['inertia_about_kg_m2']) == pytest.approx(expected+4*(.25*np.eye(3)-np.diag([0, 0, .25])))
    # Body axes: Jxz keeps its sign, Jxy and Jyz reverse.
    t = np.array([[1, -2, -3], [-2, 4, -5], [-3, -5, 6.]])
    b = frames.tensor_geometry_to_body(t)
    assert b[0, 2] == t[0, 2] and b[0, 1] == -t[0, 1] and b[1, 2] == -t[1, 2]


def test_combine_matches_existing_b_mass_properties_implementation():
    from cem.glider.mounted_wing import mass_properties
    parts = [mass.solid_box('a', .3, (.1, .05, .02), (.2, .1, .05), evidence='declared_hardware', source='t'),
             mass.lamina('b', .2, (.4, -.1, .03), (1, 0, 0), (0, .9, .1), .15, .3,
                         evidence='declared_hardware', source='t'),
             mass.solid_cylinder('c', .05, (.2, 0, -.01), (1, 1, 0), .005, .3,
                                 evidence='declared_hardware', source='t')]
    ours = mass.combine(parts)
    theirs = mass_properties([dict(mass_kg=p.mass_kg, cg_m=p.cg_m, inertia_kg_m2=p.inertia_cg_kg_m2)
                              for p in parts])
    assert ours['mass_kg'] == pytest.approx(theirs['mass_kg'])
    assert ours['cg_m'] == pytest.approx(theirs['cg_m'])
    assert np.asarray(ours['inertia_cg_kg_m2']) == pytest.approx(np.asarray(theirs['inertia_kg_m2']), abs=1e-15)
    assert ours['state'] == 'declared_or_mixed_not_measured' and ours['measured_aircraft'] is False


def test_tensor_validation_and_evidence_classes():
    with pytest.raises(ValueError, match='symmetric'):
        mass.check_tensor([[1, .1, 0], [0, 1, 0], [0, 0, 1]])
    with pytest.raises(ValueError, match='positive definite'):
        mass.check_tensor(np.diag([1., 1., -1.]))
    with pytest.raises(ValueError, match='triangle'):
        mass.check_tensor(np.diag([1., 1., 3.]))
    with pytest.raises(ValidationError, match='substituted'):
        mass.MassComponent(name='x', mass_kg=1., cg_m=(0., 0., 0.), inertia_cg_kg_m2=((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)),
                           evidence='calculated_screening', self_inertia='point_mass_unknown_omitted', source='t')
    with pytest.raises(ValidationError, match='Measured self-inertia'):
        mass.MassComponent(name='x', mass_kg=1., cg_m=(0., 0., 0.),
                           inertia_cg_kg_m2=((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)),
                           evidence='declared_hardware', self_inertia='measured', source='t')


@pytest.mark.parametrize('family', ['tailless_swept', 'canard', 'tandem'])
def test_family_mass_binding_full_tensor_and_existing_diagonal(family):
    from cem.glider.families import build
    a = build(family)
    record = mass.family_screening_properties(a)
    summary = a.mass_summary()
    assert record['mass_kg'] == pytest.approx(summary['mass_kg'], abs=1e-12)
    assert record['cg_m'] == pytest.approx(summary['cg_m'], abs=1e-12)
    assert record['state'] == 'calculated_screening'
    assert record['measured_aircraft'] is False
    assert set(record['omitted_self_inertia']) == {'equipment', 'body'}
    cg = summary['cg_m']
    # Existing contracts adapter: diagonal point-mass inertia of the same inventory.
    diagonal = [sum(p.mass_kg*sum((getattr(p, ax+'_m')-cg[i])**2 for i, ax in enumerate('xyz') if i != j)
                    for p in a.inventory) for j in range(3)]
    point = np.asarray(record['point_inventory_inertia_cg_kg_m2'])
    assert np.diag(point) == pytest.approx(diagonal, rel=1e-12)
    full = np.asarray(record['inertia_cg_kg_m2'])
    # Strip self-inertia only adds positive semidefinite terms.
    assert np.linalg.eigvalsh(full-point).min() > -1e-15
    products = record['products_of_inertia_kg_m2']
    assert abs(products['Jxy']) < 1e-15 and abs(products['Jyz']) < 1e-15  # mirror symmetry
    assert abs(products['Jxz']) > 1e-6  # dihedral/sweep couple x and z
    tampered = a.inventory[:2]+a.inventory[3:]
    import dataclasses
    with pytest.raises(ValueError, match='inventory'):
        mass.family_screening_properties(dataclasses.replace(a, inventory=tampered))


def test_measured_properties_form_and_comparison():
    calc = mass.combine([mass.solid_box('a', .5, (.2, 0, 0), (.6, .1, .1), evidence='calculated_screening',
                                        source='t')])
    measured = dict(article_revision_sha256=REV, measurement_artifact_sha256=REV, configuration='flight',
        mass_method='calibrated_scale', cg_method='multi_point_scale', inertia_method='bifilar_pendulum',
        mass_kg=.5, mass_uncertainty_kg=.002, cg_m=(.2, 0., 0.), cg_uncertainty_m=(.002, .002, .002),
        inertia_cg_kg_m2=tuple(map(tuple, calc['inertia_cg_kg_m2'])),
        inertia_uncertainty_kg_m2=((1e-4,)*3,)*3)
    assert mass.compare_measured(calc, measured)['state'] == 'consistent'
    off = dict(measured, mass_kg=.52)
    out = mass.compare_measured(calc, off)
    assert out['state'] == 'inconsistent' and out['failures'] == ['mass'] and out['authoritative'] == 'measured'
    with pytest.raises(ValidationError):
        mass.MeasuredAircraftProperties(**dict(measured, inertia_method='estimated'))


# ------------------------------------------------------------------ trim
def _mass_at(point=(.25, 0., 0.)):
    x, y, z = point
    return mass.combine([
        mass.solid_box('fuselage', .3, (x, y, z), (.6, .05, .05), evidence='calculated_screening', source='t'),
        mass.lamina('wing', .2, (x, y, z), (1., 0., 0.), (0., 1., 0.), .2, 1.5,
                    evidence='calculated_screening', source='t')])


def _problem(fx, *, variables=None, equations=('Fz', 'Fx', 'My'), fixed=None, reserve=5., limit=20.,
             required=('fixture_aero',)):
    return dict(revision_sha256=fx.revision, required=list(required),
        variables=variables or [dict(name='speed_m_s', lower=3., upper=30., initial=8.),
                                dict(name='alpha_deg', lower=-5., upper=11., initial=2.),
                                dict(name='pitch_deg', lower=-30., upper=30., initial=-2.)],
        equations=list(equations),
        fixed=fixed or {'beta_deg': 0., 'bank_deg': 0., 'control:elevator': 0., 'control:aileron': 0.},
        controls=[dict(name='elevator', limit_deg=limit, reserve_deg=reserve),
                  dict(name='aileron', limit_deg=limit, reserve_deg=reserve)],
        reference_chord_m=fx.chord_m, reference_span_m=fx.span_m)


def _analytic_glide(fx, m, alpha_rad):
    cl = fx.CL0+fx.CLa*alpha_rad
    cd = fx.CD0+fx.k*cl*cl
    gamma = math.atan2(cd, cl)
    speed = math.sqrt(2*m*G*math.cos(gamma)/(fx.density_kg_m3*fx.area_m2*cl))
    return speed, gamma


def test_positive_fixed_control_trim_matches_closed_form():
    fx = fixtures.LinearAircraft()
    m = _mass_at()
    result = trim.solve(_problem(fx), fx, m)
    assert result['state'] == 'trimmed'
    alpha = -fx.Cm0/fx.Cma
    speed, gamma = _analytic_glide(fx, m['mass_kg'], alpha)
    s = result['solution']
    assert s['alpha_deg'] == pytest.approx(math.degrees(alpha), abs=1e-7)
    assert s['speed_m_s'] == pytest.approx(speed, rel=1e-8)
    assert result['flight_path_angle_deg'] == pytest.approx(-math.degrees(gamma), abs=1e-7)
    assert s['pitch_deg'] == pytest.approx(math.degrees(alpha-gamma), abs=1e-7)
    assert max(abs(v) for v in result['residual'].values()) < 1e-7
    assert result['mass_properties_sha256'] == digest(m)
    assert result['production_admission'] is False and result['qualified_aircraft'] is False
    assert result['authority'] == 'reserve_checked_at_trim_only'
    # Moving the aerodynamic reference point changes only the bookkeeping.
    shifted = fixtures.LinearAircraft(reference_point_m=(.15, 0., .03), Cm0=fx.Cm0)
    moved = trim.solve(_problem(shifted), shifted, _mass_at((.15, 0., .03)))
    assert moved['solution'] == pytest.approx(s, rel=1e-9)


def test_fixed_speed_control_trim_and_saturation():
    fx = fixtures.LinearAircraft(Cm0=.15)
    m = _mass_at()
    alpha0 = -.05/fx.Cma  # an attainable angle; speed set from closed form
    speed, gamma = _analytic_glide(fx, m['mass_kg'], alpha0)
    variables = [dict(name='alpha_deg', lower=-5., upper=11., initial=2.),
                 dict(name='pitch_deg', lower=-30., upper=30., initial=-2.),
                 dict(name='control:elevator', lower=-20., upper=20., initial=1.)]
    fixed = {'speed_m_s': speed, 'beta_deg': 0., 'bank_deg': 0., 'control:aileron': 0.}
    # CL depends on elevator; solve and verify moment and force closure analytically.
    result = trim.solve(_problem(fx, variables=variables, fixed=fixed, reserve=1.), fx, m)
    assert result['state'] == 'trimmed'
    a, d = math.radians(result['solution']['alpha_deg']), math.radians(result['solution']['control:elevator'])
    assert fx.Cm0+fx.Cma*a+fx.controls['elevator']['Cm']*d == pytest.approx(0., abs=1e-9)
    # Reserve requirement violated: trim exists but leaves too little authority.
    reserved = trim.solve(_problem(fx, variables=variables, fixed=fixed, reserve=15.), fx, m)
    assert reserved['state'] == 'control_saturated' and reserved['saturated_controls'] == ['elevator']
    # Trim would need more than the physical limit: never evaluated beyond it.
    strong = fixtures.LinearAircraft(Cm0=.9)
    beyond = trim.solve(_problem(strong, variables=variables, fixed=fixed, reserve=1.), strong, m)
    assert beyond['state'] == 'control_saturated'
    assert beyond['sign_definite_equations'] == ['My']
    states = [abs(v) for v in [beyond['problem']['variables'][2]['lower'], beyond['problem']['variables'][2]['upper']]]
    assert max(states) <= 20.


def test_untrimmable_unbalanced_nonfinite_missing_and_out_of_coverage():
    m = _mass_at()
    untrimmable = fixtures.LinearAircraft(Cm0=.3, Cma=0.)
    out = trim.solve(_problem(untrimmable), untrimmable, m)
    assert out['state'] == 'untrimmable_on_declared_grid' and out['sign_definite_equations'] == ['My']
    assert out.get('solution') is None
    fx = fixtures.LinearAircraft()
    rolled = trim.solve(_problem(fx, fixed={'beta_deg': 0., 'bank_deg': 0., 'control:elevator': 0.,
                                            'control:aileron': 5.}), fx, m)
    assert rolled['state'] == 'unbalanced_unsolved_residuals' and abs(rolled['unsolved_residuals']['Mx']) > 1e-3
    bad = fixtures.LinearAircraft(nonfinite=True)
    assert trim.solve(_problem(bad), bad, m)['state'] == 'nonfinite_or_invalid'
    missing = fixtures.LinearAircraft(unsupported=('body',))
    blocked = trim.solve(_problem(missing, required=('fixture_aero', 'body')), missing, m)
    assert blocked['state'] == 'unsupported' and blocked['resultant']['force_n'] is None
    assert blocked['resultant']['unsupported'][0]['name'] == 'body'
    narrow = fixtures.LinearAircraft(alpha_range_deg=(5., 12.))
    assert trim.solve(_problem(narrow), narrow, m)['state'] == 'out_of_coverage'


def test_problem_rejects_unjustified_variables_and_implicit_state():
    fx = fixtures.LinearAircraft()
    with pytest.raises(ValidationError, match='Unjustified'):
        trim.Problem.model_validate_json(__import__('json').dumps(_problem(fx, variables=[
            dict(name='cg_x_m', lower=0., upper=1., initial=.5), dict(name='alpha_deg', lower=-5., upper=11., initial=2.),
            dict(name='pitch_deg', lower=-30., upper=30., initial=-2.)])))
    with pytest.raises(ValueError, match='fixed or solved'):
        trim.solve(_problem(fx, fixed={'beta_deg': 0., 'bank_deg': 0., 'control:elevator': 0.}), fx, _mass_at())
    with pytest.raises(ValueError, match='physical limit'):
        trim.solve(_problem(fx, fixed={'beta_deg': 0., 'bank_deg': 0., 'control:elevator': 25.,
                                       'control:aileron': 0.}), fx, _mass_at())
    with pytest.raises(ValueError, match='one listed residual'):
        trim.solve(_problem(fx, equations=('Fz', 'Fx')), fx, _mass_at())


# ------------------------------------------------------------------ dynamics
def _analytic_matrix(fx, m, trimmed):
    """Independent textbook small-perturbation matrices about alpha = 0 trim."""
    s = trimmed['flight_state']
    assert abs(s['alpha_deg']) < 1e-8
    V, th = s['speed_m_s'], math.radians(s['pitch_deg'])
    rho, S, c, b = fx.density_kg_m3, fx.area_m2, fx.chord_m, fx.span_m
    qb = .5*rho*V*V
    cl = fx.CL0
    cd = fx.CD0+fx.k*cl*cl
    mass_kg = m['mass_kg']
    I = np.asarray(m['inertia_body_axes_kg_m2'])
    Xu, Zu = -rho*V*S*cd, -rho*V*S*cl
    Xw = qb*S/V*(cl-2*fx.k*cl*fx.CLa)
    Zw = -qb*S/V*(cd+fx.CLa)
    Mw = qb*S*c*fx.Cma/V
    Xq = -qb*S*2*fx.k*cl*fx.CLq*c/(2*V)
    Zq = -qb*S*fx.CLq*c/(2*V)
    Mq = qb*S*c*fx.Cmq*c/(2*V)
    Yv = qb*S/V*(fx.CYb-cd)
    Yp, Yr = qb*S*fx.CYp*b/(2*V), qb*S*fx.CYr*b/(2*V)
    Lv, Lp, Lr = qb*S*b*fx.Clb/V, qb*S*b*fx.Clp*b/(2*V), qb*S*b*fx.Clr*b/(2*V)
    Nv, Np, Nr = qb*S*b*fx.Cnb/V, qb*S*b*fx.Cnp*b/(2*V), qb*S*b*fx.Cnr*b/(2*V)
    A = np.zeros((8, 8))  # u v w p q r phi theta
    inv = np.linalg.inv(I)
    A[0, [0, 2, 4, 7]] = [Xu/mass_kg, Xw/mass_kg, Xq/mass_kg, -G*math.cos(th)]
    A[2, [0, 2, 4, 7]] = [Zu/mass_kg, Zw/mass_kg, Zq/mass_kg+V, -G*math.sin(th)]
    A[1, [1, 3, 5, 6]] = [Yv/mass_kg, Yp/mass_kg, Yr/mass_kg-V, G*math.cos(th)]
    moments = np.zeros((3, 8))
    moments[0, [1, 3, 5]] = [Lv, Lp, Lr]
    moments[1, [2, 4]] = [Mw, Mq]
    moments[2, [1, 3, 5]] = [Nv, Np, Nr]
    A[3:6] = inv@moments
    A[6, 3], A[6, 5] = 1., math.tan(th)
    A[7, 4] = 1.
    return A


def _alpha_zero_case(**coefficients):
    fx = fixtures.LinearAircraft(Cm0=0., **coefficients)
    c, s = math.cos(math.radians(12)), math.sin(math.radians(12))
    tilt = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    m = mass.combine([
        mass.solid_box('fuselage', .3, (.25, 0., 0.), (.6, .05, .08), rotation=tilt,
                       evidence='calculated_screening', source='t'),
        mass.lamina('wing', .2, (.25, 0., 0.), (1., 0., 0.), (0., 1., 0.), .2, 1.5,
                    evidence='calculated_screening', source='t')])
    result = trim.solve(_problem(fx), fx, m)
    assert result['state'] == 'trimmed'
    return fx, m, result


LIMITS = [dict(name='elevator', limit_deg=20.), dict(name='aileron', limit_deg=20.)]


def test_full_inertia_linearization_matches_textbook_reference_with_product_of_inertia():
    fx, m, trimmed = _alpha_zero_case()
    assert abs(m['products_of_inertia_kg_m2']['Jxz']) > 1e-5
    out = dynamics.linearize(trimmed, model=fx, mass=m, required=('fixture_aero',), control_limits=LIMITS,
                             reference_span_m=fx.span_m, reference_chord_m=fx.chord_m)
    assert out['state'] == 'computed'
    reference = _analytic_matrix(fx, m, trimmed)
    assert np.asarray(out['A']) == pytest.approx(reference, rel=1e-5, abs=1e-6)
    ours = np.sort_complex(np.linalg.eigvals(np.asarray(out['A'])))
    expected = np.sort_complex(np.linalg.eigvals(reference))
    assert ours == pytest.approx(expected, rel=1e-5, abs=1e-6)
    names = {mode['mode'] for mode in out['modes']}
    assert {'short_period', 'phugoid', 'dutch_roll', 'roll', 'spiral'} <= names
    # Primed lateral derivative identity with the product of inertia.
    I = np.asarray(m['inertia_body_axes_kg_m2'])
    jxz = -I[0, 2]
    V = trimmed['flight_state']['speed_m_s']
    qb = .5*fx.density_kg_m3*V*V
    Lp = qb*fx.area_m2*fx.span_m*fx.Clp*fx.span_m/(2*V)
    Np = qb*fx.area_m2*fx.span_m*fx.Cnp*fx.span_m/(2*V)
    primed = (Lp/I[0, 0]+jxz*Np/(I[0, 0]*I[2, 2]))/(1-jxz**2/(I[0, 0]*I[2, 2]))
    assert out['A'][3][3] == pytest.approx(primed, rel=1e-5)
    # Control columns: elevator pitch acceleration against the closed form.
    elevator = out['control_order'].index('elevator')
    Md = qb*fx.area_m2*fx.chord_m*fx.controls['elevator']['Cm']
    assert out['B'][4][elevator] == pytest.approx(Md/I[1, 1], rel=1e-5)


def test_lateral_modes_agree_with_existing_reference_without_product_of_inertia():
    from gliders.model.analysis import lateral_modes
    fx = fixtures.LinearAircraft(Cm0=0., CD0=.002, k=0.)
    m = _mass_at()
    trimmed = trim.solve(_problem(fx), fx, m)
    out = dynamics.linearize(trimmed, model=fx, mass=m, required=('fixture_aero',), control_limits=LIMITS,
                             reference_span_m=fx.span_m, reference_chord_m=fx.chord_m)
    V = trimmed['flight_state']['speed_m_s']
    I = np.asarray(m['inertia_body_axes_kg_m2'])
    ref = lateral_modes(dict(CYb=fx.CYb, CYp=fx.CYp, CYr=fx.CYr, Clb=fx.Clb, Clp=fx.Clp, Clr=fx.Clr,
                             Cnb=fx.Cnb, Cnp=fx.Cnp, Cnr=fx.Cnr), V, fx.density_kg_m3, fx.area_m2, fx.span_m,
                        m['mass_kg'], I[0, 0], I[2, 2], math.radians(trimmed['flight_path_angle_deg']), g=G)
    ours = {mode['mode']: mode for mode in out['modes']}
    assert ours['roll']['real_per_s'] == pytest.approx(ref['roll_real'], rel=1e-4)
    assert ours['dutch_roll']['natural_frequency_rad_s'] == pytest.approx(ref['dutch_roll_wn'], rel=1e-3)
    # The legacy screening routine omits two exact kinematic terms: tan(theta) r
    # in phi_dot and the drag rotation -D/V in the side-force derivative.
    # Removing exactly those from our lateral block reproduces it.
    lateral = np.asarray(out['A'])[np.ix_([1, 3, 5, 6], [1, 3, 5, 6])].copy()
    qb = .5*fx.density_kg_m3*V*V
    lateral[3, 2] = 0.
    lateral[0, 0] += qb*fx.area_m2*fx.CD0/(V*m['mass_kg'])
    assert np.sort_complex(np.linalg.eigvals(lateral)) == pytest.approx(np.sort_complex(ref['eigs']),
                                                                         rel=1e-5, abs=1e-7)
    assert ours['spiral']['real_per_s'] != pytest.approx(ref['spiral_real'], rel=.1)


def test_dynamics_rejections_and_unstable_reference():
    fx, m, trimmed = _alpha_zero_case(Cma=.3)
    out = dynamics.linearize(trimmed, model=fx, mass=m, required=('fixture_aero',), control_limits=LIMITS,
                             reference_span_m=fx.span_m, reference_chord_m=fx.chord_m)
    assert out['state'] == 'computed' and out['stability'] == 'unstable_mode_present'
    assert out['qualified_aircraft'] is False
    fx, m, trimmed = _alpha_zero_case()
    assert dynamics.linearize(dict(trimmed, state='control_saturated'), model=fx, mass=m,
        required=('fixture_aero',), control_limits=LIMITS, reference_span_m=1.5,
        reference_chord_m=.2)['state'] == 'unsupported'

    class NoRates:
        def __call__(self, state):
            if any(state[k] for k in ('p_rad_s', 'q_rad_s', 'r_rad_s')):
                return [wrench.unsupported('fixture_aero', 'aerodynamic_lifting', fx.revision,
                                           'no rate dependence')]
            return fx(state)
    blocked = dynamics.linearize(trimmed, model=NoRates(), mass=m, required=('fixture_aero',),
        control_limits=LIMITS, reference_span_m=1.5, reference_chord_m=.2)
    assert blocked['state'] == 'unsupported'
    near = dynamics.linearize(trimmed, model=fx, mass=m, required=('fixture_aero',),
        control_limits=[dict(name='elevator', limit_deg=.3), dict(name='aileron', limit_deg=20.)],
        reference_span_m=1.5, reference_chord_m=.2)
    assert near['state'] == 'control_at_limit'
    with pytest.raises(ValueError, match='Explicit limit'):
        dynamics.linearize(trimmed, model=fx, mass=m, required=('fixture_aero',), control_limits=LIMITS[:1],
                           reference_span_m=1.5, reference_chord_m=.2)
    with pytest.raises(ValueError, match='Mass properties differ'):
        dynamics.linearize(trimmed, model=fx, mass=_mass_at(), required=('fixture_aero',),
                           control_limits=LIMITS, reference_span_m=1.5, reference_chord_m=.2)


def test_control_authority_is_evaluated_at_limits_with_explicit_requirement():
    fx, m, trimmed = _alpha_zero_case()
    kw = dict(model=fx, mass=m, required=('fixture_aero',), control_limits=LIMITS)
    unassessed = dynamics.control_authority(trimmed, **kw)
    assert unassessed['state'] == 'unassessed_missing_requirement'
    V = trimmed['flight_state']['speed_m_s']
    qb = .5*fx.density_kg_m3*V*V
    roll = unassessed['channels']['aileron']['moment_increment_body_nm']['+'][0]
    assert roll == pytest.approx(qb*fx.area_m2*fx.span_m*fx.controls['aileron']['Cl']*math.radians(20))
    assert dynamics.control_authority(trimmed, requirement=dict(L=roll*.9), **kw)['state'] == \
        'requirement_met_at_fixed_state'
    short = dynamics.control_authority(trimmed, requirement=dict(L=roll*1.1, N=1e-6), **kw)
    assert short['state'] == 'insufficient_authority' and short['shortfall'] == ['L']
    assert short['qualified_control_authority'] is False
