from copy import deepcopy

import pytest

from cem.glider.families import archive, build
from cem.glider.families import native_workflow as native
from cem.glider.families.diagnostics import Conditions


def record(family='tailless_swept'):
    return archive(build(family))


@pytest.mark.parametrize('family', ['tailless_swept', 'canard', 'tandem'])
def test_native_workflow_search_pair_and_replay_keep_production_gates_closed(family):
    r = record(family)
    # The search must execute native VLM, including rejection of bad defaults.
    conditions = Conditions(coarse_resolution=2, fine_resolution=3)
    result = native.search(r, budget=1, conditions=conditions)
    assert len(result['evaluations']) == 1
    assert result['evaluations'][0]['geometry'] == r
    assert native.replay(result) is False
    changed = deepcopy(result)
    changed['evaluations'][0]['report']['verified_gain'] = True
    with pytest.raises(ValueError, match='reconstructed'):
        native.replay(changed)
    pair = native.compare(r, r, conditions=conditions)
    assert pair['arms'][0] == pair['arms'][1]
    assert pair['performance_delta'] is None and pair['verified_gain'] is False
    assert pair['independent_numerical_performance'] is False


def test_native_search_freezes_inventory_and_rejects_invalid_budgets():
    r = record()
    for budget in (0, 25, True, 1.5):
        with pytest.raises(ValueError, match='budget'):
            native.search(r, budget=budget)
    for seed in (True, -1):
        with pytest.raises(ValueError, match='Seed'):
            native.search(r, seed=seed)


def test_sampling_is_deterministic_and_inventory_never_moves(monkeypatch):
    monkeypatch.setattr(native, 'diagnose', lambda *a, **kw: {'state': 'inconclusive'})
    r = record()
    a = native.search(r, budget=4, seed=8)
    assert a == native.search(r, budget=4, seed=8)
    assert a != native.search(r, budget=4, seed=9)
    assert all(e['geometry']['parameters']['inventory'] == r['parameters']['inventory'] for e in a['evaluations'])
    assert len({e['geometry']['aircraft_revision'] for e in a['evaluations']}) == 4


def test_comparison_rejects_changed_inventory_family_and_hardware():
    r = record()
    changed = deepcopy(r['parameters'])
    changed['inventory']['equipment_x_m'] += .01
    with pytest.raises(ValueError, match='fixed inventory'):
        native.compare(r, archive(build('tailless_swept', changed)))
    with pytest.raises(ValueError, match='same family'):
        native.compare(r, record('canard'))
    changed = deepcopy(r['parameters'])
    changed['elevon_hinge'] = .8
    with pytest.raises(ValueError, match='control hardware'):
        native.compare(r, archive(build('tailless_swept', changed)))


def test_native_export_reconstructs_evidence_and_meshes(tmp_path):
    folder = tmp_path/'bundle'
    manifest = native.export(record(), folder, conditions=Conditions(coarse_resolution=2, fine_resolution=3))
    assert manifest['controlled_parts']
    assert manifest['manufacturing_ready'] is False
    assert native.validate_export(folder) is False
    with pytest.raises(ValueError, match='empty'):
        native.export(record(), folder)
    mesh = folder/manifest['controlled_parts'][0]['file']
    mesh.write_bytes(mesh.read_bytes()+b'altered')
    with pytest.raises(ValueError, match='artifacts'):
        native.validate_export(folder)


@pytest.mark.parametrize('family,x', [('tailless_swept', .22), ('canard', .30), ('tandem', .10)])
def test_refined_controlled_native_positive_diagnostics(family, x):
    from cem.glider.families.diagnostics import diagnose
    a = build(family)
    p = deepcopy(a.parameters)
    p['inventory']['equipment_x_m'] = x  # declared before the archive/solve
    a = build(family, p)
    r = archive(a)
    result = diagnose(r, Conditions(coarse_resolution=6, fine_resolution=9),
        control_deflections_deg={c.name: 0. for c in a.controls})
    assert result['state'] == 'diagnostic_equilibrium'
    assert result['installed']['payload']['geometry'] == r
    assert result['verified_gain'] is False
    assert all(l['raw']['Cma'] < 0 for l in result['mesh_levels'])
