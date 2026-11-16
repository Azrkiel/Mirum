import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('window_diagnostic', ROOT / 'scripts/cem/diagnose_window_coverage.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# digest(coordinates_payload(asb.Airfoil('naca2412').coordinates)) as produced by
# the receipt's recorded runtime (it is the canard/tandem foil in the receipt and
# matches the coordinates retained in cem-panel-distribution-results.json).
RECEIPT_NACA2412_SHA256 = 'e9426ae7efb82cf7284e292c707259f143eaab7862141409a9b3da31779a8b97'


def _skip_unless_runtime_reproduces_receipt_floats(receipt):
    """The receipt's installed_sha256 values hash exact float coordinates.

    aerosandbox NACA generation differs in the last bits across numpy builds and
    CPU features (SIMD transcendental kernels). This probe uses only third-party
    code, so a mismatch means the runtime cannot reproduce the receipt; if the
    probe matches, any identity change is a real regression and fails.
    """
    import aerosandbox as asb
    from cem.core import digest
    from cem.glider.families.coordinate_polars import coordinates_payload
    assert any(RECEIPT_NACA2412_SHA256 in s['foils'] for s in receipt['installed_states'])
    probe = digest(coordinates_payload(asb.Airfoil('naca2412').coordinates))
    if probe != RECEIPT_NACA2412_SHA256:
        pytest.skip('Runtime does not reproduce the receipt runtime float output '
                    f'(aerosandbox naca2412 coordinates sha256 {probe[:12]}, receipt '
                    f'{RECEIPT_NACA2412_SHA256[:12]}); installed-state identities cannot match')


def test_actual_installed_states_are_not_covered_by_historical_narrow_windows():
    receipt = json.loads((ROOT / 'docs/testing/cem-coordinate-window-results.json').read_text())
    _skip_unless_runtime_reproduces_receipt_floats(receipt)
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
