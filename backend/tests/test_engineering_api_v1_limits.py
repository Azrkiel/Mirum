"""HTTP-level abuse limits on /engineering/v1: body size, input bound, draft caps,
AI spend budget, and resource ids kept out of logs. Offline; no provider call."""
import json
import logging

from backend.tests.test_native_api_hardening_ports import offline_models  # noqa: F401
from backend.tests.test_engineering_api_v1 import ai_client, client, finalized, main  # noqa: F401
from backend.tests.test_engineering_contracts import data


def test_oversized_body_is_rejected_by_content_length_before_parsing(client):
    response = client.post('/engineering/v1/drafts', content=b'{"input":"' + b'x' * 70000 + b'"}',
                           headers={'content-type': 'application/json'})
    assert response.status_code == 413
    assert client.post('/engineering/v1/drafts', json={'input': data()['content']}).status_code == 201


def test_oversized_body_without_content_length_is_rejected_while_reading(client):
    def chunks():
        yield b'{"input":{"note":"'
        for _ in range(8):
            yield b'x' * 10000
        yield b'"}}'
    response = client.post('/engineering/v1/drafts', content=chunks(), headers={'content-type': 'application/json'})
    assert response.status_code == 413


def test_serialized_input_is_bounded_at_the_model_even_under_the_body_cap(client):
    big = {'requirements': {'note': 'a' * 20000}}
    assert client.post('/engineering/v1/drafts', json={'input': big}).status_code == 422
    prepared = client.post('/engineering/v1/drafts', json={'input': data()['content']}).json()
    patch = {'expected_revision': 1, 'input': big}
    assert client.patch('/engineering/v1/drafts/' + prepared['draft']['draft_id'], json=patch).status_code == 422


def test_per_client_draft_cap_returns_429_and_leaves_form_mode_working_for_others(client):
    main.app.state.engineering.quota.per_client_drafts = 2
    codes = [client.post('/engineering/v1/drafts', json={'input': {}}).status_code for _ in range(3)]
    assert codes == [201, 201, 429]
    response = client.post('/engineering/v1/drafts', json={'input': {}})
    assert response.status_code == 429 and 'retry-after' in response.headers


def test_unfinalized_drafts_do_not_block_job_admission_storage(client):
    runtime = main.app.state.engineering
    for _ in range(5):
        client.post('/engineering/v1/drafts', json={'input': data()['content']})
    assert runtime.store.storage_bytes() == 0 and runtime.store.metadata_bytes() > 0


def test_ai_over_budget_returns_unavailable_without_provider_call_or_draft(ai_client):
    from engineering.limits import AiCallBudget
    from engineering.proposer import ProviderReply, ProviderSettings
    calls = []
    class Fixture:
        available = True
        def settings(self):
            return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture', temperature=0., max_tokens=4096)
        async def generate(self, *args):
            calls.append(args)
            return ProviderReply(text='{}')
    runtime = main.app.state.engineering
    runtime.proposer.provider = Fixture()
    runtime.proposer.budget = AiCallBudget(0, 1)
    before = runtime.store.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0]
    response = ai_client.post('/engineering/v1/drafts', json={'mode': 'ai', 'prompt': 'Describe a beam', 'input': {}})
    assert response.status_code == 503
    assert response.json()['state'] == 'unavailable' and response.json()['prepared'] is None
    assert calls == []
    assert runtime.store.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0] == before
    assert ai_client.get('/engineering/v1/capabilities').status_code == 200


def test_resource_ids_are_masked_in_the_request_log(client, caplog):
    prepared = client.post('/engineering/v1/drafts', json={'input': data()['content']}).json()
    draft_id = prepared['draft']['draft_id']
    with caplog.at_level(logging.INFO, logger='mirum.api'):
        assert client.get('/engineering/v1/drafts/' + draft_id).status_code == 200
        client.get('/engineering/v1/jobs/' + 'f' * 32 + '/artifacts/zip')
    assert draft_id not in caplog.text and 'f' * 32 not in caplog.text
    assert '/engineering/v1/drafts/{' in caplog.text


def test_log_masking_leaves_other_routes_alone():
    from starlette.requests import Request
    request = Request({'type': 'http', 'method': 'GET', 'path': '/stats', 'query_string': b'', 'headers': []})
    assert main._loggable_path(request) == '/stats'
    secret = Request({'type': 'http', 'method': 'GET', 'path': '/engineering/v1/jobs/abc/artifacts/zip',
                      'query_string': b'', 'headers': []})
    assert main._loggable_path(secret) == '/engineering/v1/jobs/{id}/artifacts/zip'
