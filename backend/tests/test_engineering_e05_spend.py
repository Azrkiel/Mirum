"""E05 generator AI spend survives a restart (security finding M1). Offline, stdlib + pydantic."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from engineering.limits import AiCallBudget
from engineering.proposer import ProposalAudit, ProviderSettings
from engineering.store import GeneratorSpendBudget, SQLiteProposalLedger, SQLiteStore

PROMPT = 'Design a regen chamber for a small kerosene engine.'
SHA = hashlib.sha256(PROMPT.encode()).hexdigest()


posix = pytest.mark.skipif(os.name != 'posix', reason='SQLiteStore is Linux-only (runs in CI)')


class MemoryLedger:
    def __init__(self):
        self.rows = []

    def record_generator_call(self, capability, request_sha256):
        self.rows.append((capability, request_sha256))


def beam_audit(calls):
    return ProposalAudit(draft_id='beam-draft', draft_revision=1,
        settings=ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture', temperature=0., max_tokens=1024),
        status='proposed', calls=calls, request_characters=10, request_sha256='0' * 64)


def spend_rows(store):
    with store.transaction() as db:
        return [tuple(row) for row in db.execute('SELECT * FROM generator_spend').fetchall()]


@posix
def test_every_reserved_generator_call_is_recorded_and_seeds_after_restart(tmp_path):
    store = SQLiteStore(tmp_path)
    ledger = SQLiteProposalLedger(store)
    ledger.claim(beam_audit(2))
    inner = AiCallBudget(daily_calls=10, concurrency=1)
    budget = GeneratorSpendBudget(inner, ledger, 'regen_chamber', SHA)
    for _ in range(3):
        assert budget.reserve()
        budget.release()
    assert inner.used() == 3 and budget.has_capacity()
    store.close()

    store = SQLiteStore(tmp_path)  # restart on the same volume
    seeded = AiCallBudget(daily_calls=10, concurrency=1)
    seeded.seed(SQLiteProposalLedger(store).recent_calls(window=seeded.window))
    assert seeded.used() == 2 + 3  # beam calls + generator calls
    rows = spend_rows(store)
    assert len(rows) == 3
    # Capability id and request sha only: no prompt text, no reply.
    assert PROMPT not in json.dumps(rows) and all(SHA in row for row in rows)
    store.close()


@posix
def test_reserve_then_crash_is_not_free(tmp_path):
    store = SQLiteStore(tmp_path)
    budget = GeneratorSpendBudget(AiCallBudget(daily_calls=10, concurrency=1),
                                  SQLiteProposalLedger(store), 'glider_cem', SHA)
    assert budget.reserve()  # the request dies before any draft exists; no release, no audit
    store.close()
    store = SQLiteStore(tmp_path)
    assert len(SQLiteProposalLedger(store).recent_calls()) == 1
    store.close()


@posix
def test_refused_reservation_records_nothing(tmp_path):
    store = SQLiteStore(tmp_path)
    budget = GeneratorSpendBudget(AiCallBudget(daily_calls=0, concurrency=1),
                                  SQLiteProposalLedger(store), 'co2_dragster', SHA)
    assert not budget.has_capacity() and not budget.reserve()
    assert spend_rows(store) == [] and SQLiteProposalLedger(store).recent_calls() == []
    store.close()


def test_each_reserved_unit_writes_one_record_before_the_call():
    ledger = MemoryLedger()
    inner = AiCallBudget(daily_calls=2, concurrency=1)
    budget = GeneratorSpendBudget(inner, ledger, 'regen_chamber', SHA)
    assert budget.reserve() and ledger.rows == [('regen_chamber', SHA)]
    assert not budget.reserve()  # concurrency 1: refused, nothing recorded
    budget.release()
    assert budget.reserve() and len(ledger.rows) == 2
    budget.release()
    assert not budget.reserve() and len(ledger.rows) == 2  # daily cap reached
    assert inner.used() == 2 and not budget.has_capacity()


def test_failed_spend_write_fails_closed():
    ledger = MemoryLedger()
    inner = AiCallBudget(daily_calls=10, concurrency=1)

    def broken(*args):
        raise OSError('disk full')
    ledger.record_generator_call = broken
    budget = GeneratorSpendBudget(inner, ledger, 'regen_chamber', SHA)
    assert not budget.reserve()  # no provider call without a durable record
    assert inner._inflight == 0 and inner.used() == 1  # the unit stays spent, never refunded


@posix
def test_old_spend_ages_out_of_the_window(tmp_path):
    store = SQLiteStore(tmp_path)
    ledger = SQLiteProposalLedger(store)
    ledger.record_generator_call('regen_chamber', SHA)
    now = spend_rows(store)[0][-1]
    assert len(ledger.recent_calls(now=now + 60)) == 1
    assert ledger.recent_calls(now=now + 90000) == []
    store.close()
