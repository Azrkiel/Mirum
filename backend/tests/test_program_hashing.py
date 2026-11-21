"""F02 T1: the four independent hashes and canonical JSON."""
from __future__ import annotations

import hashlib
import json
import math

import pytest

from cem.core import canonical_json as cem_canonical_json
from engineering.program import hashing
from engineering.program.errors import ProgramError
from engineering.program.hashing import (
    artifact_sha256, canonical_json, execution_lock_sha256, semantic_payload,
    semantic_sha256, source_sha256,
)
from engineering.program.ir import ProgramIR, execution_lock, load_program_json

from tests.test_program_ir import make_dict, make_program


def edit(fn) -> ProgramIR:
    d = make_dict()
    fn(d)
    return load_program_json(json.dumps(d))


def test_module_docstring_documents_rule():
    doc = hashing.__doc__
    assert "UTF-8 of json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)" in doc
    assert 'repr' in doc and '-0.0' in doc and 'IR007' in doc


def test_canonical_json_matches_cem_core():
    value = {'b': [1, 2.5, 'é'], 'a': {'z': 1.0, 'y': None, 'x': True}, 't': (1, 2)}
    assert canonical_json(value) == cem_canonical_json(value)
    assert canonical_json(make_program()) == cem_canonical_json(make_program())


def test_canonical_json_rejects_nonfinite():
    for bad in (float('nan'), float('inf'), {'a': [float('-inf')]}):
        with pytest.raises(ProgramError) as ei:
            canonical_json(bad)
        assert ei.value.code == 'IR007'


def test_canonical_json_negative_zero():
    assert canonical_json({'a': -0.0}) == '{"a":0.0}'
    assert canonical_json([-0.0, 0.0]) == '[0.0,0.0]'


def test_source_sha256():
    assert source_sha256('abc') == hashlib.sha256(b'abc').hexdigest()
    assert source_sha256('é') == hashlib.sha256('é'.encode()).hexdigest()
    assert source_sha256(b'\x00\x01') == hashlib.sha256(b'\x00\x01').hexdigest()
    d = {'b': 1, 'a': [1, 2]}
    assert source_sha256(d) == hashlib.sha256(b'{"a":[1,2],"b":1}').hexdigest()
    assert source_sha256({'a': [1, 2], 'b': 1}) == source_sha256(d)
    assert source_sha256([1, 2]) == hashlib.sha256(b'[1,2]').hexdigest()


def test_artifact_sha256_bytes_only():
    assert artifact_sha256(b'xyz') == hashlib.sha256(b'xyz').hexdigest()
    with pytest.raises(TypeError):
        artifact_sha256('xyz')


def test_semantic_payload_shape():
    p = semantic_payload(make_program())
    assert set(p) == {'semantics_version', 'capability', 'requirements', 'candidates',
                      'analysis', 'evidence', 'construction'}
    assert p['capability'] == {'id': 'demo_cap', 'version': '1.0.0'}
    assert [r['id'] for r in p['requirements']] == ['part.length', 'part.note']
    assert p['requirements'][0]['value'] == {'kind': 'quantity', 'dimension': 'length',
                                             'value': 100.0, 'unit': 'mm'}
    assert 'span' not in json.dumps(p)
    assert p['construction'] == []
    assert p['candidates']['mode'] == 'sweep'
    assert p['analysis'][0]['requirement_refs'] == ['part.height', 'part.length']


def test_hashes_are_hex_and_stable():
    ir = make_program()
    assert len(semantic_sha256(ir)) == 64
    assert semantic_sha256(ir) == semantic_sha256(load_program_json(ir.model_dump_json()))
    assert execution_lock_sha256(ir) == execution_lock_sha256(execution_lock(ir))


def test_span_provenance_design_do_not_change_semantic_hash():
    base = make_program()
    def f(d):
        d['requirements']['requirements'][0]['span'] = {'line': 9, 'column': 9, 'end_line': 9, 'end_column': 9}
        d['provenance']['source_sha256'] = 'b' * 64
        d['provenance']['kind'] = 'ai'
        d['design'] = 'renamed_design'
    other = edit(f)
    assert other != base
    assert semantic_sha256(other) == semantic_sha256(base)
    assert execution_lock_sha256(other) == execution_lock_sha256(base)


@pytest.mark.parametrize('mutate', [
    lambda d: d['execution'].update(backend='other'),
    lambda d: d['execution'].update(exports=['step']),
    lambda d: d['capability'].update(numerical_policy_version='np2'),
    lambda d: d['capability'].update(resource_policy_version='rp2'),
])
def test_execution_changes_only_lock_hash(mutate):
    base = make_program()
    other = edit(mutate)
    assert semantic_sha256(other) == semantic_sha256(base)
    assert execution_lock_sha256(other) != execution_lock_sha256(base)


def test_requirement_value_changes_semantic_hash():
    base = make_program()
    other = edit(lambda d: d['requirements']['requirements'][0]['value'].update(value=101.0))
    assert semantic_sha256(other) != semantic_sha256(base)
    assert execution_lock_sha256(other) == execution_lock_sha256(base)


def test_candidate_value_changes_semantic_hash():
    base = make_program()
    other = edit(lambda d: d['candidates']['parameters'][0]['values'][1].update(value=7.0))
    assert semantic_sha256(other) != semantic_sha256(base)


def test_requirement_order_permutation_same_semantic_hash():
    base = make_program()
    def f(d):
        d['requirements']['requirements'].reverse()
    other = edit(f)
    assert other != base
    assert semantic_sha256(other) == semantic_sha256(base)


def test_lock_hash_matches_canonical_lock():
    ir = make_program()
    lock = execution_lock(ir)
    expected = hashlib.sha256(cem_canonical_json(lock).encode()).hexdigest()
    assert execution_lock_sha256(lock) == expected


def test_nan_never_hashes():
    with pytest.raises(ProgramError):
        source_sha256({'a': math.nan})
