"""U6.3 dragster proposer (D8 extraction as the draft source). Host-runnable, no provider, no key."""
import asyncio
import json
import os
import sys

import pytest

RULES_PROMPT = 'Design a CO2 dragster with a wedge body'


def services():
    from engineering.briefs import BriefService, MemoryBriefRepository
    return BriefService(MemoryBriefRepository())


def rules():
    from engineering.dragster_proposer import RulesProposer
    return RulesProposer()


def finalize(service, prepared):
    from engineering.contracts import ReviewAcknowledgement
    content = prepared.draft.content
    return service.finalize(ReviewAcknowledgement.model_validate_json(json.dumps(dict(
        draft_id=prepared.draft.draft_id, draft_revision=prepared.draft.revision,
        brief_sha256=content.brief_sha256, acknowledged_fields=list(content.review_fields),
        reviewed_at='2026-11-17T12:05:00Z', reviewed_by='local-user'))))


# --- rules mode --------------------------------------------------------------------------

@pytest.mark.parametrize('style,prompt', [
    ('wedge', 'Design a CO2 dragster with a wedge body'),
    ('teardrop', 'co2 dragster, tear drop shape'),
    ('narrow_waist', 'a CO2 racecar with a narrow waist')])
def test_a_styled_prompt_gives_the_preset_with_every_field_marked_policy(style, prompt):
    from dragsters.styles import load_preset
    from engineering.capabilities import get_generator
    payload, issues, provenance = rules().propose(prompt)
    preset = load_preset(style).model_dump(mode='json')
    assert payload['specification'] == preset and issues == ()
    assert payload['capability']['id'] == 'co2_dragster'
    assert {o.origin for o in provenance.field_origins} == {'policy'}
    assert {o.reference for o in provenance.field_origins} == {f'preset:{style}'}
    assert [o.field for o in provenance.field_origins] == list(get_generator('co2_dragster').review_fields(preset))
    assert provenance.input_mode == 'form'


def test_the_rules_draft_runs_the_same_review_flow_and_finalizes():
    service = services()
    payload, issues, provenance = rules().propose(RULES_PROMPT)
    prepared = service.create(payload, provenance=provenance, extra_issues=tuple(issues))
    assert prepared.issues == () and prepared.draft.content is not None
    brief = finalize(service, prepared)
    assert brief.content.capability.id == 'co2_dragster' and len(brief.content.required_checks) == 14


def test_a_prompt_with_a_digit_adds_a_blocking_clarification():
    service = services()
    payload, issues, provenance = rules().propose('CO2 dragster teardrop, 220 mm long')
    assert [(i.state, i.field) for i in issues] == [('clarification_required', 'request')]
    assert issues[0].reason == 'Stated values are not read without AI drafts; enter them in the form.'
    prepared = service.create(payload, provenance=provenance, extra_issues=tuple(issues))
    assert prepared.draft.clarifications
    with pytest.raises(ValueError):
        finalize(service, prepared)


def test_a_non_dragster_prompt_is_unsupported_with_no_specification():
    payload, issues, provenance = rules().propose('a glider with 2 m span')
    assert [i.state for i in issues if i.state == 'unsupported']
    assert 'specification' not in payload and provenance.field_origins == ()
    for prompt in ('a full size pinewood derby car', 'bracket'):
        assert any(i.state == 'unsupported' for i in rules().propose(prompt)[1])


def test_no_style_or_several_styles_need_clarification_with_the_interpret_wording():
    _, issues, _ = rules().propose('CO2 dragster')
    assert [(i.state, i.reason) for i in issues] == [('clarification_required',
        'Which body style: low wedge, smooth teardrop, or narrow waist with raised rear housing?')]
    payload, issues, _ = rules().propose('CO2 dragster wedge or teardrop')
    assert [(i.state, i.reason) for i in issues] == [('clarification_required',
        'Name one body style: wedge, teardrop, or narrow waist.')]
    assert 'specification' not in payload


def test_style_detection_helper_is_pinned():
    from dragsters.extraction import _styles
    assert _styles('Wedge') == ['wedge'] and _styles('tear-drop') == ['teardrop']
    assert _styles('waisted coke bottle') == ['narrow_waist'] and _styles('raised housing') == []
    assert _styles('wedge teardrop') == ['wedge', 'teardrop']


@pytest.mark.parametrize('prompt', ['', '   ', 'x' * 2001, None, 7])
def test_bad_prompts_are_value_errors(prompt):
    with pytest.raises(ValueError):
        rules().propose(prompt)


def test_the_rules_proposer_is_registered_and_reads_no_key_and_builds_no_provider(monkeypatch):
    reads = []

    class Watching(dict):
        def __getitem__(self, key):
            reads.append(key)
            return super().__getitem__(key)

        def get(self, key, default=None):
            reads.append(key)
            return super().get(key, default)
    monkeypatch.setattr(os, 'environ', Watching({'GEMINI_API_KEY_HEAVY': 'must-not-be-read'}))
    from engineering.proposer import rules_proposer
    proposer = rules_proposer('co2_dragster')
    proposer.propose(RULES_PROMPT)
    assert not [k for k in reads if 'KEY' in str(k)]
    assert not [m for m in sys.modules if m.startswith('google')]


# --- ai mode -----------------------------------------------------------------------------

def preset_resolution(**provenance_changes):
    from dragsters.styles import load_preset
    spec = load_preset('wedge').model_dump(mode='json')
    provenance = {}

    def walk(node, prefix=''):
        for key, value in node.items():
            if isinstance(value, dict) and value:
                walk(value, prefix + key + '.')
            else:
                provenance[prefix + key] = 'preset:wedge'
    walk(spec)
    provenance.update(provenance_changes)
    return dict(status='resolved', spec=spec, provenance=provenance, changed_fields={})


