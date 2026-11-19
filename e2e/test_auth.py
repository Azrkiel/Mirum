"""Access gate: API needs the token; the UI gate rejects a wrong token and accepts the right one."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from harness.ui import AccessGate, Workspace


def test_api_requires_token(api, anon_api):
    assert anon_api.get("/auth/check").status_code == 401
    assert anon_api.get("/auth/check", headers={"X-Mirum-Access-Token": "0" * 64}).status_code == 401
    assert api.get("/auth/check").status_code == 200


def test_ui_gate_rejects_wrong_and_accepts_right(stack, token, page, shot, external_requests):
    gate = AccessGate(page)
    gate.open(stack.config.ui_url)
    shot(page, "gate_initial")

    gate.submit("0" * 64)
    expect(gate.error).to_be_visible(timeout=30_000)
    shot(page, "gate_wrong_token")

    gate.submit(token)
    workspace = Workspace(page)
    expect(workspace.sidebar).to_be_visible(timeout=60_000)
    expect(gate.token_input).to_have_count(0)
    shot(page, "gate_accepted")
    assert external_requests == []
