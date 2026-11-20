"""Release-image retention, replay, integrity and browser evidence."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import Browser, Page, expect

from harness.api import Api, TOKEN_HEADER
from harness.guard import guarded_context
from harness.ops import Ops
from harness.stack import Stack, StackConfig
from harness.storage_flows import CaseLog, aggregate_status, artifact_digests, classify_429, regen_brief, submit, wait_for
from harness.ui import CheckedBeam, sign_in

pytestmark = pytest.mark.retention
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "e2e/artifacts/storage/retention"
MIB = 1 << 20
FORMATS = {
    "zip": "package.zip", "manifest": "manifest.json", "brief": "brief.json",
    "step": "model.step", "glb": "preview.glb", "spec": "selected_spec.json",
    "source": "model.py", "attempts": "attempts.json", "report": "report.json",
    "markdown": "report.md",
}
# B13-g36-diagnostic.json records the typed C01 oracle. Step 12 uses
# case.expected_content for its provider-free comparison (b13_step12_ui.py:602).
C01_SHA = "34aee33577a2ce2da90ed9c0e3727c53843cbcca26a4442632cd8d3bdcb5280e"


@pytest.fixture(scope="module", autouse=True)
def time_budget() -> Iterator[Callable[[], None]]:
    """Include shared fixture setup in the first case that needs it (Linux CI)."""
    deadline = time.monotonic() + 1800

    def expired(signum: int, frame: FrameType | None) -> None:
        pytest.fail("Retention test exceeded its ten-minute or module thirty-minute budget")

    def arm() -> None:
        remaining = min(600, deadline - time.monotonic())
        assert remaining > 0, "Retention module exceeded thirty minutes"
        signal.setitimer(signal.ITIMER_REAL, remaining)

    previous = signal.signal(signal.SIGALRM, expired)
    try:
        arm()
        yield arm
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture(autouse=True)
def next_case_budget(time_budget: Callable[[], None]) -> Iterator[None]:
    yield
    time_budget()


@pytest.fixture(scope="module")
def ops() -> Ops:
    config = StackConfig(
        repo_root=ROOT, compose_files=(ROOT / "compose.release.yml", ROOT / "e2e/compose/e2e.override.yml"),
        project=os.environ["MIRUM_E2E_PROJECT"], token=os.environ["MIRUM_E2E_TOKEN"],
    )
    return Ops(Stack(config))


def _healthy(client: Api) -> None:
    deadline = time.monotonic() + 180
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            response = client.get("/auth/check", timeout=min(4.0, remaining))
        except httpx.TransportError:
            pass
        else:
            if response.status_code == 200:
                return
            assert response.status_code == 503, f"Health probe returned HTTP {response.status_code}"
        time.sleep(max(0, min(1, deadline - time.monotonic())))
    pytest.fail("Authenticated backend health probe timed out")


@pytest.fixture(scope="module")
def client(ops: Ops) -> Iterator[Api]:
    api = Api("http://127.0.0.1:8000", ops.stack.config.token, timeout=15)
    try:
        _healthy(api)
        yield api
    finally:
        api.close()


# Case id -> (test function, parametrize variant); the log status is the real pytest outcome.
CASE_TESTS = {
    "S10": ("test_s10_held_download", None),
    "S12(i)": ("test_s12_persistence", "i"),
    "S12(ii)": ("test_s12_persistence", "ii"),
    "S13": ("test_s13_offline_replay", None),
    "S14": ("test_s14_tamper", None),
    "S15(real)": ("test_s15_interrupted_views", None),
    "S15(expired)": ("test_s15_expired_view", None),
}


@pytest.fixture(scope="module")
def cases(ops: Ops, request: pytest.FixtureRequest) -> Iterator[CaseLog]:
    log = CaseLog(OUT, os.environ.get("GITHUB_SHA") or "local", ops.stack.image_ids(),
                  redact=[ops.stack.config.token])
    try:
        yield log
    finally:
        for case_id, (test, variant) in CASE_TESTS.items():
            items = [item for item in request.session.items
                     if getattr(item, "originalname", None) == test
                     and (variant is None or item.callspec.params.get("variant") == variant)]
            log.set_status(case_id, aggregate_status(items))
        log.write()


@contextmanager
def _case(cases: CaseLog, ops: Ops, name: str, mechanism: str, kind: str) -> Iterator[dict]:
    """Preserve observations and injection notes even on a failed assertion."""
    start = len(ops.notes)
    observed = {"completed": False}
    try:
        yield observed
        observed["completed"] = True
    finally:
        observed["ops_notes"] = ops.notes[start:]
        cases.record(name, mechanism, kind, observed)


def _json(response: httpx.Response, status: int = 200) -> dict:
    if response.status_code == 429:
        pytest.fail(f"Unexpected HTTP 429 ({classify_429(response)}); admission was not retried")
    assert response.status_code == status
    return response.json()


@pytest.fixture(scope="module")
def typed_beam(client: Api) -> str:
    # The v1 form takes BriefContent (briefs.py:151-155), unlike the legacy
    # specification in examples/engineering/cantilever.json used by beam_brief.
    corpus = json.loads((ROOT / "backend/tests/fixtures/engineering/ai-corpus-v1.json").read_bytes())
    content = next(case["expected_content"] for case in corpus["cases"] if case["id"] == "C01")
    prepared = _json(client.create_draft({"mode": "form", "input": content}), 201)
    assert prepared["issues"] == []
    assert prepared["brief_sha256"] == C01_SHA, "Typed C01 differs from its pinned identity"
    draft = prepared["draft"]
    brief = _json(client.finalize({
        "draft_id": draft["draft_id"], "draft_revision": draft["revision"],
        "acknowledged_fields": client.capabilities()["review_fields"], "brief_sha256": C01_SHA,
        "reviewed_by": "storage-retention", "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }), 201)
    return brief["brief_id"]


def _accepted(client: Api, brief: str, key: str) -> dict:
    # api_v1.py:352-364 and jobs.py:251-255: admission then validated publication.
    job_id = _json(submit(client, brief, key), 202)["job_id"]
    job = client.wait_job(job_id, timeout_s=200)
    assert (job["lifecycle"], job["outcome"], job["delivery"]) == ("completed", "accepted", "available")
    return job


def _disk(ops: Ops, job_id: str) -> dict:
    assert re.fullmatch(r"[0-9a-f]{32}", job_id)
    return json.loads(ops.sidecar(
        "from pathlib import Path\nimport json\nroot = Path('/tmp/mirum/engineering')\n"
        f"print(json.dumps(dict(bundle=(root/'bundles'/{job_id!r}).exists(), "
        "staging=sorted(p.name for p in (root/'staging').iterdir()))))"
    ))


def _idle(ops: Ops) -> dict:
    """P1. Include Docker's view and /proc ownership without exporting argv or tokens."""
    owners = ops.db_rows("SELECT value FROM metadata WHERE key='process_owner'")
    assert len(owners) == 1 and re.fullmatch(r"[0-9a-f]{32}", owners[0][0])
    processes = ops.processes("backend")
    top = ops.stack._run(["docker", "top", ops.container_id("backend"), "-eo", "pid,comm"])
    names = []
    for row in processes:
        argv = row["argv"]
        exe = Path(argv[0]).name if argv else "<zombie>"
        names.append(exe)
        assert not row["has_token"] and row["owner"] is None, f"Tagged worker remains: PID {row['pid']}"
        init = row["pid"] == 1 and exe in {"docker-init", "tini"}
        server = exe == "uvicorn" or argv[1:3] == ["-m", "uvicorn"]
        probe = exe.startswith("python") and len(argv) >= 3 and argv[1] == "-c" and (
            'Path("/proc").iterdir()' in argv[2] or "/auth/check" in argv[2]
        )
        assert init or server or probe, f"Unexpected backend process {exe}, PID {row['pid']}"
    assert any("uvicorn" in proc["argv"] for proc in processes)
    return {"processes": names, "docker_top": top.splitlines(), "database_owner": owners[0][0]}


