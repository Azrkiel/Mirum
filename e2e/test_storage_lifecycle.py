"""Release-image lifecycle cases S01–S06, on the workflow's backend-only stack."""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from harness.api import Api
from harness.ops import Ops
from harness.stack import Stack, StackConfig
from harness.storage_flows import CaseLog, beam_brief, classify_429, regen_brief, submit, wait_for

pytestmark = pytest.mark.lifecycle

ROOT = Path(__file__).resolve().parents[1]
BUILDER = "/opt/regen/app/builder"
INTERRUPTED = "Process interrupted; evidence was not committed"
CASES = {
    "S01": ("Cancel a running beam", "R"),
    "S02": ("Gracefully stop the backend during regen", "R"),
    "S03": ("SIGSTOP the builder and observe the real 420 s deadline", "F"),
    "S04a": ("Kill the backend during a beam run", "R"),
    "S04b": ("Stop uvicorn at bundle rename, then kill the backend", "R"),
    "S05": ("Inspect /proc and docker top after each interruption", "R"),
    "S06": ("Replay interrupted jobs across recovery", "R"),
}


@dataclass
class Run:
    ops: Ops
    api: Api
    deadline: float
    evidence: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class Job:
    case: str
    id: str
    brief: str
    key: str
    state: str
    owner: str
    primary_format: str


@contextmanager
def _budget(run: Run, seconds: int) -> Iterator[None]:
    """Bound the whole operation, including Compose and sidecar subprocesses on CI Linux."""
    def expired(signum: int, frame: object) -> None:
        pytest.fail("Lifecycle case or 30-minute module time budget exhausted")

    remaining = min(seconds, run.deadline - time.monotonic())
    if remaining <= 0:
        pytest.fail("Lifecycle module time budget exhausted")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _healthy(api: Api) -> None:
    deadline = time.monotonic() + 180
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            response = api.get("/auth/check", timeout=min(remaining, 4))
        except httpx.TransportError:
            pass
        else:
            if response.status_code == 200:
                return
            assert response.status_code != 429, "Backend health probe was rate-limited"
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    pytest.fail("Authenticated backend health did not reach HTTP 200 within 180 s")


@pytest.fixture(scope="module")
def lifecycle_run() -> Iterator[Run]:
    token = os.environ["MIRUM_E2E_TOKEN"]
    cfg = StackConfig(ROOT, (ROOT / "compose.release.yml", ROOT / "e2e/compose/e2e.override.yml"),
                      os.environ["MIRUM_E2E_PROJECT"], token)
    stack = Stack(cfg)
    run = Run(Ops(stack), Api(cfg.api_url, token, timeout=5), time.monotonic() + 1800)
    log = CaseLog(ROOT / "e2e/artifacts/storage/lifecycle", os.environ.get("GITHUB_SHA") or "local",
                  stack.image_ids(), redact=[token])
    try:
        _healthy(run.api)
        yield run
    finally:
        run.api.close()
        for case, (mechanism, kind) in CASES.items():
            observations = run.evidence.get(case, {"status": "not exercised"})
            log.record(case, mechanism, kind, {**observations, "ops_notes": run.ops.notes})
        log.write()


def _start(run: Run) -> None:
    run.ops.start("backend", 90)
    _healthy(run.api)


def _restart(run: Run) -> None:
    run.ops.stop("backend", 30)
    _start(run)


def _owner(ops: Ops) -> str:
    # jobs.py:91-98 rotates this durable owner at each backend start.
    rows = ops.db_rows("SELECT value FROM metadata WHERE key='process_owner'")
    assert len(rows) == 1 and rows[0][0], "Missing durable process owner"
    return rows[0][0]


def _submit(api: Api, brief: str, key: str) -> dict:
    response = submit(api, brief, key)
    # api_v1.py:347-358 returns 202 even for an existing idempotency key.
    assert response.status_code == 202, f"Admission HTTP {response.status_code} ({classify_429(response)})"
    return response.json()


