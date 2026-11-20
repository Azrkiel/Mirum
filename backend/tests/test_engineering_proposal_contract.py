"""E05 proposal contract (Wave E P0): envelope conformance and lazy AI-proposer resolution.

Offline: no provider, no key, no network. The dragster ``AiProposer`` is the reference
implementation and runs here with a fake ``interpret``.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, replace

import pytest

from engineering import proposal_contract as pc
from engineering.capabilities import DRAGSTER, GLIDER, REGEN, UnsupportedCapability, get_generator


class Budget:
    def __init__(self, units=10):
        self.units, self.reserved, self.released = units, 0, 0
    def has_capacity(self): return self.units > 0
    def reserve(self):
        if self.units <= 0: return False
        self.units -= 1; self.reserved += 1; return True
    def release(self): self.released += 1


def _preset():
    from engineering.dragster_proposer import RulesProposer
    payload, issues, _ = RulesProposer().propose('Design a wedge CO2 dragster')
    assert not issues
    return payload['specification']


def _fake_interpret(result):
    async def interpret(prompt, base_spec):
        return result
    return interpret


def _proposed():
    from engineering.dragster_proposer import AiProposer
    spec = _preset()
    given = {name[len('specification.'):]: 'supplied' for name in DRAGSTER.review_fields(spec)}
    proposer = AiProposer(Budget(), _fake_interpret(dict(status='resolved', spec=spec, provenance=given)))
    return asyncio.run(proposer.propose('Design a wedge CO2 dragster'))


@dataclass(frozen=True)
class Envelope:
    state: str
    payload: dict
    issues: tuple
    provenance: object
    reasons: tuple


# --- conformance -----------------------------------------------------------------

def test_reference_matches_each_generator_descriptor():
    for d in (DRAGSTER, REGEN, GLIDER):
        ref = pc.reference(d)
        assert ref['id'] == d.id and ref['version'] == d.version
        assert ref['numerical_policy_version'] == d.numerical_version
        assert ref['resource_policy_version'] == 'engineering_resources_v1'


def test_dragster_ai_proposer_is_a_conforming_reference_implementation():
    proposal = _proposed()
    assert proposal.state == 'proposed'
    assert isinstance(proposal, pc.CapabilityProposal)
    assert pc.conform('co2_dragster', proposal) is proposal
    assert all(pc.UNVERIFIED in o.reference for o in proposal.provenance.field_origins
               if o.origin == 'ai_proposed')


@pytest.mark.parametrize('result,state', [
    (dict(status='needs_clarification', questions=['Which style?']), 'clarification_required'),
    (dict(status='unsupported', message='Not a dragster'), 'unsupported'),
    (dict(status='garbage'), 'unavailable'),
])
def test_dragster_non_proposed_states_conform(result, state):
    from engineering.dragster_proposer import AiProposer
    proposal = asyncio.run(AiProposer(Budget(), _fake_interpret(result)).propose('a CO2 dragster'))
    assert proposal.state == state
    assert pc.conform('co2_dragster', proposal) is proposal


def test_another_capability_is_never_substituted():
    proposal = _proposed()
    # The same envelope offered for another capability is refused, never re-labelled.
    with pytest.raises(pc.NonConformingProposal, match='different capability'):
        pc.conform('regen_chamber', proposal)
    wrong = replace(proposal, payload=dict(proposal.payload, capability=pc.reference(GLIDER)))
    with pytest.raises(pc.NonConformingProposal, match='different capability'):
        pc.conform('co2_dragster', wrong)


def test_version_drift_is_refused():
    proposal = _proposed()
    drifted = dict(pc.reference(DRAGSTER), version='9.9.9')
    with pytest.raises(pc.NonConformingProposal):
        pc.conform('co2_dragster', replace(proposal, payload=dict(proposal.payload, capability=drifted)))


def test_proposed_requires_a_valid_specification_and_no_issues():
    proposal = _proposed()
    bad = dict(proposal.payload, specification=dict(proposal.payload['specification'], not_a_field=1))
    with pytest.raises(pc.NonConformingProposal, match='schema'):
        pc.conform('co2_dragster', replace(proposal, payload=bad))
    with pytest.raises(pc.NonConformingProposal, match='no issues'):
        pc.conform('co2_dragster', replace(proposal, reasons=('x',)))
    missing = {k: v for k, v in proposal.payload.items() if k != 'specification'}
    with pytest.raises(pc.NonConformingProposal, match='needs a specification'):
        pc.conform('co2_dragster', replace(proposal, payload=missing))


def test_non_proposed_states_need_a_reason_and_no_full_specification():
    proposal = _proposed()
    with pytest.raises(pc.NonConformingProposal, match='reason'):
        pc.conform('co2_dragster', replace(proposal, state='unsupported'))
    with pytest.raises(pc.NonConformingProposal, match='partial specification'):
        pc.conform('co2_dragster', replace(proposal, state='unsupported', reasons=('no',)))
    with pytest.raises(pc.NonConformingProposal, match='unknown proposal state'):
        pc.conform('co2_dragster', replace(proposal, state='accepted', reasons=('no',)))


def test_ai_specification_needs_ai_provenance_marked_unverified():
    proposal = _proposed()
    form = proposal.provenance.model_copy(update=dict(input_mode='form'))
    with pytest.raises(pc.NonConformingProposal, match='AI provenance'):
        pc.conform('co2_dragster', replace(proposal, provenance=form))
    origins = tuple(o.model_copy(update=dict(reference='trusted')) if o.origin == 'ai_proposed' else o
                    for o in proposal.provenance.field_origins)
    assert any(o.origin == 'ai_proposed' for o in origins)
    unmarked = proposal.provenance.model_copy(update=dict(field_origins=origins))
    with pytest.raises(pc.NonConformingProposal, match='unverified'):
        pc.conform('co2_dragster', replace(proposal, provenance=unmarked))


def test_unknown_capability_and_non_envelopes_are_refused():
    with pytest.raises(UnsupportedCapability):
        pc.conform('rectangular_cantilever', _proposed())      # the beam keeps its own path
    with pytest.raises(pc.NonConformingProposal):
        pc.conform('co2_dragster', object())


# --- lazy resolution and the flag ------------------------------------------------

def test_flag_off_resolves_nothing_and_imports_no_proposer():
    for name in ('engineering.regen_proposer', 'engineering.glider_proposer'):
        sys.modules.pop(name, None)
    for cap in ('co2_dragster', 'regen_chamber', 'glider_cem', 'rectangular_cantilever', 'nope'):
        assert pc.ai_proposer(cap, enabled=False) is None
    assert 'engineering.regen_proposer' not in sys.modules
    assert 'engineering.glider_proposer' not in sys.modules


def test_flag_on_builds_the_dragster_proposer_with_the_given_budget():
    budget = Budget()
    proposer = pc.ai_proposer('co2_dragster', enabled=True, budget=budget)
    assert isinstance(proposer, pc.CapabilityAiProposer)
    assert proposer.budget is budget


def test_every_generator_declares_an_ai_proposer():
    for d in (DRAGSTER, REGEN, GLIDER):
        assert 'ai' in d.proposers, d.id
    assert REGEN.proposers['ai'] == 'engineering.regen_proposer:AiProposer'
    assert GLIDER.proposers['ai'] == 'engineering.glider_proposer:AiProposer'


@pytest.mark.parametrize('cap', ['regen_chamber', 'glider_cem'])
def test_declared_proposer_is_installed_and_built_with_the_given_budget(cap, monkeypatch):
    # Wave E P3 installed these modules; building one must not construct a real provider here.
    from engineering import proposer as beam
    monkeypatch.setattr(beam.GeminiProposalProvider, '__init__', lambda self: None)
    budget = Budget()
    built = pc.ai_proposer(cap, enabled=True, budget=budget)
    assert isinstance(built, pc.CapabilityAiProposer)
    assert built.budget is budget


def test_a_declared_but_missing_module_is_unsupported(monkeypatch):
    from engineering.capabilities import REGEN
    monkeypatch.setitem(REGEN.proposers, 'ai', 'engineering.no_such_proposer:AiProposer')
    with pytest.raises(UnsupportedCapability, match='not installed'):
        pc.ai_proposer('regen_chamber', enabled=True, budget=Budget())


def test_beam_and_unknown_capabilities_have_no_e05_proposer():
    with pytest.raises(UnsupportedCapability):
        pc.ai_proposer('rectangular_cantilever', enabled=True, budget=Budget())
    with pytest.raises(UnsupportedCapability):
        pc.ai_proposer('nope', enabled=True, budget=Budget())


# --- claim labels (Wave E contract; consumed by P1/P2 labels) ----------------------

def test_each_generator_declares_its_claim_and_none_is_certified():
    assert DRAGSTER.claim == 'geometry_checks_only'
    assert REGEN.claim == 'geometry_checks_only'
    assert GLIDER.claim == 'screening_only'
    for d in (DRAGSTER, REGEN, GLIDER):
        assert d.engineering_certified is False and d.standards == 'not_evaluated'
