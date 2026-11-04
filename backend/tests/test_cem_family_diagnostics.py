"""Actual installed VLM behavior: diagnostic evidence, not native qualification."""
import copy

import numpy as np
import pytest

from cem.glider.families import archive, build
from cem.glider.families.admission import Evidence
from cem.glider.families.diagnostics import Conditions, diagnose, identity, _run


@pytest.mark.parametrize('family,equipment_x', [
    ('tailless_swept', .22), ('canard', .3), ('tandem', .1)])
def test_native_reference_equilibrium_and_mesh_evidence(family, equipment_x):
    a = build(family)
    p = copy.deepcopy(a.parameters)
    p['inventory']['equipment_x_m'] = equipment_x
    a = build(family, p)
    before = a.revision
    report = diagnose(archive(a))
    assert a.revision == before
    assert report['aircraft_revision'] == before
    assert report['state'] in ('diagnostic_equilibrium', 'mesh_sensitive')
    assert len(report['mesh_levels']) == 2
    assert report['mesh_changes']['relative_speed'] >= 0
    for level in report['mesh_levels']:
        assert level['state'] == 'inviscid_equilibrium'
        assert abs(level['raw']['Cm']) < 1e-5
        weight = a.mass_summary()['mass_kg']*9.80665
        assert level['raw']['L'] == pytest.approx(weight, rel=1e-4)
        assert level['raw']['Cma'] < 0
        assert len(level['moment_samples']) == 9
        assert np.isfinite(level['raw']['force_geometry_n']).all()
    assert report['verified_gain'] is False
    assert report['independent_numerical_performance'] is False
    with pytest.raises(ValueError):
        Evidence.model_validate(report)


@pytest.mark.parametrize('family,expected', [('canard', 'untrimmable'), ('tandem', 'nonpositive_lift')])
def test_native_default_rejection_remains_a_rejection(family, expected):
    report = diagnose(archive(build(family)))
    assert report['state'] == 'inconclusive'
    assert all(level['state'] == expected for level in report['mesh_levels'])


def test_aft_cg_has_real_native_static_instability():
    p = copy.deepcopy(build('canard').parameters)
    p['inventory']['equipment_x_m'] = .6
    a = build('canard', p)
    raw = _run(a, Conditions(), 3., 6., 3, derivatives=True)
    assert raw['CLa'] > 0 and raw['Cma'] > 0
    report = diagnose(archive(a))
    assert report['state'] == 'inconclusive'


def test_all_lifting_surfaces_set_reference_and_archives_do_not_alias():
    a = build('canard')
    ap = a.airplane()
    assert ap.s_ref == pytest.approx(sum(w.area() for w in ap.wings))
    assert ap.b_ref == pytest.approx(max(w.span() for w in ap.wings))
    assert ap.s_ref > ap.wings[0].area()*3
    original = a.revision
    record = archive(a)
    record['parameters']['inventory']['equipment_x_m'] = .5
    assert a.revision == original


def test_diagnostic_identity_binds_operating_conditions_geometry_and_mesh():
    record = archive(build('tailless_swept'))
    first = identity(record, Conditions())
    assert first != identity(record, Conditions(altitude_m=1000.))
    assert first != identity(record, Conditions(fine_resolution=9))
    changed = copy.deepcopy(record)
    changed['parameters']['reflex_fraction'] = 0.
    with pytest.raises(ValueError, match='revision'):
        diagnose(changed)
    with pytest.raises(ValueError, match='resolution'):
        Conditions(coarse_resolution=6, fine_resolution=3)
