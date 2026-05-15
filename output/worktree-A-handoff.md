# Worktree A handoff (snapshot 2026-05-03)

Branch `mirum/backend-throughput` at `C:\Users\Kevin\Developments\T2CIII-worktrees\backend-throughput\`.

## What's landed (5 commits since branch from `main` @ 47e20b2)

| SHA | Task(s) | Summary |
|-----|---------|---------|
| 533f04b | A-01..A-03 | `/jobs` route family: JobState/JobRegistry, POST /jobs (returns < 200ms), GET /jobs/{id} polling snapshot, wired in main.py |
| d7aa8a9 | review fix | Strong task ref on JobState, test patches `routes_jobs._pipeline` (the real reference), defensive finally |
| 6a31c4c | review fix | `TERMINAL_STATUSES` frozenset + `pin_failed_if_not_terminal` helper — gate catches QUEUED-stuck, not just RUNNING-stuck |
| f3849aa | A-04 | SSE replay endpoint `GET /jobs/{id}/events?cursor=N` (poll-based v1, 100ms tick, terminal frame on close) |
| c92330e | A-05 | Cancel route + `run_pipeline_into` extracted as testable top-level coroutine. Eager QUEUED→CANCELLED, set-event-only on RUNNING |

**Tests**: 15/15 passing in `backend/tests/test_jobs.py` (pure stdlib runnable, no pytest required).
F-suite still 28/28 (`test_prompts_schema.py` 9/9, `test_runner_filters.py` 8/8, `test_md_report.py` 11/11).

**Reviewer history**: 3 zero-context-reviewer rounds for A-01..A-03 (PASS round 3); A-04 + A-05 pending review (deferred — combined into a single round with A-06 or later).

## CRITICAL realisation — `--regression-for A` may not need most of A

`backend/eval_harness/runner.py:76,93` calls `run_critic_loop` **directly**, bypassing the FastAPI/SSE layer entirely:

```python
from agents import run_critic_loop
...
code, _ = await run_critic_loop(part, max_retries=max_retries, save_step=False)
```

This means:

| A task | Affects `--regression-for A` runner pass-rate? |
|--------|------------------------------------------------|
| A-01..A-05 (jobs/cancel/SSE) | **No** — runner doesn't go through `/jobs` |
| A-06 (frontend polling)       | **No** — runner doesn't go through frontend |
| A-07 (uvicorn workers)        | **No** — runner doesn't go through uvicorn |
| A-08 (Gemini prompt cache)    | **Yes** — same `agents.py` call sites the runner exercises; reduces per-call latency |
| A-09 (telemetry)              | No (observability only) |
| A-10 (timeout middleware)     | **No** — runner doesn't go through middleware |
| A-11 (5-concurrent /jobs test)| No — tests A-01..A-05 path, not runner |

The CSV failures the slice was built from were almost all FRONTEND-layer (read timeout=180, "Could not reach backend"). The `runner.py` path uses `max_retries=3` with per-attempt budgets that are different from the frontend's 180s wall — some of these prompts may already pass via runner today.

**Implication for "clean --regression-for A run" acceptance:**

Two scenarios:
1. The slice **already passes** via runner today (frontend was the bottleneck, not the pipeline). In which case Worktree A's value is real but indirect — it fixes the user-visible failure without changing what the harness measures. We need a live baseline to confirm.
2. The slice **fails** via runner — meaning real pipeline-level bugs (timeouts inside Planner/Critic loops, LLM stalls). In which case A-08 (prompt cache) is the primary lever, and the rest of A is value-add for end users but not for the harness.

A live baseline (`--regression-for A` with `GEMINI_API_KEY_HEAVY` set) is the only way to know which scenario we're in. Cost ≈ $0.50–$2 in Gemini billing.

## Plan-§4 task list status (kept intact in TaskList)

✅ A-01..A-05 done. Remaining:

- A-06 frontend polling (Streamlit edits, no slice impact)
- A-07 Dockerfile workers + keep-alive (trivial)
- A-08 Gemini prompt cache (the runner-affecting one)
- A-09 phase-timing telemetry
- A-10 backend-side timeout middleware
- A-11 5-concurrent /jobs test
- A-12 final `--regression-for A` run + baseline diff (needs GEMINI_API_KEY_HEAVY)
- merge gate: mirum-security-reviewer + qa-stress-tester

Plan §4 lives at `C:\Users\Kevin\.claude\plans\cached-questing-hartmanis.md` if needed.

## Open question for the user (raise after compaction)

Given the realisation above:

(a) Run a live baseline now to learn which scenario we're in?
(b) Continue with A-07..A-11 sequentially, save the live runs for A-12?
(c) Skip the runner-irrelevant tasks (A-06, A-10) and go straight to A-08 + A-12?
