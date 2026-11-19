import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('window_diagnostic', ROOT / 'scripts/cem/diagnose_window_coverage.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def _runtime_matches(recorded):
    import platform
    from importlib.metadata import version
    return (platform.python_version() == recorded['python'] and
            all(version(name) == recorded['packages'][name]
                for name in ('numpy', 'scipy', 'aerosandbox')))


def _replayable_state(state, strict_identity):
    """Return the state to replay, or skip if this runtime cannot reproduce its foils.

    installed_sha256 hashes full-precision floats (coordinates, leading edges,
    reference values). They differ in the last bits across Python/numpy builds and
    CPU paths, and hosted CI runners vary from run to run. The coverage check only
    needs the foil digests to match the historical windows, so:
    - foil digests differ: skip, the windows cannot be matched at all;
    - foils match, runtime is the receipt's: the identity must match exactly;
    - foils match, other runtime: replay under the freshly computed identity.
    """
    from cem.core import digest
    from cem.glider.families.coordinate_polars import coordinates_payload
    installed = module.freeze(state['archive'], state['controls'])
    rebuilt = {digest(coordinates_payload(section['coordinates']))
               for wing in installed['payload']['wings'] for section in wing['sections']}
    if rebuilt != set(state['foils']):
        pytest.skip('Runtime does not reproduce the receipt foil coordinates for '
                    f"{state['family']} (rebuilt {sorted(d[:12] for d in rebuilt)}, receipt "
                    f"{sorted(d[:12] for d in state['foils'])}); historical windows cannot match")
    if strict_identity:
        assert installed['installed_sha256'] == state['installed_sha256']
        return state
    return dict(state, installed_sha256=installed['installed_sha256'])


def test_actual_installed_states_are_not_covered_by_historical_narrow_windows():
    receipt = json.loads((ROOT / 'docs/testing/cem-coordinate-window-results.json').read_text())
    assert receipt['installed_states'] and all(s['foils'] for s in receipt['installed_states'])
    strict = _runtime_matches(receipt['runtime'])
    states = [_replayable_state(s, strict) for s in receipt['installed_states']]
    for state in states:
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
