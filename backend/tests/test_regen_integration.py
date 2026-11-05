"""Main deterministic API integration, without CAD, provider calls or PicoGK."""
from contextlib import asynccontextmanager
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from engineering.api import attach_engineering_routes
from engineering import regen_web as web
from tests.test_regen_spec import spec_data


@pytest.fixture
def integrated(tmp_path, monkeypatch):
    runner = web.JobRunner(tmp_path / 'jobs')

    @asynccontextmanager
    async def lifespan(app):
        async with web.regen_lifespan(app, runner):
            yield

    app = FastAPI(lifespan=lifespan)
    limiter = Limiter(key_func=get_remote_address, enabled=False)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    attach_engineering_routes(app, limiter)
    monkeypatch.setattr(web, '_ready', lambda: True)
    with TestClient(app) as client:
        yield client, runner
    assert runner.closed


def test_main_api_exposes_packaged_presets_and_nozzle_schema(integrated, monkeypatch, tmp_path):
    client, _ = integrated
    # The production backend image does not include root-level examples/assets.
    monkeypatch.setattr(web, 'ROOT', tmp_path)
    response = client.get('/engineering/regen/config')
    assert response.status_code == 200
    config = response.json()
    assert config['ready'] is True
    assert config['examples']['small']['channel_count'] == 24
    assert config['schema']['properties']['voxel_size_mm']['minimum'] == 0.4
    assert 'Do not manufacture or fire' in config['notice']
    assert client.get('/').status_code == 404
    assert '/engineering/generate' in client.get('/openapi.json').json()['paths']


def test_main_api_serves_only_accepted_artifacts_at_its_own_prefix(integrated):
    client, runner = integrated
    # Submit through the real route while suppressing only execution of the worker.
    runner.pool.shutdown()
    class Deferred:
        def submit(self, *args): pass
        def shutdown(self, **kwargs): pass
    runner.pool = Deferred()
    response = client.post('/engineering/regen/jobs', json=spec_data())
    assert response.status_code == 202, response.text
    job_id = response.json()['id']
    assert client.get(f'/engineering/regen/jobs/{job_id}/artifacts/stl').status_code == 409
    output = runner.root / job_id / 'output'
    output.mkdir()
    mesh = b'accepted fixture mesh'
    report = {'passed': True, 'engineering_certified': False,
              'stl_sha256': hashlib.sha256(mesh).hexdigest()}
    (output / 'cleaned.stl').write_bytes(mesh)
    (output / 'report.json').write_text(json.dumps(report))
    runner._update(job_id, status='accepted', report=report,
                   artifacts={name: f'/api/jobs/{job_id}/artifacts/{name}' for name in web.FILES})
    job = client.get(f'/engineering/regen/jobs/{job_id}').json()
    for name, url in job['artifacts'].items():
        assert url == f'/engineering/regen/jobs/{job_id}/artifacts/{name}'
    assert client.get(job['artifacts']['stl']).content == mesh
    assert client.get(job['artifacts']['report']).json() == report
    assert client.get(f'/engineering/regen/jobs/{job_id}/artifacts/worker.log').status_code == 404


@pytest.mark.parametrize('changes', [{'channel_count': 999}, {'voxel_size_mm': 0.1}, {'family': 'nozzle'}])
def test_main_api_rejects_unsupported_parameters_before_execution(integrated, changes):
    client, runner = integrated
    assert client.post('/engineering/regen/jobs', json=spec_data(**changes)).status_code == 422
    assert not runner.jobs


def test_main_api_missing_runtime_is_unavailable_without_jobs(integrated, monkeypatch):
    client, runner = integrated
    monkeypatch.setattr(web, '_ready', lambda: False)
    assert client.get('/engineering/regen/config').json()['ready'] is False
    assert client.post('/engineering/regen/jobs', json=spec_data()).status_code == 503
    assert not runner.jobs


def test_main_api_retains_body_size_bound(integrated):
    client, runner = integrated
    assert client.post('/engineering/regen/jobs', content=b' ' * 65537).status_code == 413
    assert not runner.jobs


@pytest.mark.parametrize('headers', [{'Origin': 'https://other.example'}, {'Sec-Fetch-Site': 'cross-site'}])
def test_main_api_blocks_cross_origin_builds(integrated, monkeypatch, headers):
    client, runner = integrated
    monkeypatch.setattr(web, '_ready', lambda: False)
    assert client.post('/engineering/regen/jobs', json=spec_data(), headers=headers).status_code == 403
    assert not runner.jobs


def test_main_api_limits_generation_attempts(integrated, monkeypatch):
    client, runner = integrated
    client.app.state.limiter.enabled = True
    monkeypatch.setattr(web, '_ready', lambda: False)
    codes = [client.post('/engineering/regen/jobs', json=spec_data()).status_code for _ in range(3)]
    assert codes == [503, 503, 429]
    assert not runner.jobs


def test_packaged_presets_match_native_examples():
    from engineering.regen_presets import EXAMPLES
    from pathlib import Path
    for name, spec in EXAMPLES.items():
        original = Path(__file__).resolve().parents[2] / 'picogk-builder' / 'examples' / f'{name}.json'
        assert spec == json.loads(original.read_text())
