"""Page objects for the Streamlit UI. Every selector in the e2e suite lives here.

Selector order (Streamlit 1.39): role/label first, stable data-testid second,
``.st-key-<key>`` only where the DOM probe (e2e/probe_dom.py) has confirmed it. Never emotion
classes or nth-child. Wait on text with ``expect(...).to_have_text``: the results view
refreshes about once a second.
"""
from __future__ import annotations

from playwright.sync_api import Locator, Page, expect

ACCESS_LABEL = "Access token"
WORKFLOW_RADIO = "Design workflow"
CHECKED_WORKFLOW = "Checked design"
DEFAULT_TIMEOUT_MS = 30_000


class AccessGate:
    def __init__(self, page: Page):
        self.page = page

    @property
    def token_input(self) -> Locator:
        return self.page.get_by_label(ACCESS_LABEL, exact=True)

    @property
    def error(self) -> Locator:
        return self.page.get_by_test_id("stAlert").filter(has_text="Invalid access token")

    def open(self, ui_url: str) -> None:
        self.page.goto(ui_url, wait_until="domcontentloaded")
        expect(self.token_input).to_be_visible(timeout=60_000)

    def submit(self, token: str) -> None:
        self.token_input.fill(token)
        self.token_input.press("Enter")


class Workspace:
    """The app after the gate: sidebar workflow radio and the active workflow."""

    def __init__(self, page: Page):
        self.page = page

    @property
    def sidebar(self) -> Locator:
        return self.page.get_by_test_id("stSidebar")

    @property
    def heading(self) -> Locator:
        return self.page.get_by_role("heading", level=1)

    def workflow_option(self, name: str) -> Locator:
        return self.sidebar.get_by_role("radiogroup", name=WORKFLOW_RADIO).get_by_text(name, exact=True)

    def select_workflow(self, name: str) -> None:
        self.workflow_option(name).click()
        expect(self.heading).to_have_text(name, timeout=DEFAULT_TIMEOUT_MS)

    def button(self, name: str) -> Locator:
        return self.page.get_by_role("button", name=name, exact=True)

    def alerts(self) -> Locator:
        return self.page.get_by_test_id("stAlert")

    def section(self, title: str) -> Locator:
        """Scope to the block that follows a section heading."""
        return self.page.get_by_role("heading", name=title)


class CheckedBeam(Workspace):
    """The 'Checked design' beam form (frontend/engineering_ui.py)."""

    def open_form(self) -> None:
        self.select_workflow(CHECKED_WORKFLOW)
        expect(self.section("1. Prepare requirements")).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)

    def field(self, label: str) -> Locator:
        return self.page.get_by_label(label, exact=True)

    def set_field(self, label: str, value: str) -> None:
        box = self.field(label)
        box.fill(value)
        box.press("Tab")

    @property
    def save_draft(self) -> Locator:
        return self.page.get_by_role("button", name="Save draft", exact=True)
