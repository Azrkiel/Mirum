"""F02 T1: ProgramIR records and the strict loader."""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from engineering.program import (
    ARTIFACT_GRAPH_VERSION, CONSTRUCTION_GRAPH_VERSION, EXECUTION_LOCK_VERSION,
    OP_STREAM_VERSION, PROGRAM_IR_VERSION, REGISTRY_VERSION, SEMANTICS_VERSION,
)
from engineering.program.versions import REGISTRY_VERSION_V0_1
from engineering.program.errors import Diagnostic, ProgramError
from engineering.program.ir import (
    CapabilityRef, CandidateParameter, CandidateSpace, ConstructionGraph,
    ConstructionStep, EvidenceExpectation, ExecutionSelection, ProgramIR,
    ProgramProvenance, Requirement, RequirementPlan, SourceSpan, AnalysisPlanRef,
    TextValue, execution_lock, load_program_json, program_from_dict,
)
from engineering.program.units import normalize


def make_program() -> ProgramIR:
    return ProgramIR(
        schema_version=PROGRAM_IR_VERSION,
        design='small_part',
        capability=CapabilityRef(id='demo_cap', version='1.0.0',
                                 numerical_policy_version='np1', resource_policy_version='rp1'),
        requirements=RequirementPlan(requirements=(
            Requirement(id='part.length', value=normalize(10.0, 'cm', 'length'),
                        span=SourceSpan(line=1, column=1, end_line=1, end_column=5)),
            Requirement(id='part.note', value=TextValue(kind='text', text='hello')),
        )),
        candidates=CandidateSpace(mode='sweep', parameters=(
            CandidateParameter(id='part.height', values=(
                normalize(5.0, 'mm', 'length'), normalize(6.0, 'mm', 'length'))),
        )),
        analysis=(AnalysisPlanRef(id='check.size', check='size_ok',
                                  requirement_refs=('part.length', 'part.height')),),
        construction=ConstructionGraph(schema_version=CONSTRUCTION_GRAPH_VERSION, steps=()),
        evidence=(EvidenceExpectation(id='evidence.size', analysis_ref='check.size', required=True),),
        execution=ExecutionSelection(backend='occt', exports=('step', 'glb')),
        provenance=ProgramProvenance(kind='form', source_sha256='a' * 64),
    )


def make_dict() -> dict:
    return json.loads(make_program().model_dump_json())


def load_err(obj_or_text) -> ProgramError:
    text = obj_or_text if isinstance(obj_or_text, (str, bytes)) else json.dumps(obj_or_text)
    with pytest.raises(ProgramError) as ei:
        load_program_json(text)
    return ei.value


def test_versions():
    assert PROGRAM_IR_VERSION == 'mirum_program_ir_v0_1'
    assert SEMANTICS_VERSION == 'mirum_semantics_v0_1'
    assert REGISTRY_VERSION == 'mirum_operation_registry_v0_2'
    assert REGISTRY_VERSION_V0_1 == 'mirum_operation_registry_v0_1'
    assert EXECUTION_LOCK_VERSION == 'mirum_execution_lock_v0_1'
    assert CONSTRUCTION_GRAPH_VERSION == 'mirum_construction_graph_v0_1'
    assert OP_STREAM_VERSION == 'mirum_op_stream_v0_1'
    assert ARTIFACT_GRAPH_VERSION == 'mirum_artifact_graph_v0_1'


def test_round_trip():
    ir = make_program()
    back = load_program_json(ir.model_dump_json())
    assert back == ir
    assert load_program_json(ir.model_dump_json().encode('utf-8')) == ir
    assert program_from_dict(json.loads(ir.model_dump_json())) == ir


def test_models_frozen_and_strict():
    ir = make_program()
    with pytest.raises(ValidationError):
        ir.design = 'other'
    with pytest.raises(ValidationError):
        TextValue(kind='text', text=5)


def test_source_span_end_before_start():
    with pytest.raises(ValidationError) as ei:
        SourceSpan(line=3, column=4, end_line=3, end_column=2)
    assert ei.value.errors()[0]['ctx']['error'].code == 'IR010'
    SourceSpan(line=1, column=1, end_line=1, end_column=1)


def test_execution_lock():
    lock = execution_lock(make_program())
    assert lock.schema_version == EXECUTION_LOCK_VERSION
    assert lock.program_ir_version == PROGRAM_IR_VERSION
    assert lock.semantics_version == SEMANTICS_VERSION
    assert lock.registry_version == REGISTRY_VERSION
    assert (lock.capability_id, lock.capability_version) == ('demo_cap', '1.0.0')
    assert (lock.numerical_policy_version, lock.resource_policy_version) == ('np1', 'rp1')
    assert lock.backend == 'occt' and lock.exports == ('step', 'glb')


def test_unknown_schema_version_ir001():
    d = make_dict()
    d['schema_version'] = 'nope'
    e = load_err(d)
    assert (e.code, e.field) == ('IR001', 'schema_version')
    d.pop('schema_version')
    assert load_err(d).code == 'IR001'
    assert load_err('[1, 2]').code == 'IR001'


def test_nested_unknown_schema_version_ir001():
    d = make_dict()
    d['construction']['schema_version'] = 'bad'
    e = load_err(d)
    assert (e.code, e.field) == ('IR001', 'construction.schema_version')


