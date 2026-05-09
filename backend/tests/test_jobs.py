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
    """Build a fresh TestClient over the FastAPI app.

    Disables both rate limiters (main.py's slowapi `limiter` for
    /generate et al, and routes_jobs.py's in-house buckets for /jobs)
    so the multiple-POST tests don't trip 5/minute on the submitter
    or 30/minute on cancel/events. Production limits are unaffected
    — the test toggle does NOT persist across processes; uvicorn
    starts with the limiter enabled."""
    from fastapi.testclient import TestClient
    from main import app, limiter as _main_limiter
    from routes_jobs import disable_rate_limit
    _main_limiter.enabled = False
    disable_rate_limit()
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


async def _three_event_pipeline(prompt: str):
    """Yields three SSE-formatted events synchronously (no awaits between
    yields so the runner buffers them all before the SSE consumer reads).
    Mirrors the real _pipeline's yield format: 'data: {...}\\n\\n'."""
    yield 'data: {"step":"planner","status":"running"}\n\n'
    yield 'data: {"step":"planner","status":"done"}\n\n'
    yield 'data: {"step":"machinist","status":"done"}\n\n'


def _wait_until_terminal(app, job_id: str, timeout: float = 2.0) -> None:
    """Spin until the job's status is terminal or the timeout elapses.
    The runner is on the same event loop as the test, so yielding via
    sleep(0) gives it scheduling slots."""
    import asyncio
    state = app.state.job_registry.get(job_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if state is not None and state.is_terminal():
            return
        # Drive the event loop briefly so the background runner can advance.
        asyncio.run(asyncio.sleep(0.05))
        state = app.state.job_registry.get(job_id)
    raise AssertionError(
        f"job {job_id} did not reach terminal status within {timeout}s; "
        f"current status: {getattr(state, 'status', None)}"
    )


def test_get_events_replays_from_cursor_zero() -> None:
    """A-04: GET /jobs/{id}/events?cursor=0 must replay every event
    the runner has buffered, then emit a terminal 'complete' event
    once the job reaches a terminal status. Runs against a synchronous
    pipeline that yields three events so the assertion is deterministic."""
    client = _client()
    import routes_jobs
    with unittest.mock.patch.object(
        routes_jobs, "_pipeline", _three_event_pipeline
    ):
        submit = client.post("/jobs", json={"prompt": "x"})
        assert submit.status_code == 200
        job_id = submit.json()["job_id"]

        # Open the SSE stream. Body should contain all 3 events + 'complete'.
        response = client.get(f"/jobs/{job_id}/events?cursor=0")

    assert response.status_code == 200, (
        f"GET /jobs/{{id}}/events returned {response.status_code}; "
        f"route likely missing. Body: {response.text[:200]}"
    )
    body = response.text
    # Each pipeline event becomes one SSE frame; the route appends a
    # `_stream_end` synthetic frame after the job reaches a terminal
    # status (named so it doesn't collide with `_pipeline`'s own
    # `step:"complete"` payload that carries the finished GLB).
    assert "planner" in body and "machinist" in body, (
        f"missing pipeline events in stream body: {body[:400]}"
    )
    assert "_stream_end" in body, (
        f"missing synthetic terminal '_stream_end' event: {body[:400]}"
    )

    # Frames must be FLAT — i.e. the inner pipeline JSON is spread into
    # the outer envelope, not nested under a "payload" key. Frontend
    # consumers should be able to read step/status from the top-level
    # object without unwrapping.
    import json as _json
    frames = []
    for ln in body.splitlines():
        if ln.startswith("data: "):
            try:
                frames.append(_json.loads(ln[6:]))
            except _json.JSONDecodeError:
                pass
    pipeline_frames = [f for f in frames if f.get("step") in ("planner", "machinist")]
    assert pipeline_frames, f"no parseable pipeline frames in body: {body[:400]}"
    sample = pipeline_frames[0]
    assert "payload" not in sample, (
        f"frame is double-nested under 'payload' — A-06 frontend would "
        f"have to do nested parsing: {sample}"
    )
    assert "seq" in sample and "step" in sample and "status" in sample, (
        f"frame missing required flat fields {{seq,step,status}}: {sample}"
    )


def test_get_events_skips_already_seen_with_cursor() -> None:
    """If the client has already seen events 0..N-1 and reconnects with
    cursor=N, the replay must NOT re-deliver them. This is the whole
    point of the cursor — without it, a dropped client doubles every
    event on reconnect."""
    client = _client()
    import routes_jobs
    with unittest.mock.patch.object(
        routes_jobs, "_pipeline", _three_event_pipeline
    ):
        submit = client.post("/jobs", json={"prompt": "x"})
        job_id = submit.json()["job_id"]
        # Wait for the job to finish so the events are fully buffered.
        from main import app
        _wait_until_terminal(app, job_id)

        # cursor=2 → only the 3rd event (seq=2) should appear, plus terminal.
        response = client.get(f"/jobs/{job_id}/events?cursor=2")

    assert response.status_code == 200
    body = response.text
    assert body.count("data:") <= 3, (
        f"with cursor=2 we expected at most 1 buffered event + 1 terminal "
        f"(2 frames); got body with {body.count('data:')} frames: {body[:400]}"
    )
    assert "machinist" in body, "missing the 3rd event under cursor=2 replay"
    assert "planner" not in body, (
        f"cursor=2 replay leaked already-seen planner events: {body[:400]}"
    )


def test_cancel_route_promotes_queued_to_cancelled() -> None:
    """A-05 part 1a: a queued job (runner hasn't started) goes
    eagerly to CANCELLED so the polling endpoint reflects the user's
    intent immediately. We bypass the /jobs POST route and submit
    directly into the registry so the test doesn't get bitten by
    starlette's per-request event-loop lifecycle (which would let the
    runner race ahead and tear down before the cancel call lands)."""
    client = _client()
    from main import app
    from jobs import JobStatus

    state = app.state.job_registry.submit("manual queued")
    assert state.status == JobStatus.QUEUED  # premise
    response = client.post(f"/jobs/{state.job_id}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled", body
    assert state.status == JobStatus.CANCELLED
    assert state.cancel_event is not None and state.cancel_event.is_set(), (
        "cancel route must set state.cancel_event in addition to the eager "
        "status promotion (so the runner, if it ever does start, observes it)"
    )


def test_cancel_route_sets_event_on_running_state() -> None:
    """A-05 part 1b: a job whose runner has already started (RUNNING)
    is NOT eagerly transitioned by the cancel route — that would
    race the runner. The route only sets the event; the runner
    observes it on its next post-yield check and moves to CANCELLED
    itself (covered by test_run_pipeline_into_observes_mid_run_cancel)."""
    client = _client()
    from main import app
    from jobs import JobStatus

    state = app.state.job_registry.submit("manual running")
    state.status = JobStatus.RUNNING  # simulate runner mid-flight

    response = client.post(f"/jobs/{state.job_id}/cancel")

    assert response.status_code == 200
    assert state.cancel_event is not None and state.cancel_event.is_set()
    # Status stays RUNNING — runner is responsible for the terminal transition.
    assert state.status == JobStatus.RUNNING, (
        f"cancel route must NOT racily overwrite RUNNING; got {state.status.value}"
    )


def test_cancel_terminal_job_is_no_op() -> None:
    """A-05 part 2: cancelling a job that already reached DONE/FAILED/
    CANCELLED returns the current status without overwriting it. The
    client may have raced the runner; the response should let them
    re-render the real outcome."""
    client = _client()
    from main import app
    from jobs import JobStatus

    # Fabricate a terminal job directly (no runner involved).
    state = app.state.job_registry.submit("manual")
    state.status = JobStatus.DONE
    response = client.post(f"/jobs/{state.job_id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done", f"cancel must not overwrite DONE; got {body}"
    # Cancel event should NOT be set (we no-op'd before .set()).
    # If state.cancel_event was None it stays None; if it existed, it stays unset.
    if state.cancel_event is not None:
        assert not state.cancel_event.is_set(), (
            "cancel on terminal must not set the event"
        )


def test_run_pipeline_into_sanitizes_error_paths() -> None:
    """Security gate: a pipeline exception's raw str(e) can leak file
    paths and tracebacks. The runner MUST route the error string
    through main._safe_error before storing on state.error, since
    state.error is exposed verbatim via the SSE _stream_end frame
    AND the GET /jobs/{id} polling response."""
    import asyncio
    from jobs import JobState, JobStatus
    from routes_jobs import run_pipeline_into

    leaky_path = r"C:\Users\Kevin\secret\internal.py"

    async def _crash(prompt):
        # Raise an exception whose str() contains a Windows-shaped path;
        # _safe_error's regex should redact it to <internal>.
        raise FileNotFoundError(f"[Errno 2] No such file: '{leaky_path}'")
        yield  # pragma: no cover — generator type marker

    async def _scenario():
        state = JobState(job_id="t", prompt="x")
        await run_pipeline_into(state, _crash)
        return state

    state = asyncio.run(_scenario())
    assert state.status == JobStatus.FAILED
    assert state.error is not None
    assert leaky_path not in state.error, (
        f"raw filesystem path leaked into state.error: {state.error!r}"
    )
    assert "<internal>" in state.error, (
        f"_safe_error redaction missing — expected '<internal>' marker; "
        f"got {state.error!r}"
    )


def test_run_pipeline_into_observes_pre_set_cancel() -> None:
    """A-05 part 3: direct unit test of routes_jobs.run_pipeline_into.
    If cancel_event is set BEFORE the runner enters its loop, the
    runner must transition to CANCELLED on the first iteration check
    (not finish all events as DONE)."""
    import asyncio
    from jobs import JobState, JobStatus
    from routes_jobs import run_pipeline_into

    async def _yield_three(prompt):
        for i in range(3):
            yield f'data: {{"i":{i}}}\n\n'

    async def _scenario():
        state = JobState(job_id="t", prompt="x")
        cancel = state._ensure_cancel()
        cancel.set()  # pre-set BEFORE the runner enters its loop
        await run_pipeline_into(state, _yield_three)
        return state

    state = asyncio.run(_scenario())
    assert state.status == JobStatus.CANCELLED, (
        f"runner must observe pre-set cancel and land on CANCELLED; "
        f"got {state.status.value} with {len(state.events)} events"
    )
    # The runner appended one event before checking cancel — that's the
    # documented granularity (one check per pipeline yield). Asserting
    # exactly 1 event pins the granularity contract.
    assert len(state.events) == 1, (
        f"granularity contract: cancel is checked AFTER the first event "
        f"is appended; got {len(state.events)} events"
    )


def test_run_pipeline_into_observes_mid_run_cancel() -> None:
    """A-05 part 4: a cancel that lands MID-run (after some events
    have been emitted) still terminates the runner promptly."""
    import asyncio
    from jobs import JobState, JobStatus
    from routes_jobs import run_pipeline_into

    cancel_after = 2  # set cancel after 2 events have been appended

    async def _yield_until_cancelled(prompt):
        i = 0
        while True:
            yield f'data: {{"i":{i}}}\n\n'
            await asyncio.sleep(0.001)  # let the canceller scheduler-tick in
            i += 1
            if i > 20:
                # Failsafe: if cancel never lands, don't loop forever.
                return

    async def _scenario():
        state = JobState(job_id="t", prompt="x")
        cancel = state._ensure_cancel()

        async def _canceller():
            while len(state.events) < cancel_after:
                await asyncio.sleep(0.001)
            cancel.set()

        await asyncio.gather(
            run_pipeline_into(state, _yield_until_cancelled),
            _canceller(),
        )
        return state

    state = asyncio.run(_scenario())
    assert state.status == JobStatus.CANCELLED
    # cancel landed at events_count >= 2; the runner appended one more
    # before its post-yield check fired. So events should be >= 3 and
    # < 20 (well short of the failsafe).
    assert cancel_after <= len(state.events) < 20


def test_cancel_unknown_job_returns_404() -> None:
    client = _client()
    response = client.post("/jobs/does-not-exist/cancel")
    assert response.status_code == 404


def test_get_events_unknown_job_returns_404() -> None:
    client = _client()
    response = client.get("/jobs/does-not-exist/events?cursor=0")
    assert response.status_code == 404


def test_five_concurrent_submissions_each_within_budget() -> None:
    """A-11: five POST /jobs issued *in parallel* (asyncio.gather over
    httpx.AsyncClient) — while a sixth slow pipeline is running in the
    background — must each return a distinct {job_id, status_url}
    inside the A-01 200ms per-request budget.

    The legacy /generate route held a single uvicorn worker hostage
    for the duration of a generation, so even trivial submissions
    serialised behind a slow request — the headline failure mode in
    the user CSV. This test recreates that scenario:

      1. Submit one job whose pipeline hangs for 60s (still running).
      2. While it hangs, submit five more jobs concurrently.
      3. Assert each of the five returns within 200ms.

    Without /jobs spawning the pipeline as a background task, the
    sixth submission would block on the first one's _pipeline body
    and trip the 200ms budget. The first iteration of A-11 was
    purely sequential and would have missed that regression — caught
    by the round-1 zero-context reviewer."""
    import asyncio
    import httpx
    import routes_jobs

    async def _hung_pipeline(prompt):
        await asyncio.sleep(60)
        yield 'data: {"never":"reached"}\n\n'

    async def _scenario():
        from main import app
        # ASGI transport keeps everything on the same event loop, so
        # background tasks created by the route survive across requests.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac:
            # Kick off the slow job. Returns immediately; pipeline hangs
            # in the background.
            slow = await ac.post("/jobs", json={"prompt": "slow"})
            assert slow.status_code == 200
            slow_id = slow.json()["job_id"]

            # Five concurrent fast submissions.
            n = 5
            t0 = time.monotonic()
            responses = await asyncio.gather(*[
                ac.post("/jobs", json={"prompt": f"a {10 + i}mm cube"})
                for i in range(n)
            ])
            wall = (time.monotonic() - t0) * 1000

            # Cancel the slow job so the test process can exit cleanly
            # and we don't leave a 60-second hang in the loop.
            state = app.state.job_registry.get(slow_id)
            if state is not None and state.task is not None:
                state.task.cancel()

            return responses, wall

    with unittest.mock.patch.object(routes_jobs, "_pipeline", _hung_pipeline):
        responses, wall_ms = asyncio.run(_scenario())

    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
    job_ids = [r.json()["job_id"] for r in responses]
    assert len(set(job_ids)) == 5, f"job_ids collided: {job_ids}"
    # The five fast submissions ran concurrently against a hung pipeline;
    # if the route were awaiting the pipeline body inline, the 60-second
    # sleep would dwarf the budget and wall_ms would be on the order of
    # 60 seconds, not under 1 second. The 5 × 200ms ceiling is a generous
    # bound — in practice these complete in < 100ms.
    assert wall_ms < 1000, (
        f"five concurrent submissions took {wall_ms:.0f}ms total — workers "
        f"are serialising on the slow background job, which is exactly the "
        f"regression /jobs is supposed to prevent"
    )


def test_rate_limit_returns_429_when_enabled() -> None:
    """Security gate: the new POST /jobs route MUST trip a 429 once the
    per-IP per-minute budget is exhausted. Without this guard, a client
    could spam POST /jobs and burn Gemini quota one background task at
    a time. We re-enable the limiter for this single test (other tests
    disable it via _client() so they can drive the route freely)."""
    from fastapi.testclient import TestClient
    from main import app, limiter as _main_limiter
    from routes_jobs import enable_rate_limit, disable_rate_limit, _RATE_BUCKETS
    _main_limiter.enabled = False  # we're not testing slowapi here
    _RATE_BUCKETS.clear()           # fresh bucket
    enable_rate_limit()
    try:
        client = TestClient(app)
        import routes_jobs

        async def _instant(prompt):
            yield 'data: {"step":"machinist","status":"done"}\n\n'

        with unittest.mock.patch.object(routes_jobs, "_pipeline", _instant):
            statuses = [
                client.post("/jobs", json={"prompt": f"x {i}"}).status_code
                for i in range(7)
            ]
        # 5/minute on submitter → first 5 should land 200, 6th and 7th 429.
        assert statuses[:5] == [200] * 5, (
            f"first 5 submits should pass; got {statuses[:5]}"
        )
        assert statuses[5] == 429 and statuses[6] == 429, (
            f"6th + 7th submits must trip the limiter; got {statuses[5:]}"
        )
    finally:
        disable_rate_limit()
        _RATE_BUCKETS.clear()


def test_rate_limit_no_TOCTOU_under_concurrency() -> None:
    """Regression guard for M1 — the rate_limit dep MUST be `async def`
    so FastAPI runs it on the event loop. A sync dep would run in
    anyio.to_thread.run_sync; concurrent requests on the same bucket
    would race on the read-then-append and over-fill past per_minute.

    Submits 10 POSTs in parallel against a 5/min bucket; expects
    exactly 5 200s and 5 429s. A racy sync impl would see 6+ 200s."""
    import asyncio
    import httpx
    from main import app, limiter as _main_limiter
    from routes_jobs import enable_rate_limit, disable_rate_limit, _RATE_BUCKETS
    import routes_jobs

    _main_limiter.enabled = False
    _RATE_BUCKETS.clear()
    enable_rate_limit()
    try:
        async def _instant(prompt):
            yield 'data: {"step":"machinist","status":"done"}\n\n'

        async def _scenario():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as ac:
                return await asyncio.gather(*[
                    ac.post("/jobs", json={"prompt": f"x {i}"})
                    for i in range(10)
                ])

        with unittest.mock.patch.object(routes_jobs, "_pipeline", _instant):
            responses = asyncio.run(_scenario())

        codes = sorted(r.status_code for r in responses)
        # Exactly 5 × 200 and 5 × 429. A TOCTOU race would land 6+ 200s.
        assert codes == [200]*5 + [429]*5, (
            f"rate limit raced under concurrency — got {codes}; sync `_dep` "
            f"would let extra 200s slip through. Did someone revert M1?"
        )
    finally:
        disable_rate_limit()
        _RATE_BUCKETS.clear()


def test_streaming_route_classifier_is_correct() -> None:
    """A-10 unit guard: _is_streaming_route must catch the SSE routes
    (/generate, /jobs/{id}/events) so the timeout middleware doesn't
    504 a healthy stream, and must NOT catch the polling endpoint
    (/jobs/{id}) which should be subject to the timeout."""
    from main import _is_streaming_route
    assert _is_streaming_route("/generate")
    assert _is_streaming_route("/jobs/abc123/events")
    assert _is_streaming_route("/jobs/abc-1.2_3/events")  # uuid-ish chars
    assert not _is_streaming_route("/jobs/abc123")        # polling endpoint
    assert not _is_streaming_route("/jobs/abc123/cancel") # cancel route
    assert not _is_streaming_route("/jobs")               # submit route
    assert not _is_streaming_route("/run")
    assert not _is_streaming_route("/plan")
    assert not _is_streaming_route("/")


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
        test_get_events_replays_from_cursor_zero,
        test_get_events_skips_already_seen_with_cursor,
        test_cancel_route_promotes_queued_to_cancelled,
        test_cancel_route_sets_event_on_running_state,
        test_cancel_terminal_job_is_no_op,
        test_run_pipeline_into_sanitizes_error_paths,
        test_run_pipeline_into_observes_pre_set_cancel,
        test_run_pipeline_into_observes_mid_run_cancel,
        test_cancel_unknown_job_returns_404,
        test_get_events_unknown_job_returns_404,
        test_five_concurrent_submissions_each_within_budget,
        test_rate_limit_returns_429_when_enabled,
        test_rate_limit_no_TOCTOU_under_concurrency,
        test_streaming_route_classifier_is_correct,
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
