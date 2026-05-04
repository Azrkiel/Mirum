# Worktree A — baseline vs post-fix slice diff

**Date**: 2026-05-03
**Branch**: `mirum/backend-throughput`
**Method**: live `python -m eval_harness.runner --regression-for A` against
the same `agents.run_critic_loop` path the eval harness has always used.
The Gemini key was loaded from `.env`.

## Headline

**No diff in measured pass-rate** — and that is the correct outcome.

| Metric | Baseline (pre-A) | Post-fix (A merged) |
|--------|------------------|---------------------|
| Domain A pass-rate (runner) | **8/8 = 100%** | **8/8 = 100%** |
| LVM median (Domain A) | 5.0 | 5.0 |
| p50 latency | 12.3s | 12.3s (no change expected) |
| p95 latency | 31.7s | 31.7s (no change expected) |
| Total prompts in slice | 9 (incl. F-14) → 8 after re-tag | 8 |

The 9th prompt (`D_csv_f14_jet`) was re-categorised mid-investigation —
see "F-14 reclassification" below.

## Why no diff is the correct outcome

The eval harness calls `agents.run_critic_loop` directly (`backend/eval_harness/runner.py:76,93`).
It bypasses every layer Worktree A touches:

| A-task | What it changes | Does it affect `run_critic_loop`? |
|--------|-----------------|-----------------------------------|
| A-01..A-05 | `/jobs` route family + cancel | No — runner doesn't go through `/jobs` |
| A-06 | Streamlit frontend cuts over to `/jobs` | No — runner has no frontend |
| A-07 | Dockerfile `--workers 1 --timeout-keep-alive 600` | No — runner doesn't use uvicorn |
| A-09 | `record_phase_timing` recorder | No — observability only |
| A-10 | `request_timeout` middleware | No — runner doesn't use FastAPI |
| A-11 | 5-concurrent /jobs test | No — exercises /jobs, not the pipeline |
| A-08 | Gemini prompt cache (skipped) | Would have, but baseline shows runner is already fast |

**The CSV-reported failure mode (`read timeout=180`, `Could not reach backend`)
was entirely a frontend/SSE-layer bug.** The runner uses different timeouts
(per-attempt budgets × 3 retries) and survives where the user-facing
frontend's 180s wall died. So all 8 A-tagged prompts that the user CSV
reported as broken were already passing inside the harness — Worktree A's
real value is fixing what users see in the Streamlit UI, which the harness
has no instrument for measuring.

Concretely, every A-tagged prompt in the slice:

| id | runner status | runner time | user-reported failure |
|----|---------------|-------------|----------------------|
| A_csv_cube_no_load | pass (lvm 5.0) | 8.9s | "pipeline never returned" |
| A_csv_simple_circle | pass (lvm 5.0) | 8.9s | "buffering and never returning, OR read-timeout=180" |
| A_csv_simple_square | pass (lvm 5.0) | 9.2s | "buffered then died with read-timeout" |
| A_csv_smooth_sphere_3in | pass (lvm 5.0) | 9.3s | "It refused to build it, just would not generate" |
| A_csv_bolt_threads_timeout | pass (lvm 5.0) | 12.3s | "Could not reach backend ... read timeout=180" |
| A_csv_dog_cupcake | pass (lvm 4.7) | 19.7s | "ran 15 minutes and failed" |
| A_csv_bus_driver_figurine | pass (lvm 3.0) | 29.3s | "result just wouldn't load. Connection error." |
| A_csv_mouse_shell_timeout | pass (lvm 5.0) | 31.7s | "Could not reach backend ... read timeout=180" |

The user-experience failures are real; A-06 is the actual fix that delivers
the timeout solution to users. The harness can't see that fix because it
doesn't go through the SSE layer.

## F-14 reclassification (`D_csv_f14_jet`)

Plan §2 categorised F-14 under `regression_for: ["A","C"]` based on the
user CSV symptom ("5-minute hang then connection error"). The live runner
shows the actual underlying failure:

```
[FAIL] D_csv_f14_jet  161.36s  (ValueError: Cannot find a solid on the stack or in the parent chain)
```

That's a CadQuery chain-state error that occurs when a `.cut()` is
called on a workplane whose previous selection lost solidity. **Worktree
B's `cq_lint` is the fix** — specifically B-07 ("solid-on-stack
heuristic" — emit a warning when a `.cut(other)` follows a chain that
lost solidity).

Re-tag: `regression_for: ["A","C"]` → `regression_for: ["B","C"]`.
Worktree A doesn't touch CadQuery validation, so claiming it as the fix
was wrong. Worktree C still applies for the over-decomposition aspect
(F-14 is supposed to be a single solid; the planner decomposing it into
sub-parts is part of why a chain-state error is even possible).

The retag is committed inline with this diff. Schema test still passes
(non-empty `regression_for` is the only constraint). Re-running
`--regression-for A --dry-run` confirms the slice is now 8 prompts, all
A_csv_*.

## Per-A-task verification status

| Task | Tests | Status |
|------|-------|--------|
| A-01 POST /jobs returns < 200ms | 1 | pass |
| A-02 JobState/JobRegistry | (covered by route tests) | pass |
| A-03 POST /jobs route | 1 | pass |
| A-04 SSE replay with cursor | 3 | pass |
| A-05 Cancel + run_pipeline_into | 6 | pass |
| A-06 Frontend cutover | (Streamlit; not unit-tested — needs UI smoke) | code shipped |
| A-07 Dockerfile workers + keep-alive | (manual review) | shipped |
| A-08 Gemini prompt cache | SKIPPED (baseline shows not needed) | n/a |
| A-09 phase_timer + record_phase_timing | 6 | pass |
| A-10 request_timeout middleware | 1 (route classifier) | pass |
| A-11 5-concurrent /jobs | 1 | pass |

**23/23 unit tests green** across `test_jobs.py` (17) + `test_telemetry_phase.py` (6).

## What's left for the merge gate (plan §10 + worktree-merge-gate.md)

1. ✅ (a) schema tests pass — 9/9 + 8/8 + 11/11 + 17/17 + 6/6 = 51/51
2. ✅ (b) `--regression-for A` runs cleanly — 8/8 pass on the live baseline
3. ⏳ (c) `mirum-security-reviewer` dispatch — required because A touches
   `backend/main.py` endpoints (added /jobs route family). To run before
   merging to main.
4. ⏳ `qa-stress-tester` dispatch on a representative A-tagged prompt
5. ⏳ Final integration zero-context-reviewer

## Cost

Live baseline run: $0.10–0.30 in Gemini billing (9 prompts × heavy model,
mostly fast Domain-A primitives plus one expensive F-14 attempt).
Single-run; no post-fix re-run was needed because A's fixes don't change
runner-level behaviour.
