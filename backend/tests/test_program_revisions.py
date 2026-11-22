"""F06 task 3: revision_store schema, CAS transitions, append-only journal, compaction and caps."""
from __future__ import annotations

import itertools
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

import pytest
from pydantic import ValidationError

from engineering.admission import AdmissionBusy
from engineering.capabilities import CAPABILITY
from engineering.program import revision_store as rst
from engineering.program.revision_store import RevisionStore
from engineering.program.revisions import (
    REVISION_STATES, ProgramRevision, RevisionPayload, RevisionRefusal, can_transition,
)
# Fixtures and builders are reused from the task-1 record tests.
from tests.test_program_revision_records import H, NOW, bundle, compiled, lock, make_rev  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
RETENTION = CAPABILITY.resources.state_retention_seconds


class HostStore:
    """Windows-host stand-in for SQLiteStore: same connection settings and transaction() semantics."""

    def __init__(self):
        self._lock = RLock()
        self.db = sqlite3.connect(':memory:', check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE briefs(id TEXT PRIMARY KEY, record TEXT);
            CREATE TABLE jobs(id TEXT PRIMARY KEY, brief TEXT, record TEXT,
                FOREIGN KEY(brief) REFERENCES briefs(id));
            CREATE TABLE publications(job TEXT PRIMARY KEY, record TEXT, expires REAL);
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


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def host():
    return HostStore()


@pytest.fixture
def rs(host, clock):
    return RevisionStore(host, clock=clock)


def hexid(n):
    return f'{n:032x}'


def payload(size=1):
    return RevisionPayload(source_id='s', source='x' * size, ir_json='{}', lock_json='{}')


def staged(lock, bundle, pid, n, parent=None):
    rid = hexid(n)
    return make_rev(lock, bundle, revision_id=rid, program_id=pid, parent=parent, evidence_revision_id=rid,
                    state='staged', brief_id=None, job_id=None, bundle=None, published_seq=None)


def evolve(rev, **changes):
    return ProgramRevision.model_validate(dict(rev.model_dump(), **changes))


def in_state(rev, state, bundle):
    if state == 'staged':
        return evolve(rev, state='staged', job_id=None, brief_id=None, bundle=None, published_seq=None)
    extra = dict(job_id=hexid(0xAAA), brief_id=hexid(0xBBB))
    if state == 'published':
        extra.update(bundle=bundle, published_seq=1)
    else:
        extra.update(bundle=None, published_seq=None)
    return evolve(rev, state=state, **extra)


def new_program(rs, host, client='c'):
    with host.transaction() as db:
        return rs.create_program(db, client)


def stage(rs, host, lock, bundle, pid, n, parent=None, size=1):
    with host.transaction() as db:
        return rs.insert_staged(db, staged(lock, bundle, pid, n, parent), payload(size), expected_parent=parent)


def raw_insert(host, rs, rev, *, payload_text=None):
    with host.transaction() as db:
        rs._insert(db, rev, payload_text, 1000.0)


def journal_count(host):
    return host.db.execute('SELECT COUNT(*) FROM program_journal').fetchone()[0]


def test_real_cap_constants():
    assert rst.MAX_PROGRAMS == 256
    assert rst.MAX_REVISIONS_PER_PROGRAM == 256
    assert rst.MAX_STAGED_PER_PROGRAM == 4
    assert rst.MAX_PAYLOAD_BYTES == 16 * 1024 * 1024


# ---- schema ----

def test_schema_idempotent_and_user_version_untouched(host, clock):
    RevisionStore(host, clock=clock)
    before = host.db.execute('SELECT type, name, sql FROM sqlite_master ORDER BY name').fetchall()
    uv = host.db.execute('PRAGMA user_version').fetchone()[0]
    RevisionStore(host, clock=clock)
    after = host.db.execute('SELECT type, name, sql FROM sqlite_master ORDER BY name').fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert host.db.execute('PRAGMA user_version').fetchone()[0] == uv == 0
    names = {r['name'] for r in after}
    assert {'programs', 'program_revisions', 'program_journal', 'program_revisions_run_job',
            'program_revisions_by_program'} <= names
    assert len([r for r in after if r['type'] == 'trigger']) == 2


def test_journal_is_append_only(rs, host, lock, bundle):
    pid = new_program(rs, host)
    with host.transaction() as db:
        rs.append_journal(db, pid, None, 'staged', {})
    with pytest.raises(sqlite3.DatabaseError, match='append-only'):
        host.db.execute('UPDATE program_journal SET event=?', ('failed',))
    with pytest.raises(sqlite3.DatabaseError, match='append-only'):
        host.db.execute('DELETE FROM program_journal')
    assert journal_count(host) == 1


def _row(pid, rid, state='published', kind='edit', seq=None, job=None):
    return ('INSERT INTO program_revisions(id, program_id, kind, state, published_seq, job_id, record, created, '
            'updated) VALUES (?,?,?,?,?,?,?,1,1)', (rid, pid, kind, state, seq, job, '{}'))


def test_unique_published_seq(rs, host):
    pid = new_program(rs, host)
    host.db.execute(*_row(pid, hexid(1), seq=1))
    with pytest.raises(sqlite3.IntegrityError):
        host.db.execute(*_row(pid, hexid(2), seq=1))
    host.db.execute(*_row(pid, hexid(3), state='failed'))
    host.db.execute(*_row(pid, hexid(4), state='failed'))


def test_run_job_partial_unique_index(rs, host):
    pid = new_program(rs, host)
    host.db.execute(*_row(pid, hexid(1), state='failed', job='j' * 32))
    with pytest.raises(sqlite3.IntegrityError):
        host.db.execute(*_row(pid, hexid(2), state='failed', job='j' * 32))
    with pytest.raises(sqlite3.IntegrityError):
        host.db.execute(*_row(pid, hexid(3), state='failed', kind='rebuild', job='j' * 32))
    host.db.execute(*_row(pid, hexid(4), kind='restore', seq=1, job='j' * 32))
    host.db.execute(*_row(pid, hexid(5), kind='restore', seq=2, job='j' * 32))


def test_check_constraints(rs, host):
    pid = new_program(rs, host)
    with pytest.raises(sqlite3.IntegrityError):
        host.db.execute(*_row(pid, hexid(1), state='bogus'))
    with pytest.raises(sqlite3.IntegrityError):
        host.db.execute(*_row(pid, hexid(1), kind='bogus'))
    with pytest.raises(sqlite3.IntegrityError):
        host.db.execute(*_row('f' * 32, hexid(1)))  # FK to programs


def test_no_foreign_key_into_existing_tables(rs, host):
    fk = host.db.execute("PRAGMA foreign_key_list('program_revisions')").fetchall()
    assert [r['table'] for r in fk] == ['programs']
    assert host.db.execute("PRAGMA foreign_key_list('program_journal')").fetchall() == []
    assert host.db.execute("PRAGMA foreign_key_list('programs')").fetchall() == []


def test_deleting_jobs_row_with_revisions_present(rs, host, lock, bundle):
    host.db.execute("INSERT INTO briefs VALUES ('b', '{}')")
    host.db.execute("INSERT INTO jobs VALUES ('j', 'b', '{}')")
    pid = new_program(rs, host)
    rev = in_state(staged(lock, bundle, pid, 1), 'running', bundle)
    raw_insert(host, rs, rev)
    host.db.execute("DELETE FROM jobs WHERE id='j'")
    host.db.execute("DELETE FROM briefs WHERE id='b'")
    with host.transaction() as db:
        assert rs.get(db, rev.revision_id)[0] == rev


# ---- transitions ----

@pytest.mark.parametrize('src,dst', list(itertools.product(REVISION_STATES, REVISION_STATES)))
def test_every_transition_pair(rs, host, lock, bundle, src, dst):
    pid = new_program(rs, host)
    base = in_state(staged(lock, bundle, pid, 1), src, bundle)
    raw_insert(host, rs, base)
    new = in_state(base, dst, bundle)
    allowed = can_transition(src, dst) and dst != 'published'  # publishing goes only through promote()
    with host.transaction() as db:
        if allowed:
            assert rs.transition(db, new, expected_state=src, event=dst if dst != 'running' else 'linked',
                                 detail={'why': 'test'}) is True
        else:
            with pytest.raises(ValueError):
                rs.transition(db, new, expected_state=src, event='failed')
    with host.transaction() as db:
        stored = rs.get(db, base.revision_id)[0]
    assert stored == (new if allowed else base)
    assert journal_count(host) == (1 if allowed else 0)


def test_cas_miss_writes_nothing(rs, host, lock, bundle):
    pid = new_program(rs, host)
    base = staged(lock, bundle, pid, 1)
    raw_insert(host, rs, base)
    new = in_state(base, 'failed', bundle)
    with host.transaction() as db:
        assert rs.transition(db, new, expected_state='running', event='failed') is False
    with host.transaction() as db:
        assert rs.get(db, base.revision_id)[0] == base
    assert journal_count(host) == 0


def test_transition_unknown_row_and_bad_event(rs, host, lock, bundle):
    pid = new_program(rs, host)
    base = staged(lock, bundle, pid, 1)
    new = in_state(base, 'failed', bundle)
    with host.transaction() as db:
        assert rs.transition(db, new, expected_state='staged', event='failed') is False
    raw_insert(host, rs, base)
    with pytest.raises(ValidationError):
        with host.transaction() as db:
            rs.transition(db, new, expected_state='staged', event='not_an_event')
    with host.transaction() as db:
        assert rs.get(db, base.revision_id)[0] == base
    assert journal_count(host) == 0


def test_columns_always_equal_record(rs, host, lock, bundle):
    pid = new_program(rs, host)
    assert stage(rs, host, lock, bundle, pid, 1) is None
    base = staged(lock, bundle, pid, 1)
    running = in_state(base, 'running', bundle)
    with host.transaction() as db:
        assert rs.transition(db, evolve(running, reason='go'), expected_state='staged', event='linked')
    row = host.db.execute('SELECT * FROM program_revisions').fetchone()
    assert (row['state'], row['job_id'], row['brief_id'], row['reason'], row['kind'],
            row['evidence_revision'], row['published_seq']) == (
        'running', running.job_id, running.brief_id, 'go', 'edit', base.revision_id, None)
    assert ProgramRevision.model_validate_json(row['record']).reason == 'go'


# ---- staging ----

def test_insert_staged_writes_row_payload_and_journal(rs, host, lock, bundle):
    pid = new_program(rs, host)
    assert stage(rs, host, lock, bundle, pid, 1) is None
    with host.transaction() as db:
        rev, cancel, pl = rs.get(db, hexid(1))
        entries = rs.journal(db, pid)
    assert rev == staged(lock, bundle, pid, 1) and cancel is False and pl == payload()
    assert [(e.event, e.revision_id) for e in entries] == [('staged', hexid(1))]


def test_stale_parent_at_staging_writes_nothing(rs, host, lock, bundle):
    pid = new_program(rs, host)
    with host.transaction() as db:
        r = rs.insert_staged(db, staged(lock, bundle, pid, 1, parent=hexid(9)), payload(), expected_parent=hexid(9))
    assert r == RevisionRefusal(('stale_parent',))
    with host.transaction() as db:
        r = rs.insert_staged(db, staged(lock, bundle, pid, 2), payload(), expected_parent=hexid(9))
    assert r == RevisionRefusal(('stale_parent',))
    assert host.db.execute('SELECT COUNT(*) FROM program_revisions').fetchone()[0] == 0
    assert journal_count(host) == 0


def test_staged_refuses_wrong_state_kind_and_unknown_program(rs, host, lock, bundle):
    pid = new_program(rs, host)
    with host.transaction() as db:
        with pytest.raises(ValueError):
            rs.insert_staged(db, in_state(staged(lock, bundle, pid, 1), 'failed', bundle), payload(),
                             expected_parent=None)
        with pytest.raises(KeyError):
            rs.insert_staged(db, staged(lock, bundle, hexid(77), 1), payload(), expected_parent=None)


# ---- promote ----

def run_one(rs, host, lock, bundle, pid, n, parent=None):
    assert stage(rs, host, lock, bundle, pid, n, parent) is None
    running = in_state(staged(lock, bundle, pid, n, parent), 'running', bundle)
    running = evolve(running, job_id=hexid(0x1000 + n))
    with host.transaction() as db:
        assert rs.transition(db, running, expected_state='staged', event='linked')
    return running


def test_promote_success(rs, host, lock, bundle, clock):
    pid = new_program(rs, host)
    running = run_one(rs, host, lock, bundle, pid, 1)
    with host.transaction() as db:
        pub = rs.promote(db, running, expected_head=None, bundle=bundle, now=NOW)
    assert pub.state == 'published' and pub.published_seq == 1 and pub.bundle == bundle
    with host.transaction() as db:
        prog = rs.program(db, pid)
        assert (prog['head'], prog['head_seq']) == (hexid(1), 1)
        assert rs.get(db, hexid(1))[0] == pub
        assert [e.event for e in rs.journal(db, pid)] == ['staged', 'linked', 'published']
    # Second revision on top of the first gets the next seq.
    running2 = run_one(rs, host, lock, bundle, pid, 2, parent=hexid(1))
    with host.transaction() as db:
        pub2 = rs.promote(db, running2, expected_head=hexid(1), bundle=bundle, now=NOW)
    assert pub2.published_seq == 2


def test_promote_conflict_writes_nothing(rs, host, lock, bundle):
    pid = new_program(rs, host)
    a = run_one(rs, host, lock, bundle, pid, 1)
    b = run_one(rs, host, lock, bundle, pid, 2)
    with host.transaction() as db:
        assert rs.promote(db, a, expected_head=None, bundle=bundle, now=NOW) is not None
    before = journal_count(host)
    with host.transaction() as db:
        assert rs.promote(db, b, expected_head=None, bundle=bundle, now=NOW) is None
    assert journal_count(host) == before
    with host.transaction() as db:
        assert rs.get(db, b.revision_id)[0] == b
        prog = rs.program(db, pid)
    assert (prog['head'], prog['head_seq']) == (hexid(1), 1)


def test_promote_of_non_running_is_noop(rs, host, lock, bundle):
    pid = new_program(rs, host)
    a = run_one(rs, host, lock, bundle, pid, 1)
    with host.transaction() as db:
        assert rs.transition(db, in_state(a, 'cancelled', bundle), expected_state='running', event='cancelled')
    before = journal_count(host)
    with host.transaction() as db:
        assert rs.promote(db, a, expected_head=None, bundle=bundle, now=NOW) is None
        assert rs.program(db, pid)['head'] is None
    assert journal_count(host) == before


# ---- restore ----

def restore_rev(lock, bundle, pid, n, parent, target):
    return make_rev(lock, bundle, revision_id=hexid(n), program_id=pid, parent=parent, kind='restore',
                    derived_from=target, evidence_revision_id=target, state='published', published_seq=1)


def test_insert_restore_success_and_stale(rs, host, lock, bundle):
    pid = new_program(rs, host)
    a = run_one(rs, host, lock, bundle, pid, 1)
    with host.transaction() as db:
        pub = rs.promote(db, a, expected_head=None, bundle=bundle, now=NOW)
    b = run_one(rs, host, lock, bundle, pid, 2, parent=hexid(1))
    with host.transaction() as db:
        rs.promote(db, b, expected_head=hexid(1), bundle=bundle, now=NOW)
    snapshot = host.db.execute('SELECT record FROM program_revisions WHERE id=?', (hexid(1),)).fetchone()[0]
    rr = restore_rev(lock, bundle, pid, 3, hexid(2), hexid(1))
    with host.transaction() as db:
        out = rs.insert_restore(db, rr, expected_head=hexid(2))
    assert isinstance(out, ProgramRevision) and out.published_seq == 3 and out.kind == 'restore'
    with host.transaction() as db:
        assert rs.program(db, pid)['head'] == hexid(3)
        stored, cancel, pl = rs.get(db, hexid(3))
        assert stored == out and pl is None
        assert rs.journal(db, pid)[-1].event == 'restored'
    assert host.db.execute('SELECT record FROM program_revisions WHERE id=?', (hexid(1),)).fetchone()[0] == snapshot
    # Stale head: nothing written.
    before = journal_count(host)
    with host.transaction() as db:
        r = rs.insert_restore(db, restore_rev(lock, bundle, pid, 4, hexid(2), hexid(1)), expected_head=hexid(2))
    assert r == RevisionRefusal(('stale_parent',))
    assert journal_count(host) == before
    assert host.db.execute('SELECT COUNT(*) FROM program_revisions WHERE id=?', (hexid(4),)).fetchone()[0] == 0
    with host.transaction() as db:
        assert rs.program(db, pid)['head_seq'] == 3


def test_restore_rows_may_share_a_job(rs, host, lock, bundle):
    pid = new_program(rs, host)
    a = run_one(rs, host, lock, bundle, pid, 1)
    with host.transaction() as db:
        rs.promote(db, a, expected_head=None, bundle=bundle, now=NOW)
    r1 = restore_rev(lock, bundle, pid, 5, hexid(1), hexid(1))
    r1 = evolve(r1, job_id=a.job_id)
    with host.transaction() as db:
        assert isinstance(rs.insert_restore(db, r1, expected_head=hexid(1)), ProgramRevision)
    r2 = evolve(restore_rev(lock, bundle, pid, 6, hexid(5), hexid(1)), job_id=a.job_id)
    with host.transaction() as db:
        assert isinstance(rs.insert_restore(db, r2, expected_head=hexid(5)), ProgramRevision)


# ---- caps ----

def test_program_cap(rs, host):
    with host.transaction() as db:
        for _ in range(256):
            rs.create_program(db, 'c')
    with pytest.raises(AdmissionBusy):
        with host.transaction() as db:
            rs.create_program(db, 'c')
    assert host.db.execute('SELECT COUNT(*) FROM programs').fetchone()[0] == 256


def test_revisions_per_program_cap(rs, host, lock, bundle):
    pid = new_program(rs, host)
    base = staged(lock, bundle, pid, 1)
    with host.transaction() as db:
        for n in range(1, 257):
            rev = base.model_copy(update=dict(revision_id=hexid(n), evidence_revision_id=hexid(n)))
            assert rs.insert_staged(db, rev, payload(), expected_parent=None) is None
            done = rev.model_copy(update=dict(state='cancelled'))
            assert rs.transition(db, done, expected_state='staged', event='cancelled')
    before = journal_count(host)
    with host.transaction() as db:
        rev = base.model_copy(update=dict(revision_id=hexid(999), evidence_revision_id=hexid(999)))
        r = rs.insert_staged(db, rev, payload(), expected_parent=None)
    assert r == RevisionRefusal(('capacity',), 'revisions per program')
    assert journal_count(host) == before
    assert host.db.execute('SELECT COUNT(*) FROM program_revisions').fetchone()[0] == 256
    # Another program is unaffected.
    other = new_program(rs, host)
    assert stage(rs, host, lock, bundle, other, 5000) is None


def test_staged_per_program_cap(rs, host, lock, bundle):
    pid = new_program(rs, host)
    for n in range(1, 5):
        assert stage(rs, host, lock, bundle, pid, n) is None
    before = journal_count(host)
    r = stage(rs, host, lock, bundle, pid, 5)
    assert r == RevisionRefusal(('capacity',), 'staged revisions per program')
    assert journal_count(host) == before
    # Leaving 'staged' frees a slot.
    with host.transaction() as db:
        assert rs.transition(db, in_state(staged(lock, bundle, pid, 1), 'cancelled', bundle),
                             expected_state='staged', event='cancelled')
    assert stage(rs, host, lock, bundle, pid, 5) is None


def test_payload_byte_cap_at_limit_and_over(rs, host, lock, bundle, monkeypatch):
    pid = new_program(rs, host)
    size = len(payload(500).model_dump_json().encode())
    monkeypatch.setattr(rst, 'MAX_PAYLOAD_BYTES', size * 3)
    for n in (1, 2, 3):  # third lands exactly on the cap
        assert stage(rs, host, lock, bundle, pid, n, size=500) is None
    before = journal_count(host)
    assert stage(rs, host, lock, bundle, pid, 4, size=500) == RevisionRefusal(('capacity',), 'payload bytes')
    assert journal_count(host) == before
    assert host.db.execute('SELECT COUNT(*) FROM program_revisions').fetchone()[0] == 3
    # Compaction frees budget.
    with host.transaction() as db:
        assert rs.compact(db, now=1000.0 + RETENTION) == 3
    assert stage(rs, host, lock, bundle, pid, 4, size=500) is None


# ---- compaction ----

def test_compaction_boundary_head_included_hashes_kept(rs, host, lock, bundle, clock):
    pid = new_program(rs, host)
    running = run_one(rs, host, lock, bundle, pid, 1)
    with host.transaction() as db:
        pub = rs.promote(db, running, expected_head=None, bundle=bundle, now=NOW)
    clock.t = 5000.0
    assert stage(rs, host, lock, bundle, pid, 2, parent=hexid(1)) is None  # created later
    with host.transaction() as db:
        assert rs.compact(db, now=1000.0 + RETENTION - 1) == 0
        assert rs.get(db, hexid(1))[2] is not None
    with host.transaction() as db:
        assert rs.compact(db, now=1000.0 + RETENTION) == 1
    with host.transaction() as db:
        rev, cancel, pl = rs.get(db, hexid(1))
        assert pl is None and rev == pub  # head compacted, record and hashes kept
        assert rs.program(db, pid)['head'] == hexid(1)
        assert rs.get(db, hexid(2))[2] is not None
        events = [(e.event, e.revision_id) for e in rs.journal(db, pid) if e.event == 'compacted']
    assert events == [('compacted', hexid(1))]
    with host.transaction() as db:
        assert rs.compact(db, now=1000.0 + RETENTION) == 0  # nothing left to compact at that age
        assert len([e for e in rs.journal(db, pid) if e.event == 'compacted']) == 1


# ---- fail closed reads ----

def test_altered_record_or_columns_raise(rs, host, lock, bundle):
    pid = new_program(rs, host)
    assert stage(rs, host, lock, bundle, pid, 1) is None
    original = host.db.execute('SELECT record FROM program_revisions').fetchone()[0]
    for column, value in (('state', 'failed'), ('job_id', hexid(5)), ('kind', 'rebuild'), ('parent', hexid(8)),
                          ('derived_from', hexid(8)), ('evidence_revision', hexid(8)), ('brief_id', hexid(8)),
                          ('published_seq', 3), ('reason', 'x')):
        host.db.execute(f'UPDATE program_revisions SET {column}=?', (value,))
        with pytest.raises(RuntimeError):
            with host.transaction() as db:
                rs.get(db, hexid(1))
        with pytest.raises(RuntimeError):
            with host.transaction() as db:
                rs.revisions(db, pid)
        if column != 'state':  # a changed state column legitimately drops the row from this scan
            with pytest.raises(RuntimeError):
                with host.transaction() as db:
                    rs.staged_or_running(db)
        host.db.execute('UPDATE program_revisions SET state=\'staged\', job_id=NULL, kind=\'edit\', parent=NULL, '
                        'derived_from=NULL, evidence_revision=?, brief_id=NULL, published_seq=NULL, reason=NULL',
                        (hexid(1),))
    with host.transaction() as db:
        assert rs.get(db, hexid(1)) is not None
    host.db.execute('UPDATE program_revisions SET record=?', (original.replace('"staged"', '"running"'),))
    with pytest.raises(RuntimeError):
        with host.transaction() as db:
            rs.get(db, hexid(1))
    host.db.execute('UPDATE program_revisions SET record=?', ('not json',))
    with pytest.raises(RuntimeError):
        with host.transaction() as db:
            rs.get(db, hexid(1))
    host.db.execute('UPDATE program_revisions SET record=?, payload=?', (original, '{"nope": 1}'))
    with pytest.raises(RuntimeError):
        with host.transaction() as db:
            rs.get(db, hexid(1))


# ---- journal ----

def test_journal_seq_strictly_increasing_and_validated(rs, host, lock, bundle):
    pid = new_program(rs, host)
    run_one(rs, host, lock, bundle, pid, 1)
    with host.transaction() as db:
        rs.append_journal(db, pid, hexid(1), 'recovered', {'why': 'x', 'n': 3, 'ok': True, 'none': None})
        rs.append_journal(db, pid, None, 'compacted')
        entries = rs.journal(db, pid)
    seqs = [e.seq for e in entries]
    assert seqs == sorted(set(seqs)) and len(seqs) == 4
    assert all(b > a for a, b in zip(seqs, seqs[1:]))
    count = journal_count(host)
    bad = [('not_an_event', {}), ('staged', {'Bad Key': 1}), ('staged', {'k': 'caf' + chr(0xE9)}),
           ('staged', {'k': 'x' * 241}), ('staged', {f'k{i}'.replace('0', 'a'): 1 for i in range(17)}),
           ('staged', {'k': {'nested': 1}})]
    for event, detail in bad:
        with pytest.raises(ValidationError):
            with host.transaction() as db:
                rs.append_journal(db, pid, None, event, detail)
    with pytest.raises(ValidationError):
        with host.transaction() as db:
            rs.append_journal(db, 'short', None, 'staged', {})
    assert journal_count(host) == count


def test_journal_scoped_per_program(rs, host):
    a, b = new_program(rs, host), new_program(rs, host)
    with host.transaction() as db:
        rs.append_journal(db, a, None, 'staged')
        rs.append_journal(db, b, None, 'staged')
        assert len(rs.journal(db, a)) == 1 and len(rs.journal(db, b)) == 1


# ---- cancel and recovery reads ----

def test_set_cancel_and_reads(rs, host, lock, bundle, clock):
    pid = new_program(rs, host)
    clock.t = 1.0
    assert stage(rs, host, lock, bundle, pid, 2) is None
    clock.t = 0.5
    assert stage(rs, host, lock, bundle, pid, 1) is None
    with host.transaction() as db:
        assert [r.revision_id for r in rs.revisions(db, pid)] == [hexid(1), hexid(2)]
        assert [r.revision_id for r in rs.staged_or_running(db)] == [hexid(1), hexid(2)]
        assert rs.set_cancel(db, hexid(2)) is True
        assert rs.set_cancel(db, hexid(99)) is False
        assert rs.get(db, hexid(2))[1] is True and rs.get(db, hexid(1))[1] is False
    with host.transaction() as db:
        assert rs.transition(db, in_state(staged(lock, bundle, pid, 1), 'failed', bundle),
                             expected_state='staged', event='failed')
        assert rs.set_cancel(db, hexid(1)) is False  # terminal: no-op
        assert [r.revision_id for r in rs.staged_or_running(db)] == [hexid(2)]


def test_helpers_share_one_transaction_and_roll_back(rs, host, lock, bundle):
    pid = new_program(rs, host)
    with pytest.raises(RuntimeError):
        with host.transaction() as db:
            assert rs.insert_staged(db, staged(lock, bundle, pid, 1), payload(), expected_parent=None) is None
            raise RuntimeError('boom')
    assert host.db.execute('SELECT COUNT(*) FROM program_revisions').fetchone()[0] == 0
    assert journal_count(host) == 0


def test_import_loads_no_heavy_modules():
    code = ('import sys; import engineering.program.revision_store; '
            "bad=[m for m in ('cadquery','OCP','google.genai','fastapi','httpx') if m in sys.modules]; "
            'sys.exit(1 if bad else 0)')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / 'backend'), str(ROOT)]))
    assert subprocess.run([sys.executable, '-c', code], env=env, cwd=str(ROOT)).returncode == 0


