"""Single-process durable job controller. Publication is a fail-closed seam."""
import asyncio
from datetime import datetime, timezone
import math
import time
from uuid import uuid4

from .admission import AdmissionBusy, native_slot
from .briefs import RevisionConflict
from .capabilities import CAPABILITY
from .contracts import EngineeringRun, Lifecycle, Outcome, ContractModel, Text, Sha256, CapabilityReference
from pydantic import AwareDatetime
from typing import Literal
from .search import search, _controlled, _Stopped
from .store import SQLiteBriefRepository

TERMINAL = {Lifecycle.COMPLETED, Lifecycle.CANCELLED, Lifecycle.UNAVAILABLE}


class ExpiredRun(ContractModel):
    schema_version: Literal['engineering_expired_run_v1']
    job_id: Text
    capability: CapabilityReference
    brief_sha256: Sha256
    fixed_requirements_sha256: Sha256
    lifecycle: Lifecycle
    outcome: Outcome
    created_at: AwareDatetime
    updated_at: AwareDatetime
    expired: Literal[True]


def compact_run(record):
    fields=('job_id','capability','brief_sha256','fixed_requirements_sha256','lifecycle','outcome','created_at','updated_at')
    return dict({k:record[k] for k in fields},schema_version='engineering_expired_run_v1',expired=True)


def parse_run(value):
    import json
    model=ExpiredRun if json.loads(value)['schema_version']=='engineering_expired_run_v1' else EngineeringRun
    return model.model_validate_json(value)


def changed(run, **updates):
    return EngineeringRun.model_validate(dict(run.model_dump(), updated_at=datetime.now(timezone.utc), **updates))


