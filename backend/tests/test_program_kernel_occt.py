"""F03 OCCT adapter: offline snapshot tests. The native call is stubbed at the module boundary."""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from engineering.contracts import CandidateSpec, Outcome
from engineering.program.adapters import adapter_for
from engineering.program.adapters.occt import ADAPTER_VERSION, OcctAdapter, compare_reread
from engineering.program.errors import SourceSpan
from engineering.program.kernel import (
    Artifact, ArtifactRef, ExecutionContext, KernelAdapter, NativeVersion, ProbeResult, SpanBinding,
    backend_lock, plan_for,
)
from engineering.program.registry import OpStream, OpStreamEntry

FIXTURES = Path(__file__).parent / 'fixtures'
OCCT = FIXTURES / 'program' / 'f03' / 'occt'
CTX = ExecutionContext(deadline_s=5, max_artifact_bytes=10000, workspace='test')


def beam():
    return CandidateSpec.model_validate_json((FIXTURES / 'engineering' / 'candidate-15mm.json').read_text())


def span(n):
    return SourceSpan(line=n, column=1, end_line=n, end_column=5)


def plan(exports=('step', 'glb'), backend='occt', spans=()):
    return plan_for('rectangular_cantilever', '1.0.0', backend, beam(), exports, spans)


def load_eval(name):
    raw = json.loads((OCCT / name).read_text())
    return dict(outcome=Outcome(raw['outcome']), report=raw['report'], report_sha256=raw['report_sha256'],
                artifacts={k: v.encode('ascii') for k, v in raw['artifacts'].items()})


AVAILABLE = ProbeResult(backend='occt', mode='native', available=True, native_versions=(
    NativeVersion(name='cadquery', version='2.6.1'), NativeVersion(name='OCP', version='7.8.1')))


@pytest.fixture
def stub(monkeypatch):
    """Probe available; evaluate returns the accepted fixture and counts calls."""
    from engineering.cantilever import CantileverAdapter
    state = dict(calls=0, evaluation=load_eval('evaluation-accepted.json'), delay=0, exc=None)

    async def evaluate(self, candidate):
        state['calls'] += 1
        if state['delay']:
            await asyncio.sleep(state['delay'])
        if state['exc'] is not None:
            raise state['exc']
        return state['evaluation']

    async def probe(self):
        return AVAILABLE

    monkeypatch.setattr(CantileverAdapter, 'evaluate', evaluate)
    monkeypatch.setattr(OcctAdapter, 'probe', probe)
    return state


def build(p, ctx=CTX, adapter=None):
    return asyncio.run((adapter or OcctAdapter()).build(p, ctx))


# ------------------------------------------------------------------ describe

def test_describe_and_protocol():
    a = OcctAdapter()
    assert isinstance(a, KernelAdapter) and (a.backend, a.mode) == ('occt', 'native')
    d = a.describe()
    assert d.adapter_version == ADAPTER_VERSION == 'occt-0.1.0' and d.mode == 'native'
    assert d.export_formats == ('step', 'glb') and d.capabilities == ('rectangular_cantilever@1.0.0',)
    got = {t.name: (t.value, t.unit, t.source) for t in d.tolerances}
    assert got['topology_tolerance_max'][:2] == (1e-6, 'mm')
    assert got['volume_relative'][:2] == (1e-8, 'relative')
    assert all(src.startswith('engineering.verify.verify_step ') for _, _, src in got.values())
    assert adapter_for('occt').backend == 'occt'


def test_backend_lock_needs_a_real_probe():
    lock = backend_lock(OcctAdapter().describe(), AVAILABLE)
    assert [(v.name, v.version) for v in lock.native_versions] == [('cadquery', '2.6.1'), ('OCP', '7.8.1')]


# ------------------------------------------------------------------ build

