"""MIRUM_ACCESS_TOKEN gates every backend route; unset leaves the API open."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GEMINI_API_KEY_HEAVY', 'offline-fixture-never-sent')

import main
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.limiter, 'enabled', False)
    with TestClient(main.app) as test_client:
        yield test_client


def test_unset_token_leaves_api_open(client, monkeypatch):
    monkeypatch.delenv('MIRUM_ACCESS_TOKEN', raising=False)
    assert client.get('/auth/check').status_code == 200


@pytest.mark.parametrize('header', [None, '', 'wrong', 'fixture-token-x'])
def test_missing_or_wrong_token_is_rejected_before_any_route(client, monkeypatch, header):
    monkeypatch.setenv('MIRUM_ACCESS_TOKEN', 'fixture-token')
    headers = {} if header is None else {'X-Mirum-Access-Token': header}
    for method, path in [('get', '/auth/check'), ('post', '/run'), ('post', '/generate'),
                         ('get', '/stats'), ('get', '/openapi.json')]:
        kwargs = {'json': {}} if method == 'post' else {}
        response = getattr(client, method)(path, headers=headers, **kwargs)
        assert response.status_code == 401, (path, response.text)
        assert response.json() == {'detail': 'Access token required.'}


def test_correct_token_reaches_the_route(client, monkeypatch):
    monkeypatch.setenv('MIRUM_ACCESS_TOKEN', 'fixture-token')
    headers = {'X-Mirum-Access-Token': 'fixture-token'}
    assert client.get('/auth/check', headers=headers).json() == {'ok': True}
    # Past the gate, ordinary request validation applies.
    assert client.post('/run', headers=headers, json={}).status_code == 422