def test_s10_held_download(client: Api, ops: Ops, cases: CaseLog, typed_beam: str) -> None:
    with _case(cases, ops, "S10", "Hold cleaned STL across accelerated expiry", "A") as obs:
        job = _accepted(client, regen_brief(client)["brief_id"], uuid4().hex)
        job_id = job["job_id"]
        manifest = _json(client.artifact(job_id, "manifest"))
        stl = next(p for p in manifest["payloads"] if p["path"] == "generator-cleaned.stl")
        url = f"{client.base_url}/engineering/v1/jobs/{job_id}/artifacts/generator-cleaned-stl"
        headers = {TOKEN_HEADER: ops.stack.config.token, "Accept-Encoding": "identity"}
        with httpx.stream("GET", url, headers=headers, timeout=30) as held:
            assert held.status_code == 200
            size = int(held.headers["Content-Length"])
            assert size == stl["bytes"] and size > MIB, "STL cannot span the held-stream window"
            blocks = held.iter_raw(chunk_size=MIB)
            first = next(blocks)
            assert len(first) == MIB
            digest = hashlib.sha256(first)
            received = len(first)
            header = held.headers["X-Content-SHA256"]
            ops.set_expiry(job_id, time.time() - 1)
            # jobs.py:100-106 runs cleanup on read. bundles.py:464-477 skips
            # leased bytes; checking the directory detects premature POSIX unlink.
            denied = client.artifact(job_id, "generator-cleaned-stl")
            obs.update(job_id=job_id, new_get=denied.status_code)
            assert denied.status_code == 410  # api_v1.py:116-117, bundles.py:390
            expired = _json(client.get_job(job_id))
            assert expired["delivery"] == "expired" and expired["artifacts"] == {}
            assert _disk(ops, job_id) == {"bundle": True, "staging": []}, "Missed active lease"
            for block in blocks:
                received += len(block)
                digest.update(block)
        assert received == size and digest.hexdigest() == header == stl["sha256"]
        # Allow the ASGI response's finally block to run after client EOF.
        deadline = time.monotonic() + 15
        while True:
            assert _json(client.get_job(job_id))["delivery"] == "expired"
            if _disk(ops, job_id) == {"bundle": False, "staging": []}:
                break
            assert time.monotonic() < deadline, "Bundle still present after lease release"
            time.sleep(0.2)
        assert ops.db_rows("SELECT reserved FROM jobs WHERE id=?", [job_id]) == [[0]]
        obs.update(bytes=received, sha256=header, manifest_sha256=stl["sha256"], cleaned=True,
                   delivery=expired["delivery"], idle=_idle(ops))
        obs["fresh_beam"] = _accepted(client, typed_beam, uuid4().hex)["job_id"]  # P5


