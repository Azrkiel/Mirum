"""Segmented installed geometry, measured sections and bounded joint evidence."""
import copy
import json
import math

import numpy as np
import pytest

from cem.glider import segmented as seg
from cem.glider import segmented_geometry as geom
from cem.glider.calibration import initial
from cem.glider.model import configuration, evaluate_candidate
from cem.glider.requirements import GliderMissionRequirements as Mission


def example():
    from backend.tests.test_cem_pipeline import example as base
    _, _, gl = base()
    req = Mission(launch={'method': 'drop'}, process='sheet_foam',
                  construction='segmented_foam_wing_v1', reference_target=1200.,
                  constraints={'payload': {'mass_kg': .06, 'diameter_m': .045, 'length_m': .06}},
                  discovery={'budget_evals': 8})
    request, gl.cfg = configuration(req, initial(), 'sheet_foam')
    return req, request, gl


def test_oblique_solid_and_hollow_sections_are_measured_in_their_own_frame():
    axis = geom.unit([.07, 1., .2]); normal = geom.unit([0., 1., .1])
    points = [np.zeros(3), .3*axis]
    for inner in (0., .0015):
        mesh = geom.tube(points, [normal, normal], [axis], .0025, inner)
        result = geom.section(mesh, .15*axis, axis, hollow=bool(inner))
        theta = 2*math.pi/48
        expected = 48*math.sin(theta)*(2+math.cos(theta))*(.0025**4-inner**4)/24
        assert result['min_inertia_m4'] == pytest.approx(expected, rel=2e-6)
        assert result['area_m2'] == pytest.approx(24*math.sin(theta)*(.0025**2-inner**2), rel=2e-6)


def test_segmented_polyhedral_parts_are_closed_and_fit_installed_channels():
    _, _, gl = example()
    assembly = seg.construct(gl, .004)
    assert len(assembly['panels']) == 4
    assert len(assembly['joints']) == 3
    assert len(assembly['parts']) == 14
    for item in assembly['parts'].values():
        mesh = item['mesh']
        assert mesh.is_watertight and mesh.is_winding_consistent and mesh.body_count == 1
        assert mesh.volume > 0
    assert sum(p['outer'].volume for p in assembly['panels']) == pytest.approx(
        sum(m.volume for m in seg.reference_wings(gl)), rel=2e-6)
    assert min(p['web_m'] for p in assembly['panels']) >= .001-geom.TOL


@pytest.fixture(scope='module')
def delivered(tmp_path_factory):
    from dataclasses import asdict
    from gliders.design_spec import placement
    from gliders.search import finite_json
    from cem.core import digest
    from cem.glider.service import build_design
    req, request, gl = example()
    result = evaluate_candidate(gl, request, req, initial(), 'sheet_foam')
    assert result['valid'], result['fail_reasons']
    revision = dict(process='sheet_foam', parameters=dict(wing=asdict(gl.wing), fuselage=asdict(gl.fuse), tail=asdict(gl.tail)),
                    placement=placement(gl))
    elite = finite_json(dict(elite_id=digest(revision)[:16], result=result, **revision))
    directory = tmp_path_factory.mktemp('segmented-delivery')
    report = build_design(req, initial(), elite, directory)
    # Parent validation receives decoded worker JSON, not NumPy scalars from
    # the in-process aerodynamic solver.
    report = json.loads((directory/'result.json').read_text())
    return req, gl, elite, directory, report


def test_real_dihedral_design_passes_closure_and_parent_archive_validation(delivered):
    from cem.evidence import validate
    from cem.glider.service import identity
    req, gl, elite, directory, report = delivered
    assert report['status'] == 'screened', report['closure']['failures']
    assert report['accepted_for_manufacture'] is False and report['engineering_certified'] is False
    assert report['closure']['manufacturing_ready'] is False
    evidence = report['closure']['segmented_structure']
    assert evidence['state'] == 'passed' and evidence['load_path_state'] == 'unverified'
    assert len(evidence['panels']) == 4 and len(evidence['joints']) == 3
    assert evidence['mass_kg'] == report['closure']['measured_mass_kg']
    assert evidence['min_channel_clearance_m'] >= .00025-geom.TOL
    assert report['closure']['mass_relative_error'] < .05
    validate(report, dict(kind='design', requirements=req.model_dump(mode='json'), calibration=initial(), elite=elite),
             identity(req, initial()), directory)


