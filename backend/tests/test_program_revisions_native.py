"""F06 native CI evidence (task 7): program revisions over the REAL beam adapter, no Replay.

Native shard only: skipped when cadquery is absent or off posix. Every job here runs the real
CantileverAdapter (real CadQuery evaluation inside ProcessRunner workers) through the existing
search and policy.assess, publishes through a real BundlePublisher into a real SQLiteStore, and every
revision is planned and locked with one OcctAdapter. Nothing is replayed and no verdict is hard-coded:
each assertion reads the outcome from the job record, the revision record or the served bytes.

Tests 1-4 and 6 share one module-scoped world (native work is slow) and one event loop; each rebuilds
the state it needs through ``first_revision`` / ``second_revision``, so a single test also runs alone.
Test 5 (real os._exit crash) owns its own store root in a subprocess.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

cadquery = pytest.importorskip('cadquery')

# process_runner.ProcessRunner returns PROCESS_TREE_UNAVAILABLE off posix, so the native path (probe,
# worker) cannot run on a Windows host even when cadquery imports. The native shards are posix.
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='ProcessRunner needs posix process groups')

from engineering.program.revisions import ProgramRevision, RevisionRefusal  # noqa: E402
from tests.test_program_revisions import (  # noqa: E402  (non-fixture helpers reused from task 4)
    BEAM, CLIENT, REJECTED, WHITESPACE, _finalize_brief, land_staged, stage_final, start_run, step_sha,
)

ROOT = Path(__file__).resolve().parents[2]
WAIT_S = 600


class World:
    pass


async def open_world(root, adapter, occt):
    """Real store, publisher, brief service, job controller (real beam adapter) and RevisionService."""
    from engineering.briefs import BriefService
    from engineering.bundles import BundlePublisher
    from engineering.jobs import JobController
    from engineering.store import SQLiteBriefRepository, SQLiteStore
    w = World()
    w.store = SQLiteStore(root)
    w.jobs = w.svc = None
    try:
        w.pub = BundlePublisher(w.store)
        w.briefs = BriefService(SQLiteBriefRepository(w.store))
        w.jobs = JobController(w.store, publisher=w.pub, adapter=adapter)
        w.svc = RevisionService(w.store, w.jobs, w.pub, w.briefs,
                                adapter_factory=lambda backend, mode='native': occt)
    except BaseException:
        if w.jobs is not None:
            await w.jobs.close()
        w.store.close()
        raise
    w.pid = None
    return w


async def close_world(w):
    try:
        await w.svc.close()
    finally:
        try:
            await w.jobs.close()
        finally:
            w.store.close()


def counting_adapter():
    """The real CantileverAdapter, recording every candidate it really evaluates."""
    from engineering.cantilever import CantileverAdapter

    class Counting(CantileverAdapter):
        def __init__(self):
            self.evaluated = []

        async def evaluate(self, candidate):
            self.evaluated.append(candidate.specification.height_mm)
            return await super().evaluate(candidate)

    return Counting()


def make_occt():
    from engineering.program.adapters.occt import OcctAdapter
    return OcctAdapter()


class Native:
    """Module-scoped world, its own event loop and the state shared between tests."""

    def __init__(self, root):
        self.loop = asyncio.new_event_loop()
        self.adapter = counting_adapter()
        self.state = {}
        try:
            self.w = self.run(open_world(root, self.adapter, make_occt()))
            self.w.pid = self.w.svc.create_program(CLIENT)
        except BaseException:
            self.loop.close()
            raise

    def run(self, coro, timeout=WAIT_S):
        return self.loop.run_until_complete(asyncio.wait_for(coro, timeout))

    def close(self):
        try:
            self.run(close_world(self.w), 120)
        finally:
            self.loop.close()


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    n = Native(tmp_path_factory.mktemp('f06native'))
    try:
        yield n
    finally:
        n.close()


def head_id(w):
    view = w.svc.current(w.pid, client=CLIENT)
    return None if view is None else view.revision.revision_id


def job_run(w, job_id):
    from engineering.jobs import parse_run
    with w.store.transaction() as db:
        return parse_run(db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()['record'])


def heights(run):
    return [a.candidate.specification.height_mm for a in run.attempts]


def assert_selected_15(run):
    """The job completed accepted and the 15 mm candidate (the last attempt) is the selected one."""
    assert run.lifecycle.value == 'completed' and run.outcome.value == 'accepted', run
    assert heights(run) == [5, 10, 15]
    assert [a.outcome.value for a in run.attempts][-1] == 'accepted'
    assert all(a.outcome.value != 'accepted' for a in run.attempts[:-1])
    assert run.selected_candidate_sha256 == run.attempts[-1].candidate.candidate_sha256
    assert run.bundle is not None


def recorded_step_sha(rev):
    return next(p.sha256 for p in rev.bundle.payloads if p.path == 'model.step')


def step_bytes(w, rid):
    download = w.svc.open_artifact(rid, 'step', client=CLIENT)
    try:
        return download.file.read(), download.sha256
    finally:
        download.close()


async def first_revision(n):
    """rev1: beam.mir staged, finalized, run for real and published (cached)."""
    if 'first' not in n.state:
        w = n.w
        assert head_id(w) is None
        rev, brief_id = await stage_final(w, BEAM, None)
        first = await land_staged(w, rev, brief_id)
        assert first.state == 'published', first
        n.state['first'] = (first, step_sha(w, first.revision_id))
    return n.state['first']


async def second_revision(n):
    """rev2: the whitespace variant (same brief), a real new job, published on top of rev1 (cached)."""
    if 'second' not in n.state:
        first, _ = await first_revision(n)
        w = n.w
        assert head_id(w) == first.revision_id
        rev, brief_id = await stage_final(w, WHITESPACE, first.revision_id)
        assert rev.brief_sha256 == first.brief_sha256
        second = await land_staged(w, rev, brief_id)
        assert second.state == 'published', second
        n.state['second'] = second
    return n.state['second']


# ---------------------------------------------------------------- 1. rev1 accepted and published

def test_beam_revision_one_is_accepted_published_and_current(native):
    async def scenario():
        w = native.w
        calls = len(native.adapter.evaluated)
        fresh = 'first' not in native.state
        first, sha = await first_revision(native)
        assert first.published_seq == 1 and first.kind == 'edit' and first.parent is None
        assert first.job_id is not None and first.bundle is not None
        run = job_run(w, first.job_id)
        assert_selected_15(run)
        if fresh:
            assert native.adapter.evaluated[calls:] == [5, 10, 15], 'the real adapter evaluated every candidate'
        view = w.svc.current(w.pid, client=CLIENT)
        assert view.revision.revision_id == first.revision_id and view.is_current
        assert view.artifacts == 'available'
        assert sha == recorded_step_sha(first)
        events = [e.event for e in w.svc.journal(w.pid, client=CLIENT) if e.revision_id == first.revision_id]
        assert events == ['staged', 'admit_intent', 'linked', 'published']
    native.run(scenario())


# ---------------------------------------------------------------- 2. rejected batch

def test_rejected_batch_leaves_rev1_and_its_download_unchanged(native):
    async def scenario():
        w = native.w
        first, sha = await first_revision(native)
        parent = head_id(w)
        rev, brief_id = await stage_final(w, REJECTED, parent)
        done = await land_staged(w, rev, brief_id)
        assert done.state == 'rejected' and done.published_seq is None and done.bundle is None
        run = job_run(w, done.job_id)
        assert run.lifecycle.value == 'completed' and run.outcome.value == 'rejected', run
        assert heights(run) == [5, 10]
        assert all(a.outcome.value != 'accepted' for a in run.attempts)
        assert w.svc.view(done.revision_id, client=CLIENT).artifacts == 'none'
        assert head_id(w) == parent
        view = w.svc.current(w.pid, client=CLIENT)
        assert view.artifacts == 'available'
        assert step_sha(w, first.revision_id) == sha
    native.run(scenario())


# ---------------------------------------------------------------- 3. rev2 then restore rev1

def test_restore_after_rev2_serves_rev1_bytes_exactly(native):
    async def scenario():
        w = native.w
        first, sha1 = await first_revision(native)
        second = await second_revision(native)
        assert second.job_id != first.job_id
        assert_selected_15(job_run(w, second.job_id))
        assert head_id(w) == second.revision_id
        bytes1, digest1 = step_bytes(w, first.revision_id)
        assert digest1 == sha1
        restored = await w.svc.restore(first.revision_id, expected_head=second.revision_id, client=CLIENT)
        assert isinstance(restored, ProgramRevision), restored
        assert restored.kind == 'restore' and restored.state == 'published'
        assert restored.derived_from == first.revision_id and restored.parent == second.revision_id
        assert restored.revision_id not in (first.revision_id, second.revision_id)
        view = w.svc.current(w.pid, client=CLIENT)
        assert view.revision.revision_id == restored.revision_id and view.is_current
        assert view.artifacts == 'available'
        restored_bytes, restored_digest = step_bytes(w, restored.revision_id)
        assert restored_bytes == bytes1 and restored_digest == sha1
        # rev2's own download is still rev2's own file.
        assert step_bytes(w, second.revision_id)[1] == recorded_step_sha(second)
        assert [e.event for e in w.svc.journal(w.pid, client=CLIENT)][-1] == 'restored'
    native.run(scenario())


# ---------------------------------------------------------------- 4. expired, then rebuilt

def test_expired_rev1_refuses_restore_then_rebuild_is_a_real_new_run(native):
    async def scenario():
        w = native.w
        first, _ = await first_revision(native)
        second = await second_revision(native)
        parent = head_id(w)
        with w.store.transaction() as db:
            db.execute('UPDATE publications SET expires=? WHERE job=?', (time.time() - 1, first.job_id))
        out = await w.svc.restore(first.revision_id, expected_head=parent, client=CLIENT)
        assert out == RevisionRefusal(('artifacts_expired',)), out
        assert head_id(w) == parent
        evaluated = len(native.adapter.evaluated)
        staged = await w.svc.rebuild(first.revision_id, expected_parent=parent, client=CLIENT)
        assert isinstance(staged, ProgramRevision), staged
        assert staged.kind == 'rebuild' and staged.state == 'staged' and staged.derived_from == first.revision_id
        assert staged.job_id is None and staged.bundle is None
        brief = _finalize_brief(w.briefs, staged)
        done = await land_staged(w, staged, brief.brief_id)
        assert done.state == 'published' and done.kind == 'rebuild', done
        assert done.job_id not in (first.job_id, second.job_id)
        run = job_run(w, done.job_id)
        assert run.attempts, 'a real re-run records attempts'
        assert_selected_15(run)
        assert native.adapter.evaluated[evaluated:] == [5, 10, 15], 'the real adapter re-evaluated'
        view = w.svc.current(w.pid, client=CLIENT)
        assert view.revision.revision_id == done.revision_id and view.is_current
        assert view.artifacts == 'available'
        assert step_sha(w, done.revision_id) == recorded_step_sha(done)
    native.run(scenario())


# ---------------------------------------------------------------- 6. real cancel during a run

def test_real_cancel_during_a_run_keeps_head_and_releases_the_slot(native):
    from engineering.admission import native_slot

    async def wait_running(w, job_id):
        deadline = time.monotonic() + 120
        while True:
            run = job_run(w, job_id)
            if run.lifecycle.value in ('running', 'packaging') or run.attempts:
                return run
            assert run.lifecycle.value not in ('completed', 'unavailable', 'cancelled'), run
            assert time.monotonic() < deadline, 'job never started running'
            await asyncio.sleep(0.05)

    async def cancel_running(w, parent):
        rev, brief_id = await stage_final(w, WHITESPACE, parent)
        running = await start_run(w, rev, brief_id)
        assert running.job_id is not None
        await wait_running(w, running.job_id)
        await w.svc.cancel(rev.revision_id, client=CLIENT)
        await asyncio.wait_for(w.svc.wait(rev.revision_id), 180)
        done = w.svc.view(rev.revision_id, client=CLIENT).revision
        assert done.state == 'cancelled' and done.published_seq is None and done.bundle is None, done
        assert job_run(w, running.job_id).lifecycle.value == 'cancelled'
        return done

    async def scenario():
        w = native.w
        await first_revision(native)
        parent = head_id(w)
        sha = step_sha(w, parent)
        await cancel_running(w, parent)
        assert head_id(w) == parent and step_sha(w, parent) == sha
        assert w.svc.current(w.pid, client=CLIENT).artifacts == 'available'
        # The native slot was released: a following run is admitted (then cancelled in turn).
        await cancel_running(w, parent)
        assert head_id(w) == parent and step_sha(w, parent) == sha
        owner = object()
        native_slot.claim(owner)
        native_slot.release(owner)
    native.run(scenario())


# ---------------------------------------------------------------- 5. real crash in the worker

async def crash_main(root, marker):
    """Subprocess body: land rev1 for real, then run rev2 whose adapter starts a ProcessRunner child that
    records its pid and sleeps; die with os._exit(17) as soon as the child is up."""
    root, marker = Path(root), Path(marker)
    from engineering.cantilever import CantileverAdapter
    from process_runner import ProcessRunner
    armed = {'on': False}
    code = ('import os,pathlib,time; '
            f'pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)')

    class Worker(CantileverAdapter):
        async def evaluate(self, candidate):
            if not armed['on']:
                return await super().evaluate(candidate)
            runner = ProcessRunner(timeout=60)
            try:
                await runner.run([sys.executable, '-c', code], env={'PATH': os.environ['PATH']})
            finally:
                await runner.close()

    w = await open_world(root, Worker(), make_occt())
    w.pid = w.svc.create_program(CLIENT)
    rev, brief_id = await stage_final(w, BEAM, None)
    first = await land_staged(w, rev, brief_id)
    assert first.state == 'published', first
    second, second_brief = await stage_final(w, WHITESPACE, first.revision_id)
    (root / 'state.json').write_text(json.dumps(dict(
        pid=w.pid, first=first.revision_id, second=second.revision_id, sha=step_sha(w, first.revision_id))),
        encoding='utf-8')
    armed['on'] = True
    await w.svc.run(second.revision_id, second_brief, client=CLIENT)
    for _ in range(6000):
        if marker.exists() and marker.read_text(encoding='utf-8').strip():
            os._exit(17)
        await asyncio.sleep(0.01)
    os._exit(3)  # the worker never started


def test_real_crash_during_the_worker_restart_keeps_head_and_reaps_the_worker(tmp_path):
    marker = tmp_path / 'worker.pid'
    script = ('import asyncio, sys\n'
              'from tests.test_program_revisions_native import crash_main\n'
              'asyncio.run(crash_main(sys.argv[1], sys.argv[2]))\n')
    env = dict(os.environ, PYTHONPATH=f'{ROOT / "backend"}{os.pathsep}{ROOT}')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), str(marker)],
                            timeout=WAIT_S, capture_output=True, env=env, cwd=str(ROOT))
    assert result.returncode == 17, result.stderr.decode(errors='replace')[-2000:]
    state = json.loads((tmp_path / 'state.json').read_text(encoding='utf-8'))
    pid = int(marker.read_text(encoding='utf-8'))

    async def scenario():
        # The crashed process held the volume owner lock; it is gone, so the root reopens here.
        w = await open_world(tmp_path, counting_adapter(), make_occt())
        w.pid = state['pid']
        try:
            history = {r.revision_id: r for r in w.svc.history(w.pid, client=CLIENT)}
            assert set(history) == {state['first'], state['second']}
            assert history[state['first']].state == 'published'
            crashed = history[state['second']]
            assert crashed.state in ('failed', 'cancelled'), crashed
            assert crashed.published_seq is None and crashed.bundle is None
            assert crashed.job_id is not None
            job = job_run(w, crashed.job_id)
            assert job.lifecycle.value in ('unavailable', 'cancelled') and job.bundle is None
            assert head_id(w) == state['first']
            view = w.svc.current(w.pid, client=CLIENT)
            assert view.artifacts == 'available'
            assert step_sha(w, state['first']) == state['sha']
            assert list(w.pub.staging.iterdir()) == []
            assert ('recovered', state['second']) in [(e.event, e.revision_id)
                                                      for e in w.svc.journal(w.pid, client=CLIENT)]
            for _ in range(200):
                try:
                    stat = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1][0]
                except (FileNotFoundError, ProcessLookupError):
                    break  # reaped: gone
                if stat == 'Z':
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail('Owned worker remained alive after recovery')
        finally:
            await close_world(w)
    asyncio.run(asyncio.wait_for(scenario(), 300))


def test_this_module_is_ascii_lf():
    text = Path(__file__).read_bytes()
    assert text.isascii() and b'\r' not in text and not text.endswith(b'\n\n')
