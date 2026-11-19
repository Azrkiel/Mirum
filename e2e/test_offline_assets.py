"""No request leaves loopback: every workflow page is loaded under the egress guard, and the
served HTML, CSS and JS name no non-loopback resource the browser would fetch."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from playwright.sync_api import expect

from harness.guard import is_loopback
from harness.ui import WORKFLOWS, Workspace, sign_in

TAG_URL = re.compile(r"""<(?:script|link|img|iframe|source|video|audio|embed)\b[^>]*?\b(?:src|href)=["']([^"']+)["']""", re.I)
CSS_URL = re.compile(r"""(?:url\(\s*["']?|@import\s+["'])(https?://[^"')\s]+)""", re.I)
JS_FETCH = re.compile(r"""(?:fetch|importScripts|import|WebSocket|sendBeacon)\(\s*["'`](?:https?|wss?)://([^/"'`:]+)""")


def _non_loopback(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https", "ws", "wss") and not is_loopback(url)


def test_every_workflow_page_loads_without_leaving_loopback(stack, token, page, shot, external_requests):
    sign_in(page, stack.config.ui_url, token)
    workspace = Workspace(page)
    for name in WORKFLOWS:
        workspace.select_workflow(name)
        page.wait_for_load_state("networkidle")
        shot(page, "page_" + re.sub(r"\W+", "_", name))
    assert external_requests == []


def test_served_assets_name_no_external_resource(stack):
    base = stack.config.ui_url
    html = httpx.get(base, timeout=30).text
    found = [u for u in TAG_URL.findall(html) if _non_loopback(urljoin(base, u))]
    assert found == [], found
    sources = [urljoin(base + "/", u) for u in TAG_URL.findall(html)]
    scanned = 0
    for url in sources:
        if not url.startswith(base) or not re.search(r"\.(js|css)(\?|$)", url):
            continue
        body = httpx.get(url, timeout=60).text
        scanned += 1
        assert [u for u in CSS_URL.findall(body) if _non_loopback(u)] == [], url
        hosts = [h for h in JS_FETCH.findall(body) if h not in ("127.0.0.1", "localhost")]
        assert hosts == [], (url, hosts)
    assert scanned > 0