def test_accepted_build_oplog_graph_measurements(stub):
    spans = (SpanBinding(key='build', span=span(1)), SpanBinding(key='inspect', span=span(2)),
             SpanBinding(key='export', span=span(3)))
    p = plan(spans=spans)
    built = build(p)
    r = built.result
    assert (r.outcome, r.mode, r.reasons, r.domain_outcome, r.claim_scope) == (
        'completed', 'native', (), 'accepted', 'checked_design')
    assert stub['calls'] == 1
    assert r.op_stream == p.op_stream
    assert [(e.seq, e.operation, e.operation_version, e.span) for e in r.op_stream.entries] == [
        (0, 'capability.build', '0.1.0', span(1)), (1, 'capability.inspect', '0.1.0', span(2)),
        (2, 'capability.export', '0.1.0', span(3)), (3, 'capability.export', '0.1.0', span(3))]
    assert [(n.id, n.kind, n.op_seq, n.span) for n in r.artifact_graph.nodes] == [
        ('beam.solid', 'solid', 0, span(1)), ('beam.preview', 'solid', 0, span(1))]
    assert r.artifact_graph.op_stream == r.op_stream
    assert [(a.name, a.format, a.representation, a.node_id) for a in r.artifacts] == [
        ('model.step', 'step', 'brep', 'beam.solid'), ('preview.glb', 'glb', 'mesh', 'beam.preview')]
    assert [a.ref for a in built.artifacts] == list(r.artifacts)
    assert built.artifacts[0].data == b'ISO-10303-21; fixture step bytes'
    assert r.artifacts[0].sha256 == hashlib.sha256(built.artifacts[0].data).hexdigest()
    expected = json.loads((OCCT / 'evaluation-accepted.json').read_text())['report']['measurements']
    names = {'length_mm': 'beam.length', 'width_mm': 'beam.width', 'height_mm': 'beam.height',
             'volume_mm3': 'beam.volume', 'surface_area_mm2': 'beam.surface_area',
             'section_area_mm2': 'beam.section_area', 'section_iy_mm4': 'beam.section_iy'}
    units = {'beam.length': 'mm', 'beam.width': 'mm', 'beam.height': 'mm', 'beam.volume': 'mm3',
             'beam.surface_area': 'mm2', 'beam.section_area': 'mm2', 'beam.section_iy': 'mm4'}
    assert {m.name: m.value for m in r.measurements} == pytest.approx({names[k]: v for k, v in expected.items()})
    assert all(m.source == 'report' and m.unit == units[m.name] for m in r.measurements)
    assert r.report_sha256 == '1' * 64
    assert built.evaluation['outcome'] is Outcome.ACCEPTED


def test_missing_report_keys_are_skipped_never_guessed(stub):
    stub['evaluation'] = dict(stub['evaluation'], report={'measurements': {'length_mm': 200.0, 'volume_mm3': 'x'}},
                              report_sha256='not-hex')
    r = build(plan(('step',))).result
    assert [(m.name, m.value) for m in r.measurements] == [('beam.length', 200.0)]
    assert r.report_sha256 is None


def test_rejected_evaluation_is_copied_without_artifacts(stub):
    stub['evaluation'] = load_eval('evaluation-rejected.json')
    built = build(plan())
    r = built.result
    assert (r.outcome, r.domain_outcome, r.claim_scope) == ('completed', 'rejected', 'checked_design')
    assert r.artifacts == () and r.artifact_graph.nodes == () and built.artifacts == ()
    assert r.measurements == () and r.report_sha256 == '2' * 64


def test_unsupported_never_calls_native(stub):
    glider = plan_for('glider_cem', '1.0.0', 'occt', beam(), ('step',))
    r = build(glider).result
    assert (r.outcome, r.reasons) == ('unsupported', ('capability_not_supported_by_backend',))
    r = build(plan(('stl',))).result
    assert (r.outcome, r.reasons) == ('unsupported', ('export_not_supported',))
    stream = OpStream(schema_version='mirum_op_stream_v0_1', entries=(
        OpStreamEntry(seq=0, operation='construct.extrude', operation_version='0.1.0'),))
    r = build(plan().model_copy(update={'op_stream': stream})).result
    assert (r.outcome, r.reasons, r.diagnostic_code) == ('unsupported', ('operation_not_executable',), 'IR009')
    r = build(plan(backend='picogk')).result
    assert r.reasons == ('backend_mismatch',)
    assert stub['calls'] == 0


