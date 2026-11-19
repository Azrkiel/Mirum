"""Page objects for the Streamlit UI. Every selector in the e2e suite lives here.

Selector order (Streamlit 1.39): role/label first, stable data-testid second,
``.st-key-<key>`` only where the DOM probe (e2e/probe_dom.py) has confirmed it. Never emotion
classes or nth-child. Wait on text with ``expect(...).to_have_text``: the results view
refreshes about once a second.
"""
from __future__ import annotations

import re
from pathlib import Path

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




WORKFLOWS = (
    "Text to CAD", "CO2 dragster", "Passive glider distance search",
    "Physics-based glider CEM", "Deterministic rocket nozzle", CHECKED_WORKFLOW,
)
_DRAFT_RE = re.compile(r"Draft (\S+) \| revision (\d+) \| brief SHA-256 ([0-9a-f]{64})")
_BRIEF_RE = re.compile(r"Finalized brief (\S+) \| ([0-9a-f]{64})")
_JOB_RE = re.compile(r"Job (\S+) \| brief SHA-256 ([0-9a-f]{64})")
_TERMINAL_RE = re.compile(r"State: (completed|unavailable|cancelled) \| engineering outcome: (\w+)")
CHECKED_LABEL_TEXT = "Checked design"
BEAM_PREVIEW_TRACE = "Measured STEP preview"


def sign_in(page: Page, ui_url: str, token: str) -> Workspace:
    gate = AccessGate(page)
    gate.open(ui_url)
    gate.submit(token)
    workspace = Workspace(page)
    expect(workspace.sidebar).to_be_visible(timeout=60_000)
    expect(gate.token_input).to_have_count(0)
    return workspace


class CheckedFlow(Workspace):
    """Review, finalize, run and inspect a checked job. Shared by the beam and the generators."""

    ACK_PREFIX = "I reviewed every value"

    def acknowledge(self) -> None:
        box = self.page.get_by_test_id("stCheckbox").filter(has_text=self.ACK_PREFIX)
        expect(box).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
        box.locator("label").click()
        expect(box.get_by_role("checkbox")).to_be_checked(timeout=DEFAULT_TIMEOUT_MS)

    def finalize(self) -> tuple[str, str]:
        button = self.button("Finalize reviewed brief")
        expect(button).to_be_enabled(timeout=DEFAULT_TIMEOUT_MS)
        button.click()
        caption = self.page.get_by_text(_BRIEF_RE)
        expect(caption.first).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
        found = _BRIEF_RE.search(caption.first.inner_text())
        return found.group(1), found.group(2)

    def draft_caption(self) -> tuple[str, int, str]:
        caption = self.page.get_by_text(_DRAFT_RE)
        expect(caption.first).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
        found = _DRAFT_RE.search(caption.first.inner_text())
        return found.group(1), int(found.group(2)), found.group(3)

    def run(self, run_label: str) -> str:
        button = self.button(run_label)
        expect(button).to_be_enabled(timeout=DEFAULT_TIMEOUT_MS)
        button.click()
        return self.job_caption()[0]

    def job_caption(self) -> tuple[str, str]:
        caption = self.page.get_by_text(_JOB_RE)
        expect(caption.first).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
        found = _JOB_RE.search(caption.first.inner_text())
        return found.group(1), found.group(2)

    def wait_terminal(self, timeout_ms: int) -> tuple[str, str]:
        """(lifecycle, outcome) once the job reaches a terminal state."""
        state = self.page.get_by_text(_TERMINAL_RE)
        expect(state.first).to_be_visible(timeout=timeout_ms)
        found = _TERMINAL_RE.search(state.first.inner_text())
        return found.group(1), found.group(2)

    @property
    def checked_label(self) -> Locator:
        """The bold 'Checked design' assurance label of an accepted generator run."""
        return self.page.locator("strong").get_by_text(CHECKED_LABEL_TEXT, exact=True)

    @property
    def plotly_chart(self) -> Locator:
        return self.page.get_by_test_id("stPlotlyChart")

    def plotly_trace_names(self) -> list[str]:
        self.page.wait_for_function(
            "() => { const g = document.querySelector('.js-plotly-plot'); return !!(g && g.data && g.data.length); }",
            timeout=DEFAULT_TIMEOUT_MS)
        return self.page.evaluate(
            "() => Array.from(document.querySelector('.js-plotly-plot').data, t => t.name)")

    @property
    def download_buttons(self) -> Locator:
        return self.page.get_by_test_id("stDownloadButton")

    def download_names(self) -> list[str]:
        texts = self.download_buttons.all_inner_texts()
        return sorted(t.strip().removeprefix("Download ").strip() for t in texts)

    def download(self, name: str, directory: Path) -> bytes:
        button = self.download_buttons.filter(has_text="Download " + name).get_by_role("button")
        with self.page.expect_download(timeout=DEFAULT_TIMEOUT_MS) as info:
            button.click()
        download = info.value
        assert download.suggested_filename == name, (download.suggested_filename, name)
        target = directory / name
        download.save_as(str(target))
        return target.read_bytes()

    def attempts_cells(self) -> list[str] | None:
        """Text of the result table's grid cells, or None when Streamlit draws it on a canvas
        without an accessibility tree."""
        cells = self.page.get_by_test_id("stDataFrame").last.locator('[role="gridcell"]')
        if not cells.count():
            return None
        return [c.strip() for c in cells.all_inner_texts()]