@pytest.fixture(scope="module")
def saved_beam(client: Api, typed_beam: str) -> tuple[str, dict]:
    key = uuid4().hex
    return key, _accepted(client, typed_beam, key)


def _snapshot(client: Api, ops: Ops, job_id: str) -> dict:
    response = _json(client.get_job(job_id))
    assert response["artifacts"] == dict.fromkeys(FORMATS, True)
    served = artifact_digests(client, job_id, FORMATS)
    files = ops.volume_digests(job_id)
    assert files == {FORMATS[fmt]: digest for fmt, digest in served.items()}
    image = ops.stack.image_ids()["backend"]
    assert image
    return {"api": served, "files": files, "volume": ops.volume_name(), "image": image}


@pytest.mark.parametrize("variant", ["i", "ii"])
def test_s12_persistence(
    variant: str, client: Api, ops: Ops, cases: CaseLog, saved_beam: tuple[str, dict], typed_beam: str,
) -> None:
    key, job = saved_beam
    job_id = job["job_id"]
    mechanism = "Replace backend" if variant == "i" else "Compose down preserving volume, then up"
    with _case(cases, ops, f"S12({variant})", mechanism, "R") as obs:
        before = _snapshot(client, ops, job_id)
        if variant == "i":
            old, new = ops.replace("backend")
        else:
            old = ops.container_id("backend")
            ops.stack.down(remove_volumes=False)
            ops.start("backend")
            new = ops.container_id("backend")
        assert new != old
        _healthy(client)
        after = _snapshot(client, ops, job_id)
        obs.update(job_id=job_id, old_container=old, new_container=new, before=before, after=after)
        assert after == before
        # jobs.py:129-134,170-171 returns the existing immutable record on replay.
        replay = _json(submit(client, typed_beam, key), 202)
        assert replay == job
        assert _disk(ops, job_id) == {"bundle": True, "staging": []}
        obs.update(replayed_job=replay["job_id"], idle=_idle(ops))


