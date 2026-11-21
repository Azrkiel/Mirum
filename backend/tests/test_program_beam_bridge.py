"""F02 T4: lossless beam bridge between BriefContent and ProgramIR, plus P03 fixtures."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cem.core import digest
from engineering.briefs import BriefService, MemoryBriefRepository
from engineering.capabilities import CAPABILITY
from engineering.contracts import BriefContent
from engineering.program.beam_bridge import (
    beam_capability_ref, brief_content_from_program, program_from_brief_content,
)
from engineering.program.errors import ProgramError
from engineering.program.hashing import (
    execution_lock_sha256, semantic_sha256, source_sha256,
)
from engineering.program.ir import (
    ProgramIR, ProgramProvenance, Requirement, RequirementPlan, TextValue, load_program_json,
)
from engineering.program.units import normalize

FIX = Path(__file__).with_name('fixtures') / 'program' / 'p03'
# Byte-identical copy of docs/.../examples/beam.mir: the native CI image ships backend/ but not docs/.
MIR = FIX / 'beam.mir'
DOCS_MIR = (Path(__file__).resolve().parents[2] / 'docs' / 'plans' / 'checked-engineering-v1'
            / 'phase-3-framework-language' / 'examples' / 'beam.mir')


def test_fixture_beam_mir_is_the_documented_example():
    # In a full checkout the copy must equal the documented example; images without docs/ check the copy only.
    if DOCS_MIR.exists():
        assert MIR.read_bytes() == DOCS_MIR.read_bytes()
    assert b'\r' not in MIR.read_bytes()
SPAN_FIELDS = {
    'beam.length': 'length', 'beam.width': 'width',
    'beam.dimensional_tolerance': 'dimensional_tolerance', 'load.tip_force': 'tip_force',
    'limit.max_tip_deflection': 'max_tip_deflection', 'material.youngs_modulus': 'youngs_modulus',
    'material.allowable_stress': 'allowable_stress', 'material.reference': 'material_reference',
    'material.source_kind': 'material_source_kind', 'model.assumptions': 'assumptions',
}


def service():
    return BriefService(MemoryBriefRepository())


def form_payload() -> dict:
    return json.loads((FIX / 'form.json').read_text(encoding='utf-8'))


def form_content() -> BriefContent:
    content, issues = service().validate(form_payload())
    assert content is not None and not issues
    return content


def ai_content() -> BriefContent:
    from engineering.proposer import _parse
    proposal = _parse((FIX / 'ai_proposal.json').read_text(encoding='utf-8'))
    assert proposal.state == 'proposed'
    content, issues = service().validate(proposal.fields.model_dump(mode='json', exclude_none=True))
    assert content is not None and not issues
    return content


def prov(kind='form', sha='a' * 64, source_id=None) -> ProgramProvenance:
    return ProgramProvenance(kind=kind, source_sha256=sha, source_id=source_id)


def form_program(content=None) -> ProgramIR:
    return program_from_brief_content(
        content or form_content(), provenance=prov('form', source_sha256(form_payload()), 'form.json'))


def ai_program() -> ProgramIR:
    raw = (FIX / 'ai_proposal.json').read_bytes()
    return program_from_brief_content(ai_content(), provenance=prov('ai', source_sha256(raw), 'ai_proposal.json'))


def language_program() -> ProgramIR:
    return load_program_json((FIX / 'language.program.json').read_bytes())


def replace_req(ir: ProgramIR, rid: str, value) -> ProgramIR:
    reqs = tuple(Requirement(id=r.id, value=value if r.id == rid else r.value, span=r.span)
                 for r in ir.requirements.requirements)
    return ir.model_copy(update={'requirements': RequirementPlan(requirements=reqs)})


def drop_req(ir: ProgramIR, rid: str) -> ProgramIR:
    reqs = tuple(r for r in ir.requirements.requirements if r.id != rid)
    return ir.model_copy(update={'requirements': RequirementPlan(requirements=reqs)})


# ------------------------------------------------------------------ round trip

def test_form_round_trip_is_exact():
    content = form_content()
    back = brief_content_from_program(form_program(content))
    assert back == content
    assert back.brief_sha256 == content.brief_sha256
    assert back.fixed_requirements_sha256 == content.fixed_requirements_sha256


def test_fixed_design_round_trip_is_exact():
    payload = form_payload()
    payload['mode'] = 'fixed_design'
    payload['candidate_heights_mm'] = [10.0]
    content, issues = service().validate(payload)
    assert content is not None and not issues
    ir = form_program(content)
    assert ir.candidates.mode == 'fixed_design'
    back = brief_content_from_program(ir)
    assert back == content and back.brief_sha256 == content.brief_sha256


def test_other_unit_form_input_equals_canonical_after_review():
    # The form path takes mm_N_MPa only, so mass/other-unit variants are normalized by units.normalize.
    ir = form_program()
    changed = replace_req(ir, 'beam.length', normalize(0.2, 'm', 'length'))
    assert brief_content_from_program(changed) == form_content()
    changed = replace_req(ir, 'load.tip_force', normalize(0.05, 'kN', 'force'))
    assert brief_content_from_program(changed) == form_content()


def test_structure_of_the_program():
    ir = form_program()
    ids = [r.id for r in ir.requirements.requirements]
    assert ids == list(SPAN_FIELDS)
    assert ir.candidates.mode == 'bounded_thickness'
    assert [p.id for p in ir.candidates.parameters] == ['beam.height']
    assert [v.value for v in ir.candidates.parameters[0].values] == [5.0, 10.0, 15.0]
    assert [a.check for a in ir.analysis] == list(CAPABILITY.required_checks)
    assert all(a.id == 'check.' + a.check for a in ir.analysis)
    assert all(a.requirement_refs == (*ids, 'beam.height') for a in ir.analysis)
    assert [e.analysis_ref for e in ir.evidence] == [a.id for a in ir.analysis]
    assert all(e.required and e.id == 'evidence.' + e.analysis_ref[len('check.'):] for e in ir.evidence)
    assert ir.design == 'reference_beam'
    assert (ir.execution.backend, ir.execution.exports) == ('occt', ('step', 'glb'))
    assert ir.capability == beam_capability_ref()
    assert ir.capability.id == CAPABILITY.id and ir.capability.version == CAPABILITY.version
    assert ir.capability.numerical_policy_version == CAPABILITY.numerical.version
    assert ir.capability.resource_policy_version == CAPABILITY.resources.version


# ------------------------------------------------------------------ P03 fixtures

def test_form_ai_language_share_semantic_hash_with_distinct_provenance():
    form, ai, lang = form_program(), ai_program(), language_program()
    assert semantic_sha256(form) == semantic_sha256(ai) == semantic_sha256(lang)
    assert [p.provenance.kind for p in (form, ai, lang)] == ['form', 'ai', 'language']
    assert len({p.provenance.source_sha256 for p in (form, ai, lang)}) == 3
    assert execution_lock_sha256(form) == execution_lock_sha256(ai) == execution_lock_sha256(lang)


def test_ai_fixture_goes_through_the_make_proposer_path():
    from engineering.proposer import ProposalResponse
    proposal = ProposalResponse.model_validate_json((FIX / 'ai_proposal.json').read_text(encoding='utf-8'))
    payload = proposal.fields.model_dump(mode='json', exclude_none=True)
    assert payload['candidate_heights_mm'] == [15.0, 5.0, 10.0]  # unsorted; the contract normalizes
    assert ai_content() == form_content()


def test_language_ir_provenance_and_source_hash():
    lang = language_program()
    raw = MIR.read_bytes()
    assert lang.provenance.kind == 'language'
    assert lang.provenance.source_id == 'examples/beam.mir'
    assert lang.provenance.source_sha256 == source_sha256(raw)


def test_language_spans_point_at_real_beam_mir_lines():
    lines = MIR.read_text(encoding='utf-8').split('\n')
    lang = language_program()
    seen = set()
    for r in lang.requirements.requirements:
        s = r.span
        assert s is not None and s.source_id == 'examples/beam.mir'
        assert s.line == s.end_line
        text = lines[s.line - 1]
        name = SPAN_FIELDS[r.id]
        assert re.match(rf'^\s*{name} = ', text), (r.id, text)
        assert text[s.column - 1:].startswith(name)
        assert text[s.end_column - 1] == ';'
        seen.add(r.id)
    assert seen == set(SPAN_FIELDS)
    height = lang.candidates.parameters[0].span
    assert 'candidate_heights' in lines[height.line - 1]
    mode = lang.candidates.span
    assert 'mode' in lines[mode.line - 1] and mode.end_line == height.end_line


def test_language_ir_may_order_requirements_differently():
    lang = language_program()
    assert [r.id for r in lang.requirements.requirements] != [r.id for r in form_program().requirements.requirements]
    assert brief_content_from_program(lang).brief_sha256 == form_content().brief_sha256
    assert brief_content_from_program(lang) == form_content()


def test_language_ir_form_ai_brief_digests_match():
    assert (brief_content_from_program(language_program()).brief_sha256
            == brief_content_from_program(ai_program()).brief_sha256
            == form_content().brief_sha256)


def test_brief_sha256_is_the_existing_contract_digest_rule():
    content = form_content()
    expected = digest(dict(
        schema_version=content.schema_version, capability=content.capability.model_dump(mode='json'),
        requirements=content.requirements.model_dump(mode='json'),
        required_checks=content.required_checks, mode=content.mode,
        candidate_heights_mm=content.candidate_heights_mm))
    assert content.brief_sha256 == expected
    assert brief_content_from_program(form_program()).brief_sha256 == expected


# ------------------------------------------------------------------ hash sensitivity

def test_changing_height_or_force_changes_semantic_hash():
    base = form_content()
    base_hash = semantic_sha256(form_program(base))
    payload = form_payload()
    payload['candidate_heights_mm'] = [5.0, 10.0, 14.0]
    h, _ = service().validate(payload)
    assert semantic_sha256(form_program(h)) != base_hash
    payload = form_payload()
    payload['requirements']['tip_force_n'] = 60.0
    f, _ = service().validate(payload)
    assert semantic_sha256(form_program(f)) != base_hash


def test_backend_and_exports_change_only_the_lock_hash():
    base = form_program()
    other = program_from_brief_content(form_content(), provenance=base.provenance,
                                       backend='other', exports=('step',))
    assert semantic_sha256(other) == semantic_sha256(base)
    assert execution_lock_sha256(other) != execution_lock_sha256(base)
    assert other.execution.backend == 'other' and other.execution.exports == ('step',)


# ------------------------------------------------------------------ negatives

def test_wrong_capability_is_ir010():
    ir = form_program()
    bad = ir.model_copy(update={'capability': ir.capability.model_copy(update={'id': 'rectangular_plate'})})
    with pytest.raises(ProgramError) as e:
        brief_content_from_program(bad)
    assert e.value.code == 'IR010'


@pytest.mark.parametrize('rid', list(SPAN_FIELDS))
def test_missing_requirement_is_ir004_naming_the_id(rid):
    with pytest.raises(ProgramError) as e:
        brief_content_from_program(drop_req(form_program(), rid))
    assert e.value.code == 'IR004' and e.value.field == rid


def test_missing_height_parameter_is_ir004():
    ir = form_program()
    bad = ir.model_copy(update={'candidates': ir.candidates.model_copy(update={'parameters': ()})})
    with pytest.raises(ProgramError) as e:
        brief_content_from_program(bad)
    assert e.value.code == 'IR004' and e.value.field == 'beam.height'


def test_wrong_dimension_is_ir006():
    ir = replace_req(form_program(), 'beam.length', normalize(50.0, 'N', 'force'))
    with pytest.raises(ProgramError) as e:
        brief_content_from_program(ir)
    assert e.value.code == 'IR006' and e.value.field == 'beam.length'


def test_text_where_quantity_expected_is_ir010():
    ir = replace_req(form_program(), 'load.tip_force', TextValue(kind='text', text='fifty'))
    with pytest.raises(ProgramError) as e:
        brief_content_from_program(ir)
    assert e.value.code == 'IR010' and e.value.field == 'load.tip_force'


def test_unknown_source_kind_is_ir010_with_contract_field():
    ir = replace_req(form_program(), 'material.source_kind', TextValue(kind='text', text='Illustrative'))
    with pytest.raises(ProgramError) as e:
        brief_content_from_program(ir)
    assert e.value.code == 'IR010' and e.value.field == 'requirements.material.source_kind'


def test_contract_range_violation_maps_to_ir010():
    ir = replace_req(form_program(), 'beam.width', normalize(300.0, 'mm', 'length'))
    with pytest.raises(ProgramError) as e:
        brief_content_from_program(ir)
    assert e.value.code == 'IR010'
    assert e.value.field


def test_unknown_assumptions_is_ir010():
    ir = replace_req(form_program(), 'model.assumptions', TextValue(kind='text', text='something_else'))
    with pytest.raises(ProgramError) as e:
        brief_content_from_program(ir)
    assert e.value.code == 'IR010' and e.value.field == 'requirements.assumptions'


def test_extra_requirement_is_not_dropped_silently():
    ir = form_program()
    extra = Requirement(id='beam.extra', value=normalize(1.0, 'mm', 'length'))
    bad = ir.model_copy(update={'requirements': RequirementPlan(
        requirements=(*ir.requirements.requirements, extra))})
    with pytest.raises(ProgramError) as e:
        brief_content_from_program(bad)
    assert e.value.code == 'IR002' and e.value.field == 'beam.extra'


def test_existing_golden_pins_are_untouched():
    # Guard: bridge must not alter the beam contract's own digest behavior.
    content = form_content()
    assert content.brief_sha256 == BriefContent.model_validate_json(content.model_dump_json()).brief_sha256
