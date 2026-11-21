"""K13: ledger/budget writes made inside AI proposer coroutines run off the event loop."""
from __future__ import annotations

import asyncio
import threading

from backend.tests.test_engineering_dragster_proposer import preset_resolution
from backend.tests.test_engineering_e05_glider_proposer import FakeProvider as GliderProvider
from backend.tests.test_engineering_e05_glider_proposer import reply as glider_reply
from backend.tests.test_engineering_e05_regen_proposer import GOOD, PROMPT, FakeProvider as RegenProvider
from backend.tests.test_engineering_proposer import FixtureProvider, response, setup
from engineering import glider_extraction
from engineering.dragster_proposer import AiProposer as DragsterProposer
from engineering.limits import AiCallBudget
from engineering.regen_proposer import AiProposer as RegenProposer
from engineering.store import GeneratorSpendBudget


class FakeLedger:
    """record_generator_call stand-in: records the writing thread, can fail, delay or block."""

    def __init__(self, fail=False, delay=0.0, gate=None):
        self.idents, self.fail, self.delay, self.gate = [], fail, delay, gate
        self.started = threading.Event()

    def record_generator_call(self, capability, request_sha256):
        self.started.set()
        if self.gate is not None:
            self.gate.wait(5)
        if self.delay:
            threading.Event().wait(self.delay)
        if self.fail:
            raise RuntimeError('ledger down')
        self.idents.append(threading.get_ident())


def spend(ledger, daily=5, concurrency=1):
    inner = AiCallBudget(daily_calls=daily, concurrency=concurrency)
    return inner, GeneratorSpendBudget(inner, ledger, 'regen_chamber', 'sha')


def inflight(inner):
    return inner._inflight


# --- (a) event loop not blocked ----------------------------------------------------------

def test_beam_claim_save_and_reserve_run_off_the_loop():
    from engineering.proposer import MemoryProposalLedger
    idents = {'claim': [], 'save': [], 'reserve': []}

    class Ledger(MemoryProposalLedger):
        def claim(self, audit):
            idents['claim'].append(threading.get_ident())
            super().claim(audit)

        def save(self, audit):
            idents['save'].append(threading.get_ident())
            super().save(audit)

    class Budget(AiCallBudget):
        def reserve(self):
            idents['reserve'].append(threading.get_ident())
            return super().reserve()

    provider = FixtureProvider([response()])
    service, draft, proposer, _ = setup(provider, budget=Budget(5, 1))
    proposer.ledger = Ledger()

    async def go():
        loop_ident = threading.get_ident()
        result = await proposer.propose(draft.draft_id, expected_revision=draft.revision, prompt='Reference fixture prompt')
        return loop_ident, result

    loop_ident, result = asyncio.run(go())
    assert result.state == 'proposed' and len(provider.calls) == 1
    assert len(idents['claim']) == 1 and len(idents['reserve']) == 1 and len(idents['save']) >= 2
    for name, seen in idents.items():
        assert loop_ident not in seen, name


def test_dragster_reserve_runs_off_the_loop():
    ledger = FakeLedger()
    inner, budget = spend(ledger)

    async def interpret(prompt, base_spec=None):
        return preset_resolution()

    async def go():
        return threading.get_ident(), await DragsterProposer(budget, interpret=interpret).propose('wedge dragster')

    loop_ident, outcome = asyncio.run(go())
    assert outcome.state == 'proposed' and len(ledger.idents) == 1
    assert ledger.idents[0] != loop_ident


def test_regen_reserve_runs_off_the_loop():
    ledger = FakeLedger()
    inner, budget = spend(ledger)
    provider = RegenProvider([GOOD])

    async def go():
        return threading.get_ident(), await RegenProposer(budget, provider).propose(PROMPT)

    loop_ident, proposal = asyncio.run(go())
    assert provider.calls == 1 and len(ledger.idents) == 1
    assert ledger.idents[0] != loop_ident


def test_glider_reserve_runs_off_the_loop():
    ledger = FakeLedger()
    inner, budget = spend(ledger)
    provider = GliderProvider([glider_reply()])

    async def go():
        return threading.get_ident(), await glider_extraction.extract_sparse(provider, budget, 'a glider', 4096)

    loop_ident, _ = asyncio.run(go())
    assert provider.calls == 1 and len(ledger.idents) == 1
    assert ledger.idents[0] != loop_ident


