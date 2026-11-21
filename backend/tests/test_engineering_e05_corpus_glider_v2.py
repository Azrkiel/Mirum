"""Replay the sparse glider corpus through the qualification gate, proposer and scorer."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering import glider_extraction, glider_proposer, grounding
from engineering.family_gate import family_gate
from engineering.limits import AiCallBudget
from engineering.proposer import ProviderReply, ProviderSettings
from engineering.router import route


HERE = Path(__file__).resolve().parent
CORPORA = HERE / 'fixtures' / 'engineering' / 'proposals'
V1 = json.loads((CORPORA / 'glider_cem-v1.json').read_text(encoding='utf-8'))
V2 = json.loads((CORPORA / 'glider_cem-v2.json').read_text(encoding='utf-8'))
CASES = V2['cases']
FIELD_NAMES = {f'specification.requirements.{path}': name for name, path in glider_extraction.LEAVES.items()}


@pytest.fixture(scope='module')
def harness():
    script = HERE.parents[1] / 'scripts' / 'e05_live_eval.py'
    spec = importlib.util.spec_from_file_location('e05_live_eval_glider_v2', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProvider:
    available = True

    def __init__(self, replies):
        self.replies = list(replies)

    def settings(self):
        return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture-model',
                                temperature=0, max_tokens=1024)

    async def generate(self, prompt, schema, settings):
        return ProviderReply(text=self.replies.pop(0))


def test_glider_v2_corpus_shape_and_changes():
    assert set(V2) == set(V1) | {'changes_from_v1'}
    assert V2['version'] == 'e05-glider_cem-v2'
    assert V2['capability'] == V1['capability']
    assert V2['scope'] == V1['scope']
    old = {case['id']: case for case in V1['cases']}
    ids = [case['id'] for case in CASES]
    assert len(ids) == len(set(ids)) >= 10
    assert set(ids) == set(old)
    assert {case['kind'] for case in CASES} == {
        'complete', 'clarification', 'unsupported', 'out_of_family', 'adversarial',
    }
    assert sum(case['kind'] == 'complete' for case in CASES) >= 3

    changed = []
    for case in CASES:
        previous = old[case['id']]
        assert set(case) == set(previous)
        for key in ('prompt', 'kind', 'expected_route'):
            assert case[key] == previous[key], (case['id'], key)
        if any(case[key] != previous[key] for key in ('expected_state', 'expected_content')):
            changed.append(case['id'])

    logged = []
    for entry in V2['changes_from_v1']:
        case_id, reason = entry.split(': ', 1)
        assert reason.strip() and len(entry.splitlines()) == 1
        logged.append(case_id)
    assert sorted(logged) == sorted(changed)


@pytest.mark.parametrize('case', CASES, ids=[case['id'] for case in CASES])
def test_glider_v2_replay(case, harness):
    prompt = case['prompt']
    response = json.dumps(case['fixture_response'])
    sparse = glider_extraction.GliderSparseResponse.model_validate_json(response)
    fields = sparse.fields.model_dump(exclude_none=True)
    kinds = glider_extraction.field_kinds(fields.get('objective', 'distance_m'))
    assert grounding.ungrounded_fields(fields, prompt, kinds) == ()
    assert route(prompt).capability_id == case['expected_route']

    refusal = family_gate(prompt, 'glider_cem')
    if refusal is None:
        budget = AiCallBudget(daily_calls=1, concurrency=1)
        proposer = glider_proposer.AiProposer(budget, FakeProvider([response]))
        proposal = asyncio.run(proposer.propose(prompt))
        assert budget.used() == 1
    else:
        proposal = SimpleNamespace(state=refusal.state, payload={}, provenance=None, reason_id=refusal.reason_id)

    assert proposal.state == case['expected_state']
    origins = getattr(proposal.provenance, 'field_origins', ())
    actual = {o.field: harness.lookup(proposal.payload, o.field) for o in origins if o.origin == 'ai_proposed'}
    assert actual == case['expected_content']
    actual_fields = {FIELD_NAMES[path]: value for path, value in actual.items()}
    assert grounding.ungrounded_fields(actual_fields, prompt, kinds) == ()
    if case['kind'] == 'complete':
        score = harness.score_case(case, proposal)
        assert score['accurate_reviewable_draft'] is True
        assert score['field_mismatches'] == 0
        assert score['unstated_ai_values'] == 0
