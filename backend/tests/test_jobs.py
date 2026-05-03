"""Job-submission contract tests for the /jobs route family.

Pure-Python; runnable via `python backend/tests/test_jobs.py` (no pytest).
The contract A-01 pins:

  POST /jobs returns within 200ms with `{"job_id": "...", "status_url": "..."}`
  EVEN IF the underlying pipeline would take 5 minutes.

This is the entire point of Worktree A: take the long generation off the
synchronous SSE path so the client never has to hold a TCP socket open
across a 4-minute Planner call. Today (red phase) /jobs doesn't exist —
the test surfaces that as 404. After A-02/A-03 land, it should turn green.
"""
from __future__ import annotations

import os
import sys
import time
import unittest.mock
from pathlib import Path

# Set a placeholder Gemini key BEFORE importing main.py, which calls
# genai.configure() at module load and refuses to import otherwise. The
# key isn't used by this test — the contract is "/jobs returns the id
# immediately, before the pipeline runs," so we never reach a real
# Gemini call inside the 200ms window.
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-placeholder-key")

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))


def _client():
    """Build a fresh TestClient over the FastAPI app."""
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


async def _slow_async_generator_pipeline(prompt: str):
    """An async generator that hangs for 3 seconds before yielding anything.
    A correct /jobs route MUST schedule this as a background task and
    return its job_id BEFORE the first yield — i.e. before any sleep
    elapses on the request handler's coroutine."""
    import asyncio
    await asyncio.sleep(3.0)
    yield 'data: {"step":"machinist","status":"done"}\n\n'


def test_post_jobs_returns_id_immediately() -> None:
    """A-01: POST /jobs MUST return within 200ms with `{job_id, status_url}`
    even when the pipeline would take a long time.

    We patch `routes_jobs._pipeline` (the actual reference the route
    reads, set during main.py wiring via `routes_jobs.set_pipeline(...)`)
    so a regression that awaited the pipeline inline would visibly trip
    the 200ms budget. Patching `main._pipeline` alone would NOT bite
    because the route doesn't dereference main's symbol — it reads its
    own module-global. (This was caught by zero-context review of the
    initial commit.)"""
    client = _client()

    import routes_jobs
    assert routes_jobs._pipeline is not None, (
        "test premise broken: routes_jobs._pipeline must be wired by "
        "main.py at import time; if this fails, app startup wiring "
        "was changed and the test patch would no longer cover the route"
    )

    with unittest.mock.patch.object(
        routes_jobs, "_pipeline", _slow_async_generator_pipeline
    ):
        t0 = time.monotonic()
        response = client.post("/jobs", json={"prompt": "a 10mm cube"})
        elapsed_ms = (time.monotonic() - t0) * 1000

    assert response.status_code == 200, (
        f"POST /jobs returned {response.status_code}; route likely missing. "
        f"Body: {response.text[:200]}"
    )
    assert elapsed_ms < 200, (
        f"POST /jobs took {elapsed_ms:.0f}ms — must return < 200ms even when the "
        f"pipeline would take seconds. The route is probably awaiting the "
        f"pipeline inline instead of spawning it as a background task."
    )

    body = response.json()
    assert "job_id" in body, f"response missing job_id: {body}"
    assert "status_url" in body, f"response missing status_url: {body}"
    assert isinstance(body["job_id"], str) and body["job_id"], (
        f"job_id must be a non-empty string, got {body['job_id']!r}"
    )
    # status_url should be a path the client can poll. Convention: `/jobs/<id>`.
    assert body["status_url"].endswith(body["job_id"]), (
        f"status_url should reference the returned job_id; got {body['status_url']!r}"
    )


def test_background_task_is_strongly_referenced() -> None:
    """Regression guard: asyncio.create_task only weakly references the
    returned Task. The route must store a strong ref on JobState so the
    GC does not drop a still-pending pipeline task. See zero-context
    review feedback on commit 533f04b."""
    client = _client()
    import routes_jobs
    with unittest.mock.patch.object(
        routes_jobs, "_pipeline", _slow_async_generator_pipeline
    ):
        response = client.post("/jobs", json={"prompt": "a 10mm cube"})
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    # Reach into the registry on app.state to confirm the JobState
    # actually holds a Task.
    from main import app
    state = app.state.job_registry.get(job_id)
    assert state is not None, "registry lost the job"
    assert state.task is not None, (
        "JobState.task is None — submit_job is not storing the background "
        "task reference; Python may GC the pipeline mid-run"
    )
    assert not state.task.done() or state.task.cancelled(), (
        "task should be pending (3s sleeper has not finished)"
    )


def test_pin_failed_promotes_queued_to_failed() -> None:
    """Round-2 review concern 3: gate must catch QUEUED, not just RUNNING.
    A BaseException injected at task-start (before line 71's RUNNING
    assignment) leaves the JobState at its initial QUEUED. The finally
    must still pin a terminal status — otherwise the polling endpoint
    lies about the job's outcome."""
    from jobs import JobState, JobStatus
    state = JobState(job_id="x", prompt="x")  # constructor leaves status=QUEUED
    assert state.status == JobStatus.QUEUED
    state.pin_failed_if_not_terminal("ran but never started")
    assert state.status == JobStatus.FAILED
    assert state.error == "ran but never started"


def test_pin_failed_promotes_running_to_failed() -> None:
    from jobs import JobState, JobStatus
    state = JobState(job_id="x", prompt="x")
    state.status = JobStatus.RUNNING
    state.pin_failed_if_not_terminal("died without setting terminal")
    assert state.status == JobStatus.FAILED
    assert state.error == "died without setting terminal"


def test_pin_failed_is_no_op_on_terminal() -> None:
    """Idempotence: a job that already reached DONE / FAILED / CANCELLED
    must not be overwritten by the finally clause running last."""
    from jobs import JobState, JobStatus
    for terminal in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
        state = JobState(job_id="x", prompt="x")
        state.status = terminal
        state.error = "real reason"
        state.pin_failed_if_not_terminal("would overwrite if not idempotent")
        assert state.status == terminal, f"helper overwrote terminal {terminal!r}"
        assert state.error == "real reason", "helper overwrote a real error message"


def test_post_jobs_rejects_oversized_prompt() -> None:
    """A-01 boundary: the existing GenerateRequest has max_length=5000 on
    the prompt field. The /jobs route should inherit that bound — a
    50_000-char prompt must be rejected with 422 before any work spawns."""
    client = _client()
    huge = "x" * 50_000
    response = client.post("/jobs", json={"prompt": huge})
    assert response.status_code in (400, 422), (
        f"oversized prompt should be rejected with 400/422, got {response.status_code}"
    )


def _run_all() -> int:
    funcs = [
        test_post_jobs_returns_id_immediately,
        test_background_task_is_strongly_referenced,
        test_pin_failed_promotes_queued_to_failed,
        test_pin_failed_promotes_running_to_failed,
        test_pin_failed_is_no_op_on_terminal,
        test_post_jobs_rejects_oversized_prompt,
    ]
    failures = 0
    for fn in funcs:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {fn.__name__}")
    print(f"\n{len(funcs) - failures}/{len(funcs)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
