"""R6 admission, publication and legacy-retention cases on the release backend."""
from __future__ import annotations

import errno
import json
import os
import signal
import time
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from harness.api import Api
from harness.ops import Ops
from harness.stack import Stack, StackConfig, StackError
from harness.storage_flows import (
    CaseLog, artifact_digests, beam_brief, classify_429, regen_brief, submit, wait_for,
)

pytestmark = pytest.mark.capacity
ROOT = Path(__file__).resolve().parents[1]
VOLUME = "/tmp/mirum/engineering"
MIB = 2**20
TERMINAL = ("completed", "cancelled", "unavailable")


def _healthy(api: Api) -> None:
    deadline = time.monotonic() + 180
    status = 0
    while time.monotonic() < deadline:
        try:
            status = api.get("/auth/check", timeout=3).status_code
        except httpx.TransportError:
            status = 0
        if status == 200:
            return
        assert status != 429, "Backend health probe was rate limited"
        time.sleep(0.5)
    pytest.fail(f"Backend health deadline expired (last HTTP status {status})")


def _start(ops: Ops, api: Api) -> None:
    ops.start("backend", timeout_s=120)
    _healthy(api)


def _restart(ops: Ops, api: Api) -> None:
    ops.stop("backend", 30)
    _start(ops, api)


@pytest.fixture(scope="module")
def capacity_run() -> Iterator[tuple[Stack, Api, CaseLog, float]]:
    config = StackConfig(
        repo_root=ROOT,
        compose_files=(ROOT / "compose.release.yml", ROOT / "e2e/compose/e2e.override.yml"),
        project=os.environ["MIRUM_E2E_PROJECT"], token=os.environ["MIRUM_E2E_TOKEN"],
    )
    stack = Stack(config)
    api = Api(config.api_url, config.token)
    deadline = time.monotonic() + 1800
    try:
        _start(Ops(stack), api)  # The workflow's optional overlay smoke ends with down.
        images = stack.image_ids()
        assert images.get("backend"), "Missing release backend image identity"
        log = CaseLog(ROOT / "e2e/artifacts/storage/capacity",
                      os.environ.get("GITHUB_SHA") or "local", images, redact=[config.token])
        try:
            yield stack, api, log, deadline
        finally:
            log.write()
    finally:
        api.close()


@dataclass
class Case:
    ops: Ops
    plain: Ops
    api: Api
    observed: dict = field(default_factory=dict)

    def probe(self, code: str) -> Any:
        """Inspect the backend's mount namespace, including nested staging mounts."""
        prelude = f"import json, os, shutil\nfrom pathlib import Path\nroot = Path({VOLUME!r})\n"
        return json.loads(self.ops.exec_py("backend", prelude + code))


def _scenario(case_id: str, mechanism: str, kind: str, overlay: str = "") -> pytest.MarkDecorator:
    return pytest.mark.parametrize("case", [(case_id, mechanism, kind, overlay)], indirect=True,
                                   ids=[case_id])


@pytest.fixture
def case(request: pytest.FixtureRequest, capacity_run: tuple) -> Iterator[Case]:
    base, api, log, module_deadline = capacity_run
    case_id, mechanism, kind, overlay = request.param
    # S08b must preserve the DB when staging is unmounted. S09a starts with zero retained jobs.
    isolated = case_id in {"S08b", "S09a"}
    config = base.config
    if isolated:
        config = replace(config, project=f"{config.project}-{case_id.lower()}-{uuid4().hex[:8]}")
    plain = Ops(Stack(config))
    if overlay:
        path = ROOT / "e2e/compose/storage" / f"{overlay}.yml"
        config = replace(config, compose_files=(*config.compose_files, path))
    ops = Ops(Stack(config))
    current = Case(ops, plain, api)

    def expired(*_: object) -> None:
        raise TimeoutError(f"{case_id} exceeded the case or module time budget")

    old_handler = signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, max(0.001, min(600, module_deadline - time.monotonic())))
        Ops(base).stop("backend", 30)
        if isolated:
            assert plain.volume_name() == f"{config.project}_e2e_engineering"
            if case_id == "S08b":
                _start(plain, api)  # Create application-owned staging below the future tmpfs.
        _start(ops, api)
        if overlay == "small-volume":
            loop = os.environ["MIRUM_STORAGE_LOOP_MOUNT"]
            assert os.path.ismount(loop), "Workflow loop filesystem is missing"
            assert current.probe("print(root.stat().st_dev)") == os.stat(loop).st_dev
        yield current
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        try:
            if isolated:
                if plain.volume_name() == f"{config.project}_e2e_engineering":
                    ops.stack.down(remove_volumes=True)
            elif overlay:
                ops.stack.down()
        finally:
            current.observed["ops_notes"] = ops.notes + plain.notes
            log.record(case_id, mechanism, kind, current.observed)


