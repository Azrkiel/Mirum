"""F02 T2: operation registry, construction validation, OpStream/ArtifactGraph."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from engineering.program.errors import ProgramError, SourceSpan
from engineering.program.ir import ConstructionGraph, ConstructionStep, TextValue
from engineering.program.registry import (
    DEFAULT_REGISTRY, ArgSpec, ArtifactGraph, ArtifactNode, OpStream, OpStreamEntry,
    OperationDescriptor, OperationRegistry, OutputSpec, load_artifact_graph_json,
    validate_op_stream,
)
from engineering.program.units import normalize
from engineering.program.versions import (
    ARTIFACT_GRAPH_VERSION, CONSTRUCTION_GRAPH_VERSION, OP_STREAM_VERSION, REGISTRY_VERSION,
)

EXPECTED_IDS = {
    'capability.build', 'capability.inspect', 'capability.analyze', 'capability.export',
    'construct.sketch', 'construct.extrude', 'construct.revolve', 'construct.sweep',
    'construct.loft', 'construct.fillet', 'construct.chamfer', 'construct.shell',
    'construct.pattern', 'construct.boolean',
}
PHYSICAL = {
    'construct.extrude': {'distance': 'length'},
    'construct.revolve': {'angle': 'angle'},
    'construct.fillet': {'radius': 'length'},
    'construct.chamfer': {'distance': 'length'},
    'construct.shell': {'thickness': 'length'},
    'construct.pattern': {'count': 'dimensionless', 'spacing': 'length'},
}
SPAN = SourceSpan(line=3, column=1, end_line=3, end_column=9)


def q(value, unit, dim):
    return normalize(value, unit, dim)


def step(sid, op='construct.extrude', args=None, inputs=(), span=SPAN):
    if args is None:
        args = {'distance': q(5, 'mm', 'length')}
    return ConstructionStep(id=sid, operation=op, operation_version='0.1.0',
                            args=args, inputs=inputs, span=span)


def graph(*steps):
    return ConstructionGraph(schema_version=CONSTRUCTION_GRAPH_VERSION, steps=tuple(steps))


def validate_records(graph):
    """Record-only construction validation: reserved construct.* operations are legal records."""
    return DEFAULT_REGISTRY.validate_construction(graph, require_executable=False)


def load_records(text):
    """Record-only artifact graph load (the execution gate is the default; see test_program_security_fixes)."""
    return load_artifact_graph_json(text, require_executable=False)


def code_of(fn, *a, **k):
    """Return the ProgramError raised by fn; unwraps pydantic ValidationError from validators."""
    with pytest.raises(ValueError) as ei:
        fn(*a, **k)
    exc = ei.value
    if isinstance(exc, ValidationError):
        exc = exc.errors(include_url=False)[0]['ctx']['error']
    assert isinstance(exc, ProgramError), repr(exc)
    return exc


# ------------------------------------------------------------ contents

def test_registry_contents():
    descs = DEFAULT_REGISTRY.descriptors()
    assert {d.id for d in descs} == EXPECTED_IDS
    assert len(descs) == 14
    assert DEFAULT_REGISTRY.version == REGISTRY_VERSION
    for d in descs:
        assert d.version == '0.1.0'
        if d.id.startswith('capability.'):
            assert d.executable is True
            assert d.capability_versions == ('rectangular_cantilever@1.0.0',)
        else:
            assert d.executable is False
            assert d.preconditions and d.outputs
            assert d.representations and d.backend_requirements


def test_reserved_ops_have_typed_args():
    for oid, dims in PHYSICAL.items():
        d = DEFAULT_REGISTRY.get(oid, '0.1.0')
        got = {a.name: a.dimension for a in d.args if a.kind == 'quantity'}
        assert got == dims
    boolean = DEFAULT_REGISTRY.get('construct.boolean', '0.1.0')
    op = next(a for a in boolean.args if a.name == 'operation')
    assert op.kind == 'text'
    assert set(op.allowed) == {'union', 'difference', 'intersection'}
    sweep = DEFAULT_REGISTRY.get('construct.sweep', '0.1.0')
    assert {a.name for a in sweep.args} >= {'profile', 'path'}
    assert all(a.kind == 'reference' for a in sweep.args)
    assert DEFAULT_REGISTRY.get('construct.loft', '0.1.0').args
    assert DEFAULT_REGISTRY.get('construct.sketch', '0.1.0').outputs[0].type == 'sketch'


def test_descriptors_sorted_and_deterministic():
    a = DEFAULT_REGISTRY.descriptors()
    b = DEFAULT_REGISTRY.descriptors()
    assert a == b
    assert [(d.id, d.version) for d in a] == sorted((d.id, d.version) for d in a)
    shuffled = OperationRegistry(tuple(reversed(a)))
    assert shuffled.descriptors() == a


def test_argspec_dimension_rules():
    with pytest.raises(ValueError):
        ArgSpec(name='x', kind='quantity', dimension=None)
    with pytest.raises(ValueError):
        ArgSpec(name='x', kind='text', dimension='length')


def test_descriptor_duplicate_arg_names():
    with pytest.raises(ValueError):
        OperationDescriptor(
            id='x.y', version='1', summary='s',
            args=(ArgSpec(name='a', kind='text'), ArgSpec(name='a', kind='text')),
            preconditions=('p',), outputs=(OutputSpec(name='o', type='solid'),),
            representations=('brep',), backend_requirements=('occt',),
            capability_versions=(), executable=False)


def test_duplicate_id_version_at_construction():
    d = DEFAULT_REGISTRY.get('construct.sketch', '0.1.0')
    with pytest.raises(ValueError):
        OperationRegistry((d, d))


# ------------------------------------------------------------ get / require

def test_get_unknown_op_and_version():
    assert code_of(DEFAULT_REGISTRY.get, 'construct.nope', '0.1.0').code == 'IR008'
    assert code_of(DEFAULT_REGISTRY.get, 'construct.sketch', '9.9.9').code == 'IR001'


def test_require_executable():
    assert DEFAULT_REGISTRY.require_executable('capability.build', '0.1.0').executable
    assert code_of(DEFAULT_REGISTRY.require_executable, 'construct.extrude', '0.1.0').code == 'IR009'
    assert code_of(DEFAULT_REGISTRY.require_executable, 'nope.x', '0.1.0').code == 'IR008'


# ------------------------------------------------------------ check_call

def test_check_call_valid():
    DEFAULT_REGISTRY.check_call('construct.extrude', '0.1.0', {'distance': q(5, 'mm', 'length')})
    DEFAULT_REGISTRY.check_call('construct.boolean', '0.1.0', {'operation': TextValue(kind='text', text='union')})


def test_check_call_unknown_arg():
    e = code_of(DEFAULT_REGISTRY.check_call, 'construct.extrude', '0.1.0',
                {'distance': q(5, 'mm', 'length'), 'bogus': q(1, 'mm', 'length')}, span=SPAN)
    assert e.code == 'IR002' and e.field == 'args.bogus' and e.span == SPAN


def test_check_call_missing_arg():
    e = code_of(DEFAULT_REGISTRY.check_call, 'construct.extrude', '0.1.0', {})
    assert e.code == 'IR010' and e.field == 'args.distance'


def test_check_call_wrong_dimension():
    e = code_of(DEFAULT_REGISTRY.check_call, 'construct.fillet', '0.1.0', {'radius': q(5, 'N', 'force')})
    assert e.code == 'IR006' and e.field == 'args.radius'


def test_check_call_text_for_quantity():
    e = code_of(DEFAULT_REGISTRY.check_call, 'construct.fillet', '0.1.0',
                {'radius': TextValue(kind='text', text='big')})
    assert e.code == 'IR010'


def test_check_call_text_not_allowed():
    e = code_of(DEFAULT_REGISTRY.check_call, 'construct.boolean', '0.1.0',
                {'operation': TextValue(kind='text', text='xor')}, field='steps.0.args')
    assert e.code == 'IR010' and e.field == 'steps.0.args.operation'


def test_check_call_unknown_op():
    assert code_of(DEFAULT_REGISTRY.check_call, 'zzz.q', '0.1.0', {}).code == 'IR008'


# ------------------------------------------------------------ construction

def test_validate_construction_valid_two_steps():
    g = graph(
        step('s1', 'construct.sketch', {}),
        step('s2', 'construct.extrude', inputs=('s1',)),
    )
    validate_records(g)
    validate_records(graph())


def test_validate_construction_unknown_op():
    e = code_of(validate_records, graph(step('s1', 'construct.zzz', {})))
    assert e.code == 'IR008' and e.span == SPAN and e.field.startswith('steps.0')


def test_validate_construction_forward_input():
    g = graph(step('s1', inputs=('s2',)), step('s2'))
    e = code_of(validate_records, g)
    assert e.code == 'IR004' and e.field == 'steps.0.inputs.0' and e.span == SPAN


def test_validate_construction_second_step_field():
    g = graph(step('s1'), step('s2', inputs=('s1', 'ghost')))
    e = code_of(validate_records, g)
    assert e.code == 'IR004' and e.field == 'steps.1.inputs.1'


def test_validate_construction_duplicate_id():
    e = code_of(validate_records, graph(step('s1'), step('s1')))
    assert e.code == 'IR011' and e.field == 'steps.1.id'


def test_validate_construction_reference_args_must_name_earlier_steps():
    ok = graph(step('s1', 'construct.sketch', {}), step('s2', 'construct.sketch', {}),
               step('s3', 'construct.sweep', {'profile': TextValue(kind='text', text='s1'),
                                              'path': TextValue(kind='text', text='s2')}))
    validate_records(ok)
    bad = graph(step('s1', 'construct.sketch', {}),
                step('s2', 'construct.sweep', {'profile': TextValue(kind='text', text='s1'),
                                               'path': TextValue(kind='text', text='ghost')}))
    e = code_of(validate_records, bad)
    assert (e.code, e.field, e.span) == ('IR004', 'steps.1.args.path', SPAN)


def test_validate_construction_bad_args_path():
    g = graph(step('s1', args={'radius': q(1, 'mm', 'length')}, op='construct.extrude'))
    e = code_of(validate_records, g)
    assert e.code == 'IR002' and e.field == 'steps.0.args.radius'


# ------------------------------------------------------------ OpStream / ArtifactGraph

def entry(seq, op='construct.extrude', ver='0.1.0', ref=None):
    return OpStreamEntry(seq=seq, operation=op, operation_version=ver, step_ref=ref, span=SPAN)


def stream(*entries):
    return OpStream(schema_version=OP_STREAM_VERSION, entries=tuple(entries))


def test_opstream_seq_gap():
    e = code_of(stream, entry(0), entry(2))
    assert e.code == 'IR010' and e.field == 'entries.1.seq'


def test_opstream_valid():
    s = stream(entry(0, ref='s1'), entry(1))
    assert len(s.entries) == 2


def test_artifact_graph_valid():
    g = ArtifactGraph(
        schema_version=ARTIFACT_GRAPH_VERSION, op_stream=stream(entry(0), entry(1)),
        nodes=(ArtifactNode(id='solid.a', kind='solid', op_seq=1, span=None),
               ArtifactNode(id='face.a', kind='face', op_seq=0, span=SPAN)))
    assert len(g.nodes) == 2


def test_artifact_graph_op_seq_missing():
    e = code_of(
        ArtifactGraph, schema_version=ARTIFACT_GRAPH_VERSION, op_stream=stream(entry(0)),
        nodes=(ArtifactNode(id='solid.a', kind='solid', op_seq=5, span=None),))
    assert e.code == 'IR004' and e.field == 'nodes.0.op_seq'


def test_artifact_graph_duplicate_node_id():
    e = code_of(
        ArtifactGraph, schema_version=ARTIFACT_GRAPH_VERSION, op_stream=stream(entry(0)),
        nodes=(ArtifactNode(id='n', kind='solid', op_seq=0, span=None),
               ArtifactNode(id='n', kind='edge', op_seq=0, span=None)))
    assert e.code == 'IR011' and e.field == 'nodes.1.id'


def _graph_dict():
    return {
        'schema_version': ARTIFACT_GRAPH_VERSION,
        'op_stream': {'schema_version': OP_STREAM_VERSION, 'entries': [
            {'seq': 0, 'operation': 'construct.extrude', 'operation_version': '0.1.0',
             'step_ref': None, 'span': None}]},
        'nodes': [{'id': 'solid.a', 'kind': 'solid', 'op_seq': 0, 'span': None}],
    }


def test_load_artifact_graph_json_valid():
    g = load_records(json.dumps(_graph_dict()))
    assert g.nodes[0].id == 'solid.a'
    assert load_records(json.dumps(_graph_dict()).encode()) == g


def test_load_artifact_graph_unknown_schema_version():
    d = _graph_dict()
    d['schema_version'] = 'nope'
    e = code_of(load_records, json.dumps(d))
    assert e.code == 'IR001' and e.field == 'schema_version'


def test_load_artifact_graph_duplicate_key():
    text = json.dumps(_graph_dict()).replace('"kind": "solid"', '"kind": "solid", "kind": "face"')
    e = code_of(load_records, text)
    assert e.code == 'IR003'


def test_load_artifact_graph_nan():
    text = json.dumps(_graph_dict()).replace('"op_seq": 0', '"op_seq": NaN')
    assert code_of(load_records, text).code == 'IR007'


def test_load_artifact_graph_extra_field_and_bad_seq():
    d = _graph_dict()
    d['nodes'][0]['zzz'] = 1
    e = code_of(load_records, json.dumps(d))
    assert e.code == 'IR002' and e.field == 'nodes.0.zzz'
    d = _graph_dict()
    d['nodes'][0]['op_seq'] = 9
    assert code_of(load_records, json.dumps(d)).code == 'IR004'


def test_load_artifact_graph_invalid_json_and_non_dict():
    assert code_of(load_records, '{').code == 'IR010'
    assert code_of(load_records, '[]').code == 'IR001'


def unchecked_stream(*entries):
    """Bypass the model's own registry check to exercise validate_op_stream directly."""
    return OpStream.model_construct(schema_version=OP_STREAM_VERSION, entries=tuple(entries))


def test_validate_op_stream():
    validate_op_stream(stream(entry(0), entry(1, 'capability.build')), DEFAULT_REGISTRY, require_executable=False)
    e = code_of(validate_op_stream, unchecked_stream(entry(0, 'nope.op')), DEFAULT_REGISTRY,
                require_executable=False)
    assert e.code == 'IR008' and e.field == 'entries.0.operation' and e.span == SPAN
    e = code_of(validate_op_stream, unchecked_stream(entry(0, ver='7')), DEFAULT_REGISTRY,
                require_executable=False)
    assert e.code == 'IR001'


def test_opstream_construction_enforces_the_registry():
    e = code_of(stream, entry(0, 'nope.op'))
    assert e.code == 'IR008' and e.field == 'entries.0.operation'
    assert code_of(stream, entry(0, ver='7')).code == 'IR001'
