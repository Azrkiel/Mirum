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


# ============================================================================== HTTP transport (task 3)
# api_programs.py over the real app (main.app, lifespan, middleware). fastapi and httpx are imported lazily
# inside the fixture (importorskip), so every test above still runs on a host without them. The app needs
# the posix SQLiteStore, so these run in the CI shards (Linux) and skip on the Windows laptop. No native
# kernel: the adapter is the task 2 StubAdapter and the job controller is spied.

import contextlib  # noqa: E402
import hashlib  # noqa: E402

V1 = '/engineering/v1'
MEL = 'application/vnd.mirum.mel'
JOB_ID = '1' * 32
V1_EXPECTED = {
    ('GET', '/capabilities'), ('POST', '/drafts'), ('GET', '/drafts/{draft_id}'), ('PATCH', '/drafts/{draft_id}'),
    ('POST', '/briefs'), ('GET', '/briefs/{brief_id}'), ('POST', '/jobs'), ('GET', '/jobs/{job_id}'),
    ('POST', '/jobs/{job_id}/cancel'), ('GET', '/jobs/{job_id}/artifacts/{format}'),
}
D5_EXPECTED = {
    ('GET', '/mel/meta'), ('POST', '/mel/validate'), ('POST', '/mel/format'), ('POST', '/mel/plan'),
    ('POST', '/programs'), ('GET', '/programs/{pid}'), ('GET', '/programs/{pid}/history'),
    ('GET', '/programs/{pid}/journal'), ('POST', '/programs/{pid}/revisions'),
    ('GET', '/revisions/{rid}'), ('GET', '/revisions/{rid}/evidence'),
    ('POST', '/revisions/{rid}/review'), ('POST', '/revisions/{rid}/run'), ('POST', '/revisions/{rid}/cancel'),
    ('POST', '/revisions/{rid}/restore'), ('POST', '/revisions/{rid}/rebuild'),
    ('POST', '/revisions/{rid}/compare'), ('GET', '/revisions/{rid}/artifacts/{fmt}'),
}
MEL_CASES = json.loads((F08 / 'mel_cases.json').read_text(encoding='utf-8'))['cases']


def _case_source(case) -> bytes:
    if 'source' in case:
        return case['source'].encode('utf-8')
    text = BEAM.decode('utf-8')
    for old, new in case['edits']:
        text = text.replace(old, new)
    return text.encode('utf-8')


def test_registry_routes_equal_the_d5_list():
    registry = {(r.method, r.path) for op in api.OPERATIONS for r in op.routes}
    assert registry == D5_EXPECTED and len(D5_EXPECTED) == 18


class Http:
    pass


class CountingStub(StubAdapter):
    def __init__(self):
        self.probes = 0
        self.factory_calls = 0

    async def probe(self):
        self.probes += 1
        return await super().probe()


class VerifierSpy:
    """Counts measurements; forwards to the real F07 verifier built by create_program_runtime."""

    def __init__(self, real):
        self.real = real
        self.evidence = real.evidence
        self.calls = 0

    async def verify(self, revision, ir):
        self.calls += 1
        return await self.real.verify(revision, ir)


