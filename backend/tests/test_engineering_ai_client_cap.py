"""Per-client rolling-24 h cap on AI draft requests. Offline.

Host-runnable: the cap class against a sqlite3 stand-in for the store (it only uses ``store.transaction()``).
CI only (posix SQLiteStore, fastapi): restart on a real volume and the HTTP route tests.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from engineering import proposal_contract as pc
from engineering.store import (AI_CLIENT_CAP_ENV, AI_CLIENT_CAP_REASON, ClientAiRequestCap, SQLiteStore,
                               parse_ai_client_cap)

try:  # registers the repo's offline_models fixture where fastapi exists (CI); a no-op on the host
    from backend.tests.test_native_api_hardening_ports import offline_models  # noqa: F401
except ImportError:
    pass

posix = pytest.mark.skipif(os.name != 'posix', reason='SQLiteStore is Linux-only (runs in CI)')
DAY = 86400.0
REGEN_PROMPT = ('Regeneratively cooled rocket thrust chamber with cooling channels: '
                'chamber radius 36 mm, throat radius 20 mm, 30 channels.')


class FakeStore:
    """Same ``transaction()`` contract as SQLiteStore, with the cap table, on a shared sqlite file."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.RLock()
        self.db.execute('CREATE TABLE IF NOT EXISTS ai_client_requests(client TEXT NOT NULL, created REAL NOT NULL)')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def rows(self):
        return self.db.execute('SELECT client,created FROM ai_client_requests').fetchall()


@pytest.fixture
def store(tmp_path):
    return FakeStore(str(tmp_path / 'cap.sqlite3'))


def test_unset_or_empty_disables_the_cap_and_writes_nothing(store, monkeypatch):
    for raw in (None, '', '   '):
        assert parse_ai_client_cap(raw) is None
    monkeypatch.delenv(AI_CLIENT_CAP_ENV, raising=False)
    cap = ClientAiRequestCap.from_env(store)
    assert cap.limit is None
    assert all(cap.admit('a') for _ in range(50))
    assert store.rows() == []


def test_cap_n_admits_n_then_refuses_the_next(store):
    cap = ClientAiRequestCap(store, 3)
    assert [cap.admit('a', now=1000.0 + i) for i in range(3)] == [True] * 3
    assert cap.admit('a', now=1010.0) is False
    assert len(store.rows()) == 3  # the refusal wrote nothing


def test_window_expiry_readmits_and_prunes(store):
    cap = ClientAiRequestCap(store, 1)
    assert cap.admit('a', now=1000.0)
    assert not cap.admit('a', now=1000.0 + DAY - 1)
    assert cap.admit('a', now=1000.0 + DAY)
    assert len(store.rows()) == 1  # the old row was pruned


def test_survives_a_new_instance_on_the_same_database(tmp_path):
    path = str(tmp_path / 'cap.sqlite3')
    assert ClientAiRequestCap(FakeStore(path), 1).admit('a')
    assert not ClientAiRequestCap(FakeStore(path), 1).admit('a')


def test_zero_refuses_everyone_and_writes_nothing(store):
    cap = ClientAiRequestCap(store, 0)
    assert not cap.admit('a') and not cap.admit('b')
    assert store.rows() == []


@pytest.mark.parametrize('raw,expected', [('5', 5), (' 7 ', 7), ('0', 0), ('-1', 0), ('abc', 0), ('1.5', 0),
                                          ('+3', 0), ('1e3', 0), ('99999999999999', 0),
                                          (chr(0x0665), 0)])
def test_env_parsing_fails_closed(raw, expected):
    assert parse_ai_client_cap(raw) == expected


def test_invalid_env_means_cap_zero(store, monkeypatch):
    monkeypatch.setenv(AI_CLIENT_CAP_ENV, '-4')
    cap = ClientAiRequestCap.from_env(store)
    assert cap.limit == 0 and not cap.admit('a')


