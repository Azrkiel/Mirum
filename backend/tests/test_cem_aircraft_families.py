"""Task C geometry and injected solver gates, not native solver qualification."""
import copy
import json

import numpy as np
import pytest

from cem.core import canonical_json
from cem.glider.families import (BUILDERS, archive, build, capabilities,
                                export_screening, reconstruct)
from cem.glider.families.admission import Evidence, assess
from cem.glider.families.development import search, compare, validate_record


def solver_fixture(aircraft):
    weight = aircraft.mass_summary()['mass_kg']*9.80665
    shares = {'tailless_swept': {'main_wing': 1.},
              'canard': {'foreplane': .2, 'main_wing': .8},
              'tandem': {'front_wing': .5, 'rear_wing': .5}}[aircraft.family]
    surfaces = {}
    for i, (name, share) in enumerate(shares.items()):
        surfaces[name] = dict(lift_n=weight*share, root_stall_alpha_deg=10.+i*4,
            tip_stall_alpha_deg=13.+i*4, effective_alpha_deg=3., reynolds=80000.)
    return dict(aircraft_revision=aircraft.revision, source='development_fixture',
        method_revision='deterministic-fixture-v1', speed_m_s=6., alpha_deg=3.,
        cm=0., cl_alpha_per_rad=4., cm_alpha_per_rad=-.32,
        mode_real_parts_per_s=dict(short_period=-1., phugoid=-.01, dutch_roll=-.3, roll=-2., spiral=-.02),
        control_deflections_deg={c.name: 0. for c in aircraft.controls}, surfaces=surfaces,
        interactions={a+'->'+b: dict(induced_angle_deg=-1., method='fixture-interaction')
                      for a in shares for b in shares if a != b})


@pytest.mark.parametrize('family', BUILDERS)
def test_geometry_mass_controls_and_exact_reconstruction(family):
    a = build(family)
    assert reconstruct(json.loads(canonical_json(archive(a)))).revision == a.revision
    assert a.mass_summary()['mass_kg'] > 0
    assert a.mass_summary()['cg_m'][1] == pytest.approx(0., abs=1e-14)
    assert not any(s.role == 'htail' for s in a.surfaces)
    assert {c.surface for c in a.controls} <= {s.name for s in a.surfaces}
    assert all(p.mass_kg > 0 for p in a.inventory)
    assert all(s.true_area() > 0 for s in a.surfaces)
    result = assess(a, solver_fixture(a))
    assert result['state'] == 'fixture_passed', result
    assert result['independent_numerical_performance'] is False
    assert result['physical_construction'] is False


def test_three_real_distinct_geometries_and_reflex():
    swept, canard, tandem = [build(f) for f in BUILDERS]
    assert len(swept.surfaces) == 1
    assert swept.surfaces[0].sections[-1].x > swept.surfaces[0].sections[0].x
    assert [s.role for s in canard.surfaces] == ['foreplane', 'main_wing']
    assert [s.role for s in tandem.surfaces] == ['front_wing', 'rear_wing']
    p = copy.deepcopy(swept.parameters)
    p['reflex_fraction'] = 0.
    plain = build('tailless_swept', p)
    assert plain.revision != swept.revision
    assert not np.allclose(plain.airplane().wings[0].xsecs[0].airfoil.coordinates,
                           swept.airplane().wings[0].xsecs[0].airfoil.coordinates)
    assert len({a.revision for a in (swept, canard, tandem)}) == 3


@pytest.mark.parametrize('family', BUILDERS)
@pytest.mark.parametrize('corruption', ['family', 'builder_revision', 'aircraft_revision', 'parameters'])
def test_archive_rejects_stale_relabelled_and_mutated_geometry(family, corruption):
    record = archive(build(family))
    if corruption == 'family':
        record['family'] = 'canard' if family != 'canard' else 'tandem'
    elif corruption == 'parameters':
        record['parameters']['inventory']['equipment_x_m'] += .01
    else:
        record[corruption] = '0'*64
    with pytest.raises(ValueError):
        reconstruct(record)


@pytest.mark.parametrize('family', BUILDERS)
@pytest.mark.parametrize('failure', ['trim', 'static', 'dynamic', 'missing_mode', 'control', 'missing_control',
                                    'missing_surface', 'weight', 'stall', 'nan'])
def test_rejection_gates(family, failure):
    a = build(family)
    e = solver_fixture(a)
    if failure == 'trim':
        e['cm'] = .1
    elif failure == 'static':
        e['cm_alpha_per_rad'] = .1
    elif failure == 'dynamic':
        e['mode_real_parts_per_s']['spiral'] = .001
    elif failure == 'missing_mode':
        del e['mode_real_parts_per_s']['phugoid']
    elif failure == 'control':
        e['control_deflections_deg'][a.controls[0].name] = 21.
    elif failure == 'missing_control':
        e['control_deflections_deg'] = {}
    elif failure == 'missing_surface':
        e['surfaces'] = {}
    elif failure == 'weight':
        next(iter(e['surfaces'].values()))['lift_n'] *= 2
    elif failure == 'stall':
        next(iter(e['surfaces'].values()))['effective_alpha_deg'] = 10.
    elif failure == 'nan':
        e['mode_real_parts_per_s']['roll'] = float('nan')
    if failure == 'nan':
        with pytest.raises(ValueError):
            assess(a, e)
    else:
        assert assess(a, e)['state'] == 'rejected'