def test_s13_offline_replay(
    client: Api, ops: Ops, cases: CaseLog, saved_beam: tuple[str, dict], tmp_path: Path,
) -> None:
    _, job = saved_beam
    with _case(cases, ops, "S13", "Replay delivered C01 ZIP with networking disabled", "R") as obs:
        download = client.artifact(job["job_id"], "zip")
        assert download.status_code == 200
        archive = tmp_path / "package.zip"
        archive.write_bytes(download.content)
        archive.chmod(0o444)
        sha = hashlib.sha256(download.content).hexdigest()
        assert sha == download.headers["X-Content-SHA256"] == job["bundle"]["zip_sha256"]
        source = os.environ.get("GITHUB_SHA") or "local"
        # The release target ships the backend, but not scripts/. The replay
        # writes replay.json on tmpfs; return it without a writable host mount.
        script = ROOT / "scripts/checked_engineering_replay.py"
        runner = (
            "import runpy,sys; from pathlib import Path; "
            "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__'); "
            "import json; print(json.dumps(json.loads(Path('/tmp/result/replay.json').read_text())))"
        )
        argv = ["docker", "run", "--rm", "--network", "none", "--read-only", "--user", "10001:10001",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--init",
                "--memory", "4g", "--pids-limit", "256", "--cpus", "2",
                "--tmpfs", "/tmp:rw,nosuid,exec,size=1g,mode=1777",
                "-v", f"{script}:/replay.py:ro", "-v", f"{archive}:/package.zip:ro",
                "mirum-backend:release", "python", "-c", runner, "/replay.py", "/package.zip",
                "/tmp/result", source]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=285, cwd=ROOT)
        if proc.returncode:
            pytest.fail(ops.stack.redact(proc.stderr + proc.stdout)[-3000:])
        result = json.loads(ops.stack.redact(proc.stdout).strip().splitlines()[-1])
        obs.update(job_id=job["job_id"], replay=result)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "replay.json").write_text(ops.stack.redact(json.dumps(result, indent=2)) + "\n", encoding="utf-8")
        assert result["tested_implementation_sha"] == source
        assert result["brief_sha256"] == job["brief_sha256"] == C01_SHA
        assert result["delivered_archive_sha256"] == sha
        assert result["selected_candidate_sha256"] == job["selected_candidate_sha256"]
        assert [a["candidate"]["specification"]["height_mm"] for a in job["attempts"]] == [5, 10, 15]
        assert result["verdicts"] == ["rejected", "rejected", "accepted"]
        assert result["provider_calls"] == 0
        # capabilities.py:53-54 pins both numerical tolerances.
        assert (result["relative_tolerance"], result["absolute_tolerance"]) == (1e-8, 1e-6)
        assert {row["group"] for row in result["comparisons"]} == {"measurements", "physics"}
        for row in result["comparisons"]:
            assert math.isclose(row["before"], row["after"], rel_tol=1e-8, abs_tol=1e-6), row["name"]


