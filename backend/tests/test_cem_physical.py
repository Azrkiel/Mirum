"""Analytical references and real exported fixture geometry; no aero qualification."""
import copy
import math

import numpy as np
import pytest
from pydantic import ValidationError

from cem.glider.physical_loads import (InstalledLoadCase, StationLoad, BeamMaterial,
                                     RootSupport, analyze, resultants)
from cem.glider.physical_fixture import construct, export_fixture, verify_fixture
from cem.glider.shapes import ShapedWing

G, O = 'a'*64, 'b'*64


def case(q=2., torque=.03, tip=.5):
    return InstalledLoadCase(geometry_sha256=G, operating_sha256=O,
        stations=(StationLoad(y_m=0., fz_n_m=q, my_nm_m=torque),
                  StationLoad(y_m=tip, fz_n_m=q, my_nm_m=torque)))


def arguments():
    r = .003
    return dict(geometry_sha256=G, operating_sha256=O, half_span_m=.5,
        section=dict(area_m2=math.pi*r*r, min_inertia_m4=math.pi*r**4/4,
                     polar_inertia_m4=math.pi*r**4/2, extreme_radius_m=r),
        material=BeamMaterial(youngs_modulus_pa=100e9, shear_modulus_pa=40e9,
            allowable_normal_pa=400e6, allowable_shear_pa=100e6, safety_factor=2.),
        support=RootSupport(overlap_m=.03, radius_m=r, bond_allowable_shear_pa=8e6, safety_factor=2.),
        max_deflection_m=.05, max_twist_rad=.1)


def test_uniform_beam_reference_reactions_deflection_twist_and_bond():
    args = arguments(); c = case(); r = analyze(c, **args)
    assert r['state'] == 'passed'
    assert r['root_resultants'] == pytest.approx(dict(shear_n=1., bending_nm=.25, torque_nm=.015))
    assert r['reaction'] == pytest.approx(dict(fz_n=-1., mx_nm=-.25, my_nm=-.015))
    ei = 100e9*args['section']['min_inertia_m4']
    gj = 40e9*args['section']['polar_inertia_m4']
    assert r['tip_deflection_bound_m'] == pytest.approx(2*.5**4/(8*ei), rel=1e-12)
    assert r['tip_twist_bound_rad'] == pytest.approx(.03*.5**2/(2*gj), rel=1e-12)
    expected = .25/(math.pi*.003**2*.03)+1/(2*math.pi*.003*.03)+.015/(2*math.pi*.003**2*.03)
    assert r['stress_bounds_pa']['root_bond'] == pytest.approx(expected)
    assert r['manufacturing_ready'] is False


def test_triangular_distributed_load_reference_and_convergence():
    c = case().model_copy(update={'stations': (StationLoad(y_m=0., fz_n_m=0., my_nm_m=0.),
                                              StationLoad(y_m=.5, fz_n_m=2., my_nm_m=.03))})
    assert resultants(c, 0.) == pytest.approx(dict(shear_n=.5, bending_nm=2*.5**2/3, torque_nm=.0075))
    a = analyze(c, **arguments())
    b = analyze(c, **arguments(), subdivisions=8)
    assert a['tip_deflection_bound_m'] == pytest.approx(b['tip_deflection_bound_m'], rel=1e-12)
    ei = 100e9*arguments()['section']['min_inertia_m4']
    assert a['tip_deflection_bound_m'] == pytest.approx(11*2*.5**4/(120*ei))
    assert resultants(c, .5) == dict(shear_n=0., bending_nm=0., torque_nm=0.)


def test_signed_loads_cannot_hide_outboard_failure_by_cancelling_root_shear():
    c = case().model_copy(update={'stations': (StationLoad(y_m=0., fz_n_m=-200., my_nm_m=-10.),
                                              StationLoad(y_m=.5, fz_n_m=200., my_nm_m=10.))})
    assert resultants(c, 0.)['shear_n'] == pytest.approx(0., abs=1e-12)
    r = analyze(c, **arguments())
    assert r['state'] == 'failed'
    assert r['stress_bounds_pa']['normal'] > 0
    assert r['tip_deflection_bound_m'] > 0


@pytest.mark.parametrize('mutation', ['geometry', 'operating', 'support', 'radius', 'section', 'limit', 'span'])
def test_invalid_or_missing_installed_inputs_rejected(mutation):
    args = arguments()
    if mutation == 'geometry': args['geometry_sha256'] = 'c'*64
    if mutation == 'operating': args['operating_sha256'] = 'c'*64
    if mutation == 'support': args['support'] = None
    if mutation == 'radius': args['support'] = args['support'].model_copy(update={'radius_m': .002})
    if mutation == 'section': args['section']['area_m2'] = float('nan')
    if mutation == 'limit': args['max_deflection_m'] = float('inf')
    if mutation == 'span': args['half_span_m'] = .6
    with pytest.raises(ValueError): analyze(case(), **args)


def test_fixture_contract_rejects_missing_coverage_nonfinite_and_aero_labels():
    data = case().model_dump()
    for mutation in ('duplicate', 'root', 'nan', 'aero'):
        d = copy.deepcopy(data)
        if mutation == 'duplicate': d['stations'][1]['y_m'] = 0.
        if mutation == 'root': d['stations'][0]['y_m'] = .1
        if mutation == 'nan': d['stations'][0]['fz_n_m'] = float('nan')
        if mutation == 'aero': d['source'] = 'validated_aerodynamics'
        with pytest.raises(ValidationError): InstalledLoadCase.model_validate(d)


def test_overload_and_short_joint_fail_strength_and_stiffness():
    r = analyze(case(q=2000., torque=20.), **arguments())
    assert r['state'] == 'failed' and not any(r['checks'].values())
    args = arguments()
    args['support'] = args['support'].model_copy(update={'overlap_m': .00001})
    assert analyze(case(), **args)['checks']['root_bond'] is False


