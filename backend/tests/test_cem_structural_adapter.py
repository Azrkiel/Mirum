"""Consume A's real shared schemas without smoothing loads or promoting priors."""
import copy
import json

import numpy as np
import pytest

from cem.aero.fixtures import structural_fixture
from cem.aero.contracts import InstalledStructure
from cem.core import digest
from cem.glider import structural_adapter as adapter
from cem.glider import mounted_wing
from cem.glider.physical_fixture import export_fixture
from backend.tests.test_cem_physical import wing


def consume(g, case, binding=None):
    return adapter.consume_loads(g, case, expected_binding=g.binding if binding is None else binding,
        root_axis_m=(.05, 0., 0.), half_span_m=.5)


def test_shared_analytical_load_fixture_has_exact_beam_and_reaction_reference():
    g, case, _ = structural_fixture()
    r = consume(g, case)
    for side, sign in [('left', -1), ('right', 1)]:
        assert r['sides'][side]['force_n'] == pytest.approx([0., 0., .5])
        assert r['sides'][side]['moment_root_nm'] == pytest.approx([sign*.125, 0., 0.])
        assert r['sides'][side]['reaction_moment_nm'] == pytest.approx([-sign*.125, 0., 0.])
        assert adapter.section_resultant(r, side, .5) == dict(force_n=[0., 0., 0.], moment_nm=[0., 0., 0.])
    cut = adapter.section_resultant(r, 'right', .25)
    assert cut['force_n'] == pytest.approx([0., 0., .25])
    assert cut['moment_nm'] == pytest.approx([.03125, 0., 0.])
    assert r['state'] == 'incomplete' and r['qualification_state'] == 'shared_load_contract_only'


def test_load_factor_applied_once_and_all_force_and_couple_components_retained():
    g, case, _ = structural_fixture()
    changed = []
    for s in case.loads.strips:
        f = np.array([.01, .002, .125]); couple = np.array([.003, .004, .005])
        m = np.cross(np.array(s.center_m)-g.reference.cg_m, f)+couple
        changed.append(s.model_copy(update={'force_n': tuple(f), 'moment_cg_nm': tuple(m)}))
    loads = case.loads.model_copy(update={'strips': tuple(changed),
        'force_n': tuple(np.sum([s.force_n for s in changed], axis=0)),
        'moment_cg_nm': tuple(np.sum([s.moment_cg_nm for s in changed], axis=0))})
    case = case.model_copy(update={'loads': loads, 'aerodynamic_loads_sha256': digest(loads), 'load_factor': 3.})
    r = consume(g, case)
    assert r['sides']['right']['force_n'] == pytest.approx([.12, .024, 1.5])
    for row in r['sides']['right']['strips']:
        assert row['couple_nm'] == pytest.approx([.009, .012, .015])
    assert r['load_factor_applied'] == 3.
    assert r['sides']['right']['moment_root_nm'][1] == pytest.approx(.004*4*3)


def test_moving_cg_and_reexpressing_recorded_moments_does_not_change_spar_loads():
    g, case, _ = structural_fixture()
    before = consume(g, case)
    old_cg = np.array(g.reference.cg_m)
    ref = g.reference.model_copy(update={'cg_m': (.08, 0., .02)})
    g = g.model_copy(update={'reference': ref})
    binding = g.binding.model_copy(update={'geometry_sha256': digest(g.geometry_payload())})
    g = g.model_copy(update={'binding': binding})
    rows = tuple(s.model_copy(update={'moment_cg_nm': tuple(np.array(s.moment_cg_nm)
        +np.cross(old_cg-np.array(ref.cg_m), np.array(s.force_n)))}) for s in case.loads.strips)
    loads = case.loads.model_copy(update={'binding': binding, 'strips': rows,
        'moment_cg_nm': tuple(np.sum([s.moment_cg_nm for s in rows], axis=0))})
    case = case.model_copy(update={'binding': binding, 'loads': loads, 'aerodynamic_loads_sha256': digest(loads)})
    after = consume(g, case)
    for side in ('left', 'right'):
        assert after['sides'][side]['moment_root_nm'] == pytest.approx(before['sides'][side]['moment_root_nm'])


