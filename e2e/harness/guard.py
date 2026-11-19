"""Egress guard shared by conftest and the DOM probe: abort and record non-loopback traffic."""
from __future__ import annotations

from urllib.parse import urlparse

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOCAL_SCHEMES = {"data", "blob", "about"}


def is_loopback(url: str) -> bool:
    """True for loopback http(s)/ws(s) URLs and non-network schemes (data:, blob:, about:)."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except ValueError:
        return False
    if parsed.scheme in LOCAL_SCHEMES:
        return True
    return host in LOOPBACK_HOSTS


def guarded_context(browser, external: list[str], **kwargs):
    """New browser context that aborts (and records into ``external``) every request and
    WebSocket that leaves loopback."""
    context = browser.new_context(**kwargs)

    def guard(route):
        url = route.request.url
        if is_loopback(url):
            route.continue_()
        else:
            external.append(url)
            route.abort()

    def guard_ws(ws):
        if is_loopback(ws.url):
            ws.connect_to_server()
        else:
            external.append(ws.url)
            ws.close()

    context.route("**/*", guard)
    context.route_web_socket("**/*", guard_ws)
    return context