# ---- real store (posix only) ----

posix = pytest.mark.skipif(os.name != 'posix', reason='SQLiteStore is posix-only')


@posix
def test_real_store_user_version_and_coexistence(tmp_path):
    from engineering.bundles import BundlePublisher
    from engineering.store import SQLiteStore
    store = SQLiteStore(tmp_path)
    try:
        BundlePublisher(store)
        RevisionStore(store)
        RevisionStore(store)
        with store.transaction() as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 1
            names = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'jobs', 'briefs', 'publications', 'programs', 'program_revisions', 'program_journal'} <= names
    finally:
        store.close()


@posix
def test_real_store_no_fk_and_jobs_delete_and_journal_triggers(tmp_path, lock, bundle):
    from engineering.store import SQLiteStore
    store = SQLiteStore(tmp_path)
    try:
        rs = RevisionStore(store)
        with store.transaction() as db:
            pid = rs.create_program(db, 'c')
            assert rs.insert_staged(db, staged(lock, bundle, pid, 1), payload(), expected_parent=None) is None
            assert [r['table'] for r in db.execute("PRAGMA foreign_key_list('program_revisions')")] == ['programs']
            db.execute("INSERT INTO briefs VALUES ('b','d',1,'{}')")
            db.execute("INSERT INTO jobs VALUES ('j','b','k','{}',0,0.0,NULL,0)")
            db.execute("DELETE FROM jobs WHERE id='j'")
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            with store.transaction() as db:
                db.execute('DELETE FROM program_journal')
    finally:
        store.close()


