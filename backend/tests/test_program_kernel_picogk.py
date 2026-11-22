"""F03 PicoGK adapter: offline snapshot tests. The native call is stubbed at the module boundary."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import subprocess
import sys
import types
from pathlib import Path

import pytest

from backend.tests.test_engineering_regen_adapter import STL, accepted_report, build as build_evaluation
from backend.tests.test_engineering_regen_adapter import candidate as regen_candidate
from backend.tests.test_engineering_regen_adapter import spec_model
from engineering import regen_adapter
from engineering.capabilities import REGEN
from engineering.contracts import CandidateSpec
from engineering.program.adapters import adapter_for
from engineering.program.adapters import picogk as picogk_module
from engineering.program.adapters.picogk import ADAPTER_VERSION, PicogkAdapter, compare_reread
from engineering.program.errors import SourceSpan
from engineering.program.kernel import (
    Artifact, ArtifactRef, ExecutionContext, KernelAdapter, NativeVersion, ProbeResult, SpanBinding,
    backend_lock, plan_for,
)
from engineering.program.registry import OpStream, OpStreamEntry
from engineering.regen_adapter import RegenAdapter, RegenUnavailable

FIXTURES = Path(__file__).parent / 'fixtures'
PICOGK = FIXTURES / 'program' / 'f03' / 'picogk'
CTX = ExecutionContext(deadline_s=5, max_artifact_bytes=10000, workspace='test')
HEX_A, HEX_B = 'a' * 64, 'b' * 64


def span(n):
    return SourceSpan(line=n, column=1, end_line=n, end_column=5)


def plan(exports=('stl',), backend='picogk', spans=()):
    return plan_for('regen_chamber', '1.0.0', backend, regen_candidate(), exports, spans)


def beam():
    return CandidateSpec.model_validate_json((FIXTURES / 'engineering' / 'candidate-15mm.json').read_text())


def accepted_evaluation():
    return dict(build_evaluation(regen_candidate(), accepted_report(), accepted=True))


def rejected_evaluation():
    report = accepted_report(passed=False, failures=['rib below the floor'])
    report['checks']['rib'].update(state='fail', per_station=[2.0] * 6 + [0.5], minimum=0.5)
    return dict(build_evaluation(regen_candidate(), report, accepted=False))


AVAILABLE = ProbeResult(backend='picogk', mode='native', available=True, native_versions=(
    NativeVersion(name='picogk_runtime_so_sha256', version=HEX_A),
    NativeVersion(name='regen_builder_sha256', version=HEX_B)))


@pytest.fixture
def stub(monkeypatch):
    """Probe available; RegenAdapter.evaluate returns the accepted evaluation and counts calls."""
    state = dict(calls=0, evaluation=accepted_evaluation(), delay=0, exc=None, runners=[])

    async def evaluate(self, candidate):
        state['calls'] += 1
        state['runners'].append(self._runner)
        if state['delay']:
            await asyncio.sleep(state['delay'])
        if state['exc'] is not None:
            raise state['exc']
        return state['evaluation']

    async def probe(self):
        return AVAILABLE

    monkeypatch.setattr(RegenAdapter, 'evaluate', evaluate)
    monkeypatch.setattr(PicogkAdapter, 'probe', probe)
    return state


def build(p, ctx=CTX, adapter=None):
    return asyncio.run((adapter or PicogkAdapter(runner=lambda: object())).build(p, ctx))


# ------------------------------------------------------------------ describe

def test_describe_and_protocol():
    a = PicogkAdapter()
    assert isinstance(a, KernelAdapter) and (a.backend, a.mode) == ('picogk', 'native')
    d = a.describe()
    assert d.adapter_version == ADAPTER_VERSION == 'picogk-0.1.0' and d.mode == 'native'
    assert d.export_formats == ('stl',) and 'step' not in d.export_formats
    assert d.capabilities == ('regen_chamber@1.0.0',)
    assert set(d.representations) == {'voxel', 'mesh', 'report'}
    got = {t.name: (t.value, t.unit, t.source) for t in d.tolerances}
    assert set(got) == {'volume_relative'}
    assert got['volume_relative'][:2] == (0.02, 'relative')
    from engineering import regen_mesh
    assert got['volume_relative'][0] == regen_mesh.VOLUME_TOLERANCE
    assert 'engineering.regen_mesh.VOLUME_TOLERANCE' in got['volume_relative'][2]
    assert adapter_for('picogk').backend == 'picogk'
    assert isinstance(adapter_for('picogk', runner=lambda: None), PicogkAdapter)


def test_backend_lock_needs_a_real_probe():
    lock = backend_lock(PicogkAdapter().describe(), AVAILABLE)
    assert [(v.name, v.version) for v in lock.native_versions] == [
        ('picogk_runtime_so_sha256', HEX_A), ('regen_builder_sha256', HEX_B)]


# ------------------------------------------------------------------ probe

def fake_web(monkeypatch, ready):
    module = types.ModuleType('engineering.regen_web')
    module._ready = lambda: ready
    monkeypatch.setitem(sys.modules, 'engineering.regen_web', module)


def fake_provenance(monkeypatch, tmp_path, **changes):
    values = dict(runtime_so_sha256=HEX_A, builder_sha256=HEX_B,
                  builder_path=str(tmp_path / 'app' / 'builder'), image_id=None)
    values.update(changes)
    monkeypatch.setattr(regen_adapter, 'regen_provenance', lambda env=None: values)


def probe(adapter):
    return asyncio.run(adapter.probe())


def test_probe_without_runner_is_runtime_missing(monkeypatch):
    fake_web(monkeypatch, True)
    r = probe(PicogkAdapter())
    assert (r.available, r.reasons) == (False, ('runtime_missing',))


def test_probe_without_web_module_is_runtime_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, 'engineering.regen_web', None)  # import raises ImportError
    r = probe(PicogkAdapter(runner=lambda: object()))
    assert (r.available, r.reasons) == (False, ('runtime_missing',))


def test_probe_not_ready_is_runtime_missing(monkeypatch):
    fake_web(monkeypatch, False)
    r = probe(PicogkAdapter(runner=lambda: object()))
    assert (r.available, r.reasons) == (False, ('runtime_missing',))


@pytest.mark.parametrize('field', ['runtime_so_sha256', 'builder_sha256'])
def test_probe_missing_file_identity_is_probe_failed(monkeypatch, tmp_path, field):
    fake_web(monkeypatch, True)
    fake_provenance(monkeypatch, tmp_path, **{field: None})
    r = probe(PicogkAdapter(runner=lambda: object()))
    assert (r.available, r.reasons) == (False, ('probe_failed',))


def test_probe_available_reports_identities_manifest_and_image(monkeypatch, tmp_path):
    fake_web(monkeypatch, True)
    fake_provenance(monkeypatch, tmp_path, image_id='sha256:abc')
    (tmp_path / 'picogk-runtime.sha256').write_text(HEX_B.replace('b', 'c') + '  picogk.26.2.so\n')
    r = probe(PicogkAdapter(runner=lambda: object()))
    assert r.available and r.reasons == ()
    assert [(v.name, v.version) for v in r.native_versions] == [
        ('picogk_runtime_so_sha256', HEX_A), ('regen_builder_sha256', HEX_B),
        ('picogk_runtime_manifest_sha256', 'c' * 64), ('image_id', 'sha256:abc')]


def test_probe_omits_absent_or_malformed_manifest(monkeypatch, tmp_path):
    fake_web(monkeypatch, True)
    fake_provenance(monkeypatch, tmp_path)
    r = probe(PicogkAdapter(runner=lambda: object()))
    assert [v.name for v in r.native_versions] == ['picogk_runtime_so_sha256', 'regen_builder_sha256']
    (tmp_path / 'picogk-runtime.sha256').write_text('not a digest\n')
    r = probe(PicogkAdapter(runner=lambda: object()))
    assert len(r.native_versions) == 2
    (tmp_path / 'picogk-runtime.sha256').write_bytes(b'x' * 5000)  # over the 4096-byte budget
    r = probe(PicogkAdapter(runner=lambda: object()))
    assert len(r.native_versions) == 2


# ------------------------------------------------------------------ build

def test_accepted_build_oplog_graph_measurements(stub):
    spans = (SpanBinding(key='build', span=span(1)), SpanBinding(key='inspect', span=span(2)),
             SpanBinding(key='export', span=span(3)))
    p = plan(spans=spans)
    runner = lambda: object()
    built = build(p, adapter=PicogkAdapter(runner=runner))
    r = built.result
    assert (r.outcome, r.mode, r.reasons, r.domain_outcome, r.claim_scope) == (
        'completed', 'native', (), 'accepted', 'geometry_checks_only')
    assert r.claim_scope != 'checked_design'
    assert stub['calls'] == 1 and stub['runners'] == [runner]
    assert r.op_stream == p.op_stream
    assert [(e.seq, e.operation, e.operation_version, e.span) for e in r.op_stream.entries] == [
        (0, 'capability.build', '0.2.0', span(1)), (1, 'capability.inspect', '0.2.0', span(2)),
        (2, 'capability.export', '0.2.0', span(3))]
    assert [(n.id, n.kind, n.op_seq, n.span) for n in r.artifact_graph.nodes] == [
        ('regen.chamber', 'solid', 0, span(1))]
    assert r.artifact_graph.op_stream == r.op_stream
    assert [(a.name, a.format, a.representation, a.node_id) for a in r.artifacts] == [
        (regen_adapter.STL, 'stl', 'mesh', 'regen.chamber')]
    assert [a.ref for a in built.artifacts] == list(r.artifacts)
    assert built.artifacts[0].data == STL
    assert r.artifacts[0].sha256 == hashlib.sha256(STL).hexdigest() and r.artifacts[0].size == len(STL)
    # bbox and volume checks only; 'ratio' has no Measurement unit so the volume number is skipped.
    assert [(m.name, m.value, m.unit, m.source) for m in r.measurements] == [
        ('regen.bbox.bbox_max_error', pytest.approx(0.1), 'mm', 'report')]
    assert r.report_sha256 == stub['evaluation']['report_sha256']
    assert built.evaluation['outcome'] == 'accepted'
    assert 'step' not in {a.format for a in r.artifacts}


def test_evaluation_is_copied_not_edited(stub):
    before = json.dumps(stub['evaluation']['report'], sort_keys=True)
    built = build(plan())
    assert json.dumps(built.evaluation['report'], sort_keys=True) == before
    assert built.evaluation['report_sha256'] == stub['evaluation']['report_sha256']


def test_rejected_evaluation_is_copied_without_artifacts(stub):
    stub['evaluation'] = rejected_evaluation()
    built = build(plan())
    r = built.result
    assert (r.outcome, r.domain_outcome, r.claim_scope) == ('completed', 'rejected', 'geometry_checks_only')
    assert r.claim_scope != 'checked_design'
    assert r.artifacts == () and r.artifact_graph.nodes == () and built.artifacts == ()
    assert r.report_sha256 == stub['evaluation']['report_sha256']
    assert built.evaluation['outcome'] == 'rejected'


def test_unknown_domain_outcome_is_evidence_invalid(stub):
    stub['evaluation'] = dict(stub['evaluation'], outcome='maybe')
    r = build(plan()).result
    assert (r.outcome, r.reasons) == ('unavailable', ('evidence_invalid',))


def test_malformed_report_is_evidence_invalid(stub):
    stub['evaluation'] = dict(stub['evaluation'], report={'checks': {}})
    r = build(plan()).result
    assert (r.outcome, r.reasons) == ('unavailable', ('evidence_invalid',))


def test_missing_stl_in_accepted_evaluation_is_evidence_invalid(stub):
    stub['evaluation'] = dict(stub['evaluation'], artifacts={})
    r = build(plan()).result
    assert (r.outcome, r.reasons) == ('unavailable', ('evidence_invalid',))


def test_bad_report_digest_is_dropped_not_guessed(stub):
    stub['evaluation'] = dict(stub['evaluation'], report_sha256='not-hex')
    assert build(plan()).result.report_sha256 is None


def test_unsupported_never_calls_native(stub):
    c = regen_candidate()
    r = build(plan_for('rectangular_cantilever', '1.0.0', 'picogk', beam(), ('step',))).result
    assert (r.outcome, r.reasons) == ('unsupported', ('capability_not_supported_by_backend',))
    r = build(plan_for('glider_cem', '1.0.0', 'picogk', c, ('stl',))).result
    assert (r.outcome, r.reasons) == ('unsupported', ('capability_not_supported_by_backend',))
    r = build(plan(('step',))).result
    assert (r.outcome, r.reasons) == ('unsupported', ('export_not_supported',))
    r = build(plan(('stl', 'glb'))).result
    assert r.reasons == ('export_not_supported',)
    stream = OpStream(schema_version='mirum_op_stream_v0_1', entries=(
        OpStreamEntry(seq=0, operation='construct.extrude', operation_version='0.1.0'),))
    r = build(plan().model_copy(update={'op_stream': stream})).result
    assert (r.outcome, r.reasons, r.diagnostic_code) == ('unsupported', ('operation_not_executable',), 'IR009')
    r = build(plan(backend='occt')).result
    assert r.reasons == ('backend_mismatch',)
    assert stub['calls'] == 0


def test_runner_none_is_unavailable_with_no_fallback(monkeypatch):
    """Real probe path: no runner means runtime_missing (never unsupported), and nothing else runs."""
    fake_web(monkeypatch, True)
    called = []

    async def evaluate(self, candidate):
        called.append(1)

    monkeypatch.setattr(RegenAdapter, 'evaluate', evaluate)
    from engineering.cantilever import CantileverAdapter
    monkeypatch.setattr(CantileverAdapter, 'evaluate', evaluate)
    r = asyncio.run(PicogkAdapter().build(plan(), CTX)).result
    assert (r.outcome, r.reasons, r.backend) == ('unavailable', ('runtime_missing',), 'picogk')
    assert (r.domain_outcome, r.claim_scope, r.artifacts, r.measurements) == (None, None, (), ())
    assert called == []


def test_probe_unavailable_is_unavailable_not_unsupported(monkeypatch, stub):
    async def down(self):
        return ProbeResult(backend='picogk', mode='native', available=False, reasons=('probe_failed',))
    monkeypatch.setattr(PicogkAdapter, 'probe', down)
    r = build(plan()).result
    assert (r.outcome, r.reasons) == ('unavailable', ('probe_failed',))
    assert stub['calls'] == 0


def test_regen_unavailable_is_native_failed(stub):
    stub['exc'] = RegenUnavailable('boom')
    r = build(plan()).result
    assert (r.outcome, r.reasons) == ('unavailable', ('native_failed',))


def test_unsupported_capability_is_candidate_invalid(stub):
    from engineering.capabilities import UnsupportedCapability
    stub['exc'] = UnsupportedCapability('nope')
    r = build(plan()).result
    assert (r.outcome, r.reasons) == ('unsupported', ('candidate_invalid',))


def test_deadline_exceeded(stub):
    stub['delay'] = 5
    ctx = ExecutionContext(deadline_s=0.05, max_artifact_bytes=10000, workspace='test')
    r = build(plan(), ctx).result
    assert (r.outcome, r.reasons) == ('unavailable', ('deadline_exceeded',))


def test_budget_exceeded_emits_no_artifacts(stub):
    ctx = ExecutionContext(deadline_s=5, max_artifact_bytes=3, workspace='test')
    built = build(plan(), ctx)
    r = built.result
    assert (r.outcome, r.reasons, r.artifacts, r.measurements, r.domain_outcome) == (
        'unavailable', ('budget_exceeded',), (), (), None)
    assert built.artifacts == ()


def test_cancellation_propagates(stub):
    stub['delay'] = 30

    async def scenario():
        task = asyncio.ensure_future(PicogkAdapter(runner=lambda: object()).build(plan(), CTX))
        await asyncio.sleep(0.05)
        assert stub['calls'] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    asyncio.run(scenario())


# ------------------------------------------------------------------ compare_reread / inspect

def cube_triangles():
    import numpy as np
    raw = json.loads((PICOGK / 'cube.json').read_text())
    corners = np.asarray(raw['corners'], dtype=np.float32) * raw['scale_mm']
    tris = []
    for a, b, c, d in raw['quads']:
        tris += [(a, b, c), (a, c, d)]
    return np.ascontiguousarray(corners[np.asarray(tris)], dtype=np.float32)


def cube_stl():
    from engineering import regen_mesh
    return regen_mesh.stl_bytes(cube_triangles())


def reread_report(data):
    report = json.loads((PICOGK / 'reread-report.json').read_text())
    report['stl_sha256'] = hashlib.sha256(data).hexdigest()
    return report


MESH = dict(bbox_min=[0.0, 0.0, 0.0], bbox_max=[10.0, 10.0, 10.0], volume_mm3=1000.0)
TOL = 0.8


def test_compare_consistent_carries_reread_measurements_and_deviations():
    data = cube_stl()
    r = compare_reread(reread_report(data), MESH, hashlib.sha256(data).hexdigest(), bbox_tolerance_mm=TOL)
    assert (r.outcome, r.reasons, r.backend, r.mode) == ('consistent', (), 'picogk', 'native')
    assert len(r.measurements) == 7 and all(m.source == 'reread' for m in r.measurements)
    assert [m.name for m in r.measurements] == [m.name for m in r.deviations]
    assert all(m.value == 0.0 for m in r.deviations)
    assert {m.name: m.unit for m in r.measurements}['regen.reread.volume_mm3'] == 'mm3'


def test_compare_sha_mismatch_is_reread_mismatch():
    data = cube_stl()
    r = compare_reread(reread_report(data), MESH, HEX_A, bbox_tolerance_mm=TOL)
    assert (r.outcome, r.reasons) == ('precision_failure', ('reread_mismatch',))


def test_compare_out_of_tolerance():
    data = cube_stl()
    sha = hashlib.sha256(data).hexdigest()
    far = dict(MESH, bbox_max=[10.0, 10.0, 10.0 + TOL + 0.1])
    r = compare_reread(reread_report(data), far, sha, bbox_tolerance_mm=TOL)
    assert (r.outcome, r.reasons) == ('precision_failure', ('measurement_out_of_tolerance',))
    assert max(m.value for m in r.deviations) == pytest.approx(TOL + 0.1)
    near = dict(MESH, bbox_max=[10.0, 10.0, 10.5])  # inside the limit is consistent
    assert compare_reread(reread_report(data), near, sha, bbox_tolerance_mm=TOL).outcome == 'consistent'
    vol = REGEN_VOLUME_TOL = {m.name: m.value for m in REGEN.numerical}['VOLUME_TOLERANCE']
    r = compare_reread(reread_report(data), dict(MESH, volume_mm3=1000.0 * (1 + vol + 0.01)), sha,
                       bbox_tolerance_mm=TOL)
    assert (r.outcome, r.reasons) == ('precision_failure', ('measurement_out_of_tolerance',))
    ok = compare_reread(reread_report(data), dict(MESH, volume_mm3=1000.0 * (1 + vol / 2)), sha,
                        bbox_tolerance_mm=TOL)
    assert ok.outcome == 'consistent' and REGEN_VOLUME_TOL == 0.02


@pytest.mark.parametrize('mutate', [
    lambda rep, mesh: rep['checks'].pop('bbox'),
    lambda rep, mesh: rep['checks']['volume'].update(measured_mm3=float('nan')),
    lambda rep, mesh: rep['checks']['volume'].update(measured_mm3=0.0),
    lambda rep, mesh: rep['checks']['bbox'].update(measured_min=[0.0, 0.0]),
    lambda rep, mesh: mesh.update(volume_mm3=math.inf),
    lambda rep, mesh: mesh.update(bbox_min=[0.0, float('nan'), 0.0]),
    lambda rep, mesh: mesh.pop('bbox_max'),
])
def test_compare_missing_or_non_finite_is_evidence_invalid(mutate):
    data = cube_stl()
    report, mesh = reread_report(data), dict(MESH)
    mutate(report, mesh)
    r = compare_reread(report, mesh, hashlib.sha256(data).hexdigest(), bbox_tolerance_mm=TOL)
    assert (r.outcome, r.reasons) == ('unavailable', ('evidence_invalid',))


def test_compare_needs_a_caller_tolerance_never_the_reports():
    data = cube_stl()
    r = compare_reread(reread_report(data), MESH, hashlib.sha256(data).hexdigest())
    assert (r.outcome, r.reasons) == ('unavailable', ('evidence_invalid',))
    assert compare_reread(None, MESH, HEX_A, bbox_tolerance_mm=TOL).reasons == ('evidence_invalid',)


def artifact(data):
    return Artifact(ArtifactRef(name='cleaned.stl', format='stl', representation='mesh',
                                sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                                node_id='regen.chamber'), data)


def inspect(adapter, art, p=None, **kw):
    return asyncio.run(adapter.inspect(art, p or plan(), **kw))


def test_inspect_rereads_a_real_stl_and_is_consistent():
    pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    data = cube_stl()
    assert spec_model().voxel_size_mm * 2.0 > 0
    r = inspect(PicogkAdapter(), artifact(data), report=reread_report(data))
    assert (r.outcome, r.reasons) == ('consistent', ())
    got = {m.name: m.value for m in r.measurements}
    assert got['regen.reread.volume_mm3'] == pytest.approx(1000.0)
    assert got['regen.reread.bbox_max_x'] == pytest.approx(10.0)


def test_inspect_detects_a_report_that_disagrees_with_the_mesh():
    pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    data = cube_stl()
    report = reread_report(data)
    report['checks']['volume']['measured_mm3'] = 1500.0
    r = inspect(PicogkAdapter(), artifact(data), report=report)
    assert (r.outcome, r.reasons) == ('precision_failure', ('measurement_out_of_tolerance',))
    report = reread_report(data)
    report['stl_sha256'] = HEX_A
    r = inspect(PicogkAdapter(), artifact(data), report=report)
    assert (r.outcome, r.reasons) == ('precision_failure', ('reread_mismatch',))


def test_inspect_without_report_is_evidence_invalid():
    data = cube_stl()
    r = inspect(PicogkAdapter(), artifact(data))
    assert (r.outcome, r.reasons) == ('unavailable', ('evidence_invalid',))


def test_inspect_garbage_stl_is_evidence_invalid():
    pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    data = b'not an stl'
    r = inspect(PicogkAdapter(), artifact(data), report={'stl_sha256': hashlib.sha256(data).hexdigest()})
    assert (r.outcome, r.reasons) == ('unavailable', ('evidence_invalid',))


def test_inspect_byte_budget(monkeypatch):
    data = cube_stl()
    monkeypatch.setattr(picogk_module, 'REGEN', types.SimpleNamespace(
        resources=types.SimpleNamespace(run_bytes=len(data) - 1), claim=REGEN.claim,
        numerical=REGEN.numerical))
    r = inspect(PicogkAdapter(), artifact(data), report=reread_report(data))
    assert (r.outcome, r.reasons) == ('unavailable', ('budget_exceeded',))


def test_inspect_unsupported_for_other_formats_and_plans():
    data = b'step bytes'
    step = Artifact(ArtifactRef(name='model.step', format='step', representation='brep',
                                sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                                node_id='regen.chamber'), data)
    r = inspect(PicogkAdapter(), step)
    assert (r.outcome, r.reasons) == ('unsupported', ('operation_not_supported',))
    other = plan_for('rectangular_cantilever', '1.0.0', 'picogk', beam(), ('step',))
    r = inspect(PicogkAdapter(), artifact(cube_stl()), other)
    assert (r.outcome, r.reasons) == ('unsupported', ('operation_not_supported',))


# ------------------------------------------------------------------ export

def test_export_rules():
    a = PicogkAdapter()
    art = artifact(b'stl-bytes')
    ok = a.export(art, 'stl')
    assert (ok.outcome, ok.format, ok.artifact) == ('exported', 'stl', art.ref)
    for fmt in ('step', 'glb'):
        r = a.export(art, fmt)
        assert (r.outcome, r.reasons, r.artifact) == ('unsupported', ('export_not_supported',), None)
    mismatch = Artifact(ArtifactRef(name='x.step', format='step', representation='brep',
                                    sha256=hashlib.sha256(b'z').hexdigest(), size=1, node_id='regen.chamber'), b'z')
    assert a.export(mismatch, 'stl').outcome == 'unsupported'


# ------------------------------------------------------------------ import hygiene

def test_import_hygiene_for_picogk_adapter():
    code = (
        "import sys\n"
        "import engineering.program.kernel, engineering.program.adapters\n"
        "import engineering.program.adapters.picogk\n"
        "bad = [m for m in ('cadquery','OCP','fastapi','httpx','google.genai','build123d')"
        " if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
