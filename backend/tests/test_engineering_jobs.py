"""Durable controller lifecycle; publisher failures are explicitly seams."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import subprocess
import time
import pytest

from backend.tests.test_engineering_contracts import data, review_for
from engineering.briefs import BriefService, RevisionConflict
from engineering.store import SQLiteStore, SQLiteBriefRepository


def controller(tmp_path, **kwargs):
    from engineering.jobs import JobController
    store = SQLiteStore(tmp_path)
    svc = BriefService(SQLiteBriefRepository(store))
    draft = svc.create(data()['content'])
    brief = svc.finalize(review_for(draft.draft))
    return store, svc, brief, JobController(store, **kwargs)


def test_idempotency_overload_and_one_terminal_commit(tmp_path):
    from engineering.admission import AdmissionBusy
    async def run():
        store, svc, brief, jobs = controller(tmp_path)
        first = jobs.submit(brief.brief_id, 'key')
        assert jobs.submit(brief.brief_id, 'key').job_id == first.job_id
        with pytest.raises(AdmissionBusy):
            jobs.submit(brief.brief_id, 'other')
        second = svc.create(data()['content'])
        second_brief = svc.finalize(review_for(second.draft))
        with pytest.raises(RevisionConflict):
            jobs.submit(second_brief.brief_id, 'key')
        jobs.cancel(first.job_id)
        await jobs.wait(first.job_id)
        terminal = jobs.read(first.job_id)
        assert terminal.lifecycle == 'cancelled'
        assert jobs.cancel(first.job_id) == terminal
        assert jobs.submit(brief.brief_id, 'key') == terminal
        await jobs.close()
        store.close()
    asyncio.run(run())


def test_atomic_cancel_wins_and_repeated_terminal_transitions_are_immutable(tmp_path):
    from engineering.contracts import Lifecycle
    async def run():
        store, _, brief, jobs = controller(tmp_path)
        job = jobs.submit(brief.brief_id, 'terminal')
        jobs.cancel(job.job_id)
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: jobs._terminal(job.job_id, Lifecycle.UNAVAILABLE,
                failures=('injected terminal race',)), range(4)))
        assert all(r == results[0] for r in results)
        assert results[0].lifecycle == 'cancelled'
        await jobs.close()
        store.close()
    asyncio.run(run())


def test_real_native_selection_cannot_commit_without_publication(tmp_path):
    async def run():
        store, _, brief, jobs = controller(tmp_path)
        job = jobs.submit(brief.brief_id, 'native')
        await jobs.wait(job.job_id)
        result = jobs.read(job.job_id)
        assert result.lifecycle == 'unavailable'
        assert [a.candidate.specification.height_mm for a in result.attempts] == [5, 10, 15]
        assert result.attempts[-1].outcome == 'accepted'
        assert result.outcome == 'not_evaluated' and result.bundle is None
        await jobs.close()
        store.close()
    asyncio.run(run())


@pytest.mark.parametrize('stop', ['cancel', 'deadline', 'close'])
def test_owned_real_worker_cleanup_and_legacy_coexistence(tmp_path, stop):
    from engineering.admission import native_slot, AdmissionBusy
    from engineering.cantilever import CantileverAdapter
    from process_runner import ProcessRunner
    marker = tmp_path / 'child.pid'
    class Worker(CantileverAdapter):
        async def evaluate(self, candidate):
            runner = ProcessRunner(timeout=30)
            try:
                await runner.run([sys.executable, '-c',
                    f'import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)'],
                    env={'PATH': os.environ['PATH']})
            finally:
                await runner.close()
    async def run():
        store, _, brief, jobs = controller(tmp_path, adapter=Worker(), overall_seconds=1 if stop == 'deadline' else 420)
        job = jobs.submit(brief.brief_id, stop)
        with pytest.raises(AdmissionBusy):
            native_slot.claim(object())
        for _ in range(300):
            if marker.exists(): break
            await asyncio.sleep(.01)
        assert marker.exists()
        pid = int(marker.read_text())
        if stop == 'cancel': jobs.cancel(job.job_id)
        if stop == 'close': await jobs.close()
        await jobs.wait(job.job_id)
        assert jobs.read(job.job_id).lifecycle == ('unavailable' if stop == 'deadline' else 'cancelled')
        assert not Path(f'/proc/{pid}').exists()
        owner = object()
        native_slot.claim(owner)
        native_slot.release(owner)
        await jobs.close()
        store.close()
    asyncio.run(run())


def test_restart_reconciles_interrupted_jobs_without_acceptance(tmp_path):
    async def run():
        store, _, brief, jobs = controller(tmp_path)
        job = jobs.submit(brief.brief_id, 'restart')
        # Exercise durable recovery after a stopped owner; no native process yet.
        await jobs.close()
        with store.transaction() as db:
            db.execute('UPDATE jobs SET record=?,cancel=0 WHERE id=?', (job.model_dump_json(), job.job_id))
        store.close()
        from engineering.jobs import JobController
        store = SQLiteStore(tmp_path)
        recovered = JobController(store)
        assert recovered.read(job.job_id).lifecycle == 'unavailable'
        assert recovered.read(job.job_id).bundle is None
        await recovered.close()
        store.close()
    asyncio.run(run())


def test_actual_crash_recovers_tagged_worker_and_persistent_brief(tmp_path):
    script = r'''
import asyncio, os, pathlib, sys
from engineering.cantilever import CantileverAdapter
from process_runner import ProcessRunner
from backend.tests.test_engineering_jobs import controller
root = pathlib.Path(sys.argv[1])
marker = root / 'orphan.pid'
class Worker(CantileverAdapter):
    async def evaluate(self, candidate):
        await ProcessRunner(timeout=30).run([sys.executable, '-c',
            f'import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)'], env={})
async def main():
    store, _, brief, jobs = controller(root, adapter=Worker())
    job = jobs.submit(brief.brief_id, 'crash')
    (root/'job.id').write_text(job.job_id)
    for _ in range(500):
        if marker.exists(): os._exit(17)
        await asyncio.sleep(.01)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], timeout=15, capture_output=True)
    assert result.returncode == 17, result.stderr.decode()
    pid = int((tmp_path / 'orphan.pid').read_text())
    from engineering.jobs import JobController
    async def recover():
        store = SQLiteStore(tmp_path)
        jobs = JobController(store)
        job = jobs.read((tmp_path / 'job.id').read_text())
        assert job.lifecycle == 'unavailable' and job.bundle is None
        for _ in range(100):
            path = Path(f'/proc/{pid}/stat')
            if not path.exists() or path.read_text().split(') ',1)[1][0] == 'Z': break
            await asyncio.sleep(.01)
        else: pytest.fail('Owned worker remained alive after recovery')
        await jobs.close()
        store.close()
    asyncio.run(recover())
