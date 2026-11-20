"""D7 acceptance in a real browser: three-style editor, generate path, downloads, v1 import.

Jobs per client: 3 generates (one per style); the other tests never submit.
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect

from harness.ui import AccessGate
from harness.ui_dragster import DOWNLOADS, Dragster

REPO_ROOT = Path(__file__).resolve().parents[1]
STYLES = [("Low wedge", "dragster_wedge_v2"), ("Smooth teardrop", "dragster_teardrop_v2"),
          ("Narrow waist + raised rear housing", "dragster_narrow_waist_v2")]


def open_dragster(stack, token, page) -> Dragster:
    gate = AccessGate(page)
    gate.open(stack.config.ui_url)
    gate.submit(token)
    workspace = Dragster(page)
    expect(workspace.sidebar).to_be_visible(timeout=60_000)
    workspace.open_workflow()
    return workspace


def test_three_styles_generate_and_download(stack, token, api, page, shot, external_requests):
    ui = open_dragster(stack, token, page)
    for label, generator in STYLES:
        ui.choose_style(label)
        if generator != STYLES[0][1]:
            # the previous result stops matching once the new preset is loaded into the draft
            expect(ui.match_notice).to_have_count(0, timeout=30_000)
        expect(ui.generate).to_be_enabled(timeout=30_000)
        ui.generate.click()
        ui.wait_accepted()
        for name in DOWNLOADS:
            expect(ui.button(name)).to_be_visible()
        report = json.loads(Path(ui.download("Report").path()).read_text())
        assert report["schema_version"] == 2 and report["generator_version"] == generator
        assert report["passed"] is True
        assert Path(ui.download("STEP").path()).read_bytes().startswith(b"ISO-10303-21")
        shot(page, "d7_" + generator)
    ui.envelope_toggle.click()
    expect(page.get_by_text("Wheel envelope - not in STEP", exact=False)).to_be_visible(timeout=30_000)
    shot(page, "d7_wheel_envelopes")
    assert external_requests == []


def test_invalid_edit_disables_generate(stack, token, page, shot):
    ui = open_dragster(stack, token, page)
    ui.set_field("pocket_center_z_mm", "999")
    expect(ui.generate).to_be_disabled(timeout=30_000)
    expect(ui.alerts().filter(has_text="Edited parameters are invalid")).to_be_visible(timeout=30_000)
    shot(page, "d7_invalid_edit")


def test_v1_json_loads_with_upgrade_banner(stack, token, page, shot):
    ui = open_dragster(stack, token, page)
    ui.import_expander.click()
    ui.paste_box.fill((REPO_ROOT / "backend/dragsters/examples/wedge.json").read_text())
    ui.paste_box.press("Control+Enter")
    ui.button("Load parameters").click()
    expect(ui.alerts().filter(has_text="Upgraded v1 parameters")).to_be_visible(timeout=30_000)
    expect(ui.generate).to_be_enabled()
    shot(page, "d7_v1_upgrade")
