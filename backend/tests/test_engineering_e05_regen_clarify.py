"""E05 regen proposal grounding, clarification, and fixed reason IDs."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from engineering import proposal_contract as pc
from engineering import regen_presets, regen_proposer
from engineering.capabilities import GENERATOR_SPEC_PREFIX, REGEN
from engineering.grounding import NumberField, ungrounded_fields
from engineering.limits import AiCallBudget
from engineering.proposer import ProviderReply, ProviderSettings


HERE = Path(__file__).resolve().parent
CORPUS = HERE / 'fixtures' / 'engineering' / 'proposals' / 'regen_chamber-v1.json'
CASES = {case['id']: case for case in json.loads(CORPUS.read_text(encoding='utf-8'))['cases']}
REFERENCE = regen_presets.EXAMPLES['reference']
PROMPT = CASES['R01']['prompt']


def load_reason_ids():
    script = HERE.parents[1] / 'scripts' / 'e05_live_eval.py'
    spec = importlib.util.spec_from_file_location('e05_live_eval', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.REASON_IDS


REASON_IDS = load_reason_ids()


class FakeProvider:
    def __init__(self, replies=(), available=True, error=None, settings_error=None):
        self.replies = list(replies)
        self.available = available
        self.error = error
        self.settings_error = settings_error
        self.calls = 0

    def settings(self):
        if self.settings_error is not None:
            raise self.settings_error
        return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture-model',
                                temperature=0, max_tokens=1024)

    async def generate(self, prompt, schema, settings):
        self.calls += 1
        if self.error is not None:
            raise self.error
        # Bypass reply validation to exercise the proposer's non-string guard.
        return ProviderReply.model_construct(text=self.replies.pop(0))


def reply(fields=None, **over):
    body = dict(state='proposed', out_of_family=False, preset=None, fields=fields or {},
                questions=[], unsupported_features=[])
    body.update(over)
    return json.dumps(body)


def run(provider, prompt=PROMPT, *, base_spec=None, daily_calls=5):
    budget = AiCallBudget(daily_calls=daily_calls, concurrency=1)
    proposal = asyncio.run(regen_proposer.AiProposer(budget, provider).propose(prompt, base_spec))
    return pc.conform(REGEN.id, proposal)


def run_fixture(case):
    fixture = case['fixture_response']
    responses = fixture if isinstance(fixture, list) else [fixture]
    provider = FakeProvider([text if isinstance(text, str) else json.dumps(text) for text in responses])
    return run(provider, case['prompt'])


def origins(proposal):
    return {entry.field.removeprefix(GENERATOR_SPEC_PREFIX): entry.origin
            for entry in proposal.provenance.field_origins}


def ai_values(proposal):
    return {key: proposal.payload['specification'][key]
            for key, origin in origins(proposal).items() if origin == 'ai_proposed'}


def assert_ungrounded_issue(proposal, key):
    assert (f'specification.{key}', 'clarification_required', regen_proposer.UNGROUNDED_QUESTION) in [
        (issue.field, issue.state, issue.reason) for issue in proposal.issues
    ]


def assert_reason(proposal, reason_id):
    assert proposal.reason_id == reason_id
    assert proposal.reason_id in REASON_IDS


def test_r05_keeps_the_stated_throat_and_drops_the_unstated_chamber_radius():
    provider = FakeProvider([reply(dict(throat_radius_mm=40, chamber_radius_mm=38), preset='reference')])
    proposal = run(provider, CASES['R05']['prompt'])

    assert proposal.state == 'clarification_required'
    assert proposal.payload['specification']['chamber_radius_mm'] == 35.0
    assert origins(proposal)['chamber_radius_mm'] == 'policy'
    assert ai_values(proposal) == {'throat_radius_mm': 40.0}
    assert_ungrounded_issue(proposal, 'chamber_radius_mm')


def test_r13_keeps_the_stated_count_and_drops_the_unstated_channel_radius():
    provider = FakeProvider([reply(dict(channel_count=300, channel_radius_mm=0.8))])
    proposal = run(provider, CASES['R13']['prompt'])

    assert proposal.state == 'clarification_required'
    assert proposal.payload['specification']['channel_radius_mm'] == REFERENCE['channel_radius_mm']
    assert origins(proposal)['channel_radius_mm'] == 'policy'
    assert ai_values(proposal) == {'channel_count': 300}
    assert_ungrounded_issue(proposal, 'channel_radius_mm')


def test_r01_would_be_proposed_but_an_unstated_wall_thickness_demotes_it():
    provider = FakeProvider([reply(dict(chamber_length_mm=44, end_margin_mm=9, wall_thickness_mm=6.5))])
    proposal = run(provider)

    assert proposal.state == 'clarification_required'
    assert_reason(proposal, 'clarification.ungrounded_value')
    assert proposal.payload['specification']['wall_thickness_mm'] == 7.0
    assert origins(proposal)['wall_thickness_mm'] == 'policy'
    assert ai_values(proposal) == {'chamber_length_mm': 44.0, 'end_margin_mm': 9.0}
    assert_ungrounded_issue(proposal, 'wall_thickness_mm')


@pytest.mark.parametrize('state', ['proposed', 'clarification_required'])
def test_model_questions_are_kept_and_ungrounded_values_do_not_override_the_base(state):
    questions = ['Which wall thickness should be used?', 'Which chamber radius should be used?']
    provider = FakeProvider([reply(dict(chamber_length_mm=44, wall_thickness_mm=6.5),
                                   state=state, questions=questions)])
    proposal = run(provider, base_spec={'wall_thickness_mm': 7.5})

    assert proposal.state == 'clarification_required'
    assert_reason(proposal, 'clarification.model_question')
    assert proposal.payload['specification']['wall_thickness_mm'] == 7.5
    assert origins(proposal)['wall_thickness_mm'] == 'user_supplied'
    assert ai_values(proposal) == {'chamber_length_mm': 44.0}
    assert [(issue.state, issue.reason) for issue in proposal.issues if issue.field == 'request'] == [
        ('clarification_required', question) for question in questions
    ]
    assert_ungrounded_issue(proposal, 'wall_thickness_mm')


def test_grounded_invalid_specification_gets_the_invalid_value_reason():
    proposal = run_fixture(CASES['R05'])

    assert proposal.state == 'clarification_required'
    assert_reason(proposal, 'clarification.invalid_value')
    assert ai_values(proposal) == {'throat_radius_mm': 40.0}


def test_all_non_null_regen_fields_are_checked_with_their_canonical_units():
    fields = {key: value for key, value in REFERENCE.items() if key.endswith('_mm')}
    fields.update(chamber_length_mm=44, channel_count=30, channel_twist_rad_per_mm=0.01)
    # Treating count or twist as a length would incorrectly accept both values.
    prompt = ('Draft a regeneratively cooled rocket chamber with a chamber length of 0.044 m, '
              '0.03 coolant channels, and a channel twist of 0.00001.')
    proposal = run(FakeProvider([reply(fields)]), prompt)

    assert proposal.state == 'clarification_required'
    assert_reason(proposal, 'clarification.ungrounded_value')
    assert ai_values(proposal) == {'chamber_length_mm': 44.0}
    specification = proposal.payload['specification']
    field_origins = origins(proposal)
    for key in sorted(fields.keys() - {'chamber_length_mm'}):
        assert specification[key] == REFERENCE[key]
        assert field_origins[key] == 'policy'
        assert_ungrounded_issue(proposal, key)


@pytest.mark.parametrize('case_id', ['R01', 'R02', 'R03'])
def test_stated_fixture_values_stay_proposed_with_exact_ai_origins(case_id):
    case = CASES[case_id]
    proposal = run_fixture(case)

    assert proposal.state == 'proposed'
    assert proposal.reason_id == ''
    assert ai_values(proposal) == {field.removeprefix(GENERATOR_SPEC_PREFIX): value
                                   for field, value in case['expected_content'].items()}


def test_reason_id_defaults_to_empty_for_existing_constructor_calls():
    proposal = run_fixture(CASES['R01'])
    rebuilt = regen_proposer.RegenProposal(proposal.state, proposal.payload, proposal.issues,
                                          proposal.provenance, proposal.reasons)
    assert rebuilt.reason_id == ''


@pytest.mark.parametrize('case_id', CASES)
def test_corpus_ai_origins_only_name_values_grounded_in_the_prompt(case_id):
    case = CASES[case_id]
    values = ai_values(run_fixture(case))
    kinds = {key: NumberField('length', 'mm') for key in values if key.endswith('_mm')}
    kinds.update(channel_count=NumberField('dimensionless', '1'),
                 channel_twist_rad_per_mm=NumberField('dimensionless', '1'))

    assert ungrounded_fields(values, case['prompt'], kinds) == ()


@pytest.mark.parametrize(('provider_options', 'prompt', 'daily_calls', 'calls', 'reason_id'), [
    pytest.param({}, CASES['R08']['prompt'], 5, 0, 'unsupported.family', id='router'),
    pytest.param({'replies': [reply(out_of_family=True)]}, PROMPT, 5, 1,
                 'unsupported.family', id='model-family'),
    pytest.param({'replies': [reply(unsupported_features=['STEP export'])]}, PROMPT, 5, 1,
                 'unsupported.feature', id='model-features'),
    pytest.param({'replies': [reply(state='unsupported')]}, PROMPT, 5, 1,
                 'unsupported.feature', id='model-state'),
    pytest.param({'replies': [reply(dict(voxel_size_mm=0.2))]}, CASES['R07']['prompt'], 5, 1,
                 'unsupported.value', id='refused-value'),
    pytest.param({}, PROMPT, 0, 0, 'unavailable.budget', id='budget'),
    pytest.param({'error': asyncio.TimeoutError()}, PROMPT, 5, 1,
                 'unavailable.timeout', id='timeout'),
    pytest.param({'error': RuntimeError('provider failed')}, PROMPT, 5, 1,
                 'unavailable.provider_error', id='provider-error'),
    pytest.param({'available': False}, PROMPT, 5, 0,
                 'unavailable.provider_unconfigured', id='provider-disabled'),
    pytest.param({'settings_error': RuntimeError('settings failed')}, PROMPT, 5, 0,
                 'unavailable.provider_unconfigured', id='settings-error'),
    pytest.param({'replies': [None]}, PROMPT, 5, 1, 'unavailable.oversize_reply', id='non-string'),
    pytest.param({'replies': [' ' * (REGEN.resources.structured_response_bytes + 1)]}, PROMPT, 5, 1,
                 'unavailable.oversize_reply', id='oversize'),
    pytest.param({'replies': ['not json', 'still not json']}, PROMPT, 5, 2,
                 'unavailable.nonconforming', id='repair-exhausted'),
])
def test_unsupported_and_unavailable_paths_have_fixed_reason_ids(provider_options, prompt, daily_calls, calls,
                                                                reason_id):
    provider = FakeProvider(**provider_options)
    proposal = run(provider, prompt, daily_calls=daily_calls)

    assert proposal.state == reason_id.split('.')[0]
    assert provider.calls == calls
    assert_reason(proposal, reason_id)
