"""F08 task 2: the in-process ProgramInterface facade (SDK), ProgramQuota, staged expiry, brief binding.

Pure tests (offline functions, error mapping, quota parsing, semaphore) run on every host. The facade
tests over a real RevisionService use SQLiteStore on posix (CI) and, on a non-posix host, an in-memory
stand-in with the same schema and transaction semantics, so they run on the Windows laptop too. Tests that
need the native kernel, the job controller and real artifacts are posix-only and run in the CI native
shards. Task 3 appends the HTTP tests below.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

from engineering.admission import AdmissionBusy, ClientQuotaExceeded
from engineering.briefs import BriefService, RevisionConflict
from engineering.capabilities import CAPABILITY
from engineering.contracts import REVIEW_FIELDS
from engineering.language.diagnostics import MelError, Position, Span, make
from engineering.program import interface as api
from engineering.program import offline
from engineering.program.adapters.mock import MockKernelAdapter  # noqa: F401  (import pin: adapters are pure)
from engineering.program.facade import (
    DEFAULT_SOURCE_ID, PROGRAM_QUOTA_DEFAULTS, PROGRAM_QUOTA_ENV, ProgramInterface, ProgramQuota,
    map_exception,
)
from engineering.program.interface import InterfaceFailure
from engineering.program.kernel import ProbeResult, check_plan, make_description
from engineering.program.revisions import RevisionPayload, RevisionRefusal
from engineering.program.transactions import RevisionService
from engineering.store import TOMBSTONE, ArtifactExpired, PreparationQuota, SQLiteBriefRepository
# Native service harness (posix + cadquery only; the fixtures skip elsewhere).
from tests.test_program_revisions import (  # noqa: F401
    BEAM as BEAM_TEXT, CLIENT, REJECTED as REJECTED_TEXT, evals, native_only, occt, world,
)

ROOT = Path(__file__).resolve().parents[2]
F08 = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'f08'
BEAM = (F08 / 'beam.mir').read_bytes()
BEAM_5_10 = (F08 / 'beam_5_10.mir').read_bytes()
RETENTION = CAPABILITY.resources.state_retention_seconds
TTL = 14400
ALICE, BOB = 'alice', 'bob'
BAD_ID = '0' * 32


def fail_of(coro):
    """Run a coroutine that must raise InterfaceFailure; return its envelope."""
    with pytest.raises(InterfaceFailure) as info:
        asyncio.run(coro)
    return info.value.error


# ------------------------------------------------------------------------------------ pure: offline

def test_offline_validate_format_plan_on_the_beam():
    ok = offline.validate_source(BEAM, 'beam.mir')
    assert ok.ok and ok.source_id == 'beam.mir' and len(ok.source_sha256) == 64
    fmt = offline.format_source(BEAM)
    assert fmt.formatted_source and offline.format_source(fmt.formatted_source.encode('utf-8')).unchanged
    plan = offline.plan_source(BEAM)
    assert plan.mode == 'native' and plan.backend == 'occt' and plan.candidate_count >= 1


@pytest.mark.parametrize('fn', [offline.validate_source, offline.format_source, offline.plan_source])
def test_offline_bad_source_id_is_s016_and_never_opened(fn):
    with pytest.raises(InterfaceFailure) as info:
        fn(BEAM, '../x')
    err = info.value.error
    assert err.code == 'MEL-S016' and err.kind == 'invalid_source'
    assert err.span is None or err.span.source_id is None
    assert '../x' not in err.model_dump_json()


@pytest.mark.parametrize('source,code', [
    (b'\xff\xfe', 'MEL-L001'), (b'\xef\xbb\xbfdesign x {}', 'MEL-L002'), (b'a\x00b', 'MEL-L003'),
    (b' ' * 65537, 'MEL-L022')])
def test_offline_lexical_diagnostics_stay_reachable(source, code):
    with pytest.raises(InterfaceFailure) as info:
        offline.validate_source(source)
    assert info.value.error.code == code


def test_offline_exactly_64_kib_reaches_the_compiler():
    with pytest.raises(InterfaceFailure) as info:
        offline.validate_source(b' ' * 65536)
    assert info.value.error.code != 'MEL-L022'


def test_offline_rejects_non_bytes():
    with pytest.raises(InterfaceFailure) as info:
        offline.validate_source('design x {}')
    assert info.value.error.code == 'invalid_field' and info.value.error.field == 'source'


def test_offline_import_pin():
    code = (
        'import sys\n'
        'import engineering.program.offline\n'
        "bad = [m for m in sys.modules if m.split('.')[0] in ('sqlite3', 'fastapi', 'httpx', 'starlette', "
        "'cadquery', 'genai') or m in ('engineering.store', 'engineering.jobs', "
        "'engineering.program.transactions', 'engineering.program.facade')]\n"
        'print(bad)\n')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / 'backend'), str(ROOT)]))
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env,
                         cwd=str(ROOT), check=True)
    assert out.stdout.strip() == '[]'


# -------------------------------------------------------------------------- pure: mapping and quotas

def _bare_interface():
    service = SimpleNamespace(store=None, clock=time.time, publisher=None)
    return ProgramInterface(service, None, None, ProgramQuota(None))


def test_facade_stateless_ops_run_without_a_store():
    face = _bare_interface()
    ok = asyncio.run(face.validate(BEAM, api.ValidateRequest(source_id='a.mir')))
    assert ok.source_id == 'a.mir'
    assert asyncio.run(face.format(BEAM)).formatted_source
    assert asyncio.run(face.plan(BEAM)).candidate_count >= 1
    err = fail_of(face.validate(BEAM, api.ValidateRequest(source_id='../x')))
    assert err.code == 'MEL-S016'


def test_facade_stateless_semaphore_is_non_queuing():
    face = _bare_interface()

    async def scenario():
        await face._stateless.acquire()
        await face._stateless.acquire()
        try:
            for call in (face.validate, face.format, face.plan):
                with pytest.raises(InterfaceFailure) as info:
                    await call(BEAM)
                err = info.value.error
                assert err.kind == 'busy' and err.code == 'slot_busy' and err.retry_after and err.retryable
                assert api.http_status(err) == 429
        finally:
            face._stateless.release()
            face._stateless.release()
        assert (await face.validate(BEAM)).ok

    asyncio.run(scenario())


@pytest.mark.parametrize('exc,kind,code', [
    (AdmissionBusy('x'), 'busy', 'admission_busy'),
    (RevisionConflict('x'), 'conflict', 'revision_conflict'),
    (ArtifactExpired('x'), 'expired', 'artifacts_expired'),
    (KeyError('x'), 'not_found', 'not_found'),
    (ValueError('x'), 'invalid_request', 'invalid_field'),
    (OSError('x'), 'unavailable', 'service_unavailable'),
    (RuntimeError('x'), 'unavailable', 'service_unavailable'),
    (api.BoundsError('json_too_deep'), 'invalid_request', 'json_too_deep'),
])
def test_map_exception_table(exc, kind, code):
    err = map_exception(exc)
    assert (err.kind, err.code) == (kind, code)


def test_map_exception_subclasses_before_bases():
    quota = map_exception(ClientQuotaExceeded('secret', 7))
    assert quota.kind == 'busy' and quota.retry_after == 7
    zero = Position(0, 1, 1)
    mel = make('MEL-S016', Span(zero, zero), field_name='source_id')
    assert isinstance(mel, MelError)
    assert map_exception(mel).kind == 'invalid_source'
    unsupported = map_exception(make('MEL-S011', Span(zero, zero)))
    assert unsupported.kind == 'unsupported' and api.http_status(unsupported) == 422


def test_map_exception_never_leaks_text(caplog):
    secret = 'secret-path /etc/passwd token=abc'
    with caplog.at_level(logging.DEBUG):
        for exc in (RuntimeError(secret), OSError(secret), AdmissionBusy(secret), KeyError(secret),
                    ValueError(secret), RevisionConflict(secret), ArtifactExpired(secret)):
            assert 'secret' not in map_exception(exc).model_dump_json()
    assert 'secret' not in caplog.text and 'passwd' not in caplog.text
    assert any('code=service_unavailable' in r.getMessage() for r in caplog.records)


def test_quota_defaults_are_the_decided_limits():
    assert PROGRAM_QUOTA_DEFAULTS == {'programs': 8, 'staged': 4, 'operations': 32,
                                      'payload_bytes': 2 * 1024 * 1024}
    assert ProgramQuota(None).limits == PROGRAM_QUOTA_DEFAULTS


def test_quota_env_can_tighten_but_never_loosen():
    env = {PROGRAM_QUOTA_ENV['programs']: '3', PROGRAM_QUOTA_ENV['staged']: '999',
           PROGRAM_QUOTA_ENV['operations']: '33', PROGRAM_QUOTA_ENV['payload_bytes']: '1000'}
    limits = ProgramQuota.from_env(None, environ=env).limits
    assert limits == {'programs': 3, 'staged': 4, 'operations': 32, 'payload_bytes': 1000}
    # Fail closed: an unparsable or negative value refuses everything; unset keeps the default.
    bad = ProgramQuota.from_env(None, environ={PROGRAM_QUOTA_ENV['programs']: 'many',
                                               PROGRAM_QUOTA_ENV['staged']: '-1',
                                               PROGRAM_QUOTA_ENV['operations']: ''}).limits
    assert bad['programs'] == 0 and bad['staged'] == 0 and bad['operations'] == 32
    assert ProgramQuota(None, programs=100, staged=-5).limits['programs'] == 8
    assert ProgramQuota(None, staged=-5).limits['staged'] == 0


def test_error_table_has_the_quota_codes():
    for code in ('quota_programs', 'quota_staged', 'quota_operations', 'quota_bytes', 'quota_drafts',
                 'slot_busy', 'requirements_changed', 'review_expired'):
        assert code in api.ERROR_TABLE
    assert len({c for c in api.ERROR_TABLE if c.startswith('quota_')}) == 5


# ------------------------------------------------------------------------------ service harness

_SCHEMA = '''
    CREATE TABLE drafts(id TEXT PRIMARY KEY, record TEXT NOT NULL);
    CREATE TABLE briefs(id TEXT PRIMARY KEY, draft TEXT NOT NULL, revision INTEGER NOT NULL,
        record TEXT NOT NULL, UNIQUE(draft,revision));
    CREATE TABLE proposals(draft TEXT, revision INTEGER, owner TEXT NOT NULL, PRIMARY KEY(draft,revision));
    CREATE TABLE audits(draft TEXT, revision INTEGER, record TEXT NOT NULL, PRIMARY KEY(draft,revision));
    CREATE TABLE jobs(id TEXT PRIMARY KEY, brief TEXT NOT NULL, key TEXT NOT NULL UNIQUE, record TEXT NOT NULL,
        cancel INTEGER NOT NULL DEFAULT 0, deadline REAL NOT NULL, expires REAL, reserved INTEGER NOT NULL,
        FOREIGN KEY(brief) REFERENCES briefs(id));
    CREATE TABLE publications(job TEXT PRIMARY KEY, record TEXT, expires REAL);
    CREATE TABLE draft_owners(draft TEXT PRIMARY KEY, client TEXT NOT NULL, bytes INTEGER NOT NULL,
        updated REAL NOT NULL);
    CREATE TABLE retention(kind TEXT, id TEXT, created REAL NOT NULL, PRIMARY KEY(kind,id));
'''


class HostStore:
    """Non-posix stand-in for SQLiteStore: the same schema subset and transaction() semantics."""

    def __init__(self):
        self._lock = RLock()
        self.db = sqlite3.connect(':memory:', check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(_SCHEMA)

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

    def close(self):
        self.db.close()


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class StubAdapter:
    """A native-mode occt adapter with no kernel: static description, an always-available probe."""
    mode = 'native'
    backend = 'occt'

    def describe(self):
        return make_description('occt', mode='native', adapter_version='stub-0.1.0')

    async def probe(self):
        return ProbeResult(backend='occt', mode='native', available=True)

    def supports(self, plan):
        return check_plan(self.describe(), plan)


def new_quota(store, **kw):
    params = dict(ttl_seconds=TTL, max_drafts=1000, max_draft_bytes=10 ** 9, per_client_drafts=1000,
                  max_audits=10, max_audit_bytes=10 ** 6, max_briefs=1000, per_client_briefs=1000)
    params.update(kw)
    return PreparationQuota(store, **params)


class Light:
    pass


@pytest.fixture
def light(tmp_path):
    if os.name == 'posix':
        from engineering.store import SQLiteStore
        store = SQLiteStore(tmp_path)
    else:
        store = HostStore()
    clock = Clock()
    briefs = BriefService(SQLiteBriefRepository(store))
    publisher = SimpleNamespace(cleanup=lambda: None)
    jobs = SimpleNamespace(store=store, publisher=publisher)
    stub = StubAdapter()
    service = RevisionService(store, jobs, publisher, briefs, adapter_factory=lambda backend, mode='native': stub,
                              clock=clock, verifier=None)
    w = Light()
    w.store, w.clock, w.briefs, w.service = store, clock, briefs, service
    w.quota = new_quota(store)
    w.face = ProgramInterface(service, briefs, w.quota, ProgramQuota(store, clock=clock), clock=clock)
    yield w
    store.close()


def run(coro):
    return asyncio.run(coro)


def new_program(w, client=ALICE):
    return run(w.face.program_create(client=client)).program_id


def stage(w, pid, client=ALICE, source=BEAM, parent=None):
    return run(w.face.stage(source, api.StageRequest(program_id=pid, source_id='beam.mir',
                                                     expected_parent=parent), client=client))


def finalize(w, rev, client=ALICE):
    return run(w.face.review(api.ReviewRequest(
        revision_id=rev.revision_id, brief_sha256=rev.brief_sha256, acknowledged_fields=tuple(REVIEW_FIELDS),
        reviewed_by='Ann'), client=client))


def count(w, sql, *params):
    with w.store.transaction() as db:
        return db.execute(sql, params).fetchone()[0]


# ---------------------------------------------------------------- facade: stage, review, status

def test_stage_review_status_happy_path(light):
    pid = new_program(light)
    staged = stage(light, pid)
    rev = staged.revision
    assert rev.state == 'staged' and rev.result_kind == 'pending' and rev.parent is None
    assert rev.program_id == pid and rev.artifacts == 'none' and rev.is_current is False
    assert 'material.youngs_modulus_mpa' in staged.review_fields
    reviewed = finalize(light, rev)
    assert reviewed.revision.revision_id == rev.revision_id and reviewed.revision.state == 'staged'
    status = run(light.face.status(api.StatusRequest(revision_id=rev.revision_id), client=ALICE))
    assert status.revision == reviewed.revision
    full = light.service.view(rev.revision_id, client=ALICE).revision
    assert light.briefs.is_finalized(full.draft_id, full.draft_revision)
    history = run(light.face.history(api.HistoryRequest(program_id=pid), client=ALICE))
    assert [r.revision_id for r in history.revisions] == [rev.revision_id] and history.head_revision_id is None
    program = run(light.face.program(api.HistoryRequest(program_id=pid), client=ALICE))
    assert program.program_id == pid and program.head_seq == 0 and program.current is None


def test_stage_default_source_id_and_mel_error_writes_nothing(light):
    pid = new_program(light)
    before = count(light, 'SELECT COUNT(*) FROM program_revisions')
    err = fail_of(light.face.stage(b'design not mel', api.StageRequest(program_id=pid), client=ALICE))
    assert err.kind in ('invalid_source', 'unsupported') and err.code.startswith('MEL-')
    err = fail_of(light.face.stage(BEAM, api.StageRequest(program_id=pid, source_id='../x'), client=ALICE))
    assert err.code == 'MEL-S016'
    assert count(light, 'SELECT COUNT(*) FROM program_revisions') == before == 0
    assert DEFAULT_SOURCE_ID == 'program.mir'
    assert stage(light, pid).revision.state == 'staged'


def test_stale_parent_is_a_conflict_and_writes_nothing(light):
    pid = new_program(light)
    err = fail_of(light.face.stage(BEAM, api.StageRequest(program_id=pid, expected_parent='a' * 32),
                                   client=ALICE))
    assert err.kind == 'conflict' and err.code == 'stale_parent' and err.next_action == 'refresh_and_rebase'
    assert count(light, 'SELECT COUNT(*) FROM program_revisions') == 0


def test_review_fills_the_draft_from_the_revision(light):
    pid = new_program(light)
    rev = stage(light, pid).revision
    assert 'draft_id' not in api.ReviewRequest.model_fields and 'draft_revision' not in api.ReviewRequest.model_fields
    with pytest.raises(Exception):
        api.ReviewRequest(revision_id=rev.revision_id, brief_sha256=rev.brief_sha256, draft_id='x' * 32,
                          acknowledged_fields=tuple(REVIEW_FIELDS), reviewed_by='Ann')
    full = light.service.view(rev.revision_id, client=ALICE).revision
    assert count(light, 'SELECT COUNT(*) FROM briefs') == 0
    finalize(light, rev)
    with light.store.transaction() as db:
        row = db.execute('SELECT draft, revision FROM briefs').fetchone()
    assert (row[0], row[1]) == (full.draft_id, full.draft_revision)
    finalize(light, rev)  # idempotent: still one brief
    assert count(light, 'SELECT COUNT(*) FROM briefs') == 1


def test_review_refusals(light):
    pid = new_program(light)
    rev = stage(light, pid).revision

    def attempt(**kw):
        fields = dict(revision_id=rev.revision_id, brief_sha256=rev.brief_sha256,
                      acknowledged_fields=tuple(REVIEW_FIELDS), reviewed_by='Ann')
        fields.update(kw)
        return fail_of(light.face.review(api.ReviewRequest(**fields), client=ALICE))

    err = attempt(brief_sha256='f' * 64)
    assert (err.kind, err.code) == ('refused', 'review_mismatch')
    err = attempt(acknowledged_fields=('length_mm',))
    assert err.kind == 'invalid_request' and err.field == 'acknowledged_fields'
    assert count(light, 'SELECT COUNT(*) FROM briefs') == 0
    err = fail_of(light.face.review(api.ReviewRequest(
        revision_id=rev.revision_id, brief_sha256=rev.brief_sha256, acknowledged_fields=tuple(REVIEW_FIELDS),
        reviewed_by='Ann'), client=BOB))
    assert err.kind == 'not_found'


def test_run_requires_a_finalized_brief_and_takes_no_brief_id(light):
    pid = new_program(light)
    rev = stage(light, pid).revision
    assert 'brief_id' not in api.RunRequest.model_fields
    err = fail_of(light.face.run(api.RunRequest(revision_id=rev.revision_id), client=ALICE))
    assert (err.kind, err.code) == ('refused', 'not_finalized')
    assert light.service.view(rev.revision_id, client=ALICE).revision.state == 'staged'


def test_cancel_a_staged_revision_frees_the_staged_slot(light):
    pid = new_program(light)
    rev = stage(light, pid).revision
    out = run(light.face.cancel(api.CancelRequest(revision_id=rev.revision_id), client=ALICE))
    assert out.revision.state == 'cancelled' and out.revision.result_kind == 'cancelled'


# -------------------------------------------------------------------------------- quotas (D9, K46)

def test_cap_four_staged_revisions_across_programs(light):
    first, second = new_program(light), new_program(light)
    for pid in (first, first, second, second):
        stage(light, pid)
    err = fail_of(light.face.stage(BEAM, api.StageRequest(program_id=first), client=ALICE))
    assert (err.kind, err.code) == ('busy', 'quota_staged') and api.http_status(err) == 429
    assert err.retry_after and err.retryable
    # Another client is not affected.
    other = new_program(light, BOB)
    assert stage(light, other, BOB).revision.state == 'staged'
    # Cancelling one frees a slot.
    rid = light.service.history(first, client=ALICE)[0].revision_id
    run(light.face.cancel(api.CancelRequest(revision_id=rid), client=ALICE))
    assert stage(light, first).revision.state == 'staged'


def test_cap_eight_programs_per_client(light):
    for _ in range(8):
        new_program(light)
    err = fail_of(light.face.program_create(client=ALICE))
    assert (err.kind, err.code) == ('busy', 'quota_programs') and api.http_status(err) == 429
    assert new_program(light, BOB)


def test_cap_32_operations_in_a_rolling_day(light):
    pid = new_program(light)
    for _ in range(32):
        rid = stage(light, pid).revision.revision_id
        run(light.face.cancel(api.CancelRequest(revision_id=rid), client=ALICE))
    err = fail_of(light.face.stage(BEAM, api.StageRequest(program_id=pid), client=ALICE))
    assert (err.kind, err.code) == ('busy', 'quota_operations') and api.http_status(err) == 429
    light.clock.t += 86400 + 1  # the window rolls
    assert stage(light, pid).revision.state == 'staged'


def test_cap_payload_bytes_per_client(light):
    light.face.program_quota = ProgramQuota(light.store, payload_bytes=len(BEAM) + 10, clock=light.clock)
    pid = new_program(light)
    stage(light, pid)
    err = fail_of(light.face.stage(BEAM, api.StageRequest(program_id=pid), client=ALICE))
    assert (err.kind, err.code) == ('busy', 'quota_bytes') and api.http_status(err) == 429
    assert PROGRAM_QUOTA_DEFAULTS['payload_bytes'] == 2 * 1024 * 1024


def test_env_override_tightens_the_live_cap_and_cannot_loosen_it(light):
    tight = ProgramQuota.from_env(light.store, environ={PROGRAM_QUOTA_ENV['programs']: '1'}, clock=light.clock)
    loose = ProgramQuota.from_env(light.store, environ={PROGRAM_QUOTA_ENV['programs']: '500'}, clock=light.clock)
    light.face.program_quota = tight
    new_program(light)
    err = fail_of(light.face.program_create(client=ALICE))
    assert err.code == 'quota_programs'
    light.face.program_quota = loose
    for _ in range(7):
        new_program(light)
    assert fail_of(light.face.program_create(client=ALICE)).code == 'quota_programs'


def test_draft_quota_is_checked_before_staging_and_recorded_after(light):
    light.face.preparation_quota = new_quota(light.store, per_client_drafts=1)
    pid = new_program(light)
    rev = stage(light, pid).revision
    full = light.service.view(rev.revision_id, client=ALICE).revision
    with light.store.transaction() as db:
        owner = db.execute('SELECT client FROM draft_owners WHERE draft=?', (full.draft_id,)).fetchone()
    assert owner[0] == ALICE
    err = fail_of(light.face.stage(BEAM, api.StageRequest(program_id=pid), client=ALICE))
    assert (err.kind, err.code) == ('busy', 'quota_drafts')
    assert count(light, 'SELECT COUNT(*) FROM program_revisions') == 1


# ---------------------------------------------------------------------------- staged expiry (D10)

def test_staged_expiry_frees_the_staged_cap(light):
    pid = new_program(light)
    ids = [stage(light, pid).revision.revision_id for _ in range(4)]
    assert fail_of(light.face.stage(BEAM, api.StageRequest(program_id=pid), client=ALICE)).code == 'quota_staged'
    light.clock.t += TTL + 1
    fresh = stage(light, pid)  # the prelude expires the four old ones first
    assert fresh.revision.state == 'staged'
    for rid in ids:
        rev = light.service.view(rid, client=ALICE).revision
        assert rev.state == 'failed' and rev.reason == 'review_expired'
        status = run(light.face.status(api.StatusRequest(revision_id=rid), client=ALICE)).revision
        assert status.result_kind == 'failed' and status.reason == 'review_expired'
    events = [(e.event, e.detail.get('reason')) for e in light.service.journal(pid, client=ALICE)
              if e.revision_id == ids[0]]
    assert ('failed', 'review_expired') in events


def test_expiry_triggers_on_every_mutating_operation(light):
    pid = new_program(light)
    rid = stage(light, pid).revision.revision_id
    light.clock.t += TTL + 1
    new_program(light)  # program_create is mutating: it runs the prelude
    assert light.service.view(rid, client=ALICE).revision.state == 'failed'


def test_review_of_an_expired_revision_is_review_expired(light):
    pid = new_program(light)
    rev = stage(light, pid).revision
    light.clock.t += TTL + 1
    err = fail_of(light.face.review(api.ReviewRequest(
        revision_id=rev.revision_id, brief_sha256=rev.brief_sha256, acknowledged_fields=tuple(REVIEW_FIELDS),
        reviewed_by='Ann'), client=ALICE))
    assert (err.kind, err.code) == ('expired', 'review_expired') and api.http_status(err) == 410


def test_expire_staged_skips_rows_being_admitted(light):
    pid = new_program(light)
    rid = stage(light, pid).revision.revision_id
    light.clock.t += TTL + 1
    light.service._admitting[rid] += 1
    assert light.service.expire_staged(light.clock(), ttl_seconds=TTL) == 0
    assert light.service.view(rid, client=ALICE).revision.state == 'staged'
    del light.service._admitting[rid]
    assert light.service.expire_staged(light.clock(), ttl_seconds=TTL) == 1
    assert light.service.view(rid, client=ALICE).revision.state == 'failed'
    assert light.service.expire_staged(light.clock(), ttl_seconds=TTL) == 0  # terminal rows are left alone


def test_expire_staged_fails_a_revision_whose_draft_is_tombstoned_or_missing(light):
    pid = new_program(light)
    first = stage(light, pid).revision.revision_id
    second = stage(light, pid).revision.revision_id
    fresh = stage(light, pid).revision.revision_id
    drafts = {rid: light.service.view(rid, client=ALICE).revision.draft_id for rid in (first, second, fresh)}
    with light.store.transaction() as db:
        db.execute('UPDATE drafts SET record=? WHERE id=?', (TOMBSTONE, drafts[first]))
        db.execute('DELETE FROM drafts WHERE id=?', (drafts[second],))
    assert light.service.expire_staged(light.clock(), ttl_seconds=TTL) == 2
    states = {rid: light.service.view(rid, client=ALICE).revision.state for rid in (first, second, fresh)}
    assert states == {first: 'failed', second: 'failed', fresh: 'staged'}


# ------------------------------------------------------------- ids, redaction, foreign == unknown

def test_foreign_client_and_unknown_id_give_the_identical_envelope(light):
    pid = new_program(light)
    rev = stage(light, pid).revision
    finalize(light, rev)
    rid = rev.revision_id
    calls = [
        lambda client, i: light.face.status(api.StatusRequest(revision_id=i), client=client),
        lambda client, i: light.face.inspect(api.InspectRequest(revision_id=i), client=client),
        lambda client, i: light.face.cancel(api.CancelRequest(revision_id=i), client=client),
        lambda client, i: light.face.run(api.RunRequest(revision_id=i), client=client),
        lambda client, i: light.face.review(api.ReviewRequest(
            revision_id=i, brief_sha256='a' * 64, acknowledged_fields=(), reviewed_by='x'), client=client),
        lambda client, i: light.face.restore(api.RestoreRequest(revision_id=i), client=client),
        lambda client, i: light.face.rebuild(api.RebuildRequest(revision_id=i), client=client),
        lambda client, i: light.face.export(api.ExportRequest(revision_id=i, fmt='program-source'), client=client),
        lambda client, i: light.face.diff(api.DiffRequest(revision_id=i, other_revision_id=i), client=client),
        lambda client, i: light.face.history(api.HistoryRequest(program_id=i), client=client),
        lambda client, i: light.face.program(api.HistoryRequest(program_id=i), client=client),
        lambda client, i: light.face.stage(BEAM, api.StageRequest(program_id=i), client=client),
    ]
    own = {pid: 'program', rid: 'revision'}  # the real ids, asked by the wrong client, vs an unknown id
    for call in calls:
        for real in (pid, rid):
            try:
                foreign = fail_of(call(BOB, real))
            except Exception:
                continue  # a call whose model rejects this id kind is skipped (program vs revision ids)
            unknown = fail_of(call(BOB, BAD_ID))
            assert foreign == unknown and foreign.kind == 'not_found', own[real]
    assert fail_of(light.face.status(api.StatusRequest(revision_id=rid), client=BOB)) == fail_of(
        light.face.status(api.StatusRequest(revision_id=BAD_ID), client=ALICE))


def test_no_draft_brief_or_job_id_in_any_response(light):
    pid = new_program(light)
    staged = stage(light, pid)
    rev = staged.revision
    reviewed = finalize(light, rev)
    full = light.service.view(rev.revision_id, client=ALICE).revision
    with light.store.transaction() as db:
        brief_id = db.execute('SELECT id FROM briefs').fetchone()[0]
    light.service.revision_store  # journal rows that carry the bearer ids, as run() writes them
    with light.store.transaction() as db:
        light.service.revision_store.append_journal(db, pid, rev.revision_id, 'admit_intent',
                                                    {'brief_id': brief_id})
        light.service.revision_store.append_journal(db, pid, rev.revision_id, 'linked', {'job_id': 'b' * 32})
    inspected = run(light.face.inspect(api.InspectRequest(revision_id=rev.revision_id), client=ALICE))
    bodies = [staged, reviewed, inspected,
              run(light.face.status(api.StatusRequest(revision_id=rev.revision_id), client=ALICE)),
              run(light.face.history(api.HistoryRequest(program_id=pid), client=ALICE)),
              run(light.face.program(api.HistoryRequest(program_id=pid), client=ALICE)),
              run(light.face.diff(api.DiffRequest(revision_id=rev.revision_id, other_revision_id=rev.revision_id),
                                  client=ALICE))]
    for body in bodies:
        text = body.model_dump_json()
        for needle in ('draft_id', 'brief_id', 'job_id', full.draft_id, brief_id, 'b' * 32):
            assert needle not in text, needle
    events = [(line.event, dict(line.detail)) for line in inspected.journal]
    assert ('admit_intent', {}) in events and ('linked', {}) in events
    for name in api.RESPONSE_MODELS:
        assert not {'draft_id', 'brief_id', 'job_id'} & set(name.model_fields)


def test_inspect_evidence_is_none_without_the_verifier(light):
    pid = new_program(light)
    rev = stage(light, pid).revision
    out = run(light.face.inspect(api.InspectRequest(revision_id=rev.revision_id), client=ALICE))
    assert out.evidence.status == 'none' and [l.event for l in out.journal] == ['staged']


# --------------------------------------------------------------------------- propose guard (D12)

def test_propose_without_a_head_or_a_guard_is_refused(light):
    pid = new_program(light)
    request = api.ProposeRequest(program_id=pid, source_id='beam.mir')
    for guard in ('a' * 64, None):
        err = fail_of(light.face.propose(BEAM, request, client=ALICE, require_requirements_sha256=guard))
        assert (err.kind, err.code) == ('refused', 'requirements_changed')
        assert err.next_action == 'refresh_and_rebase'
    stage_guarded = fail_of(light.face.stage(BEAM, api.StageRequest(
        program_id=pid, require_requirements_sha256='a' * 64), client=ALICE))
    assert stage_guarded.code == 'requirements_changed'
    assert count(light, 'SELECT COUNT(*) FROM program_revisions') == 0


# ------------------------------------------------------------------------------------ the slot

def test_stage_slot_is_non_queuing(light):
    pid = new_program(light)

    async def scenario():
        await light.face._slot.acquire()
        try:
            with pytest.raises(InterfaceFailure) as info:
                await light.face.stage(BEAM, api.StageRequest(program_id=pid), client=ALICE)
            err = info.value.error
            assert (err.kind, err.code) == ('busy', 'slot_busy') and err.retry_after and api.http_status(err) == 429
            with pytest.raises(InterfaceFailure) as info:
                await light.face.rebuild(api.RebuildRequest(revision_id=BAD_ID), client=ALICE)
            assert info.value.error.code in ('slot_busy', 'not_found')
        finally:
            light.face._slot.release()
        return await light.face.stage(BEAM, api.StageRequest(program_id=pid), client=ALICE)

    assert asyncio.run(scenario()).revision.state == 'staged'
    assert count(light, 'SELECT COUNT(*) FROM program_revisions') == 1


def test_compile_runs_off_the_event_loop(light, monkeypatch):
    import engineering.program.transactions as tx
    seen = []
    original = tx.compile_source

    def spy(*args, **kw):
        import threading
        seen.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kw)

    monkeypatch.setattr(tx, 'compile_source', spy)
    pid = new_program(light)
    stage(light, pid)
    assert seen == [False]


# --------------------------------------------------------------------- transactions.source, export

def test_service_source_is_client_checked_like_view(light):
    pid = new_program(light)
    rid = stage(light, pid).revision.revision_id
    payload = light.service.source(rid, client=ALICE)
    assert isinstance(payload, RevisionPayload) and payload.source == BEAM.decode('utf-8')
    for client, revision in ((BOB, rid), (ALICE, BAD_ID), (BOB, BAD_ID)):
        with pytest.raises(KeyError):
            light.service.source(revision, client=client)
    with pytest.raises(KeyError):
        light.service.view(rid, client=BOB)
    light.clock.t += RETENTION + 1
    out = light.service.source(rid, client=ALICE)
    assert isinstance(out, RevisionRefusal) and out.reasons == ('history_expired',)


def test_export_source_formats_and_allowlist(light):
    import hashlib
    pid = new_program(light)
    rid = stage(light, pid).revision.revision_id
    got = run(light.face.export(api.ExportRequest(revision_id=rid, fmt='program-source'), client=ALICE))
    assert got.data == BEAM and got.metadata.sha256 == hashlib.sha256(BEAM).hexdigest()
    assert got.metadata.size_bytes == len(BEAM) and got.metadata.fmt == 'program-source'
    for fmt in ('program-ir', 'execution-lock'):
        meta = run(light.face.export(api.ExportRequest(revision_id=rid, fmt=fmt), client=ALICE))
        assert json.loads(meta.data) and meta.metadata.content_type == 'application/json'
    for fmt in ('../etc/passwd', 'ZIP', 'nope', 'program-source/'):
        assert fail_of(light.face.export(api.ExportRequest(revision_id=rid, fmt=fmt), client=ALICE)
                       ).kind == 'not_found'
    assert fail_of(light.face.export(api.ExportRequest(revision_id=rid, fmt='program-source'),
                                     client=BOB)) == fail_of(
        light.face.export(api.ExportRequest(revision_id=BAD_ID, fmt='program-source'), client=BOB))
    # A staged revision has no published bundle: a bundle format is not found.
    assert fail_of(light.face.export(api.ExportRequest(revision_id=rid, fmt='step'), client=ALICE)
                   ).kind == 'not_found'
    light.clock.t += RETENTION + 1
    err = fail_of(light.face.export(api.ExportRequest(revision_id=rid, fmt='program-source'), client=ALICE))
    assert (err.kind, err.code) == ('expired', 'history_expired') and api.http_status(err) == 410


def test_read_only_operations_do_not_write(light):
    pid = new_program(light)
    rid = stage(light, pid).revision.revision_id
    tables = ('program_revisions', 'program_journal', 'drafts', 'briefs', 'jobs', 'draft_owners')

    def snapshot():
        return tuple(count(light, f'SELECT COUNT(*) FROM {t}') for t in tables)

    before = snapshot()
    for _ in range(5):
        run(light.face.status(api.StatusRequest(revision_id=rid), client=ALICE))
        run(light.face.inspect(api.InspectRequest(revision_id=rid), client=ALICE))
        run(light.face.history(api.HistoryRequest(program_id=pid), client=ALICE))
        run(light.face.diff(api.DiffRequest(revision_id=rid, other_revision_id=rid), client=ALICE))
        run(light.face.export(api.ExportRequest(revision_id=rid, fmt='program-source'), client=ALICE))
    assert snapshot() == before


# ----------------------------------------------------------------- native: run, guard, idempotency

def _native_face(w):
    quota = new_quota(w.store)
    return ProgramInterface(w.svc, w.briefs, quota, ProgramQuota(w.store))


@native_only
def test_native_stage_review_run_status_and_run_twice_is_one_job(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            face = _native_face(w)
            staged = await face.stage(BEAM, api.StageRequest(program_id=w.pid, source_id='beam.mir'),
                                      client=CLIENT)
            rev = staged.revision
            await face.review(api.ReviewRequest(
                revision_id=rev.revision_id, brief_sha256=rev.brief_sha256,
                acknowledged_fields=tuple(REVIEW_FIELDS), reviewed_by='Ann'), client=CLIENT)
            first = await face.run(api.RunRequest(revision_id=rev.revision_id), client=CLIENT)
            second = await face.run(api.RunRequest(revision_id=rev.revision_id), client=CLIENT)
            assert first.revision.state == 'running' and second.revision.revision_id == rev.revision_id
            await w.svc.wait(rev.revision_id)
            done = await face.status(api.StatusRequest(revision_id=rev.revision_id), client=CLIENT)
            assert done.revision.state == 'published' and done.revision.result_kind == 'published'
            assert done.revision.artifacts == 'available' and done.revision.is_current
            third = await face.run(api.RunRequest(revision_id=rev.revision_id), client=CLIENT)
            assert third.revision.state == 'published'
            with w.store.transaction() as db:
                assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
            text = (await face.inspect(api.InspectRequest(revision_id=rev.revision_id), client=CLIENT)
                    ).model_dump_json()
            for needle in ('draft_id', 'brief_id', 'job_id'):
                assert needle not in text
            step = await face.export(api.ExportRequest(revision_id=rev.revision_id, fmt='step'), client=CLIENT)
            assert step.data and step.metadata.size_bytes == len(step.data)
            with pytest.raises(InterfaceFailure) as info:
                await face.export(api.ExportRequest(revision_id=rev.revision_id, fmt='step'), client='other')
            assert info.value.error.kind == 'not_found'
    asyncio.run(scenario())


@native_only
def test_native_propose_guard_against_the_head(tmp_path, evals, occt):
    async def scenario():
        async with world(tmp_path, evals, occt) as w:
            face = _native_face(w)
            first = await face.stage(BEAM, api.StageRequest(program_id=w.pid, source_id='beam.mir'),
                                     client=CLIENT)
            rid = first.revision.revision_id
            await face.review(api.ReviewRequest(
                revision_id=rid, brief_sha256=first.revision.brief_sha256,
                acknowledged_fields=tuple(REVIEW_FIELDS), reviewed_by='Ann'), client=CLIENT)
            await face.run(api.RunRequest(revision_id=rid), client=CLIENT)
            await w.svc.wait(rid)
            head = (await face.status(api.StatusRequest(revision_id=rid), client=CLIENT)).revision
            assert head.state == 'published'
            request = api.ProposeRequest(program_id=w.pid, source_id='beam.mir', expected_parent=rid)
            with pytest.raises(InterfaceFailure) as info:
                await face.propose(BEAM, request, client=CLIENT, require_requirements_sha256='c' * 64)
            assert (info.value.error.kind, info.value.error.code) == ('refused', 'requirements_changed')
            ok = await face.propose(BEAM, request, client=CLIENT,
                                    require_requirements_sha256=head.fixed_requirements_sha256)
            assert ok.revision.state == 'staged' and ok.revision.parent == rid
    asyncio.run(scenario())
