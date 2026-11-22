"""F06 RevisionService: stage, run, settle, cancel and snapshot views of transactional program revisions.

Execution reuses B09/B10 unchanged (D2): ``run`` authorizes a run (fresh probe, lock and semantic
checks), admits a job through the existing JobController, links the job to the revision and starts it.
Evidence comes from the existing search, ``policy.assess`` and BundlePublisher; every run is a fresh
job. Promotion happens only in ``settle`` (one store transaction, D6) through ``RevisionStore.promote``,
which moves the program head and publishes the revision together. Reads are one-transaction snapshots
(D7) and never settle. No transaction spans an await (D14). There is no route or runtime wiring here
(D15): the history API is these Python methods.

Importing this module loads no native kernel, web framework or client library.
"""
from __future__ import annotations

import asyncio
import time
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone

from engineering.jobs import TERMINAL, parse_run
from engineering.store import ArtifactExpired
from engineering.language.semantic import compile_source
from engineering.language.diagnostics import MelError

from .adapters import adapter_for
from .planning import (
    PlanRefusal, ProgramReview, authorize_run, lock_program, open_review, plan_program,
)
from .revision_store import RevisionStore
from .revisions import (
    ProgramRevision, RevisionRefusal, RevisionView, build_payload,
)

__all__ = ['RevisionService']

