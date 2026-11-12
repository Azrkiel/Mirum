"""SQLite ownership and transactions for the integrated preparation contracts.

One API process on one local Linux volume. Never place this database on NFS.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from threading import RLock

from .briefs import MemoryBriefRepository, PreparedDraft, RevisionConflict, _check_revision
from .contracts import EngineeringBrief
from .proposer import ProposalAudit


class SQLiteStore:
    def __init__(self, root):
        if os.name != 'posix':
            raise RuntimeError('Engineering persistence requires Linux and a single API process')
        for name in ('WEB_CONCURRENCY', 'UVICORN_WORKERS', 'MIRUM_API_REPLICAS'):
            if os.environ.get(name, '1') != '1':
                raise RuntimeError('Engineering supports a single API process/replica')
        import fcntl
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_uid != os.geteuid():
            raise RuntimeError('Engineering volume must be application-owned')
        self._owner = (self.root / 'owner.lock').open('a+b')
        try:
            fcntl.flock(self._owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._owner.close()
            raise RuntimeError('Engineering volume already has an active owner') from None
        self._lock = RLock()
        self.db = sqlite3.connect(self.root / 'engineering.sqlite3', check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if self.db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
            self.db.close()
            self._owner.close()
            raise RuntimeError('Unsupported engineering database schema version')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS briefs(id TEXT PRIMARY KEY, draft TEXT NOT NULL,
                revision INTEGER NOT NULL, record TEXT NOT NULL, UNIQUE(draft,revision));
            CREATE TABLE IF NOT EXISTS proposals(draft TEXT, revision INTEGER, owner TEXT NOT NULL,
                PRIMARY KEY(draft,revision));
            CREATE TABLE IF NOT EXISTS audits(draft TEXT, revision INTEGER, record TEXT NOT NULL,
                PRIMARY KEY(draft,revision));
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, brief TEXT NOT NULL,
                key TEXT NOT NULL UNIQUE, record TEXT NOT NULL, cancel INTEGER NOT NULL DEFAULT 0,
                deadline REAL NOT NULL, expires REAL, reserved INTEGER NOT NULL,
                FOREIGN KEY(brief) REFERENCES briefs(id));
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            PRAGMA user_version=1;
        ''')

    @contextmanager
    def transaction(self):
        with self._lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def recover(self):
        """Call after acquiring exclusive volume ownership, before admitting work."""
        with self.transaction() as db:
            db.execute('DELETE FROM proposals')
            for row in db.execute('SELECT * FROM audits').fetchall():
                audit = ProposalAudit.model_validate_json(row['record'])
                if audit.status == 'running':
                    audit = ProposalAudit.model_validate(dict(audit.model_dump(), status='unavailable'))
                    db.execute('UPDATE audits SET record=? WHERE draft=? AND revision=?',
                               (audit.model_dump_json(), audit.draft_id, audit.draft_revision))

    def close(self):
        self.db.close()
        self._owner.close()


class SQLiteBriefRepository:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _read(db, draft_id):
        row = db.execute('SELECT record FROM drafts WHERE id=?', (draft_id,)).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return PreparedDraft.model_validate_json(row[0])

    def create(self, prepared):
        prepared = PreparedDraft.model_validate(prepared.model_dump())
        with self.store.transaction() as db:
            if prepared.draft.revision != 1:
                raise RevisionConflict('Draft is not an initial revision')
            try:
                db.execute('INSERT INTO drafts VALUES (?,?)', (prepared.draft.draft_id, prepared.model_dump_json()))
            except sqlite3.IntegrityError:
                raise RevisionConflict('Draft already exists') from None

    def read(self, draft_id):
        with self.store.transaction() as db:
            return self._read(db, draft_id)

    def replace(self, prepared, *, expected_revision):
        _check_revision(expected_revision)
        prepared = PreparedDraft.model_validate(prepared.model_dump())
        with self.store.transaction() as db:
            if (self._read(db, prepared.draft.draft_id).draft.revision != expected_revision
                    or prepared.draft.revision != expected_revision + 1):
                raise RevisionConflict('Draft revision changed; read and review again')
            db.execute('UPDATE drafts SET record=? WHERE id=?', (prepared.model_dump_json(), prepared.draft.draft_id))

    def finalize_if_current(self, brief):
        brief = EngineeringBrief.model_validate(brief.model_dump())
        with self.store.transaction() as db:
            # Reuse the integrated contract's exact comparison/idempotency logic
            # under the SAME SQLite write transaction as proposal ownership.
            memory = MemoryBriefRepository()
            current = self._read(db, brief.draft_id)
            memory._drafts[brief.draft_id] = current.model_dump_json()
            key = brief.draft_id, brief.draft_revision
            row = db.execute('SELECT owner FROM proposals WHERE draft=? AND revision=?', key).fetchone()
            if row:
                memory._proposals[key] = row[0]
            row = db.execute('SELECT id,record FROM briefs WHERE draft=? AND revision=?', key).fetchone()
            if row:
                memory._briefs[row[0]] = row[1]
                memory._finalized[key] = row[0]
            result = memory.finalize_if_current(brief)
            if not row:
                try:
                    db.execute('INSERT INTO briefs VALUES (?,?,?,?)',
                        (brief.brief_id, *key, brief.model_dump_json()))
                except sqlite3.IntegrityError:
                    raise RevisionConflict('Brief ID already exists') from None
            return result

    def read_brief(self, brief_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT record FROM briefs WHERE id=?', (brief_id,)).fetchone()
            if row is None:
                raise KeyError(brief_id)
            return EngineeringBrief.model_validate_json(row[0])

    def is_finalized(self, draft_id, revision):
        with self.store.transaction() as db:
            return db.execute('SELECT 1 FROM briefs WHERE draft=? AND revision=?', (draft_id, revision)).fetchone() is not None

    def begin_proposal(self, draft_id, revision, owner):
        _check_revision(revision)
        with self.store.transaction() as db:
            if (self._read(db, draft_id).draft.revision != revision or
                    db.execute('SELECT 1 FROM briefs WHERE draft=? AND revision=?', (draft_id, revision)).fetchone()):
                raise RevisionConflict('Draft changed or finalized')
            try:
                db.execute('INSERT INTO proposals VALUES (?,?,?)', (draft_id, revision, owner))
            except sqlite3.IntegrityError:
                raise RevisionConflict('Draft already has active AI preparation') from None

    def end_proposal(self, draft_id, revision, owner):
        with self.store.transaction() as db:
            if db.execute('DELETE FROM proposals WHERE draft=? AND revision=? AND owner=?',
                          (draft_id, revision, owner)).rowcount != 1:
                raise RevisionConflict('AI preparation reservation belongs to another owner')


class SQLiteProposalLedger:
    def __init__(self, store):
        self.store = store

    def claim(self, audit):
        audit = ProposalAudit.model_validate(audit.model_dump())
        with self.store.transaction() as db:
            try:
                db.execute('INSERT INTO audits VALUES (?,?,?)', (audit.draft_id, audit.draft_revision, audit.model_dump_json()))
            except sqlite3.IntegrityError:
                raise RevisionConflict('AI preparation already claimed for this draft revision') from None

    def save(self, audit):
        audit = ProposalAudit.model_validate(audit.model_dump())
        with self.store.transaction() as db:
            row = db.execute('SELECT record FROM audits WHERE draft=? AND revision=?', (audit.draft_id, audit.draft_revision)).fetchone()
            if row is None:
                raise KeyError(audit.draft_id)
            current = ProposalAudit.model_validate_json(row[0])
            if (audit.calls < current.calls or audit.settings != current.settings
                    or audit.request_sha256 != current.request_sha256):
                raise RevisionConflict('Proposal audit cannot reset its budget or provider')
            db.execute('UPDATE audits SET record=? WHERE draft=? AND revision=?',
                       (audit.model_dump_json(), audit.draft_id, audit.draft_revision))

    def read(self, draft_id, revision):
        with self.store.transaction() as db:
            row = db.execute('SELECT record FROM audits WHERE draft=? AND revision=?', (draft_id, revision)).fetchone()
            if row is None:
                raise KeyError(draft_id)
            return ProposalAudit.model_validate_json(row[0])