# =====================================================================================================
# F06 task 4: RevisionService (stage, run, settle, cancel, snapshot views, open_artifact)
# =====================================================================================================
# Host tests (Windows) use HostStore and fakes. Everything marked ``native_only`` needs cadquery, a real
# posix SQLiteStore and ProcessRunner: those tests run only in the CI native shards.

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from engineering.program.transactions import RevisionService

F06 = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'f06'
P03 = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'p03'
CLIENT = 'client-a'
native_only = pytest.mark.skipif(os.name != 'posix', reason='native service tests need posix SQLiteStore')


def _host_service(host, clock):
    pub = SimpleNamespace()
    jobs = SimpleNamespace(store=host, publisher=pub)
    briefs = SimpleNamespace(repository=SimpleNamespace(store=host))
    return RevisionService(host, jobs, pub, briefs, clock=clock)


def test_service_constructor_requires_one_shared_store(host, clock):
    other = HostStore()
    pub = SimpleNamespace()
    good_jobs = SimpleNamespace(store=host, publisher=pub)
    good_briefs = SimpleNamespace(repository=SimpleNamespace(store=host))
    RevisionService(host, good_jobs, pub, good_briefs, clock=clock)
    with pytest.raises(ValueError):
        RevisionService(host, good_jobs, pub, SimpleNamespace(repository=SimpleNamespace(store=other)))
    with pytest.raises(ValueError):
        RevisionService(host, SimpleNamespace(store=other, publisher=pub), pub, good_briefs)
    with pytest.raises(ValueError):
        RevisionService(host, SimpleNamespace(store=host, publisher=SimpleNamespace()), pub, good_briefs)
    with pytest.raises(ValueError):
        RevisionService(host, good_jobs, pub, SimpleNamespace(repository=SimpleNamespace()))