def test_clients_are_independent(store):
    cap = ClientAiRequestCap(store, 1)
    assert cap.admit('a') and cap.admit('b')
    assert not cap.admit('a') and not cap.admit('b')


def test_threads_never_admit_more_than_the_cap(store):
    cap = ClientAiRequestCap(store, 5)
    results, gate = [], threading.Barrier(16)

    def worker():
        gate.wait()
        results.append(cap.admit('a'))
    threads = [threading.Thread(target=worker) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 5 and len(store.rows()) == 5


def test_rows_hold_client_and_timestamp_only(store):
    ClientAiRequestCap(store, 2).admit('client-x', now=5.0)
    assert store.rows() == [('client-x', 5.0)]


@posix
def test_real_store_has_the_table_and_the_cap_survives_restart(tmp_path):
    first = SQLiteStore(tmp_path)
    assert ClientAiRequestCap(first, 1).admit('a')
    first.close()
    second = SQLiteStore(tmp_path)  # restart on the same volume
    assert not ClientAiRequestCap(second, 1).admit('a')
    second.close()


# --- CI only: HTTP --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _engineering_key_only(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY_PRO', raising=False)


def _client(request, monkeypatch, tmp_path, flag):
    pytest.importorskip('fastapi.testclient')
    from fastapi.testclient import TestClient
    from backend.tests.test_native_api_hardening_ports import main
    request.getfixturevalue('offline_models')
    if flag:
        import config
        monkeypatch.setattr(config, 'ENGINEERING_AI_DRAFTS', True)
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'engineering'))
    return TestClient, main


@pytest.fixture
def ai_client(request, tmp_path, monkeypatch):
    TestClient, main = _client(request, monkeypatch, tmp_path, True)
    with TestClient(main.app) as c:
        main.app.state.limiter.reset()
        yield c


@pytest.fixture
def off_client(request, tmp_path, monkeypatch):
    TestClient, main = _client(request, monkeypatch, tmp_path, False)
    with TestClient(main.app) as c:
        main.app.state.limiter.reset()
        yield c


def runtime():
    from backend.tests.test_native_api_hardening_ports import main
    return main.app.state.engineering


def counts(value):
    with value.store.transaction() as db:
        return tuple(db.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]
                     for t in ('drafts', 'audits', 'generator_spend', 'ai_client_requests'))


class Spy:
    """Stands in for both generator proposers and the beam provider; counts every use."""
    available = True

    def __init__(self, state='unavailable'):
        self.calls, self.state = 0, state
        self.budget = None

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError('proposer must not be resolved')

    def settings(self):
        from engineering.proposer import ProviderSettings
        return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture', temperature=0., max_tokens=1024)

    async def generate(self, *args):
        self.calls += 1
        raise RuntimeError('provider down')


def post_regen(client):
    return client.post('/engineering/v1/drafts', json=dict(capability='regen_chamber', mode='ai',
                                                           prompt=REGEN_PROMPT, input={}))


def post_beam(client):
    return client.post('/engineering/v1/drafts', json={'mode': 'ai', 'prompt': 'Describe a beam', 'input': {}})


def assert_cap_refusal(response):
    assert response.status_code == 503, response.text
    body = response.json()
    assert set(body) == {'state', 'reasons', 'prepared'}
    assert body['state'] == 'unavailable' and body['prepared'] is None
    assert body['reasons'] == [AI_CLIENT_CAP_REASON]
    assert response.headers['cache-control'] == 'no-store'


def test_generator_branch_refuses_over_cap_before_any_proposer_draft_or_ledger_row(ai_client, monkeypatch):
    monkeypatch.setenv(AI_CLIENT_CAP_ENV, '0')
    spy = Spy()
    monkeypatch.setattr(pc, 'ai_proposer', spy)
    before = counts(runtime())
    assert_cap_refusal(post_regen(ai_client))
    assert spy.calls == 0 and counts(runtime()) == before