def wing():
    return ShapedWing(id='task-b-fixture', style='polyhedral', span=1., root_chord=.14, taper=.8,
        dihedral_deg=0., sweep_qc_deg=0., incidence_deg=0., washout_deg=0., naca='0012',
        chord_factors=(1., .9, 1.1, .95, 1.))


@pytest.fixture(scope='module')
def delivered(tmp_path_factory):
    w = wing(); a = construct(w)
    directory = tmp_path_factory.mktemp('physical-fixture')
    manifest = export_fixture(a, directory)
    return w, a, directory, manifest


def test_real_shaped_material_exports_have_measured_mass_inertia_and_sections(delivered):
    w, a, directory, manifest = delivered
    r = verify_fixture(w, .004, manifest, directory)
    assert len(r['measured_parts']) == 11
    assert r['state'] == 'incomplete' and r['manufacturing_ready'] is False
    assert r['wing_mass_kg'] == pytest.approx(sum(p['mesh'].volume*p['material']['density_kg_m3']
        for p in a['parts'].values()), rel=1e-5)
    assert abs(r['wing_cg_m'][1]) < 1e-8
    assert np.linalg.eigvalsh(r['wing_inertia_kg_m2']).min() > 0
    assert r['material_volume_relative_error'] < 1e-5
    theta = 2*math.pi/64
    expected = 64*math.sin(theta)*(2+math.cos(theta))*.002**4/24
    assert r['measured_section']['min_inertia_m4'] == pytest.approx(expected, rel=1e-5)
    # The measured fixture feeds the load analysis without pretending to be
    # a complete aircraft or to use Task A's still-unavailable validated loads.
    args = arguments(); args['geometry_sha256'] = a['geometry_sha256']
    args['section'] = r['measured_section']; args['support'] = RootSupport(**r['measured_support'])
    c = case(q=.1, torque=0.).model_copy(update={'geometry_sha256': a['geometry_sha256']})
    result = analyze(c, **args)
    assert result['state'] == 'passed'
    assert result['qualification_state'] == 'analytical_fixture_only'


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'law', 'revision', 'path', 'hash'])
def test_delivered_inventory_and_revision_rejections(delivered, mutation):
    w, _, directory, original = delivered
    m = copy.deepcopy(original)
    if mutation == 'missing': m['parts'].pop()
    if mutation == 'duplicate': m['parts'][-1] = m['parts'][0]
    if mutation == 'law': m['parts'][0]['material']['density_kg_m3'] *= 2
    if mutation == 'revision': m['geometry_sha256'] = 'f'*64
    if mutation == 'path': m['parts'][0]['file'] = '../spar.stl'
    if mutation == 'hash': m['parts'][0]['sha256'] = '0'*64
    with pytest.raises(ValueError): verify_fixture(w, .004, m, directory)


def test_rehashed_disconnected_or_shifted_part_cannot_pass(delivered, tmp_path):
    import hashlib
    import shutil
    from cem.glider.geometry import load_part_mesh
    from gliders.model.export import write_stl
    w, _, directory, original = delivered
    for p in directory.iterdir(): shutil.copy2(p, tmp_path/p.name)
    m = copy.deepcopy(original)
    p = next(x for x in m['parts'] if x['name'] == 'spar')
    path = tmp_path/p['file']; mesh = load_part_mesh(path)
    mesh.apply_translation([.001, 0., 0.]); write_stl(str(path), mesh.vertices, mesh.faces)
    p['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='construction revision'):
        verify_fixture(w, .004, m, tmp_path)


def test_shape_revision_and_unsupported_envelope_cannot_reuse_fixture(delivered):
    w, _, directory, manifest = delivered
    changed = copy.deepcopy(w); changed.chord_factors = (1., 1., 1., 1., 1.)
    with pytest.raises(ValueError, match='revision'): verify_fixture(changed, .004, manifest, directory)
    for field in ('dihedral_deg', 'sweep_qc_deg', 'incidence_deg', 'washout_deg'):
        changed = copy.deepcopy(w); setattr(changed, field, 1.)
        with pytest.raises(ValueError, match='envelope'): construct(changed)


def test_physical_shaped_modes_remain_closed_until_full_evidence_exists():
    from cem.glider.requirements import GliderMissionRequirements
    for mode in ('foam_core_wing_v1', 'segmented_foam_wing_v1', 'bonded_straight_shaped_wing_fixture_v1'):
        with pytest.raises(ValidationError):
            GliderMissionRequirements(launch={'method': 'drop'}, process='sheet_foam',
                                      construction=mode, discovery={'wing_shape': 'multi_station'})


def test_actual_disconnected_spar_is_rejected_even_with_updated_hash(delivered, tmp_path):
    import hashlib
    import shutil
    import trimesh
    from cem.glider.segmented_geometry import tube
    from gliders.model.export import write_stl
    w, _, directory, original = delivered
    for p in directory.iterdir(): shutil.copy2(p, tmp_path/p.name)
    m = copy.deepcopy(original)
    item = next(p for p in m['parts'] if p['name'] == 'spar')
    axis = np.array([0., 1., 0.]); x = .3*w.root_chord
    halves = [tube(np.array([[x, a, 0.], [x, b, 0.]]), [axis, axis], [axis], .002, count=64)
              for a, b in [(-.5, -.01), (.01, .5)]]
    mesh = trimesh.util.concatenate(halves); path = tmp_path/item['file']
    write_stl(str(path), mesh.vertices, mesh.faces)
    item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='connected'):
        verify_fixture(w, .004, m, tmp_path)
