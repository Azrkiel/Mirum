"""Exercise engineering requests against an in-memory HTTP transport."""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.api import Api, TOKEN_HEADER


def test_anonymous_shares_transport_and_omits_token() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Test"] == "probe"
        status = 200 if request.headers.get(TOKEN_HEADER) == "test" else 401
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handle), headers={TOKEN_HEADER: "test"}) as client:
        api = Api("http://api/", "test", client=client)
        anon = api.anonymous()
        assert anon.get("/auth/check", headers={"X-Test": "probe"}).status_code == 401
        anon.close()
        assert api.get("/auth/check", headers={"X-Test": "probe"}).status_code == 200


def test_engineering_routes_and_bodies() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers[TOKEN_HEADER] == "test"
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"capabilities": []})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        api = Api("http://api", "test", client=client)
        assert api.capabilities() == {"capabilities": []}
        body = {"input": {"length": 1}}
        api.create_draft(body, idempotency_key="unused")
        api.get_draft("d")
        api.patch_draft("d", body, revision=2)
        assert "expected_revision" not in body
        api.finalize({"draft_id": "d"})
        api.get_brief("b")
        job = {"brief_id": "b"}
        api.start_job(job, idempotency_key="k")
        assert "idempotency_key" not in job
        api.get_job("j")
        api.artifact("j", "step")
    expected = [
        ("GET", "/capabilities", None), ("POST", "/drafts", body), ("GET", "/drafts/d", None),
        ("PATCH", "/drafts/d", {**body, "expected_revision": 2}), ("POST", "/briefs", {"draft_id": "d"}),
        ("GET", "/briefs/b", None), ("POST", "/jobs", {"brief_id": "b", "idempotency_key": "k"}),
        ("GET", "/jobs/j", None), ("GET", "/jobs/j/artifacts/step", None),
    ]
    assert calls == [(method, "/engineering/v1" + path, payload) for method, path, payload in expected]


@pytest.mark.parametrize("status", [409, 422, 503])
def test_http_failures_return_response(status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    with httpx.Client(transport=transport) as client:
        api = Api("http://api", None, client=client)
        assert api.patch_draft("d", {"input": {}}, revision=1).status_code == status


def test_transport_error_redacts_token() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.TransportError("transport rejected " + request.headers[TOKEN_HEADER])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        api = Api("http://api", "sensitive", client=client)
        with pytest.raises(RuntimeError) as exc:
            api.get("/auth/check")
        assert str(exc.value) == "transport rejected ***"


@pytest.mark.parametrize("terminal", ["completed", "unavailable", "cancelled", None])
def test_wait_job(terminal: str | None) -> None:
    elapsed = 0.0
    states = iter(["admitted", "running", "packaging", terminal or "running"])

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/engineering/v1/jobs/j"
        return httpx.Response(200, json={"job_id": "j", "lifecycle": next(states, "running")})

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        assert 0 < seconds <= 2
        elapsed += seconds

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        api = Api("http://api", None, client=client, clock=lambda: elapsed, sleep=sleep)
        if terminal:
            assert api.wait_job("j", timeout_s=7) == {"job_id": "j", "lifecycle": terminal}
            assert elapsed == 6
        else:
            with pytest.raises(TimeoutError, match="j"):
                api.wait_job("j", timeout_s=7)
            assert elapsed == 7
