"""N1c-1: mirum-native in kernel records v0_2, registry v0_6, the IR enum and execution lock v0_3.

No adapter, no routing: nothing here builds geometry. The frozen counterparts are in
test_program_n1c1_frozen.py.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.language.diagnostics import MelError
from engineering.language.semantic import compile_source
from engineering.program import kernel, schema_export as se
from engineering.program.adapters import adapter_for
from engineering.program.adapters.mock import MockKernelAdapter
from engineering.program.errors import ProgramError
from engineering.program.hashing import canonical_json, execution_lock_v0_2_sha256, execution_lock_v0_3_sha256
from engineering.program.ir import (
    ExecutionLock, ExecutionSelection, execution_lock_v0_2, execution_lock_v0_3, load_execution_lock_json,
    load_program_json, program_from_dict,
)
from engineering.program.kernel import (
    NATIVE_CLAIMS, NATIVE_DECLARATION, BackendDescription, BackendLock, BackendLockV0_2, BuildPlan, BuildResult,
    ExecutionLockV0_2, ExecutionLockV0_3, ExportResult, InspectResult, NativeVersion, ProbeResult,
    SupportDecision, ToleranceConvention, backend_lock, canonical_entries, check_plan, make_description, plan_for,
)
from engineering.program.planning import generator_candidates
from engineering.program.registry import (
    BAY_REGISTRY, NATIVE_REGISTRY, PLATE_REGISTRY, THERMAL_REGISTRY, OpStream, load_artifact_graph_json,
    registry_for_capability,
)
from engineering.program.replay import LOCK_TYPES
from engineering.program.revisions import ProgramRevision

REPO = Path(__file__).resolve().parents[2]
PLATE = REPO / 'backend' / 'engineering' / 'fixtures' / 'editable_plate' / 'edge.mir'
BEAM = REPO / 'backend' / 'engineering' / 'fixtures' / 'structural' / 'cantilever.mir'
V2, V1 = 'mirum_kernel_v0_2', 'mirum_kernel_v0_1'
NATIVE = 'mirum-native'
TOL = (ToleranceConvention(name='length', value=1e-6, unit='mm', source='n1c1-test'),)


def native_description(mode='native'):
    return make_description(NATIVE, mode=mode, adapter_version='native_1', tolerances=TOL)


def native_probe():
    return ProbeResult(backend=NATIVE, mode='native', available=True,
                       native_versions=(NativeVersion(name='mirum-native', version='0.1.0'),))


def native_backend_lock():
    return backend_lock(native_description(), native_probe())


@pytest.fixture(scope='module')
def plate():
    return compile_source(PLATE.read_bytes())


@pytest.fixture(scope='module')
def candidate(plate):
    return generator_candidates(plate.brief_content)[0]


# ------------------------------------------------------------------ records v0_2

def test_description_for_native_is_v0_2_with_the_plate_capability_only():
    d = native_description()
    assert d.schema_version == V2 and d.backend == NATIVE
    assert d.capabilities == ('editable_plate@1.0.0',)
    assert d.operations == ('capability.build@0.6.0', 'capability.inspect@0.6.0', 'capability.export@0.6.0')
    assert d.export_formats == ('step',)
    assert d.representations == ('brep', 'report')
    assert NATIVE_DECLARATION['capabilities'] == d.capabilities


def test_native_claim_scope_is_the_occt_reference_scope_and_pinned():
    assert dict(NATIVE_CLAIMS) == {'editable_plate@1.0.0': 'geometry_checks_only'}
    with pytest.raises(TypeError):
        NATIVE_CLAIMS['editable_plate@1.0.0'] = 'checked_design'


def test_existing_backends_still_build_v0_1_records():
    for backend in ('occt', 'picogk'):
        assert make_description(backend, mode='native', adapter_version='a').schema_version == V1


@pytest.mark.parametrize('make', [
    lambda **kw: BackendDescription(adapter_version='a', mode='native', representations=(), export_formats=(),
                                    tolerances=(), capabilities=(), operations=(), **kw),
    lambda **kw: ProbeResult(mode='native', available=True, **kw),
    lambda **kw: SupportDecision(mode='native', supported=True, **kw),
    lambda **kw: InspectResult(mode='native', outcome='consistent', artifact_sha256='0' * 64, **kw),
    lambda **kw: ExportResult(mode='native', outcome='unsupported', reasons=('export_not_supported',),
                              format='glb', **kw),
    lambda **kw: BuildResult(adapter_version='a', mode='native', capability_id='editable_plate',
                             capability_version='1.0.0', outcome='unavailable', reasons=('runtime_missing',), **kw),
], ids=['description', 'probe', 'support', 'inspect', 'export', 'build'])
def test_record_version_and_backend_must_agree(make):
    assert make(backend=NATIVE).schema_version == V2  # defaults to v0_2 for mirum-native
    assert make(backend=NATIVE, schema_version=V2).backend == NATIVE
    with pytest.raises(ValueError):
        make(backend=NATIVE, schema_version=V1)
    for backend in ('occt', 'picogk'):
        assert make(backend=backend).schema_version == V1
        with pytest.raises(ValueError):
            make(backend=backend, schema_version=V2)


def test_build_plan_record_follows_the_same_rule(candidate):
    plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('step',))
    assert plan.schema_version == V2
    with pytest.raises(ValueError):
        BuildPlan.model_validate({**plan.model_dump(mode='json'), 'schema_version': V1})
    occt = plan_for('editable_plate', '1.0.0', 'occt', candidate, ('step',))
    assert occt.schema_version == V1


def test_unknown_backend_names_are_rejected():
    with pytest.raises(ValueError):
        ProbeResult(backend='mirum_native', mode='native', available=True)


# ------------------------------------------------------------------ registry v0_6

def test_registry_v0_6_is_a_strict_superset_of_v0_5_with_native_plate_operations():
    assert NATIVE_REGISTRY.version == 'mirum_operation_registry_v0_6'
    for d in THERMAL_REGISTRY.descriptors():
        assert NATIVE_REGISTRY.get(d.id, d.version) == d
    new = [d for d in NATIVE_REGISTRY.descriptors() if d.version == '0.6.0']
    assert sorted(d.id for d in new) == ['capability.build', 'capability.export', 'capability.inspect']
    assert len(NATIVE_REGISTRY.descriptors()) == len(THERMAL_REGISTRY.descriptors()) + 3
    for d in new:
        assert d.executable and d.backend_requirements == (NATIVE,)
        assert d.capability_versions == ('editable_plate@1.0.0',)
    export = NATIVE_REGISTRY.get('capability.export', '0.6.0')
    assert [(a.name, a.allowed) for a in export.args] == [('format', ('step',))]
    for d in THERMAL_REGISTRY.descriptors():
        assert NATIVE not in d.backend_requirements


def test_registry_selection_by_capability_and_backend():
    assert registry_for_capability('editable_plate', NATIVE) is NATIVE_REGISTRY
    assert registry_for_capability('editable_plate') is PLATE_REGISTRY
    assert registry_for_capability('editable_plate', 'occt') is PLATE_REGISTRY
    assert registry_for_capability('equipment_bay', NATIVE) is BAY_REGISTRY
    assert registry_for_capability('thermal_bar', NATIVE) is THERMAL_REGISTRY


def test_registry_v0_6_export_exists_and_matches_generated():
    name = 'mirum_operation_registry_v0_6.json'
    assert name in se.generated()
    data = json.loads((se.SCHEMA_DIR / name).read_text(encoding='utf-8'))
    assert data['registry_version'] == 'mirum_operation_registry_v0_6'
    assert len(data['operations']) == len(THERMAL_REGISTRY.descriptors()) + 3


def test_canonical_entries_and_plan_use_0_6_0_only_for_native_plate(candidate):
    assert canonical_entries('editable_plate', ('step',), NATIVE) == (
        ('capability.build', '0.6.0'), ('capability.inspect', '0.6.0'), ('capability.export', '0.6.0'))
    assert canonical_entries('editable_plate', ('step',)) == (
        ('capability.build', '0.4.0'), ('capability.inspect', '0.4.0'), ('capability.export', '0.4.0'))
    assert canonical_entries('editable_plate', ('step',), 'occt')[0] == ('capability.build', '0.4.0')
    assert canonical_entries('equipment_bay', ('step',), 'occt')[0] == ('capability.build', '0.3.0')
    plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('step',))
    assert [e.operation_version for e in plan.op_stream.entries] == ['0.6.0'] * 3


def test_op_stream_and_artifact_graph_loaders_accept_v0_6_operations(candidate):
    plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('step',))
    stream = OpStream.model_validate_json(canonical_json(plan.op_stream), native=True)
    assert stream == plan.op_stream
    graph = {'schema_version': 'mirum_artifact_graph_v0_1', 'op_stream': json.loads(canonical_json(stream)),
             'nodes': []}
    assert load_artifact_graph_json(json.dumps(graph), native=True).op_stream == stream


# ------------------------------------------------------------------ plan support

def test_native_plate_plan_is_supported_by_the_native_description(candidate):
    plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('step',))
    decision = check_plan(native_description(), plan)
    assert decision.supported and decision.backend == NATIVE and decision.schema_version == V2


def test_native_and_occt_plans_never_cross(candidate):
    native_plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('step',))
    occt_plan = plan_for('editable_plate', '1.0.0', 'occt', candidate, ('step',))
    occt_desc = make_description('occt', mode='native', adapter_version='a')
    assert check_plan(occt_desc, native_plan).reasons == ('backend_mismatch',)
    assert check_plan(native_description(), occt_plan).reasons == ('backend_mismatch',)


@pytest.mark.parametrize('capability', ['equipment_bay', 'thermal_bar', 'rectangular_cantilever', 'regen_chamber'])
def test_native_plan_for_another_capability_is_not_supported_by_the_native_backend(capability, candidate):
    version = {'rectangular_cantilever': '1.0.0'}.get(capability, '1.0.0')
    # The candidate is irrelevant: support fails before the candidate is inspected.
    plan = plan_for(capability, version, NATIVE, candidate, ('step',))
    decision = check_plan(native_description(), plan)
    assert not decision.supported
    assert decision.reasons == ('capability_not_supported_by_backend',)


def test_native_plan_for_plate_with_glb_is_export_not_supported(candidate):
    plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('glb',))
    assert check_plan(native_description(), plan).reasons == ('export_not_supported',)


# ------------------------------------------------------------------ IR enum and no fallback

def test_adapter_for_native_is_never_occt_and_never_completes(candidate):
    native = adapter_for(NATIVE)
    assert native.backend == NATIVE
    assert 'occt' not in type(native).__module__
    probe = asyncio.run(native.probe())
    assert not probe.available and probe.reasons == ('runtime_missing',) and probe.backend == NATIVE
    plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('step',))
    ctx = kernel.ExecutionContext(deadline_s=5.0, max_artifact_bytes=1024, workspace='n1c1')
    built = asyncio.run(native.build(plan, ctx))
    assert built.result.outcome == 'unavailable' and built.result.reasons == ('runtime_missing',)
    assert built.result.backend == NATIVE and built.artifacts == ()
    assert native.describe().backend == NATIVE
    assert native.export(None, 'step').outcome == 'unsupported'  # nothing was built, nothing exports
    unsupported = asyncio.run(native.build(plan_for('equipment_bay', '1.0.0', NATIVE, candidate, ('step',)), ctx))
    assert unsupported.result.outcome == 'unsupported'
    assert unsupported.result.reasons == ('capability_not_supported_by_backend',)


def test_mock_adapter_for_native_is_planned_only(candidate):
    mock = adapter_for(NATIVE, mode='mock')
    assert isinstance(mock, MockKernelAdapter) and mock.backend == NATIVE
    probe = asyncio.run(mock.probe())
    assert (probe.available, probe.reasons, probe.schema_version) == (False, ('mock_backend',), V2)
    assert mock.describe().schema_version == V2


def test_unknown_backends_are_still_refused():
    with pytest.raises(ValueError):
        adapter_for('mirum_native')


def _ir_dict():
    return json.loads(canonical_json(compile_source(PLATE.read_bytes()).ir))


def test_ir_accepts_the_additive_enum_value_and_still_rejects_others():
    raw = _ir_dict()
    raw['execution']['backend'] = NATIVE
    ir = program_from_dict(raw)
    assert ir.execution.backend == NATIVE
    assert load_program_json(canonical_json(ir)).execution.backend == NATIVE
    raw['execution']['backend'] = 'mirum_native'
    with pytest.raises(ProgramError):
        program_from_dict(raw)
    assert ExecutionSelection(backend=NATIVE, exports=('step',)).backend == NATIVE


def test_mel_accepts_mirum_native_for_the_plate_only_since_n1c_2():
    source = PLATE.read_bytes().replace(b'backend = "occt"', b'backend = "mirum-native"')
    assert source != PLATE.read_bytes()
    assert compile_source(source).ir.execution.backend == NATIVE
    beam = BEAM.read_bytes().replace(b'backend = "occt"', b'backend = "mirum-native"')
    with pytest.raises(MelError) as exc:
        compile_source(beam)
    assert 'MEL-S012' in str(exc.value)


# ------------------------------------------------------------------ lock v0_3

def _native_ir(plate):
    raw = _ir_dict()
    raw['execution']['backend'] = NATIVE
    return program_from_dict(raw)


def test_lock_v0_3_fields_and_registry(plate):
    ir = _native_ir(plate)
    bl = native_backend_lock()
    assert isinstance(bl, BackendLockV0_2) and bl.schema_version == V2
    lock = execution_lock_v0_3(ir, bl)
    assert isinstance(lock, ExecutionLockV0_3)
    assert lock.schema_version == 'mirum_execution_lock_v0_3'
    assert lock.backend == NATIVE and lock.registry_version == 'mirum_operation_registry_v0_6'
    assert lock.backend_lock == bl
    assert execution_lock_v0_3_sha256(lock) == execution_lock_v0_2_sha256(lock)
    assert load_execution_lock_json(canonical_json(lock)) == lock
    assert type(load_execution_lock_json(canonical_json(lock))) is ExecutionLockV0_3
    assert ExecutionLockV0_3 in LOCK_TYPES and ExecutionLockV0_2 in LOCK_TYPES


def test_lock_v0_3_cannot_carry_occt_or_picogk(plate):
    lock = json.loads(canonical_json(execution_lock_v0_3(_native_ir(plate), native_backend_lock())))
    for backend in ('occt', 'picogk'):
        top = {**lock, 'backend': backend}
        with pytest.raises(ValueError):
            ExecutionLockV0_3.model_validate(top)
        with pytest.raises(ValueError):
            load_execution_lock_json(json.dumps(top))
        both = {**lock, 'backend': backend, 'backend_lock': {**lock['backend_lock'], 'backend': backend}}
        with pytest.raises(ValueError):
            ExecutionLockV0_3.model_validate(both)
        with pytest.raises(ValueError):
            BackendLockV0_2.model_validate({**lock['backend_lock'], 'backend': backend})


def test_v0_1_and_v0_2_locks_cannot_carry_mirum_native(plate):
    ir = _native_ir(plate)
    with pytest.raises(ValueError):
        execution_lock_v0_2(ir, native_backend_lock())  # the v0_2 builder wants a v0_1 BackendLock
    from engineering.program.ir import execution_lock
    with pytest.raises(ValueError):
        execution_lock(ir)
    lock3 = json.loads(canonical_json(execution_lock_v0_3(ir, native_backend_lock())))
    as_v2 = {**lock3, 'schema_version': 'mirum_execution_lock_v0_2'}
    with pytest.raises(ValueError):
        ExecutionLockV0_2.model_validate(as_v2)
    with pytest.raises(ValueError):
        load_execution_lock_json(json.dumps(as_v2))
    as_v1 = {k: lock3[k] for k in ExecutionLock.model_fields}
    as_v1['schema_version'] = 'mirum_execution_lock_v0_1'
    with pytest.raises(ValueError):
        load_execution_lock_json(json.dumps(as_v1))
    v1_bl = {**lock3['backend_lock'], 'schema_version': V1}
    with pytest.raises(ValueError):
        BackendLock.model_validate(v1_bl)


def test_lock_v0_3_is_refused_for_other_capabilities_and_mock_locks(plate):
    raw = _ir_dict()
    raw['capability']['id'] = 'equipment_bay'
    raw['execution']['backend'] = NATIVE
    with pytest.raises(ValueError):
        execution_lock_v0_3(program_from_dict(raw), native_backend_lock())
    mock_probe = ProbeResult(backend=NATIVE, mode='mock', available=False, reasons=('mock_backend',))
    with pytest.raises(ValueError):
        backend_lock(native_description('mock'), mock_probe)
    with pytest.raises(ValueError):
        execution_lock_v0_3(_native_ir(plate), BackendLock.model_construct())


def test_program_revision_lock_is_a_union_discriminated_on_schema_version():
    field = ProgramRevision.model_fields['lock']
    text = repr(field.annotation)
    assert 'ExecutionLockV0_2' in text and 'ExecutionLockV0_3' in text
    schema = json.loads((se.SCHEMA_DIR / 'mirum_program_revision_v0_1.schema.json').read_text(encoding='utf-8'))
    refs = {o['$ref'].rsplit('/', 1)[1] for o in schema['properties']['lock']['oneOf']}
    assert refs == {'ExecutionLockV0_2', 'ExecutionLockV0_3'}
    assert schema['properties']['lock']['discriminator']['propertyName'] == 'schema_version'


def test_beam_ir_with_native_backend_is_not_lockable_as_v0_3():
    raw = json.loads(canonical_json(compile_source(BEAM.read_bytes()).ir))
    raw['execution']['backend'] = NATIVE
    with pytest.raises(ValueError):
        execution_lock_v0_3(program_from_dict(raw), native_backend_lock())


# ------------------------------------------------------------------ schema diff is additive only

def test_ir_schema_gained_exactly_the_enum_value():
    schema = json.loads((se.SCHEMA_DIR / 'mirum_program_ir_v0_1.schema.json').read_text(encoding='utf-8'))
    assert schema['$defs']['ExecutionSelection']['properties']['backend']['enum'] == [
        'occt', 'picogk', 'mirum-native']


REGISTRY_V0_6_EXPORT_SHA256 = '37cff71e789967ebce931d73cb0eda293163af7586ab57f819038138da518547'


def test_registry_v0_6_export_bytes_are_pinned():
    import hashlib
    raw = (se.SCHEMA_DIR / 'mirum_operation_registry_v0_6.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == REGISTRY_V0_6_EXPORT_SHA256
