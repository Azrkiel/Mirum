"""Actual material volume, installed channel and adversarial delivery checks."""
import hashlib

import numpy as np
import pytest
from pydantic import ValidationError

from cem.glider.requirements import GliderMissionRequirements as Mission
from cem.glider.foam_core import build_core, verify_core, FOAM, channel_radius, spar_position
from cem.glider.geometry import surface_meshes, load_part_mesh, measure_part, export_bundle
from cem.glider.calibration import initial
from cem.glider.model import configuration


def example():
    from backend.tests.test_cem_pipeline import example as base
    _, _, gl = base()
    req = Mission(launch={'method': 'drop'}, process='sheet_foam', construction='foam_core_wing_v1',
                  constraints={'payload': {'mass_kg': .06, 'diameter_m': .045, 'length_m': .06}},
                  discovery={'budget_evals': 8})
    request, cfg = configuration(req, initial(), 'sheet_foam')
    gl.cfg = cfg
    gl.wing.style, gl.wing.dihedral_deg, gl.wing.washout_deg = 'tapered', 0., 0.
    gl.wing.sweep_qc_deg, gl.wing.taper, gl.wing.naca = .7, .9, '2410'
    return req, request, gl


def test_construction_is_explicit_and_restricted_to_its_process():
    assert Mission(launch={'method': 'drop'}).construction == 'screening_oml'
    with pytest.raises(ValidationError):
        Mission(launch={'method': 'drop'}, process='balsa', construction='foam_core_wing_v1')
    with pytest.raises(ValidationError):
        Mission(launch={'method': 'drop'}, process='auto', construction='foam_core_wing_v1')


def test_closed_core_volume_removes_real_channel_and_uses_solid_density(tmp_path):
    req, _, gl = example()
    bundle = export_bundle(gl, tmp_path, '0123456789ab')
    diameter = gl.spar()['diameter_m']
    for side, outer in enumerate(surface_meshes(gl.wing_surface())):
        item = next(p for p in bundle['parts'] if p['name'] == f'wing_{side}')
        assert item['material']['law'] == 'solid'
        core = load_part_mesh(tmp_path/item['file'])
        assert core.body_count == 1 and core.is_watertight and core.volume > 0
        removed = 64/2 * channel_radius(diameter)**2*np.sin(2*np.pi/64)*gl.wing.span/2
        # Cap-alignment vertices subdivide the regular channel polygon toward
        # its circumcircle; removed volume lies between those analytic bounds.
        circular = np.pi*channel_radius(diameter)**2*gl.wing.span/2
        assert outer.volume-circular-2e-9 <= core.volume <= outer.volume-removed+2e-9
        measured = measure_part(tmp_path/item['file'], item['material'])
        assert measured['mass_kg'] == pytest.approx(core.volume*FOAM['density_kg_m3'])
        assert np.linalg.eigvalsh(measured['inertia_kg_m2']).min() > 0
        evidence, recovered = verify_core(core, outer, spar_position(gl), diameter)
        assert evidence['state'] == 'passed'
        assert evidence['clearance_m'] >= FOAM['radial_clearance_m']-2e-7
        assert recovered.volume == pytest.approx(outer.volume, rel=2e-6)


@pytest.mark.parametrize('mutation', ['plug', 'undersize_channel', 'shift_channel', 'outer_shape', 'open'])
def test_material_or_channel_tampering_fails_after_rehash(tmp_path, mutation):
    _, _, gl = example()
    outer = surface_meshes(gl.wing_surface())[0]
    position, diameter = spar_position(gl), gl.spar()['diameter_m']
    core = build_core(outer, position, diameter)
    if mutation == 'plug':
        core = outer.copy()
    elif mutation == 'undersize_channel':
        core = build_core(outer, position, diameter*.5)
    elif mutation == 'shift_channel':
        core = build_core(outer, np.array(position)+[.001, 0, 0], diameter)
    elif mutation == 'outer_shape':
        core.vertices[:, 0] *= 1.05
    else:
        core.update_faces(np.arange(len(core.faces)-1))
    from gliders.model.export import write_stl
    path = tmp_path/'tampered.stl'
    write_stl(str(path), core.vertices, core.faces)
    assert hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        verify_core(load_part_mesh(path), outer, position, diameter)


def test_construction_cannot_hide_incompatible_installed_path():
    _, _, gl = example()
    gl.wing.dihedral_deg = 6.
    with pytest.raises(ValueError, match='channel|enclos|web'):
        build_core(surface_meshes(gl.wing_surface())[0], spar_position(gl), gl.spar()['diameter_m'])


