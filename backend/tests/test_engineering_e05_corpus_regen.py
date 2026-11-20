"""E05 regen chamber corpus replay with an offline fake provider."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from engineering import proposal_contract as pc
from engineering.capabilities import REGEN
from engineering.limits import AiCallBudget
from engineering.proposer import ProviderReply, ProviderSettings
from engineering.regen_proposer import AiProposer
from engineering.router import route


CORPUS_PATH = Path(__file__).parent / 'fixtures' / 'engineering' / 'proposals' / 'regen_chamber-v1.json'
CORPUS = json.loads(CORPUS_PATH.read_text(encoding='utf-8'))
CASES = CORPUS['cases']


class FakeProvider:
    available = True

    def __init__(self, replies: list[str], budget: AiCallBudget):
        self.replies = list(replies)
        self.budget = budget
        self.calls = 0

    def settings(self) -> ProviderSettings:
        return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture-model',
                                temperature=0, max_tokens=1024)

    async def generate(self, prompt, schema, settings) -> ProviderReply:
        self.calls += 1
        assert self.budget.used() == self.calls
        return ProviderReply(text=self.replies.pop(0))


def reply(response: dict | str) -> str:
    return response if isinstance(response, str) else json.dumps(response)


def test_regen_corpus_shape():
    assert CORPUS['version'] == 'e05-regen_chamber-v1'
    assert CORPUS['capability'] == pc.reference(REGEN)
    assert CORPUS['scope'] == 'offline fake-provider corpus; live use only inside an owner-approved qualification job'
    assert len(CASES) >= 10
    assert len({case['id'] for case in CASES}) == len(CASES)
    assert {case['kind'] for case in CASES} == {
        'complete', 'clarification', 'unsupported', 'out_of_family', 'adversarial',
    }
    assert sum(case['kind'] == 'complete' for case in CASES) >= 3
    for case in CASES:
        assert 0 < len(case['prompt']) <= 2000, case['id']
        if case['kind'] == 'out_of_family':
            assert case['expected_state'] == 'unsupported', case['id']
        elif case['kind'] == 'adversarial':
            assert case['expected_state'] in {'unsupported', 'clarification_required'}, case['id']


@pytest.mark.parametrize('case', CASES, ids=[case['id'] for case in CASES])
def test_regen_corpus_case(case: dict):
    prompt = case['prompt']
    expected_route = case['expected_route']
    assert route(prompt).capability_id == expected_route

    # A list provides successive replies so a case can exercise the single repair call.
    fixture = case['fixture_response']
    responses = fixture if isinstance(fixture, list) else [fixture]
    expected_calls = len(responses) if expected_route in (None, REGEN.id) else 0
    budget = AiCallBudget(daily_calls=10, concurrency=1)
    provider = FakeProvider([reply(response) for response in responses], budget)

    proposal = asyncio.run(AiProposer(budget, provider=provider).propose(prompt))
    assert provider.calls == expected_calls
    assert budget.used() == provider.calls
    assert proposal.state == case['expected_state']
    assert proposal.payload['capability'] == pc.reference(REGEN)
    assert pc.conform(REGEN.id, proposal) is proposal

    serialized = json.dumps([proposal.payload, [issue.model_dump(mode='json') for issue in proposal.issues],
                             proposal.provenance.model_dump(mode='json'), proposal.reasons])
    for response in responses:
        if isinstance(response, str):
            assert json.dumps(response)[1:-1] not in serialized

    if proposal.state == 'proposed':
        specification = proposal.payload['specification']
        origins = {origin.field: origin for origin in proposal.provenance.field_origins}
        expected_content = case['expected_content']
        assert expected_content
        assert set(origins) == set(REGEN.review_fields(specification))
        assert {path for path, origin in origins.items() if origin.origin == 'ai_proposed'} == set(expected_content)
        for path, expected in expected_content.items():
            root, field = path.split('.')
            assert root == 'specification', path
            assert specification[field] == expected, path
            assert pc.UNVERIFIED in origins[path].reference, path
        preset = responses[-1]['preset'] or 'reference'
        for path in origins.keys() - expected_content.keys():
            assert origins[path].origin == 'policy', path
            assert origins[path].reference == f'preset:{preset}', path
