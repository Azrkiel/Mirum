"""Real STL regressions for physical accounting and installed spar evidence."""
import copy

import numpy as np
import pytest
import trimesh

from cem.glider.geometry import measure_part


def write_mesh(path, mesh):
    from gliders.model.export import write_stl
    write_stl(str(path), mesh.vertices, mesh.faces)
    return path


@pytest.mark.parametrize('law', [
    {'law': 'unknown', 'areal_density_kg_m2': .2},
    {'law': 'solid', 'density_kg_m3': -1.},
    {'law': 'skin_equivalent', 'areal_density_kg_m2': float('nan')},
])
def test_unrecognized_or_nonphysical_material_law_fails(tmp_path, law):
    path = write_mesh(tmp_path / 'part.stl', trimesh.creation.box())
    with pytest.raises(ValueError):
        measure_part(path, law)


def test_disconnected_watertight_solids_are_not_one_part(tmp_path):
    a = trimesh.creation.box()
    b = a.copy(); b.apply_translation([2., 0., 0.])
    path = write_mesh(tmp_path / 'two.stl', trimesh.util.concatenate([a, b]))
    with pytest.raises(ValueError, match='connected'):
        measure_part(path, {'law': 'solid', 'density_kg_m3': 1600.})


def test_spar_enclosure_uses_delivered_geometry_not_bounds():
    from cem.glider.geometry import spar_enclosure
    wing = trimesh.creation.box(extents=[.2, .5, .04])
    wing.apply_translation([0., .25, 0.])
    left = wing.copy(); left.apply_scale([1., -1., 1.])
    transform = trimesh.geometry.align_vectors([0, 0, 1], [0, 1, 0])
    rod = trimesh.creation.cylinder(radius=.004, height=.9, sections=48, transform=transform)
    good = spar_enclosure(rod, [wing, left])
    assert good['state'] == 'passed'
    assert good['load_path_state'] == 'unverified'
    # A sloping wing has the same global envelope at the root but cannot house
    # the delivered horizontal rod. Even a refreshed artifact hash cannot help.
    for mesh in (wing, left):
        mesh.vertices[:, 2] += .2 * np.abs(mesh.vertices[:, 1])
    bad = spar_enclosure(rod, [wing, left])
    assert bad['state'] == 'failed'
    assert bad['max_outside_m'] > .05


def test_spar_enclosure_checks_volume_between_sides():
    from cem.glider.geometry import spar_enclosure
    right = trimesh.creation.box(extents=[.2, .4, .04])
    right.apply_translation([0, .3, 0])  # central gap from -.1 to +.1
    left = right.copy(); left.apply_scale([1, -1, 1])
    rod = trimesh.creation.box(extents=[.008, .8, .008])
    assert spar_enclosure(rod, [right, left])['state'] == 'failed'


@pytest.mark.parametrize('mutation', ['omit', 'duplicate', 'extra', 'material', 'renamed', 'unlisted'])
def test_closure_rejects_incomplete_or_unaccounted_parts_before_analysis(tmp_path, monkeypatch, mutation):
    from backend.tests.test_cem_pipeline import example
    from cem.glider.geometry import export_bundle
    from cem.glider.closure import verify
    from cem.glider.calibration import initial
    from cem.glider import closure
    req, request, gl = example()
    bundle = export_bundle(gl, tmp_path, '0123456789ab')
    if mutation == 'omit':
        item = bundle['parts'].pop(0)
        (tmp_path / item['file']).unlink()
    elif mutation == 'duplicate':
        bundle['parts'].append(copy.deepcopy(bundle['parts'][0]))
    elif mutation == 'extra':
        item = copy.deepcopy(bundle['parts'][0])
        item['name'] = 'unaccounted'
        item['file'] = '0123456789ab_unaccounted.stl'
        (tmp_path / item['file']).write_bytes((tmp_path / bundle['parts'][0]['file']).read_bytes())
        bundle['parts'].append(item)
    elif mutation == 'material':
        bundle['parts'][0]['material']['areal_density_kg_m2'] *= .8
    elif mutation == 'renamed':
        item = bundle['parts'][0]
        new_file = '0123456789ab_other.stl'
        (tmp_path / item['file']).rename(tmp_path / new_file)
        item['file'] = new_file
    else:
        (tmp_path / 'unlisted.stl').write_bytes((tmp_path / bundle['parts'][0]['file']).read_bytes())
    def forbidden(*args):
        raise AssertionError('Invalid accounting must fail before expensive aero analysis')
    monkeypatch.setattr(closure, 'evaluate_candidate', forbidden)
    result = verify(gl, {'mass_kg': .2}, bundle, tmp_path, request, req, initial(), 'sheet_foam')
    assert result['state'] == 'failed'
    assert 'accounting' in ' '.join(result['failures']).lower()


