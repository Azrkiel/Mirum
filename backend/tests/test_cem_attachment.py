"""Real attachment exports and equilibrium/nominal strength references."""
import copy

import numpy as np
import pytest

from cem.glider import attachment as mount
from cem.glider import segmented_geometry as geo
from cem.glider.physical_loads import InstalledLoadCase, StationLoad
from cem.glider.geometry import load_part_mesh
from gliders.model.export import write_stl

G, O = 'c'*64, 'd'*64


def case(q=2., torque=.01):
    return InstalledLoadCase(geometry_sha256=G, operating_sha256=O,
        stations=(StationLoad(y_m=0., fz_n_m=q, my_nm_m=torque),
                  StationLoad(y_m=.5, fz_n_m=q, my_nm_m=torque)))


@pytest.fixture(scope='module')
def delivered(tmp_path_factory):
    point = np.array([.042, 0., .1])
    assembly = mount.construct(point, .0033)
    collar = geo.tube([point+[0., -.1, 0.], point+[0., .1, 0.]],
        [[0., 1., 0.]]*2, [[0., 1., 0.]], .0033, .0023, count=64)
    directory = tmp_path_factory.mktemp('mount')
    meshes = {}
    for name, part in {**assembly['parts'], 'collar': dict(mesh=collar)}.items():
        path = directory/(name+'.stl')
        write_stl(str(path), part['mesh'].vertices, part['mesh'].faces)
        meshes[name] = load_part_mesh(path)
    return point, assembly, meshes.pop('collar'), meshes


def analyze(interfaces, left=None, right=None):
    return mount.analyze(case() if left is None else left, case() if right is None else right,
        geometry_sha256=G, operating_sha256=O, half_span_m=.5, interfaces=interfaces)


def test_real_installed_parts_are_closed_and_contacts_recovered(delivered):
    point, assembly, collar, meshes = delivered
    assert set(meshes) == {'mount_plate_-1', 'mount_plate_1', 'mount_rivet_0', 'mount_rivet_1',
                          'mount_collar_bond_-1', 'mount_collar_bond_1', 'fuselage_keel'}
    evidence = mount.measure(point, collar, meshes, collar_radius_m=.0033)
    assert evidence['contact_state'] == 'canonical_conforming_bearing_interfaces'
    assert evidence['keel_width_m'] == pytest.approx(.030, abs=geo.TOL)
    assert [p['thickness_m'] for p in evidence['plates']] == pytest.approx([.005, .005], abs=geo.TOL)
    assert [r['section']['extreme_radius_m'] for r in evidence['rivets']] == pytest.approx([.0015]*2, abs=geo.TOL)
    for name, mesh in meshes.items():
        assert mesh.volume == pytest.approx(assembly['parts'][name]['mesh'].volume, rel=1e-5)
        assert mesh.is_watertight and mesh.body_count == 1


@pytest.mark.parametrize('left_q,right_q,left_t,right_t', [(2., 2., 0., 0.), (0., 4., 0., 0.),
    (-2., 3., .01, .02), (0., 0., .03, .03)])
def test_asymmetric_and_pitch_reaction_equilibrium(delivered, left_q, right_q, left_t, right_t):
    point, _, collar, meshes = delivered
    e = mount.measure(point, collar, meshes, collar_radius_m=.0033)
    r = analyze(e, case(left_q, left_t), case(right_q, right_t))
    assert max(abs(v) for v in r['equilibrium_residual']) < 1e-12
    assert r['applied'] == pytest.approx(dict(fz_n=(left_q+right_q)*.5,
        mx_nm=(right_q-left_q)*.5**2/2, my_nm=(left_t+right_t)*.5))
    if left_q == right_q and left_t == right_t == 0:
        assert np.asarray(r['reactions_n']).ravel() == pytest.approx([-left_q*.25]*4)
    assert r['state'] in ('incomplete', 'failed')
    assert r['load_path_state'] == 'partial' and r['manufacturing_ready'] is False


def test_rivet_shear_matches_force_over_measured_area_and_overloads_fail(delivered):
    point, _, collar, meshes = delivered
    e = mount.measure(point, collar, meshes, collar_radius_m=.0033)
    r = analyze(e, case(torque=0.), case(torque=0.))
    assert r['supported_checks_state'] == 'passed'
    for rivet, actual in zip(r['rivets'], e['rivets']):
        assert rivet['shear_pa'] == pytest.approx(4/3*.5/actual['section']['area_m2'])
        assert rivet['keel_bearing_bound_pa'] == pytest.approx(1./(.003*.030), rel=1e-5)
        assert rivet['pin_bearing']['max_bending_nm'] == pytest.approx(.005, rel=1e-5)
        assert abs(rivet['pin_bearing']['force_residual_n']) < 1e-12
    overloaded = analyze(e, case(20000., 100.), case(20000., 100.))
    assert overloaded['state'] == 'failed'
    assert not overloaded['checks']['rivet_0_shear']
    assert not overloaded['checks']['keel_0_bearing']


