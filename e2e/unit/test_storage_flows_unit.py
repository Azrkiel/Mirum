"""Exercise the storage harness with an offline engineering API."""

import hashlib
import json
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.api import Api, TOKEN_HEADER
from harness.storage_flows import (
    CaseLog,
    artifact_digests,
    beam_brief,
    classify_429,
    regen_brief,
    submit,
    wait_for,
)

_V1 = "/engineering/v1"
_TOKEN = "mock-access-token"
_BEAM = "rectangular_cantilever"
_REGEN = "regen_chamber"


@pytest.mark.parametrize("variant", ["reference", "low_allowable"])
def test_beam_brief_reviews_current_draft(variant: str) -> None:
    expected_input = json.loads(
        (Path(__file__).resolve().parents[2] / "examples/engineering/cantilever.json").read_text(
            encoding="utf-8"
        )
    )
    if variant == "low_allowable":
        expected_input["specification"]["allowable_stress_mpa"] = 1.0
    fields = ["specification.allowable_stress_mpa", "candidate_heights_mm"]
    entry = {"id": _BEAM, "review_fields": fields}
    _exercise_brief(beam_brief, variant, entry, expected_input, {}, fields)


def test_regen_brief_reviews_normalized_specification_leaves() -> None:
    entry = {
        "id": _REGEN,
        "version": "cap-v",
        "numerical": {"version": "num-v"},
        "resources": {"version": "res-v"},
        "review_fields": [],
        "review_fields_prefix": "specification.",
    }
    expected_input = {
        "capability": {
            "id": _REGEN,
            "version": "cap-v",
            "numerical_policy_version": "num-v",
            "resource_policy_version": "res-v",
        },
        "specification": {
            "family": _REGEN,
            "units": "mm",
            "assumptions": "geometry_only_no_gas_dynamics_thermal_or_structural_analysis",
            "chamber_radius_mm": 35.0,
            "chamber_length_mm": 40.0,
            "convergent_length_mm": 50.0,
            "throat_radius_mm": 20.0,
            "divergent_length_mm": 80.0,
            "exit_radius_mm": 45.0,
            "wall_thickness_mm": 7.0,
            "channel_count": 32,
            "channel_radius_mm": 1.0,
            "channel_twist_rad_per_mm": 0.012,
            "manifold_radius_mm": 1.5,
            "end_margin_mm": 8.0,
            "voxel_size_mm": 0.4,
        },
    }
    normalized = {"b": {"z": [1, 2], "a": {}}, "a": 2}
    fields = ["specification.a", "specification.b.a", "specification.b.z"]
    _exercise_brief(regen_brief, "reference", entry, expected_input, normalized, fields)


def _exercise_brief(
    helper: Callable[[Api, str], dict], variant: str, entry: dict,
    expected_input: dict, normalized: dict, fields: list[str],
) -> None:
    calls = []
    brief = {"brief_id": "reviewed", "brief_sha256": "latest-sha"}

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers[TOKEN_HEADER] == _TOKEN
        path = request.url.path.removeprefix(_V1)
        calls.append((request.method, path))
        if path == "/capabilities":
            return httpx.Response(200, json={
                "capabilities": [{"id": "other", "review_fields": ["other"]}, entry],
                "review_fields": ["legacy-top-level"],
            })
        if path == "/drafts":
            body = json.loads(request.content)
            assert body.pop("mode", "form") == "form"
            assert body == {"capability": entry["id"], "input": expected_input}
            return httpx.Response(201, json={
                "draft": {"draft_id": "draft", "revision": 1}, "brief_sha256": "old-sha",
            })
        if path == "/drafts/draft":
            return httpx.Response(200, json={
                "draft": {
                    "draft_id": "draft", "revision": 2, "content": {"specification": normalized},
                },
                "brief_sha256": "latest-sha",
            })
        assert path == "/briefs"
        review = json.loads(request.content)
        stamp = datetime.fromisoformat(review.pop("reviewed_at").replace("Z", "+00:00"))
        assert stamp.utcoffset() == timedelta(0)
        assert review.pop("reviewed_by").strip()
        assert review == {
            "draft_id": "draft", "draft_revision": 2, "brief_sha256": "latest-sha",
            "acknowledged_fields": fields,
        }
        return httpx.Response(201, json=brief)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert helper(Api("http://api", _TOKEN, client=client), variant) == brief
    assert calls == [
        ("GET", "/capabilities"), ("POST", "/drafts"), ("GET", "/drafts/draft"), ("POST", "/briefs"),
    ]


