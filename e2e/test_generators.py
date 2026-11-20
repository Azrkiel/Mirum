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
    """The repository's glider-distance example mission, pinned to the server's calibration."""
    example = json.loads((FIXTURES.parents[1] / "examples" / "cem" / "glider-distance.json").read_text(encoding="utf-8"))
    metadata = api.get("/cem/glider/metadata").json()
    requirements = example["requirements"]
    requirements["reference_target"] = 50.0  # the example's 1200 m is infeasible from a 10 m drop
    requirements["discovery"]["families"] = ["conventional", "t_tail", "v_tail"]  # "all" is refused by v1
    return dict(requirements=requirements, calibration_version=metadata["calibration_version"])


def _reasons(node, path="", out=None):
    """Failure-relevant leaves of a report, for the assertion message."""
    out = [] if out is None else out
    keys = {"reason", "failed", "verdict"}
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and value:
                out.append(f"{path}.{key}={str(value)[:160]}")
            _reasons(value, f"{path}.{key}", out)
    elif isinstance(node, list):
        for n, value in enumerate(node[:12]):
            _reasons(value, f"{path}[{n}]", out)
    return out[:60]


CASES = [  # regen first: its ~125 MiB bundle needs 256 MiB of free store (512 MiB volume, never cleared)
    pytest.param("regen_chamber", lambda api: _load("regen_spec.json"), 900_000, id="regen"),
    pytest.param("co2_dragster", lambda api: _load("dragster_spec.json"), 600_000, id="dragster"),
    pytest.param("glider_cem", _glider_spec, 900_000, id="glider"),
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
    detail = ""
    if outcome != "accepted":
        report = api.artifact(job_id, "report")
        detail = _reasons(report.json()) if report.is_success else str(report.status_code)
    assert (lifecycle, outcome) == ("completed", "accepted"), (job["lifecycle"], job["outcome"], job.get("failures"), detail)
    expect(view.checked_label).to_be_visible(timeout=30_000)

    offered = [fmt for fmt, ok in job["artifacts"].items() if ok]
    assert offered, job["artifacts"]
    expected = {}
    for fmt in offered:
        response = api.artifact(job_id, fmt)
        assert response.status_code == 200, (fmt, response.status_code)
        name = response.headers["content-disposition"].split('filename="')[1].rstrip('"')
        expected[name] = hashlib.sha256(response.content).hexdigest()
    expect(view.download_buttons).to_have_count(len(expected), timeout=300_000)  # large bundles load slowly
    assert view.download_names() == sorted(expected)
    for name, digest in expected.items():
        assert hashlib.sha256(view.download(name, work_dir)).hexdigest() == digest, name
    shot(page, f"{capability_id}_downloads")
    assert external_requests == []
