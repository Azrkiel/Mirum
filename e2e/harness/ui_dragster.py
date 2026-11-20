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
DIFFER_TEXT = "Edited parameters differ from displayed result."
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

    @property
    def differ_notice(self) -> Locator:
        return self.page.get_by_text(DIFFER_TEXT, exact=False)

    def wait_style_loaded(self) -> None:
        """A style switch is two reruns (pending preset, then load); the differ notice is the
        positive signal that the second one finished and the draft holds the new preset."""
        expect(self.differ_notice).to_be_visible(timeout=30_000)

    @property
    def revision(self) -> Locator:
        return self.page.get_by_text("Accepted revision:", exact=False)

    def current_revision(self) -> str:
        return self.revision.text_content() if self.revision.count() else ""

    def wait_accepted(self, previous: str = "") -> None:
        """A new accepted revision, not just the match notice: the notice renders before the
        download buttons in the same run, so only the trailing revision caption proves the
        buttons already carry the new files."""
        expect(self.match_notice).to_be_visible(timeout=120_000)
        expect(self.revision).to_be_visible(timeout=120_000)
        if previous:
            expect(self.revision).not_to_have_text(previous, timeout=120_000)

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