def _admit(case: Case, brief: str, key: str) -> str:
    response = submit(case.api, brief, key)
    assert response.status_code == 202, f"Job admission returned HTTP {response.status_code}"
    return response.json()["job_id"]


def _accepted(case: Case, brief: str) -> dict:
    job = _admit(case, brief, uuid4().hex)
    result = case.api.wait_job(job, timeout_s=180)
    assert (result["lifecycle"], result["outcome"]) == ("completed", "accepted")
    case.observed.setdefault("accepted_jobs", []).append(job)
    return result


def _cancel(case: Case, job: str) -> None:
    response = case.api.post(f"/engineering/v1/jobs/{job}/cancel")
    assert response.status_code == 200
    result = case.api.wait_job(job, timeout_s=45)
    assert (result["lifecycle"], result["outcome"]) == ("cancelled", "not_evaluated")


def _states(case: Case) -> dict[str, str]:
    return dict(case.ops.db_rows("SELECT id,json_extract(record,'$.lifecycle') FROM jobs"))


def _refusal(case: Case, response: httpx.Response, *, quota: bool = False) -> None:
    why = classify_429(response)
    delay = response.headers.get("Retry-After")
    case.observed["refusal"] = {"status": response.status_code, "kind": why, "retry_after": delay}
    assert response.status_code == 429
    if quota:
        assert why == "client_quota" and delay and 5 < int(delay) <= 3600
    else:
        assert why == "admission_busy" and delay == "5"


def _quiet(case: Case) -> None:
    # P1, process_runner.py:41,182-184. Include untagged regen descendants and zombies.
    top = case.ops.stack._run(["docker", "top", case.ops.container_id("backend"), "-eo", "pid,comm"])
    case.observed.setdefault("docker_top", []).append(top.splitlines())
    owners = case.ops.db_rows("SELECT value FROM metadata WHERE key='process_owner'")
    assert len(owners) == 1
    for row in case.ops.processes("backend"):
        argv = row["argv"]
        exe = Path(argv[0]).name if argv and argv[0] else ""
        assert not row["has_token"] and row["owner"] is None, f"Tagged worker PID {row['pid']}"
        init = row["pid"] == 1 and exe in {"docker-init", "tini"}
        server = "uvicorn" in [Path(arg).name for arg in argv[:3]] and row["ppid"] == 1
        helper = (exe.startswith("python") and len(argv) >= 3 and argv[1] == "-c" and
                  ("rows.append(dict(pid=" in argv[2] or "/auth/check" in argv[2]))
        assert init or server or helper, f"Unexpected backend process {exe!r} (PID {row['pid']})"


def _unpublished(case: Case, job: str) -> None:
    # P2, bundles.py:302-323. A sidecar cannot see S08b's nested tmpfs.
    assert case.probe("print(json.dumps([p.name for p in (root/'staging').iterdir()]))") == []
    assert case.probe(f"print(json.dumps((root/'bundles'/{job!r}).exists()))") is False
    assert case.ops.db_rows("SELECT job FROM publications WHERE job=?", (job,)) == []


def _failed_publication(case: Case, brief: str, key: str) -> str:
    job = _admit(case, brief, key)
    result = case.api.wait_job(job, timeout_s=180)
    case.observed.update(job_id=job, failures=result["failures"], lifecycle=result["lifecycle"],
                         selected_candidate_sha256=result["selected_candidate_sha256"])
    # jobs.py:249-256,270-271. Selection precedes publication; its OSError is masked.
    assert result["selected_candidate_sha256"], "Beam failed before reaching publication"
    assert (result["lifecycle"], result["outcome"]) == ("unavailable", "not_evaluated")
    assert "Required execution or publication unavailable" in result["failures"]
    _check_failed(case, brief, key, job)
    return job


def _check_failed(case: Case, brief: str, key: str, job: str) -> None:
    # P3, jobs.py:128-132 and api_v1.py:377-384. Replay is a read of the same terminal row.
    replay = submit(case.api, brief, key)
    assert replay.status_code == 202
    row = replay.json()
    assert (row["job_id"], row["lifecycle"], row["outcome"]) == (job, "unavailable", "not_evaluated")
    assert row["bundle"] is None
    for fmt in ("zip", "step"):
        assert case.api.artifact(job, fmt).status_code in (404, 503)
    _unpublished(case, job)
    _quiet(case)


