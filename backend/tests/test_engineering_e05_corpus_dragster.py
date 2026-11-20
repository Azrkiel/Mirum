"""E05 dragster corpus replay with a fake interpretation for each prompt."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from engineering import proposal_contract as pc
from engineering.capabilities import DRAGSTER
from engineering.dragster_proposer import AiProposer
from engineering.limits import AiCallBudget
from engineering.router import route


CORPUS_PATH = Path(__file__).parent / 'fixtures' / 'engineering' / 'proposals' / 'co2_dragster-v1.json'
CORPUS = json.loads(CORPUS_PATH.read_text(encoding='utf-8'))
CASES = CORPUS['cases']


def test_dragster_corpus_shape():
    assert CORPUS['version'] == 'e05-co2_dragster-v1'
    assert CORPUS['capability'] == pc.reference(DRAGSTER)
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
def test_dragster_corpus_case(case: dict):
    prompt = case['prompt']
    assert route(prompt).capability_id == case['expected_route']
    budget = AiCallBudget(daily_calls=10, concurrency=1)
    calls = []

    async def interpret(prompt: str, base_spec: dict | None) -> dict:
        calls.append((prompt, base_spec))
        assert budget.used() == 1
        return case['fixture_response']

    proposal = asyncio.run(AiProposer(budget, interpret=interpret).propose(prompt))
    assert calls == [(prompt, None)]
    assert budget.used() == 1
    assert proposal.state == case['expected_state']
    assert proposal.payload['capability'] == pc.reference(DRAGSTER)
    assert pc.conform('co2_dragster', proposal) is proposal

    if proposal.state == 'proposed':
        specification = proposal.payload['specification']
        origins = {origin.field: origin for origin in proposal.provenance.field_origins}
        expected_content = case['expected_content']
        assert expected_content
        assert set(origins) == set(DRAGSTER.review_fields(specification))
        assert {path for path, origin in origins.items() if origin.origin == 'ai_proposed'} == set(expected_content)
        for path, expected in expected_content.items():
            root, *parts = path.split('.')
            assert root == 'specification' and parts, path
            value = specification
            for part in parts:
                value = value[part]
            assert value == expected, path
            assert pc.UNVERIFIED in origins[path].reference, path
        for path in origins.keys() - expected_content.keys():
            assert origins[path].origin == 'policy', path
            assert origins[path].reference == f"preset:{specification['style']}", path