def test_measured_sections_and_bonds_fail_when_loading_is_increased(delivered):
    from cem.glider.geometry import load_part_mesh
    req, gl, elite, directory, report = delivered
    assembly = seg.selected(gl)
    meshes = {p['name']: load_part_mesh(directory/p['file']) for p in report['bundle']['parts']
              if p['name'] in seg.material_inventory(gl)}
    result = seg.structural_checks(assembly, meshes, 10*report['closure']['measured_mass_kg'], gl.wing.span, 'drop')
    assert result['state'] == 'failed'
    assert any('spar_' in f for f in result['failures'])
    assert any('joint_' in f for f in result['failures'])
    assert any('bond_' in f for f in result['failures'])


def test_load_envelope_has_known_root_force_and_moment():
    _, _, gl = example()
    assembly = seg.construct(gl, .004)
    moment, shear, slope = seg.load_envelope(assembly, .2, gl.wing.span, 'drop', 0.)
    assert moment == pytest.approx(2*.2*9.81*gl.wing.span/(3*math.pi))
    assert shear == pytest.approx(2*.2*9.81/2)
    assert slope >= 0
    assert seg.load_envelope(assembly, .2, gl.wing.span, 'drop', gl.wing.span/2)[:2] == (0., 0.)


@pytest.mark.parametrize('mutation', ['mass', 'overlap', 'bond_stress', 'material', 'load_path', 'missing_joint', 'section'])
def test_parent_rejects_forged_joint_and_section_evidence(delivered, mutation):
    from cem.evidence import segmented_evidence
    req, gl, elite, directory, original = delivered
    report = copy.deepcopy(original)
    e = report['closure']['segmented_structure']
    if mutation == 'mass': report['closure']['measured_mass_kg'] *= .8
    elif mutation == 'overlap': e['joints'][0]['laps'][0]['overlap_m'] *= 2
    elif mutation == 'bond_stress': e['joints'][0]['laps'][0]['bond_shear_pa'] = 0.
    elif mutation == 'material': e['adhesive_assumptions']['allowable_shear_pa'] *= 2
    elif mutation == 'load_path': e['load_path_state'] = 'passed'
    elif mutation == 'missing_joint': e['joints'].pop()
    elif mutation == 'section': e['panels'][0]['section']['min_inertia_m4'] *= 2
    with pytest.raises(ValueError):
        segmented_evidence(report, dict(kind='design', requirements=req.model_dump(mode='json'), calibration=initial(), elite=elite), directory)


@pytest.mark.parametrize('name', ['joint_1', 'bond_0', 'spar_panel_2', 'wing_panel_0'])
def test_missing_material_cannot_pass_canonical_part_accounting(delivered, tmp_path, monkeypatch, name):
    from cem.glider.closure import verify
    from cem.glider import closure
    req, gl, elite, directory, original = delivered
    bundle = copy.deepcopy(original['bundle'])
    bundle['parts'] = [p for p in bundle['parts'] if p['name'] != name]
    def forbidden(*a, **kw): raise AssertionError('Accounting must fail before aerodynamic evaluation')
    monkeypatch.setattr(closure, 'evaluate_candidate', forbidden)
    result = verify(gl, elite['result'], bundle, directory, configuration(req, initial(), 'sheet_foam')[0], req, initial(), 'sheet_foam')
    assert result['state'] == 'failed'
    assert 'accounting' in ' '.join(result['failures'])


def test_shortened_actual_joint_lap_fails_before_using_declared_length():
    _, _, gl = example()
    assembly = seg.construct(gl, .004)
    meshes = {n: p['mesh'].copy() for n, p in assembly['parts'].items()}
    j = assembly['joints'][1]; point, axes = j['point'], j['axes']
    half = seg.JOINT['overlap_m']/2
    ends = [point-half*axes[0], point, point+half*axes[1]]
    normal = geom.unit(axes[0]+axes[1])
    meshes['joint_1'] = geom.tube(ends, [axes[0], normal, axes[1]], axes, j['outer_radius_m'], j['inner_radius_m'])
    meshes['bond_1'] = geom.tube(ends, [axes[0], normal, axes[1]], axes, j['inner_radius_m'], j['radius_m'])
    with pytest.raises(ValueError, match='overlap'):
        seg.structural_checks(assembly, meshes, .2, gl.wing.span, 'drop')


def test_geometry_revision_cannot_be_replaced_by_a_refreshed_hash(delivered):
    from cem.glider.geometry import load_part_mesh
    req, gl, elite, directory, report = delivered
    meshes = {p['name']: load_part_mesh(directory/p['file']) for p in report['bundle']['parts']
              if p['name'] in seg.material_inventory(gl)}
    meshes['joint_1'].apply_translation([.001, 0., 0.])
    with pytest.raises(ValueError):
        seg.verify_delivery(gl, meshes, report['closure']['measured_mass_kg'])


