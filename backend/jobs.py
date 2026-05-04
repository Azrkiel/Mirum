"""In-memory job registry for the /jobs route family.

v1 design — single uvicorn worker, per-process state. The frontend
submits to POST /jobs, polls GET /jobs/{id}, and streams progress from
GET /jobs/{id}/events?cursor=N. Long generations no longer hold the
client's TCP socket open across a multi-minute Planner call, which is
the root cause of the read-timeout=180 / "Could not reach backend"
failures the user-feedback CSV documented (see
backend/eval_harness/prompts.json `regression_for: ["A"]` entries).

Multi-worker note: this module's state is per-process. Routing a job
across workers requires a sticky cookie or a shared store (Redis).
Worktree A's v1 keeps `--workers 1` and proves the unblocking helps
before adding sticky routing — see plan §4 A-07.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# A status is "terminal" if no further state transitions are expected.
# Used by the runner's finally clause to decide whether to pin a stale
# QUEUED/RUNNING to FAILED. Centralised here so a future add to the
# status enum (e.g. TIMED_OUT in A-10) cannot silently bypass the gate.
TERMINAL_STATUSES = frozenset(
    {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED}
)


@dataclass
class JobEvent:
    """One pipeline-emitted event.

    Contract: `seq` is dense and contiguous (0, 1, 2, ...) — every
    event in JobState.events has `seq == its index`. The SSE replay
    endpoint relies on this when computing `next_cursor = ev.seq + 1`
    to skip already-seen events. A future refactor that filters
    events post-append (or otherwise introduces gaps) would silently
    break the replay loop's progress condition; pin the invariant
    here so the comment shows up at the source-of-truth file."""
    seq: int
    timestamp: float
    payload: dict[str, Any]


@dataclass
class JobState:
    job_id: str
    prompt: str
    status: JobStatus = JobStatus.QUEUED
    events: list[JobEvent] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    # asyncio primitives are constructed lazily so JobState() can be
    # instantiated outside an event loop (e.g. inside a sync test fixture).
    cancel_event: asyncio.Event | None = None
    new_event_cond: asyncio.Condition | None = None
    # Strong reference to the background runner so Python's GC does not
    # silently drop a still-pending pipeline task. asyncio.create_task
    # only weakly references the returned Task; without storing it we
    # have seen "Task was destroyed but it is pending" warnings.
    task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.monotonic)

    def _ensure_cancel(self) -> asyncio.Event:
        if self.cancel_event is None:
            self.cancel_event = asyncio.Event()
        return self.cancel_event

    def _ensure_cond(self) -> asyncio.Condition:
        if self.new_event_cond is None:
            self.new_event_cond = asyncio.Condition()
        return self.new_event_cond

    async def append_event(self, payload: dict[str, Any]) -> None:
        """Append a pipeline event and notify any waiters on the SSE
        replay endpoint. Sync-friendly variant lives in `append_event_nowait`."""
        ev = JobEvent(seq=len(self.events), timestamp=time.monotonic(), payload=payload)
        self.events.append(ev)
        cond = self._ensure_cond()
        async with cond:
            cond.notify_all()

    def append_event_nowait(self, payload: dict[str, Any]) -> None:
        """Append without notifying waiters. Use only when no SSE clients
        could be subscribed (e.g. inside the constructor of the runner
        before async waiters can attach)."""
        self.events.append(
            JobEvent(seq=len(self.events), timestamp=time.monotonic(), payload=payload)
        )

    def events_since(self, cursor: int) -> list[JobEvent]:
        if cursor < 0:
            cursor = 0
        return self.events[cursor:]

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def pin_failed_if_not_terminal(self, reason: str) -> None:
        """Idempotent: pin status to FAILED only if not already terminal.
        The runner calls this from its finally clause so that a
        BaseException (CancelledError on shutdown, KeyboardInterrupt)
        cannot leave the JobState stuck at QUEUED or RUNNING — which
        would make the polling endpoint lie about the job's outcome."""
        if not self.is_terminal():
            self.status = JobStatus.FAILED
            if self.error is None:
                self.error = reason


class JobRegistry:
    """Per-process map of job_id → JobState. v1 only."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}

    def submit(self, prompt: str) -> JobState:
        job_id = uuid.uuid4().hex
        state = JobState(job_id=job_id, prompt=prompt)
        self._jobs[job_id] = state
        return state

    def get(self, job_id: str) -> JobState | None:
        return self._jobs.get(job_id)

    def __len__(self) -> int:
        return len(self._jobs)