def test_nested_missing_schema_version_ir001():
    d = make_dict()
    del d['construction']['schema_version']
    e = load_err(d)
    assert (e.code, e.field) == ('IR001', 'construction.schema_version')


def test_extra_field_ir002():
    d = make_dict()
    d['surprise'] = 1
    e = load_err(d)
    assert (e.code, e.field) == ('IR002', 'surprise')
    d = make_dict()
    d['capability']['extra'] = 1
    assert (load_err(d).code, load_err(d).field) == ('IR002', 'capability.extra')


def test_duplicate_key_top_level_ir003():
    text = '{"design": "a", "design": "b"}'
    e = load_err(text)
    assert (e.code, e.field) == ('IR003', 'design')


def test_duplicate_key_nested_ir003():
    text = json.dumps(make_dict()).replace('"id": "demo_cap"', '"id": "demo_cap", "id": "x"')
    e = load_err(text)
    assert (e.code, e.field) == ('IR003', 'capability.id')


def test_analysis_ref_missing_requirement_ir004():
    d = make_dict()
    d['analysis'][0]['requirement_refs'].append('part.nothing')
    e = load_err(d)
    assert (e.code, e.field) == ('IR004', 'analysis.0.requirement_refs.2')


def test_evidence_ref_missing_analysis_ir004():
    d = make_dict()
    d['evidence'][0]['analysis_ref'] = 'check.missing'
    e = load_err(d)
    assert (e.code, e.field) == ('IR004', 'evidence.0.analysis_ref')


def test_nan_literal_ir007():
    text = json.dumps(make_dict()).replace('"value": 100.0', '"value": NaN')
    assert 'NaN' in text
    e = load_err(text)
    assert e.code == 'IR007'
    assert e.field == 'requirements.requirements.0.value.value'
    for lit in ('Infinity', '-Infinity'):
        t = json.dumps(make_dict()).replace('"value": 100.0', '"value": ' + lit)
        assert load_err(t).code == 'IR007'


def test_wrong_type_ir010():
    d = make_dict()
    d['design'] = 5
    e = load_err(d)
    assert (e.code, e.field) == ('IR010', 'design')


def test_missing_field_ir010():
    d = make_dict()
    del d['execution']
    e = load_err(d)
    assert (e.code, e.field) == ('IR010', 'execution')


def test_field_path_strips_union_tag():
    d = make_dict()
    d['requirements']['requirements'][1]['value']['text'] = ''
    e = load_err(d)
    assert e.code == 'IR010'
    assert e.field == 'requirements.requirements.1.value.text'


def test_quantity_noncanonical_unit_in_json_ir006():
    d = make_dict()
    d['requirements']['requirements'][0]['value']['unit'] = 'm'
    e = load_err(d)
    assert (e.code, e.field) == ('IR006', 'requirements.requirements.0.value.unit')


def test_duplicate_requirement_id_ir011():
    d = make_dict()
    d['requirements']['requirements'][1]['id'] = 'part.length'
    e = load_err(d)
    assert (e.code, e.field) == ('IR011', 'requirements.requirements.1.id')


def test_candidate_id_collides_with_requirement_ir011():
    d = make_dict()
    d['candidates']['parameters'][0]['id'] = 'part.note'
    assert load_err(d).code == 'IR011'


def test_duplicate_analysis_and_evidence_ids_ir011():
    d = make_dict()
    d['analysis'].append(copy.deepcopy(d['analysis'][0]))
    assert load_err(d).code == 'IR011'
    d = make_dict()
    d['evidence'].append(copy.deepcopy(d['evidence'][0]))
    assert load_err(d).code == 'IR011'


def test_nonempty_construction_ir009():
    d = make_dict()
    d['construction']['steps'] = [{
        'id': 'step.one', 'operation': 'construct.extrude', 'operation_version': '0.1.0',
        'args': {}, 'inputs': []}]
    e = load_err(d)
    assert e.code == 'IR009'
    assert e.field == 'construction.steps'


def test_construction_step_record_shape():
    s = ConstructionStep(id='step.one', operation='construct.extrude', operation_version='0.1.0',
                         args={'distance': normalize(1.0, 'mm', 'length')})
    assert s.inputs == ()


def test_error_span_from_nearest_record():
    d = make_dict()
    d['requirements']['requirements'][0]['value']['unit'] = 'm'
    e = load_err(d)
    assert e.span == SourceSpan(line=1, column=1, end_line=1, end_column=5)


def test_diagnostic_shape():
    d = make_dict()
    d['surprise'] = 1
    diag = load_err(d).diagnostic()
    assert isinstance(diag, Diagnostic)
    assert diag.code == 'IR002'
    assert diag.field == 'surprise'
    assert isinstance(diag.message, str) and diag.message
    assert diag.span is None
    assert set(json.loads(diag.model_dump_json())) == {'code', 'message', 'field', 'span'}


def test_program_error_is_value_error():
    e = ProgramError('IR010', 'bad', field='x')
    assert isinstance(e, ValueError)
    assert (e.code, e.message, e.field, e.span) == ('IR010', 'bad', 'x', None)


def test_import_hygiene():
    code = (
        "import sys, engineering.program\n"
        "bad = [m for m in ('cadquery','OCP','google.genai','fastapi','httpx') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
