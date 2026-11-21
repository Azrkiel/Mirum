"""rectangular_plate through its typed form (no JSON editor): fill, prepare, review, run, read the result.

A simply supported 1500 x 1000 mm plate under 0.01 MPa. The brief's illustrative thicknesses [8, 10, 12] fail the
small-deflection guard (w/h <= 0.2 needs h of about 22 mm or more at this load), so the candidates are
[24, 28, 32] and the smallest passing one, 24 mm, is expected. Native run: up to about a minute. Runs in CI only
(release-e2e), like every browser case.
The file name ends in _api.py so e2e/pytest.ini (python_files: test_*_api.py) collects it in the release-e2e
suite; it drives the browser UI, which calls the engineering API.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from harness.ui import CheckedGenerator, sign_in

LONG_MS = 300_000
SS_NOTE = ("corners held down: the simply supported edges and corners are held against lift; "
           "corner hold-down forces are reported")
ALLOWABLE_NOTE = "The allowable stress is your design allowable; no hidden safety factor is applied"
FIELDS = {
    "Length a (mm)": "1500",
    "Width b (mm)": "1000",
    "Pressure q (MPa)": "0.01",
    "Young's modulus E (MPa)": "200000",
    "Poisson's ratio nu": "0.3",
    "Allowable stress (MPa)": "250",
    "Max deflection (mm)": "20",
    "Dimensional tolerance (mm)": "0.01",
    "Material reference": "Illustrative steel",
    "Candidate thicknesses (mm, comma separated)": "24, 28, 32",
}


def _fill(page, label: str, value: str) -> None:
    box = page.get_by_label(label, exact=True)
    expect(box).to_be_visible(timeout=60_000)
    box.fill(value)
    box.press("Tab")


def _choose(page, label: str, option: str) -> None:
    # Streamlit 1.39 selectboxes are not label-associated: find the widget by its label text (data-testid tier).
    box = page.get_by_test_id("stSelectbox").filter(
        has=page.get_by_test_id("stWidgetLabel").get_by_text(label, exact=True))
    expect(box).to_have_count(1, timeout=60_000)
    box.locator('[data-baseweb="select"]').click()
    page.get_by_role("option", name=option, exact=True).click()
    expect(box).to_contain_text(option)


def test_plate_typed_form_ss_accepted(stack, token, api, page, shot, external_requests):
    entry = next(c for c in api.capabilities()["capabilities"] if c["id"] == "rectangular_plate")
    sign_in(page, stack.config.ui_url, token)
    view = CheckedGenerator(page)
    view.open_capability(entry["label"])

    # Typed form only: no JSON editor, no AI draft control.
    expect(page.get_by_role("textbox", name="Specification JSON", exact=True)).to_have_count(0)
    expect(page.get_by_text("AI-assisted draft", exact=True)).to_have_count(0)
    expect(view.button("Propose draft")).to_have_count(0)

    for label, value in FIELDS.items():
        _fill(page, label, value)
    _choose(page, "Mode", "Bounded thickness (1-4 candidates)")
    shot(page, "plate_form")
    view.button("Prepare brief").click()

    expect(view.section("2. Review the exact draft")).to_be_visible(timeout=60_000)
    draft_id, revision, brief_sha = view.draft_caption()
    prepared = api.get_draft(draft_id).json()
    assert prepared["issues"] == [], prepared["issues"]
    assert prepared["brief_sha256"] == brief_sha

    view.acknowledge()
    view.finalize()
    job_id = view.run_checks()
    shot(page, "plate_running")

    lifecycle, outcome = view.wait_terminal(LONG_MS)
    shot(page, "plate_result")
    job = api.get_job(job_id).json()
    assert (job["lifecycle"], job["outcome"]) == ("completed", "accepted"), (job["lifecycle"], job["outcome"], job.get("failures"))
    assert (lifecycle, outcome) == ("completed", "accepted"), (lifecycle, outcome)

    expect(view.checked_label).to_be_visible(timeout=60_000)
    expect(page.get_by_text(re.compile(r"Selected thickness: 24(\.0)? mm"))).to_be_visible(timeout=60_000)
    expect(page.get_by_text(SS_NOTE)).to_be_visible(timeout=60_000)
    expect(page.get_by_text(ALLOWABLE_NOTE)).to_be_visible(timeout=60_000)
