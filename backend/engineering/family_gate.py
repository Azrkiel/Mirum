"""Deterministic capability-family gate for generator AI drafts (E05 fix loop, D2).

One pure function shared by ``api_v1.create_draft`` (generator ``mode='ai'`` step 3) and
``scripts/e05_live_eval.py``, so production and the live harness cannot drift. Decided before any
provider call or budget unit: a prompt the router sends to another capability is ``unsupported``;
an ``Ambiguous`` route is a clarification; otherwise ``None`` (the proposer may run). No I/O, no
provider, no key, no budget.
"""
from __future__ import annotations

from dataclasses import dataclass

OTHER_FAMILY = 'This request matches another capability; no draft of the requested capability was made'
AMBIGUOUS = 'This request matches several capabilities; describe one object'


@dataclass(frozen=True)
class GateRefusal:
    state: str        # 'unsupported' | 'clarification_required'
    reason: str       # fixed user-facing text: OTHER_FAMILY or AMBIGUOUS (the api_v1 issue reason)
    reason_id: str    # 'unsupported.family' | 'clarification.ambiguous' (harness evidence, D1)


def family_gate(prompt: str, capability_id: str) -> GateRefusal | None:
    """Return the refusal for ``prompt`` requested as ``capability_id``, or ``None``.

    Exactly the decision ``api_v1`` made inline before this module existed:
    ``routed = router.route(prompt)``;
    ``routed.capability_id not in (None, capability_id)`` -> ``GateRefusal('unsupported', OTHER_FAMILY, 'unsupported.family')``;
    ``routed.capability_id is None and routed.reason.startswith('Ambiguous')`` ->
    ``GateRefusal('clarification_required', AMBIGUOUS, 'clarification.ambiguous')``; else ``None``.
    """
    from .router import route
    routed = route(prompt)
    if routed.capability_id not in (None, capability_id):
        return GateRefusal('unsupported', OTHER_FAMILY, 'unsupported.family')
    if routed.capability_id is None and routed.reason.startswith('Ambiguous'):
        return GateRefusal('clarification_required', AMBIGUOUS, 'clarification.ambiguous')
    return None
