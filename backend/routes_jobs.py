"""POST /jobs route family — non-blocking job submission for the
text-to-CAD pipeline.

Wraps the existing `_pipeline` async generator (in main.py) into a
background asyncio task. The request handler returns a `{job_id,
status_url}` envelope immediately so the client never has to hold a
TCP socket open across the multi-minute Planner / Critic-loop call —
that is the entire point of Worktree A.

Subsequent endpoints (A-04 SSE replay, A-05 cancel) plug onto the same
JobRegistry stored at `app.state.job_registry`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("mirum.api.jobs")

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Pluggable pipeline reference. main.py sets this to its `_pipeline`
# coroutine after the symbol is defined; tests can monkey-patch it to a
# fast no-op so the contract test for "POST /jobs returns < 200ms" is
# deterministic without burning Gemini quota.
PipelineFn = Callable[[str], Any]
_pipeline: PipelineFn | None = None


def set_pipeline(fn: PipelineFn) -> None:
    """Wire the production pipeline coroutine. Called once at app startup."""
    global _pipeline
    _pipeline = fn


def _parse_pipeline_event(event_str: str) -> dict[str, Any]:
    """Decode one yield from the legacy `_pipeline` async generator.

    `_pipeline` yields SSE-formatted strings (`data: {...}\\n\\n`).
    Storing the raw string in the JobState forces the SSE replay route
    and any frontend consumer to do nested JSON-and-SSE parsing each
    time — and the frontend has to do the same dance after pretty-
    printing into its progress widget.

    Parse the inner payload here so JobState.events stores clean dicts,
    and `/jobs/{id}/events` can flatten them straight into a single
    JSON object per SSE frame. Falls back to `{"raw": event_str}` if
    the input doesn't decode (defensive — a future _pipeline refactor
    might emit a non-SSE control byte and we don't want to crash the
    runner over it)."""
    s = event_str.strip()
    if s.startswith("data: "):
        try:
            return json.loads(s[6:])
        except json.JSONDecodeError:
            pass
    return {"raw": event_str}


async def run_pipeline_into(state: Any, pipeline: PipelineFn) -> None:
    """Drain the pipeline async generator into a JobState.

    Extracted from `submit_job` so it can be unit-tested without a
    FastAPI TestClient (which spins up a fresh event loop per request,
    breaking any cross-request task lifetime — see test_jobs.py for the
    motivating bug). The state-machine contract is identical to what
    the route's background task does:

      QUEUED → RUNNING → (DONE | CANCELLED | FAILED)

    The finally clause uses TERMINAL_STATUSES so a BaseException
    landing before the RUNNING assignment still pins a terminal state
    (cf. round-2 reviewer feedback on commit d7aa8a9)."""
    from jobs import JobStatus  # local import to dodge circular

    try:
        state.status = JobStatus.RUNNING
        cancel = state._ensure_cancel()
        async for event_str in pipeline(state.prompt):
            await state.append_event(_parse_pipeline_event(event_str))
            if cancel.is_set():
                state.status = JobStatus.CANCELLED
                return
        state.status = JobStatus.DONE
    except Exception as e:  # noqa: BLE001 — top-level task guard
        logger.exception("job %s pipeline raised", state.job_id)
        state.status = JobStatus.FAILED
        state.error = str(e)[:1000]
    finally:
        state.pin_failed_if_not_terminal(
            "pipeline task ended without a terminal status"
        )


class JobsSubmitRequest(BaseModel):
    prompt: str = Field(..., max_length=5000)


@router.post("")
async def submit_job(body: JobsSubmitRequest, request: Request) -> dict[str, str]:
    """Spawn the pipeline as a background task and return the job id."""
    from jobs import JobRegistry, JobStatus  # local to dodge circular import

    registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if registry is None:
        # Fail loudly rather than silently lose work — the absence of a
        # registry means startup wiring (in main.py) is incomplete.
        raise HTTPException(
            status_code=503,
            detail="job registry not configured; check app startup wiring",
        )

    state = registry.submit(body.prompt)

    pipeline = _pipeline
    if pipeline is None:
        # Same logic as registry: surface misconfiguration immediately
        # rather than letting the background task fail silently.
        raise HTTPException(
            status_code=503,
            detail="pipeline not configured; check app startup wiring",
        )

    # Keep a strong reference on the JobState so Python's GC does NOT
    # drop the still-pending Task. asyncio.create_task only weakly
    # references the returned Task; the loop's pending-task list keeps
    # it alive while running, but a defensive ref here guards against
    # future refactors that might run jobs on a custom executor.
    state.task = asyncio.create_task(run_pipeline_into(state, pipeline))

    return {"job_id": state.job_id, "status_url": f"/jobs/{state.job_id}"}


@router.get("/{job_id}/events")
async def stream_events(
    job_id: str, request: Request, cursor: int = 0
) -> StreamingResponse:
    """SSE stream of job events. `cursor=N` skips events with seq < N
    (replay) and live-tails new ones. Closes when the job reaches a
    terminal status (DONE / FAILED / CANCELLED), emitting a final
    'complete' frame.

    Polling-based v1: the live-tail loop wakes every 100ms to drain
    any new events. Avoids the asyncio.Condition cross-await race that
    would otherwise need careful lock ordering. 100ms latency per
    event is imperceptible against multi-second pipeline phases."""
    from jobs import JobRegistry  # local to dodge circular import

    registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="job registry not configured")
    state = registry.get(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id}")

    async def _generate() -> AsyncGenerator[str, None]:
        next_cursor = max(0, cursor)
        while True:
            # Drain any buffered events strictly newer than next_cursor.
            # Each frame flattens the pipeline event (already a parsed
            # dict — see _parse_pipeline_event) into the outer envelope
            # so the frontend gets a single-shot {seq, timestamp, step,
            # status, ...} structure instead of nested JSON-in-SSE-in-
            # JSON.
            while next_cursor < len(state.events):
                ev = state.events[next_cursor]
                frame = {
                    "seq": ev.seq,
                    "timestamp": ev.timestamp,
                    **(ev.payload if isinstance(ev.payload, dict) else {"payload": ev.payload}),
                }
                yield f"data: {json.dumps(frame)}\n\n"
                next_cursor = ev.seq + 1

            # Terminal status closes the stream after one final frame.
            # Named `_stream_end` (not `complete`) because `_pipeline`
            # itself emits its own `step:"complete"` event with the
            # finished GLB; we don't want to overwrite that on the
            # frontend. `_stream_end` is the route's own "no more
            # events coming" signal — frontends ignore unknown steps.
            if state.is_terminal():
                terminal = {
                    "step": "_stream_end",
                    "status": state.status.value,
                    "error": state.error,
                }
                yield f"data: {json.dumps(terminal)}\n\n"
                return

            # Client disconnected? Stop streaming so the worker is freed.
            try:
                if await request.is_disconnected():
                    return
            except Exception:  # noqa: BLE001
                pass

            await asyncio.sleep(0.1)

    return StreamingResponse(_generate(), media_type="text/event-stream")


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> dict[str, str]:
    """A-05: cooperative cancellation. Sets state.cancel_event; the
    background runner sees it on the next iteration of its `async for`
    over the pipeline generator and transitions to CANCELLED.

    The legacy `_pipeline` is NOT modified to check more frequently in
    v1 — granularity is one check per pipeline yield, which fires after
    each pipeline phase (Planner, REA, each Machinist attempt). Multi-
    second phases mean the cancel takes at most one phase-length to
    land, which is acceptable. Tighter granularity is plan §4 A-05's
    'second pass' note and ships in a follow-up if user reports demand it."""
    from jobs import JobRegistry, JobStatus  # local to dodge circular import

    registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="job registry not configured")
    state = registry.get(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id}")

    if state.is_terminal():
        # Cancel a terminated job is a no-op. Return the current state
        # so the client can re-render its outcome instead of guessing.
        return {"job_id": job_id, "status": state.status.value}

    cancel = state._ensure_cancel()
    cancel.set()
    # If the runner hasn't reached its first iteration yet (still QUEUED),
    # eagerly mark CANCELLED here. The finally clause's gate will not
    # overwrite a terminal status, so this is safe.
    if state.status == JobStatus.QUEUED:
        state.status = JobStatus.CANCELLED
    return {"job_id": job_id, "status": state.status.value}


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    """Polling endpoint — JSON snapshot of the job state. The frontend
    polls this every 2s as a fallback when SSE is unavailable."""
    from jobs import JobRegistry  # local to dodge circular import

    registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="job registry not configured")
    state = registry.get(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id}")
    return {
        "job_id": state.job_id,
        "status": state.status.value,
        "events_count": len(state.events),
        "error": state.error,
    }