def test_service_host_foreign_client_and_stale_parent_write_nothing(host, clock):
    svc = _host_service(host, clock)
    pid = svc.create_program('owner')
    assert svc.current(pid, client='owner') is None
    assert svc.history(pid, client='owner') == ()
    for call in (lambda: svc.current(pid, client='other'), lambda: svc.history(pid, client='other'),
                 lambda: svc.journal(pid, client='other'), lambda: svc.current('f' * 32, client='owner'),
                 lambda: svc.view(hexid(9), client='owner'),
                 lambda: svc.open_artifact(hexid(9), 'step', client='owner')):
        with pytest.raises(KeyError):
            call()
    with pytest.raises(KeyError):
        asyncio.run(svc.stage(pid, 'x', source_id='a.mir', expected_parent=None, client='other'))
    before = (journal_count(host), host.db.execute('SELECT COUNT(*) FROM program_revisions').fetchone()[0])
    # Stale parent is refused before any compile (the source below is not even valid MEL).
    out = asyncio.run(svc.stage(pid, 'not mel', source_id='a.mir', expected_parent=hexid(5), client='owner'))
    assert out == RevisionRefusal(('stale_parent',))
    assert before == (journal_count(host), host.db.execute('SELECT COUNT(*) FROM program_revisions').fetchone()[0])


