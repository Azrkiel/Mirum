"""Real native API success and HTTP validation without provider credentials."""
import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import artifacts

EXAMPLE = json.loads((Path(__file__).parents[1] / 'dragsters/examples/wedge.json').read_text())


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(artifacts, '_RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(main.limiter, 'enabled', False)
    def forbidden(*a, **kw):
        pytest.fail('Provider invocation on deterministic workflow')
    monkeypatch.setattr(main.genai, 'GenerativeModel', forbidden)
    with TestClient(main.app) as client:
        yield client


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_real_api_downloads_and_revision(client):
    result = events(client.post('/dragsters/generate', json=EXAMPLE))[-1]
    assert result['status'] == 'done', result
    files = {fmt: client.get(f"/download/{result['run_id']}/{fmt}").content for fmt in ('spec', 'report', 'step', 'glb')}
    assert result['digests'] == {fmt: hashlib.sha256(data).hexdigest() for fmt, data in files.items()}
    assert json.loads(files['report']) == result['report']
    assert json.loads(files['spec']) == result['spec']
    assert result['report']['revision_id'] == result['run_id']
    assert result['report']['passed']
    assert files['glb'].startswith(b'glTF')
    # Server expiry is explicit while client may retain its already-fetched bytes.
    artifacts.sweep(ttl_seconds=-1)
    missing = client.get(f"/download/{result['run_id']}/spec")
    assert missing.status_code == 404
    assert 'expired' in missing.json()['detail']


@pytest.mark.parametrize('payload', [{**EXAMPLE, 'pocket_center_z_mm': 40.0},
    {**EXAMPLE, 'width_mm': True}, {**EXAMPLE, 'unknown': 1}])
def test_invalid_spec(client, payload):
    assert client.post('/dragsters/generate', json=payload).status_code == 422
    assert not list(artifacts._RUNS_DIR.glob('*'))


def test_invalid_json(client):
    assert client.post('/dragsters/generate', content='{"broken":', headers={'Content-Type':'application/json'}).status_code == 422


def test_publication_failure_never_emits_success(client, monkeypatch):
    monkeypatch.setattr(artifacts, 'store', lambda *a, **kw: False)
    result = events(client.post('/dragsters/generate', json=EXAMPLE))
    assert result[-1]['status'] == 'error'
    assert not any(event.get('run_id') for event in result)
    assert not list(artifacts._RUNS_DIR.glob('*'))
