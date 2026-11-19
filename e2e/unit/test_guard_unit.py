import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.guard import guarded_context, is_loopback


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8501/", "http://localhost:8000/x", "http://[::1]:8501/", "ws://127.0.0.1:8501/_stcore/stream",
    "data:text/plain,hi", "blob:http://127.0.0.1:8501/abc", "about:blank",
])
def test_loopback_allowed(url):
    assert is_loopback(url)


@pytest.mark.parametrize("url", [
    "https://example.com/", "http://127.0.0.1.evil.com/", "http://localhost.evil.com/", "http://evil.com/127.0.0.1",
    "http://user@evil.com:80/", "http://127.0.0.2/", "http://0.0.0.0:8000/", "wss://cdn.example/s", "ftp://x/", "", "http://[bad",
])
def test_external_denied(url):
    assert not is_loopback(url)


class _Route:
    def __init__(self, url):
        self.request = type("R", (), {"url": url})()
        self.did = None
    def continue_(self): self.did = "continue"
    def abort(self): self.did = "abort"


class _WS:
    def __init__(self, url): self.url, self.did = url, None
    def connect_to_server(self): self.did = "connect"
    def close(self): self.did = "close"


class _Ctx:
    def route(self, pattern, fn): self.http = fn
    def route_web_socket(self, pattern, fn): self.ws = fn


class _Browser:
    def new_context(self, **kw):
        self.ctx = _Ctx()
        return self.ctx


def test_guard_records_and_aborts_http_and_ws():
    ext, b = [], _Browser()
    guarded_context(b, ext)
    ok, bad = _Route("http://127.0.0.1:8501/a"), _Route("https://cdn.example/a.js")
    b.ctx.http(ok); b.ctx.http(bad)
    assert (ok.did, bad.did) == ("continue", "abort")
    wok, wbad = _WS("ws://127.0.0.1:8501/s"), _WS("wss://evil.example/s")
    b.ctx.ws(wok); b.ctx.ws(wbad)
    assert (wok.did, wbad.did) == ("connect", "close")
    assert ext == ["https://cdn.example/a.js", "wss://evil.example/s"]
