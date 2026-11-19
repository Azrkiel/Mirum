"""Task 1 probe: dump the beam page's DOM facts to JSON and settle the st-key question.

Run against a live stack (see .github/workflows/release-e2e.yml):
    MIRUM_E2E_TOKEN=... python e2e/probe_dom.py [out_dir]
Writes <out_dir>/dom_probe.json and a PNG. Never prints the token.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

UI_URL = os.environ.get("MIRUM_E2E_UI_URL", "http://127.0.0.1:8501")

JS = """
() => {
  const out = {};
  out.st_key_classes = [...new Set([...document.querySelectorAll('[class*="st-key-"]')]
      .flatMap(e => [...e.classList].filter(c => c.startsWith('st-key-'))))].sort();
  out.testids = {};
  document.querySelectorAll('[data-testid]').forEach(e => {
    const t = e.getAttribute('data-testid'); out.testids[t] = (out.testids[t] || 0) + 1; });
  out.roles = {};
  document.querySelectorAll('[role]').forEach(e => {
    const t = e.getAttribute('role'); out.roles[t] = (out.roles[t] || 0) + 1; });
  out.buttons = [...document.querySelectorAll('button')].map(b => ({
      text: (b.innerText || '').trim().slice(0, 80), aria: b.getAttribute('aria-label'),
      testid: (b.closest('[data-testid]') || {getAttribute: () => null}).getAttribute('data-testid'),
      parent_classes: (b.closest('[class*="st-key-"]') || {classList: []}).classList
        ? [...((b.closest('[class*="st-key-"]') || {classList: []}).classList)].filter(c => c.startsWith('st-key-')) : []}));
  out.inputs = [...document.querySelectorAll('input, textarea')].map(i => ({
      type: i.type, aria: i.getAttribute('aria-label'), id: i.id,
      label: (document.querySelector('label[for="' + i.id + '"]') || {innerText: null}).innerText}));
  out.headings = [...document.querySelectorAll('h1,h2,h3')].map(h => ({tag: h.tagName, text: h.innerText.trim()}));
  out.streamlit_version = (window.__streamlit || {}).version || null;
  return out;
}
"""


def main() -> int:
    token = os.environ["MIRUM_E2E_TOKEN"]
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e/artifacts/probe")
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
        page.goto(UI_URL, wait_until="domcontentloaded")
        gate = page.get_by_label("Access token", exact=True)
        gate.wait_for(state="visible", timeout=60_000)
        result["gate"] = page.evaluate(JS)
        gate.fill(token)
        gate.press("Enter")
        page.get_by_test_id("stSidebar").get_by_text("Checked design", exact=True).click()
        page.get_by_role("heading", name="1. Prepare requirements").wait_for(timeout=60_000)
        page.wait_for_timeout(1500)
        result["beam"] = page.evaluate(JS)
        result["st_key_supported"] = any("checked_new_" in c for c in result["beam"]["st_key_classes"])
        result["version_meta"] = page.evaluate("() => (document.querySelector('meta[name=generator]') || {}).content || null")
        page.screenshot(path=str(out_dir / "probe_beam.png"), full_page=True)
        browser.close()
    (out_dir / "dom_probe.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("st_key_supported:", result["st_key_supported"])
    print("st-key classes:", result["beam"]["st_key_classes"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
