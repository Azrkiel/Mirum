"""Dragster proposers for the checked-design draft flow (U6.3, D8 extraction).

Both modes return what ``BriefService.create`` takes: a payload, issues and provenance. Nothing
here finalizes a brief, runs native work or builds a provider.

- ``RulesProposer`` (no model, no key, no network; allowed with the AI flag off). It uses the
  repo's ``dragsters`` intent rules, ``extraction._styles`` and the preset loader (pydantic, no
  CadQuery), not the standard library alone. It drafts the preset for the
  one style the prompt names, every field marked ``policy``. A prompt that states any digit gets a
  blocking clarification: stated values are never silently ignored.
- ``AiProposer`` (built only when ``MIRUM_ENGINEERING_AI_DRAFTS`` is on): reserves one call in the
  engineering ``AiCallBudget``, then calls ``dragsters.extraction.interpret`` unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from uuid import uuid4

from .briefs import InputIssue
from .capabilities import DRAGSTER
from .contracts import CapabilityReference, FieldOrigin, Provenance

PROMPT_VERSION = 'dragster_interpret_v1'
MAX_PROMPT = 2000
UNAVAILABLE = ('AI preparation unavailable; typed form remains available',)
DIGIT_REASON = 'Stated values are not read without AI drafts; enter them in the form.'
NO_STYLE = 'Which body style: low wedge, smooth teardrop, or narrow waist with raised rear housing?'
MANY_STYLES = 'Name one body style: wedge, teardrop, or narrow waist.'
NOT_A_DRAGSTER = 'This request is not a CO2 dragster; no dragster draft was made'


def _reference() -> dict:
    return CapabilityReference(id=DRAGSTER.id, version=DRAGSTER.version,
                               numerical_policy_version=DRAGSTER.numerical_version,
                               resource_policy_version=DRAGSTER.resources.version).model_dump(mode='json')


def _states_a_value(prompt: str) -> bool:
    """Any digit, except the one in the name "CO2" (the spellings ``intent.detect`` reads)."""
    text = re.sub(r'co\s*[2₂]', ' ', prompt, flags=re.IGNORECASE)
    return any(c.isdigit() for c in text)


def _check_prompt(prompt):
    if type(prompt) is not str or not prompt.strip() or len(prompt) > MAX_PROMPT:
        raise ValueError('prompt must contain 1-2000 characters')


def _provenance(origins=()) -> Provenance:
    return Provenance(record_id=uuid4().hex, created_at=datetime.now(timezone.utc),
                      input_mode='form', field_origins=tuple(origins))


def _issue(state, reason, field='request') -> InputIssue:
    return InputIssue(field=field, state=state, reason=reason[:240])


class RulesProposer:
    """``propose(prompt) -> (payload, issues, provenance)``: the contract ``rules_proposer`` documents."""

    def propose(self, prompt: str):
        _check_prompt(prompt)
        from dragsters import intent
        from dragsters.extraction import _styles
        from dragsters.styles import load_preset
        payload = dict(capability=_reference())
        if not intent.detect(prompt).is_dragster:
            return payload, (_issue('unsupported', NOT_A_DRAGSTER),), _provenance()
        styles = _styles(prompt)
        if len(styles) != 1:
            return payload, (_issue('clarification_required', MANY_STYLES if styles else NO_STYLE),), _provenance()
        style = styles[0]
        specification = load_preset(style).model_dump(mode='json')
        origins = tuple(FieldOrigin(field=name, origin='policy', reference=f'preset:{style}')
                        for name in DRAGSTER.review_fields(specification))
        issues = ((_issue('clarification_required', DIGIT_REASON),) if _states_a_value(prompt) else ())
        return dict(payload, specification=specification), issues, _provenance(origins)


@dataclass(frozen=True)
class DragsterProposal:
    state: str                       # proposed | clarification_required | unsupported | unavailable
    payload: dict
    issues: tuple
    provenance: Provenance
    reasons: tuple


class AiProposer:
    def __init__(self, budget, interpret=None):
        self.budget, self._interpret = budget, interpret

    async def propose(self, prompt: str, base_spec=None) -> DragsterProposal:
        _check_prompt(prompt)
        payload = dict(capability=_reference())
        empty = _provenance()
        if not self.budget.reserve():
            return DragsterProposal('unavailable', payload, (), empty, UNAVAILABLE)
        from dragsters import extraction
        interpret = self._interpret or extraction.interpret
        try:
            result = await interpret(prompt, base_spec)
        except extraction.ExtractionUnavailable:
            return DragsterProposal('unavailable', payload, (), empty, UNAVAILABLE)
        finally:
            self.budget.release()
        status = result.get('status')
        if status == 'needs_clarification':
            issues = tuple(_issue('clarification_required', q) for q in result['questions'])
            return DragsterProposal('clarification_required', payload, issues, empty, tuple(i.reason for i in issues))
        if status == 'unsupported':
            issues = (_issue('unsupported', result['message']),)
            return DragsterProposal('unsupported', payload, issues, empty, (issues[0].reason,))
        if status != 'resolved':
            return DragsterProposal('unavailable', payload, (), empty, UNAVAILABLE)
        specification, given = result['spec'], result['provenance']
        sha = hashlib.sha256(prompt.encode()).hexdigest()
        origins = []
        for name in DRAGSTER.review_fields(specification):
            source = given.get(name[len('specification.'):])
            if source == 'supplied':
                origin = FieldOrigin(field=name, origin='ai_proposed',
                                     reference=f'{sha}; {PROMPT_VERSION}; extraction; unverified proposal'[:240])
            elif source == 'base_spec':
                origin = FieldOrigin(field=name, origin='user_supplied', reference='Supplied base specification')
            else:
                origin = FieldOrigin(field=name, origin='policy', reference=str(source or 'preset')[:240])
            origins.append(origin)
        provenance = Provenance(record_id=uuid4().hex, created_at=datetime.now(timezone.utc), input_mode='ai',
                                field_origins=tuple(origins))
        return DragsterProposal('proposed', dict(payload, specification=specification), (), provenance, ())


def make_ai_proposer(*, enabled: bool, budget=None, interpret=None):
    """None unless the flag is on: off builds nothing, imports nothing and reads no key."""
    if not enabled:
        return None
    if budget is None:
        from .limits import AiCallBudget
        budget = AiCallBudget.from_config()
    return AiProposer(budget, interpret)