@pytest.mark.parametrize('family', ['canard', 'tandem'])
def test_interaction_stall_sequence_and_lift_sharing_are_required(family):
    a = build(family)
    e = solver_fixture(a)
    e['interactions'].pop(next(iter(e['interactions'])))
    assert 'missing bidirectional interference evidence' in assess(a, e)['failures']
    e = solver_fixture(a)
    front, rear = list(e['surfaces'])
    e['surfaces'][front]['root_stall_alpha_deg'] = 20.
    e['surfaces'][front]['tip_stall_alpha_deg'] = 21.
    assert 'unsupported front-first stall sequence' in assess(a, e)['failures']
    e = solver_fixture(a)
    total = sum(s['lift_n'] for s in e['surfaces'].values())
    e['surfaces'][front]['lift_n'], e['surfaces'][rear]['lift_n'] = .01*total, .99*total
    assert 'lift sharing outside family envelope' in assess(a, e)['failures']


def test_tailless_requires_root_first_stall_and_does_not_use_tail_volume():
    a = build('tailless_swept')
    e = solver_fixture(a)
    e['surfaces']['main_wing']['tip_stall_alpha_deg'] = 9.
    assert 'elevon tip region stalls before required root-first margin' in assess(a, e)['failures']
    assert 'tail_volume' not in canonical_json(a.parameters)


@pytest.mark.parametrize('family', BUILDERS)
def test_stale_solver_or_native_claim_cannot_be_admitted(family):
    a = build(family)
    e = solver_fixture(a)
    e['aircraft_revision'] = 'f'*64
    with pytest.raises(ValueError, match='revision'):
        assess(a, e)
    e = solver_fixture(a)
    e['source'] = 'native_qualified'
    with pytest.raises(ValueError):
        Evidence.model_validate(e)


@pytest.mark.parametrize('family', BUILDERS)
def test_native_geometry_export_search_comparison_roundtrip(family, tmp_path):
    from cem.evidence import file_digest
    import trimesh
    result = search(family, solver_fixture, budget=4, seed=1)
    assert canonical_json(result) == canonical_json(search(family, solver_fixture, budget=4, seed=1))
    assert len(result['evaluations']) == len(result['accepted']) == 4
    assert validate_record(result['accepted'][0]).revision == result['accepted'][0]['geometry']['aircraft_revision']
    altered = copy.deepcopy(result['accepted'][0])
    altered['evidence']['cm'] = .1
    with pytest.raises(ValueError, match='assessment'):
        validate_record(altered)
    left, right = [r['geometry'] for r in result['accepted'][:2]]
    assert left['aircraft_revision'] != right['aircraft_revision']
    comparison = compare(left, right, solver_fixture)
    assert comparison['state'] == 'fixture_passed'
    assert comparison['verified_gain'] is False and comparison['performance_delta'] is None
    manifest = export_screening(right, tmp_path)
    assert manifest['scope'] == 'lifting_surface_oml_only'
    assert manifest['manufacturing_ready'] is False
    for part in manifest['parts']:
        path = tmp_path/part['file']
        assert part['sha256'] == file_digest(path)
        mesh = trimesh.load(str(path), force='mesh')
        assert len(mesh.faces) > 100 and np.isfinite(mesh.vertices).all()
        assert mesh.extents[1] > 100  # millimetres, not metres
    assert json.loads((tmp_path/'screening.json').read_text()) == manifest
    with pytest.raises(ValueError, match='empty directory'):
        export_screening(right, tmp_path)


@pytest.mark.parametrize('family', BUILDERS)
def test_missing_qualification_keeps_production_unavailable(family):
    from cem.glider.model import configuration, SUPPORTED_FAMILIES
    from cem.glider.calibration import initial
    from cem.glider.requirements import GliderMissionRequirements
    assert family not in SUPPORTED_FAMILIES
    caps = capabilities()[family]
    assert caps['geometry'] == 'screening_prototype'
    assert not caps['production_search'] and not caps['physical_construction']
    with pytest.raises(ValueError, match='qualified'):
        configuration(GliderMissionRequirements(launch={'method': 'drop'},
            discovery={'families': [family]}), initial(), 'balsa')


@pytest.mark.parametrize('family,change', [
    ('tailless_swept', {'wing': {'sweep_deg': 0.}}),
    ('canard', {'main_x_m': .25, 'foreplane_x_m': .15, 'foreplane': {'root_chord_m': .2}}),
    ('tandem', {'front_wing': {'span_m': .2, 'root_chord_m': .04}}),
    ('tailless_swept', {'reflex_fraction': float('inf')}),
])
def test_invalid_family_geometry_rejected(family, change):
    p = BUILDERS[family].Parameters().model_dump(mode='json')
    for key, value in change.items():
        if isinstance(value, dict):
            p[key].update(value)
        else:
            p[key] = value
    with pytest.raises(ValueError):
        build(family, p)


def test_api_metadata_exposes_separate_prototype_capabilities():
    from fastapi.testclient import TestClient
    from cem.app import app
    with TestClient(app) as client:
        response = client.get('/cem/glider/metadata')
        assert response.status_code == 200
        data = response.json()
        assert data['family_prototypes'] == capabilities()
        assert set(data['supported_families']) == {'conventional', 't_tail', 'v_tail'}
        for family in BUILDERS:
            response = client.post('/cem/glider/discover', json={
                'requirements': {'launch': {'method': 'drop'}, 'discovery': {'families': [family]}}})
            assert response.status_code == 422