def _recover(case: Case, brief: str, key: str, job: str, *, plain: bool = False) -> None:
    # P5 at the first opportunity after removing the fault. Only S08b needs a new
    # container to remove the tmpfs. P4 spans two startups on the original volume.
    if not plain:
        _accepted(case, brief)
    case.ops.stop("backend", 30)
    if plain:
        case.ops = case.plain
    _start(case.ops, case.api)
    _check_failed(case, brief, key, job)
    if plain:
        _accepted(case, brief)
    _restart(case.ops, case.api)
    _check_failed(case, brief, key, job)


@_scenario("S07a", "Second admission during a running regen job", "R")
def test_slot_busy(case: Case) -> None:
    regen = regen_brief(case.api)["brief_id"]
    beam = beam_brief(case.api)["brief_id"]
    job = _admit(case, regen, uuid4().hex)
    wait_for(case.api, job, "running", 30)
    usage = _usage(case)
    case.observed["usage_before_second_admission"] = usage
    assert usage["reservations"] < 50
    assert max(usage["logical_bytes"], usage["reserved_bytes"]) + 32 * MIB <= 512 * MIB
    before = _states(case)
    assert before[job] == "running", "Missed running regen window"
    response = submit(case.api, beam, uuid4().hex)
    _refusal(case, response)  # admission.py:81-95; api_v1.py:120-121.
    after = _states(case)
    assert after.keys() == before.keys() and after[job] == "running"
    case.observed.update(running_job=job, rows_before=len(before), rows_after=len(after))
    _cancel(case, job)


@_scenario("S07b", "Six cancelled admissions followed by a seventh request", "R")
def test_client_quota(case: Case) -> None:
    brief = beam_brief(case.api)["brief_id"]
    cancelled = case.observed["cancelled_jobs"] = []
    for _ in range(6):
        job = _admit(case, brief, uuid4().hex)
        _cancel(case, job)
        cancelled.append(job)
    assert len(set(cancelled)) == 6
    before = _states(case)
    assert all(state in TERMINAL for state in before.values())
    _refusal(case, submit(case.api, brief, uuid4().hex), quota=True)  # admission.py:59-66.
    assert _states(case) == before
    _restart(case.ops, case.api)
    _accepted(case, brief)


@_scenario("S08a", "Immutable bundle directory during the staging rename", "F", "small-volume")
def test_immutable_publication(case: Case) -> None:
    brief = beam_brief(case.api)["brief_id"]
    key = uuid4().hex
    try:
        case.ops.host_chattr("bundles", "+i")
        job = _failed_publication(case, brief, key)  # bundles.py:309.
    finally:
        case.ops.host_chattr("bundles", "-i")
    _recover(case, brief, key, job)


@_scenario("S08b", "16 KiB staging tmpfs on a disposable project volume", "F", "staging-tiny")
def test_staging_enospc(case: Case) -> None:
    # bundles.py:302-308 writes the selected payload to this specific mount.
    stage = case.probe("p = root/'staging'\nprint(json.dumps([p.is_mount(), shutil.disk_usage(p).total]))")
    case.observed["staging_mount_and_bytes"] = stage
    assert stage == [True, 16 * 1024]
    # Capture the real errno independently; the API masks publisher OSErrors.
    probe_errno = case.probe(
        "from tempfile import TemporaryFile\n"
        "with TemporaryFile(dir=root/'staging', buffering=0) as stream:\n"
        "    try:\n"
        "        stream.write(bytes(16 * 1024))\n"
        "        stream.write(b'x')\n"
        "    except OSError as error: print(error.errno)\n"
        "    else: print(0)"
    )
    case.observed["staging_write_errno"] = probe_errno
    assert probe_errno == errno.ENOSPC
    brief = beam_brief(case.api)["brief_id"]
    key = uuid4().hex
    job = _failed_publication(case, brief, key)
    _recover(case, brief, key, job, plain=True)


def _regen_worker(case: Case) -> bool:
    return any("engineering.regen_web" in p["argv"] and "--worker" in p["argv"]
               for p in case.ops.processes("backend"))