def test_service_host_capacity_refusal_before_compile(host, clock, lock, bundle):
    svc = _host_service(host, clock)
    pid = svc.create_program('c')
    for n in range(1, rst.MAX_STAGED_PER_PROGRAM + 1):
        assert stage(svc.revision_store, host, lock, bundle, pid, n) is None
    out = asyncio.run(svc.stage(pid, 'not mel', source_id='a.mir', expected_parent=None, client='c'))
    assert out == RevisionRefusal(('capacity',), 'staged revisions per program')


def test_service_cancel_of_a_staged_revision_never_admitted(host, clock, lock, bundle):
    svc = _host_service(host, clock)
    pid = svc.create_program('c')
    assert stage(svc.revision_store, host, lock, bundle, pid, 1) is None
    rid = hexid(1)
    out = asyncio.run(svc.cancel(rid, client='c'))
    assert out.state == 'cancelled'
    assert asyncio.run(svc.cancel(rid, client='c')).state == 'cancelled'
    with pytest.raises(KeyError):
        asyncio.run(svc.cancel(rid, client='other'))
    assert [e.event for e in svc.journal(pid, client='c')] == ['staged', 'cancel_requested', 'cancelled']
    assert svc.view(rid, client='c').artifacts == 'none'


def test_service_settle_is_a_noop_unless_running(host, clock, lock, bundle):
    svc = _host_service(host, clock)
    pid = svc.create_program('c')
    assert stage(svc.revision_store, host, lock, bundle, pid, 1) is None
    assert svc.settle(hexid(1)).state == 'staged'
    with pytest.raises(KeyError):
        svc.settle(hexid(2))


