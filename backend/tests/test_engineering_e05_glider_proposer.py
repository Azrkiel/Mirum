"""E05 glider_cem AI proposer: offline, fake extract, no network, no sleeps."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from cem.glider import calibration
from cem.glider.requirements import GliderMissionRequirements
from engineering import proposal_contract as pc
from engineering.capabilities import GLIDER
from engineering.glider_proposer import (AiProposer, CALIBRATION_QUESTION, FAMILIES_QUESTION, KEY_MISMATCH,
                                         NOT_A_GLIDER, PROMPT_VERSION, UNAVAILABLE)
from engineering.limits import AiCallBudget

PROMPT = 'Design a glider launched by drop that maximizes distance, using conventional and t_tail families.'
BACKEND = Path(__file__).resolve().parents[1]
SECRET = 'SECRET-EXC-TEXT-9137'


PLANTED_PRO = 'planted-fake-pro-key-0001'
PLANTED_HEAVY = 'planted-fake-heavy-key-0002'


@pytest.fixture(autouse=True)
def _engineering_key_only(monkeypatch):
    """Default: the approved configuration (no separate Pro key)."""
    monkeypatch.delenv('GEMINI_API_KEY_PRO', raising=False)


def requirements(**over):
    body = GliderMissionRequirements(launch=dict(method='drop')).model_dump(mode='json')
    body['discovery']['families'] = ['conventional', 't_tail']
    for key, value in over.items():
        body[key] = value
    return body


def review(req=None):
    return dict(status='review_requirements', requirements=req or requirements())


class FakeExtract:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls, self.prompts = result, error, 0, []

    async def __call__(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return copy.deepcopy(self.result)


def run(extract, budget=None, prompt=PROMPT, base_spec=None):
    budget = budget or AiCallBudget(daily_calls=5, concurrency=1)
    proposal = asyncio.run(AiProposer(budget, extract).propose(prompt, base_spec))
    pc.conform('glider_cem', proposal)
    return proposal, budget


def origins(proposal):
    return {o.field: o for o in proposal.provenance.field_origins}


def test_complete_mission_is_proposed():
    req = requirements()
    req['constraints']['max_span_m'] = 1.2
    extract = FakeExtract(review(req))
    proposal, budget = run(extract)
    assert proposal.state == 'proposed' and not proposal.issues and not proposal.reasons
    spec = proposal.payload['specification']
    assert proposal.payload['capability'] == pc.reference(GLIDER)
    assert spec['calibration_version'] == calibration.version(calibration.initial())
    assert proposal.provenance.input_mode == 'ai'
    got = origins(proposal)
    assert len(got) == len(GLIDER.review_fields(spec))
    cal = got['specification.calibration_version']
    assert cal.origin == 'policy' and 'shipped default' in cal.reference
    span = got['specification.requirements.constraints.max_span_m']
    assert span.origin == 'ai_proposed' and 'unverified proposal' in span.reference
    assert PROMPT_VERSION in span.reference
    assert got['specification.requirements.launch.method'].origin == 'ai_proposed'
    assert got['specification.requirements.discovery.families'].origin == 'ai_proposed'
    # Every requirement came from the model's extraction output, so none is labeled policy (LOW-5).
    for name in ('constraints.max_length_m', 'discovery.budget_evals', 'process'):
        assert got['specification.requirements.' + name].origin == 'ai_proposed'
    assert [f for f, o in got.items() if o.origin == 'policy'] == ['specification.calibration_version']
    assert extract.calls == 1 and budget.used() == 1


def test_model_value_equal_to_default_is_ai_proposed_and_proposer_policy_is_policy():
    """LOW-5: label by origin, not by value. A model-supplied value equal to the documented
    default is still an unverified AI proposal; only what the proposer fills is policy."""
    req = requirements()
    default = GliderMissionRequirements(launch=dict(method='drop')).model_dump(mode='json')
    assert req['constraints']['max_length_m'] == default['constraints']['max_length_m']
    proposal, _ = run(FakeExtract(review(req)))
    got = origins(proposal)
    same = got['specification.requirements.constraints.max_length_m']
    assert same.origin == 'ai_proposed' and pc.UNVERIFIED in same.reference
    cal = got['specification.calibration_version']
    assert cal.origin == 'policy' and 'shipped default' in cal.reference
    assert 'calibration_version' not in req  # filled by the proposer, never by the model


def test_needs_clarification_has_no_spec():
    extract = FakeExtract(dict(status='needs_clarification', questions=['Which launch?', 7, 'x' * 500]))
    proposal, _ = run(extract)
    assert proposal.state == 'clarification_required'
    assert 'specification' not in proposal.payload
    assert [i.field for i in proposal.issues] == ['request', 'request']
    assert len(proposal.issues[1].reason) == 240
    assert proposal.reasons == tuple(i.reason for i in proposal.issues)
    assert proposal.provenance.input_mode == 'form' and not proposal.provenance.field_origins


def test_empty_questions_get_generic_question():
    proposal, _ = run(FakeExtract(dict(status='needs_clarification', questions='nope')))
    assert proposal.state == 'clarification_required' and len(proposal.issues) == 1


def test_families_all_needs_clarification_with_partial_spec():
    req = requirements()
    req['discovery']['families'] = 'all'
    proposal, _ = run(FakeExtract(review(req)))
    assert proposal.state == 'clarification_required'
    assert [i.field for i in proposal.issues] == ['specification.requirements.discovery.families']
    assert proposal.reasons == (FAMILIES_QUESTION,)
    spec = proposal.payload['specification']
    assert spec['requirements']['discovery']['families'] == 'all'
    assert spec['calibration_version'] == calibration.version(calibration.initial())
    assert proposal.provenance.input_mode == 'ai'


def test_thermal_environment_is_unsupported():
    req = requirements()
    req['environment'] = dict(kind='thermal', thermal=dict(core_uplift_m_s=1.0, radius_m=50, distance_m=100))
    proposal, _ = run(FakeExtract(review(req)))
    assert proposal.state == 'unsupported' and 'specification' not in proposal.payload
    assert [i.field for i in proposal.issues] == ['specification.requirements.environment']
    assert proposal.reasons == tuple(i.reason for i in proposal.issues)


def test_unsupported_family_is_unsupported():
    req = requirements()
    req['discovery']['families'] = ['conventional', 'canard']
    proposal, _ = run(FakeExtract(review(req)))
    assert proposal.state == 'unsupported' and 'specification' not in proposal.payload
    assert [i.field for i in proposal.issues] == ['specification.requirements.discovery.families']


def test_calibration_mention_needs_clarification():
    prompt = PROMPT + ' Use the latest flight test calibration.'
    proposal, _ = run(FakeExtract(review()), prompt=prompt)
    assert proposal.state == 'clarification_required'
    assert [i.field for i in proposal.issues] == ['specification.calibration_version']
    assert proposal.reasons == (CALIBRATION_QUESTION,)
    spec = proposal.payload['specification']
    assert 'calibration_version' not in spec
    assert 'specification.calibration_version' not in origins(proposal)


def test_unreadable_calibration_needs_clarification(monkeypatch):
    def boom():
        raise OSError(SECRET)
    monkeypatch.setattr(calibration, 'initial', boom)
    proposal, _ = run(FakeExtract(review()))
    assert proposal.state == 'clarification_required'
    assert [i.field for i in proposal.issues] == ['specification.calibration_version']
    assert 'calibration_version' not in proposal.payload['specification']
    assert SECRET not in json.dumps(proposal.reasons)


def test_extract_error_is_unavailable_and_not_refunded():
    extract = FakeExtract(error=RuntimeError(SECRET))
    proposal, budget = run(extract)
    assert proposal.state == 'unavailable' and proposal.reasons == UNAVAILABLE
    assert extract.calls == 1 and budget.used() == 1
    assert budget._inflight == 0
    blob = json.dumps([proposal.payload, proposal.reasons, [i.reason for i in proposal.issues],
                       proposal.provenance.model_dump(mode='json')], default=str)
    assert SECRET not in blob and PROMPT not in blob


def test_zero_budget_is_unavailable_without_call():
    extract = FakeExtract(review())
    proposal, budget = run(extract, AiCallBudget(daily_calls=0, concurrency=1))
    assert proposal.state == 'unavailable' and extract.calls == 0 and budget.used() == 0


@pytest.mark.parametrize('result', [dict(status='weird'), dict(status='review_requirements', requirements=[1]),
                                    dict(status='review_requirements'), 'text', None])
def test_unknown_reply_is_unavailable(result):
    proposal, budget = run(FakeExtract(result))
    assert proposal.state == 'unavailable' and budget.used() == 1


def test_router_precheck_blocks_other_capability():
    extract = FakeExtract(review())
    proposal, budget = run(extract, prompt='Design a CO2 dragster car with a teardrop body for a 3 inch track.')
    assert proposal.state == 'unsupported' and proposal.reasons == (NOT_A_GLIDER,)
    assert extract.calls == 0 and budget.used() == 0


@pytest.mark.parametrize('prompt', ['', '   ', 'x' * 2001, None, 5])
def test_bad_prompt_raises(prompt):
    extract = FakeExtract(review())
    with pytest.raises(ValueError):
        asyncio.run(AiProposer(AiCallBudget(daily_calls=5, concurrency=1), extract).propose(prompt))
    assert extract.calls == 0


def test_invalid_requirements_give_partial_spec_without_input_values():
    req = requirements()
    req['constraints']['max_span_m'] = 3.0
    proposal, _ = run(FakeExtract(review(req)))
    assert proposal.state == 'clarification_required'
    assert [i.field for i in proposal.issues] == ['specification.requirements.constraints.max_span_m']
    assert '3.0' not in json.dumps(proposal.reasons)
    assert proposal.payload['specification']['requirements']['constraints']['max_span_m'] == 3.0
    assert proposal.provenance.input_mode == 'ai'


@pytest.mark.parametrize('pro', [None, PLANTED_HEAVY])
def test_engineering_key_configuration_proceeds(monkeypatch, pro):
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', PLANTED_HEAVY)
    if pro is not None:
        monkeypatch.setenv('GEMINI_API_KEY_PRO', pro)
    extract = FakeExtract(review())
    proposal, budget = run(extract)
    assert proposal.state == 'proposed' and extract.calls == 1 and budget.used() == 1


@pytest.mark.parametrize('heavy', [PLANTED_HEAVY, None])
def test_separate_pro_key_is_unavailable_before_budget_or_extract(monkeypatch, heavy):
    """M3: cem.extraction's legacy global client would use GEMINI_API_KEY_PRO, not the approved key."""
    monkeypatch.setenv('GEMINI_API_KEY_PRO', PLANTED_PRO)
    if heavy is None:
        monkeypatch.delenv('GEMINI_API_KEY_HEAVY', raising=False)
    else:
        monkeypatch.setenv('GEMINI_API_KEY_HEAVY', heavy)
    extract = FakeExtract(review())
    proposal, budget = run(extract)
    assert proposal.state == 'unavailable' and proposal.reasons == KEY_MISMATCH
    assert KEY_MISMATCH == ('Glider AI preparation needs the engineering key configuration; typed form remains available',)
    assert extract.calls == 0 and budget.used() == 0 and budget._inflight == 0
    blob = json.dumps([proposal.payload, proposal.reasons, [i.reason for i in proposal.issues],
                       proposal.provenance.model_dump(mode='json')], default=str)
    assert PLANTED_PRO not in blob and PLANTED_HEAVY not in blob


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
