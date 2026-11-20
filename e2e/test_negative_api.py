"""Refusals, review revisions and job idempotency on the release API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from harness.api import Api

pytestmark = pytest.mark.usefixtures("evidence")

REFERENCE = Path(__file__).resolve().parents[1] / "examples" / "engineering" / "cantilever.json"
BEAM_ID = "rectangular_cantilever"


@pytest.fixture
def beam_contract(api: Api) -> dict[str, Any]:
    response = api.get("/engineering/v1/capabilities")
    # backend/engineering/api_v1.py:205 uses the default GET status.
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["capabilities"] if item["id"] == BEAM_ID)


def _beam_input(contract: dict[str, Any]) -> dict[str, Any]:
    example = json.loads(REFERENCE.read_text(encoding="utf-8"))
    specification = example["specification"]
    requirements = {
        field: specification[field]
        for field in (
            "units", "length_mm", "width_mm", "dimensional_tolerance_mm", "tip_force_n",
            "max_tip_deflection_mm", "assumptions",
        )
    }
    requirements["material"] = {
        "youngs_modulus_mpa": specification["youngs_modulus_mpa"],
        "allowable_stress_mpa": specification["allowable_stress_mpa"],
        "source_kind": "illustrative",
        "reference": specification["material_reference"],
    }
    return {
        "schema_version": "engineering_brief_v1",
        "capability": {
            "id": contract["id"],
            "version": contract["version"],
            "numerical_policy_version": contract["numerical"]["version"],
            "resource_policy_version": contract["resources"]["version"],
        },
        "requirements": requirements,
        "mode": "bounded_thickness",
        "candidate_heights_mm": example["candidate_heights_mm"],
    }


def _create_beam(api: Api, payload: dict[str, Any]) -> dict[str, Any]:
    response = api.create_draft({"capability": BEAM_ID, "input": payload})
    # backend/engineering/api_v1.py:226 sets the draft creation status.
    assert response.status_code == 201, response.text
    prepared = response.json()
    assert prepared["issues"] == []
    return prepared


def _review(prepared: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "draft_id": prepared["draft"]["draft_id"],
        "draft_revision": prepared["draft"]["revision"],
        "brief_sha256": prepared["brief_sha256"],
        "acknowledged_fields": contract["review_fields"],
        "reviewed_at": "2026-11-19T12:00:00Z",
        "reviewed_by": "release-e2e",
    }


def _finalize(api: Api, review: dict[str, Any]) -> dict[str, Any]:
    response = api.finalize(review)
    # backend/engineering/api_v1.py:307 sets the successful finalization status.
    assert response.status_code == 201, response.text
    return response.json()


def _assert_no_records_returned(api: Api, result: dict[str, Any]) -> None:
    prepared = result.get("prepared") or result
    draft_id = (prepared.get("draft") or prepared).get("draft_id")
    if draft_id is not None:
        response = api.get_draft(draft_id)
        # backend/engineering/api_v1.py:123 maps a missing draft to 404.
        assert response.status_code == 404, response.text
    job_id = (result.get("job") or result).get("job_id")
    if job_id is not None:
        response = api.get_job(job_id)
        # backend/engineering/api_v1.py:123 maps a missing job to 404.
        assert response.status_code == 404, response.text
    assert result == {"detail": "Invalid or unsupported engineering input"}


def test_unsupported_capability_is_refused(api: Api, beam_contract: dict[str, Any]) -> None:
    payload = _beam_input(beam_contract)
    unsupported = "unsupported-e2e-capability"
    payload["capability"]["id"] = unsupported
    response = api.create_draft({"capability": unsupported, "input": payload})
    # backend/engineering/api_v1.py:234 raises ValueError, mapped to 422 at line 125.
    assert response.status_code == 422, response.text
    _assert_no_records_returned(api, response.json())


def test_form_input_with_prompt_is_refused(api: Api, beam_contract: dict[str, Any]) -> None:
    response = api.create_draft({
        "mode": "form", "input": _beam_input(beam_contract), "prompt": "Prepare a beam",
    })
    # backend/engineering/api_v1.py:269 raises ValueError, mapped to 422 at line 125.
    assert response.status_code == 422, response.text
    _assert_no_records_returned(api, response.json())


def test_stale_review_conflicts_after_edit(api: Api, beam_contract: dict[str, Any]) -> None:
    created = _create_beam(api, _beam_input(beam_contract))
    draft_id = created["draft"]["draft_id"]
    tab_a = api.get_draft(draft_id)
    # backend/engineering/api_v1.py:283 uses the default GET status.
    assert tab_a.status_code == 200, tab_a.text
    current = tab_a.json()
    stale_review = _review(current, beam_contract)
    revision = stale_review["draft_revision"]

    edited_input = current["input"]
    edited_input["requirements"]["tip_force_n"] += 1.0
    tab_b = api.patch_draft(draft_id, {"input": edited_input}, revision=revision)
    # backend/engineering/api_v1.py:288 uses the default PATCH status.
    assert tab_b.status_code == 200, tab_b.text
    edited = tab_b.json()
    assert edited["draft"]["revision"] == revision + 1
    assert edited["brief_sha256"] != stale_review["brief_sha256"]

    rejected = api.finalize(stale_review)
    # backend/engineering/briefs.py:279 raises RevisionConflict, mapped to 409 in api_v1.py:119.
    assert rejected.status_code == 409, rejected.text
    finalized = _finalize(api, _review(edited, beam_contract))
    assert finalized["draft_id"] == draft_id
    assert finalized["draft_revision"] == revision + 1
    assert finalized["brief_sha256"] == edited["brief_sha256"]


def test_job_idempotency_is_bound_to_one_brief(api: Api, beam_contract: dict[str, Any]) -> None:
    payload = _beam_input(beam_contract)
    brief = _finalize(api, _review(_create_beam(api, payload), beam_contract))
    payload["requirements"]["length_mm"] += 1.0
    other = _finalize(api, _review(_create_beam(api, payload), beam_contract))
    assert brief["brief_id"] != other["brief_id"]

    key = uuid4().hex
    request = {"brief_id": brief["brief_id"]}
    first = api.start_job(request, idempotency_key=key)
    # backend/engineering/api_v1.py:347 sets the job submission status.
    assert first.status_code == 202, first.text
    job_id = first.json()["job_id"]
    replay = api.start_job(request, idempotency_key=key)
    # backend/engineering/api_v1.py:347 applies to idempotent replays as well.
    assert replay.status_code == 202, replay.text
    assert replay.json()["job_id"] == job_id

    conflict = api.start_job({"brief_id": other["brief_id"]}, idempotency_key=key)
    # backend/engineering/jobs.py:131 raises RevisionConflict, mapped to 409 in api_v1.py:119.
    assert conflict.status_code == 409, conflict.text
    job = api.get_job(job_id)
    # backend/engineering/api_v1.py:360 uses the default GET status.
    assert job.status_code == 200, job.text
    assert job.json()["job_id"] == job_id
    assert job.json()["brief_sha256"] == brief["brief_sha256"]


def test_unknown_job_has_a_sanitized_not_found_response(api: Api) -> None:
    response = api.get_job(uuid4().hex)
    # backend/engineering/jobs.py:105 raises KeyError, mapped to 404 in api_v1.py:123.
    assert response.status_code == 404, response.text
    assert "traceback" not in response.text.lower()
    assert response.json() == {"detail": "Engineering record or format not found"}
