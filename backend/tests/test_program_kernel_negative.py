"""Offline negative cases derived from the written F03 contract."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from backend.tests.test_engineering_regen_adapter import accepted_report, candidate as regen_candidate
from engineering import regen_adapter
from engineering.cantilever import CantileverAdapter
from engineering.capabilities import REGEN
from engineering.contracts import CandidateSpec, Outcome
from engineering.program.adapters import MockKernelAdapter, adapter_for, occt, picogk
from engineering.program.kernel import (
    Artifact,
    ArtifactRef,
    BuildResult,
    ExecutionContext,
    ExportResult,
    InspectResult,
    Measurement,
    ProbeResult,
    backend_lock,
    plan_for,
)
from engineering.program.registry import ArtifactGraph, ArtifactNode, OpStream, OpStreamEntry


FIXTURES = Path(__file__).parent / 'fixtures'
F03 = FIXTURES / 'program' / 'f03'
CONTEXT = ExecutionContext(deadline_s=5.0, max_artifact_bytes=1024, workspace='negative-tests')
NATIVE_ADAPTERS = {'occt': occt.OcctAdapter, 'picogk': picogk.PicogkAdapter}
NATIVE_EVALUATORS = {'occt': CantileverAdapter, 'picogk': regen_adapter.RegenAdapter}
NATIVE_BACKENDS = tuple(NATIVE_ADAPTERS)


def refusal_parameters():
    cases = json.loads((F03 / 'negative' / 'cases.json').read_text(encoding='ascii'))
    ids = [case['id'] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError('F03 negative case IDs must be unique')
    return [pytest.param(case, id=case['id']) for case in cases]


@pytest.fixture(scope='module')
def plans():
    candidate_path = FIXTURES / 'engineering' / 'candidate-15mm.json'
    beam = CandidateSpec.model_validate_json(candidate_path.read_text(encoding='utf-8'))
    return {
        'occt': plan_for('rectangular_cantilever', '1.0.0', 'occt', beam, ('step', 'glb')),
        'picogk': plan_for('regen_chamber', '1.0.0', 'picogk', regen_candidate(), ('stl',)),
    }


@pytest.fixture
def native_calls(monkeypatch):
    probes, evaluations = {}, {}
    for backend in NATIVE_BACKENDS:
        probes[backend] = AsyncMock(side_effect=AssertionError(f'unexpected {backend} probe'))
        evaluations[backend] = AsyncMock(side_effect=AssertionError(f'unexpected {backend} evaluate'))
        monkeypatch.setattr(NATIVE_ADAPTERS[backend], 'probe', probes[backend])
        monkeypatch.setattr(NATIVE_EVALUATORS[backend], 'evaluate', evaluations[backend])
    return probes, evaluations


def mutate_plan(plan, other_plan, mutation):
    kind = mutation['kind']
    if kind == 'capability':
        capability_id, version = mutation['value'].split('@')
        return plan.model_copy(update={'capability_id': capability_id, 'capability_version': version})
    if kind == 'backend':
        return plan.model_copy(update={'backend': mutation['value']})
    if kind == 'export':
        return plan.model_copy(update={'exports': (mutation['value'], *plan.exports[1:])})
    if kind == 'candidate':
        return plan.model_copy(update={'candidate': other_plan.candidate})

    entries = plan.op_stream.entries
    if kind == 'reserved_operation':
        operation, version = mutation['value'].split('@')
        reserved = OpStreamEntry(seq=1, operation=operation, operation_version=version)
        entries = (entries[0], reserved, *entries[1:])
    elif kind == 'unknown_build_version':
        # The stream constructor rejects this version. Pass it directly to the adapter gate.
        head = entries[0].model_copy(update={'operation_version': mutation['value']})
        stream = plan.op_stream.model_copy(update={'entries': (head, *entries[1:])})
        return plan.model_copy(update={'op_stream': stream})
    elif kind == 'inspect_before_build':
        entries = (entries[1], entries[0], *entries[2:])
    elif kind == 'missing_export':
        entries = entries[:-1]
    else:
        raise ValueError(f'unknown F03 test mutation {kind!r}')

    stream = OpStream(
        schema_version=plan.op_stream.schema_version,
        entries=tuple(entry.model_copy(update={'seq': index}) for index, entry in enumerate(entries)),
    )
    return plan.model_copy(update={'op_stream': stream})


@pytest.mark.parametrize('case', refusal_parameters())
def test_plan_refusal_matches_contract(case, plans, native_calls):
    adapter_id = case['adapter']
    backend = adapter_id.removeprefix('mock_')
    mode = 'mock' if adapter_id.startswith('mock_') else 'native'
    adapter = adapter_for(backend, mode=mode)
    baseline = plans[backend]
    other_plan = plans['picogk' if backend == 'occt' else 'occt']
    plan = mutate_plan(baseline, other_plan, case['mutation'])
    assert adapter.supports(baseline).supported

    expected = case['expect']
    reasons = tuple(expected['reasons'])
    diagnostic = expected['diagnostic_code']
    decision = adapter.supports(plan)
    assert (decision.backend, decision.mode, decision.supported) == (backend, mode, False)
    assert (decision.reasons, decision.diagnostic_code) == (reasons, diagnostic)

    built = asyncio.run(adapter.build(plan, CONTEXT))
    result = built.result
    assert (result.backend, result.mode) == (backend, mode)
    assert (result.outcome, result.reasons, result.diagnostic_code) == (
        expected['outcome'], reasons, diagnostic,
    )
    assert built.artifacts == result.artifacts == ()
    assert built.evaluation is None
    for calls in native_calls:
        for call in calls.values():
            call.assert_not_called()


def stl_artifact(data):
    ref = ArtifactRef(
        name='generator-cleaned.stl', format='stl', representation='mesh',
        sha256=hashlib.sha256(data).hexdigest(), size=len(data), node_id='regen.chamber',
    )
    return Artifact(ref, data)


def picogk_reread(artifact):
    report = json.loads((F03 / 'picogk' / 'reread-report.json').read_text(encoding='ascii'))
    report['stl_sha256'] = artifact.ref.sha256
    mesh = {
        'bbox_min': [0.0, 0.0, 0.0],
        'bbox_max': [10.0, 10.0, 10.0],
        'volume_mm3': 1000.0,
    }
    return report, mesh


def precision_inspection(adapter, plan, monkeypatch, tmp_path):
    other_digest = hashlib.sha256(b'different artifact').hexdigest()
    if adapter.backend == 'occt':
        from process_runner import ProcessRunner

        data = b'beam artifact'
        ref = ArtifactRef(
            name='model.step', format='step', representation='brep',
            sha256=hashlib.sha256(data).hexdigest(), size=len(data), node_id='beam.solid',
        )
        report = json.loads((F03 / 'occt' / 'reread-report.json').read_text(encoding='ascii'))
        report['step_sha256'] = other_digest
        compiler = ModuleType('compiler')
        compiler._WORK_DIR = tmp_path
        monkeypatch.setitem(sys.modules, 'compiler', compiler)

        async def write_report(command, **kwargs):
            Path(command[-1]).write_text(json.dumps(report), encoding='ascii')
            return dict(returncode=0, code=None)

        run = AsyncMock(side_effect=write_report)
        monkeypatch.setattr(ProcessRunner, 'run', run)
        result = asyncio.run(adapter.inspect(Artifact(ref, data), plan))
        run.assert_awaited_once()
        return result

    artifact = stl_artifact(b'stl artifact')
    report, mesh = picogk_reread(artifact)
    report['stl_sha256'] = other_digest
    reread = Mock(return_value=mesh)
    monkeypatch.setattr(picogk, '_reread_mesh', reread)
    result = asyncio.run(adapter.inspect(artifact, plan, report=report))
    reread.assert_called_once_with(artifact.data)
    return result


@pytest.mark.parametrize('backend', NATIVE_BACKENDS)
@pytest.mark.parametrize(
    'scenario,outcome,reason',
    [
        ('wrong_backend', 'unsupported', 'backend_mismatch'),
        ('missing_runtime', 'unavailable', 'runtime_missing'),
        ('mismatched_reread', 'precision_failure', 'reread_mismatch'),
    ],
)
def test_native_outcomes_remain_distinct(
    backend, scenario, outcome, reason, plans, native_calls, monkeypatch, tmp_path,
):
    other = 'picogk' if backend == 'occt' else 'occt'
    alternate = Mock(side_effect=AssertionError(f'unexpected {other} adapter construction'))
    monkeypatch.setattr(NATIVE_ADAPTERS[other], '__init__', alternate)
    adapter = adapter_for(backend)
    assert type(adapter) is NATIVE_ADAPTERS[backend]
    plan = plans[backend]
    probes, evaluations = native_calls

    if scenario == 'mismatched_reread':
        result = precision_inspection(adapter, plan, monkeypatch, tmp_path)
    else:
        if scenario == 'missing_runtime':
            probes[backend].side_effect = None
            probes[backend].return_value = ProbeResult(
                backend=backend, mode='native', available=False, reasons=('runtime_missing',),
            )
        else:
            plan = plan.model_copy(update={'backend': other})
        result = asyncio.run(adapter.build(plan, CONTEXT)).result

    assert (result.backend, result.mode, result.outcome, result.reasons) == (
        backend, 'native', outcome, (reason,),
    )
    if scenario == 'missing_runtime':
        probes[backend].assert_awaited_once()
    else:
        probes[backend].assert_not_called()
    alternate.assert_not_called()
    probes[other].assert_not_called()
    for evaluate in evaluations.values():
        evaluate.assert_not_called()


def test_factory_rejects_unknown_backend():
    with pytest.raises(ValueError):
        adapter_for('unknown-kernel')


@pytest.fixture
def valid_record_inputs(plans):
    stream = plans['occt'].op_stream
    artifact = ArtifactRef(
        name='model.step', format='step', representation='brep',
        sha256='a' * 64, size=3, node_id='beam.solid',
    )
    measurement = Measurement(name='beam.length', value=200.0, unit='mm', source='report')
    graph = ArtifactGraph(
        schema_version='mirum_artifact_graph_v0_1', op_stream=stream,
        nodes=(ArtifactNode(id='beam.solid', kind='solid', op_seq=0),),
    )
    build = dict(
        backend='occt', adapter_version='test-1', capability_id='rectangular_cantilever',
        capability_version='1.0.0',
    )
    return {
        'completed': (BuildResult, dict(
            build, mode='native', outcome='completed', domain_outcome='accepted',
            claim_scope='checked_design', op_stream=stream, artifact_graph=graph,
            artifacts=(artifact,), measurements=(measurement,),
        )),
        'planned': (BuildResult, dict(build, mode='mock', outcome='planned')),
        'consistent': (InspectResult, dict(
            backend='occt', mode='native', outcome='consistent', artifact_sha256=artifact.sha256,
        )),
        'exported': (ExportResult, dict(
            backend='occt', mode='native', outcome='exported', format='step', artifact=artifact,
        )),
        'available': (ProbeResult, dict(backend='occt', mode='native', available=True)),
    }


@pytest.mark.parametrize(
    'baseline,field,value',
    [
        pytest.param('completed', 'mode', 'mock', id='completed-build'),
        pytest.param('planned', 'domain_outcome', 'accepted', id='accepted-verdict'),
        pytest.param('planned', 'claim_scope', 'checked_design', id='claim-scope'),
        pytest.param('planned', 'artifacts', None, id='artifacts'),
        pytest.param('planned', 'measurements', None, id='measurements'),
        pytest.param('consistent', 'mode', 'mock', id='consistent-inspection'),
        pytest.param('exported', 'mode', 'mock', id='exported-artifact'),
        pytest.param('available', 'mode', 'mock', id='available-probe'),
    ],
)
def test_mock_record_cannot_qualify(baseline, field, value, valid_record_inputs):
    record_type, fields = valid_record_inputs[baseline]
    record_type(**fields)
    if value is None:
        value = valid_record_inputs['completed'][1][field]
    with pytest.raises(ValidationError):
        record_type(**{**fields, field: value})


@pytest.mark.parametrize('backend', NATIVE_BACKENDS)
def test_mock_probe_cannot_issue_backend_lock(backend):
    adapter = MockKernelAdapter(backend)
    description = adapter.describe()
    probe = asyncio.run(adapter.probe())
    with pytest.raises(ValueError):
        backend_lock(description, probe)


@pytest.mark.parametrize(
    'size,available,reasons',
    [(65535, True, ()), (65537, False, ('probe_failed',))],
    ids=['under-limit', 'over-limit'],
)
def test_occt_probe_stdout_byte_limit(size, available, reasons, monkeypatch):
    from process_runner import ProcessRunner

    payload = '{"cadquery":"2.6.1","ocp":"7.8.1","volume":6.0}'
    stdout = payload.ljust(size)
    assert len(stdout.encode('ascii')) == size
    run = AsyncMock(return_value=dict(returncode=0, code=None, stdout=stdout, stderr=''))
    monkeypatch.setattr(ProcessRunner, 'run', run)

    result = asyncio.run(occt.OcctAdapter().probe())

    run.assert_awaited_once()
    assert (result.available, result.reasons) == (available, reasons)


class SizedSTL(bytes):
    """Report a large length while hashing and rereading a tiny fixture."""

    def __new__(cls, size):
        data = super().__new__(cls, b'fixture STL')
        data.size = size
        return data

    def __len__(self):
        return self.size


@pytest.mark.parametrize(
    'offset,outcome,reasons',
    [(-1, 'consistent', ()), (1, 'unavailable', ('budget_exceeded',))],
    ids=['under-limit', 'over-limit'],
)
def test_picogk_inspect_byte_budget(offset, outcome, reasons, plans, monkeypatch):
    size = REGEN.resources.run_bytes + offset
    artifact = stl_artifact(SizedSTL(size))
    report, mesh = picogk_reread(artifact)
    reread = Mock(return_value=mesh)
    if offset > 0:
        reread.side_effect = AssertionError('oversize STL reached the parser')
    monkeypatch.setattr(picogk, '_reread_mesh', reread)

    result = asyncio.run(picogk.PicogkAdapter().inspect(artifact, plans['picogk'], report=report))

    assert artifact.ref.size == len(artifact.data) == size
    assert (result.outcome, result.reasons) == (outcome, reasons)
    if offset > 0:
        reread.assert_not_called()
    else:
        reread.assert_called_once_with(artifact.data)


@pytest.mark.parametrize('backend', NATIVE_BACKENDS)
@pytest.mark.parametrize(
    'offset,outcome,reasons',
    [(-1, 'completed', ()), (1, 'unavailable', ('budget_exceeded',))],
    ids=['under-limit', 'over-limit'],
)
def test_build_total_artifact_byte_budget(backend, offset, outcome, reasons, plans, native_calls):
    context = ExecutionContext(deadline_s=5.0, max_artifact_bytes=32, workspace='negative-tests')
    size = context.max_artifact_bytes + offset
    if backend == 'occt':
        # Each file fits on its own. The over-budget case exceeds the aggregate limit.
        half = size // 2
        artifacts = {'model.step': b's' * half, 'preview.glb': b'g' * (size - half)}
        report = {}
    else:
        stl = b's' * size
        artifacts = {'generator-cleaned.stl': stl}
        report = accepted_report(stl=stl)
    assert sum(map(len, artifacts.values())) == size
    probes, evaluations = native_calls
    probes[backend].side_effect = None
    probes[backend].return_value = ProbeResult(backend=backend, mode='native', available=True)
    evaluations[backend].side_effect = None
    evaluations[backend].return_value = dict(
        outcome=Outcome.ACCEPTED, report=report, artifacts=artifacts,
    )

    built = asyncio.run(adapter_for(backend).build(plans[backend], context))

    probes[backend].assert_awaited_once()
    evaluations[backend].assert_awaited_once()
    assert (built.result.outcome, built.result.reasons) == (outcome, reasons)
    if offset > 0:
        assert built.artifacts == built.result.artifacts == ()
        assert built.evaluation is None
    else:
        assert sum(ref.size for ref in built.result.artifacts) == size
        assert sum(len(artifact.data) for artifact in built.artifacts) == size