@contextlib.contextmanager
def _http_app(tmp_path, monkeypatch, flag='1', recover_fails=False):
    pytest.importorskip('fastapi')
    pytest.importorskip('httpx')
    if os.name != 'posix':
        pytest.skip('the app uses the posix SQLiteStore')
    import importlib
    os.environ.setdefault('GEMINI_API_KEY_HEAVY', 'offline-fixture-never-sent')
    main = importlib.import_module('main')
    from fastapi.testclient import TestClient
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'engineering'))
    if flag is None:
        monkeypatch.delenv('MIRUM_ENGINEERING_PROGRAMS', raising=False)
    else:
        monkeypatch.setenv('MIRUM_ENGINEERING_PROGRAMS', flag)
    if recover_fails:
        def boom(self):
            raise RuntimeError('secret-recover-detail')
        monkeypatch.setattr(RevisionService, 'recover', boom)
    monkeypatch.setattr(main.limiter, 'enabled', False)
    # The client identity is a test header: the routes call limiter._key_func exactly like the v1 routes.
    monkeypatch.setattr(main.limiter, '_key_func', lambda request: request.headers.get('x-test-client', ALICE))
    with TestClient(main.app) as client:
        main.app.state.limiter.reset()
        w = Http()
        w.c, w.main, w.face = client, main, main.app.state.programs
        w.engineering = main.app.state.engineering
        w.stub, w.verifier = CountingStub(), None
        w.admits, w.starts = [], []
        if w.face is not None:
            def factory(backend, mode='native'):
                w.stub.factory_calls += 1
                return w.stub
            w.face.service.adapter_factory = factory
            w.verifier = VerifierSpy(w.face.service.verifier)
            w.face.service.verifier = w.verifier
            jobs = w.face.service.jobs

            def admit(brief_id, key, who):
                w.admits.append((key, who))
                return SimpleNamespace(job=SimpleNamespace(job_id=JOB_ID), owner=None)

            async def wait(job_id):
                await asyncio.Event().wait()
            monkeypatch.setattr(jobs, 'admit', admit)
            monkeypatch.setattr(jobs, 'start', lambda admitted: w.starts.append(1))
            monkeypatch.setattr(jobs, 'wait', wait)
            monkeypatch.setattr(jobs, 'cancel', lambda job_id: None)
        yield w


@pytest.fixture
def http(tmp_path, monkeypatch):
    with _http_app(tmp_path, monkeypatch) as w:
        yield w


def hdr(who=ALICE, **extra):
    return {'x-test-client': who, **extra}


def post_mel(w, path, body=BEAM, who=ALICE, **extra):
    return w.c.post(V1 + path, content=body, headers=hdr(who, **{'Content-Type': MEL}, **extra))


def make_program(w, who=ALICE):
    response = w.c.post(V1 + '/programs', headers=hdr(who))
    assert response.status_code == 201, response.text
    return response.json()['program_id']


def stage_http(w, pid, who=ALICE, **extra):
    extra.setdefault('X-Mirum-Source-Id', 'beam.mir')
    response = post_mel(w, f'/programs/{pid}/revisions', BEAM, who, **extra)
    assert response.status_code == 201, response.text
    return response.json()['revision']


def review_body(rev):
    return {'brief_sha256': rev['brief_sha256'], 'acknowledged_fields': list(REVIEW_FIELDS), 'reviewed_by': 'Ann'}


def review_http(w, rev, who=ALICE):
    response = w.c.post(f"{V1}/revisions/{rev['revision_id']}/review", json=review_body(rev), headers=hdr(who))
    assert response.status_code == 200, response.text
    return response.json()['revision']


def fill(path, pid='0' * 32, rid='0' * 32, fmt='zip'):
    return path.replace('{pid}', pid).replace('{rid}', rid).replace('{fmt}', fmt)


def db_counts(w):
    with w.face.service.store.transaction() as db:
        return tuple(db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                     for t in ('programs', 'program_revisions', 'program_journal', 'drafts', 'briefs', 'jobs'))


# ------------------------------------------------------------------------------------- routes, auth

def test_http_route_snapshot(http):
    routes = {}
    for route in http.main.app.routes:
        path, methods = getattr(route, 'path', ''), getattr(route, 'methods', None)
        if path.startswith(V1) and methods:
            for method in methods:
                if method not in ('HEAD', 'OPTIONS'):
                    routes[(method, path[len(V1):])] = route
    found = set(routes)
    assert V1_EXPECTED <= found, 'the existing /engineering/v1 route set must be unchanged'
    assert found - V1_EXPECTED == D5_EXPECTED
    assert found == V1_EXPECTED | D5_EXPECTED


def test_http_401_without_the_access_token_on_every_new_route(http, monkeypatch):
    pid = make_program(http)
    monkeypatch.setenv('MIRUM_ACCESS_TOKEN', 'fixture-token')
    for method, template in sorted(D5_EXPECTED):
        url = V1 + fill(template, pid, pid)
        denied = http.c.request(method, url)
        assert denied.status_code == 401, (method, template)
        assert denied.json() == {'detail': 'Access token required.'}
        wrong = http.c.request(method, url, headers={'X-Mirum-Access-Token': 'nope'})
        assert wrong.status_code == 401, (method, template)
    allowed = http.c.get(V1 + '/mel/meta', headers={'X-Mirum-Access-Token': 'fixture-token'})
    assert allowed.status_code == 200 and allowed.json()['media_type'] == MEL