# --- (b) concurrent reserves never exceed the cap ----------------------------------------

def test_concurrent_proposers_respect_concurrency_and_daily_caps():
    ledger = FakeLedger(delay=0.01)
    inner, budget = spend(ledger, daily=6, concurrency=2)
    stats = dict(now=0, peak=0, calls=0)

    class Slow(RegenProvider):
        async def generate(self, prompt, schema, settings):
            stats['now'] += 1
            stats['calls'] += 1
            stats['peak'] = max(stats['peak'], stats['now'])
            try:
                await asyncio.sleep(0.02)
                return await super().generate(prompt, schema, settings)
            finally:
                stats['now'] -= 1

    provider = Slow([GOOD] * 20)

    async def go():
        proposer = RegenProposer(budget, provider)
        return await asyncio.gather(*(proposer.propose(PROMPT) for _ in range(12)))

    results = asyncio.run(go())
    assert len(results) == 12
    assert stats['peak'] <= 2
    assert stats['calls'] == len(ledger.idents) <= 6
    assert inner.used() <= 6
    assert inflight(inner) == 0 and stats['now'] == 0


# --- (c) ledger write failure ------------------------------------------------------------

def _unavailable_shape(proposal):
    return proposal.state, tuple(proposal.reasons), getattr(proposal, 'reason_id', None)


def test_regen_ledger_failure_is_unavailable_budget_with_slot_released():
    ledger = FakeLedger(fail=True)
    inner, budget = spend(ledger)
    provider = RegenProvider([GOOD])
    proposal = asyncio.run(RegenProposer(budget, provider).propose(PROMPT))
    exhausted = asyncio.run(RegenProposer(AiCallBudget(0, 1), RegenProvider([GOOD])).propose(PROMPT))
    assert _unavailable_shape(proposal) == _unavailable_shape(exhausted)
    assert proposal.state == 'unavailable' and provider.calls == 0
    assert inflight(inner) == 0 and inner.has_capacity() and inner.used() == 1
    assert inner.reserve()


def test_dragster_ledger_failure_is_unavailable_with_slot_released():
    ledger = FakeLedger(fail=True)
    inner, budget = spend(ledger)
    seen = []

    async def interpret(prompt, base_spec=None):
        seen.append(prompt)
        return preset_resolution()

    outcome = asyncio.run(DragsterProposer(budget, interpret=interpret).propose('wedge dragster'))
    assert outcome.state == 'unavailable' and outcome.reasons == ('AI preparation unavailable; typed form remains available',)
    assert seen == [] and inflight(inner) == 0 and inner.used() == 1


def test_glider_ledger_failure_is_unavailable_budget_with_slot_released():
    ledger = FakeLedger(fail=True)
    inner, budget = spend(ledger)
    provider = GliderProvider([glider_reply()])
    parsed, reason, _ = asyncio.run(glider_extraction.extract_sparse(provider, budget, 'a glider', 4096))
    assert parsed is None and reason == 'unavailable.budget' and provider.calls == 0
    assert inflight(inner) == 0 and inner.used() == 1


# --- (d) cancellation --------------------------------------------------------------------

def test_cancelled_reserve_does_not_leak_the_inflight_slot():
    gate = threading.Event()
    ledger = FakeLedger(gate=gate)
    inner, budget = spend(ledger)
    provider = RegenProvider([GOOD])

    async def go():
        task = asyncio.ensure_future(RegenProposer(budget, provider).propose(PROMPT))
        for _ in range(100):
            if ledger.started.is_set():
                break
            await asyncio.sleep(0.002)
        assert ledger.started.is_set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert inflight(inner) == 1  # reserve thread still holds the slot
        gate.set()
        for _ in range(100):
            if inflight(inner) == 0:
                break
            await asyncio.sleep(0.002)

    try:
        asyncio.run(go())
    finally:
        gate.set()
    assert inflight(inner) == 0 and inner.used() == 1 and provider.calls == 0
