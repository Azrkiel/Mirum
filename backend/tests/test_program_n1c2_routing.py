"""N1c-2: (capability, backend) routing, no OCCT fallback, MEL acceptance, negative table, edit and delivered rules."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering.jobs import Admitted, JobController, UnavailableCapability
from engineering.language.diagnostics import MelError
from engineering.language.semantic import compile_source
from engineering.program.errors import ProgramError
from engineering.program.hashing import canonical_json, semantic_sha256
from engineering.program.kernel import check_plan, make_description, plan_for
from engineering.program.planning import generator_candidates
from engineering.program.registry import OpStream, load_artifact_graph_json
from tests.test_program_interface_api import ALICE, light, new_program, run  # noqa: F401  (light: fixture)

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / 'backend' / 'engineering' / 'fixtures'
PLATE = (FIX / 'editable_plate' / 'edge.mir').read_bytes()
NATIVE = 'mirum-native'


def native_source(source=PLATE):
    return source.replace(b'backend = "occt"', b'backend = "mirum-native"')


def brief_for(capability='editable_plate'):
    return SimpleNamespace(content=SimpleNamespace(capability=SimpleNamespace(id=capability)))


class Boom:
    def __init__(self, *a, **k):
        raise AssertionError('the OCCT path was constructed for a native revision')


# ------------------------------------------------------------------ routing without fallback

def make_controller(tmp_path, **kwargs):
    from engineering.store import SQLiteStore
    return SQLiteStore(tmp_path), kwargs


def test_adapter_for_routes_by_capability_and_backend(tmp_path):
    from engineering.store import SQLiteStore
    store = SQLiteStore(tmp_path)
    occt, native = object(), object()
    jobs = JobController(store, generators={'editable_plate': occt, ('editable_plate', NATIVE): native})
    assert jobs._adapter_for(brief_for(), 'occt') is occt
    assert jobs._adapter_for(brief_for()) is occt
    assert jobs._adapter_for(brief_for(), NATIVE) is native
    asyncio.run(jobs.close())
    store.close()


def test_missing_native_generator_is_unavailable_never_occt(tmp_path):
    from engineering.store import SQLiteStore
    store = SQLiteStore(tmp_path)
    jobs = JobController(store, generators={'editable_plate': Boom})  # only the OCCT generator is registered
    with pytest.raises(UnavailableCapability):
        jobs._adapter_for(brief_for(), NATIVE)
    # capability-keyed entries are never consulted for native, even for a capability with the beam adapter
    with pytest.raises(UnavailableCapability):
        jobs._adapter_for(brief_for('rectangular_cantilever'), NATIVE)
    asyncio.run(jobs.close())
    store.close()


def test_admitted_carries_the_backend_and_defaults_to_occt():
    assert Admitted(1, 2, 3).backend == 'occt'
    assert Admitted(1, 2, 3, NATIVE).backend == NATIVE


def test_native_generator_never_constructs_the_occt_generator_or_adapter(monkeypatch):
    import engineering.editable_plate_adapter as epa
    import engineering.program.adapters.native as native
    import engineering.program.adapters.occt as occt

    built = []
    monkeypatch.setattr(occt.OcctAdapter, '__init__', Boom.__init__)
    real_init = epa.EditablePlateAdapter.__init__

    def spy_init(self, backend='occt'):
        built.append(backend)
        real_init(self, backend)

    monkeypatch.setattr(epa.EditablePlateAdapter, '__init__', spy_init)
    seen = []

    class FakeNative:
        def __init__(self, **kw):
            seen.append(kw)

        async def build(self, plan, ctx):
            seen.append(plan.backend)
            from engineering.program.kernel import Built, BuildResult
            return Built(BuildResult(backend=NATIVE, adapter_version='x', mode='native', capability_id='editable_plate',
                                     capability_version='1.0.0', outcome='unavailable', reasons=('runtime_missing',)))

    monkeypatch.setattr(native, 'NativePlateAdapter', FakeNative)
    generator = epa.EditablePlateAdapter(backend=NATIVE)
    candidate = generator_candidates(compile_source(native_source()).brief_content)[0]
    from engineering.service import EngineeringUnavailable
    with pytest.raises(EngineeringUnavailable):
        asyncio.run(generator.evaluate(candidate))
    assert built == [NATIVE] and seen[-1] == NATIVE  # one generator, native plan, OCCT adapter never built


def test_occt_generator_default_is_unchanged(monkeypatch):
    import engineering.editable_plate_adapter as epa
    assert epa.EditablePlateAdapter().backend == 'occt'
    with pytest.raises(ValueError):
        epa.EditablePlateAdapter(backend='picogk')


def test_programs_runtime_registers_native_under_its_own_key():
    import inspect
    import engineering.api_programs as api_programs
    source = inspect.getsource(api_programs.create_program_runtime)
    assert "(PLATE_ID, 'mirum-native')" in source and "EditablePlateAdapter(backend='mirum-native')" in source


def test_native_run_passes_backend_to_admit_only_for_native():
    import inspect
    import engineering.program.transactions as tx
    source = inspect.getsource(tx.RevisionService._admit_link_start)
    assert "'backend': 'mirum-native'" in source and '**extra' in source


# ------------------------------------------------------------------ MEL and the IR

def test_mel_accepts_native_for_editable_plate_and_hashes_match_occt():
    occt = compile_source(PLATE)
    native = compile_source(native_source())
    assert native.ir.execution.backend == NATIVE and occt.ir.execution.backend == 'occt'
    assert native.semantic_sha256 == occt.semantic_sha256
    assert native.brief_sha256 == occt.brief_sha256
    assert native.source_sha256 != occt.source_sha256


@pytest.mark.parametrize('path', ['structural/cantilever.mir', 'thermal/bar.mir', 'equipment_bay/nominal.mir'])
def test_mel_s012_for_native_on_other_capabilities(path):
    candidates = [FIX / path]
    assert candidates, path
    source = candidates[0].read_bytes()
    assert b'backend = "occt"' in source
    with pytest.raises(MelError) as error:
        compile_source(source.replace(b'backend = "occt"', b'backend = "mirum-native"'))
    assert error.value.diagnostic.code == 'MEL-S012'


def test_mel_s012_for_native_on_regen():
    sources = [FIX / 'picogk' / 'reference.mir']
    assert sources
    with pytest.raises(MelError) as error:
        compile_source(sources[0].read_bytes().replace(b'backend = "picogk"', b'backend = "mirum-native"'))
    assert error.value.diagnostic.code == 'MEL-S012'


def test_bridge_round_trip_keeps_the_backend():
    from engineering.program.editable_plate_bridge import content_from_program, content_for, program_for
    native = compile_source(native_source())
    assert content_from_program(native.ir) == native.brief_content
    rebuilt = program_for(native.brief_content, native.ir.design, native.ir.provenance, backend=NATIVE)
    assert rebuilt.execution.backend == NATIVE
    assert program_for(native.brief_content, native.ir.design, native.ir.provenance).execution.backend == 'occt'


# ------------------------------------------------------------------ negative table

def native_description():
    return make_description(NATIVE, mode='native', adapter_version='t')


def occt_plate_description():
    from engineering.editable_plate_adapter import describe
    return describe()


@pytest.mark.parametrize('capability,source', [
    ('equipment_bay', FIX / 'equipment_bay'), ('thermal_bar', FIX / 'thermal'),
    ('rectangular_cantilever', FIX / 'structural')])
def test_native_with_other_capabilities_is_not_supported(capability, source):
    sources = sorted(source.glob('*.mir'))
    compiled = compile_source(sources[0].read_bytes())
    if capability == 'rectangular_cantilever':
        from engineering.program.planning import _program_candidates
        candidate = _program_candidates(compiled.brief_content)[0]
    else:
        candidate = generator_candidates(compiled.brief_content)[0]
    plan = plan_for(capability, '1.0.0', NATIVE, candidate, ('step',))
    decision = check_plan(native_description(), plan)
    assert not decision.supported and decision.reasons in (
        ('capability_not_supported_by_backend',), ('operation_not_supported',)), decision


def test_backend_mismatch_both_ways():
    candidate = generator_candidates(compile_source(PLATE).brief_content)[0]
    native_plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('step',))
    occt_plan = plan_for('editable_plate', '1.0.0', 'occt', candidate, ('step',))
    assert check_plan(native_description(), occt_plan).reasons == ('backend_mismatch',)
    assert check_plan(occt_plate_description(), native_plan).reasons == ('backend_mismatch',)
    assert check_plan(native_description(), native_plan).supported
    assert check_plan(occt_plate_description(), occt_plan).supported


def test_native_op_versions_are_not_accepted_for_occt_plans():
    candidate = generator_candidates(compile_source(PLATE).brief_content)[0]
    native_plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('step',))
    forged = native_plan.model_copy(update={'backend': 'occt'})
    assert not check_plan(occt_plate_description(), forged).supported


# ------------------------------------------------------------------ loaders (security review LOW 3)

def test_loaders_reject_native_ops_unless_native():
    candidate = generator_candidates(compile_source(native_source()).brief_content)[0]
    plan = plan_for('editable_plate', '1.0.0', NATIVE, candidate, ('step',))
    text = canonical_json(plan.op_stream)
    with pytest.raises(ProgramError):
        OpStream.model_validate_json(text)
    assert OpStream.model_validate_json(text, native=True) == plan.op_stream
    graph = json.dumps({'schema_version': 'mirum_artifact_graph_v0_1', 'op_stream': json.loads(text), 'nodes': []})
    with pytest.raises(ProgramError):
        load_artifact_graph_json(graph)
    assert load_artifact_graph_json(graph, native=True).op_stream == plan.op_stream
    occt_plan = plan_for('editable_plate', '1.0.0', 'occt', candidate, ('step',))
    assert OpStream.model_validate_json(canonical_json(occt_plan.op_stream)) == occt_plan.op_stream


# ------------------------------------------------------------------ planning annotation (LOW 1)

def test_run_authorization_backend_lock_annotation_names_both_lock_types():
    from engineering.program.planning import RunAuthorization
    text = str(RunAuthorization.__annotations__['backend_lock'])
    assert 'BackendLockV0_2' in text and 'BackendLock' in text


# ------------------------------------------------------------------ delivered (LOW 2)

def test_delivered_native_lock_is_explicitly_out_of_scope_and_never_uses_occt():
    from engineering.program.delivered import DeliveredVerifier

    def factory(*a, **k):
        raise AssertionError('no adapter may be built for a native revision in N1c-2')

    verifier = DeliveredVerifier.__new__(DeliveredVerifier)
    verifier.adapter_factory = factory
    verifier.timeout_s = 5
    stored = []
    verifier.evidence = SimpleNamespace(put=lambda record: stored.append(record) or record)
    lock = SimpleNamespace(backend_lock=SimpleNamespace(backend=NATIVE, native_versions=()))
    revision = SimpleNamespace(lock=lock, revision_id='a' * 32, job_id='b' * 32)
    ir = compile_source(native_source()).ir
    record = asyncio.run(verifier.verify(revision, ir))
    assert record.verdict == 'unavailable' and 'out_of_scope' in record.reasons
    assert record.claim_scope == 'geometry_checks_only'


# ------------------------------------------------------------------ edits (backend_changed)

def test_edit_that_changes_backend_is_refused_and_writes_nothing(light, monkeypatch):
    service = light.service
    stub = service.adapter_factory('occt')
    service.adapter_factory = lambda backend, mode='native', **kw: stub
    pid = new_program(light)
    for before, after, refused in (('occt', NATIVE, True), (NATIVE, 'occt', True), (NATIVE, NATIVE, False),
                                   ('occt', 'occt', False)):
        parent = SimpleNamespace(lock=SimpleNamespace(backend_lock=SimpleNamespace(backend=before),
                                                      capability_id='editable_plate'),
                                 fixed_requirements_sha256=compile_source(PLATE).brief_content.fixed_requirements_sha256)
        monkeypatch.setattr(service, '_owned_program', lambda db, program_id, client: {'head': 'a' * 32})
        monkeypatch.setattr(service, '_load', lambda db, rid, client: (parent, False, None))
        source = native_source() if after == NATIVE else PLATE
        result = run(service.stage(pid, source, source_id='plate.mir', expected_parent='a' * 32, client=ALICE))
        reasons = getattr(result, 'reasons', ())
        assert (reasons == ('backend_changed',)) is refused, (before, after, result)


def test_backend_changed_is_a_pinned_refusal_with_an_error_envelope():
    from engineering.program import interface as api
    from engineering.program.revisions import REFUSAL_REASONS
    assert 'backend_changed' in REFUSAL_REASONS
    err = api.from_revision_refusal(('backend_changed',))
    assert (err.kind, err.code) == ('refused', 'backend_changed')