def test_http_meta_is_versioned(http):
    body = http.c.get(V1 + '/mel/meta', headers=hdr()).json()
    assert body['api_version'] == 'mirum_program_api_v0_1' and body['max_source_bytes'] == 65536
    assert http.c.get(V1 + '/mel/meta', headers=hdr()).headers['cache-control'] == 'no-store'


# ------------------------------------------------------------------------------------ flag and wiring

def test_http_flag_off_returns_503_everywhere_and_creates_no_tables(tmp_path, monkeypatch):
    with _http_app(tmp_path, monkeypatch, flag=None) as w:
        assert w.main.app.state.programs is None and w.face is None
        for method, template in sorted(D5_EXPECTED):
            kwargs = {'content': BEAM, 'headers': {'Content-Type': MEL}} if method == 'POST' else {}
            response = w.c.request(method, V1 + fill(template), **kwargs)
            assert response.status_code == 503, (method, template)
            body = response.json()
            assert (body['kind'], body['code'], body['next_action']) == (
                'programs_disabled', 'programs_disabled', 'contact_operator')
            assert response.headers['cache-control'] == 'no-store'
        tables = w.engineering.store.db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'program%'").fetchall()
        assert tables == []
        assert w.c.get(V1 + '/capabilities').status_code == 200


@pytest.mark.parametrize('flag', ['0', 'off', ''])
def test_http_flag_values_other_than_on_stay_off(tmp_path, monkeypatch, flag):
    with _http_app(tmp_path, monkeypatch, flag=flag) as w:
        assert w.main.app.state.programs is None
        assert w.c.get(V1 + '/mel/meta').status_code == 503