def ai(interpret, budget=None):
    from engineering.dragster_proposer import AiProposer
    from engineering.limits import AiCallBudget
    return AiProposer(budget or AiCallBudget(5, 1), interpret=interpret)


def test_ai_resolved_maps_provenance_and_feeds_the_review_flow():
    async def interpret(prompt, base_spec=None):
        return preset_resolution(**{'wheels.front_diameter_mm': 'supplied', 'length_mm': 'base_spec'})
    outcome = asyncio.run(ai(interpret).propose('wedge dragster with bigger front wheels'))
    assert outcome.state == 'proposed' and outcome.issues == ()
    origins = {o.field: o for o in outcome.provenance.field_origins}
    assert outcome.provenance.input_mode == 'ai'
    supplied = origins['specification.wheels.front_diameter_mm']
    assert supplied.origin == 'ai_proposed'
    assert 'dragster_interpret_v1' in supplied.reference and 'unverified proposal' in supplied.reference
    assert origins['specification.length_mm'].origin == 'user_supplied'
    assert origins['specification.width_mm'].origin == 'policy'
    service = services()
    prepared = service.create(outcome.payload, provenance=outcome.provenance, extra_issues=tuple(outcome.issues))
    assert prepared.issues == () and finalize(service, prepared).content.capability.id == 'co2_dragster'


def test_ai_status_mapping_for_clarification_unsupported_and_unavailable():
    from dragsters.extraction import ExtractionUnavailable

    def returning(value):
        async def interpret(prompt, base_spec=None):
            return value
        return interpret
    outcome = asyncio.run(ai(returning(dict(status='needs_clarification', questions=['How much wider?', 'Which?'])))
                          .propose('wider dragster'))
    assert outcome.state == 'clarification_required'
    assert [(i.state, i.reason) for i in outcome.issues] == [('clarification_required', 'How much wider?'),
                                                              ('clarification_required', 'Which?')]
    assert 'specification' not in outcome.payload
    outcome = asyncio.run(ai(returning(dict(status='unsupported', message='Unsupported request: fillets',
                                            supported_fields=[]))).propose('fillets'))
    assert outcome.state == 'unsupported' and [i.state for i in outcome.issues] == ['unsupported']

    async def unavailable(prompt, base_spec=None):
        raise ExtractionUnavailable('secret provider detail')
    outcome = asyncio.run(ai(unavailable).propose('wedge dragster'))
    assert outcome.state == 'unavailable'
    assert outcome.reasons == ('AI preparation unavailable; typed form remains available',)
    assert 'secret' not in json.dumps(outcome.reasons)


def test_ai_reserves_one_budget_call_before_interpret_and_stops_when_exhausted():
    from engineering.limits import AiCallBudget
    budget = AiCallBudget(1, 1)
    seen = []

    async def interpret(prompt, base_spec=None):
        seen.append(prompt)
        return preset_resolution()
    proposer = ai(interpret, budget)
    assert asyncio.run(proposer.propose('wedge dragster')).state == 'proposed'
    assert not budget.has_capacity()
    outcome = asyncio.run(proposer.propose('wedge dragster again'))
    assert outcome.state == 'unavailable' and seen == ['wedge dragster']


def test_ai_validates_the_prompt_before_spending_budget():
    from engineering.limits import AiCallBudget
    budget = AiCallBudget(1, 1)
    with pytest.raises(ValueError):
        asyncio.run(ai(lambda *a, **k: None, budget).propose(''))
    assert budget.has_capacity()


def test_ai_calls_the_real_interpret_unchanged_when_only_its_provider_reply_is_replaced(monkeypatch):
    from dragsters import extraction
    asked = []

    async def reply(prompt, style, allowed):
        asked.append(style)
        return json.dumps(dict(changes=[dict(field='wheels.front_diameter_mm', op='set', value=3.0, unit='cm')],
                               unsupported=[], questions=[]))
    monkeypatch.setattr(extraction, '_response', reply)
    from engineering.dragster_proposer import AiProposer
    from engineering.limits import AiCallBudget
    outcome = asyncio.run(AiProposer(AiCallBudget(1, 1)).propose('wedge dragster with 3 cm front wheels'))
    assert asked == ['wedge'] and outcome.state == 'proposed', outcome
    assert outcome.payload['specification']['wheels']['front_diameter_mm'] == 30.0


def test_flag_off_builds_no_provider_and_reads_no_key(monkeypatch):
    reads = []

    class Watching(dict):
        def __getitem__(self, key):
            reads.append(key)
            return super().__getitem__(key)

        def get(self, key, default=None):
            reads.append(key)
            return super().get(key, default)
    monkeypatch.setattr(os, 'environ', Watching({'GEMINI_API_KEY_HEAVY': 'must-not-be-read'}))
    from dragsters import extraction
    called = []
    monkeypatch.setattr(extraction, 'interpret', lambda *a, **k: called.append(1))
    monkeypatch.setattr(extraction, '_response', lambda *a, **k: called.append(2))
    from engineering.dragster_proposer import make_ai_proposer
    assert make_ai_proposer(enabled=False) is None
    assert called == [] and not [k for k in reads if 'KEY' in str(k) or 'GEMINI' in str(k)]
    assert not [m for m in sys.modules if m.startswith('google')]


def test_flag_on_builds_an_ai_proposer_with_a_budget_and_no_key_read(monkeypatch):
    from engineering.dragster_proposer import AiProposer, make_ai_proposer
    from engineering.limits import AiCallBudget
    proposer = make_ai_proposer(enabled=True, budget=AiCallBudget(2, 1))
    assert isinstance(proposer, AiProposer)
