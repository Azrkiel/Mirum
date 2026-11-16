"""Integrated wedge delivery: real native success; failures cannot publish."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from backend.dragsters.spec import DragsterSpec
from backend.dragsters import service
from backend import artifacts

EXAMPLE = Path(__file__).parents[1] / 'dragsters/examples/wedge.json'


@pytest.fixture
def spec():
    return DragsterSpec.model_validate_json(EXAMPLE.read_text())


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(artifacts, '_RUNS_DIR', tmp_path / 'runs')
    return artifacts


@pytest.mark.asyncio
async def test_native_delivery_and_binding(spec, store):
    result = await service.generate(spec)
    run_id = result['run_id']
    delivered = {fmt: store.artifact_path(run_id, fmt).read_bytes() for fmt in ('spec', 'report', 'step', 'glb')}
    assert json.loads(delivered['spec']) == spec.model_dump()
    report = json.loads(delivered['report'])
    assert report['passed'] is True
    assert report['revision_id'] == run_id == result['revision_id']
    assert report['spec_sha256'] == hashlib.sha256(delivered['spec']).hexdigest()
    assert report['step_sha256'] == hashlib.sha256(delivered['step']).hexdigest()
    assert report['preview']['glb_sha256'] == hashlib.sha256(delivered['glb']).hexdigest()
    assert result['digests'] == {fmt: hashlib.sha256(data).hexdigest() for fmt, data in delivered.items()}
    assert delivered['glb'][:4] == b'glTF'


@pytest.mark.asyncio
@pytest.mark.parametrize('fmt', ['step', 'glb', 'spec', 'report'])
async def test_required_publication_failure(spec, store, monkeypatch, fmt):
    original = store.store
    monkeypatch.setattr(store, 'store', lambda run, kind, data, **kwargs: False if kind == fmt else original(run, kind, data, **kwargs))
    with pytest.raises(service.GenerationError, match='publication'):
        await service.generate(spec)
    assert not list(store._RUNS_DIR.glob('[0-9a-f]' * 32))


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['TIMEOUT', 'OUTPUT_LIMIT'])
async def test_worker_failure_does_not_publish(spec, store, monkeypatch, code):
    async def fail(*args, **kwargs):
        return dict(code=code, returncode=-9, stderr='', stdout='')
    monkeypatch.setattr(service.ProcessRunner, 'run', fail)
    with pytest.raises(service.GenerationError, match=code):
        await service.generate(spec)
    assert not store._RUNS_DIR.exists()


@pytest.mark.asyncio
async def test_concurrent_native_attempts_remain_distinct(spec, store):
    other = DragsterSpec.model_validate({**spec.model_dump(), 'width_mm': 46.0})
    first, second = await asyncio.gather(service.generate(spec), service.generate(other))
    assert first['run_id'] != second['run_id']
    for result, width in [(first, 42.0), (second, 46.0)]:
        saved = json.loads(store.artifact_path(result['run_id'], 'spec').read_bytes())
        assert saved['width_mm'] == width
        assert result['report']['bounds_mm']['ymax'] == pytest.approx(width/2)
        assert hashlib.sha256(store.artifact_path(result['run_id'], 'step').read_bytes()).hexdigest() == result['report']['step_sha256']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['step', 'glb'])
async def test_native_check_or_preview_failure_cleans_attempt(spec, store, monkeypatch, tmp_path, failure):
    original = service.ProcessRunner.run
    backend = str(Path(__file__).resolve().parents[1])
    module, function = ('dragsters.validation', 'verify_step') if failure == 'step' else ('dragsters.local', 'export_preview')
    async def injected(self, command, **kwargs):
        source = (f'import sys; sys.path.insert(0, {backend!r}); '
                  f'import {module} as target; '
                  f'defect = lambda *a, **k: (_ for _ in ()).throw(ValueError("forced {failure} failure")); '
                  f'target.{function} = defect; '
                  'from dragsters.worker import main; '
                  f'sys.argv = ["worker", {command[-1]!r}]; raise SystemExit(main())')
        return await original(self, [command[0], '-c', source], **kwargs)
    monkeypatch.setattr(service, '_WORK_DIR', tmp_path / 'scratch')
    monkeypatch.setattr(service.ProcessRunner, 'run', injected)
    with pytest.raises(service.GenerationError, match=f'forced {failure} failure'):
        await service.generate(spec)
    assert not list(service._WORK_DIR.iterdir())
    assert not store._RUNS_DIR.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [True, False])
async def test_service_real_process_timeout_and_cancellation_cleanup(spec, store, monkeypatch, tmp_path, cancel):
    from backend.tests.test_process_lifecycle_ports import alive
    original = service.ProcessRunner.run
    started = asyncio.Event()
    marker = tmp_path / 'pid'
    monkeypatch.setattr(service, '_WORK_DIR', tmp_path / 'scratch')
    async def delayed(self, command, **kwargs):
        code = f'import os,time,pathlib; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)'
        task = asyncio.create_task(original(self, [command[0], '-c', code], **kwargs))
        for _ in range(200):
            if marker.exists():
                break
            await asyncio.sleep(.01)
        started.set()
        try:
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    monkeypatch.setattr(service.ProcessRunner, 'run', delayed)
    runner = service.ProcessRunner(timeout=5 if cancel else .5)
    task = asyncio.create_task(service.generate(spec, runner=runner))
    await asyncio.wait_for(started.wait(), 3)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(service.GenerationError, match='TIMEOUT'):
            await task
    await runner.close()
    assert not alive(int(marker.read_text()))
    assert not list(service._WORK_DIR.iterdir())
    assert not store._RUNS_DIR.exists()


def test_publication_is_atomic_and_collision_preserves_existing(store, monkeypatch):
    run_id = 'a' * 32
    payloads = {fmt: fmt.encode() for fmt in ('step', 'glb', 'spec', 'report')}
    original = store.store
    observed = []
    def observe(*args, **kwargs):
        observed.append(store.run_dir(run_id).exists())
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'store', observe)
    store.publish_required(run_id, payloads)
    assert observed == [False] * 4
    with pytest.raises(OSError, match='occupied'):
        store.publish_required(run_id, {fmt: b'new' for fmt in payloads})
    assert {fmt: store.artifact_path(run_id, fmt).read_bytes() for fmt in payloads} == payloads
    assert not list(store._RUNS_DIR.glob('.publish-*'))


@pytest.mark.asyncio
async def test_stream_disconnect_cleans_native_and_emits_no_acceptance(spec, monkeypatch):
    from backend.dragsters import api
    from types import SimpleNamespace
    cleaned = asyncio.Event()
    async def slow(*a, **kw):
        try:
            await asyncio.sleep(60)
        finally:
            cleaned.set()
    class Request:
        state = SimpleNamespace(dragster_attempt_id='attempt')
        async def is_disconnected(self):
            return True
    monkeypatch.setattr(api, 'generate', slow)
    seen = [value async for value in api.stream(spec, Request(), str)]
    assert cleaned.is_set()
    assert len(seen) == 1 and 'running' in seen[0]
    assert not any('"done"' in value for value in seen)


@pytest.mark.asyncio
async def test_failed_success_send_discards_unclaimed_revision(spec, store, monkeypatch):
    from backend.dragsters import api
    from types import SimpleNamespace
    run_id = 'b' * 32
    store.publish_required(run_id, {fmt:b'accepted' for fmt in ('step','glb','spec','report')})
    async def completed(*a, **kw):
        return dict(run_id=run_id)
    class Request:
        state = SimpleNamespace(dragster_attempt_id='attempt')
        async def is_disconnected(self):
            return False
    monkeypatch.setattr(api, 'generate', completed)
    stream = api.stream(spec, Request(), str)
    assert 'running' in await anext(stream)
    assert 'done' in await anext(stream)
    await stream.aclose()  # ASGI could not send the yielded success frame
    assert not store.run_dir(run_id).exists()


@pytest.mark.asyncio
async def test_disconnect_reaps_actual_native_worker(spec, store, monkeypatch, tmp_path):
    from backend.dragsters import api
    from backend.tests.test_process_lifecycle_ports import alive
    from types import SimpleNamespace
    original = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    processes = []
    monkeypatch.setattr(service, '_WORK_DIR', tmp_path / 'scratch')
    async def capture(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        spawned.set()
        return process
    class Request:
        state = SimpleNamespace(dragster_attempt_id='disconnect')
        async def is_disconnected(self):
            await asyncio.wait_for(spawned.wait(), 3)
            return True
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
    seen = [item async for item in api.stream(spec, Request(), str)]
    assert len(processes) == 1 and not alive(processes[0].pid)
    assert len(seen) == 1 and 'running' in seen[0]
    assert not list(service._WORK_DIR.iterdir())
    assert not store._RUNS_DIR.exists()


@pytest.mark.asyncio
async def test_changed_native_artifact_is_not_published(spec, store, monkeypatch, tmp_path):
    original = service.ProcessRunner.run
    monkeypatch.setattr(service, '_WORK_DIR', tmp_path / 'scratch')
    async def tamper(self, command, **kwargs):
        assert set(kwargs['env']).isdisjoint({'GEMINI_API_KEY_HEAVY', 'FLY_API_TOKEN'})
        assert self.memory_mb == 1536 and self.timeout == 30 and self.output_limit == 1048576
        result = await original(self, command, **kwargs)
        assert result['returncode'] == 0
        next(Path(command[-1]).glob('dragster-*/body.step')).write_bytes(b'changed')
        return result
    monkeypatch.setattr(service.ProcessRunner, 'run', tamper)
    with pytest.raises(service.GenerationError, match='evidence mismatch'):
        await service.generate(spec)
    assert not list(service._WORK_DIR.iterdir())
    assert not store._RUNS_DIR.exists()


def test_oversized_failed_publication_does_not_evict_prior_run(store, monkeypatch):
    prior, attempt = 'c'*32, 'd'*32
    files = {fmt:b'prior' for fmt in ('step','glb','spec','report')}
    store.publish_required(prior, files)
    monkeypatch.setattr(store, '_MAX_TOTAL_BYTES', 20)
    with pytest.raises(OSError, match='storage budget'):
        store.publish_required(attempt, {fmt:b'oversized' for fmt in files})
    assert {fmt:store.artifact_path(prior, fmt).read_bytes() for fmt in files} == files
    assert not store.run_dir(attempt).exists()
