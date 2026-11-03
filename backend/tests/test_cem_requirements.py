import json
import math
import time
import pytest
from pydantic import ValidationError

from cem import VERSION
from cem.core import canonical_json, output_id
from cem.glider.requirements import GliderMissionRequirements as Mission
from cem.glider.calibration import initial, version, update_drag, save, load
from cem.glider.feasibility import feasibility
from cem.glider.physics import launch_state, trajectory
from cem.glider.processes import spar_size, PROCESSES
from cem.extraction import clarifications


def mission(**changes):
    return Mission.model_validate(dict({'launch': {'method': 'drop'}, 'reference_target':1200.}, **changes))


@pytest.mark.parametrize('changes', [
    {'objective':'speed'}, {'mode':'meet'}, {'reference_target':float('nan')},
    {'launch':{'method':'drop','launch_speed_m_s':5.}},
    {'launch':{'method':'hi_start'}},
    {'environment':{'wind_m_s':2.}}, {'environment':{'kind':'wind','uplift_m_s':.2}},
    {'environment':{'kind':'thermal'}}, {'environment':{'kind':'ridge'}},
    {'constraints':{'max_span_m':10.}}, {'constraints':{'max_mass_kg':'0.5'}},
    {'discovery':{'seed':True}}, {'discovery':{'budget_evals':100000}},
    {'discovery':{'families':[]}}, {'discovery':{'families':['conventional','conventional']}},
    {'unknown':1},
])
def test_invalid_requirements_fail_before_physics(changes):
    with pytest.raises(ValidationError):
        Mission.model_validate(dict({'launch': {'method':'drop'}}, **changes))


def test_identity_pins_every_requirement_and_versions():
    req = mission()
    identifier = output_id(req, VERSION, version(initial()))
    assert identifier == output_id(Mission.model_validate_json(req.model_dump_json()), VERSION, version(initial()))
    for changes in [{'reference_target':1201.}, {'process':'balsa'}, {'discovery':{'seed':43}}]:
        changed = Mission.model_validate(dict(req.model_dump(), **changes))
        assert output_id(changed, VERSION, version(initial())) != identifier
    assert output_id(req, VERSION+'x', version(initial())) != identifier
    assert output_id(req, VERSION, 'another-calibration') != identifier
    assert canonical_json({'b':1,'a':2}) == canonical_json({'a':2,'b':1})
    with pytest.raises(ValueError): canonical_json({'bad':float('nan')})


def test_reference_1200_m_from_10_m_is_gap_not_a_search_stop():
    start = time.perf_counter()
    report = feasibility(mission(), initial())
    assert time.perf_counter() - start < 1
    assert report['verdict'] == 'infeasible'
    assert report['continue_search'] is True
    assert report['required_lift_to_drag'] == pytest.approx(120)
    assert 100 < report['optimistic_bound'] < 1200
    assert report['reference_gap'] == pytest.approx(1200 - report['optimistic_bound'])
    assert report['bounds_source']['xfoil'] == 'not_cross_validated'
    assert report['verdict_scope'] == 'sampled_model_envelope'


def test_1200_seconds_reality_check():
    report = feasibility(mission(objective='duration_s'), initial())
    assert report['verdict'] == 'infeasible' and report['continue_search']
    assert report['required_sink_m_s'] == pytest.approx(10/1200)
    assert report['optimistic_sink_m_s'] > report['required_sink_m_s']


def test_zoom_and_wind_and_sustained_lift_have_finite_semantics():
    req = mission(launch={'method':'discus'})
    launch = launch_state(req.launch, initial())
    assert launch['effective_height_m'] == pytest.approx(10+.6*25**2/(2*9.81))
    assert launch['assumptions']
    still = trajectory(10, 5, .4)
    wind = trajectory(10, 5, .4, wind=2)
    assert wind['distance_m'] > still['distance_m'] and wind['duration_s'] == still['duration_s']
    sustained = trajectory(10, 5, .4, uplift=.4)
    assert sustained['state'] == 'sustained_lift_limited' and sustained['distance_m'] is None
    with pytest.raises(ValueError): trajectory(10, float('nan'), .4)
    # A finite still-air bound cannot declare windy/uplift missions infeasible.
    assert feasibility(mission(environment={'kind':'uniform_uplift','uplift_m_s':1.}), initial())['optimistic_bound'] is None


def test_headwind_does_not_remove_airborne_duration():
    report = trajectory(10., 5., .4, wind=-10.)
    assert report['state'] == 'no_forward_progress'
    assert report['distance_m'] == 0. and report['duration_s'] == pytest.approx(25.)


def test_spar_stress_is_within_allowable_and_scales_with_launch_load():
    low, high = spar_size(.4,1.2,'drop'), spar_size(.4,1.2,'catapult')
    assert low['stress_pa'] <= low['allowable_with_sf_pa']
    assert high['stress_pa'] <= high['allowable_with_sf_pa']
    assert high['mass_kg'] > low['mass_kg']
    assert low['root_moment_nm'] == pytest.approx(2*.4*9.81*1.2/(3*math.pi))
    for process in PROCESSES:
        report = feasibility(mission(process=process), initial())
        assert report['wing_loading_lower_bound_n_m2'] > 0


def test_synthetic_drag_recovery_and_immutable_old_version(monkeypatch,tmp_path):
    monkeypatch.setenv('MIRUM_WORK_DIR', str(tmp_path))
    prior = initial()
    before = canonical_json(prior)
    fitted = update_drag(prior, [1.35]*30)
    assert fitted['cd0_factor']['median'] == pytest.approx(1.35,rel=.05)
    assert canonical_json(prior) == before
    old, new = save(prior), save(fitted)
    assert old != new and load(old) == prior and load(new) == fitted
    assert version(load(old)) == old
    with pytest.raises(ValueError): load('../unsafe')


def test_changed_calibration_cannot_reuse_an_uncalibrated_infeasibility_bound():
    calibrated = update_drag(initial(), [.7] * 20)
    report = feasibility(mission(), calibrated)
    assert report['verdict'] == 'marginal'
    assert report['optimistic_bound'] is None and report['reference_gap'] is None
    assert report['bound_state'] == 'rebuild_required_for_calibration'


def test_protocol_sizing_cannot_bypass_the_compute_budget():
    from cem.glider.service import GliderCEM
    with pytest.raises(ValidationError):
        GliderCEM(initial()).size(mission(), 1000000)


def test_ambiguous_units_missing_launch_and_mode_require_clarification():
    assert clarifications('1200 m of flight duration from 10 m')
    assert clarifications('maximize distance, launched from 10 m')
    assert not clarifications('Maximize distance from a 10 m drop; 1200 m reference target')
    schema = json.dumps(Mission.model_json_schema())
    assert 'exclusiveMinimum' not in schema and 'exclusiveMaximum' not in schema