def test_s14_tamper(client: Api, ops: Ops, cases: CaseLog, typed_beam: str) -> None:
    with _case(cases, ops, "S14", "Flip a STEP byte, then truncate the same file", "F") as obs:
        job_id = _accepted(client, typed_beam, uuid4().hex)["job_id"]
        good = client.artifact(job_id, "step")
        assert good.status_code == 200 and len(good.content) > 1
        obs.update(job_id=job_id, original_sha256=hashlib.sha256(good.content).hexdigest())
        assert obs["original_sha256"] == good.headers["X-Content-SHA256"]
        for mode in ("flip_byte", "truncate"):
            ops.tamper(job_id, "model.step", mode)
            denied = client.artifact(job_id, "step")
            # bundles.py:344,406-411: stat-level and byte-level integrity gates.
            assert denied.status_code == 503  # api_v1.py:390-391
            assert denied.json()["detail"] == "Required publication integrity check failed"
            view = _json(client.get_job(job_id))
            obs[mode] = {"status": denied.status_code, "delivery": view["delivery"]}
            if mode == "truncate":
                assert view["delivery"] == "unavailable" and view["artifacts"] == {}
        assert _disk(ops, job_id) == {"bundle": True, "staging": []}
        obs["idle"] = _idle(ops)
        obs["fault_injected"] = True  # Both tampered reads were refused by the integrity gates.


@pytest.fixture(scope="module")
def frontend(ops: Ops, client: Api) -> str:
    # New quota group. Ops.start is idempotent and waits for service health.
    ops.stop("backend", 30)
    ops.start("backend")
    _healthy(client)
    ops.start("frontend")
    return ops.stack.config.ui_url


@contextmanager
def _browser_job(browser: Browser, frontend: str, ops: Ops) -> Iterator[tuple[Page, str, str]]:
    """Create the job in its owning tab; Streamlit deliberately has no job deep link."""
    context = guarded_context(browser, [], viewport={"width": 1400, "height": 1000})
    try:
        page = context.new_page()
        page.set_default_timeout(20_000)
        sign_in(page, frontend, ops.stack.config.token)
        beam = CheckedBeam(page)
        beam.open_form()
        example = json.loads((ROOT / "examples/engineering/cantilever.json").read_text(encoding="utf-8"))
        beam.type_requirements(example["specification"], example["candidate_heights_mm"])
        beam.save_draft()
        beam.acknowledge()
        brief_id, _ = beam.finalize()
        job_id = beam.run_checks()
        yield page, brief_id, job_id
    finally:
        context.close()


def _capture(page: Page, state: str, job_id: str) -> dict:
    # frontend/engineering_ui.py:116-144 withholds downloads in all three states.
    messages = {
        "cancelled": "Cancelled before terminal commit. No accepted package is offered.",
        "unavailable": "A required execution or publication stage is unavailable. No accepted package is offered.",
        "expired": "Expired. The six-hour download window ended. Review a new request to generate again.",
    }
    label = page.get_by_text(messages[state], exact=True)
    expect(label).to_be_visible(timeout=30_000)
    expect(page.get_by_text(re.compile(rf"^Job {job_id} \|"))).to_be_visible()
    if state != "expired":
        expect(page.get_by_text(f"State: {state} | engineering outcome: not_evaluated", exact=True)).to_be_visible()
    expect(page.get_by_test_id("stDownloadButton")).to_have_count(0)
    # Package downloads are stDownloadButton (asserted absent above). Streamlit's own dataframe
    # toolbar also has a "Download as CSV" button, so record every "Download" button and its
    # nearest test-id ancestors, and require that none sits outside a Streamlit element toolbar.
    others = page.evaluate(
        "() => Array.from(document.querySelectorAll('button,[role=button]')).filter(b =>"
        " /^Download /.test((b.getAttribute('aria-label') || b.getAttribute('title') || b.innerText || '').trim()))"
        ".map(b => ({name: (b.getAttribute('aria-label') || b.getAttribute('title') || b.innerText || '').trim(),"
        " in_toolbar: !!b.closest('[data-testid=stElementToolbar]'),"
        " testids: (() => { const o = []; let n = b; while (n) { const t = n.getAttribute && n.getAttribute('data-testid');"
        " if (t) o.push(t); n = n.parentElement; } return o.slice(0, 6); })()}))"
    )
    assert all(item["in_toolbar"] for item in others), others
    expect(page.get_by_role("heading", name="Revision-bound downloads")).to_have_count(0)
    OUT.mkdir(parents=True, exist_ok=True)
    shot = OUT / f"s15-{state}.png"
    label.scroll_into_view_if_needed()
    page.screenshot(path=str(shot), full_page=True, timeout=20_000)
    return {"job_id": job_id, "label": messages[state], "downloads": 0, "non_package_download_buttons": others,
            "screenshot": shot.name,
            "sha256": hashlib.sha256(shot.read_bytes()).hexdigest()}