def test_probe_unavailable_is_unavailable_not_unsupported(monkeypatch, stub):
    async def down(self):
        return ProbeResult(backend='occt', mode='native', available=False, reasons=('runtime_missing',))
    monkeypatch.setattr(OcctAdapter, 'probe', down)
    r = build(plan()).result
    assert (r.outcome, r.reasons) == ('unavailable', ('runtime_missing',))
    assert (r.domain_outcome, r.claim_scope, r.artifacts, r.measurements) == (None, None, (), ())
    assert stub['calls'] == 0


def test_engineering_unavailable_is_native_failed(stub):
    from engineering.service import EngineeringUnavailable
    stub['exc'] = EngineeringUnavailable('boom')
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
    ctx = ExecutionContext(deadline_s=5, max_artifact_bytes=10, workspace='test')
    built = build(plan(), ctx)
    r = built.result
    assert (r.outcome, r.reasons, r.artifacts, r.measurements, r.domain_outcome) == (
        'unavailable', ('budget_exceeded',), (), (), None)
    assert built.artifacts == ()


def test_unexpected_domain_outcome_is_evidence_invalid(stub):
    stub['evaluation'] = dict(stub['evaluation'], outcome=Outcome.NOT_EVALUATED)
    assert build(plan()).result.reasons == ('evidence_invalid',)


def test_cancellation_propagates(stub):
    stub['delay'] = 30

    async def go():
        task = asyncio.create_task(OcctAdapter().build(plan(), CTX))
        await asyncio.sleep(0.1)
        assert stub['calls'] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    asyncio.run(go())


# ------------------------------------------------------------------ compare_reread

STEP = b'ISO-10303-21; fixture step bytes'
STEP_SHA = hashlib.sha256(STEP).hexdigest()


def reread(**overrides):
    measurements = json.loads((OCCT / 'reread-report.json').read_text())['measurements']
    measurements.update(overrides)
    return dict(step_sha256=STEP_SHA, measurements=measurements)


def spec():
    return beam().specification


def test_compare_reread_consistent():
    r = compare_reread(reread(), spec(), STEP_SHA)
    assert (r.outcome, r.reasons, r.mode, r.artifact_sha256) == ('consistent', (), 'native', STEP_SHA)
    assert all(m.source == 'reread' for m in r.measurements + r.deviations)
    assert {m.name: m.value for m in r.measurements}['beam.section_iy'] == pytest.approx(2812.5)
    assert {m.name: m.value for m in r.deviations} == pytest.approx(
        {'beam.length': 0.0, 'beam.width': 0.0, 'beam.height': 0.0, 'beam.volume': 0.0})


def test_compare_reread_within_dimensional_tolerance():
    r = compare_reread(reread(length_mm=200.005, volume_mm3=200.005 * 150), spec(), STEP_SHA)
    assert r.outcome == 'consistent'
    assert {m.name: m.value for m in r.deviations}['beam.length'] == pytest.approx(0.005)


def test_compare_reread_mismatch():
    report = dict(reread(), step_sha256='0' * 64)
    r = compare_reread(report, spec(), STEP_SHA)
    assert (r.outcome, r.reasons) == ('precision_failure', ('reread_mismatch',))


def test_compare_reread_out_of_tolerance():
    r = compare_reread(reread(height_mm=15.5, volume_mm3=200 * 10 * 15.5), spec(), STEP_SHA)
    assert (r.outcome, r.reasons) == ('precision_failure', ('measurement_out_of_tolerance',))
    assert {m.name: m.value for m in r.deviations}['beam.height'] == pytest.approx(0.5)
    # complete-prism volume rule
    r = compare_reread(reread(volume_mm3=29000.0), spec(), STEP_SHA)
    assert (r.outcome, r.reasons) == ('precision_failure', ('measurement_out_of_tolerance',))


@pytest.mark.parametrize('bad', [{'measurements': {}}, {'measurements': None}, {},
                                 {'measurements': {'length_mm': 200.0, 'width_mm': 10.0,
                                                   'height_mm': 15.0, 'volume_mm3': True}}])
def test_compare_reread_evidence_invalid(bad):
    r = compare_reread(dict(bad, step_sha256=STEP_SHA), spec(), STEP_SHA)
    assert (r.outcome, r.reasons) == ('unavailable', ('evidence_invalid',))
    r = compare_reread(reread(width_mm=float('nan')), spec(), STEP_SHA)
    assert r.reasons == ('evidence_invalid',)


