from copy import deepcopy

import pytest

from cem.core import digest
from cem.glider.families import archive, build, capabilities
from cem.glider.families import control_response as response


@pytest.mark.parametrize('family', ['tailless_swept', 'canard', 'tandem'])
def test_native_control_response_retains_fixed_geometry_and_all_channels(family):
    record = archive(build(family))
    report = response.sweep(record, dict(coarse_resolution=2, fine_resolution=3))
    assert report['geometry'] == record
    assert report['production_admission'] is False
    assert report['qualified_control_authority'] is False
    assert report['equilibrium_solved'] is False
    assert report['verified_gain'] is False
    assert report['physical_construction'] is False
    assert report['sample_count'] == 2 * (1 + 2 * len(build(family).controls))
    for level in report['mesh_levels']:
        assert len(level['channels']) == len(build(family).controls)
        for channel in level['channels']:
            assert channel['positive']['controls'][channel['name']] == 2.
            assert channel['negative']['controls'][channel['name']] == -2.
            for sample in (channel['negative'], channel['positive']):
                assert sum(v != 0 for v in sample['controls'].values()) == 1
                assert len(sample['raw']['force_geometry_n']) == 3
                assert len(sample['raw']['moment_geometry_nm']) == 3
            # Symmetric channels induce pitch; differential channels induce roll.
            component = 1 if channel['symmetric'] else 0
            assert abs(channel['central_derivative_per_rad']['moment_geometry_nm'][component]) > .001
            if not channel['symmetric']:
                assert channel['positive']['raw']['moment_geometry_nm'][0] == pytest.approx(
                    -channel['negative']['raw']['moment_geometry_nm'][0], rel=1e-6)
    from cem.glider.families.native_workflow import replay
    assert replay(report) is False
    for field in ('Cm', 'qualified_control_authority', 'conditions', 'installed_sha256'):
        changed = deepcopy(report)
        sample = changed['mesh_levels'][0]['channels'][0]['positive']
        if field == 'Cm':
            sample['raw']['Cm'] += .1
        elif field == 'conditions':
            changed['conditions']['alpha_deg'] += 1.
        elif field == 'installed_sha256':
            sample[field] = '0'*64
        else:
            changed[field] = True
        changed['report_sha256'] = digest({k: v for k, v in changed.items() if k != 'report_sha256'})
        with pytest.raises(ValueError, match='reconstruction'):
            replay(changed)
    assert capabilities()[family]['native_control_response'] == 'fixed_operating_point_central_difference'


@pytest.mark.parametrize('options', [dict(step_deg=0.), dict(step_deg=float('nan')),
    dict(step_deg=True), dict(step_deg=1e-12), dict(step_deg=11.), dict(speed_m_s=0.),
    dict(alpha_deg=16.), dict(coarse_resolution=3, fine_resolution=3)])
def test_invalid_conditions_rejected_before_native_solving(options, monkeypatch):
    monkeypatch.setattr(response, '_run', lambda *a, **k: pytest.fail('solver must not run'))
    with pytest.raises(ValueError):
        response.sweep(archive(build('tailless_swept')), options)


def test_replay_rejects_scope_changes_and_promotion():
    with pytest.raises(ValueError, match='scope'):
        response.replay({'scope': 'production'})


def test_nonfinite_native_control_output_is_rejected(monkeypatch):
    monkeypatch.setattr(response, '_run', lambda *a, **k: dict(Cm=float('nan')))
    with pytest.raises(ValueError, match='Nonfinite|nonfinite'):
        response.sweep(archive(build('canard')), dict(coarse_resolution=2, fine_resolution=3))


def test_control_response_cli_and_no_clobber(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    record = tmp_path/'archive.json'
    record.write_text(json.dumps(archive(build('tandem'))))
    conditions = tmp_path/'conditions.json'
    conditions.write_text(json.dumps(dict(coarse_resolution=2, fine_resolution=3)))
    output = tmp_path/'response.json'
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, '-m', 'cem.glider.families.native_workflow', 'control-response',
               str(record), '--conditions', str(conditions), '--output', str(output)]
    env = dict(os.environ, PYTHONPATH=str(root))
    subprocess.run(command, env=env, check=True, capture_output=True, timeout=60)
    assert response.replay(json.loads(output.read_text())) is False
    original = output.read_bytes()
    result = subprocess.run(command, env=env, capture_output=True, timeout=60)
    assert result.returncode != 0 and b'new file' in result.stderr
    assert output.read_bytes() == original