@pytest.mark.parametrize("path", ["/capabilities", "/drafts", "/drafts/draft", "/briefs"])
def test_brief_http_failure_reports_redacted_excerpt(path: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.removeprefix(_V1)
        if endpoint == path:
            return httpx.Response(503, text=f"service rejected {_TOKEN} " + "x" * 1000)
        if endpoint == "/capabilities":
            return httpx.Response(200, json={"capabilities": [{"id": _BEAM, "review_fields": []}]})
        assert endpoint in {"/drafts", "/drafts/draft"}
        return httpx.Response(201 if endpoint == "/drafts" else 200, json={
            "draft": {"draft_id": "draft", "revision": 1}, "brief_sha256": "sha",
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(AssertionError, match="503.*service rejected") as exc:
            beam_brief(Api("http://api", _TOKEN, client=client))
    assert _TOKEN not in str(exc.value)
    assert len(str(exc.value)) < 300


@pytest.mark.parametrize("status", [429, 503])
def test_submit_sends_once_on_failure(status: int) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(status, headers={"Retry-After": "5"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        response = submit(Api("http://api", _TOKEN, client=client), "brief", "key")
    assert response.status_code == status
    assert calls == [("POST", _V1 + "/jobs", {"brief_id": "brief", "idempotency_key": "key"})]


@pytest.mark.parametrize("status, body, retry_after, expected", [
    (429, {"detail": "Engineering capacity busy; retry later"}, "5", "admission_busy"),
    (429, {"detail": "Engineering capacity busy; retry after 5 seconds"}, "5", "admission_busy"),
    (429, {"detail": "Engineering storage capacity exhausted; retry after expiry"}, "5", "storage_exhausted"),
    (429, {"detail": "Engineering job quota reached for this client"}, "5", "client_quota"),
    (429, {"detail": "Another client is waiting for engineering capacity"}, "20", "client_quota"),
    (429, {"detail": "Engineering capacity busy; retry later"}, "3599", "client_quota"),
    (429, {"error": "Rate limit exceeded: 10 per 1 minute"}, None, "rate_limit"),
    (429, {"detail": "unclassified"}, "5", "unknown"),
    (503, {"error": "Rate limit exceeded: 10 per 1 minute"}, None, "unknown"),
])
def test_classify_429(status: int, body: dict, retry_after: str | None, expected: str) -> None:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    response = httpx.Response(status, json=body, headers=headers)
    assert classify_429(response) == expected


@pytest.mark.parametrize("reaches_running", [True, False])
def test_wait_for_specific_state(reaches_running: bool) -> None:
    elapsed = 0.0
    states = iter(["admitted", "running"] if reaches_running else ["running", "unavailable"])
    polls = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == _V1 + "/jobs/job"
        polls.append(elapsed)
        return httpx.Response(200, json={"job_id": "job", "lifecycle": next(states)})

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        api = Api("http://api", _TOKEN, client=client)
        options = {"timeout_s": 1.5, "clock": lambda: elapsed, "sleep": sleep}
        if reaches_running:
            assert wait_for(api, "job", "running", **options) == {"job_id": "job", "lifecycle": "running"}
        else:
            with pytest.raises(TimeoutError, match="unavailable"):
                wait_for(api, "job", "completed", **options)
    assert polls == [0.0, 1.0]
    assert elapsed == (1.0 if reaches_running else 1.5)


@pytest.mark.parametrize("mismatch", [False, True])
def test_artifact_digests(mismatch: bool) -> None:
    bodies = {"step": b"step content", "zip": b"package content"}
    expected = {fmt: hashlib.sha256(body).hexdigest() for fmt, body in bodies.items()}

    def handle(request: httpx.Request) -> httpx.Response:
        fmt = request.url.path.removeprefix(_V1 + "/jobs/job/artifacts/")
        headers = {}
        if fmt == "zip":
            headers["X-Content-SHA256"] = "0" * 64 if mismatch else expected[fmt]
        return httpx.Response(200, content=bodies[fmt], headers=headers)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        api = Api("http://api", _TOKEN, client=client)
        if mismatch:
            with pytest.raises(AssertionError, match="SHA256"):
                artifact_digests(api, "job", bodies)
        else:
            assert artifact_digests(api, "job", bodies) == expected


def test_case_log_schema_and_deterministic_bytes(tmp_path: Path) -> None:
    cases = CaseLog(tmp_path, "source", {"frontend": "image-f", "backend": "image-b"})
    cases.record("S01", "cancel", "R", {"running": True})
    cases.record("S08a", "rename", "F", {"status": 503})
    cases.record("S10", "update expiry", "A", {"status": 410}, expiry_accelerated=False)
    path = cases.write()
    expected = {
        "source_sha": "source",
        "image_ids": {"backend": "image-b", "frontend": "image-f"},
        "cases": [
            {"case_id": "S01", "mechanism": "cancel", "kind": "R", "expiry_accelerated": False,
             "observations": {"running": True}},
            {"case_id": "S08a", "mechanism": "rename", "kind": "F", "expiry_accelerated": False,
             "observations": {"status": 503}},
            {"case_id": "S10", "mechanism": "update expiry", "kind": "A", "expiry_accelerated": True,
             "observations": {"status": 410}},
        ],
    }
    assert path == tmp_path / "storage-cases.json"
    contents = (json.dumps(expected, sort_keys=True, indent=2) + "\n").encode()
    assert path.read_bytes() == contents
    assert cases.write().read_bytes() == contents


def test_case_log_rejects_invalid_kind(tmp_path: Path) -> None:
    cases = CaseLog(tmp_path, "source", {})
    with pytest.raises(ValueError, match="kind"):
        cases.record("S01", "cancel", "invalid", {})


@pytest.mark.parametrize("secret", ["mock-secret", 'mock"quoted', "mock\nline"])
def test_case_log_redaction_preserves_previous_file(tmp_path: Path, secret: str) -> None:
    cases = CaseLog(tmp_path, "source", {}, redact=[secret])
    path = cases.write()
    original = path.read_bytes()
    cases.record("S01", "cancel", "R", {"nested": {secret: "blocked"}})
    with pytest.raises(ValueError) as exc:
        cases.write()
    assert secret not in str(exc.value)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
