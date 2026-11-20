"""E05 regen_chamber AI proposer: offline, fake provider, no network, no sleeps."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from engineering import proposal_contract as pc
from engineering import regen_presets
from engineering.capabilities import REGEN
from engineering.limits import AiCallBudget
from engineering.proposer import ProviderReply, ProviderSettings
from engineering.regen_proposer import (AiProposer, NOT_A_REGEN, PROMPT_VERSION, UNAVAILABLE,
                                        provider_schema)

PROMPT = ('Regeneratively cooled rocket thrust chamber with cooling channels: '
          'chamber radius 36 mm, throat radius 20 mm, 30 channels.')
BACKEND = Path(__file__).resolve().parents[1]


class FakeProvider:
    def __init__(self, replies=(), available=True, error=None):
        self.replies, self.available, self.error, self.calls, self.prompts = list(replies), available, error, 0, []

    def settings(self):
        return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture-model',
                                temperature=0, max_tokens=1024)

    async def generate(self, prompt, schema, settings):
        self.calls += 1
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return ProviderReply(text=self.replies.pop(0))


def reply(fields=None, **over):
    body = dict(state='proposed', out_of_family=False, preset=None, fields=fields or {},
                questions=[], unsupported_features=[])
    body.update(over)
    return json.dumps(body)


GOOD = reply(dict(chamber_radius_mm=36.0, throat_radius_mm=20.0, channel_count=30))


def run(provider, budget=None, prompt=PROMPT, base_spec=None):
    budget = budget or AiCallBudget(daily_calls=5, concurrency=1)
    proposal = asyncio.run(AiProposer(budget, provider).propose(prompt, base_spec))
    pc.conform('regen_chamber', proposal)
    return proposal, budget


def test_complete_prompt_is_proposed_with_origins():
    provider = FakeProvider([GOOD])
    proposal, budget = run(provider)
    assert proposal.state == 'proposed' and not proposal.issues and not proposal.reasons
    spec = proposal.payload['specification']
    assert spec['chamber_radius_mm'] == 36.0 and spec['channel_count'] == 30
    assert spec['wall_thickness_mm'] == regen_presets.EXAMPLES['reference']['wall_thickness_mm']
    assert proposal.payload['capability'] == pc.reference(REGEN)
    assert proposal.provenance.input_mode == 'ai'
    origins = {o.field: o for o in proposal.provenance.field_origins}
    assert len(origins) == len(REGEN.review_fields(spec))
    for name in ('chamber_radius_mm', 'throat_radius_mm', 'channel_count'):
        o = origins['specification.' + name]
        assert o.origin == 'ai_proposed' and 'unverified proposal' in o.reference
        assert PROMPT_VERSION in o.reference and 'fixture-model' in o.reference
    assert origins['specification.wall_thickness_mm'].origin == 'policy'
    assert origins['specification.wall_thickness_mm'].reference == 'preset:reference'
    assert provider.calls == 1 and budget.used() == 1
    assert json.loads(provider.prompts[0].split('data:\n', 1)[1]) == PROMPT


def test_preset_and_base_spec_overlay():
    base = dict(regen_presets.EXAMPLES['small'], wall_thickness_mm=6.5)
    proposal, _ = run(FakeProvider([reply(dict(channel_count=28), preset='small')]), base_spec=base)
    assert proposal.state == 'proposed'
    origins = {o.field: o for o in proposal.provenance.field_origins}
    assert origins['specification.wall_thickness_mm'].origin == 'user_supplied'
    assert origins['specification.channel_count'].origin == 'ai_proposed'
    assert proposal.payload['specification']['wall_thickness_mm'] == 6.5


def test_malformed_then_valid_repairs_with_two_units():
    provider = FakeProvider(['not json', GOOD])
    proposal, budget = run(provider)
    assert proposal.state == 'proposed'
    assert provider.calls == 2 and budget.used() == 2
    assert provider.prompts[1].startswith(provider.prompts[0]) and 'Repair' in provider.prompts[1]


@pytest.mark.parametrize('bad', ['{"state": "proposed", "state": "proposed"}',
                                 '{"fields": {"chamber_radius_mm": NaN}}',
                                 '{"fields": {"chamber_radius_mm": Infinity}}'])
def test_duplicate_keys_and_nonfinite_rejected(bad):
    proposal, _ = run(FakeProvider([bad, bad]))
    assert proposal.state == 'unavailable'


def test_malformed_twice_unavailable_without_raw_text():
    secret = 'SECRET-RAW-REPLY-TEXT'
    provider = FakeProvider([secret, secret + ' again'])
    proposal, budget = run(provider)
    assert proposal.state == 'unavailable' and proposal.reasons == UNAVAILABLE
    assert provider.calls == 2 and budget.used() == 2
    blob = json.dumps([proposal.payload, [i.model_dump() for i in proposal.issues],
                       proposal.provenance.model_dump(mode='json'), proposal.reasons])
    assert 'SECRET-RAW' not in blob and 'Regeneratively' not in blob


def test_zero_budget_makes_no_call():
    provider = FakeProvider([GOOD])
    proposal, _ = run(provider, AiCallBudget(daily_calls=0, concurrency=1))
    assert proposal.state == 'unavailable' and provider.calls == 0


def test_budget_of_one_stops_after_malformed_first_call():
    provider = FakeProvider(['nope', GOOD])
    proposal, budget = run(provider, AiCallBudget(daily_calls=1, concurrency=1))
    assert proposal.state == 'unavailable' and provider.calls == 1 and budget.used() == 1


def test_provider_error_unavailable_unit_not_refunded():
    provider = FakeProvider(error=RuntimeError('boom with PROMPT text'))
    proposal, budget = run(provider)
    assert proposal.state == 'unavailable' and 'boom' not in json.dumps(proposal.reasons)
    assert provider.calls == 1 and budget.used() == 1
    assert budget.has_capacity()  # in-flight released


def test_provider_unavailable_makes_no_call_and_reserves_nothing():
    provider = FakeProvider([GOOD], available=False)
    proposal, budget = run(provider)
    assert proposal.state == 'unavailable' and provider.calls == 0 and budget.used() == 0


def test_settings_error_is_unavailable():
    provider = FakeProvider([GOOD])
    provider.settings = lambda: (_ for _ in ()).throw(RuntimeError('no key'))
    proposal, budget = run(provider)
    assert proposal.state == 'unavailable' and provider.calls == 0 and budget.used() == 0


def test_out_of_family_unsupported_without_specification():
    proposal, _ = run(FakeProvider([reply(out_of_family=True, state='unsupported')]))
    assert proposal.state == 'unsupported' and 'specification' not in proposal.payload
    assert proposal.reasons == (NOT_A_REGEN,)


def test_router_precheck_skips_provider():
    provider = FakeProvider([GOOD])
    proposal, budget = run(provider, prompt='design a CO2 dragster with a wedge body')
    assert proposal.state == 'unsupported' and proposal.reasons == (NOT_A_REGEN,)
    assert provider.calls == 0 and budget.used() == 0


def test_unsupported_features_listed():
    proposal, _ = run(FakeProvider([reply(unsupported_features=['STEP export'])]))
    assert proposal.state == 'unsupported' and 'specification' not in proposal.payload
    assert [(i.field, i.state, i.reason) for i in proposal.issues] == [('features', 'unsupported', 'STEP export')]
    assert proposal.reasons == ('STEP export',)


def test_stated_voxel_below_limit_refused():
    proposal, _ = run(FakeProvider([reply(dict(voxel_size_mm=0.2))]))
    assert proposal.state == 'unsupported' and 'specification' not in proposal.payload
    assert proposal.issues[0].field == 'specification.voxel_size_mm'


def test_unsupported_value_is_never_downgraded_to_a_clarification():
    proposal, _ = run(FakeProvider([reply(dict(voxel_size_mm=0.2), state='clarification_required',
                                          questions=['Which channel count?'])]))
    assert proposal.state == 'unsupported' and 'specification' not in proposal.payload
    assert proposal.issues[0].field == 'specification.voxel_size_mm'


def test_envelope_violation_is_clarification_with_partial_spec():
    proposal, _ = run(FakeProvider([reply(dict(throat_radius_mm=40.0))]))
    assert proposal.state == 'clarification_required'
    assert proposal.payload['specification']['throat_radius_mm'] == 40.0
    assert proposal.reasons and all(i.field.startswith('specification') for i in proposal.issues)
    assert '40' not in ' '.join(proposal.reasons)  # msg only, never the input value
    assert proposal.provenance.input_mode == 'ai'


def test_questions_are_clarification():
    proposal, _ = run(FakeProvider([reply(dict(channel_count=30), questions=['Which chamber radius?'])]))
    assert proposal.state == 'clarification_required'
    assert [(i.field, i.reason) for i in proposal.issues] == [('request', 'Which chamber radius?')]
    assert proposal.payload['specification']['channel_count'] == 30


@pytest.mark.parametrize('prompt', ['   ', '', 'x' * 2001])
def test_bad_prompt_raises_before_any_call(prompt):
    provider = FakeProvider([GOOD])
    with pytest.raises(ValueError):
        asyncio.run(AiProposer(AiCallBudget(daily_calls=5, concurrency=1), provider).propose(prompt))
    assert provider.calls == 0


def test_oversized_reply_unavailable():
    big = reply(questions=['q'], preset=None) + ' ' * (REGEN.resources.structured_response_bytes + 1)
    provider = FakeProvider([big])
    proposal, budget = run(provider)
    assert proposal.state == 'unavailable' and provider.calls == 1 and budget.used() == 1


def test_provider_schema_is_gemini_subset():
    schema = provider_schema()
    assert schema['type_'] == 'OBJECT'
    assert schema['properties']['fields']['properties']['channel_count'] == {'type_': 'INTEGER', 'nullable': True}
    assert schema['properties']['preset']['nullable'] is True
    text = json.dumps(schema)
    assert '$ref' not in text and 'anyOf' not in text


def test_factory_builds_proposer_only_when_enabled():
    budget = AiCallBudget(daily_calls=1, concurrency=1)
    built = pc.ai_proposer('regen_chamber', enabled=True, budget=budget)
    assert isinstance(built, AiProposer) and built.budget is budget
    assert isinstance(built, pc.CapabilityAiProposer)
    assert pc.ai_proposer('regen_chamber', enabled=False) is None


def test_import_pulls_no_sdk():
    code = ('import sys, engineering.regen_proposer; '
            'bad = [m for m in ("google.genai", "google.generativeai") if m in sys.modules]; '
            'sys.exit(1 if bad else 0)')
    done = subprocess.run([sys.executable, '-c', code], cwd=BACKEND, capture_output=True)
    assert done.returncode == 0, done.stderr[-300:]
