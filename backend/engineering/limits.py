"""Process-wide provider-spend budget for AI draft preparation, and a small thread helper."""
import asyncio
from collections import deque
import time
from threading import Lock


class AiCallBudget:
    """Rolling-window budget of provider calls plus a small in-flight limit.

    Every provider call, including the repair call, reserves one unit. A reserved
    unit is never refunded: the call may already have been billed.
    """
    def __init__(self, daily_calls, concurrency, *, window_seconds=86400.0, clock=time.monotonic):
        self.daily_calls, self.concurrency = max(0, int(daily_calls)), max(1, int(concurrency))
        self.window, self._clock = window_seconds, clock
        self._calls: deque = deque()
        self._inflight = 0
        self._lock = Lock()

    @classmethod
    def from_config(cls):
        import config
        return cls(config.ENGINEERING_AI_DAILY_CALLS, config.ENGINEERING_AI_CONCURRENCY)

    def _prune(self, now):
        while self._calls and self._calls[0] <= now - self.window:
            self._calls.popleft()

    def seed(self, ages_seconds):
        """Restore spend after a restart: one entry per past call, ``age`` seconds old."""
        with self._lock:
            now = self._clock()
            self._calls = deque(sorted(self._calls + deque(now - age for age in ages_seconds if 0 <= age < self.window)))
            self._prune(now)

    def has_capacity(self):
        """Non-reserving pre-check used before any draft record is created."""
        with self._lock:
            self._prune(self._clock())
            return len(self._calls) < self.daily_calls and self._inflight < self.concurrency

    def reserve(self):
        with self._lock:
            now = self._clock(); self._prune(now)
            if len(self._calls) >= self.daily_calls or self._inflight >= self.concurrency:
                return False
            self._calls.append(now)
            self._inflight += 1
            return True

    def release(self):
        with self._lock:
            self._inflight = max(0, self._inflight - 1)

    def used(self):
        with self._lock:
            self._prune(self._clock())
            return len(self._calls)


async def run_in_thread_owned(function, *args, on_orphan):
    """``await asyncio.to_thread(function, *args)`` that cannot leak its result.

    If the awaiting task is cancelled, the thread keeps running; its result is then
    handed to ``on_orphan`` on the event loop instead of being dropped (for example to
    release a lease or start an already-admitted job).
    """
    future = asyncio.ensure_future(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        def orphaned(done):
            if done.cancelled():
                return
            if done.exception() is None:
                on_orphan(done.result())
        future.add_done_callback(orphaned)
        raise