class CheckedBeam(CheckedFlow):
    """The 'Checked design' beam form (frontend/engineering_ui.py)."""

    FIELDS = {
        "length_mm": "Length (mm)",
        "width_mm": "Width (mm)",
        "tip_force_n": "Tip force (+Z, N)",
        "max_tip_deflection_mm": "Maximum tip displacement (mm)",
        "dimensional_tolerance_mm": "Digital dimensional tolerance (mm)",
        "youngs_modulus_mpa": "Elastic modulus (MPa)",
        "allowable_stress_mpa": "Allowable stress (MPa)",
    }
    REFERENCE_LABEL = "Material / allowable source reference"
    CHOICES_LABEL = "Thickness choices to review (mm, comma-separated; 1 to 4)"

    def open_form(self) -> None:
        self.select_workflow(CHECKED_WORKFLOW)
        expect(self.section("1. Prepare requirements")).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)

    def field(self, label: str) -> Locator:
        return self.page.get_by_label(label, exact=True)

    def set_field(self, label: str, value: str) -> None:
        box = self.field(label)
        box.fill(value)
        box.press("Tab")

    def type_requirements(self, specification: dict, heights: list[float]) -> None:
        """Type every value of examples/engineering/cantilever.json into the form."""
        for key, label in self.FIELDS.items():
            self.set_field(label, repr(float(specification[key])))
        self.set_field(self.REFERENCE_LABEL, specification["material_reference"])
        self.set_field(self.CHOICES_LABEL, ", ".join(repr(float(h)) for h in heights))

    def save_draft(self) -> tuple[str, int, str]:
        self.button("Save draft").click()
        expect(self.section("2. Review the exact draft")).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
        return self.draft_caption()

    def run_checks(self) -> str:
        return self.run("Run reviewed beam checks")

    @property
    def rejected_notice(self) -> Locator:
        return self.alerts().filter(has_text="No reviewed candidate passed")

    @property
    def accepted_notice(self) -> Locator:
        return self.alerts().filter(has_text="Accepted under the declared digital beam model")


class CheckedGenerator(CheckedFlow):
    """A generator capability (dragster, regen, glider) in the Checked design view."""

    def open_capability(self, label: str) -> None:
        self.select_workflow(CHECKED_WORKFLOW)
        group = self.page.get_by_role("radiogroup", name="Capability")
        expect(group).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
        group.get_by_text(label, exact=True).click()
        expect(self.section("1. Prepare the specification")).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)

    def start_empty_draft(self) -> None:
        self.button("Start an empty draft").click()
        expect(self.page.get_by_role("textbox", name="Specification JSON", exact=True)).to_be_visible(
            timeout=DEFAULT_TIMEOUT_MS)

    def save_specification_json(self, text: str) -> tuple[str, int, str]:
        box = self.page.get_by_role("textbox", name="Specification JSON", exact=True)
        box.fill(text)
        box.press("Control+Enter")
        save = self.button("Save specification")
        expect(save).to_be_enabled(timeout=DEFAULT_TIMEOUT_MS)
        save.click()
        expect(self.button("Save draft")).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
        return self.draft_caption()

    def run_checks(self) -> str:
        return self.run("Run reviewed checks")


class TextToCad(Workspace):
    """The default workflow: prompt box and Generate model."""

    @property
    def prompt(self) -> Locator:
        return self.page.get_by_role("textbox", name="prompt", exact=True)

    def submit_prompt(self, text: str) -> None:
        self.prompt.fill(text)
        self.prompt.press("Control+Enter")
        generate = self.button("Generate model")
        expect(generate).to_be_enabled(timeout=DEFAULT_TIMEOUT_MS)
        generate.click()

    @property
    def route_notice(self) -> Locator:
        return self.alerts().filter(has_text=re.compile(r"Checked design: "))

    @property
    def failure_alert(self) -> Locator:
        return self.alerts().filter(has_text=re.compile(r"Planning failed|Could not reach backend|Generation failed"))


class StaleBanner(Workspace):
    @property
    def warning(self) -> Locator:
        return self.alerts().filter(has_text="This draft changed in another tab")

    @property
    def load_latest(self) -> Locator:
        return self.button("Load latest revision")