def _begin(run: Run, case: str, obs: dict) -> Job:
    beam = case in {"S01", "S04a"}
    owner = _owner(run.ops)
    brief = (beam_brief(run.api) if beam else regen_brief(run.api))["brief_id"]
    key = f"{case}-{uuid4().hex}"
    admitted = _submit(run.api, brief, key)
    assert admitted["lifecycle"] == "admitted", "Fresh key did not admit a new job"
    job = Job(case, admitted["job_id"], brief, key,
              "cancelled" if case in {"S01", "S02"} else "unavailable",
              owner, "step" if beam else "generator-cleaned-stl")
    obs.update(job_id=job.id, owner_before=owner)
    wait_for(run.api, job.id, "running", 15, poll_s=0.5)
    return job


def _worker(run: Run, job: Job) -> int:
    # process_runner.py:182-184 tags beam children. regen_cli.py:60 runs the builder directly.
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        processes = run.ops.processes("backend")
        if job.primary_format == "step":
            matches = [p for p in processes if p["has_token"] and p["owner"] == job.owner]
        else:
            matches = [p for p in processes if BUILDER in p["argv"]]
        response = run.api.get_job(job.id)
        assert response.status_code == 200
        assert response.json()["lifecycle"] == "running", f"{job.case} missed the active worker window"
        if matches:
            return matches[0]["pid"]
        time.sleep(0.5)
    pytest.fail(f"{job.case} did not observe its running worker within 50 s")


def _deadline(run: Run, job: Job, pid: int, obs: dict) -> None:
    run.ops.signal("backend", pid, "STOP")
    state = run.ops.exec_py("backend", f"print(open('/proc/{pid}/stat').read().rsplit(')', 1)[1].split()[0])")
    assert state.strip() == "T", "Builder did not enter SIGSTOP state"
    obs["builder_state"] = state.strip()
    # Each poll must answer successfully while the builder is stopped. No synthetic clock or expiry.
    assert run.api.get("/auth/check").status_code == 200
    active = run.api.get_job(job.id)
    assert active.status_code == 200 and active.json()["lifecycle"] == "running"
    terminal = run.api.wait_job(job.id, timeout_s=465, poll_s=5)
    elapsed = (datetime.fromisoformat(terminal["updated_at"].replace("Z", "+00:00"))
               - datetime.fromisoformat(terminal["created_at"].replace("Z", "+00:00"))).total_seconds()
    obs.update(terminal_elapsed_s=elapsed, deadline_observation_kind="R")
    # jobs.py:65,176,246-247 gives search a 1 s cleanup margin; search.py:42-46 supplies the reason.
    assert elapsed >= 419, "Unavailable before the real deadline window"
    assert terminal["lifecycle"] == "unavailable"
    assert "Overall engineering deadline exceeded" in terminal["failures"]


def _crash_at_rename(run: Run, job: Job, obs: dict) -> None:
    pid = run.ops.api_host_pid("backend")
    try:
        seen = run.ops.host_poll_then_stop(f"bundles/{job.id}", pid, 220)
        obs.update(api_host_pid=pid, bundle_seen=seen)
        assert seen, "S04b missed the bundle rename window"
        # bundles.py:308-317 can already have a publication row. The job's terminal
        # commit (jobs.py:254-256) must still be pending while uvicorn is frozen.
        frozen = _stored_job(run.ops, job.id)
        obs["frozen_lifecycle"] = frozen["lifecycle"]
        assert frozen["lifecycle"] == "packaging" and frozen["outcome"] == "not_evaluated", (
            "S04b missed the pre-commit window"
        )
    finally:
        run.ops.kill("backend", "KILL")
        obs["exit_code"] = run.ops.exit_code("backend")
        _start(run)
    assert obs["exit_code"] == 137


def _stored_job(ops: Ops, job_id: str) -> dict:
    rows = ops.db_rows("SELECT record FROM jobs WHERE id=?", (job_id,))
    assert len(rows) == 1, "Expected one durable job record"
    return json.loads(rows[0][0])


