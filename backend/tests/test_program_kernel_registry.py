"""F03 T2: registry v0_2 adds executable regen capability operations; v0_1 content is unchanged."""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from engineering.program.errors import ProgramError
from engineering.program.hashing import execution_lock_sha256, semantic_sha256
from engineering.program.ir import TextValue, execution_lock
from engineering.program.registry import (
    DEFAULT_REGISTRY, REGISTRY_V0_1, OpStream, OpStreamEntry, validate_op_stream,
)
from engineering.program.versions import REGISTRY_VERSION, REGISTRY_VERSION_V0_1

from tests.test_program_beam_bridge import form_program

V0_1_SHA256 = 'e7f847c66f827266e25f18d2db0dfda1a32a6bcec4cea10c4f9ac5bfe1f82c11'
REGEN_KEYS = [('capability.build', '0.2.0'), ('capability.inspect', '0.2.0'), ('capability.export', '0.2.0')]


def _err(fn, *a, **k) -> ProgramError:
    with pytest.raises(ValueError) as ei:
        fn(*a, **k)
    exc = ei.value
    if isinstance(exc, ValidationError):
        exc = exc.errors(include_url=False)[0]['ctx']['error']
    assert isinstance(exc, ProgramError), repr(exc)
    return exc


def test_v0_1_descriptors_are_byte_identical():
    blob = json.dumps([d.model_dump(mode='json') for d in REGISTRY_V0_1.descriptors()],
                      sort_keys=True, separators=(',', ':')).encode()
    assert hashlib.sha256(blob).hexdigest() == V0_1_SHA256
    assert REGISTRY_V0_1.version == REGISTRY_VERSION_V0_1


def test_default_registry_contains_every_v0_1_descriptor():
    assert DEFAULT_REGISTRY.version == REGISTRY_VERSION
    for d in REGISTRY_V0_1.descriptors():
        assert DEFAULT_REGISTRY.get(d.id, d.version) == d


def test_regen_descriptors():
    for op, ver in REGEN_KEYS:
        d = DEFAULT_REGISTRY.require_executable(op, ver)
        assert d.executable and d.version == '0.2.0'
        assert d.capability_versions == ('regen_chamber@1.0.0',)
        assert d.backend_requirements == ('capability_runtime',)
        assert any('geometry-only' in p for p in d.preconditions)
    build = DEFAULT_REGISTRY.get('capability.build', '0.2.0')
    assert [(o.name, o.type) for o in build.outputs] == [('geometry', 'solid')]
    assert build.representations == ('voxel', 'mesh')
    inspect = DEFAULT_REGISTRY.get('capability.inspect', '0.2.0')
    assert [(o.name, o.type) for o in inspect.outputs] == [('measurements', 'measurement')]
    assert inspect.representations == ('report',)
    export = DEFAULT_REGISTRY.get('capability.export', '0.2.0')
    assert [(o.name, o.type) for o in export.outputs] == [('file', 'file')]
    assert export.representations == ('file',)
    assert [(a.name, a.kind, a.allowed) for a in export.args] == [('format', 'text', ('stl',))]
    DEFAULT_REGISTRY.check_call('capability.export', '0.2.0', {'format': TextValue(kind='text', text='stl')})
    e = _err(DEFAULT_REGISTRY.check_call, 'capability.export', '0.2.0',
             {'format': TextValue(kind='text', text='step')})
    assert e.code == 'IR010'
    # no regen analyze
    assert _err(DEFAULT_REGISTRY.get, 'capability.analyze', '0.2.0').code == 'IR001'


def test_construct_ops_still_reserved_in_both_registries():
    for reg in (REGISTRY_V0_1, DEFAULT_REGISTRY):
        assert _err(reg.require_executable, 'construct.extrude', '0.1.0').code == 'IR009'
        assert _err(reg.require_executable, 'construct.boolean', '0.1.0').code == 'IR009'


def test_op_stream_with_regen_build_validates():
    stream = OpStream(schema_version='mirum_op_stream_v0_1', entries=(
        OpStreamEntry(seq=0, operation='capability.build', operation_version='0.2.0'),
        OpStreamEntry(seq=1, operation='capability.export', operation_version='0.2.0'),
    ))
    validate_op_stream(stream, DEFAULT_REGISTRY)
    bad = _err(validate_op_stream, stream, REGISTRY_V0_1)
    assert bad.code == 'IR001'


def test_execution_lock_registry_version_changes_semantic_hash_does_not():
    ir = form_program()
    assert execution_lock(ir).registry_version == 'mirum_operation_registry_v0_2'
    assert semantic_sha256(ir).startswith('4d81808ade568a03')
    new = execution_lock_sha256(ir)
    assert not new.startswith('b7001e7004009a1e')
