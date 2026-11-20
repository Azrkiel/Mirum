"""D7 service acceptance on the release stack: three styles end to end through /dragsters/*.

Jobs per client: 4 generate calls (3 presets + the v1 example), under the 5/min limit. 422s are
rejected by validation before the limiter counts them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
STYLES = {"wedge": "dragster_wedge_v2", "teardrop": "dragster_teardrop_v2",
          "narrow_waist": "dragster_narrow_waist_v2"}
REQUIRED_CHECKS = {"single_valid_solid", "absolute_bounds", "symmetry", "reference_volume", "pocket_void",
                   "front_axle_void", "rear_axle_void", "pocket_wall", "front_bore_wall", "rear_bore_wall",
                   "spec_revalidation", "wheel_clearance_front", "wheel_clearance_rear", "body_above_ground"}


def events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_presets_endpoint_lists_three_styles_and_needs_token(api, anon_api):
    assert anon_api.get("/dragsters/presets").status_code == 401
    response = api.get("/dragsters/presets")
    assert response.status_code == 200
    styles = response.json()["styles"]
    assert [s["name"] for s in styles] == list(STYLES)
    assert all(s["preset"]["schema_version"] == 2 and s["preset"]["style"] == s["name"] for s in styles)


@pytest.mark.parametrize("style", list(STYLES))
def test_preset_generates_verified_artifacts(api, style):
    preset = next(s["preset"] for s in api.get("/dragsters/presets").json()["styles"] if s["name"] == style)
    response = api.post("/dragsters/generate", json=preset, timeout=180)
    assert response.status_code == 200, response.text[:300]
    sse = events(response)
    result = sse[-1]
    assert result["status"] == "done", result
    assert style.replace("_", " ") in sse[0]["message"]
    files = {fmt: api.get(f"/download/{result['run_id']}/{fmt}").content for fmt in ("spec", "report", "step", "glb")}
    assert result["digests"] == {fmt: hashlib.sha256(data).hexdigest() for fmt, data in files.items()}
    report = result["report"]
    assert json.loads(files["report"]) == report
    assert report["schema_version"] == 2 and report["generator_version"] == STYLES[style]
    assert REQUIRED_CHECKS <= report["checks"].keys()
    assert all(check["passed"] is True for check in report["checks"].values())
    assert report["engineering_certified"] is False
    assert files["step"].startswith(b"ISO-10303-21") and files["glb"][:4] == b"glTF"


def test_v1_example_still_generates_as_v1(api):
    payload = json.loads((REPO_ROOT / "backend/dragsters/examples/wedge.json").read_text())
    result = events(api.post("/dragsters/generate", json=payload, timeout=180))[-1]
    assert result["status"] == "done", result
    assert result["spec"] == payload
    assert result["report"]["schema_version"] == 1
    assert result["report"]["generator_version"] == "dragster_wedge_v1"


@pytest.mark.parametrize("change", [{"unexpected": 1}, {"style": "teardrop"}, {"style": "../../compiler"},
                                    {"pocket_center_z_mm": 60.0}, {"length_mm": "220"}])
def test_invalid_v2_body_is_rejected_422(api, change):
    preset = api.get("/dragsters/presets").json()["styles"][0]["preset"]
    response = api.post("/dragsters/generate", json={**preset, **change})
    assert response.status_code == 422, response.text[:300]
    assert response.json()["detail"]