# ------------------------------------------------------------------ inspect (worker stubbed)

def _artifact(fmt='step', data=STEP):
    return Artifact(ArtifactRef(name='model.step', format=fmt, representation='brep',
                                sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                                node_id='beam.solid'), data)


@pytest.fixture
def worker(monkeypatch, tmp_path):
    import compiler
    from process_runner import ProcessRunner
    monkeypatch.setattr(compiler, '_WORK_DIR', tmp_path)
    state = dict(calls=[], outcome=dict(returncode=0, code=None, stdout='', stderr=''),
                 report=json.dumps(reread()), runner_closed=0)

    async def run(self, command, *, input=b'', cwd=None, env=None):
        state['calls'].append((command, cwd, env))
        if state['report'] is not None:
            Path(command[-1]).write_text(state['report'], encoding='utf-8')
        return state['outcome']

    monkeypatch.setattr(ProcessRunner, 'run', run)
    return state


def test_inspect_consistent_runs_the_existing_worker(worker):
    r = asyncio.run(OcctAdapter().inspect(_artifact(), plan()))
    assert (r.outcome, r.reasons) == ('consistent', ())
    command, cwd, env = worker['calls'][0]
    assert Path(command[1]).name == 'worker.py' and Path(command[1]).parent.name == 'engineering'
    assert env['OMP_NUM_THREADS'] == '1' and 'GEMINI_API_KEY_HEAVY' not in env
    assert Path(cwd).name.startswith('kernel-occt-')


def test_inspect_reports_mismatch_and_failures(worker):
    worker['report'] = json.dumps(dict(reread(), step_sha256='0' * 64))
    r = asyncio.run(OcctAdapter().inspect(_artifact(), plan()))
    assert r.reasons == ('reread_mismatch',)
    worker['outcome'] = dict(returncode=1, code=None, stdout='', stderr='')
    r = asyncio.run(OcctAdapter().inspect(_artifact(), plan()))
    assert (r.outcome, r.reasons) == ('unavailable', ('native_failed',))
    worker['outcome'] = dict(returncode=None, code='PROCESS_TREE_UNAVAILABLE', stdout='', stderr='')
    assert asyncio.run(OcctAdapter().inspect(_artifact(), plan())).reasons == ('native_failed',)


@pytest.mark.parametrize('report', [None, '{not json', '[1]', '{"step_sha256": NaN}',
                                    ' ' * 1048577 + '{}'])
def test_inspect_malformed_report_is_native_failed(worker, report):
    worker['report'] = report
    r = asyncio.run(OcctAdapter().inspect(_artifact(), plan()))
    assert (r.outcome, r.reasons) == ('unavailable', ('native_failed',))


def test_inspect_only_step_of_a_supported_plan(worker):
    r = asyncio.run(OcctAdapter().inspect(_artifact('glb'), plan()))
    assert (r.outcome, r.reasons) == ('unsupported', ('operation_not_supported',))
    r = asyncio.run(OcctAdapter().inspect(_artifact(), plan(('stl',))))
    assert r.outcome == 'unsupported'
    assert worker['calls'] == []


# ------------------------------------------------------------------ export

def test_export_rules():
    a = OcctAdapter()
    step = _artifact()
    r = a.export(step, 'step')
    assert (r.outcome, r.format, r.artifact) == ('exported', 'step', step.ref)
    for fmt in ('glb', 'stl'):
        r = a.export(step, fmt)
        assert (r.outcome, r.reasons, r.artifact) == ('unsupported', ('export_not_supported',), None)
    glb = Artifact(ArtifactRef(name='preview.glb', format='glb', representation='mesh',
                               sha256=hashlib.sha256(b'g').hexdigest(), size=1, node_id='beam.preview'), b'g')
    assert a.export(glb, 'glb').outcome == 'exported'
    assert a.export(glb, 'step').outcome == 'unsupported'


# ------------------------------------------------------------------ probe (ProcessRunner.run stubbed)

@pytest.fixture
def probe_run(monkeypatch):
    from process_runner import ProcessRunner
    state = dict(calls=[], outcome=None)

    async def run(self, command, *, input=b'', cwd=None, env=None):
        state['calls'].append((command, env))
        return state['outcome']

    monkeypatch.setattr(ProcessRunner, 'run', run)
    return state


