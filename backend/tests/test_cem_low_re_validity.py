"""Low-Re method-validity rule (docs/implementation/cem-low-re-method-decision.md, P1)."""
import copy
import math

import pytest

from cem.evidence import number
from cem.glider.calibration import initial
from cem.glider.physics import launch_state
from cem.glider.requirements import GliderMissionRequirements as Mission
from cem.glider.uncertainty import uncertainty
from cem.glider.validity import (DEGRADED_CD_FACTOR_P90, DEGRADED_CD_LOG_SIGMA, REYNOLDS_DEGRADED_BELOW,
                                 REYNOLDS_FLOOR, check_envelope, station_validity)


def test_floor_and_degraded_band_values_follow_the_decision():
    assert REYNOLDS_FLOOR == 50000. and REYNOLDS_DEGRADED_BELOW == 70000.
    assert math.exp(1.2815515655446004 * DEGRADED_CD_LOG_SIGMA) == pytest.approx(DEGRADED_CD_FACTOR_P90)


@pytest.mark.parametrize('min_re, admitted, degraded', [
    (30000., False, True), (49999., False, True), (50000., True, True),
    (69999., True, True), (70000., True, False), (250000., True, False)])
def test_reynolds_boundaries(min_re, admitted, degraded):
    envelope, reason = station_validity([.95, .9], [min_re, 300000.])
    assert (reason is None) is admitted
    assert envelope['low_re_degraded'] is degraded
    assert envelope['reynolds_floor'] == 50000.
    if not admitted:
        assert 'section Re below method validity floor' in reason


def test_confidence_nonfinite_and_missing_evidence_fail_closed():
    assert 'confidence' in station_validity([.79], [100000.])[1]
    assert station_validity([.8], [100000.])[1] is None
    assert 'nonfinite' in station_validity([float('nan')], [100000.])[1]
    assert 'nonfinite' in station_validity([.9], [float('inf')])[1]
    assert 'missing' in station_validity([], [])[1]
    both = station_validity([.5], [40000.])[1]
    assert 'floor' in both and 'confidence' in both


def test_parent_check_rejects_below_floor_and_inconsistent_degraded_flag():
    good, _ = station_validity([.9], [60000.])
    check_envelope(good, number)
    with pytest.raises(ValueError):
        check_envelope(dict(good, min_station_reynolds=40000., low_re_degraded=True), number)
    with pytest.raises(ValueError, match='degraded-band flag'):
        check_envelope(dict(good, low_re_degraded=False), number)
    clear, _ = station_validity([.9], [90000.])
    check_envelope(clear, number)
    with pytest.raises(ValueError, match='degraded-band flag'):
        check_envelope(dict(clear, low_re_degraded=True), number)


def _result(min_re):
    envelope, reason = station_validity([.95], [min_re])
    assert reason is None
    return dict(CL_trim=.6, CD_trim=.04, aspect_ratio=8., cd_profile=.025, CLmax_est=1.2,
                glide_speed_m_s=6., validity_envelope=envelope)


def test_degraded_band_widens_profile_drag_and_lowers_p10_only_when_flagged():
    req = Mission(launch={'method': 'drop'}, process='balsa')
    calibration = initial()
    launch = launch_state(req.launch, calibration)
    clear = uncertainty(req, _result(90000.), calibration, 'balsa', launch)
    degraded = uncertainty(req, _result(60000.), calibration, 'balsa', launch)
    assert clear['low_re_degraded_prior']['applied'] is False
    assert clear['low_re_degraded_prior']['log_sigma'] is None
    assert degraded['low_re_degraded_prior']['applied'] is True
    assert degraded['low_re_degraded_prior']['log_sigma'] == DEGRADED_CD_LOG_SIGMA
    p10 = lambda bands: bands['bands']['distance_m']['p10']
    assert p10(degraded) < p10(clear)
    # Non-degraded designs draw no extra samples: seeded bands are reproducible.
    again = uncertainty(req, copy.deepcopy(_result(90000.)), calibration, 'balsa', launch)
    assert again['bands'] == clear['bands']