def test_core_model_mass_matches_measured_delivery_and_not_areal_estimate(tmp_path):
    req, request, gl = example()
    bundle = export_bundle(gl, tmp_path, '0123456789ab')
    physical = sum(p.mass for p in gl.mass_items() if p.name.startswith('wing'))
    measured = sum(measure_part(tmp_path/p['file'], p['material'])['mass_kg']
                   for p in bundle['parts'] if p['name'].startswith('wing'))
    assert physical == pytest.approx(measured, rel=2e-6)
    assert physical != pytest.approx(gl.wing_surface().true_area()*gl.cfg['materials']['wing_areal_density_kg_m2'])


@pytest.mark.parametrize('mutation', [None, 'mass', 'clearance', 'shape_claim', 'load_path', 'rod_axis'])
def test_parent_remeasures_physical_parts_and_channel(tmp_path, mutation):
    from dataclasses import asdict
    from gliders.design_spec import placement
    from cem.glider.foam_core import verify_delivery
    from cem.evidence import foam_evidence
    req, _, gl = example()
    bundle = export_bundle(gl, tmp_path, '0123456789ab')
    by_name = {p['name']: p for p in bundle['parts']}
    evidence, _ = verify_delivery(gl,
        [load_part_mesh(tmp_path/by_name[f'wing_{s}']['file']) for s in (0, 1)],
        load_part_mesh(tmp_path/by_name['spar']['file']))
    parts = [dict(name=p['name'], **measure_part(tmp_path/p['file'], p['material'])) for p in bundle['parts']]
    report = dict(output_id='0123456789ab', bundle=bundle, closure=dict(parts=parts, foam_core=evidence))
    payload = dict(requirements=req.model_dump(mode='json'), calibration=initial(), elite=dict(
        process='sheet_foam', parameters=dict(wing=asdict(gl.wing), fuselage=asdict(gl.fuse), tail=asdict(gl.tail)),
        placement=placement(gl)))
    if mutation == 'mass':
        next(p for p in parts if p['name'] == 'wing_0')['mass_kg'] *= .8
    elif mutation == 'clearance':
        evidence['wings'][0]['clearance_m'] *= 2
    elif mutation == 'shape_claim':
        evidence['wings'][0]['outer_shape_state'] = 'independently_flight_qualified'
    elif mutation == 'load_path':
        evidence['load_path_state'] = 'passed'
    elif mutation == 'rod_axis':
        path = tmp_path/by_name['spar']['file']
        rod = load_part_mesh(path); rod.apply_translation([.001, 0, 0])
        from gliders.model.export import write_stl
        write_stl(str(path), rod.vertices, rod.faces)
        by_name['spar']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    if mutation is None:
        foam_evidence(report, payload, tmp_path)
    else:
        with pytest.raises(ValueError):
            foam_evidence(report, payload, tmp_path)


def test_incompatible_geometry_is_an_admission_failure_not_an_uncaught_job_error():
    from cem.glider.model import evaluate_candidate
    req, request, gl = example()
    gl.wing.dihedral_deg = 8.
    result = evaluate_candidate(gl, request, req, initial(), 'sheet_foam')
    assert result['valid'] is False
    assert result['manufacturability']['manufacturing_ready'] is False
    assert 'channel' in result['fail_reasons']


def test_physical_mass_error_is_rejected_and_physical_scope_stays_unqualified(tmp_path):
    from cem.glider.closure import verify
    req, request, gl = example()
    mass = gl.mass_summary()['mass']
    bundle = export_bundle(gl, tmp_path, '0123456789ab')
    report = verify(gl, dict(mass_kg=mass*1.2), bundle, tmp_path, request, req, initial(), 'sheet_foam')
    assert report['state'] == 'failed'
    assert any('mass' in f for f in report['failures'])
    assert report['foam_core']['state'] == 'passed'
    assert report['foam_core']['load_path_state'] == 'unverified'
    assert report['manufacturing_ready'] is False


def test_core_construction_does_not_reuse_areal_mass_feasibility_bound():
    from cem.glider.feasibility import feasibility
    req, _, _ = example()
    result = feasibility(req, initial())
    assert result['optimistic_bound'] is None
    assert result['continue_search'] is True
    assert result['bound_state'] == 'rebuild_required_for_construction'


def test_physical_core_stl_is_deterministic_in_the_pinned_runtime(tmp_path):
    _, _, gl = example()
    for name in ('a', 'b'):
        export_bundle(gl, tmp_path/name, '0123456789ab')
    for path in (tmp_path/'a').glob('*.stl'):
        assert path.read_bytes() == (tmp_path/'b'/path.name).read_bytes()


def test_actual_material_envelope_cannot_exceed_stock_thickness():
    from cem.glider.foam_core import core_meshes
    _, _, gl = example()
    gl.wing.root_chord = .22
    with pytest.raises(ValueError, match='stock thickness'):
        core_meshes(gl)
