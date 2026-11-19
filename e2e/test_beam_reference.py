"""Beam reference flow: examples/engineering/cantilever.json typed in through the UI, run to
acceptance, every download checked against the API. A low-allowable variant must be rejected
with no preview, no downloads and refused artifacts. Two jobs: well inside the 6-job quota."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import expect

from harness.ui import BEAM_PREVIEW_TRACE, CheckedBeam, sign_in

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((REPO_ROOT / "examples" / "engineering" / "cantilever.json").read_text(encoding="utf-8"))
SPEC = EXAMPLE["specification"]
HEIGHTS = EXAMPLE["candidate_heights_mm"]
BEAM_FORMATS = ("zip", "brief", "spec", "source", "step", "glb", "attempts", "report", "markdown", "manifest")
GEOMETRY_FORMATS = ("step", "glb", "source", "spec")
RUN_TIMEOUT_MS = 600_000


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def drive_to_run(page, ui_url, token, api, *, allowable: float | None = None):
    """Sign in, type the example (optionally with another allowable stress), review and run."""
    sign_in(page, ui_url, token)
    beam = CheckedBeam(page)
    beam.open_form()
    spec = dict(SPEC)
    if allowable is not None:
        spec["allowable_stress_mpa"] = allowable
    beam.type_requirements(spec, HEIGHTS)
    draft_id, revision, brief_sha = beam.save_draft()
    # The review caption equals what the API holds for the same draft.
    prepared = api.get_draft(draft_id).json()
    assert prepared["draft"]["draft_id"] == draft_id
    assert prepared["draft"]["revision"] == revision
    assert prepared["brief_sha256"] == brief_sha
    requirements = prepared["draft"]["content"]["requirements"]
    assert requirements["length_mm"] == spec["length_mm"]
    assert requirements["material"]["allowable_stress_mpa"] == spec["allowable_stress_mpa"]
    assert prepared["draft"]["content"]["candidate_heights_mm"] == HEIGHTS
    beam.acknowledge()
    brief_id, brief_sha_final = beam.finalize()
    brief = api.get_brief(brief_id).json()
    assert brief["draft_id"] == draft_id and brief["draft_revision"] == revision
    assert brief["brief_sha256"] == brief_sha == brief_sha_final
    job_id = beam.run_checks()
    return beam, job_id, brief_sha


def test_reference_beam_accepted_through_the_ui(stack, token, api, page, shot, external_requests, work_dir):
    beam, job_id, brief_sha = drive_to_run(page, stack.config.ui_url, token, api)
    shot(page, "beam_running")
    lifecycle, outcome = beam.wait_terminal(RUN_TIMEOUT_MS)
    assert (lifecycle, outcome) == ("completed", "accepted")
    shown_job, shown_sha = beam.job_caption()
    assert shown_job == job_id and shown_sha == brief_sha

    job = api.get_job(job_id).json()
    assert job["job_id"] == job_id and job["brief_sha256"] == brief_sha
    assert job["outcome"] == "accepted" and job["delivery"] == "available"
    attempts = [(a["candidate"]["specification"]["height_mm"], a["outcome"]) for a in job["attempts"]]
    assert attempts == [(5.0, "rejected"), (10.0, "rejected"), (15.0, "accepted")]

    expect(beam.accepted_notice).to_be_visible(timeout=30_000)
    cells = beam.attempts_cells()
    if cells is not None:  # Streamlit exposes the grid cells to the accessibility tree
        flat = " ".join(cells)
        assert flat.count("rejected") == 2 and flat.count("accepted") == 1, cells
    # "Measured STEP preview": the plotly chart renders from the captured GLB.
    expect(beam.plotly_chart.first).to_be_visible(timeout=60_000)
    assert BEAM_PREVIEW_TRACE in beam.plotly_trace_names()
    expect(beam.alerts().filter(has_text="captured preview could not be displayed")).to_have_count(0)
    shot(page, "beam_accepted")

    # Every download equals the API artifact, the bundle identity and the ZIP manifest.
    expected: dict[str, bytes] = {}
    for fmt in BEAM_FORMATS:
        response = api.artifact(job_id, fmt)
        assert response.status_code == 200, (fmt, response.status_code)
        assert sha256(response.content) == response.headers["x-content-sha256"]
        name = response.headers["content-disposition"].split('filename="')[1].rstrip('"')
        expected[name] = response.content
    assert beam.download_names() == sorted(expected)
    got = {name: beam.download(name, work_dir) for name in expected}
    for name, value in expected.items():
        assert sha256(got[name]) == sha256(value), name
    bundle = job["bundle"]
    assert sha256(got["package.zip"]) == bundle["zip_sha256"]
    assert sha256(got["manifest.json"]) == bundle["manifest_sha256"]
    manifest = json.loads(got["manifest.json"])
    with zipfile.ZipFile(io.BytesIO(got["package.zip"])) as archive:
        assert archive.read("manifest.json") == got["manifest.json"]
        for entry in manifest["payloads"]:
            assert sha256(archive.read(entry["path"])) == entry["sha256"] == sha256(got[entry["path"]])
    shot(page, "beam_downloads")
    assert external_requests == []


def test_low_allowable_stress_is_rejected_without_preview_or_geometry(
        stack, token, api, page, shot, external_requests):
    beam, job_id, brief_sha = drive_to_run(page, stack.config.ui_url, token, api, allowable=5.0)
    lifecycle, outcome = beam.wait_terminal(RUN_TIMEOUT_MS)
    assert outcome == "rejected", (lifecycle, outcome)
    expect(beam.rejected_notice).to_be_visible(timeout=30_000)
    job = api.get_job(job_id).json()
    assert job["outcome"] == "rejected"
    assert [a["outcome"] for a in job["attempts"]] == ["rejected"] * len(HEIGHTS)
    # No preview and no geometry. A rejected run offers only its diagnostic bundle.
    expect(beam.plotly_chart).to_have_count(0)
    for fmt in GEOMETRY_FORMATS:
        assert not api.artifact(job_id, fmt).is_success, fmt
    offered = beam.download_names()
    assert offered and not set(offered) & {"model.step", "preview.glb", "model.py", "selected_spec.json"}, offered
    assert set(offered) <= {"package.zip", "report.json", "brief.json", "report.md", "attempts.json", "manifest.json"}
    shot(page, "beam_rejected")
    assert external_requests == []