def test_undersized_actual_sections_fail_even_with_unchanged_declared_radius():
    _, _, gl = example()
    assembly = seg.construct(gl, .004)
    meshes = {n: p['mesh'].copy() for n, p in assembly['parts'].items()}
    for i, panel in enumerate(assembly['panels']):
        rod = meshes[f'spar_panel_{i}']; point, axis = panel['points'][0], panel['axis']
        delta = rod.vertices-point; axial = np.outer(delta@axis, axis)
        rod.vertices = point+axial+.5*(delta-axial)
    evidence = seg.structural_checks(assembly, meshes, .2, gl.wing.span, 'drop')
    assert evidence['state'] == 'failed'
    assert any('spar_' in f for f in evidence['failures'])


def test_bond_holes_match_rod_polygons_at_both_sides_of_every_joint():
    _, _, gl = example()
    assembly = seg.construct(gl, .004)
    directions = np.column_stack([np.cos(np.linspace(0, 2*np.pi, 137)), np.sin(np.linspace(0, 2*np.pi, 137))])
    for i, joint in enumerate(assembly['joints']):
        for sign, panel_index, axis in [(-1, i, joint['axes'][0]), (1, i+1, joint['axes'][1])]:
            point = joint['point']+sign*axis*joint['overlap_m']/2
            frame = geom.basis(axis)
            rod = geom.section_loops(assembly['parts'][f'spar_panel_{panel_index}']['mesh'], point, axis)
            bond = geom.section_loops(assembly['parts'][f'bond_{i}']['mesh'], point, axis)
            rod_xy = (rod[0]-point)@frame
            bond_xy = min(((loop-point)@frame for loop in bond), key=lambda xy: np.max(np.linalg.norm(xy, axis=1)))
            # Support functions ignore incidental collinear cut vertices while
            # detecting a different polygon phase and material overlap/gap.
            assert np.max(rod_xy@directions.T, axis=0) == pytest.approx(
                np.max(bond_xy@directions.T, axis=0), abs=1e-10)


def test_segmented_mode_does_not_substitute_an_unsupported_process_or_layout():
    from pydantic import ValidationError
    for process in ('auto', 'balsa', 'fdm_lwpla'):
        with pytest.raises(ValidationError):
            Mission(launch={'method': 'drop'}, process=process, construction=seg.MODE)
    _, _, gl = example(); gl.wing.style = 'elliptical'
    with pytest.raises(ValueError, match='polyhedral'):
        seg.construct(gl, .004)


def test_segmented_design_json_and_material_stl_are_repeatable(delivered, tmp_path):
    from cem.glider.service import build_design
    req, gl, elite, directory, report = delivered
    again = build_design(req, initial(), elite, tmp_path)
    assert again['status'] == report['status']
    assert (tmp_path/'design.json').read_bytes() == (directory/'design.json').read_bytes()
    for path in directory.glob('*.stl'):
        assert path.read_bytes() == (tmp_path/path.name).read_bytes()


def test_twenty_percent_mass_injection_fails_segmented_closure(delivered):
    from cem.glider.closure import verify
    req, gl, elite, directory, report = delivered
    wrong = copy.deepcopy(elite['result']); wrong['mass_kg'] *= 1.2
    result = verify(gl, wrong, report['bundle'], directory, configuration(req, initial(), 'sheet_foam')[0], req, initial(), 'sheet_foam')
    assert result['state'] == 'failed'
    assert any('mass' in reason for reason in result['failures'])


def test_rehashed_and_remeasured_joint_shift_is_rejected_by_parent(delivered, tmp_path):
    import hashlib
    import shutil
    from cem.evidence import segmented_evidence
    from cem.glider.geometry import load_part_mesh, measure_part
    from gliders.model.export import write_stl
    from gliders.model.assembly import Glider
    req, gl, elite, directory, original = delivered
    report = copy.deepcopy(original)
    for item in report['bundle']['parts']:
        shutil.copy2(directory/item['file'], tmp_path/item['file'])
    item = next(p for p in report['bundle']['parts'] if p['name'] == 'joint_1')
    path = tmp_path/item['file']; mesh = load_part_mesh(path)
    mesh.apply_translation([.001, 0., 0.]); write_stl(str(path), mesh.vertices, mesh.faces)
    item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    replacement = dict(name=item['name'], **measure_part(path, item['material']))
    report['closure']['parts'] = [replacement if p['name'] == item['name'] else p for p in report['closure']['parts']]
    report['closure']['measured_mass_kg'] = sum(p['mass_kg'] for p in report['closure']['parts'])
    report['closure']['measured_mass_kg'] += sum(p.mass for p in Glider.mass_items(gl) if p.name in ('hardware', 'payload', 'ballast'))
    with pytest.raises(ValueError, match='construction revision'):
        segmented_evidence(report, dict(kind='design', requirements=req.model_dump(mode='json'), calibration=initial(), elite=elite), tmp_path)