def test_service_crash_point_is_a_noop_and_recover_is_a_stub(host, clock):
    svc = _host_service(host, clock)
    for name in ('after_admit_before_link', 'after_link_before_start', 'before_settle_commit',
                 'restore_after_validate'):
        assert svc._crash_point(name) is None
    assert svc.recover() is None


def test_import_transactions_loads_no_native_provider_or_web_module():
    code = (
        'import sys, json\n'
        'import engineering.program.transactions\n'
        'print(json.dumps(sorted(m for m in ("cadquery", "OCP", "google.genai", "fastapi", "httpx")'
        ' if m in sys.modules)))\n')
    env = dict(os.environ, PYTHONPATH=f'{ROOT / "backend"}{os.pathsep}{ROOT}')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env,
                         timeout=120, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_transactions_module_is_ascii_lf():
    text = (ROOT / 'backend' / 'engineering' / 'program' / 'transactions.py').read_bytes()
    assert text.isascii() and b'\r' not in text


# ---- native service harness (CI native shards only) ----

BEAM = (P03 / 'beam.mir').read_text(encoding='utf-8')
WHITESPACE = (F06 / 'beam_whitespace.mir').read_text(encoding='utf-8')
REJECTED = (F06 / 'beam_5_10.mir').read_text(encoding='utf-8')


def _finalize_brief(service, review):
    from engineering.contracts import REVIEW_FIELDS, ReviewAcknowledgement
    return service.finalize(ReviewAcknowledgement(
        draft_id=review.draft_id, draft_revision=review.draft_revision, brief_sha256=review.brief_sha256,
        acknowledged_fields=REVIEW_FIELDS, reviewed_at=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
        reviewed_by='local-user'))


@pytest.fixture(scope='module')
def occt():
    pytest.importorskip('cadquery')
    if os.name != 'posix':
        pytest.skip('ProcessRunner needs posix process groups')
    from engineering.program.adapters.occt import OcctAdapter
    return OcctAdapter()


@pytest.fixture(scope='module')
def evals():
    """Real native evidence, captured once: a search of the beam brief (5, 10, 15 mm: accepted at 15)
    and of the heights-5/10-only brief (rejected). Replayed by the job controller in every test."""
    pytest.importorskip('cadquery')
    if os.name != 'posix':
        pytest.skip('ProcessRunner needs posix process groups')
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.language.semantic import compile_source
    from engineering.search import search
    out = []
    for path, expected in ((P03 / 'beam.mir', 'selected'), (F06 / 'beam_5_10.mir', 'rejected')):
        compiled = compile_source(path.read_bytes(), source_id='capture.mir')
        svc = BriefService(MemoryBriefRepository())
        prepared = svc.create(json.loads(compiled.brief_content.model_dump_json()))
        brief = _finalize_brief(svc, SimpleNamespace(
            draft_id=prepared.draft.draft_id, draft_revision=prepared.draft.revision,
            brief_sha256=prepared.draft.content.brief_sha256))
        result = asyncio.run(search(brief))
        assert result.state == expected
        out.extend(result.evaluations)
    return tuple(out)


def _replay(evals):
    from engineering.cantilever import CantileverAdapter
    from engineering.service import EngineeringUnavailable

    class Replay(CantileverAdapter):
        """Replays captured native evaluations keyed by candidate (candidate_sha256 plus its brief)."""
        fail = False
        delay = 0
        calls = 0

        async def evaluate(self, candidate):
            type(self).calls += 1
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise EngineeringUnavailable('replay unavailable')
            return next(e for e in evals if e['candidate'] == candidate)

    return Replay()


def _publisher_class():
    from engineering.bundles import BundlePublisher

    class FailablePublisher(BundlePublisher):
        fail = False

        async def publish(self, *args):
            if self.fail:
                raise OSError('injected publication failure')
            return await super().publish(*args)

    return FailablePublisher


class World:
    pass


@asynccontextmanager
async def world(tmp_path, evals, occt):
    from engineering.briefs import BriefService
    from engineering.jobs import JobController
    from engineering.store import SQLiteBriefRepository, SQLiteStore
    w = World()
    w.store = SQLiteStore(tmp_path)
    w.pub = _publisher_class()(w.store)
    w.briefs = BriefService(SQLiteBriefRepository(w.store))
    w.replay = _replay(evals)
    w.jobs = JobController(w.store, publisher=w.pub, adapter=w.replay)
    w.svc = RevisionService(w.store, w.jobs, w.pub, w.briefs,
                            adapter_factory=lambda backend, mode='native': occt)
    w.pid = w.svc.create_program(CLIENT)
    try:
        yield w
    finally:
        await w.svc.close()
        await w.jobs.close()
        w.store.close()


async def stage_final(w, text, parent):
    rev = await w.svc.stage(w.pid, text, source_id='beam.mir', expected_parent=parent, client=CLIENT)
    assert isinstance(rev, ProgramRevision), rev
    brief = _finalize_brief(w.briefs, rev)
    return rev, brief.brief_id


async def start_run(w, rev, brief_id):
    out = await w.svc.run(rev.revision_id, brief_id, client=CLIENT)
    assert isinstance(out, ProgramRevision) and out.state == 'running', out
    return out


async def land_staged(w, rev, brief_id):
    await start_run(w, rev, brief_id)
    await w.svc.wait(rev.revision_id)
    return w.svc.view(rev.revision_id, client=CLIENT).revision


async def land(w, text, parent):
    """Stage, finalize, run and settle one revision; returns the settled record."""
    rev, brief_id = await stage_final(w, text, parent)
    return await land_staged(w, rev, brief_id)


