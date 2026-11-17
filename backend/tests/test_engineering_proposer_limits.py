"""Provider spend budget and TLS defaults for AI preparation (offline fixtures only)."""
import asyncio

import pytest

from backend.tests.test_engineering_proposer import FixtureProvider, propose, response, setup


def test_every_provider_call_including_repair_spends_the_budget():
    from engineering.limits import AiCallBudget
    budget = AiCallBudget(2, 2)
    provider = FixtureProvider(['not json', 'still not json'])
    _, draft, proposer, _ = setup(provider, budget=budget)
    assert propose(proposer, draft).state == 'unavailable'
    assert len(provider.calls) == 2 and budget.used() == 2
    other = FixtureProvider([response()])
    _, draft2, proposer2, _ = setup(other, budget=budget)
    refused = propose(proposer2, draft2)
    assert refused.state == 'unavailable' and other.calls == [] and budget.used() == 2


def test_budget_exhausted_before_the_repair_call_skips_it():
    from engineering.limits import AiCallBudget
    provider = FixtureProvider(['not json', response()])
    _, draft, proposer, ledger = setup(provider, budget=AiCallBudget(1, 2))
    result = propose(proposer, draft)
    assert result.state == 'unavailable' and len(provider.calls) == 1
    assert ledger.read(draft.draft_id, 1).calls == 1


def test_zero_budget_never_calls_the_provider_or_claims_the_ledger():
    from engineering.limits import AiCallBudget
    provider = FixtureProvider([response()])
    _, draft, proposer, ledger = setup(provider, budget=AiCallBudget(0, 2))
    assert propose(proposer, draft).state == 'unavailable'
    assert provider.calls == []
    with pytest.raises(KeyError):
        ledger.read(draft.draft_id, 1)


def test_in_flight_slot_is_released_after_a_failed_call():
    from engineering.limits import AiCallBudget
    budget = AiCallBudget(10, 1)
    _, draft, proposer, _ = setup(FixtureProvider([RuntimeError('boom')]), budget=budget)
    assert propose(proposer, draft).state == 'unavailable'
    assert budget.reserve()


def test_default_tls_uses_sdk_bundled_roots_and_never_reads_the_os_store(monkeypatch):
    import ssl
    from backend.tests.test_engineering_proposer import FakeGenai
    from engineering.proposer import GeminiProposalProvider, provider_schema
    import config

    async def failing():
        raise RuntimeError('offline provider failure')

    def forbidden(*args, **kwargs):
        raise AssertionError('OS trust store or custom credentials used by default')

    fake = FakeGenai(monkeypatch, behaviour=failing)
    monkeypatch.setattr(config, 'ENGINEERING_TLS_OS_TRUST', False)
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    monkeypatch.setattr(ssl, 'create_default_context', forbidden)
    provider = GeminiProposalProvider()
    with pytest.raises(RuntimeError, match='offline provider failure'):
        asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
    options = fake.clients[0]['http_options']
    assert set(fake.clients[0]) == {'api_key', 'vertexai', 'http_options'}
    assert options.httpx_async_client is None and options.async_client_args is None
    assert fake.events == ['called', 'closed']


def test_os_trust_opt_in_with_an_empty_store_fails_closed(monkeypatch):
    import ssl
    from engineering.proposer import GeminiProposalProvider, provider_schema
    import config

    class Context:
        def get_ca_certs(self, binary_form=False): return []

    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    monkeypatch.setattr(ssl, 'create_default_context', lambda *a, **k: Context())
    monkeypatch.setattr(config, 'ENGINEERING_TLS_OS_TRUST', True)
    provider = GeminiProposalProvider()
    with pytest.raises(RuntimeError, match='trusted roots unavailable'):
        asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