def _observe(case: Case, job: str, phase: str) -> None:
    response = case.api.get_job(job)
    sample = {"status": response.status_code}
    case.observed[phase] = sample
    if response.status_code == 200:
        record = response.json()
        sample.update(lifecycle=record["lifecycle"], outcome=record["outcome"])
        assert record["outcome"] != "accepted"
    # A 500 by itself says nothing about the durable outcome. Read it independently.
    rows = case.ops.db_rows("SELECT json_extract(record,'$.lifecycle'),"
                            "json_extract(record,'$.outcome') FROM jobs WHERE id=?", (job,))
    assert len(rows) == 1, "Could not observe the durable regen record"
    sample["stored_lifecycle"], sample["stored_outcome"] = rows[0]
    assert sample["stored_outcome"] != "accepted"
    assert response.status_code != 429, "Exploratory read was rate limited"


@_scenario("S08c", "Read-only loop filesystem during a live regen worker", "F", "small-volume")
def test_read_only_observation(case: Case) -> None:
    brief = regen_brief(case.api)["brief_id"]
    job = _admit(case, brief, uuid4().hex)
    case.observed["job_id"] = job
    wait_for(case.api, job, "running", 30)
    deadline = time.monotonic() + 30
    while not _regen_worker(case):
        assert time.monotonic() < deadline, "Missed the active regen worker before remount"
        time.sleep(0.5)
    _observe(case, job, "before_remount")
    try:
        try:
            case.ops.host_remount("ro")
        except StackError as error:
            case.observed["remount_error"] = case.ops.stack.redact(str(error))
            raise
        readonly = case.probe("print(json.dumps(bool(os.statvfs(root).f_flag & os.ST_RDONLY)))")
        case.observed["backend_mount_readonly"] = readonly
        assert readonly, "Host fault did not reach the backend mount namespace"
        assert _regen_worker(case), "Regen worker exited before the read-only window"
        _observe(case, job, "during_regen")
        deadline = time.monotonic() + 150
        while _regen_worker(case):
            assert time.monotonic() < deadline, "Regen worker still active at observation deadline"
            time.sleep(1)
        # Give the controller time to consume the worker result with storage still read-only.
        time.sleep(5)
        _observe(case, job, "before_restart")
    finally:
        case.ops.host_remount("rw")
    # jobs.py:80-89 recovers interrupted rows. Record the actual state, including unexpected ones.
    _restart(case.ops, case.api)
    _observe(case, job, "after_restart")
    _quiet(case)


def _usage(case: Case) -> dict:
    # store.py:97-104 excludes only the root database/lock files. jobs.py:142-147 uses
    # max(artifact bytes, reserved bytes), and independently caps the number of reservations.
    logical = case.probe(
        "excluded = {'engineering.sqlite3','engineering.sqlite3-wal','engineering.sqlite3-shm','owner.lock'}\n"
        "print(sum(p.stat().st_size for p in root.rglob('*') if not p.is_symlink() and p.is_file()\n"
        "          and not (p.parent == root and p.name in excluded)))"
    )
    reserved, count = case.ops.db_rows(
        "SELECT COALESCE(SUM(reserved),0),COUNT(*) FROM jobs WHERE reserved>0"
    )[0]
    return {"logical_bytes": logical, "reserved_bytes": reserved, "reservations": count}


@_scenario("S09a", "Retain real regen bundles until the next reservation exceeds 512 MiB", "R")
def test_real_capacity(case: Case) -> None:
    brief = regen_brief(case.api)["brief_id"]
    assert _states(case) == {}, "Real-capacity case requires a fresh volume"
    attempts = case.observed["attempts"] = []
    retained = 0
    for index in range(4):
        before = _states(case)
        assert all(state in TERMINAL for state in before.values())
        usage = _usage(case)
        assert usage["reserved_bytes"] == retained and usage["reservations"] == index
        would_exceed = max(usage["logical_bytes"], retained) + 256 * MIB > 512 * MIB
        if index == 3 and not would_exceed:
            pytest.fail("Three real regen bundles did not reach the refusal threshold")
        response = submit(case.api, brief, uuid4().hex)
        attempts.append(dict(usage, status=response.status_code, would_exceed=would_exceed))
        if response.status_code == 429:
            _refusal(case, response)
            assert would_exceed, "Storage refused a reservation below the measured limit"
            assert _states(case) == before, "Refusal changed the idle job set"
            return
        assert response.status_code == 202
        job = response.json()["job_id"]
        if would_exceed:
            _cancel(case, job)
            pytest.fail("Storage admitted a regen job above the measured limit")
        result = case.api.wait_job(job, timeout_s=180)
        assert (result["lifecycle"], result["outcome"]) == ("completed", "accepted")
        size = result["bundle"]["stored_bytes"]  # jobs.py:223-225 retains this reservation.
        assert size > 0
        retained += size
        attempts[-1].update(job_id=job, stored_bytes=size)
    pytest.fail("No storage refusal observed")


