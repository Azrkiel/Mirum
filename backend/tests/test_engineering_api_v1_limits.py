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


def _on_loop():
    import asyncio
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def test_full_draft_and_audit_storage_does_not_refuse_job_admission(client):
    """The H3 property: a volume full of drafts and audits still admits a reviewed job,
    and the blocking admission work runs off the event loop while the task starts on it."""
    import time
    runtime = main.app.state.engineering
    _, brief = finalized(client)
    runtime.quota.max_drafts = 5
    runtime.quota.per_client_drafts = 5
    created = [client.post('/engineering/v1/drafts', json={'input': {}}).status_code for _ in range(6)]
    assert created.count(201) >= 4 and created[-1] == 429       # the draft cap is genuinely full
    with runtime.store.transaction() as db:                     # and so is audit storage
        db.execute('INSERT INTO audits VALUES (?,?,?)', ('junk', 1, 'x' * 5_000_000))
    assert runtime.store.metadata_bytes() > 5_000_000 and runtime.store.storage_bytes() == 0
    seen = {}
    original_admit, original_start = runtime.jobs.admit, runtime.jobs.start
    def admit(*args, **kwargs):
        seen['admit_on_loop'] = _on_loop()
        return original_admit(*args, **kwargs)
    def start(*args, **kwargs):
        seen['start_on_loop'] = _on_loop()
        return original_start(*args, **kwargs)
    runtime.jobs.admit, runtime.jobs.start = admit, start
    response = client.post('/engineering/v1/jobs', json={'brief_id': brief['brief_id'], 'idempotency_key': 'after-full-drafts'})
    assert response.status_code == 202, response.text
    assert seen == {'admit_on_loop': False, 'start_on_loop': True}
    job_id = response.json()['job_id']
    assert client.post('/engineering/v1/jobs/' + job_id + '/cancel').status_code == 200
    for _ in range(300):
        if client.get('/engineering/v1/jobs/' + job_id).json()['lifecycle'] in ('completed', 'unavailable', 'cancelled'):
            break
        time.sleep(.1)


def test_draft_creation_quota_work_runs_off_the_event_loop(client):
    runtime = main.app.state.engineering
    seen = []
    original = runtime.quota.admit_draft
    def spy(*args, **kwargs):
        seen.append(_on_loop())
        return original(*args, **kwargs)
    runtime.quota.admit_draft = spy
    assert client.post('/engineering/v1/drafts', json={'input': {}}).status_code == 201
    assert seen == [False]


def test_patch_growth_beyond_the_byte_cap_is_refused(client):
    runtime = main.app.state.engineering
    prepared = client.post('/engineering/v1/drafts', json={'input': {}}).json()
    runtime.quota.max_draft_bytes = 100        # smaller than the edited record
    response = client.patch('/engineering/v1/drafts/' + prepared['draft']['draft_id'],
                            json={'expected_revision': 1, 'input': data()['content']})
    assert response.status_code == 429


def test_legacy_generate_has_the_same_body_cap_hourly_quota_and_no_queue(client, monkeypatch):
    import engineering.api as legacy
    from backend.tests.test_engineering_spec import spec_data
    calls = []
    async def fast(specification, heights):
        calls.append(1)
        return {'status': 'rejected'}
    monkeypatch.setattr(legacy, 'generate_with_feedback', fast)
    body = {'specification': spec_data()}
    oversized = client.post('/engineering/generate', content=b'{"x":"' + b'y' * 70000 + b'"}',
                            headers={'content-type': 'application/json'})
    assert oversized.status_code == 413
    main.app.state.engineering.fairness.per_hour = 1
    assert client.post('/engineering/generate', json=body).status_code == 200
    quota = client.post('/engineering/generate', json=body)
    assert quota.status_code == 429 and 'retry-after' in quota.headers and calls == [1]
    main.app.state.engineering.fairness.per_hour = 100
    main.app.state.limiter.reset()
    class Held:
        def locked(self): return True
    monkeypatch.setattr(legacy, '_slots', Held())
    busy = client.post('/engineering/generate', json=body)      # refused at once, never queued
    assert busy.status_code == 429 and busy.headers['retry-after'] == '5' and calls == [1]


def test_uvicorn_access_log_filter_masks_engineering_ids_only():
    record = logging.LogRecord('uvicorn.access', logging.INFO, __file__, 1,
                               '%s - "%s %s HTTP/%s" %d',
                               ('1.2.3.4:5', 'GET', '/engineering/v1/jobs/' + 'e' * 32 + '/artifacts/zip?x=1', '1.1', 200), None)
    assert any(isinstance(f, main._MaskEngineeringIds) for f in logging.getLogger('uvicorn.access').filters)
    assert main._MaskEngineeringIds().filter(record) is True
    assert 'e' * 32 not in record.getMessage() and '/engineering/v1/jobs/{id}/artifacts/zip?x=1' in record.getMessage()
    other = logging.LogRecord('uvicorn.access', logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                              ('1.2.3.4:5', 'GET', '/stats', '1.1', 200), None)
    main._MaskEngineeringIds().filter(other)
    assert '/stats' in other.getMessage()


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
