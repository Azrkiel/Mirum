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

import contextlib
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


def test_post_jobs_returns_id_immediately() -> None:
    """A-01: POST /jobs MUST return within 200ms with `{job_id, status_url}`
    even when the pipeline would take a long time. We patch the long-running
    pipeline to a slow no-op so a buggy implementation that awaits it
    inline visibly trips the budget."""
    client = _client()

    async def _slow_no_op_pipeline(*args, **kwargs):
        # 3-second sleep simulates a slow LLM call. A correct route MUST
        # spawn this as a background task and return BEFORE awaiting it.
        import asyncio
        await asyncio.sleep(3.0)
        return None

    # Patch any module-level pipeline coroutine the route uses, if it exists.
    # The patch is a no-op when the symbol isn't there yet (red phase) — the
    # 404 from the missing route will still fail the test.
    patches = []
    try:
        import main
        if hasattr(main, "_pipeline"):
            patches.append(unittest.mock.patch.object(main, "_pipeline", _slow_no_op_pipeline))
    except Exception:
        pass
    try:
        import jobs as _jobs_mod  # type: ignore[import-not-found]
        if hasattr(_jobs_mod, "_pipeline"):
            patches.append(unittest.mock.patch.object(_jobs_mod, "_pipeline", _slow_no_op_pipeline))
    except Exception:
        pass

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)

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
