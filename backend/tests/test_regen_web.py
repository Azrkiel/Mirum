"""Browser API contracts using disposable fixture worker processes, no PicoGK required."""
import hashlib
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from engineering import regen_web as web
from tests.test_regen_spec import spec_data


@pytest.fixture
def browser(tmp_path, monkeypatch):
    builder = tmp_path / 'builder'
    builder.write_text('fixture')
    builder.chmod(0o700)
    (tmp_path / 'picogk.26.2.so').write_bytes(b'fixture')
    monkeypatch.setenv('REGEN_BUILDER', str(builder))
    runner = web.JobRunner(tmp_path / 'jobs', timeout=5)
    with TestClient(web.create_app(runner)) as client:
        yield client, runner


def wait_done(client, job_id):
    for _ in range(200):
        result = client.get(f'/api/jobs/{job_id}').json()
        if result['status'] not in ('queued', 'running'):
            return result
        time.sleep(0.02)
    pytest.fail('Fixture worker did not finish')


def fixture_worker(monkeypatch, exit_code=0, *, malformed=False, delay=0, tamper=''):
    script = """
import hashlib, json, pathlib, sys, time
out = pathlib.Path(sys.argv[1]); time.sleep(float(sys.argv[2]))
mesh = b'fixture-stl'
report = dict(passed=True, geometry_source='picogk_builder', engineering_certified=False,
              stl_sha256=hashlib.sha256(mesh).hexdigest(), failures=[],
              spec_sha256=hashlib.sha256(pathlib.Path(sys.argv[3]).read_bytes()).hexdigest())
(out/'cleaned.stl').write_bytes(mesh)
(out/'report.json').write_text(json.dumps(report))
(out/'spec.canonical.json').write_bytes(pathlib.Path(sys.argv[3]).read_bytes())
"""
    if malformed:
        script += "(out/'report.json').write_text('{}')\n"
    if tamper:
        script += f"(out/{tamper!r}).write_bytes(b'tampered')\n"
    script += f'raise SystemExit({exit_code})\n'
    monkeypatch.setattr(web, '_worker_command', lambda spec, out: [sys.executable, '-c', script, str(out), str(delay), str(spec)])


def test_browser_serves_offline_assets_and_schema(browser):
    client, _ = browser
    assert 'PicoGK' in client.get('/').text
    assert client.get('/app.js').status_code == 200
    assert client.get('/styles.css').status_code == 200
    assert client.get('/api/config').json()['ready'] is True
    assert client.get('/api/config').json()['examples']['small']['family'] == 'regen_chamber'


def test_invalid_spec_is_rejected_before_starting_worker(browser):
    client, runner = browser
    assert client.post('/api/jobs', json=spec_data(channel_count=999)).status_code == 422
    assert client.post('/api/jobs', json=spec_data(voxel_size_mm=0.1)).status_code == 422
    assert not runner.jobs


def test_accepted_job_exposes_only_bound_artifacts(browser, monkeypatch):
    client, _ = browser
    fixture_worker(monkeypatch)
    response = client.post('/api/jobs', json=spec_data())
    assert response.status_code == 202
    job = wait_done(client, response.json()['id'])
    assert job['status'] == 'accepted', job
    mesh = client.get(job['artifacts']['stl'])
    assert hashlib.sha256(mesh.content).hexdigest() == job['report']['stl_sha256']
    assert client.get(job['artifacts']['report']).json()['engineering_certified'] is False
    assert client.get(f"/api/jobs/{job['id']}/artifacts/worker.log").status_code == 404
    assert client.get('/api/jobs/not-a-job').status_code == 404


@pytest.mark.parametrize('exit_code, malformed', [(1, False), (2, False), (3, False), (0, True)])
def test_failure_or_untrusted_report_never_exposes_mesh(browser, monkeypatch, exit_code, malformed):
    client, _ = browser
    fixture_worker(monkeypatch, exit_code, malformed=malformed)
    job = wait_done(client, client.post('/api/jobs', json=spec_data()).json()['id'])
    assert job['status'] in ('rejected', 'unavailable')
    assert not job['artifacts']
    assert client.get(f"/api/jobs/{job['id']}/artifacts/stl").status_code == 409


def test_busy_runner_rejects_second_job_and_shutdown_kills_worker(browser, monkeypatch):
    client, runner = browser
    fixture_worker(monkeypatch, delay=30)
    assert client.post('/api/jobs', json=spec_data()).status_code == 202
    assert client.post('/api/jobs', json=spec_data()).status_code == 409
    runner.close()
    assert runner.process is None


def test_timeout_is_unavailable_and_no_artifact_leaks(browser, monkeypatch):
    client, runner = browser
    runner.timeout = 0.1
    fixture_worker(monkeypatch, delay=30)
    job = wait_done(client, client.post('/api/jobs', json=spec_data()).json()['id'])
    assert job['status'] == 'unavailable'
    assert 'time limit' in job['message']
    assert not job['artifacts']


def test_missing_runtime_fails_clearly(browser, monkeypatch):
    client, _ = browser
    monkeypatch.setenv('REGEN_BUILDER', '/no/such/builder')
    assert client.get('/api/config').json()['ready'] is False
    assert client.post('/api/jobs', json=spec_data()).status_code == 503


def test_output_limit_stops_worker_and_hides_artifacts(browser, monkeypatch):
    client, runner = browser
    runner.output_limit = 100
    fixture_worker(monkeypatch, delay=30)
    job = wait_done(client, client.post('/api/jobs', json=spec_data()).json()['id'])
    assert job['status'] == 'unavailable'
    assert 'output size limit' in job['message']
    assert not job['artifacts']


def test_cross_origin_generation_is_rejected(browser):
    client, _ = browser
    assert client.post('/api/jobs', json=spec_data(), headers={'Origin': 'https://other.example'}).status_code == 403


def test_oversized_body_is_rejected_before_parsing(browser):
    client, _ = browser
    assert client.post('/api/jobs', content=b' ' * 65537, headers={'Content-Type': 'application/json'}).status_code == 413


@pytest.mark.parametrize('body', [b'{invalid', b'[]', b'{"voxel_size_mm": NaN}'])
def test_malformed_json_is_a_validation_error(browser, body):
    client, runner = browser
    response = client.post('/api/jobs', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json()['detail']
    assert not runner.jobs


def test_expired_results_are_removed(browser, monkeypatch):
    client, runner = browser
    fixture_worker(monkeypatch)
    job = wait_done(client, client.post('/api/jobs', json=spec_data()).json()['id'])
    runner.jobs[job['id']]['updated'] = 0
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert not (runner.root / job['id']).exists()


@pytest.mark.parametrize('filename', ['spec.canonical.json', 'cleaned.stl'])
def test_inconsistent_artifact_hashes_fail_closed(browser, monkeypatch, filename):
    client, _ = browser
    fixture_worker(monkeypatch, tamper=filename)
    job = wait_done(client, client.post('/api/jobs', json=spec_data()).json()['id'])
    assert job['status'] == 'unavailable'
    assert not job['artifacts']
