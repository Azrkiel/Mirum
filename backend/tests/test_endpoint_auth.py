"""Tests for endpoint authentication and the /run kill switch.

Exercises the real `require_api_key` middleware function directly with
minimal request stubs, rather than through fastapi.testclient — TestClient
needs httpx, which is not a project dependency and is absent in CI.

    python -m pytest backend/tests/test_endpoint_auth.py
"""

import asyncio
import os
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# main.py refuses to import without a Heavy key; the value is never used
# because no Gemini call is made in these tests.
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-key")

import main  # noqa: E402


class _FakeRequest:
    """Minimal stand-in for starlette.Request (what the middleware touches)."""

    def __init__(self, path="/generate", method="POST", headers=None, host="10.0.0.1"):
        self.url = types.SimpleNamespace(path=path)
        self.method = method
        self.headers = headers or {}
        self.client = types.SimpleNamespace(host=host)


_SENTINEL = object()


async def _call_next(_request):
    """Marker for 'the request was allowed through'."""
    return _SENTINEL


def _run(request):
    return asyncio.run(main.require_api_key(request, _call_next))


# ---------------------------------------------------------------------------
# Auth disabled (local development)
# ---------------------------------------------------------------------------

def test_no_key_configured_allows_everything(monkeypatch):
    monkeypatch.setattr(main, "_API_KEY", "")
    assert _run(_FakeRequest()) is _SENTINEL


# ---------------------------------------------------------------------------
# Auth enabled
# ---------------------------------------------------------------------------

def test_missing_header_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "_API_KEY", "s3cret")
    resp = _run(_FakeRequest())
    assert resp is not _SENTINEL
    assert resp.status_code == 401


def test_wrong_key_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "_API_KEY", "s3cret")
    resp = _run(_FakeRequest(headers={"X-API-Key": "guess"}))
    assert resp.status_code == 401


def test_correct_key_is_allowed(monkeypatch):
    monkeypatch.setattr(main, "_API_KEY", "s3cret")
    assert _run(_FakeRequest(headers={"X-API-Key": "s3cret"})) is _SENTINEL


def test_rejection_body_leaks_nothing(monkeypatch):
    monkeypatch.setattr(main, "_API_KEY", "s3cret")
    resp = _run(_FakeRequest())
    body = resp.body.decode()
    assert "s3cret" not in body
    assert "Invalid or missing API key" in body


@pytest.mark.parametrize("path", ["/generate", "/plan", "/run", "/refine",
                                  "/clarify", "/requirements", "/stats",
                                  "/pmi/annotate", "/export/parasolid",
                                  "/docs", "/openapi.json"])
def test_every_sensitive_path_requires_a_key(monkeypatch, path):
    """/docs and /openapi.json included: they enumerate the attack surface."""
    monkeypatch.setattr(main, "_API_KEY", "s3cret")
    assert _run(_FakeRequest(path=path)).status_code == 401


def test_health_is_exempt(monkeypatch):
    monkeypatch.setattr(main, "_API_KEY", "s3cret")
    assert _run(_FakeRequest(path="/health", method="GET")) is _SENTINEL


def test_cors_preflight_is_exempt(monkeypatch):
    # OPTIONS carries no auth header by design; blocking it breaks CORS
    # without adding protection (preflight reveals nothing).
    monkeypatch.setattr(main, "_API_KEY", "s3cret")
    assert _run(_FakeRequest(method="OPTIONS")) is _SENTINEL


def test_health_exempt_set_is_minimal():
    assert main._AUTH_EXEMPT_PATHS == frozenset({"/health"})


# ---------------------------------------------------------------------------
# /run kill switch
# ---------------------------------------------------------------------------

def test_run_endpoint_defaults_to_disabled():
    """Default posture must be off — /run feeds the sandbox directly."""
    env = os.environ.get("MIRUM_ENABLE_RUN_ENDPOINT", "0")
    assert env.lower() in ("0", "false", "no", ""), (
        "test environment has /run explicitly enabled; the DEFAULT is what "
        "matters here"
    )
    assert main._ENABLE_RUN_ENDPOINT is False


def _real_request(path="/run", method="POST"):
    """A genuine starlette Request — slowapi's limiter decorator inspects it."""
    from starlette.requests import Request
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("10.0.0.1", 1234),
        "server": ("testserver", 80),
        "app": main.app,
    })


def test_run_endpoint_returns_404_when_disabled(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(main, "_ENABLE_RUN_ENDPOINT", False)
    body = types.SimpleNamespace(script="import cadquery as cq\nresult = cq.Workplane()")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.run_script(body, _real_request()))
    # 404, not 403: a disabled endpoint should not advertise its existence.
    assert exc.value.status_code == 404
    # And it must bail out BEFORE the script reaches the sandbox.
    assert "sandbox" not in str(exc.value.detail).lower()


def test_enable_flag_parses_truthy_values(monkeypatch):
    import importlib
    for raw, expected in [("1", True), ("true", True), ("YES", True),
                          ("0", False), ("", False), ("off", False)]:
        monkeypatch.setenv("MIRUM_ENABLE_RUN_ENDPOINT", raw)
        reloaded = importlib.reload(main)
        assert reloaded._ENABLE_RUN_ENDPOINT is expected, f"{raw!r}"
    monkeypatch.delenv("MIRUM_ENABLE_RUN_ENDPOINT", raising=False)
    importlib.reload(main)