def accounting_report():
    from backend.tests.test_cem_pipeline import example
    from cem.glider.geometry import expected_materials
    from cem.glider.calibration import initial
    from dataclasses import asdict
    from gliders.design_spec import placement
    req, request, gl = example()
    expected = expected_materials(gl)
    names = sorted(expected)
    parts = [dict(name=n, file=f'0123456789ab_{n}.stl', material=expected[n]) for n in names]
    report = dict(output_id='0123456789ab', bundle={'parts': parts}, closure=dict(
        part_accounting=dict(state='passed', expected_parts=names, files=[p['file'] for p in parts]),
        parts=[dict(name=n, material_law=expected[n], connected_components=1) for n in names],
        spar_enclosure=dict(state='passed', method='delivered-wing-face-halfspaces-v1',
                            max_outside_m=0., tolerance_m=2e-7, load_path_state='unverified')))
    payload = dict(requirements=req.model_dump(mode='json'), calibration=initial(),
        elite=dict(process='sheet_foam', placement=placement(gl), parameters=dict(
            wing=asdict(gl.wing), fuselage=asdict(gl.fuse), tail=asdict(gl.tail))))
    return report, payload


@pytest.mark.parametrize('mutation', [None, 'missing', 'duplicate', 'material', 'disconnected',
                                      'outside', 'missing_enclosure', 'load_path', 'tolerance'])
def test_parent_requires_physical_accounting_and_enclosure(mutation):
    from cem.evidence import physical_accounting
    report, payload = accounting_report()
    if mutation == 'missing':
        report['closure'].pop('part_accounting')
    elif mutation == 'duplicate':
        report['closure']['parts'][0] = report['closure']['parts'][1]
    elif mutation == 'material':
        report['bundle']['parts'][0]['material'] = {'law': 'solid', 'density_kg_m3': 1.}
    elif mutation == 'disconnected':
        report['closure']['parts'][0]['connected_components'] = 2
    elif mutation == 'outside':
        report['closure']['spar_enclosure']['max_outside_m'] = .001
    elif mutation == 'missing_enclosure':
        report['closure'].pop('spar_enclosure')
    elif mutation == 'load_path':
        report['closure']['spar_enclosure']['load_path_state'] = 'passed'
    elif mutation == 'tolerance':
        report['closure']['spar_enclosure']['tolerance_m'] = 1.
    if mutation is None:
        physical_accounting(report, payload)
    else:
        with pytest.raises((ValueError, KeyError)):
            physical_accounting(report, payload)


@pytest.mark.parametrize('kind', ['open', 'truncated', 'symlink'])
def test_missing_closed_local_mesh_evidence_fails(tmp_path, kind):
    mesh = trimesh.creation.box()
    if kind == 'open':
        mesh.update_faces(np.arange(len(mesh.faces) - 1))
    path = write_mesh(tmp_path / 'part.stl', mesh)
    if kind == 'truncated':
        path.write_bytes(path.read_bytes()[:-1])
    elif kind == 'symlink':
        link = tmp_path / 'linked.stl'; link.symlink_to(path); path = link
    with pytest.raises(ValueError):
        measure_part(path, {'law': 'solid', 'density_kg_m3': 1600.})
