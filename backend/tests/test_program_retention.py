"""K51 retention cuts over only after a durable archive exists."""

import json
import os
import sqlite3

import pytest

from engineering.admission import AdmissionBusy
from engineering.capabilities import CAPABILITY
from engineering.program.delivered import EvidenceStore
from engineering.program.facade import ProgramQuota
from engineering.program.retention import retire_program
from engineering.program.revision_store import MAX_PROGRAMS, RevisionStore
from engineering.store import SQLiteStore
from tests.test_program_revision_records import bundle, compiled, lock  # noqa: F401
from tests.test_program_revisions import Clock, stage


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='engineering volumes require POSIX')
RETENTION = CAPABILITY.resources.state_retention_seconds


@pytest.fixture
def volume(tmp_path):
    store = SQLiteStore(tmp_path / 'live')
    clock = Clock()
    rs = RevisionStore(store, clock=clock)
    root = tmp_path / 'archive'
    root.mkdir()
    try:
        yield store, clock, rs, root
    finally:
        store.close()


def test_retirement_frees_active_capacity_beyond_the_old_lifetime_ceiling(volume):
    store, clock, rs, root = volume
    quota = ProgramQuota(store, programs=1, clock=clock)
    with store.transaction() as db:
        retired = rs.create_program(db, 'alpha')
        for i in range(MAX_PROGRAMS - 1):
            rs.create_program(db, f'client-{i}')
    with pytest.raises(AdmissionBusy), store.transaction() as db:
        rs.create_program(db, 'extra')
    assert quota.check_create('alpha') == 'quota_programs'

    clock.t += RETENTION
    archive = retire_program(store, retired, root, clock=clock)
    assert json.loads((archive / 'history.json').read_text())['program']['id'] == retired
    assert quota.check_create('alpha') is None
    with store.transaction() as db:
        assert rs.program(db, retired) is None
        replacement = rs.create_program(db, 'alpha')
        assert replacement != retired
    assert quota.check_create('alpha') == 'quota_programs'


def test_young_and_pending_programs_cannot_be_retired(volume, lock, bundle):
    store, clock, rs, root = volume
    with store.transaction() as db:
        young = rs.create_program(db, 'alpha')
        pending = rs.create_program(db, 'alpha')
    assert stage(rs, store, lock, bundle, pending, 1) is None

    with pytest.raises(ValueError, match='retention age'):
        retire_program(store, young, root, clock=clock)
    clock.t += RETENTION
    with pytest.raises(ValueError, match='staged or running'):
        retire_program(store, pending, root, clock=clock)
    assert list(root.iterdir()) == []


def test_retry_after_database_failure_uses_archive_and_keeps_journal_guard(volume):
    store, clock, rs, root = volume
    EvidenceStore(store)
    with store.transaction() as db:
        pid = rs.create_program(db, 'alpha')
        rs.append_journal(db, pid, None, 'recovered', {'reason': 'interrupted'})
        db.execute("""CREATE TRIGGER simulated_failure BEFORE DELETE ON programs
                      BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END""")
    clock.t += RETENTION

    with pytest.raises(sqlite3.IntegrityError, match='simulated interruption'):
        retire_program(store, pid, root, clock=clock)
    saved = (root / pid / 'history.json').read_bytes()
    with store.transaction() as db:
        assert rs.program(db, pid) is not None
        assert len(rs.journal(db, pid)) == 1
        db.execute('DROP TRIGGER simulated_failure')
    with pytest.raises(sqlite3.IntegrityError, match='append-only'), store.transaction() as db:
        db.execute('DELETE FROM program_journal WHERE program_id=?', (pid,))

    archive = retire_program(store, pid, root, clock=clock)
    assert (archive / 'history.json').read_bytes() == saved
    assert retire_program(store, pid, root, clock=clock) == archive
    with store.transaction() as db:
        assert rs.program(db, pid) is None
        assert rs.journal(db, pid) == ()
        other = rs.create_program(db, 'other')
        rs.append_journal(db, other, None, 'recovered')
    with pytest.raises(sqlite3.IntegrityError, match='append-only'), store.transaction() as db:
        db.execute('DELETE FROM program_journal WHERE program_id=?', (other,))