_CRASH_POINTS = ('after_admit_before_link', 'after_link_before_start', 'before_settle_commit',
                 'restore_after_validate')


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class RevisionService:
    def __init__(self, store, jobs, publisher, briefs, adapter_factory=adapter_for, *, clock=time.time):
        if getattr(getattr(briefs, 'repository', None), 'store', None) is not store:
            raise ValueError('briefs must use the same store')
        if getattr(jobs, 'store', None) is not store or getattr(jobs, 'publisher', None) is not publisher:
            raise ValueError('jobs must use the same store and publisher')
        self.store = store
        self.jobs = jobs
        self.publisher = publisher
        self.briefs = briefs
        self.adapter_factory = adapter_factory
        self.clock = clock
        self.revision_store = RevisionStore(store, clock=clock)
        self._tasks: dict[str, asyncio.Task] = {}
        self._admitting: Counter = Counter()
        self._admit_lock = threading.Lock()
        self.last_settle_error: BaseException | None = None
        self.recover()

    # ------------------------------------------------------------------ seams

    def _crash_point(self, name: str) -> None:
        """Crash-injection seam. A no-op unless a test overrides it (names in _CRASH_POINTS)."""
        return None

    def recover(self) -> None:
        """Startup reconciliation (task 5, D12). Not implemented yet."""
        return None

    # ------------------------------------------------------------------ helpers

    def _now(self) -> datetime:
        return _utc(self.clock())

    def _evolve(self, rev: ProgramRevision, **changes) -> ProgramRevision:
        return ProgramRevision.model_validate(dict(rev.model_dump(), updated_at=max(self._now(), rev.updated_at), **changes))

    def _owned_program(self, db, program_id, client):
        row = self.revision_store.program(db, program_id) if type(program_id) is str else None
        if row is None or row['client'] != client:
            raise KeyError('Unknown program')
        return row

    def _load(self, db, revision_id, client):
        """(revision, cancel flag, payload or None); KeyError for an unknown or foreign revision."""
        got = self.revision_store.get(db, revision_id) if type(revision_id) is str else None
        if got is None:
            raise KeyError('Unknown revision')
        program = self.revision_store.program(db, got[0].program_id)
        if program is None or program['client'] != client:
            raise KeyError('Unknown revision')
        return got

    def _gate(self, db, revision_id, client):
        """Common pre-admit check inside the caller's transaction.

        Returns (result, loaded). ``result`` is not None when run must stop and return it: the record
        itself (not staged, or cancel requested) or a stale_parent refusal after a staged -> conflict CAS."""
        loaded = self._load(db, revision_id, client)
        rev, cancel, _ = loaded
        if rev.state != 'staged' or cancel:
            return rev, loaded
        head = self.revision_store.program(db, rev.program_id)['head']
        if head != rev.parent:
            moved = self._evolve(rev, state='conflict', reason='stale_parent')
            if self.revision_store.transition(db, moved, expected_state='staged', event='conflict',
                                              detail={'reason': 'stale_parent'}):
                return RevisionRefusal(('stale_parent',)), loaded
            return self.revision_store.get(db, revision_id)[0], loaded
        return None, loaded

    # ------------------------------------------------------------------ programs and staging

    def create_program(self, client: str) -> str:
        with self.store.transaction() as db:
            return self.revision_store.create_program(db, client)

    async def stage(self, program_id, source, *, source_id, expected_parent, client):
        """Compile, plan, lock and open a review, then stage one revision.

        A stale parent or capacity refusal is decided in a first transaction, before any compile, probe
        or draft, so it writes nothing at all. MelError propagates unchanged and PlanRefusal is returned;
        neither writes anything. The final insert repeats the parent CAS: a race lost there returns the
        refusal and leaves the freshly opened draft orphaned, which expires under the existing draft
        retention (nothing else references it)."""
        if not isinstance(source_id, str) or not source_id:
            raise ValueError('source_id is required')
        rs = self.revision_store
        with self.store.transaction() as db:
            rs.compact(db, now=self.clock())
            program = self._owned_program(db, program_id, client)
            if program['head'] != expected_parent:
                return RevisionRefusal(('stale_parent',))
            refusal = rs._capacity(db, program_id, staged=True, payload_bytes=0)
            if refusal is not None:
                return refusal
        compiled = compile_source(source, source_id=source_id)
        text = source if isinstance(source, str) else bytes(source).decode('utf-8')
        backend = compiled.ir.execution.backend
        adapter = self.adapter_factory(backend, mode='native')
        plan = plan_program(compiled, mode='native', adapter_factory=lambda _backend, mode='native': adapter)
        if isinstance(plan, PlanRefusal):
            return plan
        lock = await lock_program(plan, adapter=adapter)
        if isinstance(lock, PlanRefusal):
            return lock
        payload = build_payload(source_id, text, compiled.ir, lock)
        review = open_review(plan, lock, self.briefs)
        revision_id = uuid.uuid4().hex
        now = self._now()
        revision = ProgramRevision(
            revision_id=revision_id, program_id=program_id, parent=expected_parent, kind='edit',
            evidence_revision_id=revision_id, state='staged', source_sha256=compiled.source_sha256,
            semantic_sha256=compiled.semantic_sha256, brief_sha256=review.brief_sha256,
            fixed_requirements_sha256=compiled.brief_content.fixed_requirements_sha256,
            lock_sha256=review.lock_sha256, lock=lock, draft_id=review.draft_id,
            draft_revision=review.draft_revision, created_at=now, updated_at=now)
        with self.store.transaction() as db:
            rs.compact(db, now=self.clock())
            self._owned_program(db, program_id, client)
            refusal = rs.insert_staged(db, revision, payload, expected_parent=expected_parent)
        return revision if refusal is None else refusal

    # ------------------------------------------------------------------ run

    async def run(self, revision_id, brief_id, *, client):
        rs = self.revision_store
        with self.store.transaction() as db:
            rs.compact(db, now=self.clock())
            stop, (rev, _cancel, payload) = self._gate(db, revision_id, client)
        if stop is not None:
            return stop
        if payload is None:
            return RevisionRefusal(('history_expired',))
        try:
            self.briefs.repository.read(rev.draft_id)
        except (ArtifactExpired, KeyError):
            return RevisionRefusal(('review_expired',))
        try:
            self.briefs.read_brief(brief_id)
        except ArtifactExpired:
            return RevisionRefusal(('review_expired',))
        except KeyError:
            pass  # an unknown brief id is reported by authorize_run as not_finalized
        try:
            compiled = compile_source(payload.source, source_id=payload.source_id)
        except MelError:
            return RevisionRefusal(('record_altered',))
        if compiled.source_sha256 != rev.source_sha256 or compiled.semantic_sha256 != rev.semantic_sha256:
            return RevisionRefusal(('record_altered',))
        try:
            review = ProgramReview(
                draft_id=rev.draft_id, draft_revision=rev.draft_revision, brief_sha256=rev.brief_sha256,
                semantic_sha256=rev.semantic_sha256, lock=rev.lock, lock_sha256=rev.lock_sha256,
                source_sha256=rev.source_sha256)
        except ValueError:
            return RevisionRefusal(('record_altered',))
        adapter = self.adapter_factory(compiled.ir.execution.backend, mode='native')
        auth = await authorize_run(review, compiled, self.briefs, brief_id, adapter=adapter)
        if isinstance(auth, PlanRefusal):
            return auth

        # The probe above awaited: re-check state, cancel flag and parent, then journal the intent.
        with self.store.transaction() as db:
            stop, _ = self._gate(db, revision_id, client)
            if stop is not None:
                return stop
            rs.append_journal(db, rev.program_id, revision_id, 'admit_intent', {'brief_id': brief_id})
            with self._admit_lock:
                self._admitting[revision_id] += 1
        try:
            return await self._admit_link_start(rev, brief_id, client)
        finally:
            with self._admit_lock:
                self._admitting[revision_id] -= 1
                if self._admitting[revision_id] <= 0:
                    del self._admitting[revision_id]

    async def _admit_link_start(self, rev, brief_id, client):
        rs = self.revision_store
        revision_id = rev.revision_id
        # AdmissionBusy and the like propagate: the revision stays staged.
        admitted = await asyncio.to_thread(self.jobs.admit, brief_id, 'program-revision:' + revision_id, client)
        holds_slot = admitted.owner is not None
        started = False
        try:
            self._crash_point('after_admit_before_link')
            job_id = admitted.job.job_id
            flagged = False
            linked = None
            with self.store.transaction() as db:
                current, cancel, _ = self._load(db, revision_id, client)
                flagged = cancel
                if current.state == 'staged':
                    new = self._evolve(current, state='running', job_id=job_id, brief_id=brief_id)
                    if rs.transition(db, new, expected_state='staged', event='linked', detail={'job_id': job_id}):
                        linked = new
                if linked is None:
                    current = rs.get(db, revision_id)[0]
            if linked is None:
                # Another caller linked (same idempotent job) or the revision left staged meanwhile.
                if holds_slot:
                    started = True
                    if current.job_id == job_id and current.state == 'running':
                        self.jobs.start(admitted)
                        self._own_settle(revision_id, job_id)
                    else:
                        self._release_slot(admitted)
                return current
            if flagged:
                # D13: link anyway, then cancel and start so the native slot is released.
                self.jobs.cancel(job_id)
            self._crash_point('after_link_before_start')
            started = True
            self.jobs.start(admitted)
            self._own_settle(revision_id, job_id)
            return linked
        except BaseException:
            if holds_slot and not started:
                self._release_slot(admitted)
            raise

    def _release_slot(self, admitted) -> None:
        """Cancel an admitted job and start it so its task ends and the native slot is released."""
        try:
            self.jobs.cancel(admitted.job.job_id)
        except KeyError:
            pass
        try:
            self.jobs.start(admitted)
        except Exception:
            pass  # start() already released the slot on failure

    def _own_settle(self, revision_id, job_id) -> None:
        existing = self._tasks.get(revision_id)
        if existing is not None and not existing.done():
            return
        self._tasks[revision_id] = asyncio.create_task(self._settle_when_done(revision_id, job_id))

    async def _settle_when_done(self, revision_id, job_id) -> None:
        try:
            await self.jobs.wait(job_id)
            self.settle(revision_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the revision stays running; recovery (task 5) or a later settle finishes it
            self.last_settle_error = exc

    async def wait(self, revision_id) -> None:
        """Await the owned settle task of a revision (tests, shutdown)."""
        task = self._tasks.get(revision_id)
        if task is not None:
            await asyncio.shield(task)

    # ------------------------------------------------------------------ settle

    def settle(self, revision_id) -> ProgramRevision:
        """D6: finish a running revision from its terminal job, in ONE store transaction."""
        rs = self.revision_store
        with self.store.transaction() as db:
            got = rs.get(db, revision_id) if type(revision_id) is str else None
            if got is None:
                raise KeyError('Unknown revision')
            rev, cancel, _ = got
            if rev.state != 'running':
                return rev
            row = db.execute('SELECT record FROM jobs WHERE id=?', (rev.job_id,)).fetchone()
            run = None
            if row is not None:
                try:
                    run = parse_run(row['record'])
                except ValueError:
                    run = False
                if run and run.lifecycle not in TERMINAL:
                    return rev
            result = self._decide(db, rev, cancel, row is None, run)
            self._crash_point('before_settle_commit')
            return result

    def _decide(self, db, rev, cancel, job_missing, run) -> ProgramRevision:
        rs = self.revision_store

        def finish(state, event, reason):
            new = self._evolve(rev, state=state, reason=reason)
            if not rs.transition(db, new, expected_state='running', event=event, detail={'reason': reason}):
                raise RuntimeError('Revision left running inside its own settle transaction')
            return new

        if cancel or (run and run.lifecycle.value == 'cancelled'):
            return finish('cancelled', 'cancelled', 'cancelled')
        if job_missing:
            return finish('failed', 'failed', 'job_missing')
        if run is False:
            return finish('failed', 'failed', 'job_record_invalid')
        if run.lifecycle.value == 'completed' and run.outcome.value == 'accepted':
            problem = self._acceptance_problem(rev, run)
            if problem is not None:
                return finish('failed', 'failed', problem)
            promoted = rs.promote(db, rev, expected_head=rev.parent, bundle=run.bundle,
                                  now=max(self._now(), rev.updated_at),
                                  detail={'job_id': rev.job_id})
            if promoted is None:
                return finish('conflict', 'conflict', 'stale_parent')
            return promoted
        if run.lifecycle.value == 'completed' and run.outcome.value == 'rejected':
            return finish('rejected', 'rejected', 'rejected_by_policy')
        return finish('failed', 'failed', 'job_unavailable')

    def _acceptance_problem(self, rev, run) -> str | None:
        bundle = getattr(run, 'bundle', None)
        if bundle is None:
            return 'no_bundle'
        if (run.brief_sha256 != rev.brief_sha256
                or run.fixed_requirements_sha256 != rev.fixed_requirements_sha256):
            return 'brief_mismatch'
        try:
            self.publisher.quick_check(rev.job_id, bundle)
            expires = self.publisher.expires_at(rev.job_id)
        except (KeyError, ValueError, OSError):
            return 'publication_invalid'
        if not expires > self.clock():
            return 'artifacts_expired'
        return None

    # ------------------------------------------------------------------ cancel

    def _cancel_sync(self, revision_id, *, client) -> ProgramRevision:
        rs = self.revision_store
        job_id = None
        with self.store.transaction() as db:
            rev, _cancel, _ = self._load(db, revision_id, client)
            if rev.state not in ('staged', 'running'):
                return rev
            rs.set_cancel(db, revision_id)
            rs.append_journal(db, rev.program_id, revision_id, 'cancel_requested', {})
            with self._admit_lock:
                in_flight = self._admitting.get(revision_id, 0) > 0
            if rev.state == 'staged' and not in_flight:
                new = self._evolve(rev, state='cancelled', reason='cancelled')
                rs.transition(db, new, expected_state='staged', event='cancelled', detail={'reason': 'cancelled'})
                return new
            job_id = rev.job_id
        if job_id is not None:
            try:
                self.jobs.cancel(job_id)
            except KeyError:
                pass
            task = self._tasks.get(revision_id)
            if task is None or task.done():
                return self.settle(revision_id)
        with self.store.transaction() as db:
            return self._load(db, revision_id, client)[0]

    async def cancel(self, revision_id, *, client) -> ProgramRevision:
        """D13: durable cancel flag first; the owned settle task finishes a linked run."""
        return self._cancel_sync(revision_id, client=client)

    # ------------------------------------------------------------------ snapshot views

    def _artifacts(self, db, rev: ProgramRevision) -> str:
        if rev.state != 'published':
            return 'none'
        row = db.execute('SELECT j.expires AS jexp, p.expires AS pexp FROM jobs j '
                         'LEFT JOIN publications p ON p.job=j.id WHERE j.id=?', (rev.job_id,)).fetchone()
        if row is None or row['pexp'] is None:
            return 'missing'
        now = self.clock()
        if row['pexp'] <= now or (row['jexp'] is not None and row['jexp'] <= now):
            return 'expired'
        try:
            self.publisher.quick_check(rev.job_id, rev.bundle)
        except (KeyError, ValueError, OSError):
            return 'missing'
        return 'available'

    def _view(self, db, program_row, rev) -> RevisionView:
        return RevisionView(revision=rev, artifacts=self._artifacts(db, rev),
                            is_current=program_row['head'] == rev.revision_id)

    def current(self, program_id, *, client):
        with self.store.transaction() as db:
            program = self._owned_program(db, program_id, client)
            if program['head'] is None:
                return None
            got = self.revision_store.get(db, program['head'])
            if got is None or got[0].program_id != program_id or got[0].state != 'published':
                raise RuntimeError('Program head does not point to a published revision')
            return self._view(db, program, got[0])

    def view(self, revision_id, *, client):
        with self.store.transaction() as db:
            rev = self._load(db, revision_id, client)[0]
            return self._view(db, self.revision_store.program(db, rev.program_id), rev)

    def history(self, program_id, *, client):
        with self.store.transaction() as db:
            self._owned_program(db, program_id, client)
            return self.revision_store.revisions(db, program_id)

    def journal(self, program_id, *, client):
        with self.store.transaction() as db:
            self._owned_program(db, program_id, client)
            return self.revision_store.journal(db, program_id)

    def open_artifact(self, revision_id, fmt, *, client):
        """Open one served file of THIS revision's own bundle under a lease (D7), never 'whatever is
        current'. Blocking (streams the digest of the file): callers run it off the event loop
        (asyncio.to_thread). The caller closes the returned Download."""
        with self.store.transaction() as db:
            rev = self._load(db, revision_id, client)[0]
        if rev.state != 'published' or rev.job_id is None or rev.bundle is None:
            raise KeyError('Revision has no published artifacts')
        return self.publisher.open_lease(rev.job_id, fmt, envelope=rev.bundle)

    # ------------------------------------------------------------------ shutdown

    async def close(self) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
