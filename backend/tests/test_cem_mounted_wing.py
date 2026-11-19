"""Combined shaped stock/adhesive/collar/attachment delivery, kept fail-closed."""
import copy
import hashlib
import json
import shutil

import numpy as np
import pytest

from cem.glider import mounted_wing as mounted
from cem.glider.physical_fixture import export_fixture
from cem.glider.physical_loads import InstalledLoadCase, StationLoad
from backend.tests.test_cem_physical import wing


@pytest.fixture(scope='module')
def delivered(tmp_path_factory):
    w = wing(); a = mounted.construct(w)
    directory = tmp_path_factory.mktemp('mounted-wing')
    manifest = export_fixture(a, directory)
    # Roundtrip manifest to model the serialized worker boundary.
    return w, a, directory, json.loads(json.dumps(manifest))


def loads(revision, q=2., torque=.01):
    return InstalledLoadCase(geometry_sha256=revision, operating_sha256='a'*64,
        stations=(StationLoad(y_m=0., fz_n_m=q, my_nm_m=torque),
                  StationLoad(y_m=.5, fz_n_m=q, my_nm_m=torque)))


def test_real_mounted_geometry_properties_and_reactions_are_reconstructed(delivered):
    w, a, directory, manifest = delivered
    c = loads(a['geometry_sha256'])
    r = mounted.verify(w, .004, manifest, directory, left=c, right=c, operating_sha256='a'*64)
    assert r['state'] == 'incomplete'
    assert r['attachment_mechanics']['supported_checks_state'] == 'passed'
    assert len(r['measured_parts']) == 20
    assert r['material_partition_relative_error'] < 1e-5
    props = r['material_properties']
    expected_mass = sum(p['mesh'].volume*p['material']['density_kg_m3'] for p in a['parts'].values())
    assert props['mass_kg'] == pytest.approx(expected_mass, rel=1e-5)
    assert abs(props['cg_m'][1]) < 1e-8
    assert np.linalg.eigvalsh(props['inertia_kg_m2']).min() > 0
    assert r['root_cutback_m'] == pytest.approx(.02)
    assert r['engineering_certified'] is False and r['manufacturing_ready'] is False


def test_geometry_cutback_is_a_new_revision_and_no_interpenetrating_root_foam(delivered):
    from cem.glider.physical_fixture import construct
    w, a, directory, manifest = delivered
    assert a['geometry_sha256'] != construct(w)['geometry_sha256']
    for side in (-1, 1):
        core = a['parts'][f'foam_{side}_0']['mesh']
        assert np.min(core.vertices[:, 1]*side) == pytest.approx(.02)
        plate = a['parts'][f'mount_plate_{side}']['mesh']
        assert np.max(plate.vertices[:, 1]*side) == pytest.approx(.02)


@pytest.mark.parametrize('part', ['wing_collar_bond_1', 'mount_plate_-1', 'mount_rivet_0', 'fuselage_keel'])
def test_missing_installed_load_path_part_fails(delivered, part):
    w, _, directory, original = delivered
    manifest = copy.deepcopy(original)
    manifest['parts'] = [p for p in manifest['parts'] if p['name'] != part]
    with pytest.raises(ValueError, match='inventory'):
        mounted.verify(w, .004, manifest, directory)


def test_stale_original_wing_load_revision_cannot_analyze_installed_revision(delivered):
    from cem.glider.physical_fixture import construct
    w, _, directory, manifest = delivered
    c = loads(construct(w)['geometry_sha256'])
    with pytest.raises(ValueError, match='Stale'):
        mounted.verify(w, .004, manifest, directory, left=c, right=c, operating_sha256='a'*64)


def test_new_geometry_exports_and_measures_repeatably(delivered, tmp_path):
    w, a, directory, manifest = delivered
    again = export_fixture(mounted.construct(w), tmp_path)
    assert again == manifest
    for item in again['parts']:
        assert (tmp_path/item['file']).read_bytes() == (directory/item['file']).read_bytes()
    assert mounted.verify(w, .004, again, tmp_path)['state'] == 'incomplete'


def test_rehashed_and_moved_fuselage_attachment_is_rejected(delivered, tmp_path):
    from cem.glider.geometry import load_part_mesh
    from gliders.model.export import write_stl
    w, _, directory, original = delivered
    manifest = copy.deepcopy(original)
    for p in directory.iterdir(): shutil.copy2(p, tmp_path/p.name)
    item = next(p for p in manifest['parts'] if p['name'] == 'fuselage_keel')
    path = tmp_path/item['file']; mesh = load_part_mesh(path)
    mesh.apply_translation([0., 0., -.002]); write_stl(str(path), mesh.vertices, mesh.faces)
    item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='construction revision'):
        mounted.verify(w, .004, manifest, tmp_path)


def test_extreme_fixture_overload_cannot_obtain_incomplete_passing_screen(delivered):
    w, a, directory, manifest = delivered
    c = loads(a['geometry_sha256'], q=20000., torque=100.)
    r = mounted.verify(w, .004, manifest, directory, left=c, right=c, operating_sha256='a'*64)
    assert r['state'] == 'failed'
    assert r['attachment_mechanics']['supported_checks_state'] == 'failed'


def test_real_cli_sizing_reexports_every_changed_section_and_keeps_closure_incomplete(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from cem.glider.physical_loads import resultants
    root = Path(__file__).resolve().parents[2]
    out = tmp_path/'sizing'
    env = dict(os.environ, PYTHONPATH=str(root/'backend'), OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
               MPLCONFIGDIR=str(tmp_path/'mpl'), XDG_CACHE_HOME=str(tmp_path/'cache'))
    run = subprocess.run([sys.executable, '-m', 'cem.glider.mounted_wing', '--output', str(out),
                          '--non-wing-mass-kg', '0', '--load-factor', '1'],
                         cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert run.returncode == 3, run.stderr
    report = json.loads(run.stdout)
    assert report == json.loads((out/'result.json').read_text())
    assert report['state'] == 'incomplete' and report['manufacturing_ready'] is False
    selected = report['selected_fixture_section']
    assert selected is not None and selected['beam_states'] == ['passed', 'passed']
    assert all(a['state'] in ('failed', 'geometry_rejected') for a in report['attempts'][:-1])
    seen = set()
    for attempt in report['attempts']:
        if attempt['state'] == 'geometry_rejected': continue
        target = out/attempt['directory']
        manifest = json.loads((target/'manifest.json').read_text())
        measurement = json.loads((target/'measurement.json').read_text())
        data = json.loads((target/'loads.json').read_text())
        left = InstalledLoadCase.model_validate_json(json.dumps(data['left']))
        assert attempt['geometry_sha256'] == manifest['geometry_sha256'] == left.geometry_sha256
        assert left.geometry_sha256 not in seen; seen.add(left.geometry_sha256)
        assert len(manifest['parts']) == 20
        assert 2*resultants(left, 0.)['shear_n'] == pytest.approx(attempt['loaded_mass_kg']*9.81)
        assert measurement['state'] == attempt['state']
        assert measurement['foam_transfer']['nominal_checks_state'] == attempt['foam_nominal_state']


def test_sizing_rejects_invalid_inputs_and_nonempty_output(tmp_path):
    for value in (float('nan'), -1., 20.):
        with pytest.raises(ValueError): mounted.size_fixture(wing(), tmp_path/'unused', load_factor=value)
    assert not (tmp_path/'unused').exists()
    (tmp_path/'existing.txt').write_text('preserve')
    with pytest.raises(ValueError, match='empty'): mounted.size_fixture(wing(), tmp_path)
    assert (tmp_path/'existing.txt').read_text() == 'preserve'