def step_sha(w, rid):
    download = w.svc.open_artifact(rid, 'step', client=CLIENT)
    try:
        return download.sha256
    finally:
        download.close()


def table_counts(w):
    with w.store.transaction() as db:
        return tuple(db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                     for t in ('program_revisions', 'program_journal', 'drafts', 'jobs'))


async def with_first(w):
    first = await land(w, BEAM, None)
    assert first.state == 'published', first
    view = w.svc.current(w.pid, client=CLIENT)
    assert view.revision.revision_id == first.revision_id and view.artifacts == 'available' and view.is_current
    return first, step_sha(w, first.revision_id)


def assert_head_unchanged(w, first, sha):
    view = w.svc.current(w.pid, client=CLIENT)
    assert view.revision.revision_id == first.revision_id
    assert view.artifacts == 'available'
    assert step_sha(w, first.revision_id) == sha


@native_only
def test_first_revision_publishes_and_is_current(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, _ = await with_first(w)
            assert first.published_seq == 1 and first.kind == 'edit' and first.parent is None
            assert first.evidence_revision_id == first.revision_id
            assert [e.event for e in w.svc.journal(w.pid, client=CLIENT)] == [
                'staged', 'admit_intent', 'linked', 'published']
            assert w.svc.history(w.pid, client=CLIENT) == (first,)
    asyncio.run(scenario())


@native_only
def test_rejected_batch_leaves_the_head_and_its_download(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, sha = await with_first(w)
            second = await land(w, REJECTED, first.revision_id)
            assert second.state == 'rejected' and second.published_seq is None
            assert w.svc.view(second.revision_id, client=CLIENT).artifacts == 'none'
            assert_head_unchanged(w, first, sha)
    asyncio.run(scenario())


@native_only
def test_unavailable_batch_leaves_the_head_and_its_download(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, sha = await with_first(w)
            w.replay.fail = True
            second = await land(w, WHITESPACE, first.revision_id)
            assert second.state == 'failed'
            assert_head_unchanged(w, first, sha)
    asyncio.run(scenario())


@native_only
def test_cancelled_batch_leaves_the_head_and_its_download(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, sha = await with_first(w)
            w.replay.delay = 10
            rev, brief_id = await stage_final(w, WHITESPACE, first.revision_id)
            await start_run(w, rev, brief_id)
            await asyncio.sleep(0.5)
            await w.svc.cancel(rev.revision_id, client=CLIENT)
            await w.svc.wait(rev.revision_id)
            assert w.svc.view(rev.revision_id, client=CLIENT).revision.state == 'cancelled'
            assert_head_unchanged(w, first, sha)
            # Cancelling a published or already cancelled revision is a no-op.
            assert (await w.svc.cancel(first.revision_id, client=CLIENT)).state == 'published'
            assert (await w.svc.cancel(rev.revision_id, client=CLIENT)).state == 'cancelled'
    asyncio.run(scenario())


@native_only
def test_publication_failure_batch_leaves_the_head_and_its_download(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, sha = await with_first(w)
            w.pub.fail = True
            second = await land(w, WHITESPACE, first.revision_id)
            assert second.state == 'failed'
            assert_head_unchanged(w, first, sha)
    asyncio.run(scenario())


@native_only
def test_exception_before_settle_commit_rolls_everything_back(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, sha = await with_first(w)

            def boom(name):
                if name == 'before_settle_commit':
                    raise RuntimeError('injected before settle commit')
            w.svc._crash_point = boom
            second = await land(w, WHITESPACE, first.revision_id)
            assert second.state == 'running' and second.published_seq is None
            assert isinstance(w.svc.last_settle_error, RuntimeError)
            assert_head_unchanged(w, first, sha)
            assert 'published' not in [e.event for e in w.svc.journal(w.pid, client=CLIENT)][4:]
            # The job finished; a later settle with the seam cleared rolls forward exactly once.
            del w.svc._crash_point
            settled = w.svc.settle(second.revision_id)
            assert settled.state == 'published' and settled.published_seq == 2
            assert w.svc.current(w.pid, client=CLIENT).revision.revision_id == second.revision_id
            assert w.svc.settle(second.revision_id) == settled
    asyncio.run(scenario())


@native_only
def test_read_versus_promotion_hammer_every_view_is_consistent(tmp_path, evals, occt):
    from engineering.jobs import parse_run

    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, _ = await with_first(w)
            second, brief_id = await stage_final(w, WHITESPACE, first.revision_id)
            stop, problems, seen = threading.Event(), [], []

            def check(view):
                rev = view.revision
                if rev.state == 'published':
                    assert view.is_current
                    assert rev.bundle is not None and rev.published_seq is not None
                    with w.store.transaction() as db:
                        row = db.execute('SELECT record FROM jobs WHERE id=?', (rev.job_id,)).fetchone()
                    assert parse_run(row['record']).bundle == rev.bundle
                    assert view.artifacts == 'available'
                else:
                    assert view.artifacts == 'none' and not view.is_current

            def reader():
                try:
                    while not stop.is_set():
                        current = w.svc.current(w.pid, client=CLIENT)
                        pending = w.svc.view(second.revision_id, client=CLIENT)
                        assert current.is_current and current.revision.state == 'published'
                        check(current)
                        check(pending)
                        seen.append((current.revision.revision_id, pending.revision.state))
                        time.sleep(0.001)
                except BaseException as exc:  # reported by the main thread
                    problems.append(exc)

            threads = [threading.Thread(target=reader) for _ in range(3)]
            for t in threads:
                t.start()
            try:
                await start_run(w, second, brief_id)
                await w.svc.wait(second.revision_id)
                await asyncio.sleep(0.05)
            finally:
                stop.set()
                for t in threads:
                    t.join(timeout=30)
            assert problems == []
            assert seen
            assert {rid for rid, _ in seen} <= {first.revision_id, second.revision_id}
            assert w.svc.current(w.pid, client=CLIENT).revision.revision_id == second.revision_id
    asyncio.run(scenario())


@native_only
def test_open_artifact_is_pinned_to_its_own_revision(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, sha1 = await with_first(w)
            second = await land(w, WHITESPACE, first.revision_id)
            assert second.state == 'published' and second.job_id != first.job_id
            for rev in (first, second):
                recorded = next(p.sha256 for p in rev.bundle.payloads if p.path == 'model.step')
                download = w.svc.open_artifact(rev.revision_id, 'step', client=CLIENT)
                try:
                    assert download.sha256 == recorded
                finally:
                    download.close()
            assert step_sha(w, first.revision_id) == sha1
            with pytest.raises(KeyError):
                w.svc.open_artifact(first.revision_id, 'no-such-format', client=CLIENT)
    asyncio.run(scenario())


@native_only
def test_stale_parent_at_staging_writes_nothing(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            await with_first(w)
            before = table_counts(w)
            out = await w.svc.stage(w.pid, BEAM, source_id='beam.mir', expected_parent=None, client=CLIENT)
            assert out == RevisionRefusal(('stale_parent',))
            assert table_counts(w) == before
    asyncio.run(scenario())


@native_only
def test_stale_parent_between_staged_siblings_first_to_publish_wins(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, sha = await with_first(w)
            a, a_brief = await stage_final(w, WHITESPACE, first.revision_id)
            b, b_brief = await stage_final(w, WHITESPACE, first.revision_id)
            winner = await land_staged(w, a, a_brief)
            assert winner.state == 'published' and winner.published_seq == 2
            calls, jobs_before = type(w.replay).calls, table_counts(w)[3]
            out = await w.svc.run(b.revision_id, b_brief, client=CLIENT)
            assert out == RevisionRefusal(('stale_parent',))
            lost = w.svc.view(b.revision_id, client=CLIENT).revision
            assert lost.state == 'conflict' and lost.reason == 'stale_parent'
            assert type(w.replay).calls == calls and table_counts(w)[3] == jobs_before  # no native work
            assert w.svc.current(w.pid, client=CLIENT).revision.revision_id == a.revision_id
            assert step_sha(w, first.revision_id) == sha
    asyncio.run(scenario())


@native_only
def test_promotion_cas_conflict_when_the_head_moved_during_the_run(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, _ = await with_first(w)
            a, a_brief = await stage_final(w, WHITESPACE, first.revision_id)
            c, c_brief = await stage_final(w, WHITESPACE, first.revision_id)
            real_settle = w.svc.settle
            w.svc.settle = lambda rid: None  # let a's job finish without settling it
            try:
                await start_run(w, a, a_brief)
                await w.svc.wait(a.revision_id)
            finally:
                del w.svc.settle
            assert w.svc.view(a.revision_id, client=CLIENT).revision.state == 'running'
            winner = await land_staged(w, c, c_brief)
            assert winner.state == 'published'
            lost = real_settle(a.revision_id)
            assert lost.state == 'conflict' and lost.reason == 'stale_parent'
            assert w.svc.current(w.pid, client=CLIENT).revision.revision_id == c.revision_id
    asyncio.run(scenario())


@native_only
def test_cancel_between_admit_and_link_leaves_the_native_slot_reclaimable(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, sha = await with_first(w)
            a, a_brief = await stage_final(w, WHITESPACE, first.revision_id)
            b, b_brief = await stage_final(w, WHITESPACE, first.revision_id)

            def cancel_at(name):
                if name == 'after_admit_before_link':
                    w.svc._cancel_sync(a.revision_id, client=CLIENT)
            w.svc._crash_point = cancel_at
            running = await w.svc.run(a.revision_id, a_brief, client=CLIENT)
            del w.svc._crash_point
            assert running.state == 'running'
            await w.svc.wait(a.revision_id)
            assert w.svc.view(a.revision_id, client=CLIENT).revision.state == 'cancelled'
            assert_head_unchanged(w, first, sha)
            # The slot was released: a following run admits and publishes.
            following = await land_staged(w, b, b_brief)
            assert following.state == 'published'
    asyncio.run(scenario())


@native_only
def test_review_expired_after_the_draft_is_tombstoned(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, _ = await with_first(w)
            rev, brief_id = await stage_final(w, WHITESPACE, first.revision_id)
            with w.store.transaction() as db:
                db.execute('UPDATE drafts SET record=? WHERE id=?', ('{"expired":true}', rev.draft_id))
            calls = type(w.replay).calls
            out = await w.svc.run(rev.revision_id, brief_id, client=CLIENT)
            assert out == RevisionRefusal(('review_expired',))
            assert w.svc.view(rev.revision_id, client=CLIENT).revision.state == 'staged'
            assert type(w.replay).calls == calls
    asyncio.run(scenario())


@native_only
def test_bundles_cleanup_deleting_old_jobs_with_revision_rows_present(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, _ = await with_first(w)
            second = await land(w, REJECTED, first.revision_id)
            old = time.time() - 8 * 86400
            with w.store.transaction() as db:
                db.execute('UPDATE jobs SET deadline=?, reserved=0', (old,))
            w.pub.cleanup()
            with w.store.transaction() as db:
                assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
                assert db.execute('SELECT COUNT(*) FROM publications').fetchone()[0] == 0
            assert len(w.svc.history(w.pid, client=CLIENT)) == 2
            view = w.svc.current(w.pid, client=CLIENT)
            assert view.revision.revision_id == first.revision_id and view.artifacts == 'missing'
            assert w.svc.view(second.revision_id, client=CLIENT).revision.state == 'rejected'
            with pytest.raises(KeyError):
                w.svc.open_artifact(first.revision_id, 'step', client=CLIENT)
            assert w.svc.settle(first.revision_id).state == 'published'
    asyncio.run(scenario())


@native_only
def test_expired_head_shows_the_head_with_expired_artifacts(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, _ = await with_first(w)
            second = await land(w, WHITESPACE, first.revision_id)
            assert second.state == 'published'
            with w.store.transaction() as db:
                db.execute('UPDATE publications SET expires=? WHERE job=?', (time.time() - 1, second.job_id))
            view = w.svc.current(w.pid, client=CLIENT)
            assert view.revision.revision_id == second.revision_id  # never an older revision
            assert view.artifacts == 'expired' and view.is_current
            assert w.svc.view(first.revision_id, client=CLIENT).artifacts == 'available'
    asyncio.run(scenario())


@native_only
def test_foreign_client_gets_key_error_everywhere(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            first, brief_id = await stage_final(w, BEAM, None)
            rid = first.revision_id
            calls = (lambda: w.svc.current(w.pid, client='other'), lambda: w.svc.view(rid, client='other'),
                     lambda: w.svc.history(w.pid, client='other'), lambda: w.svc.journal(w.pid, client='other'),
                     lambda: w.svc.open_artifact(rid, 'step', client='other'))
            for call in calls:
                with pytest.raises(KeyError):
                    call()
            with pytest.raises(KeyError):
                await w.svc.stage(w.pid, BEAM, source_id='beam.mir', expected_parent=None, client='other')
            with pytest.raises(KeyError):
                await w.svc.run(rid, brief_id, client='other')
            with pytest.raises(KeyError):
                await w.svc.cancel(rid, client='other')
            assert w.svc.view(rid, client=CLIENT).revision.state == 'staged'
    asyncio.run(scenario())
