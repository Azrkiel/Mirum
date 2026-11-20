"""Cross-family refusal at the E05 proposer and public draft boundaries."""
import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, Mock

import pytest

from cem.glider.requirements import GliderMissionRequirements
from engineering import glider_proposer, proposal_contract, proposer, regen_proposer
from engineering.capabilities import GENERATORS, GLIDER, REGEN, UnsupportedCapability
from engineering.limits import AiCallBudget
from engineering.router import route

try:  # registers the repo's offline_models fixture where fastapi exists (CI); a no-op on the host
    from backend.tests.test_native_api_hardening_ports import offline_models  # noqa: F401
except ImportError:
    pass


FAMILY_PROMPTS = {
    'co2_dragster': ('Design a wedge body for a CO2 dragster.',
                     'Make a teardrop CO2 race car.'),
    'regen_chamber': ('Draw a regeneratively cooled rocket chamber.',
                      'Model a thrust chamber with cooling channels.'),
    'glider_cem': ('Design a hand-launched glider for distance.',
                   'Make a passive glider for maximum flight time.'),
    'rectangular_cantilever': ('Size a rectangular cantilever beam.',
                               'Check the tip load on a rectangular cantilever beam.'),
}
NO_MATCH = ('Design a coffee mug.', 'Make a bookshelf.')
AMBIGUOUS = ('Design a CO2 dragster and a hand-launched glider for distance.',
             'Size a rectangular cantilever beam and model a thrust chamber with cooling channels.')


@pytest.mark.parametrize(('expected_id', 'reason', 'prompt'), [
    *((family, None, prompt) for family, prompts in FAMILY_PROMPTS.items() for prompt in prompts),
    *((None, 'No registered capability matched', prompt) for prompt in NO_MATCH),
    *((None, 'Ambiguous', prompt) for prompt in AMBIGUOUS),
])
def test_prompt_routes(expected_id, reason, prompt):
    decision = route(prompt)
    assert decision.capability_id == expected_id
    if reason == 'Ambiguous':
        assert decision.reason.startswith('Ambiguous')
    elif reason is not None:
        assert decision.reason == reason


def other_family_prompts(requested_id):
    return [prompt for family, prompts in FAMILY_PROMPTS.items()
            if family != requested_id for prompt in prompts]


def fake_regen(budget, *, out_of_family=False):
    reply = dict(state='proposed', out_of_family=out_of_family, preset='reference',
                 fields={}, questions=[], unsupported_features=[])
    provider = Mock(available=True)
    provider.settings.return_value = proposer.ProviderSettings(
        provider='fixture', sdk_version='fixture', model='fixture', temperature=0, max_tokens=1024)
    provider.generate = AsyncMock(return_value=proposer.ProviderReply(text=json.dumps(reply)))
    return regen_proposer.AiProposer(budget, provider), provider.generate


def fake_glider(budget, *, needs_clarification=False):
    if needs_clarification:
        reply = dict(status='needs_clarification', questions=['Which object should be designed?'])
    else:
        requirements = GliderMissionRequirements(launch=dict(method='drop')).model_dump(mode='json')
        requirements['discovery']['families'] = ['conventional']
        reply = dict(status='review_requirements', requirements=requirements)
    extract = AsyncMock(return_value=reply)
    return glider_proposer.AiProposer(budget, extract), extract


PROPOSERS = ((REGEN, fake_regen), (GLIDER, fake_glider))


@pytest.mark.parametrize(('descriptor', 'fake'), PROPOSERS, ids=[REGEN.id, GLIDER.id])
def test_substituting_fake_can_propose_in_its_own_family(descriptor, fake):
    budget = AiCallBudget(daily_calls=1, concurrency=1)
    ai, call = fake(budget)
    proposal = asyncio.run(ai.propose(FAMILY_PROMPTS[descriptor.id][0]))
    assert proposal_contract.conform(descriptor.id, proposal).state == 'proposed'
    call.assert_awaited_once()


