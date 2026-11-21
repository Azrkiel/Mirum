"""E05 capability proposal contract (Wave E, P0).

One envelope and one conformance check for AI proposals of registered *generator*
capabilities (co2_dragster, regen_chamber, glider_cem). The beam keeps its own
B13-qualified path: ``proposer.py``, ``dragster_proposer.py``, ``config.py`` and
``subagents/model_registry.py`` are byte-pinned by ``e2e/test_image_pins.py`` and are
never edited for E05. New proposers live in new modules and only *import* from them.

Contract (every E05 AI proposer obeys all of it; ``conform`` enforces 1-4 at runtime):

1. Capability: ``payload['capability']`` is exactly the requested descriptor's
   reference (id, version, numerical and resource policy versions). A proposer never
   substitutes another or a less capable family; anything outside the family is
   ``unsupported`` with a reason.
2. ``proposed``: ``payload['specification']`` parses with ``descriptor.parse_spec``,
   ``descriptor.refuse`` returns nothing, and there are no issues.
3. Every other state carries at least one reason, and no ``specification`` unless the
   proposal is ``clarification_required`` (a partial draft the user completes).
4. Provenance: ``input_mode == 'ai'`` when a specification is present; each reviewable
   field has one origin; an ``ai_proposed`` reference names the prompt SHA-256, the
   proposer's prompt version and the words ``unverified proposal``.
5. Budget: one ``AiCallBudget`` unit per provider call (repair calls included), reserved
   before the call and never refunded; no capacity means ``unavailable`` with no call.
6. Flag: the factory returns ``None`` when ``MIRUM_ENGINEERING_AI_DRAFTS`` is off, and
   then builds no provider, imports no SDK and reads no key.
7. Raw provider replies never enter the envelope, provenance, logs or responses.
8. Prompt length: at most ``MAX_PROMPT`` characters; the caller rejects longer input.

Corpus fixtures for offline tests: ``backend/tests/fixtures/engineering/proposals/``
(see the README there).
"""
from __future__ import annotations

import importlib
import re
from typing import Any, Literal, Protocol, runtime_checkable

from .capabilities import GeneratorDescriptor, UnsupportedCapability, get_generator

ENVELOPE_VERSION = 'capability_proposal_v1'
MAX_PROMPT = 2000
STATES = ('proposed', 'clarification_required', 'unsupported', 'unavailable')
ProposalState = Literal['proposed', 'clarification_required', 'unsupported', 'unavailable']
UNVERIFIED = 'unverified proposal'
_SHA256 = re.compile(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])')


@runtime_checkable
class CapabilityProposal(Protocol):
    """What ``propose`` returns. ``dragster_proposer.DragsterProposal`` already matches."""
    state: str
    payload: dict
    issues: tuple
    provenance: Any
    reasons: tuple


@runtime_checkable
class CapabilityAiProposer(Protocol):
    """An E05 AI proposer. Constructed as ``cls(budget)`` or ``cls(budget, <fake>)`` in tests."""
    async def propose(self, prompt: str, base_spec: dict | None = None) -> CapabilityProposal: ...


class NonConformingProposal(ValueError):
    """A proposer broke the contract. Callers report ``unavailable``, never the payload."""


def reference(descriptor: GeneratorDescriptor) -> dict:
    return dict(id=descriptor.id, version=descriptor.version,
                numerical_policy_version=descriptor.numerical_version,
                resource_policy_version=descriptor.resources.version)


def prompt_version(capability_id: str) -> str:
    """The AI proposer module's ``PROMPT_VERSION``; fails closed when there is none."""
    descriptor = get_generator(capability_id)
    target = descriptor.proposers.get('ai') if descriptor else None
    if target is None:
        raise NonConformingProposal('capability has no AI proposer prompt version')
    try:
        version = getattr(importlib.import_module(target.partition(':')[0]), 'PROMPT_VERSION', None)
    except ImportError:
        version = None
    if not isinstance(version, str) or not version:
        raise NonConformingProposal('AI proposer declares no prompt version')
    return version


def conform(capability_id: str, proposal: Any) -> Any:
    """Return ``proposal`` unchanged if it meets rules 1-4, else raise ``NonConformingProposal``."""
    descriptor = get_generator(capability_id)
    if descriptor is None:
        raise UnsupportedCapability('Unknown capability')
    if not isinstance(proposal, CapabilityProposal):
        raise NonConformingProposal('not a capability proposal')
    if proposal.state not in STATES:
        raise NonConformingProposal('unknown proposal state')
    payload = proposal.payload
    if not isinstance(payload, dict) or payload.get('capability') != reference(descriptor):
        raise NonConformingProposal('proposal names a different capability or version')
    spec = payload.get('specification')
    if proposal.state == 'proposed':
        if not isinstance(spec, dict) or proposal.issues or proposal.reasons:
            raise NonConformingProposal('a proposed state needs a specification and no issues')
        try:
            descriptor.parse_spec(spec)
        except Exception:
            raise NonConformingProposal('specification does not match the capability schema') from None
        if descriptor.refuse(spec):
            raise NonConformingProposal('specification requests an unsupported feature')
    else:
        if not proposal.reasons:
            raise NonConformingProposal('a non-proposed state needs a reason')
        if spec is not None and proposal.state != 'clarification_required':
            raise NonConformingProposal('only a clarification may carry a partial specification')
    if spec is not None:
        if getattr(proposal.provenance, 'input_mode', None) != 'ai':
            raise NonConformingProposal('an AI specification needs AI provenance')
        origins = tuple(getattr(proposal.provenance, 'field_origins', ()))
        fields = [o.field for o in origins]
        if len(fields) != len(set(fields)):
            raise NonConformingProposal('each field needs one origin')
        if not isinstance(spec, dict) or set(fields) != set(descriptor.review_fields(spec)):
            raise NonConformingProposal('every reviewable field needs exactly one origin')
        version = None
        for o in origins:
            if o.origin != 'ai_proposed':
                continue
            if UNVERIFIED not in o.reference:
                raise NonConformingProposal('AI-proposed fields must be marked unverified')
            if _SHA256.search(o.reference) is None:
                raise NonConformingProposal('AI-proposed fields must carry the prompt SHA-256')
            version = version or prompt_version(capability_id)
            if version not in o.reference:
                raise NonConformingProposal('AI-proposed fields must carry the prompt version')
    return proposal


def ai_proposer(capability_id: str, *, enabled: bool, budget=None):
    """Resolve the descriptor's ``ai`` proposer lazily; ``None`` when the flag is off.

    Flag off: imports nothing and reads nothing (contract rule 6). A declared but not
    yet installed module raises ``UnsupportedCapability('proposer not installed')``.
    """
    if not enabled:
        return None
    descriptor = get_generator(capability_id)
    target = descriptor.proposers.get('ai') if descriptor else None
    if target is None:
        raise UnsupportedCapability('AI drafts are not offered for this capability')
    module, _, attribute = target.partition(':')
    try:
        cls = getattr(importlib.import_module(module), attribute)
    except ModuleNotFoundError as missing:
        if missing.name == module:
            raise UnsupportedCapability('proposer not installed') from None
        raise
    if budget is None:
        from .limits import AiCallBudget
        budget = AiCallBudget.from_config()
    return cls(budget)
