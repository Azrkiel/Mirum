"""N1c-2 review M1: the job row persists its backend; an idempotency replay cannot cross backends;
the revision key prefix is reserved and refused on the public job route."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from backend.tests.test_engineering_contracts import data, review_for
from engineering.briefs import BriefService, RevisionConflict
from engineering.jobs import JobController, native_slot
from engineering.store import SQLiteBriefRepository, SQLiteStore

NATIVE = 'mirum-native'
KEY = 'program-revision:rev-abc'


def build(tmp_path):
    store = SQLiteStore(tmp_path)
    svc = BriefService(SQLiteBriefRepository(store))
    brief = svc.finalize(review_for(svc.create(data()['content']).draft))
    return store, brief, JobController(store)


def close(store, jobs, *admitted):
    for item in admitted:
        if item.owner is not None:
            native_slot.release(item.owner)
    asyncio.run(jobs.close())
    store.close()


def test_replay_with_a_different_backend_is_refused_both_ways(tmp_path):
    store, brief, jobs = build(tmp_path)
    first = jobs.admit(brief.brief_id, KEY, 'alice')          # OCCT job via the public route
    assert first.backend == 'occt'
    with pytest.raises(RevisionConflict):                     # the native revision run must not adopt it
        jobs.admit(brief.brief_id, KEY, 'alice', backend=NATIVE)
    close(store, jobs, first)
    (tmp_path / 'b').mkdir()
    store, brief, jobs = build(tmp_path / 'b')
    native = jobs.admit(brief.brief_id, KEY, 'alice', backend=NATIVE)
    with pytest.raises(RevisionConflict):
        jobs.admit(brief.brief_id, KEY, 'alice')
    close(store, jobs, native)


def test_same_backend_replay_still_works_and_carries_the_persisted_backend(tmp_path):
    store, brief, jobs = build(tmp_path)
    first = jobs.admit(brief.brief_id, KEY, 'alice', backend=NATIVE)
    again = jobs.admit(brief.brief_id, KEY, 'alice', backend=NATIVE)
    assert again.job.job_id == first.job.job_id and again.owner is None and again.backend == NATIVE
    close(store, jobs, first)


def test_old_rows_without_a_backend_replay_as_occt(tmp_path):
    store, brief, jobs = build(tmp_path)
    first = jobs.admit(brief.brief_id, 'k', 'alice')
    store.db.execute('UPDATE jobs SET backend=NULL')
    again = jobs.admit(brief.brief_id, 'k', 'alice')
    assert again.job.job_id == first.job.job_id and again.backend == 'occt'
    with pytest.raises(RevisionConflict):
        jobs.admit(brief.brief_id, 'k', 'alice', backend=NATIVE)
    close(store, jobs, first)


def test_existing_database_without_the_column_is_migrated_additively(tmp_path):
    old = sqlite3.connect(tmp_path / 'engineering.sqlite3')
    old.executescript('''
        CREATE TABLE jobs(id TEXT PRIMARY KEY, brief TEXT NOT NULL, key TEXT NOT NULL UNIQUE, record TEXT NOT NULL,
            cancel INTEGER NOT NULL DEFAULT 0, deadline REAL NOT NULL, expires REAL, reserved INTEGER NOT NULL);
        INSERT INTO jobs(id,brief,key,record,deadline,reserved) VALUES ('j','b','k','{}',0,0);
        PRAGMA user_version=1;
    ''')
    old.commit()
    old.close()
    store = SQLiteStore(tmp_path)
    cols = [r[1] for r in store.db.execute('PRAGMA table_info(jobs)')]
    assert 'backend' in cols
    assert store.db.execute("SELECT backend FROM jobs WHERE id='j'").fetchone()[0] is None  # NULL reads as occt
    assert store.db.execute('PRAGMA user_version').fetchone()[0] == 1
    store.close()


def test_public_jobs_route_refuses_the_reserved_revision_key_prefix():
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from engineering.api_v1 import attach_v1_routes

    calls = []

    class Jobs:
        def admit(self, *args, **kwargs):
            calls.append(args)
            raise RuntimeError('reached admit')

        def start(self, admitted):
            return admitted

    app = FastAPI()
    app.state.limiter = limiter = Limiter(key_func=get_remote_address)
    app.state.engineering = SimpleNamespace(jobs=Jobs())
    attach_v1_routes(app, limiter)
    with TestClient(app) as http:
        for key in (KEY, 'program-revision:', 'program-revision:' + 'x' * 100):
            response = http.post('/engineering/v1/jobs', json={'brief_id': 'b', 'idempotency_key': key})
            assert response.status_code == 422, (key, response.text)
        assert calls == []                                    # nothing was admitted under a reserved key
        http.post('/engineering/v1/jobs', json={'brief_id': 'b', 'idempotency_key': 'ordinary'})
        assert len(calls) == 1                                # control: a normal key still reaches admit
