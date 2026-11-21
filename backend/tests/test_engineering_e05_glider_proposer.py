"""E05 glider_cem AI proposer (sparse extraction, D5): offline, fake provider, no network, no sleeps."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from cem.glider import calibration
from cem.glider.requirements import GliderMissionRequirements
from engineering import glider_extraction
from engineering import proposal_contract as pc
from engineering.capabilities import GLIDER
from engineering.glider_proposer import (AiProposer, CALIBRATION_QUESTION, FAMILIES_QUESTION, NOT_A_GLIDER,
                                         PROMPT_VERSION, UNAVAILABLE, UNGROUNDED_QUESTION)
from engineering.limits import AiCallBudget
from engineering.proposer import ProviderReply, ProviderSettings

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
CORPUS = HERE / 'fixtures' / 'engineering' / 'proposals' / 'glider_cem-v1.json'
CASES = {case['id']: case for case in json.loads(CORPUS.read_text(encoding='utf-8'))['cases']}
PROMPT = 'Design a glider launched by drop that maximizes distance, using conventional and t_tail families.'
SECRET = 'SECRET-EXC-TEXT-9137'
DEFAULTS = GliderMissionRequirements(launch=dict(method='drop')).model_dump(mode='json')


def load_reason_ids():
    script = HERE.parents[1] / 'scripts' / 'e05_live_eval.py'
    spec = importlib.util.spec_from_file_location('e05_live_eval_glider', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.REASON_IDS


REASON_IDS = load_reason_ids()


class FakeProvider:
    def __init__(self, replies=(), available=True, error=None, settings_error=None):
        self.replies, self.available, self.error, self.settings_error = list(replies), available, error, settings_error
        self.calls, self.prompts = 0, []

    def settings(self):
        if self.settings_error is not None:
            raise self.settings_error
        return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture-model',
                                temperature=0, max_tokens=1024)

    async def generate(self, prompt, schema, settings):
        self.calls += 1
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        # Bypass reply validation to exercise the proposer's non-string guard.
        return ProviderReply.model_construct(text=self.replies.pop(0))


def reply(fields=None, **over):
    body = dict(out_of_family=False, fields=fields or {}, questions=[], unsupported_features=[])
    body.update(over)
    return json.dumps(body)


def run(provider, prompt=PROMPT, budget=None, daily_calls=5):
    budget = budget or AiCallBudget(daily_calls=daily_calls, concurrency=1)
    proposal = asyncio.run(AiProposer(budget, provider).propose(prompt))
    pc.conform('glider_cem', proposal)
    return proposal, budget


def origins(proposal):
    return {o.field.removeprefix('specification.requirements.'): o for o in proposal.provenance.field_origins}


def walk(tree, path):
    for part in path.split('.'):
        tree = tree[part]
    return tree


def assert_sparse_provenance(proposal, stated):
    """Every ai_proposed leaf is one the model stated; every other leaf is policy with its default."""
    spec = proposal.payload['specification']['requirements']
    got = origins(proposal)
    ai = {path for path, o in got.items() if o.origin == 'ai_proposed'}
    assert ai == {glider_extraction.LEAVES[k] for k in stated} | ({'constraints.payload'} & ai)
    for path, o in got.items():
        if path in ai:
            assert pc.UNVERIFIED in o.reference and PROMPT_VERSION in o.reference and 'fixture-model' in o.reference
        elif path == 'specification.calibration_version':
            continue
        elif path != 'calibration_version':
            assert o.origin == 'policy', path
            assert o.reference == 'GliderMissionRequirements default', path
            assert walk(spec, path) == walk(DEFAULTS, path), path


G01 = dict(objective='distance_m', launch_method='drop', launch_height_m=14, families=['conventional', 't_tail'],
           max_span_m=1.2)
G02 = dict(objective='duration_s', launch_method='hand_toss', launch_speed_m_s=6, max_length_m=0.7,
           families=['v_tail'])
G03 = dict(objective='distance_m', launch_method='catapult', families=['conventional', 'v_tail'],
           payload_mass_kg=0.06, payload_diameter_m=0.025, payload_length_m=0.08)


@pytest.mark.parametrize('case_id,fields', [('G01', G01), ('G02', G02), ('G03', G03)])
def test_complete_corpus_prompts_are_proposed_with_sparse_provenance(case_id, fields):
    provider = FakeProvider([reply(fields)])
    proposal, budget = run(provider, CASES[case_id]['prompt'])
    assert proposal.state == 'proposed' and not proposal.issues and not proposal.reasons and proposal.reason_id == ''
    spec = proposal.payload['specification']
    assert proposal.payload['capability'] == pc.reference(GLIDER)
    assert spec['calibration_version'] == calibration.version(calibration.initial())
    assert proposal.provenance.input_mode == 'ai'
    assert len(origins(proposal)) == len(GLIDER.review_fields(spec))
    for path, expected in CASES[case_id]['expected_content'].items():
        assert walk(spec['requirements'], path.removeprefix('specification.requirements.')) == expected, path
    assert_sparse_provenance(proposal, fields)
    cal = origins(proposal)['specification.calibration_version'] if 'specification.calibration_version' in origins(proposal) else None
    assert cal is None or cal.origin == 'policy'
    assert provider.calls == 1 and budget.used() == 1
    assert json.loads(provider.prompts[0].split('data:\n', 1)[1]) == CASES[case_id]['prompt']


def test_calibration_version_is_policy_with_the_shipped_reference():
    proposal, _ = run(FakeProvider([reply(G01)]), CASES['G01']['prompt'])
    cal = {o.field: o for o in proposal.provenance.field_origins}['specification.calibration_version']
    assert cal.origin == 'policy' and 'shipped default' in cal.reference


def test_unstated_value_is_dropped_and_the_default_kept():
    fields = dict(G01, max_mass_kg=0.3)  # G01 never states a mass
    proposal, _ = run(FakeProvider([reply(fields)]), CASES['G01']['prompt'])
    assert proposal.state == 'clarification_required' and proposal.reason_id == 'clarification.ungrounded_value'
    spec = proposal.payload['specification']['requirements']
    assert spec['constraints']['max_mass_kg'] == DEFAULTS['constraints']['max_mass_kg']
    got = origins(proposal)['constraints.max_mass_kg']
    assert got.origin == 'policy' and got.reference == 'GliderMissionRequirements default'
    assert ('specification.requirements.constraints.max_mass_kg', 'clarification_required',
            UNGROUNDED_QUESTION) in [(i.field, i.state, i.reason) for i in proposal.issues]
    assert_sparse_provenance(proposal, G01)


def test_unstated_choice_and_derived_number_are_dropped():
    fields = dict(G01, wing_shape='multi_station', max_span_m=2.4 / 2 + 0.1)
    proposal, _ = run(FakeProvider([reply(fields)]), CASES['G01']['prompt'])
    spec = proposal.payload['specification']['requirements']
    assert spec['discovery']['wing_shape'] == 'baseline' and spec['constraints']['max_span_m'] == DEFAULTS['constraints']['max_span_m']
    fields_flagged = {i.field for i in proposal.issues}
    assert {'specification.requirements.discovery.wing_shape',
            'specification.requirements.constraints.max_span_m'} <= fields_flagged


def test_missing_launch_method_is_a_missing_field_clarification():
    prompt = CASES['G05']['prompt'].replace('drop-launched ', '')
    proposal, _ = run(FakeProvider([reply(dict(objective='distance_m', families=['conventional']))]), prompt)
    assert proposal.state == 'clarification_required' and proposal.reason_id == 'clarification.missing_field'
    assert 'specification.requirements.launch.method' in [i.field for i in proposal.issues]
    assert 'method' not in proposal.payload['specification']['requirements']['launch']
    assert 'launch.method' not in origins(proposal)


def test_unstated_launch_method_is_dropped_and_missing():
    proposal, _ = run(FakeProvider([reply(dict(launch_method='drop', families=['conventional']))],),
                      'Design a glider to maximize distance using the conventional family.')
    assert proposal.state == 'clarification_required' and proposal.reason_id == 'clarification.missing_field'
    fields = [i.field for i in proposal.issues]
    assert fields.count('specification.requirements.launch.method') == 2  # missing + ungrounded


def test_families_all_is_a_clarification_with_a_partial_spec():
    proposal, _ = run(FakeProvider([reply(dict(launch_method='drop'))]), 'Design a drop-launched glider.')
    assert proposal.state == 'clarification_required' and proposal.reason_id == 'clarification.families'
    assert [i.field for i in proposal.issues] == ['specification.requirements.discovery.families']
    assert proposal.reasons == (FAMILIES_QUESTION,)
    spec = proposal.payload['specification']
    assert spec['requirements']['discovery']['families'] == 'all'
    assert spec['calibration_version'] == calibration.version(calibration.initial())


def test_calibration_mention_needs_clarification():
    prompt = PROMPT + ' Use the latest flight test calibration.'
    proposal, _ = run(FakeProvider([reply(dict(G01_FAMILIES, launch_method='drop'))]), prompt)
    assert proposal.state == 'clarification_required' and proposal.reason_id == 'clarification.calibration'
    assert [i.field for i in proposal.issues] == ['specification.calibration_version']
    assert proposal.reasons == (CALIBRATION_QUESTION,)
    assert 'calibration_version' not in proposal.payload['specification']
    assert 'specification.calibration_version' not in {o.field for o in proposal.provenance.field_origins}


G01_FAMILIES = dict(families=['conventional', 't_tail'])


def test_unreadable_calibration_needs_clarification(monkeypatch):
    def boom():
        raise OSError(SECRET)
    monkeypatch.setattr(calibration, 'initial', boom)
    proposal, _ = run(FakeProvider([reply(dict(G01_FAMILIES, launch_method='drop'))]))
    assert proposal.state == 'clarification_required'
    assert [i.field for i in proposal.issues] == ['specification.calibration_version']
    assert SECRET not in json.dumps(proposal.reasons)


def test_model_questions_come_first_with_their_reason_id():
    provider = FakeProvider([reply(dict(G01_FAMILIES, launch_method='drop'), questions=['Which span?', 'x' * 240])])
    proposal, _ = run(provider)
    assert proposal.state == 'clarification_required' and proposal.reason_id == 'clarification.model_question'
    assert [i.field for i in proposal.issues] == ['request', 'request']
    assert len(proposal.issues[1].reason) == 240


def test_questions_only_reply_has_no_specification():
    proposal, _ = run(FakeProvider([reply(questions=['Which launch?'])]))
    assert proposal.state == 'clarification_required' and proposal.reason_id == 'clarification.model_question'
    assert 'specification' not in proposal.payload
    assert proposal.provenance.input_mode == 'form' and not proposal.provenance.field_origins


def test_invalid_stated_value_is_an_invalid_value_clarification():
    prompt = CASES['G14']['prompt']
    proposal, _ = run(FakeProvider([reply(dict(launch_method='hand_toss', families=['conventional'], max_span_m=3.0))]),
                      prompt)
    assert proposal.state == 'clarification_required' and proposal.reason_id == 'clarification.invalid_value'
    assert [i.field for i in proposal.issues] == ['specification.requirements.constraints.max_span_m']
    assert '3.0' not in json.dumps(proposal.reasons)


def test_thermal_corpus_prompt_is_unsupported_by_feature_or_value():
    g07 = CASES['G07']['prompt']
    listed, _ = run(FakeProvider([reply(unsupported_features=['Thermal soaring'])]), g07)
    assert listed.state == 'unsupported' and listed.reason_id == 'unsupported.feature'
    assert 'specification' not in listed.payload
    stated, _ = run(FakeProvider([reply(dict(environment_kind='thermal', launch_method='drop',
                                             families=['conventional']))]), g07)
    assert stated.state == 'unsupported' and stated.reason_id == 'unsupported.value'
    assert [i.field for i in stated.issues] == ['specification.requirements.environment']
    assert stated.reasons == tuple(i.reason for i in stated.issues)


def test_canard_corpus_prompt_is_unsupported_by_value():
    proposal, _ = run(FakeProvider([reply(dict(launch_method='discus', families=['canard']))]),
                      CASES['G08']['prompt'])
    assert proposal.state == 'unsupported' and proposal.reason_id == 'unsupported.value'
    assert 'specification' not in proposal.payload
    assert [i.field for i in proposal.issues] == ['specification.requirements.discovery.families']


def test_out_of_family_reply_is_unsupported_family():
    proposal, _ = run(FakeProvider([reply(out_of_family=True)]), 'Design a ceramic coffee mug with a wide handle.')
    assert proposal.state == 'unsupported' and proposal.reason_id == 'unsupported.family'
    assert proposal.reasons == (NOT_A_GLIDER,)


UNAVAILABLE_CASES = [
    ('provider_unconfigured', dict(available=False), 0, 'unavailable.provider_unconfigured'),
    ('settings_error', dict(settings_error=RuntimeError(SECRET)), 0, 'unavailable.provider_unconfigured'),
    ('provider_error', dict(error=RuntimeError(SECRET)), 1, 'unavailable.provider_error'),
    ('oversize', dict(replies=['x' * (GLIDER.resources.structured_response_bytes + 1)]), 1,
     'unavailable.oversize_reply'),
    ('non_string', dict(replies=[None]), 1, 'unavailable.oversize_reply'),
    ('nonconforming', dict(replies=['not json ' + SECRET, '{"nope": 1}']), 2, 'unavailable.nonconforming'),
]


@pytest.mark.parametrize('name,kwargs,units,reason_id', UNAVAILABLE_CASES, ids=[c[0] for c in UNAVAILABLE_CASES])
def test_unavailable_reason_ids_and_one_unit_per_call(name, kwargs, units, reason_id):
    provider = FakeProvider(**kwargs)
    proposal, budget = run(provider)
    assert proposal.state == 'unavailable' and proposal.reasons == UNAVAILABLE
    assert proposal.reason_id == reason_id and reason_id in REASON_IDS
    assert budget.used() == units == provider.calls and budget._inflight == 0
    blob = json.dumps([proposal.payload, proposal.reasons, [i.reason for i in proposal.issues],
                       proposal.provenance.model_dump(mode='json')], default=str)
    assert SECRET not in blob and PROMPT not in blob


def test_zero_budget_is_unavailable_budget_without_a_call():
    provider = FakeProvider([reply(G01)])
    proposal, budget = run(provider, daily_calls=0)
    assert proposal.state == 'unavailable' and proposal.reason_id == 'unavailable.budget'
    assert provider.calls == 0 and budget.used() == 0


def test_timeout_is_unavailable_timeout(monkeypatch):
    monkeypatch.setattr(glider_extraction, 'CALL_SECONDS', 0.01)

    class Slow(FakeProvider):
        async def generate(self, prompt, schema, settings):
            self.calls += 1
            await asyncio.sleep(5)

    provider = Slow()
    proposal, budget = run(provider)
    assert proposal.state == 'unavailable' and proposal.reason_id == 'unavailable.timeout'
    assert budget.used() == 1 and budget._inflight == 0


def test_malformed_then_valid_repairs_with_two_units():
    provider = FakeProvider(['not json', reply(dict(G01_FAMILIES, launch_method='drop'))])
    proposal, budget = run(provider)
    assert proposal.state == 'proposed' and provider.calls == 2 and budget.used() == 2
    assert provider.prompts[1].startswith(provider.prompts[0]) and 'Repair' in provider.prompts[1]


@pytest.mark.parametrize('bad', ['{"out_of_family": false, "out_of_family": false}',
                                 '{"fields": {"max_span_m": NaN}}'])
def test_duplicate_keys_and_nonfinite_rejected(bad):
    proposal, _ = run(FakeProvider([bad, bad]))
    assert proposal.state == 'unavailable' and proposal.reason_id == 'unavailable.nonconforming'


def test_every_non_proposed_return_has_a_known_reason_id():
    for provider in (FakeProvider([reply(out_of_family=True)]), FakeProvider([reply()]),
                     FakeProvider(error=RuntimeError(SECRET))):
        proposal, _ = run(provider)
        assert proposal.state != 'proposed' and proposal.reason_id in REASON_IDS


def test_router_precheck_blocks_other_capability():
    provider = FakeProvider([reply()])
    proposal, budget = run(provider, prompt='Design a CO2 dragster car with a teardrop body for a 3 inch track.')
    assert proposal.state == 'unsupported' and proposal.reasons == (NOT_A_GLIDER,)
    assert proposal.reason_id == 'unsupported.family'
    assert provider.calls == 0 and budget.used() == 0


@pytest.mark.parametrize('prompt', ['', '   ', 'x' * 2001, None, 5])
def test_bad_prompt_raises(prompt):
    provider = FakeProvider([reply()])
    with pytest.raises(ValueError):
        asyncio.run(AiProposer(AiCallBudget(daily_calls=5, concurrency=1), provider).propose(prompt))
    assert provider.calls == 0


def test_provider_schema_is_nullable_unions_only():
    schema = glider_extraction.provider_schema()
    assert schema['type_'] == 'OBJECT'
    fields = schema['properties']['fields']['properties']
    assert set(fields) == set(glider_extraction.LEAVES) and all(f['nullable'] for f in fields.values())


def test_default_provider_is_the_engineering_provider():
    from engineering.proposer import GeminiProposalProvider
    assert isinstance(AiProposer(AiCallBudget(daily_calls=1, concurrency=1)).provider, GeminiProposalProvider)


class EnvSpy(dict):
    def __init__(self, base):
        super().__init__(base)
        self.read = []

    def get(self, key, default=None):
        self.read.append(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.read.append(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self.read.append(key)
        return super().__contains__(key)


def test_factory_builds_proposer_when_enabled_and_none_when_off(monkeypatch):
    import os
    original = os.environ
    spy = EnvSpy(original)
    monkeypatch.setattr(os, 'environ', spy)
    assert pc.ai_proposer('glider_cem', enabled=False, budget=AiCallBudget(daily_calls=1, concurrency=1)) is None
    assert spy.read == []  # flag off: the environment is not read at all
    monkeypatch.setattr(os, 'environ', original)
    budget = AiCallBudget(daily_calls=1, concurrency=1)
    built = pc.ai_proposer('glider_cem', enabled=True, budget=budget)
    # By name: another test may drop the module from sys.modules, so a re-import makes a new class object.
    assert (type(built).__module__, type(built).__qualname__) == ('engineering.glider_proposer', 'AiProposer')
    assert built.budget is budget
    assert isinstance(built, pc.CapabilityAiProposer)
    assert pc.ai_proposer('glider_cem', enabled=False, budget=budget) is None


def test_import_pulls_no_extraction_or_sdk():
    code = ('import sys, engineering.glider_proposer\n'
            'bad = [m for m in ("cem.extraction", "google.genai", "google.generativeai") if m in sys.modules]\n'
            'print(bad)\nsys.exit(1 if bad else 0)')
    done = subprocess.run([sys.executable, '-c', code], cwd=BACKEND, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