def test_pitch_torque_requires_the_explicit_collar_bond_capacity(delivered):
    point, _, collar, meshes = delivered
    e = mount.measure(point, collar, meshes, collar_radius_m=.0033)
    r = analyze(e, case(0., 100.), case(0., 100.))
    assert not r['checks']['collar_bond_0_shear']
    assert not r['checks']['collar_bond_1_shear']
    assert r['state'] == 'failed'


def test_measured_net_ligaments_match_analytical_reference(delivered):
    point, _, collar, meshes = delivered
    e = mount.measure(point, collar, meshes, collar_radius_m=.0033)
    p = e['plates'][0]
    at_bolts = p['net_sections'][1]
    assert at_bolts['area_m2'] == pytest.approx(.005*(.044-4*.0015), rel=2e-5)
    # Both circular bore diameters lie on this cut; subtract their exact
    # rectangular void sections from the full rectangular net section.
    integral = .005*.044**3/12
    for x in (-.012, .012):
        integral -= .005*((x+.0015)**3-(x-.0015)**3)/3
    assert at_bolts['inertia_y_m4'] == pytest.approx(integral, rel=2e-5)


@pytest.mark.parametrize('missing', ['mount_plate_-1', 'mount_plate_1', 'mount_rivet_0', 'fuselage_keel', 'mount_collar_bond_1'])
def test_missing_support_or_member_rejected(delivered, missing):
    point, _, collar, original = delivered
    meshes = dict(original); meshes.pop(missing)
    with pytest.raises(ValueError, match='Missing installed'):
        mount.measure(point, collar, meshes, collar_radius_m=.0033)


@pytest.mark.parametrize('shifted', ['mount_plate_-1', 'mount_rivet_1', 'fuselage_keel'])
def test_disconnected_or_revised_member_rejected(delivered, shifted):
    point, _, collar, original = delivered
    meshes = dict(original); mesh = meshes[shifted].copy()
    mesh.apply_translation([.001, 0., 0.]); meshes[shifted] = mesh
    with pytest.raises(ValueError, match='construction revision'):
        mount.measure(point, collar, meshes, collar_radius_m=.0033)


def test_short_collar_and_stale_loads_cannot_supply_attachment_evidence(delivered):
    point, _, collar, meshes = delivered
    short = collar.copy(); short.vertices[:, 1] *= .1
    with pytest.raises(ValueError, match='cover'): mount.measure(point, short, meshes, collar_radius_m=.0033)
    e = mount.measure(point, collar, meshes, collar_radius_m=.0033)
    c = case().model_copy(update={'geometry_sha256': 'f'*64})
    with pytest.raises(ValueError, match='Stale'): analyze(e, c)
    bad = copy.deepcopy(e); bad['plates'].pop()
    with pytest.raises(ValueError, match='Missing distinct'): analyze(bad)


def test_intersecting_or_unenclosed_bores_are_rejected():
    for holes in [[(0., 0., 2.)], [(0., 0., .2), (.1, 0., .2)]]:
        with pytest.raises(ValueError):
            mount.perforated_prism([-1., -1., 1., 1.], [-.1, .1], holes)


def test_distributed_pin_reference_and_asymmetric_keel_couple():
    bounds = [[-.020, -.015], [.015, .020]]
    result = mount.pin_bearing([.5, .5], bounds, .030)
    assert result['max_shear_n'] == pytest.approx(.5)
    assert result['max_bending_nm'] == pytest.approx(.005)
    assert result['keel_peak_line_load_n_m'] == pytest.approx(1/.030)
    asymmetric = mount.pin_bearing([-1., 1.], bounds, .030)
    assert asymmetric['keel_peak_line_load_n_m'] == pytest.approx(6*.035/.030**2)
    assert asymmetric['keel_absolute_force_n'] > 0
    assert asymmetric['force_residual_n'] == pytest.approx(0., abs=1e-12)
    assert asymmetric['moment_residual_nm'] == pytest.approx(0., abs=1e-12)
