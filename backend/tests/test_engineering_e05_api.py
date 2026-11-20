"""E05 generator AI drafts over HTTP (POST /engineering/v1/drafts mode=ai) and the job-view claim.

Host-runnable: each generator AI proposer, with a fake provider, feeds BriefService.create.
CI only (fastapi + native TestClient): the HTTP tests; their fixtures skip without fastapi.
Nothing here calls a live model: every proposer gets a fake provider / extract / interpret.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
from types import SimpleNamespace

import pytest

from engineering import proposal_contract as pc
from engineering import regen_presets
from engineering.briefs import BriefService, MemoryBriefRepository
from engineering.capabilities import get_generator
from engineering.limits import AiCallBudget
from engineering.proposer import ProviderReply, ProviderSettings

try:  # registers the repo's offline_models fixture where fastapi exists (CI); a no-op on the host
    from backend.tests.test_native_api_hardening_ports import offline_models  # noqa: F401
except ImportError:
    pass

REGEN_PROMPT = ('Regeneratively cooled rocket thrust chamber with cooling channels: '
                'chamber radius 36 mm, throat radius 20 mm, 30 channels.')
GLIDER_PROMPT = 'Design a glider launched by drop that maximizes distance, using conventional and t_tail families.'
DRAGSTER_PROMPT = 'Design a CO2 dragster with a wedge body'
MARKER = 'RAW-REPLY-MARKER'
SECRET = 'SECRET-EXC-TEXT-9137'
UNAVAILABLE_KEYS = {'state', 'reasons', 'prepared'}


# --- fakes (same shapes as the proposer tests) ----------------------------------------------

class FakeProvider:
    def __init__(self, replies=(), error=None):
        self.replies, self.error, self.calls, self.available = list(replies), error, 0, True

    def settings(self):
        return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture-model',
                                temperature=0, max_tokens=1024)

    async def generate(self, prompt, schema, settings):
        self.calls += 1
        if self.error:
            raise self.error
        return ProviderReply(text=self.replies.pop(0))


def regen_reply(fields=None, **over):
    body = dict(state='proposed', out_of_family=False, preset=None, fields=fields or {},
                questions=[], unsupported_features=[])
    body.update(over)
    return json.dumps(body)


REGEN_GOOD = regen_reply(dict(chamber_radius_mm=36.0, throat_radius_mm=20.0, channel_count=30))


def glider_requirements(families=('conventional', 't_tail')):
    from cem.glider.requirements import GliderMissionRequirements
    body = GliderMissionRequirements(launch=dict(method='drop')).model_dump(mode='json')
    body['discovery']['families'] = families if isinstance(families, str) else list(families)
    return body


class FakeExtract:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def __call__(self, prompt):
        self.calls += 1
        if self.error:
            raise self.error
        return copy.deepcopy(self.result)


def dragster_resolution():
    from dragsters.styles import load_preset
    spec = load_preset('wedge').model_dump(mode='json')
    provenance = {}

    def walk(node, prefix=''):
        for key, value in node.items():
            if isinstance(value, dict) and value:
                walk(value, prefix + key + '.')
            else:
                provenance[prefix + key] = 'preset:wedge'
    walk(spec)
    provenance['wheels.front_diameter_mm'] = 'supplied'
    return dict(status='resolved', spec=spec, provenance=provenance, changed_fields={})


def fake_interpret(result=None):
    async def interpret(prompt, base_spec=None):
        return copy.deepcopy(result if result is not None else dragster_resolution())
    return interpret


# --- host-runnable: proposer -> BriefService.create -----------------------------------------

def propose(capability, fake, prompt):
    from engineering import dragster_proposer, glider_proposer, regen_proposer
    budget = AiCallBudget(daily_calls=5, concurrency=1)
    module = dict(regen_chamber=regen_proposer, glider_cem=glider_proposer, co2_dragster=dragster_proposer)[capability]
    proposal = asyncio.run(module.AiProposer(budget, fake).propose(prompt))
    pc.conform(capability, proposal)
    return proposal


def through_brief_service(proposal):
    return BriefService(MemoryBriefRepository()).create(
        proposal.payload, provenance=proposal.provenance, extra_issues=tuple(proposal.issues))


CASES = {
    'regen_proposed': ('regen_chamber', lambda: FakeProvider([REGEN_GOOD]), REGEN_PROMPT, 'proposed', 'ai'),
    'regen_clarify': ('regen_chamber', lambda: FakeProvider([regen_reply(
        dict(chamber_radius_mm=36.0), state='clarification_required', questions=['Which throat radius?'])]),
        REGEN_PROMPT, 'clarification_required', 'ai'),
    'glider_proposed': ('glider_cem', lambda: FakeExtract(dict(status='review_requirements',
        requirements=glider_requirements())), GLIDER_PROMPT, 'proposed', 'ai'),
    'glider_clarify': ('glider_cem', lambda: FakeExtract(dict(status='review_requirements',
        requirements=glider_requirements('all'))), GLIDER_PROMPT, 'clarification_required', 'ai'),
    'dragster_proposed': ('co2_dragster', lambda: fake_interpret(), DRAGSTER_PROMPT, 'proposed', 'ai'),
    # A bare dragster clarification carries no specification: its provenance is the proposer's form-mode empty one.
    'dragster_clarify': ('co2_dragster', lambda: fake_interpret(dict(status='needs_clarification',
        questions=['Which body style?'])), DRAGSTER_PROMPT, 'clarification_required', 'form'),
}


@pytest.mark.parametrize('name', sorted(CASES))
def test_proposals_pass_through_the_brief_service(name):
    capability, make_fake, prompt, state, mode = CASES[name]
    proposal = propose(capability, make_fake(), prompt)
    assert proposal.state == state
    prepared = through_brief_service(proposal)
    assert prepared.draft.provenance.input_mode == mode
    if state == 'proposed':
        assert prepared.draft.content is not None and not prepared.issues
        assert prepared.draft.content.capability.id == capability
    else:
        assert prepared.draft.clarifications or prepared.issues


# --- CI only: HTTP --------------------------------------------------------------------------

def _client(request, monkeypatch, tmp_path, flag):
    pytest.importorskip('fastapi.testclient')
    from fastapi.testclient import TestClient
    from backend.tests.test_native_api_hardening_ports import main
    request.getfixturevalue('offline_models')
    if flag:
        import config
        monkeypatch.setattr(config, 'ENGINEERING_AI_DRAFTS', True)
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'engineering'))
    return TestClient, main


@pytest.fixture
def client(request, tmp_path, monkeypatch):
    TestClient, main = _client(request, monkeypatch, tmp_path, False)
    with TestClient(main.app) as c:
        main.app.state.limiter.reset()
        yield c


@pytest.fixture
def ai_client(request, tmp_path, monkeypatch):
    """Same app with MIRUM_ENGINEERING_AI_DRAFTS on (config constant, read at startup)."""
    TestClient, main = _client(request, monkeypatch, tmp_path, True)
    with TestClient(main.app) as c:
        main.app.state.limiter.reset()
        yield c


def runtime():
    from backend.tests.test_native_api_hardening_ports import main
    return main.app.state.engineering


def install_factory(monkeypatch, fake, *, build=None):
    """Replace proposal_contract.ai_proposer: real class resolution, fake provider, the budget it was given."""
    calls = []

    def factory(capability_id, *, enabled, budget=None):
        calls.append(dict(capability=capability_id, enabled=enabled, budget=budget))
        if build is not None:
            return build(budget)
        module, _, attribute = get_generator(capability_id).proposers['ai'].partition(':')
        return getattr(importlib.import_module(module), attribute)(budget, fake)
    monkeypatch.setattr(pc, 'ai_proposer', factory)
    return calls


def forbid_factory(monkeypatch):
    calls = []

    def factory(*args, **kwargs):
        calls.append(args)
        raise AssertionError('ai_proposer must not be called')
    monkeypatch.setattr(pc, 'ai_proposer', factory)
    return calls


def post_ai(client, capability, prompt, **over):
    body = dict(capability=capability, mode='ai', prompt=prompt, input={})
    body.update(over)
    return client.post('/engineering/v1/drafts', json=body)


def assert_unavailable(response):
    assert response.status_code == 503, response.text
    body = response.json()
    assert set(body) == UNAVAILABLE_KEYS
    assert body['state'] == 'unavailable' and body['prepared'] is None
    assert isinstance(body['reasons'], list) and body['reasons']
    return body


@pytest.mark.parametrize('capability,prompt', [('regen_chamber', REGEN_PROMPT), ('glider_cem', GLIDER_PROMPT),
                                               ('co2_dragster', DRAGSTER_PROMPT)])
def test_flag_off_generator_ai_is_503_and_resolves_no_proposer(client, monkeypatch, capability, prompt):
    calls = forbid_factory(monkeypatch)
    response = post_ai(client, capability, prompt)
    assert response.status_code == 503
    assert response.json()['detail'] == 'AI draft preparation is not enabled'
    assert calls == []


def test_regen_ai_draft_is_unverified_and_uses_one_budget_unit(ai_client, monkeypatch):
    budget = runtime().proposer.budget
    calls = install_factory(monkeypatch, FakeProvider([REGEN_GOOD]))
    before = budget.used()
    response = post_ai(ai_client, 'regen_chamber', REGEN_PROMPT)
    assert response.status_code == 201, response.text
    assert calls and all(c['budget'] is budget and c['enabled'] is True for c in calls)
    body = response.json()
    assert body['draft']['content'] is not None
    provenance = body['draft']['provenance']
    assert provenance['input_mode'] == 'ai'
    proposed = [o for o in provenance['field_origins'] if o['origin'] == 'ai_proposed']
    assert proposed and all('unverified proposal' in o['reference'] for o in proposed)
    assert budget.used() - before == 1


def test_repair_call_uses_two_units_and_the_raw_reply_never_reaches_the_response(ai_client, monkeypatch):
    budget = runtime().proposer.budget
    provider = FakeProvider([MARKER + ' this is not json', REGEN_GOOD])
    install_factory(monkeypatch, provider)
    before = budget.used()
    response = post_ai(ai_client, 'regen_chamber', REGEN_PROMPT)
    assert response.status_code == 201, response.text
    assert budget.used() - before == 2 and provider.calls == 2
    assert MARKER not in response.text


def test_glider_complete_prompt_records_the_calibration_policy(ai_client, monkeypatch):
    install_factory(monkeypatch, FakeExtract(dict(status='review_requirements', requirements=glider_requirements())))
    response = post_ai(ai_client, 'glider_cem', GLIDER_PROMPT)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['draft']['content'] is not None
    origins = {o['field']: o for o in body['draft']['provenance']['field_origins']}
    assert origins['specification.calibration_version']['origin'] == 'policy'
    assert body['draft']['provenance']['input_mode'] == 'ai'


def test_glider_needing_clarification_is_a_201_with_the_issue(ai_client, monkeypatch):
    install_factory(monkeypatch, FakeExtract(dict(status='needs_clarification', questions=['Which launch?'])))
    response = post_ai(ai_client, 'glider_cem', GLIDER_PROMPT)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['draft']['content'] is None
    assert 'clarification_required' in [i['state'] for i in body['issues']]


def test_dragster_resolved_interpretation_is_a_draft(ai_client, monkeypatch):
    budget = runtime().proposer.budget
    install_factory(monkeypatch, fake_interpret())
    before = budget.used()
    response = post_ai(ai_client, 'co2_dragster', DRAGSTER_PROMPT)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['draft']['content'] is not None and body['draft']['provenance']['input_mode'] == 'ai'
    assert budget.used() - before == 1


def test_exhausted_budget_is_unavailable_before_any_proposer(ai_client, monkeypatch):
    budget = runtime().proposer.budget
    while budget.reserve():
        budget.release()
    assert not budget.has_capacity()
    calls = forbid_factory(monkeypatch)
    assert_unavailable(post_ai(ai_client, 'regen_chamber', REGEN_PROMPT))
    assert calls == []


def test_non_conforming_proposal_is_unavailable_and_never_stored(ai_client, monkeypatch):
    class Stub:
        def __init__(self, budget):
            self.budget = budget

        async def propose(self, prompt, base_spec=None):
            return SimpleNamespace(state='proposed', payload=dict(capability=dict(id='co2_dragster', version='0'),
                                                                  leak='LEAK-PAYLOAD-MARKER'),
                                   issues=(), provenance=None, reasons=())
    install_factory(monkeypatch, None, build=Stub)
    response = post_ai(ai_client, 'regen_chamber', REGEN_PROMPT)
    body = assert_unavailable(response)
    assert 'LEAK-PAYLOAD-MARKER' not in response.text and body['prepared'] is None


def test_provider_failure_is_unavailable_and_leaks_nothing(ai_client, monkeypatch):
    install_factory(monkeypatch, FakeProvider(error=RuntimeError(SECRET)))
    response = post_ai(ai_client, 'regen_chamber', REGEN_PROMPT)
    assert_unavailable(response)
    assert SECRET not in response.text


@pytest.mark.parametrize('over', [dict(input={'x': 1}), dict(prompt=None), dict(prompt='x' * 2001)])
def test_invalid_ai_input_is_422_and_spends_nothing(ai_client, monkeypatch, over):
    budget = runtime().proposer.budget
    calls = forbid_factory(monkeypatch)
    before = budget.used()
    response = post_ai(ai_client, 'regen_chamber', REGEN_PROMPT, **over)
    assert response.status_code == 422, response.text
    assert budget.used() == before and calls == []


def test_beam_ai_mode_keeps_its_own_path_and_flag_message(client, monkeypatch):
    # Beam mode=ai with the flag off (the live beam B13 path is covered in test_engineering_api_v1.py).
    calls = forbid_factory(monkeypatch)
    response = client.post('/engineering/v1/drafts',
                           json={'mode': 'ai', 'prompt': 'Describe a beam', 'capability': 'rectangular_cantilever',
                                 'input': {}})
    assert response.status_code == 503 and response.json()['detail'] == 'AI draft preparation is not enabled'
    assert calls == []


def _submit(client, payload, capability=None):
    from engineering.contracts import BriefDraft
    body = dict(input=payload) if capability is None else dict(capability=capability, input=payload)
    made = client.post('/engineering/v1/drafts', json=body)
    assert made.status_code == 201, made.text
    draft = BriefDraft.model_validate_json(json.dumps(made.json()['draft']))
    review = dict(draft_id=draft.draft_id, draft_revision=draft.revision, brief_sha256=draft.content.brief_sha256,
                  acknowledged_fields=list(draft.content.review_fields), reviewed_at='2026-11-17T12:05:00Z',
                  reviewed_by='local-user')
    brief = client.post('/engineering/v1/briefs', json=review)
    assert brief.status_code == 201, brief.text
    job = client.post('/engineering/v1/jobs', json=dict(brief_id=brief.json()['brief_id'], idempotency_key='claim'))
    assert job.status_code == 202, job.text
    return job.json()


def test_beam_job_view_claims_a_checked_design(client):
    from backend.tests.test_engineering_contracts import data
    job = _submit(client, data()['content'])
    assert job['claim'] == 'checked_design'
    assert client.get('/engineering/v1/jobs/' + job['job_id']).json()['claim'] == 'checked_design'
    client.post('/engineering/v1/jobs/' + job['job_id'] + '/cancel')


def test_generator_job_view_claims_geometry_checks_only(client):
    ref = pc.reference(get_generator('regen_chamber'))
    job = _submit(client, dict(capability=ref, specification=dict(regen_presets.EXAMPLES['small'])),
                  capability='regen_chamber')
    assert job['claim'] == 'geometry_checks_only'
    assert client.get('/engineering/v1/jobs/' + job['job_id']).json()['claim'] == 'geometry_checks_only'
    client.post('/engineering/v1/jobs/' + job['job_id'] + '/cancel')