def _interrupt(run: Run, job: Job, obs: dict) -> None:
    pid = _worker(run, job)
    obs["worker_pid"] = pid
    if job.case == "S01":
        # api_v1.py:366-370 and jobs.py:187-195,213-214 persist cancellation before commit.
        response = run.api.post(f"/engineering/v1/jobs/{job.id}/cancel")
        assert response.status_code == 200 and response.json()["lifecycle"] == "running"
    elif job.case == "S02":
        # main.py:185 and jobs.py:286-292 drain cancelled tasks during graceful shutdown.
        obs["stop_elapsed_s"] = run.ops.stop("backend", 60)
        obs["exit_code"] = run.ops.exit_code("backend")
        _start(run)
        # uvicorn 0.30.6 re-raises the captured SIGTERM after its graceful shutdown, so a clean
        # stop reports 143 (128+15), not 0. 137 would mean the grace period expired (SIGKILL).
        assert obs["exit_code"] in (0, 143)
        assert obs["stop_elapsed_s"] < 55
    elif job.case == "S03":
        _deadline(run, job, pid, obs)
    elif job.case == "S04a":
        run.ops.kill("backend", "KILL")
        try:
            obs["exit_code"] = run.ops.exit_code("backend")
            obs["crash_lifecycle"] = _stored_job(run.ops, job.id)["lifecycle"]
        finally:
            _start(run)
        assert obs["exit_code"] == 137
        assert obs["crash_lifecycle"] == "running", "S04a missed the running crash window"
    else:
        _crash_at_rename(run, job, obs)


def _no_orphans(run: Run, job: Job) -> dict:
    """Require only init, the API, and identifiable inspection/health helpers."""
    rows = run.ops.processes("backend")
    roles = []
    for row in rows:
        argv = row["argv"]
        name = Path(argv[0]).name if argv else ""
        assert not row["has_token"] and row["owner"] is None, f"Worker tag on PID {row['pid']}"
        assert BUILDER not in argv, "Regen builder survived interruption"
        if row["pid"] == 1 and name in {"tini", "docker-init"}:
            roles.append("init")
        elif row["ppid"] == 1 and argv[1:4] == ["-m", "uvicorn", "main:app"]:
            roles.append("api")
        elif (name.startswith("python") and len(argv) == 3 and argv[1] == "-c"
              and ('Path("/proc").iterdir()' in argv[2] or "/auth/check" in argv[2])):
            roles.append("probe")
        else:
            pytest.fail(f"Unexpected process after {job.case}: PID {row['pid']}")
    assert roles.count("init") == roles.count("api") == 1
    # Stack's command runner redacts output. docker top sees host PIDs, not namespace PIDs.
    top = run.ops.stack._run(["docker", "top", run.ops.container_id("backend"), "-eo", "pid,args"])
    names = []
    for line in top.splitlines()[1:]:
        _, args = line.strip().split(maxsplit=1)
        exe = Path(args.split()[0]).name
        if exe in {"docker-init", "tini"}:
            names.append("init")
        elif args.split()[1:4] == ["-m", "uvicorn", "main:app"]:
            names.append("api")
        elif exe.startswith("python") and " -c " in args and "/auth/check" in args:
            names.append("health")
        else:
            pytest.fail(f"Unexpected process in docker top after {job.case}")
    assert names.count("init") == names.count("api") == 1
    return {"proc_roles": roles, "docker_top_roles": names, "owner_before": job.owner}


def _no_bundle(run: Run, job: Job) -> dict:
    # bundles.py:434-457 discards orphan accepted directories and publication rows.
    raw = run.ops.sidecar(
        "import json, os\nfrom pathlib import Path\nroot = Path('/tmp/mirum/engineering')\n"
        "print(json.dumps([len(list((root/'staging').iterdir())), "
        f"os.path.lexists(root/'bundles'/{job.id!r})]))"
    )
    staging, exists = json.loads(raw)
    rows = run.ops.db_rows("SELECT COUNT(*) FROM publications WHERE job=?", (job.id,))
    assert staging == 0 and exists is False and rows == [[0]], "Staging, bundle or publication survived"
    return {"staging_entries": staging, "bundle_exists": exists, "publications": rows[0][0]}


