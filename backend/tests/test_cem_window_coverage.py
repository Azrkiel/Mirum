import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('window_diagnostic', ROOT / 'scripts/cem/diagnose_window_coverage.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def _skip_unless_runtime_reproduces_receipt_floats(state):
    """Skip when this runtime cannot regenerate the receipt's exact coordinates.

    installed_sha256 hashes full-precision floats. Airfoil coordinate generation
    differs in the last bits across numpy builds and CPU features, and hosted CI
    runners differ from run to run, so the same commit can match on one machine
    and not on another. The foil digests are compared per state: if the rebuilt
    coordinates do not hash to the receipt's foil digests, the identity cannot
    match and the replay is skipped. If they do match, an identity change is a
    real regression and diagnose() fails the test.
    """
    from cem.core import digest
    from cem.glider.families.coordinate_polars import coordinates_payload
    from cem.glider.families.installed import freeze
    payload = freeze(state['archive'], state['controls'])['payload']
    rebuilt = {digest(coordinates_payload(section['coordinates']))
               for wing in payload['wings'] for section in wing['sections']}
    recorded = set(state['foils'])
    if rebuilt != recorded:
        pytest.skip('Runtime does not reproduce the receipt float output for '
                    f"{state['family']}: rebuilt foil digests {sorted(d[:12] for d in rebuilt)}, "
                    f'receipt {sorted(d[:12] for d in recorded)}; installed-state identity cannot match')


def test_actual_installed_states_are_not_covered_by_historical_narrow_windows():
    receipt = json.loads((ROOT / 'docs/testing/cem-coordinate-window-results.json').read_text())
    assert receipt['installed_states'] and all(s['foils'] for s in receipt['installed_states'])
    for state in receipt['installed_states']:
        _skip_unless_runtime_reproduces_receipt_floats(state)
    for state in receipt['installed_states']:
        result = module.diagnose(state, receipt['windows'])
        assert json.loads(json.dumps(result, allow_nan=False)) == result
        assert result['outside_count'] > 0
        assert result['common_reynolds_speed_interval']['empty'] is True
        assert result['historical_evidence_replayed'] is False
        assert result['converged_flight_coverage'] == 'unestablished'
        assert result['production_admission'] is False
        for strip in result['strips']:
            for check in strip['historical_window_checks']:
                lo, hi = check['speed_interval_m_s']
                assert lo > 0 and hi > lo
                assert strip['reynolds'] * lo / 6 == pytest.approx(check['reynolds_grid'][0])


def test_changed_installed_controls_cannot_reuse_state_identity():
    receipt = json.loads((ROOT / 'docs/testing/cem-coordinate-window-results.json').read_text())
    state = deepcopy(receipt['installed_states'][0])
    state['controls']['elevon_pitch'] = 1.
    with pytest.raises(ValueError, match='identity changed'):
        module.diagnose(state, receipt['windows'])


@pytest.mark.parametrize('conditions', [dict(speed_m_s=float('nan')), dict(alpha_deg=float('inf')),
                                        dict(altitude_m=-1)])
def test_invalid_operating_points_rejected(conditions):
    with pytest.raises(ValueError, match='operating point'):
        module.diagnose({}, [], **conditions)
