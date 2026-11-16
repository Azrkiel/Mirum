"""SQLite ownership and transactions for the integrated preparation contracts.

One API process on one local Linux volume. Never place this database on NFS.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time
from threading import RLock

from .admission import AdmissionBusy
from .briefs import MemoryBriefRepository, PreparedDraft, RevisionConflict, _check_revision
from .contracts import EngineeringBrief
from .proposer import ProposalAudit


class ArtifactExpired(ValueError):
    """Known expired record/artifact; transports map this to HTTP 410."""


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
        self.db.execute('PRAGMA secure_delete=ON')
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
            CREATE TABLE IF NOT EXISTS draft_owners(draft TEXT PRIMARY KEY, client TEXT NOT NULL,
                bytes INTEGER NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS retention(kind TEXT, id TEXT, created REAL NOT NULL, PRIMARY KEY(kind,id));
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

    DATABASE_FILES = ('engineering.sqlite3', 'engineering.sqlite3-wal', 'engineering.sqlite3-shm', 'owner.lock')

    def storage_bytes(self):
        """Artifact bytes only. The SQLite files hold drafts/audits/briefs, which have
        their own caps (PreparationQuota) and must not starve job admission."""
        return sum(path.stat().st_size for path in self.root.rglob('*')
                   if path.is_file() and not path.is_symlink()
                   and not (path.parent == self.root and path.name in self.DATABASE_FILES))

    def metadata_bytes(self):
        return sum((self.root / name).stat().st_size for name in self.DATABASE_FILES if (self.root / name).is_file())


TOMBSTONE = '{"expired":true}'


class PreparationQuota:
    """Caps and short expiry for drafts, audits and finalized briefs.

    Accounted apart from job artifact storage. Counts are SQL-derived, so they
    survive restart. Callers run it synchronously (no await between admit and
    record), so the single process cannot interleave two admissions.
    """
    def __init__(self, store, *, ttl_seconds, max_drafts, max_draft_bytes, per_client_drafts,
                 max_audits, max_audit_bytes, max_briefs, per_client_briefs):
        self.store = store
        self.ttl, self.max_drafts, self.max_draft_bytes = ttl_seconds, max_drafts, max_draft_bytes
        self.per_client_drafts, self.max_audits, self.max_audit_bytes = per_client_drafts, max_audits, max_audit_bytes
        self.max_briefs, self.per_client_briefs = max_briefs, per_client_briefs

    @classmethod
    def from_config(cls, store):
        import config
        return cls(store, ttl_seconds=config.ENGINEERING_DRAFT_TTL_SECONDS, max_drafts=config.ENGINEERING_MAX_DRAFTS,
                   max_draft_bytes=config.ENGINEERING_MAX_DRAFT_BYTES, per_client_drafts=config.ENGINEERING_MAX_DRAFTS_PER_CLIENT,
                   max_audits=config.ENGINEERING_MAX_AUDITS, max_audit_bytes=config.ENGINEERING_MAX_AUDIT_BYTES,
                   max_briefs=config.ENGINEERING_MAX_BRIEFS, per_client_briefs=config.ENGINEERING_MAX_BRIEFS_PER_CLIENT)

    UNFINALIZED = 'FROM draft_owners o WHERE NOT EXISTS (SELECT 1 FROM briefs b WHERE b.draft=o.draft)'

    def admit_draft(self, client, *, ai=False, size=0):
        self.expire()
        with self.store.transaction() as db:
            count, used = db.execute('SELECT COUNT(*),COALESCE(SUM(o.bytes),0) ' + self.UNFINALIZED).fetchone()
            mine = db.execute('SELECT COUNT(*) ' + self.UNFINALIZED + ' AND o.client=?', (client,)).fetchone()[0]
            if count >= self.max_drafts or used + size > self.max_draft_bytes or mine >= self.per_client_drafts:
                raise AdmissionBusy('Unfinalized draft capacity reached; retry after drafts expire')
            if ai:
                audits, audit_bytes = db.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(record)),0) FROM audits').fetchone()
                if audits >= self.max_audits or audit_bytes >= self.max_audit_bytes:
                    raise AdmissionBusy('AI audit capacity reached; retry after records expire')

    def record_draft(self, draft_id, client, size):
        with self.store.transaction() as db:
            db.execute('INSERT OR REPLACE INTO draft_owners VALUES (?,?,?,?)', (draft_id, client, int(size), time.time()))

    def touch(self, draft_id, size):
        with self.store.transaction() as db:
            db.execute('UPDATE draft_owners SET bytes=?,updated=? WHERE draft=?', (int(size), time.time(), draft_id))

    def admit_brief(self, draft_id):
        """Bound live finalized briefs globally and per owning client before finalizing."""
        with self.store.transaction() as db:
            total = db.execute('SELECT COUNT(*) FROM briefs WHERE record!=?', (TOMBSTONE,)).fetchone()[0]
            row = db.execute('SELECT client FROM draft_owners WHERE draft=?', (draft_id,)).fetchone()
            mine = 0
            if row:
                mine = db.execute('SELECT COUNT(*) FROM briefs b JOIN draft_owners o ON o.draft=b.draft '
                                  'WHERE o.client=? AND b.record!=?', (row[0], TOMBSTONE)).fetchone()[0]
            if total >= self.max_briefs or mine >= self.per_client_briefs:
                raise AdmissionBusy('Finalized brief capacity reached; retry after records expire')

    def expire(self, now=None):
        """Tombstone unfinalized drafts older than the short TTL and release their quota."""
        now = time.time() if now is None else now
        with self.store.transaction() as db:
            stale = db.execute('SELECT o.draft ' + self.UNFINALIZED + ' AND o.updated<=? '
                               'AND NOT EXISTS (SELECT 1 FROM proposals p WHERE p.draft=o.draft)', (now - self.ttl,)).fetchall()
            for (draft_id,) in stale:
                db.execute('UPDATE drafts SET record=? WHERE id=? AND record!=?', (TOMBSTONE, draft_id, TOMBSTONE))
            db.execute('DELETE FROM draft_owners WHERE draft NOT IN (SELECT id FROM drafts WHERE record!=?)', (TOMBSTONE,))


class SQLiteBriefRepository:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _read(db, draft_id):
        row = db.execute('SELECT record FROM drafts WHERE id=?', (draft_id,)).fetchone()
        if row is None:
            raise KeyError(draft_id)
        if row[0] == '{"expired":true}':
            raise ArtifactExpired('Engineering draft expired')
        return PreparedDraft.model_validate_json(row[0])

    def create(self, prepared):
        prepared = PreparedDraft.model_validate(prepared.model_dump())
        with self.store.transaction() as db:
            if prepared.draft.revision != 1:
                raise RevisionConflict('Draft is not an initial revision')
            try:
                db.execute('INSERT INTO drafts VALUES (?,?)', (prepared.draft.draft_id, prepared.model_dump_json()))
                db.execute('INSERT INTO retention VALUES (?,?,?)',('draft',prepared.draft.draft_id,time.time()))
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
            db.execute('INSERT OR REPLACE INTO retention VALUES (?,?,?)',('draft',prepared.draft.draft_id,time.time()))

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
                    db.execute('INSERT INTO retention VALUES (?,?,?)',('brief',brief.brief_id,time.time()))
                except sqlite3.IntegrityError:
                    raise RevisionConflict('Brief ID already exists') from None
            return result

    def read_brief(self, brief_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT record FROM briefs WHERE id=?', (brief_id,)).fetchone()
            if row is None:
                raise KeyError(brief_id)
            if row[0] == '{"expired":true}':
                raise ArtifactExpired('Engineering brief expired')
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
                db.execute('INSERT INTO retention VALUES (?,?,?)',('audit',audit.draft_id+':'+str(audit.draft_revision),time.time()))
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
