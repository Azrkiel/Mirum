"""E05 P4: AI entry mode in the Checked design view, with no provider reachable.

Two stacks, selected by the workflow with -k (never by skips):
- flag_off cases run against the default release stack (AI flag off): no AI control anywhere.
- every other case runs against e2e/compose/e05-ai-offline.override.yml: flag on, a placeholder key and a backend
  on an internal network. The backend is not reachable from the host, so these cases drive the UI only.
No live model call is possible in either stack.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

from harness.ui import CHECKED_WORKFLOW, CheckedGenerator, sign_in

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARTIFACTS = Path(os.environ.get("MIRUM_E2E_ARTIFACTS", Path(__file__).resolve().parent / "artifacts")) / "test_e05_ai_offline"
UI_URL = os.environ.get("MIRUM_E2E_UI_URL", "http://127.0.0.1:8501")
TIMEOUT_MS = 180_000
PROMPTS = {  # substring of the capability label -> a prompt that reaches the provider step
    "dragster": "Design a wedge CO2 dragster body 210 mm long with 7 mm wide wheels.",
    "chamber": "Draft a regeneratively cooled rocket thrust chamber with a chamber length of 44 mm and a manifold end margin of 9 mm.",
    "Glider": "Design a glider to maximize distance from a drop at 14 m. Search conventional and t_tail families with a maximum wingspan of 1.2 m.",
}


@pytest.fixture(scope="module")
def token() -> str:
    value = os.environ.get("MIRUM_E2E_TOKEN", "")
    assert len(value) >= 32, "MIRUM_E2E_TOKEN is required"
    return value


@pytest.fixture(scope="module", autouse=True)
def ui_up():
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(UI_URL.rstrip("/") + "/_stcore/health", timeout=5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    raise AssertionError("frontend did not become healthy")


def _shot(page, name: str) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / (re.sub(r"[^\w.-]+", "_", name) + ".png")), full_page=True)


def _capability_group(page):
    return page.get_by_role("radiogroup", name="Capability")


def _open_checked(page, token):
    sign_in(page, UI_URL, token)
    view = CheckedGenerator(page)
    view.select_workflow(CHECKED_WORKFLOW)
    group = _capability_group(page)
    expect(group).to_be_visible(timeout=60_000)
    return view, [t.strip() for t in group.locator("label").all_inner_texts() if t.strip()]


def test_flag_off_no_ai_control_on_any_capability(token, page):
    view, labels = _open_checked(page, token)
    assert len(labels) == 4, labels
    for label in labels:
        _capability_group(page).get_by_text(label, exact=True).click()
        page.wait_for_timeout(1500)  # let the rerun settle; the assertions are on stable absence
        expect(page.get_by_role("radiogroup", name="Entry method")).to_have_count(0)
        expect(page.get_by_text("AI-assisted draft", exact=True)).to_have_count(0)
        expect(view.button("Propose draft")).to_have_count(0)
    _shot(page, "flag_off")


def test_ai_mode_is_offered_for_every_capability(token, page):
    view, labels = _open_checked(page, token)
    assert len(labels) == 4, labels
    for label in labels:
        _capability_group(page).get_by_text(label, exact=True).click()
        entry = page.get_by_role("radiogroup", name="Entry method")
        expect(entry).to_be_visible(timeout=60_000)
        expect(entry.get_by_text("AI-assisted draft", exact=True)).to_be_visible()
        expect(entry.get_by_text("Typed form", exact=True)).to_be_visible()
    _shot(page, "ai_offered")


def test_ai_prompt_ends_unavailable_with_typed_form_left(token, page):
    view, labels = _open_checked(page, token)
    generators = [label for label in labels if any(key in label for key in PROMPTS)]
    assert len(generators) == 3, labels
    for label in generators:
        prompt = next(text for key, text in PROMPTS.items() if key in label)
        _capability_group(page).get_by_text(label, exact=True).click()
        entry = page.get_by_role("radiogroup", name="Entry method")
        expect(entry).to_be_visible(timeout=60_000)
        entry.get_by_text("AI-assisted draft", exact=True).click()
        box = page.get_by_role("textbox", name="Engineering request", exact=True)
        expect(box).to_be_visible(timeout=60_000)
        box.fill(prompt)
        box.press("Control+Enter")
        propose = view.button("Propose draft")
        expect(propose).to_be_enabled(timeout=30_000)
        propose.click()
        expect(page.get_by_text("typed form remains available", exact=False).first).to_be_visible(timeout=TIMEOUT_MS)
        # no draft was adopted and no AI value is claimed
        expect(page.get_by_text(re.compile(r"Draft \S+ \| revision"))).to_have_count(0)
        expect(page.get_by_text("AI-proposed", exact=False)).to_have_count(0)
        expect(page.get_by_text("AI proposed a draft", exact=False)).to_have_count(0)
        expect(page.get_by_test_id("stException")).to_have_count(0)
        _shot(page, "unavailable_" + re.sub(r"\W+", "_", label))
        entry.get_by_text("Typed form", exact=True).click()
        expect(view.button("Start an empty draft")).to_be_visible(timeout=60_000)


def test_typed_form_still_completes_for_a_generator_with_ai_on(token, page):
    view, labels = _open_checked(page, token)
    label = next(label for label in labels if "dragster" in label)
    view.open_capability(label)
    view.start_empty_draft()
    spec = json.loads((FIXTURES / "dragster_spec.json").read_text(encoding="utf-8"))
    view.save_specification_json(json.dumps(spec))
    view.acknowledge()
    view.finalize()
    view.run_checks()
    lifecycle, outcome = view.wait_terminal(600_000)
    # The dragster is geometry_checks_only: its passing state line reads "geometry checks passed" (Wave E P2).
    assert (lifecycle, outcome) == ("completed", "geometry checks passed"), (lifecycle, outcome)
    _shot(page, "form_path_result")
