"""F06 SQL schema, CAS transitions, append-only journal, payload compaction and caps (D4, D5, D11, D14).

Every helper takes the caller's ``db`` (from ``store.transaction()``) so a service can do several
steps in ONE transaction; nothing here awaits or opens a second connection. The tables are created
by the constructor (copying the BundlePublisher pattern); PRAGMA user_version is never touched and
no foreign key points into jobs, briefs, drafts or publications (their cleanup deletes rows).
Reads fail closed: a row whose record disagrees with its columns, or fails validation, raises
RuntimeError.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from engineering.admission import AdmissionBusy
from engineering.capabilities import CAPABILITY

from .revisions import (
    JournalEntry, ProgramRevision, RevisionPayload, RevisionRefusal, can_transition,
)

__all__ = ['MAX_PROGRAMS', 'MAX_REVISIONS_PER_PROGRAM', 'MAX_STAGED_PER_PROGRAM', 'MAX_PAYLOAD_BYTES',
           'RevisionStore']

MAX_PROGRAMS = 256
MAX_REVISIONS_PER_PROGRAM = 256
MAX_STAGED_PER_PROGRAM = 4
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024

_REVISION_COLUMNS = ('id', 'program_id', 'parent', 'kind', 'derived_from', 'evidence_revision', 'state',
                     'cancel', 'published_seq', 'job_id', 'brief_id', 'record', 'payload', 'reason',
                     'created', 'updated')

_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS programs(
        id TEXT PRIMARY KEY, client TEXT NOT NULL, head TEXT, head_seq INTEGER NOT NULL DEFAULT 0,
        created REAL NOT NULL, updated REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS program_revisions(
        id TEXT PRIMARY KEY, program_id TEXT NOT NULL REFERENCES programs(id), parent TEXT,
        kind TEXT NOT NULL CHECK(kind IN ('edit','restore','rebuild')), derived_from TEXT,
        evidence_revision TEXT,
        state TEXT NOT NULL CHECK(state IN ('staged','running','published','rejected','failed','cancelled','conflict')),
        cancel INTEGER NOT NULL DEFAULT 0, published_seq INTEGER, job_id TEXT, brief_id TEXT,
        record TEXT NOT NULL, payload TEXT, reason TEXT, created REAL NOT NULL, updated REAL NOT NULL,
        UNIQUE(program_id, published_seq))''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS program_revisions_run_job ON program_revisions(job_id)
        WHERE kind!='restore' AND job_id IS NOT NULL''',
    'CREATE INDEX IF NOT EXISTS program_revisions_by_program ON program_revisions(program_id, created)',
    '''CREATE TABLE IF NOT EXISTS program_journal(
        seq INTEGER PRIMARY KEY AUTOINCREMENT, program_id TEXT NOT NULL, revision_id TEXT,
        event TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL)''',
    '''CREATE TRIGGER IF NOT EXISTS program_journal_no_update BEFORE UPDATE ON program_journal
        BEGIN SELECT RAISE(ABORT,'append-only'); END''',
    '''CREATE TRIGGER IF NOT EXISTS program_journal_no_delete BEFORE DELETE ON program_journal
        BEGIN SELECT RAISE(ABORT,'append-only'); END''',
)


def _columns(rev: ProgramRevision) -> dict:
    """Indexed columns that must always equal the record's fields (one builder for both)."""
    return dict(parent=rev.parent, kind=rev.kind, derived_from=rev.derived_from,
                evidence_revision=rev.evidence_revision_id, state=rev.state,
                published_seq=rev.published_seq, job_id=rev.job_id, brief_id=rev.brief_id,
                reason=rev.reason)


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class RevisionStore:
    def __init__(self, store, *, clock=time.time):
        self.store = store
        self.clock = clock
        with store.transaction() as db:
            for statement in _SCHEMA:
                db.execute(statement)

    # ------------------------------------------------------------------ programs

    def create_program(self, db, client: str) -> str:
        if db.execute('SELECT COUNT(*) FROM programs').fetchone()[0] >= MAX_PROGRAMS:
            raise AdmissionBusy('program capacity reached')
        program_id = uuid.uuid4().hex
        now = self.clock()
        db.execute('INSERT INTO programs(id, client, head, head_seq, created, updated) VALUES (?,?,NULL,0,?,?)',
                   (program_id, client, now, now))
        return program_id

    def program(self, db, program_id: str):
        return db.execute('SELECT * FROM programs WHERE id=?', (program_id,)).fetchone()

    def _program_or_raise(self, db, program_id: str):
        row = self.program(db, program_id)
        if row is None:
            raise KeyError('Unknown program')
        return row

    # ------------------------------------------------------------------ journal

    def append_journal(self, db, program_id: str, revision_id: str | None, event: str,
                       detail: dict | None = None) -> int:
        detail = {} if detail is None else detail
        now = self.clock()
        # Validate (event allowlist, flat ASCII detail rules, id shapes) BEFORE inserting.
        JournalEntry(seq=1, program_id=program_id, revision_id=revision_id, event=event, detail=detail,
                     created_at=_utc(now))
        cur = db.execute('INSERT INTO program_journal(program_id, revision_id, event, detail, created) VALUES (?,?,?,?,?)',
                         (program_id, revision_id, event, json.dumps(detail, sort_keys=True), now))
        return cur.lastrowid

    def journal(self, db, program_id: str) -> tuple[JournalEntry, ...]:
        out = []
        for row in db.execute('SELECT * FROM program_journal WHERE program_id=? ORDER BY seq',
                              (program_id,)).fetchall():
            try:
                out.append(JournalEntry(seq=row['seq'], program_id=row['program_id'],
                                        revision_id=row['revision_id'], event=row['event'],
                                        detail=json.loads(row['detail']), created_at=_utc(row['created'])))
            except (ValidationError, ValueError, TypeError) as exc:
                raise RuntimeError('Program journal row is altered') from exc
        return tuple(out)

    # ------------------------------------------------------------------ caps

    def _capacity(self, db, program_id: str, *, staged: bool, payload_bytes: int) -> RevisionRefusal | None:
        if db.execute('SELECT COUNT(*) FROM program_revisions WHERE program_id=?',
                      (program_id,)).fetchone()[0] >= MAX_REVISIONS_PER_PROGRAM:
            return RevisionRefusal(('capacity',), 'revisions per program')
        if staged and db.execute("SELECT COUNT(*) FROM program_revisions WHERE program_id=? AND state='staged'",
                                 (program_id,)).fetchone()[0] >= MAX_STAGED_PER_PROGRAM:
            return RevisionRefusal(('capacity',), 'staged revisions per program')
        if payload_bytes:
            used = db.execute('SELECT COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) FROM program_revisions').fetchone()[0]
            if used + payload_bytes > MAX_PAYLOAD_BYTES:
                return RevisionRefusal(('capacity',), 'payload bytes')
        return None

    def _insert(self, db, rev: ProgramRevision, payload_text: str | None, now: float) -> None:
        cols = _columns(rev)
        db.execute(
            'INSERT INTO program_revisions(id, program_id, parent, kind, derived_from, evidence_revision, state, '
            'cancel, published_seq, job_id, brief_id, record, payload, reason, created, updated) '
            'VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?)',
            (rev.revision_id, rev.program_id, cols['parent'], cols['kind'], cols['derived_from'],
             cols['evidence_revision'], cols['state'], cols['published_seq'], cols['job_id'],
             cols['brief_id'], rev.model_dump_json(), payload_text, cols['reason'], now, now))

    # ------------------------------------------------------------------ staging

    def insert_staged(self, db, revision: ProgramRevision, payload: RevisionPayload, *,
                      expected_parent: str | None, detail: dict | None = None) -> RevisionRefusal | None:
        if revision.state != 'staged' or revision.kind == 'restore':
            raise ValueError('insert_staged takes a staged edit or rebuild revision')
        program = self._program_or_raise(db, revision.program_id)
        if program['head'] != expected_parent or revision.parent != expected_parent:
            return RevisionRefusal(('stale_parent',))
        payload_text = payload.model_dump_json()
        refusal = self._capacity(db, revision.program_id, staged=True,
                                 payload_bytes=len(payload_text.encode('utf-8')))
        if refusal is not None:
            return refusal
        self._insert(db, revision, payload_text, self.clock())
        self.append_journal(db, revision.program_id, revision.revision_id, 'staged',
                           {} if detail is None else dict(detail))
        return None

    # ------------------------------------------------------------------ transitions

    def transition(self, db, revision: ProgramRevision, *, expected_state: str, event: str,
                   detail: dict | None = None) -> bool:
        """CAS the row to ``revision`` (the new record). False and nothing written when the row is not in
        ``expected_state``. Publication goes only through promote(), which also moves the head."""
        if revision.state == 'published':
            raise ValueError('publish through promote(), which moves the head in the same transaction')
        return self._cas(db, revision, expected_state=expected_state, event=event, detail=detail)

    def _cas(self, db, revision: ProgramRevision, *, expected_state: str, event: str,
             detail: dict | None = None) -> bool:
        if not can_transition(expected_state, revision.state):
            raise ValueError(f'transition {expected_state} -> {revision.state} is not allowed')
        detail = {} if detail is None else detail
        # Validate the journal entry first so an invalid event never leaves a half-applied row.
        JournalEntry(seq=1, program_id=revision.program_id, revision_id=revision.revision_id,
                     event=event, detail=detail, created_at=_utc(self.clock()))
        cols = _columns(revision)
        cur = db.execute(
            'UPDATE program_revisions SET state=?, record=?, job_id=?, brief_id=?, published_seq=?, reason=?, '
            'updated=? WHERE id=? AND state=?',
            (cols['state'], revision.model_dump_json(), cols['job_id'], cols['brief_id'],
             cols['published_seq'], cols['reason'], self.clock(), revision.revision_id, expected_state))
        if cur.rowcount != 1:
            return False
        self.append_journal(db, revision.program_id, revision.revision_id, event, detail)
        return True

    def promote(self, db, revision: ProgramRevision, *, expected_head: str | None, bundle, now: datetime,
                detail: dict | None = None) -> ProgramRevision | None:
        """Atomically move the head to this running revision and publish it. None, nothing written, when the
        row is not running or the head is not ``expected_head``."""
        if revision.state != 'running':
            raise ValueError('promote takes the running record')
        row = db.execute('SELECT state FROM program_revisions WHERE id=?', (revision.revision_id,)).fetchone()
        if row is None or row['state'] != 'running':
            return None
        cur = db.execute('UPDATE programs SET head=?, head_seq=head_seq+1, updated=? WHERE id=? AND head IS ?',
                         (revision.revision_id, self.clock(), revision.program_id, expected_head))
        if cur.rowcount != 1:
            return None
        seq = db.execute('SELECT head_seq FROM programs WHERE id=?', (revision.program_id,)).fetchone()[0]
        published = ProgramRevision.model_validate(dict(
            revision.model_dump(), state='published', published_seq=seq, bundle=bundle, updated_at=now))
        if not self._cas(db, published, expected_state='running', event='published', detail=detail):
            raise RuntimeError('Revision left running inside its own promotion transaction')
        return published

    def insert_restore(self, db, revision: ProgramRevision, *, expected_head: str | None,
                       detail: dict | None = None) -> ProgramRevision | RevisionRefusal:
        """Insert a restore row directly as published under the same head CAS. ``revision.published_seq`` is
        a placeholder; the assigned head_seq replaces it."""
        if revision.kind != 'restore' or revision.state != 'published':
            raise ValueError('insert_restore takes a published restore revision')
        self._program_or_raise(db, revision.program_id)
        refusal = self._capacity(db, revision.program_id, staged=False, payload_bytes=0)
        stale = db.execute('SELECT head FROM programs WHERE id=?', (revision.program_id,)).fetchone()[0] != expected_head
        if stale:
            return RevisionRefusal(('stale_parent',))
        if refusal is not None:
            return refusal
        cur = db.execute('UPDATE programs SET head=?, head_seq=head_seq+1, updated=? WHERE id=? AND head IS ?',
                         (revision.revision_id, self.clock(), revision.program_id, expected_head))
        if cur.rowcount != 1:
            return RevisionRefusal(('stale_parent',))
        seq = db.execute('SELECT head_seq FROM programs WHERE id=?', (revision.program_id,)).fetchone()[0]
        restored = ProgramRevision.model_validate(dict(revision.model_dump(), published_seq=seq))
        self._insert(db, restored, None, self.clock())
        self.append_journal(db, restored.program_id, restored.revision_id, 'restored', detail)
        return restored

    def set_cancel(self, db, revision_id: str) -> bool:
        """Durable cancel flag; only a staged or running revision can be flagged."""
        cur = db.execute("UPDATE program_revisions SET cancel=1, updated=? WHERE id=? AND state IN ('staged','running')",
                         (self.clock(), revision_id))
        return cur.rowcount == 1

    # ------------------------------------------------------------------ reads

    def _load(self, row) -> ProgramRevision:
        try:
            rev = ProgramRevision.model_validate_json(row['record'])
        except (ValidationError, ValueError, TypeError) as exc:
            raise RuntimeError('Program revision record is altered') from exc
        if rev.revision_id != row['id'] or rev.program_id != row['program_id']:
            raise RuntimeError('Program revision record is altered')
        for name, value in _columns(rev).items():
            column = 'evidence_revision' if name == 'evidence_revision' else name
            if row[column] != value:
                raise RuntimeError('Program revision record is altered')
        return rev

    def get(self, db, revision_id: str):
        """(revision, cancel, payload or None), or None when unknown."""
        row = db.execute('SELECT * FROM program_revisions WHERE id=?', (revision_id,)).fetchone()
        if row is None:
            return None
        rev = self._load(row)
        payload = None
        if row['payload'] is not None:
            try:
                payload = RevisionPayload.model_validate_json(row['payload'])
            except (ValidationError, ValueError, TypeError) as exc:
                raise RuntimeError('Program revision payload is altered') from exc
        return rev, bool(row['cancel']), payload

    def revisions(self, db, program_id: str) -> tuple[ProgramRevision, ...]:
        rows = db.execute('SELECT * FROM program_revisions WHERE program_id=? ORDER BY created, id',
                          (program_id,)).fetchall()
        return tuple(self._load(r) for r in rows)

    def staged_or_running(self, db) -> tuple[ProgramRevision, ...]:
        rows = db.execute("SELECT * FROM program_revisions WHERE state IN ('staged','running') "
                          'ORDER BY created, id').fetchall()
        return tuple(self._load(r) for r in rows)

    # ------------------------------------------------------------------ compaction

    def compact(self, db, *, now: float) -> int:
        """NULL the payload (hashes and record stay) of every revision at or past the retention age,
        head included, and journal one 'compacted' per revision. Returns how many were compacted."""
        cutoff = now - CAPABILITY.resources.state_retention_seconds
        rows = db.execute('SELECT id, program_id FROM program_revisions WHERE payload IS NOT NULL AND created <= ? '
                          'ORDER BY created, id', (cutoff,)).fetchall()
        for row in rows:
            db.execute('UPDATE program_revisions SET payload=NULL WHERE id=?', (row['id'],))
            self.append_journal(db, row['program_id'], row['id'], 'compacted', {})
        return len(rows)
