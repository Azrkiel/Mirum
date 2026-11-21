"""E05 glider corpus v1: shape, pin and route checks (the proposer replay moves to the v2 corpus)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering import proposal_contract as pc
from engineering.capabilities import GLIDER
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
    # D5: the v1 fixture_response (full cem.extraction reply) no longer matches the sparse provider
    # proposer, so the proposer-driving replay is dropped here; a v2 corpus test replaces it.
    assert route(case['prompt']).capability_id == case['expected_route']
    assert case['expected_state'] in {'proposed', 'clarification_required', 'unsupported'}
    if case['expected_state'] == 'proposed':
        assert case['expected_content']