def ok(**over):
    data = dict(cadquery='2.6.1', ocp='7.8.1', volume=6.0)
    data.update(over)
    return dict(returncode=0, code=None, stdout=json.dumps(data) + '\n', stderr='')


def probe(python=None):
    return asyncio.run(OcctAdapter(python=python or sys.executable).probe())


def test_probe_missing_interpreter_never_runs_anything(probe_run):
    r = probe(str(Path(__file__).parent / 'no-such-python-interpreter.exe'))
    assert (r.available, r.reasons) == (False, ('runtime_missing',))
    assert probe_run['calls'] == []


def test_probe_success(probe_run):
    probe_run['outcome'] = ok()
    r = probe()
    assert r.available and [(v.name, v.version) for v in r.native_versions] == [
        ('cadquery', '2.6.1'), ('OCP', '7.8.1')]
    command, env = probe_run['calls'][0]
    assert command[0] == sys.executable and Path(command[1]).name == '_occt_probe.py' and env is None


def test_probe_exit_3_and_import_error_are_runtime_missing(probe_run):
    probe_run['outcome'] = dict(returncode=3, code=None, stdout='{"error": "import"}\n', stderr='')
    assert probe().reasons == ('runtime_missing',)
    probe_run['outcome'] = dict(returncode=0, code=None, stdout='{"error": "import"}', stderr='')
    assert probe().reasons == ('runtime_missing',)


def test_probe_failures(probe_run):
    cases = [
        dict(returncode=0, code=None, stdout='x' * 65537, stderr=''),
        dict(returncode=None, code='OUTPUT_LIMIT', stdout='', stderr=''),
        dict(returncode=None, code='TIMEOUT', stdout='', stderr=''),
        dict(returncode=None, code='PROCESS_TREE_UNAVAILABLE', stdout='', stderr=''),
        dict(returncode=1, code=None, stdout='', stderr='boom'),
        dict(returncode=0, code=None, stdout='not json', stderr=''),
        dict(returncode=0, code=None, stdout='[1, 2]', stderr=''),
        dict(returncode=0, code=None, stdout='{"cadquery": "1", "ocp": "1", "volume": NaN}', stderr=''),
        dict(returncode=0, code=None, stdout='{"cadquery": "1", "ocp": "1"}', stderr=''),
        ok(volume=6.1), ok(volume=5.0), ok(cadquery=3), ok(ocp=''),
    ]
    for outcome in cases:
        probe_run['outcome'] = outcome
        r = probe()
        assert (r.available, r.reasons) == (False, ('probe_failed',)), outcome
    probe_run['outcome'] = ok(volume=6.0 + 1e-9)
    assert probe().available


def test_probe_closes_runner_even_on_error(monkeypatch):
    from process_runner import ProcessRunner
    closed = []
    real_close = ProcessRunner.close

    async def run(self, command, *, input=b'', cwd=None, env=None):
        raise RuntimeError('spawn failed')

    async def close(self):
        closed.append(1)
        await real_close(self)

    monkeypatch.setattr(ProcessRunner, 'run', run)
    monkeypatch.setattr(ProcessRunner, 'close', close)
    with pytest.raises(RuntimeError):
        probe()
    assert closed == [1]


def test_build_uses_the_real_probe_result(probe_run, monkeypatch):
    """Without the probe stub, a missing runtime yields unavailable before any evaluation."""
    from engineering.cantilever import CantileverAdapter
    calls = []

    async def evaluate(self, candidate):
        calls.append(1)

    monkeypatch.setattr(CantileverAdapter, 'evaluate', evaluate)
    adapter = OcctAdapter(python=str(Path(__file__).parent / 'missing-python.exe'))
    r = build(plan(), adapter=adapter).result
    assert (r.outcome, r.reasons) == ('unavailable', ('runtime_missing',))
    assert calls == []


# ------------------------------------------------------------------ import hygiene

def test_import_hygiene_for_occt_adapter():
    code = (
        "import sys\n"
        "import engineering.program.kernel, engineering.program.adapters\n"
        "import engineering.program.adapters.occt\n"
        "bad = [m for m in ('cadquery','OCP','fastapi','httpx','google.genai','build123d')"
        " if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
