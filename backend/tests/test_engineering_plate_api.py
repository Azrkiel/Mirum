"""Public plate metadata, typed preparation, AI refusal and terminal job results."""
import json
import os
from unittest.mock import Mock

import pytest

pytest.importorskip('fastapi')

import config
from backend.tests.test_engineering_e05_api import (
    FakeProvider, _submit, ai_client, client, post_ai, runtime, spend_rows,
)
from backend.tests.test_engineering_generator_contracts import reference
from backend.tests.test_engineering_plate_adapter import PLATE_ID, brief_spec, fake_stage
from backend.tests.test_native_api_hardening_ports import offline_models
from engineering import proposal_contract, proposer
from engineering.capabilities import PLATE


def plate_input(spec):
    return dict(capability=reference(PLATE), specification=spec)


def draft_request(spec):
    return dict(capability=PLATE_ID, input=plate_input(spec))


def test_plate_capability_metadata(client):
    response = client.get('/engineering/v1/capabilities')
    assert response.status_code == 200, response.text
    entries = response.json()['capabilities']
    assert entries[0]['id'] == 'rectangular_cantilever'
    plate, = [entry for entry in entries[1:] if entry['id'] == PLATE_ID]
    assert plate['version'] == '1.0.0'
    assert plate['claim'] == 'checked_design'
    assert plate['engineering_certified'] is False
    assert plate['standards'] == 'not_evaluated'
    assert plate['available'] is True
    assert 'smallest passing thickness' in plate['selection_rule']
    public = json.dumps(plate)
    for internal in ('engineering.', '.py', 'backend/', 'refuse_with'):
        assert internal not in public


def test_typed_plate_draft(client):
    spec = brief_spec()
    response = client.post('/engineering/v1/drafts', json=draft_request(spec))
    assert response.status_code == 201, response.text
    prepared = response.json()
    assert prepared['issues'] == []
    content = prepared['draft']['content']
    assert content['schema_version'] == 'engineering_generator_brief_v1'
    assert content['capability'] == reference(PLATE)
    assert content['specification'] == spec
    assert len(prepared['brief_sha256']) == 64


@pytest.mark.parametrize(('field', 'value', 'issue_field'), [
    ('length_a_mm', 300.0, 'requirements'),   # a < b (400 mm)
    ('length_a_mm', 2100.0, 'requirements'),  # a/b > 5
    ('poissons_ratio', 0.46, 'requirements.poissons_ratio'),
    ('hole_diameter_mm', 5.0, 'requirements.hole_diameter_mm'),
    ('edge_condition', 'free', 'requirements.edge_condition'),
])
def test_invalid_plate_requirements_cannot_prepare_a_brief(client, field, value, issue_field):
    spec = brief_spec(**{field: value})
    response = client.post('/engineering/v1/drafts', json=draft_request(spec))
    assert response.status_code == 201, response.text
    prepared = response.json()
    assert prepared['draft']['content'] is None
    assert prepared['brief_sha256'] is None
    assert 'specification.' + issue_field in [issue['field'] for issue in prepared['issues']]


def test_nonfinite_plate_pressure_is_refused_at_the_http_boundary(client):
    # Send a JSON number that overflows a float, bypassing httpx's finite-only encoder.
    from fastapi.testclient import TestClient
    body = json.dumps(draft_request(brief_spec(pressure_mpa='OVERFLOW'))).replace('"OVERFLOW"', '1e999')
    # Platform behaviour (not plate-specific, reported to the lead): FastAPI's default validation handler
    # cannot render the echoed non-finite input, so the refusal is a 500 rather than a 422. Either way no
    # draft is created.
    raw = TestClient(client.app, raise_server_exceptions=False)
    response = raw.post('/engineering/v1/drafts', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code in (422, 500), response.text
    assert 'draft' not in response.text


@pytest.mark.parametrize('enabled', [False, True], ids=['disabled', 'no-proposer'])
def test_plate_ai_request_spends_nothing(request, monkeypatch, enabled):
    provider = FakeProvider()
    monkeypatch.setattr(proposer, 'GeminiProposalProvider', lambda: provider)
    monkeypatch.setattr(config, 'ENGINEERING_AI_DRAFTS', enabled)
    http = request.getfixturevalue('ai_client' if enabled else 'client')
    value = runtime()
    resolve = Mock(wraps=proposal_contract.ai_proposer)
    monkeypatch.setattr(proposal_contract, 'ai_proposer', resolve)
    if enabled:
        budget = value.proposer.budget
        assert budget.has_capacity() and budget.used() == 0
        assert 'ai' not in PLATE.proposers
    response = post_ai(http, PLATE_ID, 'Design a simply supported rectangular plate under uniform pressure.')
    if enabled:
        resolve.assert_called_once()
        assert response.status_code == 422, response.text
        assert response.json()['detail'] == 'Invalid or unsupported engineering input'
        assert budget.used() == 0
    else:
        resolve.assert_not_called()
        assert response.status_code == 503, response.text
        assert response.json()['detail'] == 'AI draft preparation is not enabled'
    assert provider.calls == 0
    assert spend_rows(value) == []


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
@pytest.mark.parametrize(('thickness', 'outcome'), [(10.0, 'accepted'), (25.0, 'rejected')])
def test_simply_supported_plate_job(client, monkeypatch, fake_stage, thickness, outcome):
    jobs = runtime().jobs
    monkeypatch.setattr(jobs.generators[PLATE_ID], '_stage', fake_stage['stage'])
    payload = plate_input(brief_spec((thickness,), edge_condition='simply_supported'))
    job = _submit(client, payload, capability=PLATE_ID)
    client.portal.call(jobs.wait, job['job_id'])
    response = client.get('/engineering/v1/jobs/' + job['job_id'])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['lifecycle'] == 'completed' and result['outcome'] == outcome, result
    assert fake_stage['calls'] == [thickness]
    if outcome == 'accepted':
        assert result['claim'] == 'checked_design'
        assert result['delivery'] == 'available'
    else:
        attempt, = result['attempts']
        assert 'thin_ratio: b/h = 16 < 20' in attempt['failures']
