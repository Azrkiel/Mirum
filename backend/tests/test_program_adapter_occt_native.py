"""F03 OCCT kernel adapter conformance against the real CadQuery beam path.

Native shard only: skipped when cadquery is absent (every host without the native stack). The adapter
is a thin wrapper over engineering.cantilever.CantileverAdapter, so the reference verdicts must come
out unchanged. Measurements are compared analytically, never by artifact bytes.
"""
import asyncio
import os

import pytest

cadquery = pytest.importorskip('cadquery')

# process_runner.ProcessRunner returns PROCESS_TREE_UNAVAILABLE off posix, so the native path (probe,
# worker) cannot run on a Windows host even when cadquery imports. The native shards are posix.
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='ProcessRunner needs posix process groups')

from backend.tests.test_engineering_contracts import finalized  # noqa: E402

CAPABILITY = ('rectangular_cantilever', '1.0.0')
HEIGHTS = (5.0, 10.0, 15.0)
MIB = 1024 * 1024


def run(coro):
    return asyncio.run(coro)


def context(deadline_s=300):
    from engineering.program.kernel import ExecutionContext
    return ExecutionContext(deadline_s=deadline_s, max_artifact_bytes=64 * MIB, workspace='f03-native')


def plan_of(candidate, exports=('step', 'glb')):
    from engineering.program import kernel
    return kernel.plan_for(CAPABILITY[0], CAPABILITY[1], 'occt', candidate, exports)


def candidates():
    from engineering.cantilever import CantileverAdapter
    found = CantileverAdapter().candidates(finalized())
    assert [c.specification.height_mm for c in found] == list(HEIGHTS)
    return found


def measure(measurements, name):
    found = [m for m in measurements if m.name == name]
    assert len(found) == 1, (name, [m.name for m in measurements])
    return found[0]


@pytest.fixture(scope='module')
def adapter():
    from engineering.program.adapters.occt import OcctAdapter
    return OcctAdapter()


@pytest.fixture(scope='module')
def builds(adapter):
    """Build the three reference candidates once (native work is slow)."""
    async def go():
        out = []
        for candidate in candidates():
            plan = plan_of(candidate)
            out.append((plan, await adapter.build(plan, context())))
        return out
    return run(go())


def test_probe_reports_native_versions_and_locks(adapter):
    from engineering.program.kernel import backend_lock
    probe = run(adapter.probe())
    assert probe.available is True and probe.mode == 'native' and probe.reasons == ()
    names = {v.name for v in probe.native_versions}
    assert {'cadquery', 'OCP'} <= names
    lock = backend_lock(adapter.describe(), probe)
    assert lock.mode == 'native' and lock.backend == 'occt'


def test_reference_verdicts_unchanged_through_adapter(builds):
    assert [b.result.domain_outcome for _, b in builds] == ['rejected', 'rejected', 'accepted']
    for _, built in builds:
        result = built.result
        assert result.mode == 'native'
        assert result.outcome == 'completed'
        assert result.claim_scope == 'checked_design'
    for _, built in builds[:2]:
        assert built.result.artifacts == ()
        assert built.artifacts == ()
        assert built.result.artifact_graph.nodes == ()
    plan, accepted = builds[2]
    result = accepted.result
    by_format = {a.format: a for a in result.artifacts}
    assert set(by_format) == {'step', 'glb'} and len(result.artifacts) == 2
    assert (by_format['step'].representation, by_format['step'].node_id) == ('brep', 'beam.solid')
    assert (by_format['glb'].representation, by_format['glb'].node_id) == ('mesh', 'beam.preview')
    assert {n.id for n in result.artifact_graph.nodes} == {'beam.solid', 'beam.preview'}
    ops = [(e.operation, e.operation_version, e.seq) for e in result.op_stream.entries]
    assert ops == [('capability.build', '0.1.0', 0), ('capability.inspect', '0.1.0', 1),
                   ('capability.export', '0.1.0', 2), ('capability.export', '0.1.0', 3)]


