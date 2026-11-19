"""Fixtures for the release e2e suite (R4).

Session: one Stack (the compose stack is started by the workflow, never by the tests).
Module: restart_backend resets the in-memory quota without changing any limit.
page: a Chromium context that aborts and records every request leaving loopback.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get("MIRUM_E2E_ARTIFACTS", REPO_ROOT / "e2e" / "artifacts"))
_state: dict = {"evidence": None, "stack": None}


def pytest_collection_modifyitems(items):
    for item in items:
        if "e2e/unit/" in item.nodeid.replace("\\", "/") or "unit/" in item.nodeid.replace("\\", "/"):
            continue
        item.add_marker(pytest.mark.browser)


@pytest.fixture(scope="session")
def stack():
    from harness.stack import Stack, StackConfig

    config = StackConfig.from_env(REPO_ROOT)
    s = Stack(config)
    s.wait_healthy()
    _state["stack"] = s
    return s


@pytest.fixture(scope="session")
def token(stack) -> str:
    return stack.config.token


@pytest.fixture(scope="session")
def evidence(stack, token):
    from harness.evidence import Evidence

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    ev = Evidence(ARTIFACTS, redact=[token])
    sha = os.environ.get("GITHUB_SHA") or subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT).stdout.strip()
    ev.set_source(sha, os.environ.get("GITHUB_REF"))
    try:
        ev.set_images(stack.image_ids())
        for lock in ("requirements-release.lock", "frontend/requirements.lock", "e2e/requirements.lock"):
            ev.add_lock_hash(REPO_ROOT / lock)
        for service in ("backend", "frontend"):
            ev.set_pip_freeze(service, stack.pip_freeze(service))
    except Exception as exc:
        if os.environ.get("CI") == "true":
            raise  # CI must never go green with incomplete evidence
        ev.add_note("evidence_error", type(exc).__name__)
    _state["evidence"] = ev
    yield ev
    ev.write()


@pytest.fixture(scope="module")
def restart_backend(stack):
    """Fresh backend process per module: resets the in-memory quota. Limits are untouched."""
    stack.restart_backend()
    yield


@pytest.fixture(scope="module")
def api(stack, token, restart_backend):
    from harness.api import Api

    client = Api(stack.config.api_url, token)
    yield client
    client.close()


@pytest.fixture(scope="module")
def anon_api(api):
    return api.anonymous()


@pytest.fixture(scope="session")
def _browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def external_requests() -> list[str]:
    return []


@pytest.fixture
def page(request, _browser, external_requests):
    """A page whose context blocks (and records) every request and WebSocket leaving loopback."""
    from harness.guard import guarded_context

    context = guarded_context(_browser, external_requests, viewport={"width": 1400, "height": 1000})
    pg = context.new_page()
    pg.set_default_timeout(30_000)
    yield pg
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        _failure_evidence(request.node.name, context.pages)
    context.close()


def _failure_evidence(test_name: str, pages) -> None:
    """On failure keep a PNG and the visible text of every open tab (diagnosis in CI)."""
    import json

    safe = re.sub(r"\W+", "_", test_name)
    out = ARTIFACTS / "failures"
    out.mkdir(parents=True, exist_ok=True)
    for n, tab in enumerate(pages):
        try:
            tab.screenshot(path=str(out / f"{safe}_tab{n}.png"), full_page=True)
            text = tab.locator("body").inner_text(timeout=5_000)[:20_000]
            (out / f"{safe}_tab{n}.json").write_text(
                json.dumps({"url": tab.url.split("?")[0], "text": text}, indent=2) + chr(10),
                encoding="utf-8", newline=chr(10))
        except Exception:
            pass


@pytest.fixture
def second_tab(page):
    """A second tab in the same guarded context (its own Streamlit session)."""
    tab = page.context.new_page()
    tab.set_default_timeout(30_000)
    return tab


@pytest.fixture
def work_dir(tmp_path):
    return tmp_path


@pytest.fixture
def shot(request, evidence):
    """shot(page, name) saves e2e/artifacts/<module>/<name>.png and records it in the evidence."""
    module = re.sub(r"\W+", "_", request.module.__name__)

    def _shot(pg, name: str) -> Path:
        out = ARTIFACTS / module / (re.sub(r"[^\w.-]+", "_", name) + ".png")
        out.parent.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(out), full_page=True)
        evidence.add_screenshot(out, flow=f"{module}/{name}")
        return out

    return _shot


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item.rep_call = report
    ev = _state.get("evidence")
    if ev is not None and report.when in ("setup", "call", "teardown"):
        if report.when == "call" or report.outcome != "passed":
            kind = "error" if report.when != "call" and report.outcome == "failed" else report.outcome
            ev.record_test(item.nodeid, kind, report.duration)
