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
            # the draft differs from the accepted result only once the new preset is loaded
            ui.wait_style_loaded()
            expect(ui.style_group.get_by_role("radio", name=label)).to_be_checked()
        expect(ui.generate).to_be_enabled(timeout=30_000)
        previous = ui.current_revision()
        ui.generate.click()
        ui.wait_accepted(previous)
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



def test_zz_temp_probe_patch_later_contexts():
    """TEMP diagnostic: passively log every request of every later browser context (dump at exit)."""
    import atexit
    import os
    import time

    import harness.guard as guard

    original = guard.guarded_context
    t0 = time.monotonic()
    events: list = []
    out = Path(os.environ.get("MIRUM_E2E_ARTIFACTS", REPO_ROOT / "e2e" / "artifacts")) / "probe"

    def stamp():
        return round(time.monotonic() - t0, 3)

    def patched(browser, external, **kwargs):
        context = original(browser, external, **kwargs)
        test = os.environ.get("PYTEST_CURRENT_TEST", "?").split(" ")[0]
        cid = len(events)
        events.append({"t": stamp(), "ctx": cid, "open": test})
        for kind in ("request", "requestfinished", "requestfailed"):
            context.on(kind, lambda req, kind=kind: events.append(
                {"t": stamp(), "ctx": cid, "k": kind[7:] or "start", "m": req.method,
                 "u": req.url.split("?")[0][:140], "type": req.resource_type}))
        context.on("close", lambda _c: events.append({"t": stamp(), "ctx": cid, "close": True}))
        return context

    def dump():
        out.mkdir(parents=True, exist_ok=True)
        (out / "later_contexts.json").write_text(json.dumps(events[-6000:], indent=0),
                                                  encoding="utf-8", newline="
")

    guard.guarded_context = patched
    atexit.register(dump)
