"""Page object for the 'CO2 dragster' workflow (frontend/dragster_ui.py).

Kept apart from ui.py so the dragster suite never edits the shared page objects.
"""
from __future__ import annotations

from playwright.sync_api import Locator, expect

from harness.ui import DEFAULT_TIMEOUT_MS, Workspace

WORKFLOW = "CO2 dragster"
STYLE_RADIO = "Body style"
GENERATE = "Generate body"
MATCH_TEXT = "Edited parameters match displayed result."
DOWNLOADS = ("STEP", "GLB", "Parameters", "Report")


class Dragster(Workspace):
    def open_workflow(self) -> None:
        self.workflow_option(WORKFLOW).click()
        expect(self.style_group).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)

    @property
    def style_group(self) -> Locator:
        return self.page.get_by_role("radiogroup", name=STYLE_RADIO)

    def choose_style(self, label: str) -> None:
        self.style_group.get_by_text(label, exact=True).click()

    @property
    def generate(self) -> Locator:
        return self.button(GENERATE)

    def field(self, label: str) -> Locator:
        return self.page.get_by_label(label, exact=True)

    def set_field(self, label: str, value: str) -> None:
        box = self.field(label)
        box.fill(value)
        box.press("Tab")

    @property
    def match_notice(self) -> Locator:
        return self.page.get_by_text(MATCH_TEXT)

    def wait_accepted(self) -> None:
        """The draft equals the accepted result only after a successful generation."""
        expect(self.match_notice).to_be_visible(timeout=120_000)

    def download(self, label: str):
        with self.page.expect_download(timeout=DEFAULT_TIMEOUT_MS) as info:
            self.button(label).click()
        return info.value

    @property
    def envelope_toggle(self) -> Locator:
        return self.page.get_by_text("Show wheel envelopes", exact=True)

    @property
    def import_expander(self) -> Locator:
        return self.page.get_by_text("Import parameters", exact=True)

    @property
    def paste_box(self) -> Locator:
        return self.page.get_by_label("Paste parameters JSON", exact=True)