class JobController:
    """Publisher: reserve/cleanup, async publish(job, brief, search_result), validate.

    B10 supplies durable publication. No publisher means no accepted terminal
    commit. Polling/read never starts work; only submit creates an owned task.
    """
    def __init__(self, store, *, publisher=None, adapter=None, overall_seconds=420):
        if isinstance(overall_seconds, bool) or not math.isfinite(overall_seconds) or not 0 < overall_seconds <= 420:
            raise ValueError('Overall deadline must be positive and at most 420 seconds')
        if getattr(store, '_controller_created', False):
            raise RuntimeError('Engineering store already has a controller owner')
        store._controller_created = True
        self.store, self.publisher, self.adapter = store, publisher, adapter
        self.seconds, self.tasks = overall_seconds, {}
        self.closed = False
        store.recover()
        self._recover_workers()
        with store.transaction() as db:
            for row in db.execute('SELECT id,record,cancel FROM jobs').fetchall():
                run = parse_run(row['record'])
                if run.lifecycle not in TERMINAL:
                    run = changed(run, lifecycle=Lifecycle.CANCELLED if row['cancel'] else Lifecycle.UNAVAILABLE,
                                  outcome=Outcome.NOT_EVALUATED, bundle=None,
                                  failures=('Process interrupted; evidence was not committed',))
                    db.execute('UPDATE jobs SET record=?,reserved=0 WHERE id=?', (run.model_dump_json(), row['id']))
        if publisher:
            publisher.recover()

    def _recover_workers(self):
        from process_runner import recover_process_owner
        with self.store.transaction() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='process_owner'").fetchone()
            if row:
                recover_process_owner(row[0])
            self.process_owner = uuid4().hex
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('process_owner',?)", (self.process_owner,))

    def read(self, job_id):
        if self.publisher:
            self.publisher.cleanup()
        with self.store.transaction() as db:
            row = db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return parse_run(row[0])

    def submit(self, brief_id, key):
        if self.closed:
            raise RuntimeError('Engineering controller is closed')
        self.tasks = {key: task for key, task in self.tasks.items() if not task.done()}
        if type(key) is not str or not key.strip() or len(key) > 128:
            raise ValueError('Idempotency key must contain 1-128 characters')
        if self.publisher:
            self.publisher.cleanup()
        with self.store.transaction() as db:
            previous=db.execute('SELECT brief,record FROM jobs WHERE key=?',(key,)).fetchone()
            if previous:
                if previous['brief'] != brief_id:
                    raise RevisionConflict('Idempotency key belongs to a different finalized brief')
                return parse_run(previous['record'])
        brief = SQLiteBriefRepository(self.store).read_brief(brief_id)
        owner = object()
        with self.store.transaction() as db:
            row = db.execute('SELECT brief,record FROM jobs WHERE key=?', (key,)).fetchone()
            if row:
                if row['brief'] != brief_id:
                    raise RevisionConflict('Idempotency key belongs to a different finalized brief')
                return parse_run(row['record'])
            reserved = db.execute('SELECT COALESCE(SUM(reserved),0), COUNT(*) FROM jobs WHERE reserved>0').fetchone()
            usage=max(reserved[0],self.store.storage_bytes())
            if usage + CAPABILITY.resources.run_bytes > CAPABILITY.resources.storage_bytes or reserved[1] >= 50:
                raise AdmissionBusy('Engineering storage capacity exhausted; retry after expiry')
            native_slot.claim(owner)
            try:
                now = datetime.now(timezone.utc)
                job = EngineeringRun(schema_version='engineering_run_v1', job_id=uuid4().hex,
                    capability=brief.content.capability, brief_sha256=brief.brief_sha256,
                    fixed_requirements_sha256=brief.fixed_requirements_sha256,
                    lifecycle=Lifecycle.ADMITTED, outcome=Outcome.NOT_EVALUATED, attempts=(), created_at=now, updated_at=now)
                db.execute('INSERT INTO jobs(id,brief,key,record,deadline,reserved) VALUES (?,?,?,?,?,?)',
                           (job.job_id, brief_id, key, job.model_dump_json(), time.time()+self.seconds, CAPABILITY.resources.run_bytes))
            except BaseException:
                native_slot.release(owner)
                raise
        try:
            self.tasks[job.job_id] = asyncio.create_task(self._run(job, brief, owner, time.monotonic()+self.seconds))
        except BaseException:
            native_slot.release(owner)
            self._terminal(job.job_id, Lifecycle.UNAVAILABLE, failures=('Unable to start execution',))
            raise
        return job

    def _cancelled(self, job_id):
        with self.store.transaction() as db:
            return bool(db.execute('SELECT cancel FROM jobs WHERE id=?', (job_id,)).fetchone()[0])

    def cancel(self, job_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            run = parse_run(row[0])
            if run.lifecycle not in TERMINAL:
                db.execute('UPDATE jobs SET cancel=1 WHERE id=?', (job_id,))
            return run

    def _update(self, job_id, **updates):
        with self.store.transaction() as db:
            row = db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
            run = parse_run(row[0])
            if run.lifecycle in TERMINAL:
                return run
            run = changed(run, **updates)
            db.execute('UPDATE jobs SET record=? WHERE id=?', (run.model_dump_json(), job_id))
            return run

    def _terminal(self, job_id, lifecycle, *, outcome=Outcome.NOT_EVALUATED, failures=(), bundle=None):
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            run = parse_run(row['record'])
            if run.lifecycle in TERMINAL:
                return run
            if row['cancel']:
                lifecycle, outcome, failures, bundle = Lifecycle.CANCELLED, Outcome.NOT_EVALUATED, ('Cancellation requested before terminal commit',), None
            elif time.time() >= row['deadline']:
                lifecycle, outcome, failures, bundle = Lifecycle.UNAVAILABLE, Outcome.NOT_EVALUATED, ('Overall engineering deadline exceeded',), None
            if outcome == Outcome.ACCEPTED:
                if self.publisher is None or bundle is None:
                    raise ValueError('Durable complete publication is required')
                self.publisher.validate(job_id, bundle)
            run = changed(run, lifecycle=lifecycle, outcome=outcome, failures=tuple(failures), bundle=bundle)
            db.execute('UPDATE jobs SET record=?,reserved=?,expires=? WHERE id=?',
                       (run.model_dump_json(), bundle.stored_bytes if bundle else self.publisher.stored_bytes(job_id) if self.publisher else 0,
                        time.time()+CAPABILITY.resources.bundle_retention_seconds, job_id))
            return run

    async def _run(self, job, brief, owner, deadline):
        from process_runner import process_owner
        token = process_owner.set(self.process_owner)
        try:
            self._update(job.job_id, lifecycle=Lifecycle.RUNNING)
            async def progress(event):
                if event.attempt:
                    current = self.read(job.job_id)
                    self._update(job.job_id, attempts=(*current.attempts, event.attempt))
            result = await search(brief, adapter=self.adapter, cancelled=lambda: self._cancelled(job.job_id),
                                  deadline=deadline-min(1, self.seconds*.1), on_progress=progress)
            self._update(job.job_id, attempts=result.attempts)
            if result.state == 'selected':
                self._update(job.job_id, lifecycle=Lifecycle.PACKAGING,
                             selected_candidate_sha256=result.selected['candidate'].candidate_sha256)
                if self.publisher is None:
                    raise ValueError('Publisher unavailable')
                bundle = await _controlled(self.publisher.publish(job.job_id, brief, result),
                    cancelled=lambda: self._cancelled(job.job_id), deadline=deadline-0.5)
                self._terminal(job.job_id, Lifecycle.COMPLETED, outcome=Outcome.ACCEPTED, bundle=bundle)
            elif result.state == 'rejected':
                if self.publisher:
                    await _controlled(self.publisher.publish_diagnostics(job.job_id,brief,result),
                        cancelled=lambda:self._cancelled(job.job_id),deadline=deadline-.5)
                self._terminal(job.job_id, Lifecycle.COMPLETED, outcome=Outcome.REJECTED, failures=result.reasons)
            else:
                self._terminal(job.job_id, Lifecycle.CANCELLED if result.state == 'cancelled' else Lifecycle.UNAVAILABLE,
                               failures=result.reasons)
        except asyncio.CancelledError:
            self.cancel(job.job_id)
            self._terminal(job.job_id, Lifecycle.CANCELLED, failures=('Execution stopped and owned workers cleaned up',))
        except _Stopped as stop:
            self._terminal(job.job_id, Lifecycle.CANCELLED if stop.state == 'cancelled' else Lifecycle.UNAVAILABLE, failures=(stop.reason,))
        except Exception:
            self._terminal(job.job_id, Lifecycle.UNAVAILABLE, failures=('Required execution or publication unavailable',))
        finally:
            try:
                terminal=self.read(job.job_id)
                if self.publisher and terminal.lifecycle != Lifecycle.COMPLETED:
                    self.publisher.discard(job.job_id)
            finally:
                process_owner.reset(token)
                native_slot.release(owner)

    async def wait(self, job_id):
        task = self.tasks.get(job_id)
        if task:
            await asyncio.shield(task)

    async def close(self):
        self.closed = True
        # Durable cancellation also handles tasks cancelled before first entry.
        for job_id, task in self.tasks.items():
            if not task.done():
                self.cancel(job_id)
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
