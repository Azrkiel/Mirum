"""Generators (dragster, glider, regen) through the v1 form: JSON specification draft, save,
acknowledge, finalize, run. Accepted runs show the 'Checked design' label and verified downloads.
Three jobs per module (quota is 6 per client per hour; the backend restarts per module)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from harness.ui import CheckedGenerator, sign_in

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _glider_spec(api) -> dict:
    metadata = api.get("/cem/glider/metadata").json()
    return dict(requirements=dict(launch=dict(method="drop"), discovery=dict(families=["conventional"])),
                calibration_version=metadata["calibration_version"])


CASES = [
    pytest.param("co2_dragster", lambda api: _load("dragster_spec.json"), 600_000, id="dragster"),
    pytest.param("glider_cem", _glider_spec, 900_000, id="glider"),
    pytest.param("regen_chamber", lambda api: _load("regen_spec.json"), 900_000, id="regen",
                 marks=pytest.mark.xfail(strict=True, reason="needs #84")),
]


@pytest.mark.parametrize("capability_id,make_spec,timeout_ms", CASES)
def test_generator_checked_run(capability_id, make_spec, timeout_ms, stack, token, api, page, shot,
                               external_requests, work_dir):
    entry = next(c for c in api.capabilities()["capabilities"] if c["id"] == capability_id)
    specification = make_spec(api)
    sign_in(page, stack.config.ui_url, token)
    view = CheckedGenerator(page)
    view.open_capability(entry["label"])
    view.start_empty_draft()
    draft_id, revision, brief_sha = view.save_specification_json(json.dumps(specification))
    shot(page, f"{capability_id}_draft")

    prepared = api.get_draft(draft_id).json()
    assert prepared["draft"]["revision"] == revision and prepared["brief_sha256"] == brief_sha
    assert prepared["issues"] == []

    view.acknowledge()
    brief_id, final_sha = view.finalize()
    assert final_sha == brief_sha
    job_id = view.run_checks()
    shot(page, f"{capability_id}_running")

    lifecycle, outcome = view.wait_terminal(timeout_ms)
    shot(page, f"{capability_id}_result")
    job = api.get_job(job_id).json()
    assert (lifecycle, outcome) == ("completed", "accepted"), (job["lifecycle"], job["outcome"], job.get("failures"))
    expect(view.checked_label).to_be_visible(timeout=30_000)

    offered = [fmt for fmt, ok in job["artifacts"].items() if ok]
    assert offered, job["artifacts"]
    expected = {}
    for fmt in offered:
        response = api.artifact(job_id, fmt)
        assert response.status_code == 200, (fmt, response.status_code)
        name = response.headers["content-disposition"].split('filename="')[1].rstrip('"')
        expected[name] = hashlib.sha256(response.content).hexdigest()
    assert view.download_names() == sorted(expected)
    for name, digest in expected.items():
        assert hashlib.sha256(view.download(name, work_dir)).hexdigest() == digest, name
    shot(page, f"{capability_id}_downloads")
    assert external_requests == []
