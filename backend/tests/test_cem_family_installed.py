from copy import deepcopy

import numpy as np
import pytest

from cem.core import digest
from cem.glider.families import archive, build
from cem.glider.families.installed import airplane, freeze, validate
from cem.glider.families.diagnostics import Conditions, _run


def state(family='tailless_swept'):
    a = build(family)
    return archive(a), {c.name: 0. for c in a.controls}


@pytest.mark.parametrize('family', ['tailless_swept', 'canard', 'tandem'])
def test_coordinate_contract_preserves_reflex_references_and_exact_reconstruction(family):
    record, controls = state(family)
    frozen = freeze(record, controls)
    plane = validate(frozen)
    a = build(family)
    assert sum(float(w.area()) for w in plane.wings) == pytest.approx(
        sum(float(w.area()) for w in a.airplane().wings))
    assert plane.s_ref == a.airplane().s_ref
    assert plane.c_ref == a.airplane().c_ref
    assert plane.b_ref == a.airplane().b_ref
    assert len(plane.wings) >= len(a.surfaces)*2
    assert frozen['payload']['independent_numerical_performance'] is False
    if family == 'tailless_swept':
        coords = np.array(frozen['payload']['wings'][0]['sections'][0]['coordinates'])
        assert coords[0, 1] > .01  # actual reflex at the trailing edge
    for key in ('coordinates', 'chord_m'):
        bad = deepcopy(frozen)
        sec = bad['payload']['wings'][0]['sections'][0]
        if key == 'coordinates':
            sec[key][0][1] += .001
        else:
            sec[key] *= 1.1
        bad['installed_sha256'] = digest(bad['payload'])
        with pytest.raises(ValueError, match='reconstruction'):
            validate(bad)


def test_controls_require_complete_finite_state_and_combined_physical_limit():
    record, controls = state()
    for bad in ({}, dict(controls, elevon_pitch=float('nan')),
                dict(controls, elevon_pitch=True), dict(controls, elevon_pitch=21.),
                dict(controls, elevon_pitch=15., elevon_roll=15.)):
        with pytest.raises(ValueError, match='control|saturated'):
            freeze(record, bad)
    a = freeze(record, controls)
    changed = freeze(record, dict(controls, elevon_pitch=5.))
    assert a['installed_sha256'] != changed['installed_sha256']
    changed['payload']['verified_gain'] = True
    changed['installed_sha256'] = digest(changed['payload'])
    with pytest.raises(ValueError, match='reconstruction'):
        validate(changed)


def test_actual_coordinate_controls_change_native_pitch_and_roll_without_hardware_moves():
    record, controls = state()
    a = build('tailless_swept')
    def run(states):
        return _run(a, Conditions(), 4., 6., 3, airplane=airplane(record, states))
    neutral = run(controls)
    pitch = run(dict(controls, elevon_pitch=5.))
    roll_pos = run(dict(controls, elevon_roll=5.))
    roll_neg = run(dict(controls, elevon_roll=-5.))
    assert abs(pitch['Cm']-neutral['Cm']) > .01
    assert abs(roll_pos['moment_geometry_nm'][0]) > .001
    assert roll_pos['moment_geometry_nm'][0] == pytest.approx(-roll_neg['moment_geometry_nm'][0], rel=1e-6)
    assert roll_pos['L'] == pytest.approx(roll_neg['L'], rel=1e-6)
    assert a.mass_summary() == build('tailless_swept').mass_summary()


@pytest.mark.parametrize('family,control', [('canard', 'foreplane_elevator'), ('tandem', 'front_elevator')])
def test_other_family_controls_change_native_pitch(family, control):
    record, controls = state(family)
    a = build(family)
    neutral = _run(a, Conditions(), 3., 6., 3, airplane=airplane(record, controls))
    moved = _run(a, Conditions(), 3., 6., 3,
        airplane=airplane(record, dict(controls, **{control: 5.})))
    assert abs(moved['Cm']-neutral['Cm']) > .005


def test_overlapping_tandem_hinges_do_not_invent_a_composite_flap():
    record, controls = state('tandem')
    with pytest.raises(ValueError, match='different hinges'):
        freeze(record, dict(controls, rear_elevator=3., aileron=2.))
