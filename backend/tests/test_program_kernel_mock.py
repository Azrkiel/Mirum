"""F03 plan helpers, check_plan order, and the mock adapter."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from backend.tests.test_engineering_generator_contracts import brief_for, content
from engineering.contracts import CandidateSpec
from engineering.program.adapters import MockKernelAdapter, adapter_for
from engineering.program.errors import SourceSpan
from engineering.program.kernel import (
    Artifact, ArtifactRef, BuildPlan, ExecutionContext, KernelAdapter, ProbeResult, SpanBinding,
    backend_lock, check_plan, make_description, plan_for,
)
from engineering.program.registry import OpStream, OpStreamEntry

FIXTURES = Path(__file__).parent / 'fixtures' / 'engineering'
CTX = ExecutionContext(deadline_s=5, max_artifact_bytes=1000, workspace='test')


def beam():
    return CandidateSpec.model_validate_json((FIXTURES / 'candidate-15mm.json').read_text())


def regen():
    from engineering.regen_adapter import RegenAdapter
    (cand,) = RegenAdapter().candidates(brief_for(content()))
    return cand


def beam_plan(exports=('step', 'glb'), backend='occt', spans=()):
    return plan_for('rectangular_cantilever', '1.0.0', backend, beam(), exports, spans)


def regen_plan(exports=('stl',), backend='picogk'):
    return plan_for('regen_chamber', '1.0.0', backend, regen(), exports)


def span(n):
    return SourceSpan(line=n, column=1, end_line=n, end_column=5)


def desc(backend):
    return make_description(backend, mode='native', adapter_version='t-1')


def reasons(backend, plan):
    d = check_plan(desc(backend), plan)
    return d.reasons, d.diagnostic_code


def test_plan_for_beam_canonical_stream_and_spans():
    spans = (SpanBinding(key='build', span=span(1)), SpanBinding(key='inspect', span=span(2)),
             SpanBinding(key='export', span=span(3)))
    plan = beam_plan(spans=spans)
    got = [(e.seq, e.operation, e.operation_version, e.span) for e in plan.op_stream.entries]
    assert got == [(0, 'capability.build', '0.1.0', span(1)),
                   (1, 'capability.inspect', '0.1.0', span(2)),
                   (2, 'capability.export', '0.1.0', span(3)),
                   (3, 'capability.export', '0.1.0', span(3))]
    assert beam_plan(('step',)).op_stream.entries[-1].span is None


def test_plan_for_regen_uses_0_2_0():
    plan = regen_plan()
    assert [(e.operation, e.operation_version) for e in plan.op_stream.entries] == [
        ('capability.build', '0.2.0'), ('capability.inspect', '0.2.0'),
        ('capability.export', '0.2.0')]


def test_supported_plans():
    assert check_plan(desc('occt'), beam_plan()).supported
    assert check_plan(desc('picogk'), regen_plan()).supported


def test_backend_mismatch():
    assert reasons('picogk', beam_plan()) == (('backend_mismatch',), None)
    assert reasons('occt', regen_plan()) == (('backend_mismatch',), None)


def test_reserved_operation_is_not_executable():
    plan = beam_plan()
    stream = OpStream(schema_version='mirum_op_stream_v0_1', entries=(
        OpStreamEntry(seq=0, operation='construct.extrude', operation_version='0.1.0'),))
    bad = plan.model_copy(update={'op_stream': stream})
    assert reasons('occt', bad) == (('operation_not_executable',), 'IR009')


def test_unknown_version_maps_to_operation_not_supported():
    plan = beam_plan()
    # the record loader rejects unknown versions, so bypass it for this branch only
    stream = OpStream.model_construct(schema_version='mirum_op_stream_v0_1', entries=(
        OpStreamEntry.model_construct(seq=0, operation='capability.build', operation_version='9.9.9',
                                      step_ref=None, span=None),))
    bad = plan.model_copy(update={'op_stream': stream})
    assert reasons('occt', bad) == (('operation_not_supported',), 'IR001')
    stream = OpStream.model_construct(schema_version='mirum_op_stream_v0_1', entries=(
        OpStreamEntry.model_construct(seq=0, operation='capability.nope', operation_version='0.1.0',
                                      step_ref=None, span=None),))
    bad = plan.model_copy(update={'op_stream': stream})
    assert reasons('occt', bad) == (('operation_not_supported',), 'IR008')


@pytest.mark.parametrize('cap,ver', [('glider_cem', '1.0.0'), ('rectangular_plate', '1.0.0'),
                                     ('co2_dragster', '2.0.0')])
@pytest.mark.parametrize('backend', ['occt', 'picogk'])
def test_other_capabilities_unsupported_on_both_backends(cap, ver, backend):
    plan = plan_for(cap, ver, backend, beam(), ('step',) if backend == 'occt' else ('stl',))
    assert reasons(backend, plan)[0] == ('capability_not_supported_by_backend',)


def test_other_backends_capability_is_unsupported():
    plan = plan_for('regen_chamber', '1.0.0', 'occt', regen(), ('stl',))
    assert reasons('occt', plan)[0] == ('capability_not_supported_by_backend',)
    plan = plan_for('rectangular_cantilever', '1.0.0', 'picogk', beam(), ('stl',))
    assert reasons('picogk', plan)[0] == ('capability_not_supported_by_backend',)


def test_wrong_capability_version_is_unsupported():
    plan = plan_for('rectangular_cantilever', '2.0.0', 'occt', beam(), ('step',))
    assert reasons('occt', plan)[0] == ('capability_not_supported_by_backend',)


def test_descriptor_without_the_capability_is_unsupported():
    # regen ops (0.2.0) listed under a beam plan: descriptors do not list the beam capability
    plan = beam_plan(('step',))
    stream = regen_plan(('stl',)).op_stream
    bad = plan.model_copy(update={'op_stream': stream, 'exports': ('step',)})
    assert reasons('occt', bad)[0] == ('capability_not_supported_by_backend',)


def test_export_not_supported():
    assert reasons('picogk', regen_plan(('step',)))[0] == ('export_not_supported',)
    assert reasons('occt', beam_plan(('stl',)))[0] == ('export_not_supported',)


def test_non_canonical_stream_is_plan_invalid():
    plan = beam_plan(('step',))
    entries = plan.op_stream.entries
    swapped = OpStream(schema_version='mirum_op_stream_v0_1', entries=(
        entries[1].model_copy(update={'seq': 0}), entries[0].model_copy(update={'seq': 1}),
        entries[2]))
    assert reasons('occt', plan.model_copy(update={'op_stream': swapped}))[0] == ('plan_invalid',)
    extra = OpStream(schema_version='mirum_op_stream_v0_1', entries=entries + (
        entries[2].model_copy(update={'seq': 3}),))
    assert reasons('occt', plan.model_copy(update={'op_stream': extra}))[0] == ('plan_invalid',)
    stepref = OpStream(schema_version='mirum_op_stream_v0_1', entries=(
        entries[0].model_copy(update={'step_ref': 'x'}),) + entries[1:])
    assert reasons('occt', plan.model_copy(update={'op_stream': stepref}))[0] == ('plan_invalid',)


def test_candidate_type_mismatch():
    bad = plan_for('rectangular_cantilever', '1.0.0', 'occt', regen(), ('step',))
    assert reasons('occt', bad)[0] == ('candidate_invalid',)
    bad = plan_for('regen_chamber', '1.0.0', 'picogk', beam(), ('stl',))
    assert reasons('picogk', bad)[0] == ('candidate_invalid',)


def test_mock_describe_probe_supports():
    for backend in ('occt', 'picogk'):
        m = MockKernelAdapter(backend)
        assert isinstance(m, KernelAdapter) and m.mode == 'mock' and m.backend == backend
        d = m.describe()
        native = desc(backend)
        assert d.mode == 'mock' and d.adapter_version == 'mock-0.1.0' and d.tolerances == ()
        assert (d.representations, d.export_formats, d.capabilities, d.operations) == (
            native.representations, native.export_formats, native.capabilities, native.operations)
        p = asyncio.run(m.probe())
        assert (p.available, p.reasons, p.mode) == (False, ('mock_backend',), 'mock')
    assert MockKernelAdapter('occt').describe().operations[0] == 'capability.build@0.1.0'
    assert MockKernelAdapter('picogk').describe().operations[0] == 'capability.build@0.2.0'
    assert MockKernelAdapter('occt').supports(beam_plan()).mode == 'mock'
    assert MockKernelAdapter('occt').supports(beam_plan()).supported
    with pytest.raises(ValueError):
        MockKernelAdapter('nope')


def test_mock_build_supported_plan_is_planned_and_labelled_mock():
    for backend, plan in (('occt', beam_plan()), ('picogk', regen_plan())):
        built = asyncio.run(MockKernelAdapter(backend).build(plan, CTX))
        r = built.result
        assert (r.outcome, r.mode, r.reasons) == ('planned', 'mock', ())
        assert r.op_stream == plan.op_stream and r.artifact_graph is None
        assert (r.domain_outcome, r.claim_scope, r.artifacts, r.measurements) == (None, None, (), ())
        assert built.artifacts == () and built.evaluation is None


def test_mock_build_unsupported_plan_keeps_reasons():
    built = asyncio.run(MockKernelAdapter('picogk').build(beam_plan(), CTX))
    assert built.result.outcome == 'unsupported' and built.result.mode == 'mock'
    assert built.result.reasons == ('backend_mismatch',)
    stream = OpStream(schema_version='mirum_op_stream_v0_1', entries=(
        OpStreamEntry(seq=0, operation='construct.extrude', operation_version='0.1.0'),))
    bad = beam_plan().model_copy(update={'op_stream': stream})
    built = asyncio.run(MockKernelAdapter('occt').build(bad, CTX))
    assert built.result.reasons == ('operation_not_executable',)
    assert built.result.diagnostic_code == 'IR009'


def _artifact():
    import hashlib
    data = b'abc'
    return Artifact(ArtifactRef(name='m', format='step', representation='brep',
                                sha256=hashlib.sha256(data).hexdigest(), size=3,
                                node_id='beam.solid'), data)


def test_mock_inspect_and_export():
    m = MockKernelAdapter('occt')
    art = _artifact()
    i = asyncio.run(m.inspect(art, beam_plan()))
    assert (i.outcome, i.reasons, i.mode) == ('unavailable', ('mock_backend',), 'mock')
    assert i.measurements == () and i.artifact_sha256 == art.ref.sha256
    e = m.export(art, 'step')
    assert (e.outcome, e.reasons, e.mode, e.artifact) == (
        'unsupported', ('export_not_supported',), 'mock', None)


def test_mock_cannot_produce_an_accepted_or_qualified_result():
    m = MockKernelAdapter('occt')
    probe = asyncio.run(m.probe())
    with pytest.raises(ValueError):
        backend_lock(m.describe(), probe)
    with pytest.raises(ValueError):
        ProbeResult(backend='occt', mode='mock', available=True)


def test_adapter_for():
    assert isinstance(adapter_for('occt', mode='mock'), MockKernelAdapter)
    assert adapter_for('picogk', mode='mock').backend == 'picogk'
    with pytest.raises(ValueError):
        adapter_for('nope')
    with pytest.raises(ValueError):
        adapter_for('nope', mode='mock')
    with pytest.raises(ValueError):
        adapter_for('occt', mode='weird')


def test_import_hygiene_for_kernel_and_adapters():
    code = (
        "import sys\n"
        "import engineering.program.kernel, engineering.program.adapters\n"
        "import engineering.program.adapters.mock\n"
        "bad = [m for m in ('cadquery','OCP','fastapi','httpx','google.genai','build123d')"
        " if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
