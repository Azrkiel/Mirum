import json
import zipfile

import pytest
from pydantic import ValidationError

from gliders.design_spec import DesignSpec, FlightConditions, preset
from gliders.flight_conditions import evaluate_conditions
from gliders.preview import preview, metadata
from gliders.evaluation import run_evaluation


@pytest.mark.parametrize('values', [dict(airspeed_m_s=0), dict(airspeed_m_s=16),
    dict(alpha_deg=16), dict(beta_deg=-11), dict(altitude_m=5001), dict(altitude_m=-1),
    dict(alpha_deg=float('nan')), dict(beta_deg=float('inf')), dict(wind_m_s=3), dict(mode='CFD')])
def test_invalid_conditions(values):
    with pytest.raises(ValidationError):
        FlightConditions(**values)


def manual(**changes):
    spec = preset()
    spec.flight_conditions = FlightConditions(mode='specified', **changes)
    return spec


def test_conditions_bind_revision_but_do_not_change_geometry():
    one, two = manual(airspeed_m_s=5), manual(airspeed_m_s=7)
    assert one.digest() != two.digest()
    assert preview(one)['geometry']['geometry_digest'] == preview(two)['geometry']['geometry_digest']
    assert DesignSpec.model_validate_json(one.model_dump_json()) == one
    assert metadata()['flight_controls']['altitude_m']['maximum'] == 5000
    assert preset().flight_conditions.mode == 'automatic_trim'


def test_actual_forces_respond_to_alpha_sideslip_speed_and_atmosphere():
    baseline = manual()
    gl = baseline.build()
    before = preview(baseline)['geometry']['geometry_digest']
    low = evaluate_conditions(gl, baseline)
    high_alpha = manual(alpha_deg=8)
    high_speed = manual(airspeed_m_s=8)
    sideslip = manual(beta_deg=5)
    altitude = manual(altitude_m=2000)
    assert evaluate_conditions(high_alpha.build(), high_alpha)['lift_N'] > low['lift_N']
    assert abs(evaluate_conditions(sideslip.build(), sideslip)['side_force_N']) > .001
    fast = evaluate_conditions(high_speed.build(), high_speed)
    assert fast['dynamic_pressure_Pa'] / low['dynamic_pressure_Pa'] == pytest.approx((8/5)**2)
    assert fast['lift_N'] > low['lift_N']
    thin = evaluate_conditions(altitude.build(), altitude)
    assert thin['air_density_kg_m3'] < low['air_density_kg_m3']
    assert thin['dynamic_pressure_Pa'] < low['dynamic_pressure_Pa']
    assert low['reynolds_mac'] > 30000
    assert low['mass_kg'] == pytest.approx(gl.mass_summary()['mass'])
    assert gl.wing_x is None and gl.ballast_kg == 0 and gl.tail_incidence_deg == 0
    assert preview(baseline)['geometry']['geometry_digest'] == before
    assert low['equilibrium_verified'] is False
    assert 'glide_distance_m' not in low and 'static_margin' not in low


def test_specified_evaluation_exports_exact_conditions_and_placement(tmp_path):
    spec = manual(alpha_deg=6, beta_deg=3, altitude_m=1000)
    result = run_evaluation(spec, tmp_path)
    assert result['analysis_mode'] == 'specified'
    assert result['operating_conditions'] == spec.flight_conditions.model_dump()
    assert result['input_revision'] == spec.digest()
    assert result['adjustments'] == {'wing_x': {'nominal': None, 'effective': spec.build().wing_position()[0]}}
    with zipfile.ZipFile(tmp_path / 'evaluation.zip') as z:
        data = json.loads(z.read('design.json'))
        assert data == result
        assert data['metrics']['lift_N'] > 0
        assert len(z.read('assembly.stl')) > 84


def test_infeasible_geometry_still_gets_specified_force_estimate():
    spec = manual(airspeed_m_s=12)
    result = evaluate_conditions(spec.build(), spec)
    assert 'specified airspeed exceeds designer maximum' in result['warnings']
    assert result['lift_N'] > 0


def test_specified_mode_preserves_loaded_candidate_balance():
    spec = manual()
    from gliders.design_spec import Placement, placement
    spec.placement = Placement(wing_x=.18, payload_x=.16, ballast_kg=.005, tail_incidence_deg=-2)
    gl = spec.build()
    before = placement(gl)
    evaluate_conditions(gl, spec)
    assert placement(gl) == before
