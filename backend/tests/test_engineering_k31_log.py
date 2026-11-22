"""K31: an AI-draft proposer-resolution failure logs one fixed line, never the exception text.

CI only (needs fastapi); the fixtures come from the E05 API tests.
"""
from __future__ import annotations

import logging

import pytest

from engineering import proposal_contract as pc

from backend.tests.test_engineering_e05_api import (  # noqa: F401
    REGEN_PROMPT, SECRET, _engineering_key_only, ai_client, assert_unavailable, offline_models, post_ai, runtime,
    spend_rows)


@pytest.mark.parametrize('error', [RuntimeError(SECRET), ImportError(SECRET), pc.UnsupportedCapability(SECRET)])
def test_proposer_resolution_failure_logs_one_fixed_line(ai_client, monkeypatch, caplog, error):
    def factory(*args, **kwargs):
        raise error
    monkeypatch.setattr(pc, 'ai_proposer', factory)
    with caplog.at_level(logging.DEBUG):
        response = post_ai(ai_client, 'regen_chamber', REGEN_PROMPT)
    assert_unavailable(response)
    lines = [r for r in caplog.records if r.name.endswith('engineering.api_v1')]
    assert [(r.levelno, r.getMessage()) for r in lines] == [
        (logging.WARNING, 'engineering.ai_route proposer_unavailable capability=regen_chamber')]
    assert all(r.exc_info is None for r in lines)
    assert SECRET not in caplog.text
    assert REGEN_PROMPT not in caplog.text
