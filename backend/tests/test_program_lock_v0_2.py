"""F05: ExecutionLock v0_2 (folds in the F03 BackendLock), its hash and the versioned lock loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering.language.semantic import compile_source
from engineering.program import kernel
from engineering.program.errors import ProgramError
from engineering.program.hashing import execution_lock_sha256, execution_lock_v0_2_sha256
from engineering.program.ir import (
    ExecutionLock, execution_lock, execution_lock_v0_2, load_execution_lock_json,
)
from engineering.program.kernel import (
    ExecutionLockV0_2, NativeVersion, ProbeResult, ToleranceConvention, backend_lock, make_description,
)
from engineering.program.versions import (
    EXECUTION_LOCK_VERSION, EXECUTION_LOCK_VERSION_V0_2, MEL_COMPILER_VERSION, MEL_LANGUAGE_VERSION,
    REGISTRY_VERSION,
)

P03 = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'p03'
V0_1_BEAM_LOCK_SHA = '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e'


@pytest.fixture(scope='module')
def ir():
    return compile_source((P03 / 'beam.mir').read_bytes()).ir


def native_lock(versions=(('cadquery', '2.6.1'), ('OCP', '7.8.1')), tolerances=(), backend='occt'):
    desc = make_description(backend, mode='native', adapter_version='adapter_1', tolerances=tolerances)
    probe = ProbeResult(backend=backend, mode='native', available=True,
                        native_versions=tuple(NativeVersion(name=n, version=v) for n, v in versions))
    return backend_lock(desc, probe)


TOL = (ToleranceConvention(name='length', value=1e-3, unit='mm', source='test'),)


def test_version_constants():
    assert EXECUTION_LOCK_VERSION == 'mirum_execution_lock_v0_1'
    assert EXECUTION_LOCK_VERSION_V0_2 == 'mirum_execution_lock_v0_2'


def test_v0_2_lock_fields(ir):
    bl = native_lock()
    lock = execution_lock_v0_2(ir, bl)
    old = execution_lock(ir)
    assert isinstance(lock, ExecutionLockV0_2)
    assert lock.schema_version == EXECUTION_LOCK_VERSION_V0_2
    assert lock.registry_version == REGISTRY_VERSION
    assert lock.language_version == MEL_LANGUAGE_VERSION
    assert lock.compiler_version == MEL_COMPILER_VERSION
    assert lock.backend_lock == bl
    for f in ('program_ir_version', 'semantics_version', 'capability_id', 'capability_version',
              'numerical_policy_version', 'resource_policy_version', 'backend', 'exports'):
        assert getattr(lock, f) == getattr(old, f), f


def test_v0_1_lock_and_hash_unchanged(ir):
    assert execution_lock_sha256(ir) == V0_1_BEAM_LOCK_SHA
    lock = execution_lock(ir)
    assert isinstance(lock, ExecutionLock)
    assert lock.schema_version == EXECUTION_LOCK_VERSION
    assert execution_lock_sha256(lock) == V0_1_BEAM_LOCK_SHA


def test_hash_deterministic_and_sensitive(ir):
    lock = execution_lock_v0_2(ir, native_lock(tolerances=TOL))
    h = execution_lock_v0_2_sha256(lock)
    assert len(h) == 64
    assert h == execution_lock_v0_2_sha256(execution_lock_v0_2(ir, native_lock(tolerances=TOL)))
    assert h != V0_1_BEAM_LOCK_SHA
    variants = {
        'native_versions': lock.model_copy(update={'backend_lock': native_lock(
            versions=(('cadquery', '2.7.0'), ('OCP', '7.8.1')), tolerances=TOL)}),
        'tolerances': lock.model_copy(update={'backend_lock': native_lock()}),
        'compiler_version': lock.model_copy(update={'compiler_version': 'other_compiler'}),
        'numerical': lock.model_copy(update={'numerical_policy_version': 'other_policy'}),
        'exports': lock.model_copy(update={'exports': ('step',)}),
    }
    assert tuple(lock.exports) != ('step',)
    hashes = {k: execution_lock_v0_2_sha256(v) for k, v in variants.items()}
    for k, v in hashes.items():
        assert v != h, k
    assert len(set(hashes.values())) == len(hashes)


def test_mock_cannot_produce_lock(ir):
    desc = make_description('occt', mode='mock', adapter_version='adapter_1')
    mock_lock = kernel.BackendLock(
        backend='occt', adapter_version='adapter_1', mode='mock', native_versions=(),
        representations=desc.representations, export_formats=desc.export_formats, tolerances=())
    with pytest.raises(ValueError):
        execution_lock_v0_2(ir, mock_lock)
    with pytest.raises(ValueError):
        execution_lock_v0_2(ir, 'not a lock')  # type: ignore[arg-type]
    good = execution_lock_v0_2(ir, native_lock())
    with pytest.raises(ValueError):
        ExecutionLockV0_2(**{**good.model_dump(), 'backend_lock': mock_lock.model_dump()})


def test_backend_mismatch_and_export_outside_formats(ir):
    with pytest.raises(ValueError):
        execution_lock_v0_2(ir, native_lock(versions=(('picogk_runtime_so_sha256', 'a'),), backend='picogk'))
    good = execution_lock_v0_2(ir, native_lock())
    data = good.model_dump()
    assert 'step' in good.exports
    data['backend_lock']['export_formats'] = ('glb',)
    with pytest.raises(ValueError):
        ExecutionLockV0_2(**data)
    data = good.model_dump()
    data['exports'] = tuple(good.exports) + ('zip',)
    with pytest.raises(ValueError):
        ExecutionLockV0_2(**data)


def test_loader_round_trip_both_versions(ir):
    v1 = execution_lock(ir)
    v2 = execution_lock_v0_2(ir, native_lock(tolerances=TOL))
    assert load_execution_lock_json(v1.model_dump_json()) == v1
    assert load_execution_lock_json(v2.model_dump_json().encode('utf-8')) == v2
    assert type(load_execution_lock_json(v1.model_dump_json())) is ExecutionLock
    assert type(load_execution_lock_json(v2.model_dump_json())) is ExecutionLockV0_2


def test_loader_refusals(ir):
    v2 = execution_lock_v0_2(ir, native_lock())
    good = json.loads(v2.model_dump_json())

    def code(text):
        with pytest.raises(ProgramError) as e:
            load_execution_lock_json(text)
        return e.value.code

    assert code(json.dumps({**good, 'schema_version': 'mirum_execution_lock_v0_9'})) == 'IR001'
    no_version = {k: v for k, v in good.items() if k != 'schema_version'}
    assert code(json.dumps(no_version)) == 'IR001'
    assert code('[1, 2]') == 'IR001'
    assert code(json.dumps({**good, 'surprise': 1})) == 'IR002'
    dup = v2.model_dump_json().replace('{', '{"backend": "occt", ', 1)
    assert code(dup) == 'IR003'
    mock_json = json.loads(v2.model_dump_json())
    mock_json['backend_lock']['mode'] = 'mock'
    assert code(json.dumps(mock_json)) == 'IR010'
    assert code('{"schema_version": "mirum_execution_lock_v0_2", "pad": "' + 'x' * 70000 + '"}') == 'IR012'
