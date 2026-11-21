"""K26: conform enforces the provenance its docstring promises (offline, fail closed)."""
import asyncio
import hashlib
from dataclasses import replace

import pytest

from engineering import proposal_contract as pc
from engineering.capabilities import DRAGSTER
from engineering.contracts import FieldOrigin


class Budget:
    def has_capacity(self): return True
    def reserve(self): return True
    def release(self): pass


def _proposed():
    from engineering.dragster_proposer import AiProposer, RulesProposer
    payload, issues, _ = RulesProposer().propose('Design a wedge CO2 dragster')
    spec = payload['specification']
    given = {n[len('specification.'):]: 'supplied' for n in DRAGSTER.review_fields(spec)}

    async def interpret(prompt, base_spec):
        return dict(status='resolved', spec=spec, provenance=given)
    return asyncio.run(AiProposer(Budget(), interpret).propose('Design a wedge CO2 dragster'))


def _with_origins(proposal, origins):
    return replace(proposal, provenance=proposal.provenance.model_copy(update=dict(field_origins=tuple(origins))))


def test_pinned_dragster_proposal_still_conforms():
    p = _proposed()
    assert pc.conform('co2_dragster', p) is p


def test_missing_field_origin_is_refused():
    p = _proposed()
    with pytest.raises(pc.NonConformingProposal, match='every reviewable field'):
        pc.conform('co2_dragster', _with_origins(p, p.provenance.field_origins[1:]))


def test_unknown_extra_origin_is_refused():
    p = _proposed()
    extra = FieldOrigin(field='specification.not_a_real_field', origin='policy', reference='x')
    with pytest.raises(pc.NonConformingProposal):
        pc.conform('co2_dragster', _with_origins(p, (*p.provenance.field_origins, extra)))


def _ai_ref(p, ref):
    return [o.model_copy(update=dict(reference=ref)) if o.origin == 'ai_proposed' else o
            for o in p.provenance.field_origins]


def test_ai_reference_without_sha256_is_refused():
    p = _proposed()
    with pytest.raises(pc.NonConformingProposal, match='SHA-256'):
        pc.conform('co2_dragster', _with_origins(p, _ai_ref(p, 'dragster_interpret_v1; extraction; unverified proposal')))


def test_ai_reference_with_short_or_uppercase_sha_is_refused():
    p = _proposed()
    for sha in ('abc123', hashlib.sha256(b'x').hexdigest().upper()):
        with pytest.raises(pc.NonConformingProposal, match='SHA-256'):
            pc.conform('co2_dragster', _with_origins(p, _ai_ref(p, f'{sha}; dragster_interpret_v1; unverified proposal')))


def test_ai_reference_without_prompt_version_is_refused():
    p = _proposed()
    sha = hashlib.sha256(b'x').hexdigest()
    with pytest.raises(pc.NonConformingProposal, match='prompt version'):
        pc.conform('co2_dragster', _with_origins(p, _ai_ref(p, f'{sha}; other_v9; unverified proposal')))


def test_ai_reference_without_unverified_marker_is_refused():
    p = _proposed()
    sha = hashlib.sha256(b'x').hexdigest()
    with pytest.raises(pc.NonConformingProposal, match='unverified'):
        pc.conform('co2_dragster', _with_origins(p, _ai_ref(p, f'{sha}; dragster_interpret_v1')))


def test_partial_clarification_spec_is_also_checked():
    p = _proposed()
    partial = replace(p, state='clarification_required', reasons=('q',))
    bad = _with_origins(partial, partial.provenance.field_origins[1:])
    with pytest.raises(pc.NonConformingProposal, match='every reviewable field'):
        pc.conform('co2_dragster', bad)


@pytest.mark.parametrize('capability,module', [('regen_chamber', 'engineering.regen_proposer'),
                                               ('glider_cem', 'engineering.glider_proposer'),
                                               ('co2_dragster', 'engineering.dragster_proposer')])
def test_prompt_version_is_resolved_from_the_ai_proposer_module(capability, module):
    import importlib
    assert pc.prompt_version(capability) == importlib.import_module(module).PROMPT_VERSION


def test_no_prompt_version_fails_closed():
    with pytest.raises(pc.NonConformingProposal):
        pc.prompt_version('rectangular_plate')


def test_broken_proposer_module_is_nonconforming_not_a_crash(monkeypatch):
    """Security review LOW: any import failure fails closed as NonConformingProposal (503), never a 500."""
    def boom(name):
        raise RuntimeError('broken module')
    monkeypatch.setattr(pc.importlib, 'import_module', boom)
    with pytest.raises(pc.NonConformingProposal):
        pc.prompt_version(DRAGSTER.id)


def test_refuse_error_is_nonconforming_not_a_crash(monkeypatch):
    """Security review LOW: an exception inside descriptor.refuse fails closed."""
    proposal = _proposed()
    assert proposal.state == 'proposed'

    def broken(spec):
        raise KeyError('malformed')
    monkeypatch.setattr(type(DRAGSTER), 'refuse', lambda self, spec: broken(spec))
    with pytest.raises(pc.NonConformingProposal):
        pc.conform(DRAGSTER.id, proposal)