@_scenario("S09b", "481 MiB sparse file counts against a 32 MiB beam admission", "F")
def test_sparse_accounting(case: Case) -> None:
    brief = beam_brief(case.api)["brief_id"]
    name = f"sparse-{uuid4().hex}"
    before = _states(case)
    assert all(state in TERMINAL for state in before.values())
    baseline = _usage(case)
    assert max(baseline["logical_bytes"], baseline["reserved_bytes"]) + 32 * MIB <= 512 * MIB
    try:
        case.ops.write_sparse(name, 481 * MIB)
        filled = _usage(case)
        case.observed.update(baseline=baseline, with_sparse=filled)
        assert filled["logical_bytes"] == baseline["logical_bytes"] + 481 * MIB
        # jobs.py:142-147 checks apparent file lengths, independent of actual device free space.
        _refusal(case, submit(case.api, brief, uuid4().hex))
        assert _states(case) == before
    finally:
        case.ops.remove(name)
    assert _usage(case) == baseline
    _accepted(case, brief)


@_scenario("S09c", "Allocate device blocks while preserving logical admission headroom", "F", "small-volume")
def test_device_free_space(case: Case) -> None:
    brief = beam_brief(case.api)["brief_id"]
    key = uuid4().hex
    name = f"device-{uuid4().hex}"
    # FALLOC_FL_KEEP_SIZE reserves real ext4 blocks while st_size stays zero. Ordinary
    # posix_fallocate would also trip admission in store.py:99 before bundles.py:291.
    try:
        pressure = case.probe(
            "import ctypes\n"
            f"p = root/{name!r}\n"
            "free = shutil.disk_usage(root).free\n"
            "assert free > 32 * 2**20\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "with p.open('xb') as f:\n"
            "    rc = libc.fallocate(f.fileno(), 1, ctypes.c_longlong(0),\n"
            "                        ctypes.c_longlong(free - 16 * 2**20))\n"
            "    if rc: raise OSError(ctypes.get_errno(), 'fallocate(KEEP_SIZE)')\n"
            "print(json.dumps([free, shutil.disk_usage(root).free, p.stat().st_size]))"
        )
        case.observed["free_before_after_and_logical_size"] = pressure
        assert 0 < pressure[1] < 32 * MIB and pressure[2] == 0
        job = _failed_publication(case, brief, key)
        assert case.probe("print(shutil.disk_usage(root).free)") < 32 * MIB
    finally:
        case.ops.remove(name)
    _recover(case, brief, key, job)


def _dragster(case: Case, spec: dict) -> str:
    # main.py:1650-1658 streams one final done event after legacy artifact publication.
    response = case.api.post("/dragsters/generate", json=spec, timeout=90)
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events and events[-1].get("status") == "done", "Legacy run did not publish artifacts"
    return events[-1]["run_id"]


@_scenario("S11", "Legacy MAX_RUNS=1 and TTL_S=1 leave engineering downloads intact", "R", "legacy-knobs")
def test_legacy_eviction(case: Case) -> None:
    knobs = case.probe("print(json.dumps([os.environ['MIRUM_ARTIFACT_MAX_RUNS'],"
                       "os.environ['MIRUM_ARTIFACT_TTL_S']]))")
    assert knobs == ["1", "1"]
    result = _accepted(case, beam_brief(case.api)["brief_id"])
    job = result["job_id"]
    formats = [name for name, available in result["artifacts"].items() if available]
    assert "zip" in formats and "step" in formats
    before = artifact_digests(case.api, job, formats)
    files = case.ops.volume_digests(job)
    assert files["package.zip"] == before["zip"]
    spec = json.loads((ROOT / "backend/dragsters/examples/wedge.json").read_text(encoding="utf-8"))
    first = _dragster(case, spec)
    assert case.api.get(f"/download/{first}/spec").status_code == 200
    time.sleep(1.1)  # artifacts.py:146-156 uses the real legacy wall-clock TTL.
    second = _dragster(case, spec)
    assert first != second
    assert case.api.get(f"/download/{second}/spec").status_code == 200
    missing = case.api.get(f"/download/{first}/spec")
    assert missing.status_code == 404  # main.py:1714-1731 sweeps before serving.
    after = artifact_digests(case.api, job, formats)
    assert after == before and case.ops.volume_digests(job) == files
    case.observed.update(legacy_runs=[first, second], first_after_eviction=missing.status_code,
                         engineering_before=before, engineering_after=after)
