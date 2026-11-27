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
from engineering.store import TOMBSTONE, ArtifactExpired
from engineering.language.semantic import compile_source
from engineering.language.diagnostics import MelError

from . import delivered
from .adapters import adapter_for
from .delivered import DeliveredVerifier, EvidenceRowInvalid
from .evidence import evidence_sha256
from .planning import (
    adapter_for_program,
    PlanRefusal, ProgramReview, authorize_run, lock_program, open_review, plan_program,
)
from .hashing import execution_lock_v0_2_sha256, semantic_sha256
from .ir import load_execution_lock_json, load_program_json
from .revision_store import RevisionStore, published_revision
from .revisions import (
    ProgramRevision, RevisionDiff, RevisionRefusal, RevisionView, build_payload, diff, evidence_from_run,
)

__all__ = ['RevisionService']

_DEFAULT = object()  # RevisionService(verifier=...) sentinel: build the real DeliveredVerifier
_CRASH_POINTS = ('after_admit_before_link', 'after_link_before_start', 'before_settle_commit',
                 'restore_after_validate')


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class RevisionService:
    def __init__(self, store, jobs, publisher, briefs, adapter_factory=adapter_for, *, clock=time.time,
                 verifier=_DEFAULT, publication_archive=None):
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
        # F07 D9: a monotone gate. Default is the real verifier (fail closed); None disables it (F06 behaviour).
        self.verifier = DeliveredVerifier(store, publisher, adapter_factory) if verifier is _DEFAULT else verifier
        if publication_archive is not None and self.verifier is None:
            raise ValueError('publication archival requires delivered verification')
        self.publication_archive = publication_archive
        self._archive_lock = threading.Lock()
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
        """Startup reconciliation (D12). One pass, bounded by the row count; runs in the constructor, after
        the JobController (and so the publisher) already recovered: every job is terminal by now.

        1. Fail closed: every program head must be a published revision of that program (RuntimeError,
           nothing modified, otherwise).
        2. A staged revision whose journal holds an admit_intent was interrupted between intent and link
           (a job may exist that no revision references): staged -> failed, reason 'interrupted', journaled
           as ONE 'recovered' entry. A staged revision without an intent stays staged.
        3. A running revision settles through the normal D6 transaction after a FULL publisher.validate of
           its bundle (outside the transaction): CAS roll-forward to published, conflict when the head
           moved, else cancelled/rejected/failed. A 'recovered' entry follows the terminal entry.

        Never starts native work, never admits, never extends any expiry."""
        rs = self.revision_store
        with self.store.transaction() as db:
            for program in db.execute('SELECT id, head FROM programs WHERE head IS NOT NULL ORDER BY id').fetchall():
                got = rs.get(db, program['head'])
                if got is None or got[0].program_id != program['id'] or got[0].state != 'published':
                    raise RuntimeError('Program head does not point to a published revision of that program')
            pending = rs.staged_or_running(db)
            intents = {row[0] for row in db.execute(
                "SELECT DISTINCT revision_id FROM program_journal WHERE event='admit_intent'").fetchall()}
        for rev in pending:
            if rev.state == 'staged':
                if rev.revision_id in intents:
                    with self.store.transaction() as db:
                        failed = self._evolve(rev, state='failed', reason='interrupted')
                        rs.transition(db, failed, expected_state='staged', event='recovered',
                                      detail={'reason': 'interrupted'})
                continue
            problem = None
            with self.store.transaction() as db:
                row = db.execute('SELECT record FROM jobs WHERE id=?', (rev.job_id,)).fetchone()
            run = None
            if row is not None:
                try:
                    run = parse_run(row['record'])
                except ValueError:
                    run = None
            if (run is not None and run.lifecycle.value == 'completed' and run.outcome.value == 'accepted'
                    and getattr(run, 'bundle', None) is not None):
                try:
                    self.publisher.validate(rev.job_id, run.bundle)  # full digest check, outside any transaction
                except Exception:
                    problem = 'artifacts_invalid'
            self.settle(rev.revision_id, recovered=True, validation_problem=problem)

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

    def expire_staged(self, now, *, ttl_seconds=None) -> int:
        """F08 D10 (K47): fail every staged revision whose review can no longer be finalized or run.

        A staged row moves to failed (reason 'review_expired', journal event 'failed') in one
        transaction when its draft is missing or tombstoned, or when its age is at least the draft
        retention (``ttl_seconds``; the configured draft TTL by default). Rows being admitted by a
        run() in flight (``_admitting``) are skipped. Never probes, admits or touches a job. Returns the
        number of rows failed."""
        if ttl_seconds is None:
            import config
            ttl_seconds = config.ENGINEERING_DRAFT_TTL_SECONDS
        rs = self.revision_store
        failed_count = 0
        with self.store.transaction() as db:
            rows = db.execute("SELECT id, created FROM program_revisions WHERE state='staged' "
                              'ORDER BY created, id').fetchall()
            for row in rows:
                revision_id = row['id']
                with self._admit_lock:
                    in_flight = self._admitting.get(revision_id, 0) > 0
                if in_flight:
                    continue
                try:
                    got = rs.get(db, revision_id)
                except RuntimeError:
                    continue  # an altered row is reported by the reads that touch it, not here
                if got is None or got[0].state != 'staged':
                    continue
                rev = got[0]
                if row['created'] + ttl_seconds > now:
                    draft = db.execute('SELECT record FROM drafts WHERE id=?', (rev.draft_id,)).fetchone()
                    if draft is not None and draft[0] != TOMBSTONE:
                        continue
                failed = self._evolve(rev, state='failed', reason='review_expired')
                if rs.transition(db, failed, expected_state='staged', event='failed',
                                 detail={'reason': 'review_expired'}):
                    failed_count += 1
        return failed_count

    async def stage(self, program_id, source, *, source_id, expected_parent, client):
        """Compile, plan, lock and open a review, then stage one revision.

        A stale parent or capacity refusal is decided in a first transaction, before any compile, probe
        or draft, so it writes nothing at all. MelError propagates unchanged and PlanRefusal is returned;
        neither writes anything. The final insert repeats the parent CAS: a race lost there returns the
        refusal and leaves the freshly opened draft orphaned, which expires under the existing draft
        retention (nothing else references it)."""
        return await self._stage(program_id, source, source_id=source_id, expected_parent=expected_parent,
                                 client=client, kind='edit', derived_from=None)

    async def _stage(self, program_id, source, *, source_id, expected_parent, client, kind, derived_from):
        """Shared staging path of stage() (kind 'edit') and rebuild() (kind 'rebuild')."""
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
            parent = self._load(db, expected_parent, client)[0] if expected_parent else None
        compiled = await asyncio.to_thread(compile_source, source, source_id=source_id)
        if kind == 'edit' and parent is not None and 'editable_plate' in (parent.lock.capability_id,
                                                                          compiled.ir.capability.id):
            if parent.fixed_requirements_sha256 != compiled.brief_content.fixed_requirements_sha256:
                return RevisionRefusal(('requirements_changed',))
        if kind == 'edit' and parent is not None:
            before, after = parent.lock.backend_lock.backend, compiled.ir.execution.backend
            if before != after and 'mirum-native' in (before, after):
                # An edit never switches a program into or out of the native kernel.
                return RevisionRefusal(('backend_changed',))
        text = source if isinstance(source, str) else bytes(source).decode('utf-8')
        adapter = adapter_for_program(self.adapter_factory, compiled)
        plan = plan_program(compiled, mode='native', adapter_factory=lambda _backend, **kwargs: adapter)
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
            revision_id=revision_id, program_id=program_id, parent=expected_parent, kind=kind,
            derived_from=derived_from, evidence_revision_id=revision_id, state='staged', source_sha256=compiled.source_sha256,
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
            compiled = await asyncio.to_thread(compile_source, payload.source, source_id=payload.source_id)
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
        adapter = adapter_for_program(self.adapter_factory, compiled)
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
        # Only mirum-native carries a backend into the job controller (it routes by capability and backend);
        # every other backend keeps the unchanged call.
        extra = {'backend': 'mirum-native'} if rev.lock.backend_lock.backend == 'mirum-native' else {}
        admitted = await asyncio.to_thread(self.jobs.admit, brief_id, 'program-revision:' + revision_id, client,
                                           **extra)
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
            if self.verifier is not None:
                try:
                    await self._verify_if_accepted(revision_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # settle still runs and fails closed (delivered_unverified)
                    self.last_settle_error = exc
            if self.publication_archive is None:
                self.settle(revision_id)
            else:
                work = asyncio.create_task(asyncio.to_thread(self.settle, revision_id))
                try:
                    await asyncio.shield(work)
                finally:
                    # Shutdown must let the owned disk operation leave the store before closing it.
                    if not work.done():
                        await work
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the revision stays running; recovery (task 5) or a later settle finishes it
            self.last_settle_error = exc

    async def _verify_if_accepted(self, revision_id) -> None:
        """F07 D7: measure the delivered bytes of a committed accepted job, outside any transaction.

        A rejected, cancelled, unavailable, missing or unreadable job, a cancel request, a compacted or
        unreadable payload and a revision that is no longer running are never verified; settle then decides
        (a missing row fails closed through match_problem)."""
        with self.store.transaction() as db:
            got = self.revision_store.get(db, revision_id)
            if got is None or got[0].state != 'running' or got[1] or got[2] is None:
                return
            rev, _cancel, payload = got
            row = db.execute('SELECT record FROM jobs WHERE id=?', (rev.job_id,)).fetchone()
        if row is None:
            return
        try:
            run = parse_run(row['record'])
            ir = load_program_json(payload.ir_json)
        except ValueError:
            return
        if (run.lifecycle.value != 'completed' or run.outcome.value != 'accepted'
                or getattr(run, 'bundle', None) is None):
            return
        await self.verifier.verify(rev, ir)

    async def wait(self, revision_id) -> None:
        """Await the owned settle task of a revision (tests, shutdown)."""
        task = self._tasks.get(revision_id)
        if task is not None:
            await asyncio.shield(task)

    # ------------------------------------------------------------------ settle

    def settle(self, revision_id, *, recovered=False, validation_problem=None) -> ProgramRevision:
        """Finish a terminal job with a single transactional head move.

        When archival is configured, save a prospective publication outside the write
        transaction. Recheck the exact record and verification evidence at promotion.
        Both normal execution and startup recovery use this path.
        """
        if self.publication_archive is None:
            return self._settle(revision_id, recovered, validation_problem)
        with self._archive_lock:
            archived = self._prepare_archive(revision_id, validation_problem)
            result = self._settle(revision_id, recovered, validation_problem, archived)
            if result.state not in ('staged', 'running', 'published'):
                try:
                    self.publication_archive.discard(result)
                except (OSError, ValueError) as exc:
                    self.last_settle_error = exc
            return result

    def _settle_inputs(self, db, revision_id):
        got = self.revision_store.get(db, revision_id) if type(revision_id) is str else None
        if got is None:
            raise KeyError('Unknown revision')
        rev, cancel, payload = got
        run = None
        if rev.state == 'running':
            row = db.execute('SELECT record FROM jobs WHERE id=?', (rev.job_id,)).fetchone()
            if row is not None:
                try:
                    run = parse_run(row['record'])
                except ValueError:
                    run = False
        return rev, cancel, payload, run

    def _prepare_archive(self, revision_id, validation_problem):
        with self.store.transaction() as db:
            rev, cancel, payload, run = self._settle_inputs(db, revision_id)
            if (rev.state != 'running' or cancel or payload is None or not run
                    or run.lifecycle.value != 'completed' or run.outcome.value != 'accepted'):
                return None
            program = self.revision_store.program(db, rev.program_id)
            if program['head'] != rev.parent:
                return None
            problem, evidence = self._verified_publication(db, rev, run, validation_problem)
            if problem is not None:
                return None
            preview = published_revision(rev, program['head_seq'] + 1, run.bundle, max(self._now(), rev.updated_at))
        try:
            self.publication_archive.save(preview, payload, evidence, self.publisher)
        except (OSError, KeyError, ValueError) as exc:
            self.last_settle_error = exc
            return None
        return preview, evidence_sha256(evidence)

    def _settle(self, revision_id, recovered, validation_problem, archived=None):
        with self.store.transaction() as db:
            rev, cancel, _payload, run = self._settle_inputs(db, revision_id)
            if rev.state != 'running' or (run and run.lifecycle not in TERMINAL):
                return rev
            result = self._decide(db, rev, cancel, run, validation_problem, archived)
            if recovered:
                self.revision_store.append_journal(db, result.program_id, result.revision_id,
                                                   'recovered', {'state': result.state})
            self._crash_point('before_settle_commit')
            return result

    def _decide(self, db, rev, cancel, run, validation_problem, archived) -> ProgramRevision:
        rs = self.revision_store

        def finish(state, event, reason):
            new = self._evolve(rev, state=state, reason=reason)
            if not rs.transition(db, new, expected_state='running', event=event, detail={'reason': reason}):
                raise RuntimeError('Revision left running inside its own settle transaction')
            return new

        if cancel or (run and run.lifecycle.value == 'cancelled'):
            return finish('cancelled', 'cancelled', 'cancelled')
        if run is None:
            return finish('failed', 'failed', 'job_missing')
        if run is False:
            return finish('failed', 'failed', 'job_record_invalid')
        if run.lifecycle.value == 'completed' and run.outcome.value == 'accepted':
            problem, evidence = self._verified_publication(db, rev, run, validation_problem)
            if problem is not None:
                return finish('failed', 'failed', problem)
            detail = {'job_id': rev.job_id}
            if evidence is not None:
                detail['evidence_hash'] = evidence_sha256(evidence)
            now = max(self._now(), rev.updated_at)
            if self.publication_archive is not None:
                program = rs.program(db, rev.program_id)
                if program['head'] != rev.parent:
                    return finish('conflict', 'conflict', 'stale_parent')
                if archived is None:
                    return finish('failed', 'failed', 'archive_unavailable')
                preview, evidence_hash = archived
                now = preview.updated_at
                if (preview != published_revision(rev, program['head_seq'] + 1, run.bundle, now)
                        or evidence_hash != detail['evidence_hash']):
                    return finish('failed', 'failed', 'archive_unavailable')
            promoted = rs.promote(db, rev, expected_head=rev.parent, bundle=run.bundle, now=now, detail=detail)
            if promoted is None:
                return finish('conflict', 'conflict', 'stale_parent')
            return promoted
        if run.lifecycle.value == 'completed' and run.outcome.value == 'rejected':
            return finish('rejected', 'rejected', 'rejected_by_policy')
        return finish('failed', 'failed', 'job_unavailable')

    def _verified_publication(self, db, rev, run, validation_problem):
        problem = self._acceptance_problem(rev, run) or validation_problem
        evidence = None
        if problem is None and self.verifier is not None:
            # The verified row must still match the job, bundle and current method version.
            problem = self.verifier.evidence.match_problem(
                db, rev.revision_id, job_id=rev.job_id, bundle=run.bundle,
                method_version=delivered.method_version())
            if problem is None:
                evidence = self.verifier.evidence.get(db, rev.revision_id)
        return problem, evidence

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

    def evidence(self, revision_id, *, client):
        """The re-validated delivered-geometry record of a revision (resolved through its
        evidence_revision_id, so a restore shows its target's), or None when there is none or the gate is off.

        Client-checked exactly like view() (KeyError for an unknown or foreign revision). A stored row that
        fails validation raises RuntimeError, like the other snapshot reads of an altered record."""
        with self.store.transaction() as db:
            rev = self._load(db, revision_id, client)[0]
            if self.verifier is None:
                return None
            try:
                return self.verifier.evidence.get(db, rev.evidence_revision_id)
            except EvidenceRowInvalid as exc:
                raise RuntimeError('Program revision evidence is altered') from exc

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
        if rev.state == 'rejected' and rev.job_id is not None:
            formats = ('generator-report', 'report', 'attempts') if rev.lock.capability_id in (
                'equipment_bay', 'editable_plate', 'thermal_bar') else (
                'report', 'attempts')
            if fmt in formats:
                return self.publisher.open_lease(rev.job_id, fmt)
        if rev.state != 'published' or rev.job_id is None or rev.bundle is None:
            raise KeyError('Revision has no published artifacts')
        return self.publisher.open_lease(rev.job_id, fmt, envelope=rev.bundle)

    def source(self, revision_id, *, client):
        """F08 D11: the stored RevisionPayload of this revision's evidence revision (source text, IR and
        lock JSON), client-checked exactly like view() (KeyError for an unknown or foreign revision).

        None when the evidence revision is missing; RevisionRefusal(('history_expired',)) when its payload
        was compacted. Reads only (retention compaction aside, as in restore and rebuild)."""
        with self.store.transaction() as db:
            self.revision_store.compact(db, now=self.clock())
            rev = self._load(db, revision_id, client)[0]
            evidence = self._evidence_row(db, rev)
        if evidence is None:
            return None
        if evidence[2] is None:
            return RevisionRefusal(('history_expired',))
        return evidence[2]

    # ------------------------------------------------------------------ restore, rebuild, compare

    def _evidence_row(self, db, rev):
        """(evidence revision, cancel, payload) or None. The evidence revision holds the IR and source
        (restore rows have no payload of their own)."""
        got = self.revision_store.get(db, rev.evidence_revision_id)
        if got is None or got[0].program_id != rev.program_id:
            return None
        return got

    def _publication_state(self, db, rev):
        """None when the publication is present and unexpired, else the refusal reason."""
        row = db.execute('SELECT expires FROM publications WHERE job=?', (rev.job_id,)).fetchone()
        if row is None:
            return 'artifacts_missing'
        if row['expires'] <= self.clock():
            return 'artifacts_expired'
        return None

    def _validate_pinned(self, job_id, bundle) -> None:
        """Full validation while a lease on the manifest keeps cleanup and discard away (blocking)."""
        with self.publisher.lease(job_id, 'manifest', envelope=bundle):
            self.publisher.validate(job_id, bundle)

    @staticmethod
    def _artifact_reason(exc: BaseException) -> str:
        if isinstance(exc, ArtifactExpired):
            return 'artifacts_expired'
        if isinstance(exc, (KeyError, OSError)):
            return 'artifacts_missing'
        if isinstance(exc, ValueError) and str(exc) == 'Incomplete package':
            return 'artifacts_missing'
        return 'artifacts_altered'

    def _restore_evidence_problem(self, db, target):
        """'record_altered' unless the target's evidence row is a valid verified record for the target's own
        job and bundle. The row's method_version is deliberately NOT compared: an older row is still the
        evidence the target was published with, and a verifier change must not make it unrestorable.
        None when the gate is off. Reads only; restore never measures or writes evidence."""
        if self.verifier is None:
            return None
        try:
            record = self.verifier.evidence.get(db, target.evidence_revision_id)
        except EvidenceRowInvalid:
            return 'record_altered'
        if (record is None or record.verdict != 'verified' or target.bundle is None
                or record.job_id != target.job_id or record.manifest_sha256 != target.bundle.manifest_sha256):
            return 'record_altered'
        return None

    @staticmethod
    def _restore_record_problem(target, payload):
        """'record_altered' when the stored payload or lock no longer hashes to the stored hashes."""
        try:
            ir = load_program_json(payload.ir_json)
            payload_lock = load_execution_lock_json(payload.lock_json)
            if (semantic_sha256(ir) != target.semantic_sha256
                    or execution_lock_v0_2_sha256(payload_lock) != target.lock_sha256
                    or execution_lock_v0_2_sha256(target.lock) != target.lock_sha256):
                return 'record_altered'
        except (ValueError, TypeError):
            return 'record_altered'
        return None

    async def restore(self, revision_id, *, expected_head, client):
        """D9: roll back by adding a NEW published revision that points at the target's own evidence.

        Never probes, assesses, admits, or touches publications.expires / jobs.expires. Refusals are
        returned (nothing written); an unknown or foreign revision raises KeyError."""
        rs = self.revision_store
        with self.store.transaction() as db:
            rs.compact(db, now=self.clock())
            target = self._load(db, revision_id, client)[0]
            program = rs.program(db, target.program_id)
            if target.state != 'published':
                return RevisionRefusal(('not_restorable',))
            evidence = self._evidence_row(db, target)
            if evidence is None:
                return RevisionRefusal(('not_restorable',))
            if program['head'] == target.revision_id:
                return RevisionRefusal(('already_current',))
            payload = evidence[2]
            if payload is None:
                return RevisionRefusal(('history_expired',))
            if program['head'] != expected_head:
                return RevisionRefusal(('stale_parent',))
            problem = self._restore_record_problem(target, payload)
            if problem is not None:
                return RevisionRefusal((problem,))
            row = db.execute('SELECT record FROM jobs WHERE id=?', (target.job_id,)).fetchone()
            if row is None:
                return RevisionRefusal(('artifacts_missing',))
            try:
                run = parse_run(row['record'])
            except ValueError:
                return RevisionRefusal(('record_altered',))
            if (run.lifecycle.value != 'completed' or run.outcome.value != 'accepted'
                    or getattr(run, 'bundle', None) is None or run.bundle != target.bundle):
                return RevisionRefusal(('record_altered',))
            if self._restore_evidence_problem(db, target) is not None:
                return RevisionRefusal(('record_altered',))
            state = self._publication_state(db, target)
            if state is not None:
                return RevisionRefusal((state,))
        try:
            await asyncio.to_thread(self._validate_pinned, target.job_id, target.bundle)
        except Exception as exc:
            return RevisionRefusal((self._artifact_reason(exc),))
        self._crash_point('restore_after_validate')
        with self.store.transaction() as db:
            rs.compact(db, now=self.clock())
            try:
                self.publisher.quick_check(target.job_id, target.bundle)
            except ValueError:
                return RevisionRefusal(('artifacts_altered',))
            except (KeyError, OSError):
                return RevisionRefusal(('artifacts_missing',))
            if self._restore_evidence_problem(db, target) is not None:
                return RevisionRefusal(('record_altered',))
            state = self._publication_state(db, target)
            if state is not None:
                return RevisionRefusal((state,))
            now = self._now()
            restored = ProgramRevision(
                revision_id=uuid.uuid4().hex, program_id=target.program_id, parent=expected_head,
                kind='restore', derived_from=target.revision_id,
                evidence_revision_id=target.evidence_revision_id, state='published',
                source_sha256=target.source_sha256, semantic_sha256=target.semantic_sha256,
                brief_sha256=target.brief_sha256, fixed_requirements_sha256=target.fixed_requirements_sha256,
                lock_sha256=target.lock_sha256, lock=target.lock, draft_id=target.draft_id,
                draft_revision=target.draft_revision, brief_id=target.brief_id, job_id=target.job_id,
                bundle=target.bundle, published_seq=1, created_at=now, updated_at=now)
            return rs.insert_restore(db, restored, expected_head=expected_head,
                                     detail={'derived_from': target.revision_id})

    async def rebuild(self, revision_id, *, expected_parent, client):
        """D10: stage a NEW 'rebuild' revision from the target's stored source.

        Recompiles with the current compiler, plans, takes a fresh-probe lock and opens a new review, through
        the same path as stage(). The caller finalizes the review and runs it as a normal new job; old
        evidence is never copied. Any revision whose evidence payload is present can be rebuilt (not only
        published ones); a compacted payload gives history_expired. MelError propagates like stage()."""
        rs = self.revision_store
        with self.store.transaction() as db:
            rs.compact(db, now=self.clock())
            target = self._load(db, revision_id, client)[0]
            evidence = self._evidence_row(db, target)
        if evidence is None or evidence[2] is None:
            return RevisionRefusal(('history_expired',))
        payload = evidence[2]
        return await self._stage(target.program_id, payload.source, source_id=payload.source_id,
                                 expected_parent=expected_parent, client=client, kind='rebuild',
                                 derived_from=target.revision_id)

    def compare(self, a_id, b_id, *, client) -> RevisionDiff:
        """D8: one snapshot transaction. IR from each side's evidence revision (None when compacted);
        evidence summary from each side's OWN job row (None when no job or unreadable)."""
        with self.store.transaction() as db:
            sides = []
            for revision_id in (a_id, b_id):
                rev = self._load(db, revision_id, client)[0]
                evidence_row = self._evidence_row(db, rev)
                ir = None
                if evidence_row is not None and evidence_row[2] is not None:
                    try:
                        ir = load_program_json(evidence_row[2].ir_json)
                    except ValueError as exc:
                        raise RuntimeError('Program revision payload is altered') from exc
                evidence = None
                if rev.job_id is not None:
                    row = db.execute('SELECT record FROM jobs WHERE id=?', (rev.job_id,)).fetchone()
                    if row is not None:
                        try:
                            evidence = evidence_from_run(parse_run(row['record']))
                        except ValueError:
                            evidence = None
                sides.append((rev, ir, evidence))
        (a, a_ir, a_ev), (b, b_ir, b_ev) = sides
        return diff(a, a_ir, b, b_ir, evidence_a=a_ev, evidence_b=b_ev)

    # ------------------------------------------------------------------ shutdown

    async def close(self) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
