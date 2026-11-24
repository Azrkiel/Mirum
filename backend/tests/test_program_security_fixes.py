"""F02 security-review fixes (PR #129): loader budgets, registry gates, bridge losslessness, hashing."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.program.beam_bridge import (
    brief_content_from_program, program_from_brief_content,
)
from engineering.program.errors import CODES, ProgramError
from engineering.program.hashing import canonical_json, semantic_sha256, source_sha256
from engineering.program.ir import (
    ConstructionGraph, ConstructionStep, TextValue, load_program_json, program_from_dict,
)
from engineering.program.ir import ProgramIR
from engineering.program.registry import (
    DEFAULT_REGISTRY, ArtifactGraph, OpStream, load_artifact_graph_json, validate_op_stream,
)
from engineering.program.versions import (
    ARTIFACT_GRAPH_VERSION, CONSTRUCTION_GRAPH_VERSION, OP_STREAM_VERSION, PROGRAM_IR_VERSION,
)

from .test_program_beam_bridge import ai_program, form_content, form_program, prov

FIX02 = Path(__file__).with_name('fixtures') / 'program' / 'p02'


def beam_dict() -> dict:
    return json.loads((FIX02 / 'beam.json').read_text(encoding='utf-8'))


def graph_dict() -> dict:
    return json.loads((FIX02 / 'artifact-graph.json').read_text(encoding='utf-8'))


def err_of(fn, *args, **kwargs) -> ProgramError:
    with pytest.raises(ProgramError) as ei:
        fn(*args, **kwargs)
    return ei.value


def nested(depth: int) -> str:
    return '[' * depth + ']' * depth


# ------------------------------------------------------------------ M1 budgets

def test_ir012_is_documented():
    assert CODES['IR012'] == 'budget_exceeded'


def test_oversize_source_is_ir012_before_parse():
    text = ' ' * 65537 + '{}'
    assert err_of(load_program_json, text).code == 'IR012'
    assert err_of(load_program_json, text.encode()).code == 'IR012'
    assert err_of(load_artifact_graph_json, text).code == 'IR012'


def test_source_at_limit_is_not_a_budget_error():
    text = '{}' + ' ' * (65536 - 2)
    assert err_of(load_program_json, text).code == 'IR001'


def test_multibyte_source_counts_bytes():
    text = '"' + 'é' * 33000 + '"'  # 33000 chars, 66000 bytes
    assert err_of(load_program_json, text).code == 'IR012'


@pytest.mark.parametrize('loader', [load_program_json, load_artifact_graph_json])
def test_depth_over_16_is_ir012(loader):
    assert err_of(loader, nested(17)).code == 'IR012'


def test_depth_16_passes_the_budget():
    assert err_of(load_program_json, nested(16)).code == 'IR001'


@pytest.mark.parametrize('loader', [load_program_json, load_artifact_graph_json])
def test_pathological_nesting_is_typed_not_recursion_error(loader):
    assert err_of(loader, nested(30000)).code == 'IR012'


def test_program_from_dict_deep_dict_is_ir012_not_recursion_error():
    deep: dict = {}
    cur = deep
    for _ in range(3000):
        cur['a'] = {}
        cur = cur['a']
    assert err_of(program_from_dict, deep).code == 'IR012'


def test_node_count_over_2000_is_ir012():
    obj = {'schema_version': PROGRAM_IR_VERSION, **{f'k{i}': 0 for i in range(2001)}}
    assert err_of(load_program_json, json.dumps(obj)).code == 'IR012'
    assert err_of(program_from_dict, obj).code == 'IR012'
    obj = {'schema_version': ARTIFACT_GRAPH_VERSION, **{f'k{i}': 0 for i in range(2001)}}
    assert err_of(load_artifact_graph_json, json.dumps(obj)).code == 'IR012'


def test_list_over_256_is_ir012():
    d = beam_dict()
    d['candidates']['parameters'][0]['values'] = d['candidates']['parameters'][0]['values'] * 200
    assert err_of(load_program_json, json.dumps(d)).code == 'IR012'
    g = graph_dict()
    g['nodes'] = [{'id': f'n.a{i}', 'kind': 'solid', 'op_seq': 0} for i in range(257)]
    assert err_of(load_artifact_graph_json, json.dumps(g)).code == 'IR012'


def test_long_free_string_is_ir012():
    d = beam_dict()
    idx = next(i for i, r in enumerate(d['requirements']['requirements'])
               if r['id'] == 'material.reference')
    d['requirements']['requirements'][idx]['value']['text'] = 'x' * 241
    assert err_of(program_from_dict, d).code == 'IR012'
    d = beam_dict()
    d['provenance']['source_id'] = 'x' * 241
    assert err_of(program_from_dict, d).code == 'IR012'
    d = beam_dict()
    d['analysis'][0]['check'] = 'x' * 129
    assert err_of(program_from_dict, d).code == 'IR012'


def test_every_list_field_has_a_256_bound():
    def step(i):
        return ConstructionStep(id=f'step.s{i}', operation='construct.sketch', operation_version='0.1.0', args={})

    with pytest.raises(ValidationError):
        ConstructionGraph(schema_version=CONSTRUCTION_GRAPH_VERSION, steps=tuple(step(i) for i in range(257)))
    with pytest.raises(ValidationError):
        ConstructionStep(id='step.a', operation='construct.sketch', operation_version='0.1.0', args={},
                         inputs=tuple(f'step.s{i}' for i in range(257)))
    d = beam_dict()
    d['analysis'] = d['analysis'] * 300
    assert err_of(program_from_dict, d).code == 'IR012'
    d = beam_dict()
    d['analysis'][0]['requirement_refs'] = ['beam.length'] * 257
    assert err_of(program_from_dict, d).code == 'IR012'


def test_construction_args_dict_is_bounded():
    args = {f'a{i}': TextValue(kind='text', text='x') for i in range(65)}
    with pytest.raises(ValidationError):
        ConstructionStep(id='step.a', operation='construct.sketch', operation_version='0.1.0', args=args)


def test_long_step_strings_are_bounded():
    with pytest.raises(ValidationError):
        ConstructionStep(id='step.a', operation='x' * 300, operation_version='0.1.0', args={})


def test_artifact_graph_strings_are_bounded():
    g = graph_dict()
    g['op_stream']['entries'][0]['operation'] = 'x' * 300
    assert err_of(load_artifact_graph_json, json.dumps(g)).code == 'IR012'
    g = graph_dict()
    g['op_stream']['entries'][0]['span'] = {'line': 1, 'column': 1, 'end_line': 1, 'end_column': 2,
                                            'source_id': 'x' * 300}
    assert err_of(load_artifact_graph_json, json.dumps(g)).code == 'IR012'


def test_valid_fixtures_stay_within_budgets():
    for p in ('p02/beam.json', 'p03/language.program.json'):
        load_program_json((FIX02.parent / p).read_bytes())


# ------------------------------------------------------------------ M2 registry gates

def test_artifact_graph_loader_rejects_unknown_operation():
    g = graph_dict()
    g['op_stream']['entries'][1]['operation'] = 'construct.unknown'
    e = err_of(load_artifact_graph_json, json.dumps(g))
    assert (e.code, e.field) == ('IR008', 'op_stream.entries.1.operation')


def test_artifact_graph_loader_rejects_unknown_operation_version():
    g = graph_dict()
    g['op_stream']['entries'][1]['operation_version'] = '9.9.9'
    assert err_of(load_artifact_graph_json, json.dumps(g)).code == 'IR001'


def test_reserved_operations_load_as_records_only_when_asked():
    graph = load_artifact_graph_json((FIX02 / 'artifact-graph.json').read_bytes(), require_executable=False)
    assert graph.op_stream.entries[1].operation == 'construct.extrude'


# M-B: the execution gate is the default (fail closed).
def test_loader_default_rejects_reserved_operations():
    e = err_of(load_artifact_graph_json, (FIX02 / 'artifact-graph.json').read_bytes())
    assert (e.code, e.field) == ('IR009', 'op_stream.entries.0.operation')


def test_executable_graph_passes_the_default_gate():
    g = graph_dict()
    for e in g['op_stream']['entries']:
        e['operation'] = 'capability.build'
    graph = load_artifact_graph_json(json.dumps(g))
    assert len(graph.op_stream.entries) == 2


def test_validate_op_stream_default_rejects_reserved_operations():
    graph = load_artifact_graph_json((FIX02 / 'artifact-graph.json').read_bytes(), require_executable=False)
    validate_op_stream(graph.op_stream, require_executable=False)
    assert err_of(validate_op_stream, graph.op_stream).code == 'IR009'


def test_validate_construction_default_rejects_reserved_operations():
    graph = ConstructionGraph(schema_version=CONSTRUCTION_GRAPH_VERSION, steps=(
        ConstructionStep(id='step.s', operation='construct.sketch', operation_version='0.1.0', args={}),))
    DEFAULT_REGISTRY.validate_construction(graph, require_executable=False)
    assert err_of(DEFAULT_REGISTRY.validate_construction, graph).code == 'IR009'


# M-A: direct constructors take the same budgets and gates as the loaders.
def code_of_validation(fn, *args):
    with pytest.raises((ProgramError, ValidationError)) as ei:
        fn(*args)
    exc = ei.value
    if isinstance(exc, ValidationError):
        exc = exc.errors(include_url=False)[0]['ctx']['error']
    assert isinstance(exc, ProgramError), repr(exc)
    return exc


def over_budget_graph() -> dict:
    return {'schema_version': ARTIFACT_GRAPH_VERSION, **{f'k{i}': 0 for i in range(2001)}}


@pytest.mark.parametrize('cls,obj', [
    (ProgramIR, {'schema_version': PROGRAM_IR_VERSION, **{f'k{i}': 0 for i in range(2001)}}),
    (ArtifactGraph, over_budget_graph()),
    (OpStream, {'schema_version': OP_STREAM_VERSION, **{f'k{i}': 0 for i in range(2001)}}),
])
def test_model_validate_applies_the_node_budget(cls, obj):
    assert code_of_validation(cls.model_validate, obj).code == 'IR012'


@pytest.mark.parametrize('cls,version', [(ProgramIR, PROGRAM_IR_VERSION), (ArtifactGraph, ARTIFACT_GRAPH_VERSION),
                                         (OpStream, OP_STREAM_VERSION)])
def test_model_validate_json_applies_the_budgets_and_strict_rules(cls, version):
    over_nodes = json.dumps({'schema_version': version, **{f'k{i}': 0 for i in range(2001)}})
    assert err_of(cls.model_validate_json, over_nodes).code == 'IR012'
    assert err_of(cls.model_validate_json, ' ' * 65537).code == 'IR012'
    dup = '{"schema_version": "%s", "schema_version": "%s"}' % (version, version)
    assert err_of(cls.model_validate_json, dup).code == 'IR003'
    nonfinite = '{"schema_version": "%s", "x": 1e400}' % version
    assert err_of(cls.model_validate_json, nonfinite).code == 'IR007'


def test_model_validate_json_still_validates_a_good_document():
    g = graph_dict()
    graph = ArtifactGraph.model_validate_json(json.dumps(g))
    assert graph == load_artifact_graph_json(json.dumps(g), require_executable=False)
    ir = ProgramIR.model_validate_json(json.dumps(beam_dict()))
    assert ir == load_program_json(json.dumps(beam_dict()))


def test_constructors_reject_unknown_operations_and_versions():
    g = graph_dict()
    g['op_stream']['entries'][1]['operation'] = 'construct.unknown'
    assert code_of_validation(ArtifactGraph.model_validate, g).code == 'IR008'
    e = err_of(ArtifactGraph.model_validate_json, json.dumps(g))
    assert (e.code, e.field) == ('IR008', 'op_stream.entries.1.operation')
    stream = {'schema_version': OP_STREAM_VERSION, 'entries': [
        {'seq': 0, 'operation': 'construct.extrude', 'operation_version': '9.9.9'}]}
    assert err_of(OpStream.model_validate_json, json.dumps(stream)).code == 'IR001'
    assert code_of_validation(OpStream.model_validate, stream).code == 'IR001'


def test_reserved_construct_operations_are_legal_records_through_constructors():
    graph = ArtifactGraph.model_validate_json((FIX02 / 'artifact-graph.json').read_bytes())
    assert graph.op_stream.entries[0].operation == 'construct.sketch'
    # ...but the execution gate still refuses them.
    assert err_of(validate_op_stream, graph.op_stream).code == 'IR009'


# ------------------------------------------------------------------ M3 bridge losslessness

def ir_dict(ir) -> dict:
    return json.loads(ir.model_dump_json())


def test_bridge_rejects_non_default_evidence_required():
    d = ir_dict(form_program())
    d['evidence'][0]['required'] = False
    e = err_of(brief_content_from_program, program_from_dict(d))
    assert e.code == 'IR010' and e.field.startswith('evidence.0')


def test_bridge_rejects_missing_or_extra_evidence():
    d = ir_dict(form_program())
    del d['evidence'][-1]
    assert err_of(brief_content_from_program, program_from_dict(d)).code == 'IR010'


def test_bridge_rejects_noncanonical_requirement_refs():
    d = ir_dict(form_program())
    d['analysis'][0]['requirement_refs'] = d['analysis'][0]['requirement_refs'][:-1]
    e = err_of(brief_content_from_program, program_from_dict(d))
    assert e.code == 'IR010' and e.field == 'analysis.0.requirement_refs'


def test_bridge_rejects_noncanonical_ids():
    d = ir_dict(form_program())
    d['evidence'][0]['id'] = 'evidence.other'
    assert err_of(brief_content_from_program, program_from_dict(d)).code == 'IR010'


@pytest.mark.parametrize('backend,exports', [('picogk', ['step', 'glb']), ('occt', ['step']),
                                             ('occt', ['glb', 'step'])])
def test_bridge_rejects_execution_it_cannot_carry(backend, exports):
    d = ir_dict(form_program())
    d['execution'] = {'backend': backend, 'exports': exports}
    e = err_of(brief_content_from_program, program_from_dict(d))
    assert e.code == 'IR010' and e.field.startswith('execution')


@pytest.mark.parametrize('text', [' padded', 'padded ', '\ttab', 'nl\n'])
def test_text_with_edge_whitespace_is_rejected_at_the_ir(text):
    with pytest.raises(ValidationError):
        TextValue(kind='text', text=text)
    d = beam_dict()
    idx = next(i for i, r in enumerate(d['requirements']['requirements']) if r['id'] == 'material.reference')
    d['requirements']['requirements'][idx]['value']['text'] = text
    assert err_of(program_from_dict, d).code == 'IR010'


@pytest.mark.parametrize('make', [form_program, ai_program])
def test_ir_brief_ir_round_trip_equality_over_all_fields(make):
    ir = make()
    brief = brief_content_from_program(ir)
    # design name, provenance and spans are not representable in BriefContent: the design name travels
    # as bridge input (not provenance); provenance is passed back explicitly.
    back = program_from_brief_content(brief, provenance=ir.provenance, design=ir.design)
    assert back == ir
    assert back.model_dump(mode='json') == ir.model_dump(mode='json')
    assert brief_content_from_program(back) == brief
    assert brief_content_from_program(back).brief_sha256 == brief.brief_sha256


def test_round_trip_with_several_heights():
    payload_content = form_content()
    ir = program_from_brief_content(payload_content, provenance=prov())
    assert ir == program_from_brief_content(brief_content_from_program(ir), provenance=prov())


def test_brief_to_ir_to_brief_is_identity():
    content = form_content()
    assert brief_content_from_program(program_from_brief_content(content, provenance=prov())) == content


# ------------------------------------------------------------------ L4 non-finite literals

@pytest.mark.parametrize('literal', ['1e400', '-1e400', '1E999', '2.5e+4000'])
def test_overflowing_float_literal_is_ir007(literal):
    text = '{"schema_version": "%s", "x": %s}' % (PROGRAM_IR_VERSION, literal)
    assert err_of(load_program_json, text).code == 'IR007'
    text = '{"schema_version": "%s", "x": [%s]}' % (ARTIFACT_GRAPH_VERSION, literal)
    assert err_of(load_artifact_graph_json, text).code == 'IR007'


def test_overflowing_literal_in_quantity_value_is_ir007_with_field():
    text = (FIX02 / 'beam.json').read_text(encoding='utf-8')
    # beam.json carries a quantity value; replace the first numeric value with an overflowing literal
    obj = beam_dict()
    obj['requirements']['requirements'][0]['value']['value'] = 'PLACEHOLDER'
    text = json.dumps(obj).replace('"PLACEHOLDER"', '1e400')
    e = err_of(load_program_json, text)
    assert e.code == 'IR007' and e.field == 'requirements.requirements.0.value.value'


# ------------------------------------------------------------------ L5 candidate values

def _with_heights(heights) -> dict:
    d = ir_dict(form_program())
    d['candidates']['parameters'][0]['values'] = [
        {'kind': 'quantity', 'dimension': 'length', 'value': h, 'unit': 'mm'} for h in heights]
    return d


def test_candidate_order_does_not_change_the_semantic_hash():
    a = semantic_sha256(program_from_dict(_with_heights([5.0, 10.0])))
    b = semantic_sha256(program_from_dict(_with_heights([10.0, 5.0])))
    assert a == b


def test_duplicate_candidate_values_are_rejected():
    e = err_of(program_from_dict, _with_heights([5.0, 5.0, 10.0]))
    assert e.code == 'IR010' and 'duplicate' in e.message


def test_duplicate_candidate_values_rejected_by_loader():
    assert err_of(load_program_json, json.dumps(_with_heights([5.0, 5.0]))).code == 'IR010'


def test_negative_zero_candidate_duplicates_zero():
    assert err_of(program_from_dict, _with_heights([0.0, -0.0])).code == 'IR010'


# ------------------------------------------------------------------ L6 backend / exports allowlists

@pytest.mark.parametrize('backend', ['bogus', 'OCCT', ''])
def test_unknown_backend_is_rejected(backend):
    d = beam_dict()
    d['execution']['backend'] = backend
    assert err_of(program_from_dict, d).code == 'IR010'


def test_unknown_export_is_rejected():
    d = beam_dict()
    d['execution']['exports'] = ['step', 'exe']
    assert err_of(program_from_dict, d).code == 'IR010'


def test_export_allowlist_keeps_beam_formats_and_adds_picogk_stl():
    from typing import get_args
    from engineering.capabilities import CAPABILITY
    from engineering.program.ir import ExportFormat
    formats = get_args(ExportFormat)
    assert tuple(fmt for fmt in formats if fmt in CAPABILITY.exports) == CAPABILITY.exports
    assert set(formats) - set(CAPABILITY.exports) == {'stl'}


def test_symbolic_backends_are_accepted():
    for backend in ('occt', 'picogk'):
        d = beam_dict()
        d['execution']['backend'] = backend
        assert program_from_dict(d).execution.backend == backend


# ------------------------------------------------------------------ L7 lone surrogates

def test_lone_surrogate_in_loaded_json_is_typed():
    d = beam_dict()
    d['provenance']['source_id'] = 'PLACEHOLDER'
    text = json.dumps(d).replace('PLACEHOLDER', '\\ud800')
    assert err_of(load_program_json, text).code == 'IR010'


def test_lone_surrogate_in_dict_is_typed_before_hashing():
    d = beam_dict()
    d['provenance']['source_id'] = '\ud800'
    assert err_of(program_from_dict, d).code == 'IR010'


def test_lone_surrogate_key_is_typed():
    d = beam_dict()
    d['\udc00'] = 1
    assert err_of(program_from_dict, d).code == 'IR010'


def test_hashing_a_lone_surrogate_is_typed():
    assert err_of(source_sha256, '\ud800').code == 'IR010'
    assert err_of(source_sha256, {'a': '\ud800'}).code == 'IR010'
    assert err_of(canonical_json, {'a': '\ud800'}).code == 'IR010'


# ------------------------------------------------------------------ L8 pure imports

def test_importing_the_program_package_pulls_no_heavy_dependency():
    backend = Path(__file__).resolve().parents[1]
    root = backend.parent
    code = (
        "import sys\n"
        "import engineering.program\n"
        "bad = [m for m in ('cadquery', 'OCP', 'google.genai', 'fastapi', 'httpx') "
        "if m in sys.modules or any(k == m or k.startswith(m + '.') for k in sys.modules)]\n"
        "print(','.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(backend), str(root)]))
    proc = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ------------------------------------------------------------------ L-1 design name is never dropped silently

def test_bridge_rejects_a_non_canonical_design_name_unless_passed_explicitly():
    ir = program_from_brief_content(form_content(), provenance=prov(), design='bracket_beam')
    e = err_of(brief_content_from_program, ir)
    assert e.code == 'IR010' and e.field == 'design'
    assert err_of(brief_content_from_program, ir, design='other_beam').field == 'design'
    brief = brief_content_from_program(ir, design='bracket_beam')
    assert program_from_brief_content(brief, provenance=prov(), design='bracket_beam') == ir


def test_canonical_design_name_needs_no_acknowledgement():
    ir = form_program()
    assert ir.design == 'reference_beam'
    assert brief_content_from_program(ir) == form_content()


# ------------------------------------------------------------------ L-2 error-code noise

def test_semantic_id_format_errors_are_ir010_not_budget_errors():
    d = beam_dict()
    d['requirements']['requirements'][0]['id'] = 'a' * 129
    assert err_of(program_from_dict, d).code == 'IR010'
    d = beam_dict()
    d['analysis'][0]['requirement_refs'] = ['a' * 129]
    assert err_of(program_from_dict, d).code == 'IR010'
    d = beam_dict()
    d['requirements']['requirements'][0]['id'] = 'Bad Id'
    assert err_of(program_from_dict, d).code == 'IR010'


def test_budget_bounded_fields_stay_ir012():
    d = beam_dict()
    d['provenance']['source_id'] = 'x' * 241
    assert err_of(program_from_dict, d).code == 'IR012'
    d = beam_dict()
    d['analysis'] = d['analysis'] * 300
    assert err_of(program_from_dict, d).code == 'IR012'


def test_dumps_strict_maps_only_nonfinite_to_ir007():
    from engineering.program.ir import _dumps_strict
    assert err_of(_dumps_strict, {'a': float('inf')}).code == 'IR007'
    assert err_of(_dumps_strict, {'a': float('nan')}).code == 'IR007'
    circular: dict = {}
    circular['self'] = circular
    assert err_of(_dumps_strict, circular).code == 'IR010'
    assert err_of(_dumps_strict, {'a': object()}).code == 'IR010'


def test_package_docstring_states_its_real_dependencies():
    import engineering.program as pkg
    doc = pkg.__doc__ or ''
    assert 'engineering.contracts' in doc and 'engineering.capabilities' in doc