def test_analytic_conformance_of_the_accepted_beam(builds):
    plan, built = builds[2]
    spec = plan.candidate.specification
    tol = spec.dimensional_tolerance_mm
    measurements = built.result.measurements
    length, width, height = (measure(measurements, f'beam.{n}') for n in ('length', 'width', 'height'))
    for m, expected in ((length, 200.0), (width, 10.0), (height, 15.0)):
        assert m.unit == 'mm' and m.source == 'report'
        assert abs(m.value - expected) <= tol
    volume = measure(measurements, 'beam.volume')
    assert volume.unit == 'mm3'
    product = length.value * width.value * height.value
    assert abs(volume.value - product) <= max(1e-6, volume.value * 1e-8)
    # Volume implied by dimensions each within tol of 200 x 10 x 15 mm.
    bound = (200.0 + tol) * (10.0 + tol) * (15.0 + tol) - 200.0 * 10.0 * 15.0
    assert abs(volume.value - 30000.0) <= bound
    # Orientation verdicts are copied from the native report, never recomputed here.
    checks = built.evaluation['report']['checks']
    for name in ('datum_frame', 'axis_aligned_faces'):
        assert checks[name]['state'] == 'passed'


def test_reread_is_consistent_and_precision_failure_is_typed(adapter, builds):
    plan15, built15 = builds[2]
    plan5 = builds[0][0]
    step = next(a for a in built15.artifacts if a.ref.format == 'step')
    tol = plan15.candidate.specification.dimensional_tolerance_mm
    ok = run(adapter.inspect(step, plan15))
    assert ok.outcome == 'consistent' and ok.reasons == ()
    assert ok.mode == 'native'
    assert ok.artifact_sha256 == step.ref.sha256
    assert abs(measure(ok.measurements, 'beam.height').value - 15.0) <= tol
    bad = run(adapter.inspect(step, plan5))
    assert bad.outcome == 'precision_failure'
    assert 'measurement_out_of_tolerance' in bad.reasons
    assert bad.outcome not in ('unsupported', 'unavailable')


def test_unsupported_requests_never_convert(adapter, builds):
    plan15, built15 = builds[2]
    stl_plan = plan_of(plan15.candidate, exports=('stl',))
    built = run(adapter.build(stl_plan, context()))
    assert built.result.outcome == 'unsupported'
    assert 'export_not_supported' in built.result.reasons
    assert built.artifacts == ()
    glb = next(a for a in built15.artifacts if a.ref.format == 'glb')
    assert run(adapter.inspect(glb, plan15)).outcome == 'unsupported'
    step = next(a for a in built15.artifacts if a.ref.format == 'step')
    exported = adapter.export(step, 'stl')
    assert exported.outcome == 'unsupported' and exported.artifact is None
    assert 'export_not_supported' in exported.reasons


def test_missing_runtime_is_unavailable_not_unsupported(tmp_path, builds):
    from engineering.program.adapters.occt import OcctAdapter
    missing = OcctAdapter(python=str(tmp_path / 'missing-python'))
    probe = run(missing.probe())
    assert probe.available is False and probe.reasons == ('runtime_missing',)
    built = run(missing.build(builds[2][0], context()))
    assert built.result.outcome == 'unavailable'
    assert built.result.outcome != 'unsupported'
    assert 'runtime_missing' in built.result.reasons
    assert built.artifacts == ()


def test_deadline_is_unavailable(adapter, builds):
    built = run(adapter.build(builds[2][0], context(deadline_s=0.001)))
    assert built.result.outcome == 'unavailable'
    assert 'deadline_exceeded' in built.result.reasons


def test_cancellation_propagates_and_leaves_runtime_usable(adapter, builds):
    plan15 = builds[2][0]

    async def go():
        task = asyncio.create_task(adapter.build(plan15, context()))
        await asyncio.sleep(3)
        assert not task.done()
        task.cancel()
        start = asyncio.get_running_loop().time()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 60)
        assert asyncio.get_running_loop().time() - start < 60
        return await adapter.probe()

    probe = run(go())
    assert probe.available is True


def test_mock_cannot_satisfy_this_gate(adapter, builds):
    assert adapter.mode == 'native'
    assert all(b.result.mode == 'native' for _, b in builds)
    assert adapter.describe().mode == 'native'