@pytest.mark.parametrize(('descriptor', 'fake', 'prompt'), [
    (descriptor, fake, prompt) for descriptor, fake in PROPOSERS
    for prompt in other_family_prompts(descriptor.id)
])
def test_other_family_skips_provider(descriptor, fake, prompt):
    budget = AiCallBudget(daily_calls=1, concurrency=1)
    ai, call = fake(budget)
    proposal = asyncio.run(ai.propose(prompt))
    call.assert_not_called()
    assert budget.used() == 0
    assert proposal.state == 'unsupported'
    assert 'specification' not in proposal.payload
    assert proposal.payload['capability'] == proposal_contract.reference(descriptor)
    assert proposal_contract.conform(descriptor.id, proposal) is proposal


@pytest.mark.parametrize('prompt', NO_MATCH)
@pytest.mark.parametrize(('descriptor', 'fake', 'kwargs', 'state'), [
    (REGEN, fake_regen, dict(out_of_family=True), 'unsupported'),
    (GLIDER, fake_glider, dict(needs_clarification=True), 'clarification_required'),
])
def test_no_match_uses_provider_disposition(descriptor, fake, kwargs, state, prompt):
    budget = AiCallBudget(daily_calls=1, concurrency=1)
    ai, call = fake(budget, **kwargs)
    proposal = asyncio.run(ai.propose(prompt))
    call.assert_awaited_once()
    assert budget.used() == 1
    assert proposal.state == state
    assert 'specification' not in proposal.payload
    assert proposal_contract.conform(descriptor.id, proposal) is proposal


@pytest.fixture
def ai_client(request, tmp_path, monkeypatch):
    TestClient = pytest.importorskip('fastapi.testclient').TestClient
    from backend.tests.test_native_api_hardening_ports import main
    request.getfixturevalue('offline_models')  # no live model can be reached
    import config

    monkeypatch.setattr(config, 'ENGINEERING_AI_DRAFTS', True)
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'engineering'))
    with TestClient(main.app) as client:
        main.app.state.limiter.reset()
        yield client


@pytest.mark.parametrize(('capability', 'prompt', 'state'), [
    (descriptor.id, prompt, state) for descriptor in GENERATORS
    for state, prompts in (('unsupported', other_family_prompts(descriptor.id)),
                           ('clarification_required', AMBIGUOUS))
    for prompt in prompts
])
def test_api_refuses_before_ai(ai_client, monkeypatch, capability, prompt, state):
    def unexpected(*args, **kwargs):
        raise AssertionError('Family gate reached the AI path')

    monkeypatch.setattr(proposal_contract, 'ai_proposer', unexpected)
    budget = ai_client.app.state.engineering.proposer.budget
    monkeypatch.setattr(budget, 'has_capacity', unexpected)
    used = budget.used()
    response = ai_client.post('/engineering/v1/drafts', json=dict(
        capability=capability, mode='ai', prompt=prompt, input={}))
    assert response.status_code == 201, response.text
    prepared = response.json()
    assert ('request', state) in [(issue['field'], issue['state']) for issue in prepared['issues']]
    assert prepared['draft']['content'] is None
    assert prepared['input']['capability']['id'] == capability
    assert budget.used() == used


def test_beam_b13_contract_is_pinned():
    prompt_hash = hashlib.sha256(proposer.EXTRACTION_PROMPT.encode()).hexdigest()
    schema_hash = hashlib.sha256(json.dumps(proposer.provider_schema(), sort_keys=True).encode()).hexdigest()
    assert prompt_hash == 'cdb4666643c2f11ce8bd3db105b25de6ea0e58574460a09926d80d587413a8f4'
    assert schema_hash == '6550457d8abe2577c448e2d83a3d2ea97e1770feab7f168050105608bbd7189b'
    assert proposer.PROMPT_VERSION == 'engineering_extraction_v1'
    assert proposer.SCHEMA_VERSION == 'engineering_proposal_v1'
    with pytest.raises(UnsupportedCapability):
        proposal_contract.ai_proposer('rectangular_cantilever', enabled=True)
