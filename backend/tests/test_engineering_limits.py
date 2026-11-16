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


def test_legacy_admission_uses_turn_rule_without_consuming_job_quota():
    from engineering.admission import ClientQuotaExceeded
    gate, _ = fairness(per_hour=1)
    gate.admitted('a', count=False)
    gate.check('a')                      # legacy runs did not use up the hourly job quota
    gate.refused('b')
    with pytest.raises(ClientQuotaExceeded):
        gate.check_turn('a')


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
