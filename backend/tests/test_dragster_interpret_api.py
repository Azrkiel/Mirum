"""Strict HTTP and early routing, with every provider path mocked."""
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from dragsters import extraction
from dragsters.styles import load_preset


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.limiter, 'enabled', False)
    monkeypatch.setattr(main.genai, 'GenerativeModel', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unmocked provider')))
    with TestClient(main.app) as client:
        yield client


def test_interpret_resolved_without_worker(client, monkeypatch):
    from dragsters import service
    monkeypatch.setattr(service, 'generate', AsyncMock(side_effect=AssertionError('CAD invoked')))
    resolved = dict(status='resolved', spec=load_preset('teardrop').model_dump(), changed_fields={}, provenance={})
    interpreter = AsyncMock(return_value=resolved)
    monkeypatch.setattr(extraction, 'interpret', interpreter)
    response = client.post('/dragsters/interpret', json=dict(prompt='CO₂ teardrop', base_spec=None))
    assert response.status_code == 200 and response.json() == resolved
    interpreter.assert_awaited_once_with('CO₂ teardrop', None)
    service.generate.assert_not_awaited()


@pytest.mark.parametrize('payload', [dict(prompt=''), dict(prompt='x'*2001), dict(prompt=True),
                                   dict(prompt='wedge', extra=1), dict(prompt='wedge',base_spec={'schema_version':1}),
                                   dict(prompt='wedge',base_spec=load_preset('wedge').model_dump()|{'style':'../kernel'})])
def test_strict_http_validation(client, payload):
    assert client.post('/dragsters/interpret', json=payload).status_code == 422


@pytest.mark.parametrize('error', [extraction.ExtractionUnavailable('internal'), extraction.ExtractionUnavailable('unit-test-placeholder /tmp/private')])
def test_unavailable_safe_503(client, monkeypatch, error):
    monkeypatch.setattr(extraction, 'interpret', AsyncMock(side_effect=error))
    response = client.post('/dragsters/interpret', json={'prompt':'wedge'})
    assert response.status_code == 503
    assert response.json() == {'message': 'Text interpretation is unavailable right now; the parameter form still works.'}


def test_invalid_base_spec_is_clean_422(client, monkeypatch):
    monkeypatch.setattr(extraction, 'interpret', AsyncMock(side_effect=extraction.InvalidBaseSpec('x')))
    response = client.post('/dragsters/interpret', json={'prompt': 'wedge'})
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['body', 'base_spec']


def test_cap_reached_is_the_same_safe_503_without_provider(client, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'unit-test-placeholder')
    monkeypatch.setattr(extraction, '_daily_cap', lambda: 0)
    response = client.post('/dragsters/interpret', json={'prompt': 'CO2 wedge'})
    assert response.status_code == 503
    assert response.json() == {'message': 'Text interpretation is unavailable right now; the parameter form still works.'}


def test_missing_key_503(client, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY_HEAVY', raising=False)
    assert client.post('/dragsters/interpret', json={'prompt':'wedge'}).status_code == 503


def test_early_pipeline_route_without_provider(client, monkeypatch):
    rea = AsyncMock(side_effect=AssertionError('provider invoked'))
    monkeypatch.setattr(main, 'run_rea', rea)
    response = client.post('/generate', json={'prompt':'design a CO2 dragster'})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert len(events) == 1 and events[0]['step'] == 'route'
    assert events[0]['workflow'] == 'co2_dragster'
    rea.assert_not_awaited()
    rea = AsyncMock(side_effect=RuntimeError('stop after routing check'))
    monkeypatch.setattr(main, 'run_rea', rea)
    response = client.post('/generate', json={'prompt':'design a pinewood derby car'})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert not any(event['step'] == 'route' for event in events)
    rea.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_sdk_schema_and_explicit_key_client_without_network(monkeypatch):
    from unittest.mock import MagicMock
    import google.ai.generativelanguage_v1beta as language
    from config import GEMINI_MODEL_LITE
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY','unit-test-placeholder')
    response = language.GenerateContentResponse(candidates=[language.Candidate(content=language.Content(
        parts=[language.Part(text='{"changes":[],"unsupported":[],"questions":[]}')]))])
    native = MagicMock()
    native.generate_content = AsyncMock(return_value=response)
    native.transport.close = AsyncMock()
    factory = MagicMock(return_value=native)
    monkeypatch.setattr(language,'GenerativeServiceAsyncClient',factory)
    result = await extraction.interpret('CO2 wedge',None)
    assert result['status'] == 'resolved'
    request = native.generate_content.call_args.args[0]
    assert request.model == 'models/' + GEMINI_MODEL_LITE
    assert request.generation_config.max_output_tokens == 2048
    factory.assert_called_once_with(client_options={'api_key':'unit-test-placeholder'})
    native.transport.close.assert_awaited_once()


def test_pinned_sdk_still_exposes_the_per_model_client_slot():
    """extraction assigns model._async_client; fail loudly if the pinned SDK drops it."""
    import inspect
    import google.generativeai as genai
    model = genai.GenerativeModel('models/placeholder')
    assert hasattr(model, '_async_client')
    assert '_async_client' in inspect.getsource(genai.GenerativeModel.generate_content_async)
