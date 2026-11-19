"""Text prompts through the UI: a beam prompt routes to checked design with a notice; a prompt
no capability claims gets an error state, no checked label and no provider request. No key is
ever configured. The API half lives in test_llm_route_api.py."""
from __future__ import annotations

from playwright.sync_api import expect

from harness.ui import CHECKED_WORKFLOW, CheckedFlow, TextToCad, sign_in

BEAM_PROMPT = "Size a rectangular cantilever beam for a tip force with an allowable stress"
PLAIN_PROMPT = "A smooth ergonomic shell for a computer mouse, roughly 120mm long"


def test_beam_prompt_routes_to_checked_design_with_notice(stack, token, page, shot, external_requests):
    sign_in(page, stack.config.ui_url, token)
    home = TextToCad(page)
    home.submit_prompt(BEAM_PROMPT)
    expect(home.route_notice.first).to_be_visible(timeout=240_000)
    expect(home.heading).to_have_text(CHECKED_WORKFLOW, timeout=60_000)
    shot(page, "route_beam")
    assert external_requests == []


def test_non_capability_prompt_gets_error_state_and_no_provider_call(stack, token, page, shot, external_requests):
    sign_in(page, stack.config.ui_url, token)
    home = TextToCad(page)
    home.submit_prompt(PLAIN_PROMPT)
    expect(home.failure_alert.first).to_be_visible(timeout=240_000)
    expect(CheckedFlow(page).checked_label).to_have_count(0)
    expect(home.heading).to_have_text("Text to CAD")
    shot(page, "route_plain_error")
    assert external_requests == []
    assert "generativelanguage.googleapis.com" not in stack.logs(tail=2000), "backend contacted the provider"
