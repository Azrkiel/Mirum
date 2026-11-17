"""Deterministic abuse limits: fairness, AI spend budget, config parsing. Pure Python."""
import pytest


class Clock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


def fairness(per_hour=3, memory=120):
    from engineering.admission import ClientFairness
    clock = Clock()
    return ClientFairness(per_hour, memory, clock=clock), clock


def test_hourly_quota_is_per_client_and_rolls_off():
    from engineering.admission import ClientQuotaExceeded
    gate, clock = fairness(per_hour=2)
    for _ in range(2):
        gate.check('a'); gate.admitted('a'); clock.advance(10)
    with pytest.raises(ClientQuotaExceeded) as refusal:
        gate.check('a')
    assert 3500 <= refusal.value.retry_after <= 3600
    gate.check('b')  # another client is unaffected
    clock.advance(3600)
    gate.check('a')


def test_turn_rule_blocks_back_to_back_readmission_only_while_another_client_waits():
    from engineering.admission import ClientQuotaExceeded
    gate, clock = fairness(per_hour=10, memory=120)
    gate.check('a'); gate.admitted('a')
    gate.check('a')                      # nobody waiting: a may go again
    gate.refused('b')                    # b was refused while a held the slot
    with pytest.raises(ClientQuotaExceeded) as refusal:
        gate.check('a')                  # a must wait its turn
    assert 1 <= refusal.value.retry_after <= 120
    gate.check('b'); gate.admitted('b')  # b takes the slot, its refusal is cleared
    gate.check('a')                      # b is no longer waiting and the last admitted is b


def test_turn_rule_cannot_starve_the_last_client_forever():
    from engineering.admission import ClientQuotaExceeded
    gate, clock = fairness(per_hour=10, memory=120)
    gate.admitted('a'); gate.refused('b')
    with pytest.raises(ClientQuotaExceeded):
        gate.check('a')
    clock.advance(121)                   # b never came back
    gate.check('a')


def test_legacy_admission_spends_the_same_hourly_quota_and_turn_rule():
    from engineering.admission import ClientQuotaExceeded
    gate, _ = fairness(per_hour=1)
    gate.check('a'); gate.admitted('a')  # the legacy route now calls check() then admitted()
    with pytest.raises(ClientQuotaExceeded):
        gate.check('a')                  # one legacy run used the hourly quota
    gate.check('b')
    gate.refused('b')
    with pytest.raises(ClientQuotaExceeded):
        gate.check_turn('a')


def test_admitted_can_skip_the_quota_count_for_internal_callers():
    gate, _ = fairness(per_hour=1)
    gate.admitted('a', count=False)
    gate.check('a')                      # not counted, so the hourly quota is intact


def test_budget_seed_restores_spend_after_restart():
    from engineering.limits import AiCallBudget
    clock = Clock()
    budget = AiCallBudget(3, 5, clock=clock)
    budget.seed([10.0, 3600.0, 86300.0, 90000.0, -5.0])   # the last two are outside the window
    assert budget.used() == 3 and not budget.has_capacity()
    clock.advance(200)                                      # the 86300 s old call ages out
    assert budget.used() == 2 and budget.reserve()


def test_orphaned_thread_result_is_handed_to_the_loop_after_cancellation():
    import asyncio
    import threading
    from engineering.limits import run_in_thread_owned
    released, orphans = threading.Event(), []
    def slow():
        released.wait(5)
        return 'resource'
    async def run():
        task = asyncio.ensure_future(run_in_thread_owned(slow, on_orphan=orphans.append))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert orphans == []
        released.set()
        for _ in range(100):
            if orphans: break
            await asyncio.sleep(0.02)
    asyncio.run(run())
    assert orphans == ['resource']


def test_uncancelled_thread_call_returns_its_value_and_errors_propagate():
    import asyncio
    from engineering.limits import run_in_thread_owned
    async def run():
        assert await run_in_thread_owned(lambda x: x + 1, 1, on_orphan=lambda r: None) == 2
        with pytest.raises(ZeroDivisionError):
            await run_in_thread_owned(lambda: 1 / 0, on_orphan=lambda r: None)
    asyncio.run(run())


def test_unlimited_fairness_never_refuses_a_lone_client():
    from engineering.admission import ClientFairness
    gate = ClientFairness.unlimited()
    for _ in range(100):
        gate.check('a'); gate.admitted('a')


def test_ai_budget_counts_every_call_and_rolls_over_24_hours():
    from engineering.limits import AiCallBudget
    clock = Clock()
    budget = AiCallBudget(3, 5, clock=clock)
    for _ in range(3):
        assert budget.reserve(); budget.release()
    assert not budget.has_capacity() and not budget.reserve()
    clock.advance(86399)
    assert not budget.reserve()
    clock.advance(2)
    assert budget.has_capacity() and budget.reserve()


def test_ai_budget_zero_means_no_calls_and_concurrency_is_limited():
    from engineering.limits import AiCallBudget
    assert not AiCallBudget(0, 2).has_capacity() and not AiCallBudget(0, 2).reserve()
    budget = AiCallBudget(100, 2)
    assert budget.reserve() and budget.reserve() and not budget.reserve()
    budget.release()
    assert budget.reserve()
    assert budget.used() == 3


def test_config_defaults_and_overrides(monkeypatch):
    import importlib
    import config
    assert config.ENGINEERING_AI_DAILY_CALLS == 200 and config.ENGINEERING_AI_CONCURRENCY == 2
    assert config.ENGINEERING_TLS_OS_TRUST is False
    assert config.ENGINEERING_DRAFT_TTL_SECONDS == 4 * 3600
    monkeypatch.setenv('MIRUM_ENGINEERING_AI_DAILY_CALLS', '0')
    monkeypatch.setenv('MIRUM_ENGINEERING_TLS_OS_TRUST', 'true')
    monkeypatch.setenv('MIRUM_ENGINEERING_AI_CONCURRENCY', 'garbage')
    try:
        reloaded = importlib.reload(config)
        assert reloaded.ENGINEERING_AI_DAILY_CALLS == 0
        assert reloaded.ENGINEERING_TLS_OS_TRUST is True
        assert reloaded.ENGINEERING_AI_CONCURRENCY == 2
    finally:
        monkeypatch.undo()
        importlib.reload(config)
