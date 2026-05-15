# Mirum bug-fix worktree handoff (snapshot 2026-05-03)

Brief continuity note so post-compact context is grounded. The plan and
per-worktree task lists live at:

- `C:\Users\Kevin\.claude\plans\cached-questing-hartmanis.md` (plan §1–§12)
  - Worktree A spec: §4
  - Task → Do → Verify loop: §3
  - Bug triage table: §2

## Worktree F (qa-harness) — DONE

Branch `mirum/qa-harness` at `C:\Users\Kevin\Developments\T2CIII-worktrees\qa-harness\`.
10 commits since branch point `fe0c896`. All three pure-stdlib test files
green (28/28). Final reviewer PASS on 2026-05-03.

Shipped:
- `backend/eval_harness/_prompts_loader.py`, `_md_report.py`
- `backend/eval_harness/runner.py` — `--regression-for {A,B,C,D,E}`,
  `--dry-run`, `--report-md PATH` (heavy imports deferred so dry-run is
  dep-free)
- `backend/eval_harness/prompts.json` — 20 CSV-sourced regression entries
  with `regression_for` tags
- `backend/tests/test_prompts_schema.py`, `test_runner_filters.py`,
  `test_md_report.py`
- `.claude/agents/qa-stress-tester.md`, `.claude/agents/zero-context-reviewer.md`
- `.claude/rules/worktree-merge-gate.md`
- `backend/eval_harness/README.md`

## Worktree A (backend-throughput) — STARTING NOW

Branch `mirum/backend-throughput` at
`C:\Users\Kevin\Developments\T2CIII-worktrees\backend-throughput\`.
Spec in plan §4. Acceptance per user:
1. `--regression-for A` run is clean (no failing prompts).
2. `qa-stress-tester` agent dispatched and PASSes.
3. `zero-context-reviewer` agent dispatched and PASSes.
4. Failing prompts surfaced from the **baseline** (pre-fix) before any code
   change, then diffed against the post-fix run.

A slice (from `python -m eval_harness.runner --regression-for A --dry-run`):

| id | domain | difficulty | regression_for |
|----|--------|------------|----------------|
| A_csv_cube_no_load | A | easy | A |
| A_csv_simple_circle | A | easy | A |
| A_csv_simple_square | A | easy | A |
| A_csv_dog_cupcake | A | medium | A |
| A_csv_bus_driver_figurine | A | medium | A |
| A_csv_smooth_sphere_3in | A | easy | A |
| A_csv_bolt_threads_timeout | A | medium | A |
| A_csv_mouse_shell_timeout | A | medium | A |
| D_csv_f14_jet | D | hard | A,C |

9 prompts. All sourced from the user-feedback CSV (4/27–4/30/2026); each
prompt's `notes` field documents the original failure mode.

## Worktree A task list (plan §4)

A-01 failing test for /jobs job-submission contract (returns within 200ms).
A-02 `JobState` + `JobRegistry` in `backend/jobs.py`.
A-03 `POST /jobs` route in `backend/routes_jobs.py`.
A-04 SSE replay endpoint with cursor.
A-05 Cancel route + cancel_event propagation.
A-06 Frontend polling client (replace held-open SSE).
A-07 Bump uvicorn workers + keep-alive in Dockerfile.
A-08 Gemini prompt-cache hit at 5 call sites in agents.py.
A-09 Per-call wall-time telemetry in telemetry.py.
A-10 Backend-side request timeout middleware.
A-11 Test: 5 concurrent prompts complete without timeouts.
A-12 Run QA harness with all CSV "Could not reach backend" prompts.

## Security gate (per CLAUDE.md)

Worktree A touches `backend/main.py` endpoints → `mirum-security-reviewer`
required before merge. Track this; do not skip it.