def _unpublished(client: Api, ops: Ops, job_id: str, state: str) -> None:
    # jobs.py:82-88,213-216; api_v1.py:346-350,380-382. No diagnostic
    # publication is allowed for these cancelled/interrupted executions.
    job = _json(client.get_job(job_id))
    assert (job["lifecycle"], job["outcome"], job["delivery"]) == (state, "not_evaluated", "unavailable")
    assert job["artifacts"] == {} and job["bundle"] is None
    for fmt in ("step", "zip"):
        assert client.artifact(job_id, fmt).status_code == 404
    assert _disk(ops, job_id) == {"bundle": False, "staging": []}
    assert ops.db_rows("SELECT job FROM publications WHERE job=?", [job_id]) == []


def test_s15_interrupted_views(
    frontend: str, _browser: Browser, client: Api, ops: Ops, cases: CaseLog, typed_beam: str,
) -> None:
    with _case(cases, ops, "S15(real)", "Read cancelled and unavailable results in their owning tabs", "R") as obs:
        obs["image_ids"] = ops.stack.image_ids()
        for state in ("cancelled", "unavailable"):
            with _browser_job(_browser, frontend, ops) as (page, brief_id, job_id):
                current = _json(client.get_job(job_id))
                assert current["lifecycle"] in ("admitted", "running"), f"Missed active window for {state} view"
                if state == "cancelled":
                    _json(client.post(f"/engineering/v1/jobs/{job_id}/cancel"))
                else:
                    ops.kill("backend", "KILL")
                    ops.start("backend")
                    _healthy(client)
                wait_for(client, job_id, state, timeout_s=40)
                obs[state] = _capture(page, state, job_id)
                _unpublished(client, ops, job_id, state)
                # P3: the key is scoped to the frontend peer (jobs.py:126-134).
                # Replay from that tab after capture; wait for the successful POST
                # in the backend access log before checking the durable identity.
                line = '"POST /engineering/v1/jobs HTTP/1.1" 202'
                prior = ops.stack.logs().count(line)
                CheckedBeam(page).button("Run reviewed beam checks").click()
                deadline = time.monotonic() + 20
                while ops.stack.logs().count(line) <= prior:
                    assert time.monotonic() < deadline, "No 202 for same-key replay from owning tab"
                    time.sleep(0.25)
                assert ops.db_rows("SELECT id FROM jobs WHERE brief=?", [brief_id]) == [[job_id]]
                _unpublished(client, ops, job_id, state)
        # P4: neither terminal record nor its empty publication changes on recovery.
        ops.stop("backend", 30)
        ops.start("backend")
        _healthy(client)
        for state in ("cancelled", "unavailable"):
            _unpublished(client, ops, obs[state]["job_id"], state)
        obs["idle"] = _idle(ops)
        obs["fresh_beam"] = _accepted(client, typed_beam, uuid4().hex)["job_id"]  # P5


def test_s15_expired_view(frontend: str, _browser: Browser, client: Api, ops: Ops, cases: CaseLog) -> None:
    with _case(cases, ops, "S15(expired)", "Read expired result after accelerating both expiries", "A") as obs:
        obs["image_ids"] = ops.stack.image_ids()
        with _browser_job(_browser, frontend, ops) as (page, _, job_id):
            job = client.wait_job(job_id, timeout_s=200)
            assert (job["outcome"], job["delivery"]) == ("accepted", "available")
            # First prove that this same tab offered the artifact before expiry.
            expect(page.get_by_role("button", name="Download package.zip", exact=True)).to_be_visible(timeout=45_000)
            ops.set_expiry(job_id, time.time() - 1)
            obs.update(_capture(page, "expired", job_id))
            expired = _json(client.get_job(job_id))
            assert expired["delivery"] == "expired" and expired["artifacts"] == {}
            assert client.artifact(job_id, "zip").status_code == 410
