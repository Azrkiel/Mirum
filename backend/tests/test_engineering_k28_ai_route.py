"""K28: the AI-draft route fails closed when resolving the proposer itself raises.

CI only (needs fastapi); the fixtures come from the E05 API tests.
"""
from __future__ import annotations

import pytest

from engineering import proposal_contract as pc

from backend.tests.test_engineering_e05_api import (  # noqa: F401
    REGEN_PROMPT, SECRET, _engineering_key_only, ai_client, assert_unavailable, offline_models, post_ai, runtime,
    spend_rows)


@pytest.mark.parametrize('error', [pc.UnsupportedCapability('proposer not installed'),
                                   RuntimeError(SECRET), ImportError(SECRET)])
def test_proposer_resolution_failure_is_unavailable_and_spends_nothing(ai_client, monkeypatch, error):
    def factory(*args, **kwargs):
        raise error
    monkeypatch.setattr(pc, 'ai_proposer', factory)
    budget = runtime().proposer.budget
    before = budget.used()
    response = post_ai(ai_client, 'regen_chamber', REGEN_PROMPT)
    body = assert_unavailable(response)
    assert body['reasons'] == ['AI preparation unavailable; typed form remains available']
    assert SECRET not in response.text and 'proposer not installed' not in response.text
    assert budget.used() == before
    assert spend_rows(runtime()) == []
