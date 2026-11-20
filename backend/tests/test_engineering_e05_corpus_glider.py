"""E05 glider corpus replay with an offline fake extractor."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from cem.glider import calibration
from engineering import proposal_contract as pc
from engineering.capabilities import GLIDER
from engineering.glider_proposer import AiProposer
from engineering.limits import AiCallBudget
from engineering.router import route


CORPUS_PATH = Path(__file__).parent / 'fixtures' / 'engineering' / 'proposals' / 'glider_cem-v1.json'
CORPUS = json.loads(CORPUS_PATH.read_text(encoding='utf-8'))
CASES = CORPUS['cases']


def test_glider_corpus_shape():
    assert CORPUS['version'] == 'e05-glider_cem-v1'
    assert CORPUS['capability'] == pc.reference(GLIDER)
    assert CORPUS['scope'] == 'offline fake-provider corpus; live use only inside an owner-approved qualification job'
    assert len(CASES) >= 10
    assert len({case['id'] for case in CASES}) == len(CASES)
    assert {case['kind'] for case in CASES} == {
        'complete', 'clarification', 'unsupported', 'out_of_family', 'adversarial',
    }
    assert sum(case['kind'] == 'complete' for case in CASES) >= 3
    assert {case['expected_route'] for case in CASES if case['kind'] == 'out_of_family'} == {
        'co2_dragster', 'regen_chamber',
    }
    for case in CASES:
        assert 0 < len(case['prompt']) <= pc.MAX_PROMPT, case['id']
        if case['kind'] == 'complete':
            assert case['expected_state'] == 'proposed', case['id']
        elif case['kind'] == 'out_of_family':
            assert case['expected_state'] == 'unsupported', case['id']
        elif case['kind'] == 'adversarial':
            assert case['expected_state'] in {'unsupported', 'clarification_required'}, case['id']


@pytest.mark.parametrize('case', CASES, ids=[case['id'] for case in CASES])
def test_glider_corpus_case(case: dict):
    prompt = case['prompt']
    expected_route = case['expected_route']
    assert route(prompt).capability_id == expected_route

    budget = AiCallBudget(daily_calls=10, concurrency=1)
    calls: list[str] = []

    async def extract(user_prompt: str) -> dict:
        calls.append(user_prompt)
        assert budget.used() == 1
        return deepcopy(case['fixture_response'])

    proposal = asyncio.run(AiProposer(budget, extract=extract).propose(prompt))
    assert calls == ([prompt] if expected_route in (None, GLIDER.id) else [])
    assert budget.used() == len(calls)
    assert proposal.state == case['expected_state']
    assert proposal.payload['capability'] == pc.reference(GLIDER)
    assert pc.conform(GLIDER.id, proposal) is proposal

    if proposal.state == 'proposed':
        specification = proposal.payload['specification']
        origins = {origin.field: origin for origin in proposal.provenance.field_origins}
        expected_content = case['expected_content']
        assert expected_content
        assert set(origins) == set(GLIDER.review_fields(specification))
        assert {path for path, origin in origins.items() if origin.origin == 'ai_proposed'} == set(expected_content)
        for path, expected in expected_content.items():
            root, *parts = path.split('.')
            assert root == 'specification' and parts, path
            value = specification
            for part in parts:
                value = value[part]
            assert value == expected, path
            assert pc.UNVERIFIED in origins[path].reference, path
        assert specification['calibration_version'] == calibration.version(calibration.initial())
        assert origins['specification.calibration_version'].origin == 'policy'
        for path in origins.keys() - expected_content.keys():
            assert origins[path].origin == 'policy', path