def test_http_construction_failure_leaves_v1_routes_working(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        with _http_app(tmp_path, monkeypatch, recover_fails=True) as w:
            assert w.main.app.state.programs is None
            assert w.c.get(V1 + '/mel/meta').json()['code'] == 'programs_disabled'
            assert w.c.get(V1 + '/capabilities').status_code == 200
    assert 'construction_failed' in caplog.text and 'secret-recover-detail' not in caplog.text


def test_create_program_runtime_off_builds_nothing(monkeypatch):
    pytest.importorskip('fastapi')
    from engineering.api_programs import create_program_runtime, programs_enabled
    monkeypatch.delenv('MIRUM_ENGINEERING_PROGRAMS', raising=False)
    assert programs_enabled() is False
    exploding = SimpleNamespace()  # any attribute access would raise AttributeError
    assert create_program_runtime(exploding) is None
    assert create_program_runtime(None) is None


def test_http_runtime_is_built_over_the_apis_own_services(http):
    face, engineering = http.face, http.engineering
    assert isinstance(face, ProgramInterface)
    assert face.service.store is engineering.store and face.service.jobs is engineering.jobs
    assert face.service.publisher is engineering.publisher and face.briefs is engineering.briefs
    assert face.preparation_quota is engineering.quota and isinstance(face.program_quota, ProgramQuota)
    assert type(http.verifier.real).__name__ == 'DeliveredVerifier'  # the default F07 verifier


# ---------------------------------------------------------------------------------- MEL bodies and caps

def test_http_validate_format_plan_on_the_beam(http):
    ok = post_mel(http, '/mel/validate', BEAM, **{'X-Mirum-Source-Id': 'beam.mir'})
    assert ok.status_code == 200 and ok.json()['ok'] is True and ok.json()['source_id'] == 'beam.mir'
    assert ok.json() == json.loads(offline.validate_source(BEAM, 'beam.mir').model_dump_json())
    fmt = post_mel(http, '/mel/format')
    assert fmt.status_code == 200 and fmt.json()['formatted_source']
    plan = post_mel(http, '/mel/plan')
    assert plan.status_code == 200 and plan.json()['mode'] == 'native' and plan.json()['candidate_count'] >= 1


def test_http_413_at_64_kib_plus_one_and_exactly_64_kib_reaches_the_compiler(http, monkeypatch):
    seen = []
    original = offline.compile_source

    def spy(source, **kwargs):
        seen.append(len(source))
        return original(source, **kwargs)
    monkeypatch.setattr(offline, 'compile_source', spy)
    too_big = post_mel(http, '/mel/validate', b' ' * 65537)
    assert too_big.status_code == 413 and seen == []
    body = too_big.json()
    assert (body['kind'], body['code']) == ('too_large', 'body_too_large')
    assert too_big.headers['cache-control'] == 'no-store'
    exact = post_mel(http, '/mel/validate', b' ' * 65536)
    assert exact.status_code == 422 and seen == [65536]
    assert exact.json()['kind'] == 'invalid_source' and exact.json()['code'] != 'MEL-L022'
    for path in ('/mel/format', '/mel/plan'):
        assert post_mel(http, path, b' ' * 65537).status_code == 413
    pid = make_program(http)
    assert post_mel(http, f'/programs/{pid}/revisions', b' ' * 65537).status_code == 413


@pytest.mark.parametrize('source,code', [
    (b'\xff\xfe', 'MEL-L001'), (b'\xef\xbb\xbfdesign x {}', 'MEL-L002'), (b'a\x00b', 'MEL-L003')])
def test_http_invalid_utf8_bom_and_nul_are_mel_diagnostics(http, source, code):
    for path in ('/mel/validate', '/mel/plan'):
        response = post_mel(http, path, source)
        assert response.status_code == 422, path
        body = response.json()
        assert (body['kind'], body['code'], body['category']) == ('invalid_source', code, 'lexical')
        assert body['changed'] == 'nothing_written' and body['next_action'] == 'fix_source'


@pytest.mark.parametrize('case', MEL_CASES, ids=[c['id'] for c in MEL_CASES])
def test_http_mel_error_corpus(http, case):
    response = post_mel(http, '/mel/validate', _case_source(case), **{'X-Mirum-Source-Id': case['source_id']})
    assert response.status_code == 422
    body = response.json()
    assert (body['kind'], body['code']) == (case['kind'], case['code'])
    span = body['span']
    assert [span['line'], span['column'], span['end_line'], span['end_column']] == case['span']
    assert body['field'] == case['field']
    assert case['source_id'] != '../x' or '../x' not in response.text


def test_http_traversal_source_id_is_s016_and_never_opened(http):
    for path in ('/mel/validate', '/mel/format', '/mel/plan'):
        response = post_mel(http, path, BEAM, **{'X-Mirum-Source-Id': '../x'})
        assert response.status_code == 422 and response.json()['code'] == 'MEL-S016'
        assert '../x' not in response.text
    pid = make_program(http)
    staged = post_mel(http, f'/programs/{pid}/revisions', BEAM, **{'X-Mirum-Source-Id': '../x'})
    assert staged.status_code == 422 and staged.json()['code'] == 'MEL-S016'
    assert db_counts(http)[1] == 0


def test_http_header_and_media_type_validation(http):
    pid = make_program(http)
    long_id = post_mel(http, '/mel/validate', BEAM, **{'X-Mirum-Source-Id': 'a' * 129})
    assert long_id.status_code == 413 and long_id.json()['code'] == 'header_too_large'
    bad_parent = post_mel(http, f'/programs/{pid}/revisions', BEAM, **{'X-Mirum-Expected-Parent': 'zz'})
    assert bad_parent.status_code == 422 and bad_parent.json()['code'] == 'invalid_header'
    bad_guard = post_mel(http, f'/programs/{pid}/revisions', BEAM, **{'X-Mirum-Require-Requirements': 'abc'})
    assert bad_guard.status_code == 422 and bad_guard.json()['code'] == 'invalid_header'
    wrong = http.c.post(V1 + '/mel/validate', content=BEAM, headers=hdr(**{'Content-Type': 'text/plain'}))
    assert wrong.status_code == 422 and wrong.json()['code'] == 'wrong_media_type'
    assert db_counts(http)[1] == 0


def test_http_small_json_bodies_are_bounded(http):
    pid = make_program(http)
    rev = stage_http(http, pid)
    url = f"{V1}/revisions/{rev['revision_id']}/review"
    assert http.c.post(url, content=b'{not json', headers=hdr(**{'Content-Type': 'application/json'})
                       ).json()['code'] == 'malformed_json'
    assert http.c.post(url, content=b'[]', headers=hdr(**{'Content-Type': 'application/json'})
                       ).json()['code'] == 'malformed_json'
    assert http.c.post(url, content=b'{"a":' * 12 + b'1' + b'}' * 12,
                       headers=hdr(**{'Content-Type': 'application/json'})).json()['code'] == 'json_too_deep'
    big = http.c.post(url, content=b'{"reviewed_by":"' + b'x' * 65536 + b'"}',
                      headers=hdr(**{'Content-Type': 'application/json'}))
    assert big.status_code == 413 and big.json()['kind'] == 'too_large'
    with_id = http.c.post(url, json=dict(review_body(rev), revision_id=rev['revision_id']), headers=hdr())
    assert with_id.status_code == 422 and with_id.json()['field'] == 'revision_id'
    bad = http.c.post(url, json={'brief_sha256': 'nope', 'acknowledged_fields': [], 'reviewed_by': 'x'},
                      headers=hdr())
    assert bad.status_code == 422 and bad.json()['code'] == 'invalid_field'
    assert http.face.service.view(rev['revision_id'], client=ALICE).revision.state == 'staged'


# -------------------------------------------------------------------------- stage, review, run, polls

def test_http_stage_review_run_status_flow(http):
    pid = make_program(http)
    program = http.c.get(f'{V1}/programs/{pid}', headers=hdr()).json()
    assert program['program_id'] == pid and program['head_revision_id'] is None and program['current'] is None
    staged = http.c.post(f'{V1}/programs/{pid}/revisions', content=BEAM,
                         headers=hdr(**{'Content-Type': MEL, 'X-Mirum-Source-Id': 'beam.mir'}))
    assert staged.status_code == 201 and staged.headers['cache-control'] == 'no-store'
    body = staged.json()
    rev = body['revision']
    assert rev['state'] == 'staged' and rev['result_kind'] == 'pending' and body['review_fields']
    reviewed = review_http(http, rev)
    assert reviewed['state'] == 'staged'
    run_now = http.c.post(f"{V1}/revisions/{rev['revision_id']}/run", headers=hdr())
    assert run_now.status_code == 200 and run_now.json()['revision']['state'] == 'running'
    status = http.c.get(f"{V1}/revisions/{rev['revision_id']}", headers=hdr())
    assert status.status_code == 200 and status.json()['revision']['revision_id'] == rev['revision_id']
    history = http.c.get(f'{V1}/programs/{pid}/history', headers=hdr()).json()
    assert [r['revision_id'] for r in history['revisions']] == [rev['revision_id']]


def test_http_run_without_review_is_refused_409(http):
    rev = stage_http(http, make_program(http))
    response = http.c.post(f"{V1}/revisions/{rev['revision_id']}/run", headers=hdr())
    assert response.status_code == 409
    body = response.json()
    assert (body['kind'], body['code'], body['next_action']) == ('refused', 'not_finalized', 're_review')
    assert http.admits == []


def test_http_same_revision_run_twice_admits_one_job(http):
    rev = stage_http(http, make_program(http))
    review_http(http, rev)
    url = f"{V1}/revisions/{rev['revision_id']}/run"
    first, second = http.c.post(url, headers=hdr()), http.c.post(url, headers=hdr())
    assert first.status_code == second.status_code == 200
    assert first.json()['revision']['state'] == second.json()['revision']['state'] == 'running'
    assert http.admits == [('program-revision:' + rev['revision_id'], ALICE)] and http.starts == [1]


def test_http_stage_with_requirements_guard_header(http):
    pid = make_program(http)
    refused = post_mel(http, f'/programs/{pid}/revisions', BEAM, **{'X-Mirum-Require-Requirements': 'a' * 64})
    assert refused.status_code == 409
    assert (refused.json()['kind'], refused.json()['code']) == ('refused', 'requirements_changed')
    assert db_counts(http)[1] == 0


def test_http_cancel_and_compare_and_export(http):
    pid = make_program(http)
    rev = stage_http(http, pid)
    rid = rev['revision_id']
    diff = http.c.post(f'{V1}/revisions/{rid}/compare', json={'other_revision_id': rid}, headers=hdr())
    assert diff.status_code == 200 and 'diff' in diff.json()
    got = http.c.get(f'{V1}/revisions/{rid}/artifacts/program-source', headers=hdr())
    assert got.status_code == 200 and got.content == BEAM
    assert got.headers['x-content-sha256'] == hashlib.sha256(BEAM).hexdigest()
    assert 'filename="program.mir"' in got.headers['content-disposition']
    assert got.headers['cache-control'] == 'no-store' and got.headers['content-type'].startswith(MEL)
    cancelled = http.c.post(f'{V1}/revisions/{rid}/cancel', headers=hdr())
    assert cancelled.status_code == 200 and cancelled.json()['revision']['state'] == 'cancelled'


def test_http_export_unknown_format_is_404(http):
    rid = stage_http(http, make_program(http))['revision_id']
    odd = http.c.get(f'{V1}/revisions/{rid}/artifacts/nope', headers=hdr())
    assert odd.status_code == 404 and odd.json()['kind'] == 'not_found'
    assert http.c.get(f'{V1}/revisions/{rid}/artifacts/..%2Fx', headers=hdr()).status_code == 404
    assert http.c.get(f'{V1}/revisions/{rid}/artifacts/..%2F..%2Fetc%2Fpasswd', headers=hdr()).status_code == 404
    assert http.c.get(f'{V1}/revisions/{rid}/artifacts/' + 'a' * 100, headers=hdr()).status_code == 404
    # A bundle format of a staged revision has nothing to serve.
    assert http.c.get(f'{V1}/revisions/{rid}/artifacts/step', headers=hdr()).status_code == 404


def test_http_50_polls_never_admit_probe_build_or_measure(http):
    pid = make_program(http)
    rev = stage_http(http, pid)
    rid = rev['revision_id']
    review_http(http, rev)
    before = (db_counts(http), len(http.admits), len(http.starts), http.stub.probes, http.stub.factory_calls,
              http.verifier.calls)
    assert http.stub.probes > 0  # staging did probe: the counters are live
    calls = [
        lambda: http.c.get(f'{V1}/revisions/{rid}', headers=hdr()),
        lambda: http.c.get(f'{V1}/revisions/{rid}/evidence', headers=hdr()),
        lambda: http.c.get(f'{V1}/programs/{pid}/journal', headers=hdr()),
        lambda: http.c.get(f'{V1}/programs/{pid}/history', headers=hdr()),
        lambda: http.c.get(f'{V1}/programs/{pid}', headers=hdr()),
        lambda: http.c.get(f'{V1}/revisions/{rid}/artifacts/program-source', headers=hdr()),
        lambda: http.c.post(f'{V1}/revisions/{rid}/compare', json={'other_revision_id': rid}, headers=hdr()),
    ]
    for _ in range(50):
        for call in calls:
            assert call().status_code == 200
    after = (db_counts(http), len(http.admits), len(http.starts), http.stub.probes, http.stub.factory_calls,
             http.verifier.calls)
    assert after == before


# ------------------------------------------------------------------------------ foreign client, ids

def test_http_foreign_client_is_404_identical_to_an_unknown_id_on_every_route(http):
    pid = make_program(http)
    rev = stage_http(http, pid)
    rid = rev['revision_id']
    review_http(http, rev)
    bad = '0' * 32
    review = {'brief_sha256': 'a' * 64, 'acknowledged_fields': [], 'reviewed_by': 'x'}
    jsonh = hdr('bob', **{'Content-Type': 'application/json'})
    cases = [
        ('GET', '/programs/{pid}', {}), ('GET', '/programs/{pid}/history', {}),
        ('GET', '/programs/{pid}/journal', {}), ('POST', '/programs/{pid}/revisions', {'content': BEAM}),
        ('GET', '/revisions/{rid}', {}), ('GET', '/revisions/{rid}/evidence', {}),
        ('POST', '/revisions/{rid}/review', {'json': review}), ('POST', '/revisions/{rid}/run', {}),
        ('POST', '/revisions/{rid}/cancel', {}), ('POST', '/revisions/{rid}/restore', {}),
        ('POST', '/revisions/{rid}/rebuild', {}),
        ('POST', '/revisions/{rid}/compare', {'json': {'other_revision_id': rid}}),
        ('GET', '/revisions/{rid}/artifacts/program-source', {}),
    ]
    assert len(cases) == 13
    for method, template, kwargs in cases:
        kwargs = dict(kwargs)
        headers = dict(jsonh)
        if 'content' in kwargs:
            headers['Content-Type'] = MEL
        foreign = http.c.request(method, V1 + fill(template, pid, rid, 'program-source'), headers=headers, **kwargs)
        unknown = http.c.request(method, V1 + fill(template, bad, bad, 'program-source'), headers=headers, **kwargs)
        assert foreign.status_code == unknown.status_code == 404, (method, template)
        assert foreign.json() == unknown.json() and foreign.json()['kind'] == 'not_found', (method, template)
        assert pid not in foreign.text and rid not in foreign.text
    # A foreign client changed nothing.
    assert http.face.service.view(rid, client=ALICE).revision.state == 'staged'
    assert http.admits == []


def test_http_malformed_ids_are_not_found(http):
    for template in ('/revisions/{rid}', '/programs/{pid}/history', '/revisions/{rid}/evidence'):
        response = http.c.get(V1 + fill(template, 'not-an-id', 'not-an-id'), headers=hdr())
        assert response.status_code == 404 and response.json()['kind'] == 'not_found'


def test_http_no_draft_brief_or_job_id_in_any_response(http):
    pid = make_program(http)
    rev = stage_http(http, pid)
    rid = rev['revision_id']
    reviewed = review_http(http, rev)
    full = http.face.service.view(rid, client=ALICE).revision
    http.c.post(f'{V1}/revisions/{rid}/run', headers=hdr())
    with http.face.service.store.transaction() as db:
        brief_id = db.execute('SELECT id FROM briefs').fetchone()[0]
    bodies = [
        http.c.post(f'{V1}/programs', headers=hdr()),
        http.c.get(f'{V1}/programs/{pid}', headers=hdr()), http.c.get(f'{V1}/programs/{pid}/history', headers=hdr()),
        http.c.get(f'{V1}/programs/{pid}/journal', headers=hdr()),
        http.c.get(f'{V1}/revisions/{rid}', headers=hdr()), http.c.get(f'{V1}/revisions/{rid}/evidence', headers=hdr()),
        http.c.post(f'{V1}/revisions/{rid}/compare', json={'other_revision_id': rid}, headers=hdr()),
        http.c.post(f'{V1}/revisions/{rid}/run', headers=hdr()),
        http.c.get(f'{V1}/revisions/{rid}/artifacts/program-ir', headers=hdr()),
        http.c.get(f'{V1}/revisions/{rid}/artifacts/execution-lock', headers=hdr()),
    ]
    texts = [json.dumps(reviewed)] + [b.text for b in bodies]
    journal = bodies[3].json()['journal']
    assert [line['event'] for line in journal][:1] == ['staged'] and any(l['event'] == 'linked' for l in journal)
    for text in texts:
        for needle in ('draft_id', 'brief_id', 'job_id', full.draft_id, brief_id, JOB_ID):
            assert needle not in text, needle


def test_http_request_log_masks_program_revision_and_mel_ids(http, caplog):
    pid = make_program(http)
    rid = stage_http(http, pid)['revision_id']
    with caplog.at_level(logging.INFO, logger='mirum.api'):
        assert http.c.get(f'{V1}/programs/{pid}/history', headers=hdr()).status_code == 200
        assert http.c.get(f'{V1}/revisions/{rid}', headers=hdr()).status_code == 200
        assert http.c.get(f'{V1}/revisions/{rid}/artifacts/program-source', headers=hdr()).status_code == 200
        assert http.c.get(f'{V1}/revisions/{rid}/artifacts/nope', headers=hdr()).status_code == 404
        assert post_mel(http, '/mel/validate').status_code == 200
        http.c.get(f'{V1}/revisions/{"f" * 32}/artifacts/zip', headers=hdr())
    assert pid not in caplog.text and rid not in caplog.text and 'f' * 32 not in caplog.text
    assert '/engineering/v1/programs/{pid}/history' in caplog.text
    assert '/engineering/v1/revisions/{rid}' in caplog.text and '/engineering/v1/mel/validate' in caplog.text


def test_uvicorn_access_log_mask_covers_programs_revisions_and_mel(http):
    main = http.main
    secret = 'd' * 32
    for kind in ('programs', 'revisions', 'mel'):
        record = logging.LogRecord('uvicorn.access', logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                   ('1.2.3.4:5', 'GET', f'{V1}/{kind}/{secret}/history?x=1', '1.1', 200), None)
        assert main._MaskEngineeringIds().filter(record) is True
        assert secret not in record.getMessage() and f'{V1}/{kind}/{{id}}/history?x=1' in record.getMessage()


# ------------------------------------------------------------------------------- quotas and slots (429)

def tighten(w, monkeypatch, **limits):
    for name, value in limits.items():
        monkeypatch.setenv(PROGRAM_QUOTA_ENV[name], str(value))
    w.face.program_quota = ProgramQuota.from_env(w.face.service.store)


def assert_busy(response, code):
    assert response.status_code == 429, response.text
    body = response.json()
    assert (body['kind'], body['code'], body['retryable']) == ('busy', code, True)
    assert response.headers['retry-after'] == str(body['retry_after'])
    assert body['changed'] == 'nothing_written' and body['next_action'] == 'retry_later'


def test_http_429_program_quota(http, monkeypatch):
    tighten(http, monkeypatch, programs=1)
    make_program(http)
    assert_busy(http.c.post(V1 + '/programs', headers=hdr()), 'quota_programs')
    assert http.c.post(V1 + '/programs', headers=hdr('bob')).status_code == 201  # per client
    assert db_counts(http)[0] == 2


def test_http_429_staged_quota(http, monkeypatch):
    tighten(http, monkeypatch, staged=1)
    pid = make_program(http)
    stage_http(http, pid)
    assert_busy(post_mel(http, f'/programs/{pid}/revisions'), 'quota_staged')
    assert db_counts(http)[1] == 1


def test_http_429_operations_quota(http, monkeypatch):
    tighten(http, monkeypatch, operations=1)
    pid = make_program(http)
    rid = stage_http(http, pid)['revision_id']
    assert http.c.post(f'{V1}/revisions/{rid}/cancel', headers=hdr()).status_code == 200
    assert_busy(post_mel(http, f'/programs/{pid}/revisions'), 'quota_operations')


def test_http_429_payload_bytes_quota(http, monkeypatch):
    tighten(http, monkeypatch, payload_bytes=len(BEAM) + 1)
    pid = make_program(http)
    stage_http(http, pid)
    assert_busy(post_mel(http, f'/programs/{pid}/revisions'), 'quota_bytes')


def test_http_quota_env_cannot_loosen(http, monkeypatch):
    tighten(http, monkeypatch, programs=999)
    assert http.face.program_quota.limits['programs'] == PROGRAM_QUOTA_DEFAULTS['programs'] == 8


def test_http_429_stage_slot_and_stateless_semaphore(http):
    pid = make_program(http)
    face = http.face
    asyncio.run(face._slot.acquire())
    try:
        busy = post_mel(http, f'/programs/{pid}/revisions')
        assert_busy(busy, 'slot_busy')
        assert busy.headers['retry-after'] == '2'
        rebuild = http.c.post(f'{V1}/revisions/{"0" * 32}/rebuild', headers=hdr())
        assert rebuild.status_code in (404, 429)
    finally:
        face._slot.release()
    assert db_counts(http)[1] == 0
    assert post_mel(http, f'/programs/{pid}/revisions').status_code == 201
    asyncio.run(face._stateless.acquire())
    asyncio.run(face._stateless.acquire())
    try:
        for path in ('/mel/validate', '/mel/format', '/mel/plan'):
            assert_busy(post_mel(http, path), 'slot_busy')
        assert http.c.get(V1 + '/mel/meta', headers=hdr()).status_code == 200  # meta needs no slot
    finally:
        face._stateless.release()
        face._stateless.release()
    assert post_mel(http, '/mel/validate').status_code == 200