def _never_accepted(run: Run, job: Job) -> dict:
    # jobs.py:128-133,170-171 returns the durable run on replay. api_v1.py:378-391 denies downloads.
    current = run.api.get_job(job.id)
    assert current.status_code == 200
    record = current.json()
    assert record["job_id"] == job.id and record["lifecycle"] == job.state
    assert record["outcome"] == "not_evaluated" and record["bundle"] is None and not record["artifacts"]
    replay = _submit(run.api, job.brief, job.key)
    assert replay == record, "Same key changed the interrupted job"
    downloads = {fmt: run.api.artifact(job.id, fmt).status_code for fmt in ("zip", job.primary_format)}
    allowed = {404} if job.case == "S04b" else {404, 503}
    assert all(code in allowed for code in downloads.values()), f"Unexpected artifact status: {downloads}"
    return {"job_id": job.id, "lifecycle": job.state, "outcome": record["outcome"],
            "updated_at": record["updated_at"], "failures": record["failures"], "downloads": downloads}


@pytest.fixture(scope="module", params=("S01", "S02", "S03", "S04a", "S04b"))
def interrupted(lifecycle_run: Run, request: pytest.FixtureRequest) -> Job:
    run, case = lifecycle_run, request.param
    obs = run.evidence[case] = {"status": "started"}
    with _budget(run, 540 if case == "S03" else 600):
        job = _begin(run, case, obs)
        _interrupt(run, job, obs)
        terminal = run.api.wait_job(job.id, timeout_s=35, poll_s=1)
        obs.update(lifecycle=terminal["lifecycle"], outcome=terminal["outcome"], failures=terminal["failures"])
        assert terminal["lifecycle"] == job.state and terminal["outcome"] == "not_evaluated"
        if case in {"S04a", "S04b"}:
            # jobs.py:80-89 recovers a non-terminal job before discarding orphan bytes.
            assert INTERRUPTED in terminal["failures"]
        if case in {"S02", "S04a", "S04b"}:
            obs["owner_after"] = _owner(run.ops)
            assert obs["owner_after"] != job.owner
        # Capture S05 here, before P5 or another restart could hide a surviving child.
        inspection = run.evidence.setdefault("S05", {})[case] = {"job_id": job.id, "status": "started"}
        inspection.update(_no_orphans(run, job), status="observed")
        obs["volume"] = _no_bundle(run, job)
        obs["replay"] = _never_accepted(run, job)
        if case == "S01":
            # jobs.py:273-279 releases the slot. P5 must precede any recovery restart.
            assert _owner(run.ops) == job.owner, "Backend restarted before the P5 admission"
            fresh = _submit(run.api, job.brief, f"P5-{uuid4().hex}")
            assert fresh["job_id"] != job.id
            completed = run.api.wait_job(fresh["job_id"], timeout_s=280)
            obs["fresh_beam"] = {"job_id": fresh["job_id"], "outcome": completed["outcome"]}
            assert completed["lifecycle"] == "completed" and completed["outcome"] == "accepted"
    return job


def test_interruption(lifecycle_run: Run, interrupted: Job) -> None:
    """Read the interrupted job again after its post-checks, including any fresh admission."""
    run, job = lifecycle_run, interrupted
    obs = run.evidence[job.case]
    response = run.api.get_job(job.id)
    assert response.status_code == 200
    record = response.json()
    assert record["lifecycle"] == job.state and record["outcome"] == "not_evaluated"
    assert record["updated_at"] == obs["replay"]["updated_at"]
    obs["status"] = "observed"


def test_s05_orphans(lifecycle_run: Run, interrupted: Job) -> None:
    """Require both process inventories from the immediate post-interruption window."""
    snapshot = lifecycle_run.evidence["S05"][interrupted.case]
    assert "api" in snapshot["proc_roles"] and "api" in snapshot["docker_top_roles"]


def test_s06_never_accepted(lifecycle_run: Run, interrupted: Job) -> None:
    """Compare durable state and replay after a second backend startup recovery."""
    run, job = lifecycle_run, interrupted
    before = run.evidence[job.case]["replay"]
    obs = run.evidence.setdefault("S06", {})[job.case] = {"before": before, "recoveries": []}
    # S02/S04 already started once in the disruption. Cancel/deadline need both restarts here.
    # Each group ends on a fresh backend; at most two fresh admissions per interruption.
    with _budget(run, 600):
        for _ in range(2 if job.case in {"S01", "S03"} else 1):
            _restart(run)
            after = _never_accepted(run, job)
            obs["recoveries"].append({"job": after, "volume": _no_bundle(run, job)})
            assert after == before, "Restart changed terminal evidence or artifact availability"
