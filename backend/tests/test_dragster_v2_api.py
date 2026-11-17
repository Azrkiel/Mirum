"""API dispatch with real bounded workers; provider calls forbidden."""
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import artifacts
from dragsters.styles import REGISTRY, load_preset


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(artifacts, '_RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(main.limiter, 'enabled', False)
    def forbidden(*args, **kwargs):
        pytest.fail('Provider invocation on deterministic workflow')
    monkeypatch.setattr(main.genai, 'GenerativeModel', forbidden)
    with TestClient(main.app) as client:
        yield client


@pytest.mark.parametrize('style', REGISTRY)
def test_preset_end_to_end_digests_checks_and_timing(client, style, monkeypatch):
    from dragsters import service
    original = service.ProcessRunner.run
    memory = []
    async def measured(self, command, **kwargs):
        source = (f'import sys; sys.path.insert(0, {str(Path(service.__file__).resolve().parents[1])!r}); '
                  'from dragsters.worker import main; '
                  f'sys.argv = ["worker", {command[-1]!r}]; rc = main(); '
                  'from pathlib import Path; '
                  'print("MEMORY " + " | ".join(line for line in Path("/proc/self/status").read_text().splitlines() '
                  'if line.startswith(("VmPeak:", "VmHWM:"))), file=sys.stderr); raise SystemExit(rc)')
        result = await original(self, [command[0], '-c', source], **kwargs)
        memory.extend(line for line in result.get('stderr', '').splitlines() if line.startswith('MEMORY '))
        return result
    monkeypatch.setattr(service.ProcessRunner, 'run', measured)
    times = []
    required = {'single_valid_solid', 'absolute_bounds', 'symmetry', 'reference_volume',
                'pocket_void', 'front_axle_void', 'rear_axle_void', 'pocket_wall',
                'front_bore_wall', 'rear_bore_wall', 'spec_revalidation',
                'wheel_clearance_front', 'wheel_clearance_rear', 'body_above_ground'}
    for _ in range(3):
        start = time.monotonic()
        response = client.post('/dragsters/generate', json=load_preset(style).model_dump())
        assert response.status_code == 200, response.text
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        result = events[-1]
        assert result['status'] == 'done', result
        assert style.replace('_', ' ') in events[0]['message']
        files = {fmt: client.get(f"/download/{result['run_id']}/{fmt}").content
                 for fmt in ('spec', 'report', 'step', 'glb')}
        times.append(time.monotonic() - start)
        assert result['digests'] == {fmt: hashlib.sha256(data).hexdigest() for fmt, data in files.items()}
        report = result['report']
        assert json.loads(files['report']) == report
        assert report['schema_version'] == 2
        assert report['generator_version'] == REGISTRY[style].generator_version
        assert required <= report['checks'].keys()
        assert all(check['passed'] is True for check in report['checks'].values())
        assert report['engineering_certified'] is False
    median = statistics.median(times)
    print(f'{style}: end-to-end seconds {times}; median={median:.3f}; target=12s; memory={memory}')
    assert median <= 25


def test_presets_are_registry_ordered_and_strict(client):
    response = client.get('/dragsters/presets')
    assert response.status_code == 200
    assert response.json() == {'styles': [dict(name=name, label=entry.label,
        preset=load_preset(name).model_dump()) for name, entry in REGISTRY.items()]}


@pytest.mark.parametrize('change', [{'unexpected': 1}, {'style': 'teardrop'},
                                  {'style': '../../compiler'}, {'pocket_center_z_mm': 60.0},
                                  {'schema_version': True}, {'length_mm': '220'}])
def test_invalid_v2_returns_422_and_no_artifacts(client, change):
    response = client.post('/dragsters/generate', json=load_preset('wedge').model_dump() | change)
    assert response.status_code == 422, response.text
    assert response.json()['detail']
    assert not list(artifacts._RUNS_DIR.glob('*'))


def test_v1_report_keys_stay_unchanged(client):
    payload = json.loads((Path(__file__).parents[1] / 'dragsters/examples/wedge.json').read_text())
    response = client.post('/dragsters/generate', json=payload)
    result = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')][-1]
    assert result['status'] == 'done', result
    assert result['spec'] == payload
    assert result['report']['generator_version'] == 'dragster_wedge_v1'
    assert set(result['report']) == {'schema_version', 'generator_version', 'cadquery_version', 'spec_sha256',
        'step_sha256', 'solid_count', 'bounds_mm', 'volume_mm3',
        'checks', 'passed', 'engineering_certified', 'preview', 'revision_id', 'coordinate_frame'}