def test_beam_branch_refuses_over_cap_before_any_draft_or_provider_call(ai_client, monkeypatch):
    monkeypatch.setenv(AI_CLIENT_CAP_ENV, '0')
    spy = Spy()
    runtime().proposer.provider = spy
    before = counts(runtime())
    assert_cap_refusal(post_beam(ai_client))
    assert spy.calls == 0 and counts(runtime()) == before


def test_generator_branch_cap_n_admits_n_and_never_refunds_on_unavailable(ai_client, monkeypatch):
    monkeypatch.setenv(AI_CLIENT_CAP_ENV, '2')

    class Down:
        def __init__(self, budget):
            self.budget = budget

        async def propose(self, prompt, base_spec=None):
            return SimpleNamespace(state='unavailable', payload=None, issues=(), provenance=None,
                                   reasons=('AI preparation unavailable; typed form remains available',))
    monkeypatch.setattr(pc, 'ai_proposer', lambda capability_id, *, enabled, budget=None: Down(budget))
    for _ in range(2):
        response = post_regen(ai_client)
        assert response.status_code == 503 and response.json()['reasons'] != [AI_CLIENT_CAP_REASON]
    assert counts(runtime())[3] == 2  # unavailable proposals still counted
    assert_cap_refusal(post_regen(ai_client))
    assert counts(runtime())[3] == 2


def test_beam_branch_admitted_request_counts_even_when_the_provider_is_unavailable(ai_client, monkeypatch):
    monkeypatch.setenv(AI_CLIENT_CAP_ENV, '1')
    spy = Spy()
    runtime().proposer.provider = spy
    first = post_beam(ai_client)
    assert first.status_code == 503 and first.json()['reasons'] != [AI_CLIENT_CAP_REASON]
    assert counts(runtime())[3] == 1
    assert_cap_refusal(post_beam(ai_client))


def test_draft_quota_refusal_spends_no_cap_slot_on_either_branch(ai_client, monkeypatch):
    # Review LOW-1 (PR #136): the draft quota is checked before the cap, so its refusal writes no cap row.
    monkeypatch.setenv(AI_CLIENT_CAP_ENV, '5')
    spy = Spy()
    monkeypatch.setattr(pc, 'ai_proposer', spy)
    runtime().proposer.provider = spy
    runtime().quota.per_client_drafts = 0
    before = counts(runtime())
    for post in (post_regen, post_beam):
        response = post(ai_client)
        assert response.status_code != 201 and response.json().get('reasons') != [AI_CLIENT_CAP_REASON]
    assert spy.calls == 0 and counts(runtime()) == before and before[3] == 0


@posix
def test_prune_uses_an_index_on_created(tmp_path):
    # Review LOW-3 (PR #136): the per-admit prune must not scan the whole table.
    real = SQLiteStore(tmp_path)
    try:
        with real.transaction() as db:
            plan = ' '.join(str(r) for r in db.execute(
                'EXPLAIN QUERY PLAN DELETE FROM ai_client_requests WHERE created<=?', (0.0,)).fetchall())
    finally:
        real.close()
    assert 'ai_client_requests_created' in plan, plan


def test_flag_off_never_reaches_the_cap_and_writes_nothing(off_client, monkeypatch):
    monkeypatch.setenv(AI_CLIENT_CAP_ENV, '1')
    before = counts(runtime())
    assert post_regen(off_client).status_code == 503
    assert post_beam(off_client).status_code == 503
    assert counts(runtime()) == before and before[3] == 0


def test_cap_disabled_leaves_ai_unchanged(ai_client, monkeypatch):
    monkeypatch.delenv(AI_CLIENT_CAP_ENV, raising=False)
    for _ in range(3):
        assert post_beam(ai_client).json()['reasons'] != [AI_CLIENT_CAP_REASON]
    assert counts(runtime())[3] == 0