@pytest.mark.parametrize('field', ['geometry_sha256', 'hardware_sha256', 'mission_sha256',
                                 'process_sha256', 'calibration_sha256', 'implementation_sha256'])
def test_every_shared_context_binding_is_required(field):
    g, case, _ = structural_fixture()
    expected = g.binding.model_copy(update={field: 'f'*64})
    with pytest.raises(ValueError, match='binding'): consume(g, case, expected)


def test_shared_strip_mirroring_and_totals_are_not_invented():
    g, case, _ = structural_fixture()
    rows = tuple(s for s in case.loads.strips if s.center_m[1] > 0)
    loads = case.loads.model_copy(update={'strips': rows, 'force_n': (0., 0., .5), 'moment_cg_nm': (.125, 0., 0.)})
    case = case.model_copy(update={'loads': loads, 'aerodynamic_loads_sha256': digest(loads)})
    with pytest.raises(ValueError, match='Both physical'): consume(g, case)


@pytest.fixture(scope='module')
def handoff(tmp_path_factory):
    g, _, _ = structural_fixture()
    w = wing(); a = mounted_wing.construct(w)
    directory = tmp_path_factory.mktemp('shared-structure')
    manifest = export_fixture(a, directory)
    result = adapter.export_structure(w, .004, manifest, directory, binding=g.binding)
    return w, directory, manifest, g.binding, result


def test_actual_material_and_support_inventory_roundtrips_shared_contract(handoff):
    w, directory, manifest, binding, r = handoff
    structure = InstalledStructure.model_validate_json(json.dumps(r['installed_structure']))
    assert len(structure.materials) == 20
    assert len(structure.supports) == 22
    assert structure.closure_state == 'declared'
    assert all(s.evidence_state == 'declared' for s in structure.supports)
    assert r['aerodynamic_geometry_correspondence'] == 'unverified'
    assert r['delivered_geometry_sha256'] != binding.geometry_sha256
    assert len(r['full_material_inertia_kg_m2']) == 20
    assert not adapter.validate_structure(r, w, .004, manifest, directory, binding=binding)
    assert r == adapter.export_structure(w, .004, manifest, directory, binding=binding)


@pytest.mark.parametrize('mutation', ['density', 'mass', 'support', 'closure', 'binding', 'tensor'])
def test_parent_reconstructs_shared_material_and_support_evidence(handoff, mutation):
    w, directory, manifest, binding, original = handoff
    report = copy.deepcopy(original)
    s = report['installed_structure']
    if mutation == 'density': s['materials'][0]['density_kg_m3'] *= 2
    if mutation == 'mass': s['materials'][0]['mass_kg'] *= .8
    if mutation == 'support': s['supports'].pop()
    if mutation == 'closure': s['closure_state'] = 'measured'
    if mutation == 'binding': s['binding']['process_sha256'] = 'f'*64
    if mutation == 'tensor': report['full_material_inertia_kg_m2']['spar'][0][0] *= 2
    with pytest.raises(ValueError, match='revision/context'):
        adapter.validate_structure(report, w, .004, manifest, directory, binding=binding)


def test_changed_raw_support_artifact_is_rejected_and_not_overwritten(handoff, tmp_path):
    import shutil
    w, directory, manifest, binding, report = handoff
    for path in directory.iterdir(): shutil.copy2(path, tmp_path/path.name)
    path = tmp_path/'installed-support-evidence.json'
    path.write_text('{}')
    with pytest.raises(ValueError, match='support evidence'):
        adapter.validate_structure(report, w, .004, manifest, tmp_path, binding=binding)
    with pytest.raises(ValueError, match='Preserve'):
        adapter.export_structure(w, .004, manifest, tmp_path, binding=binding)
    assert path.read_text() == '{}'
