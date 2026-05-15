# Worktree A merge-gate state (snapshot 2026-05-04)

This file captures the exact state needed to resume the merge gate after
context compaction. Companion to `output/worktree-A-handoff.md` (which
captured the mid-implementation snapshot).

## Branch / commits

- Branch: `mirum/backend-throughput`
- Worktree: `C:\Users\Kevin\Developments\T2CIII-worktrees\backend-throughput`
- HEAD: `5bc930d` (eval_harness: live A baseline + F-14 retag from "A" to "B")
- Diff range vs main: `47e20b2..5bc930d` (14 commits)

## Worktree A — implementation status

ALL 12 tasks resolved (A-08 explicitly skipped):

| Task | Commit | Notes |
|------|--------|-------|
| A-01 POST /jobs returns < 200ms | 533f04b | + d7aa8a9, 6a31c4c (review fixups) |
| A-02 JobState/JobRegistry | 533f04b | + 6a31c4c (TERMINAL_STATUSES) |
| A-03 POST /jobs route | 533f04b | + d7aa8a9 (strong task ref) |
| A-04 SSE replay with cursor | f3849aa | + bfd7119 (flat-frame format) |
| A-05 Cancel + run_pipeline_into | c92330e | extracted top-level coroutine |
| A-06 Frontend cutover | bfd7119 | Streamlit /generate → /jobs |
| A-07 Dockerfile workers + keep-alive | d70de87 | --workers 1 --timeout-keep-alive 600 |
| A-08 Gemini prompt cache | SKIPPED | Live baseline shows runner already fast (8.9s p50) |
| A-09 phase_timer + record_phase_timing | 8f0ba02 | telemetry recorder + ctx mgr |
| A-10 request_timeout middleware | d70de87 | + 07128c3 (audit_log ordering fix) |
| A-11 5-concurrent /jobs test | eb7b73c | + 07128c3 (rewritten as actually concurrent) |
| A-12 Final live run + diff | 5bc930d | 8/8 Domain A pass; F-14 retagged to B |

## Tests

51/51 total green:

- `backend/tests/test_prompts_schema.py` 9/9
- `backend/tests/test_runner_filters.py` 8/8
- `backend/tests/test_md_report.py` 11/11
- `backend/tests/test_jobs.py` 17/17
- `backend/tests/test_telemetry_phase.py` 6/6

To re-verify post-compact:
```bash
cd C:/Users/Kevin/Developments/T2CIII-worktrees/backend-throughput
python backend/tests/test_prompts_schema.py
python backend/tests/test_runner_filters.py
python backend/tests/test_md_report.py
python backend/tests/test_jobs.py
python backend/tests/test_telemetry_phase.py
```

## Live baseline data (DON'T re-run — costs Gemini quota)

`output/qa-reports/2026-05-03-A-baseline.md` — markdown table, 9 rows
`output/qa-reports/2026-05-03-A-baseline-stdout.txt` — runner log
`output/qa-reports/2026-05-03-A-baseline-vs-postfix.md` — analysis writeup

Headline: Domain A 8/8 = 100% pass at LVM median 5.0 / p50 12.3s / p95 31.7s.
Only failure was D_csv_f14_jet (CadQuery chain-state error after 161s),
which has been retagged from regression_for=["A","C"] to ["B","C"]. The
A slice is now 8 prompts and the live baseline shows them all green.

## Merge gate status (per .claude/rules/worktree-merge-gate.md)

- (a) **Schema tests pass** — ✅ 28/28 schema/filter/reporter, 17/17 jobs, 6/6 telemetry
- (b) **Slice clean** — ✅ 8/8 Domain A pass on live runner baseline
- (c) **mirum-security-reviewer** — ⏳ **BLOCKED**: agent dispatch failed at 2026-05-03 ~22:00ET with `You're out of extra usage · resets 2:30am (America/New_York)`. Re-dispatch after 2:30am ET.
- (d) **qa-stress-tester** — ⏳ deferred (costs ~$1-3 Gemini); user discretion
- (e) **Final zero-context-reviewer on A-06+A-12** — ⏳ **BLOCKED**: same quota issue.

## Re-dispatch the security reviewer (post-quota-reset)

```
Agent({
  description: "Security review Worktree A",
  subagent_type: "superpowers:code-reviewer",
  prompt: <see verbatim prompt below>
})
```

Verbatim prompt for the security reviewer dispatch:

> You are running the mirum-security-reviewer audit on Worktree A. The
> agent definition lives at `.claude/agents/mirum-security-reviewer.md` in
> the worktree at C:\Users\Kevin\Developments\T2CIII-worktrees\backend-
> throughput. Follow its workflow exactly.
>
> Scope: the full diff on branch mirum/backend-throughput vs `main`
> (commits between 47e20b2 and HEAD = 5bc930d). Worktree A's purpose:
> non-blocking job submission to fix the user-CSV "Could not reach
> backend ... read timeout=180" failures. Touched surfaces of security
> interest:
>
> - `backend/main.py`: added 2 new middleware (request_timeout, audit_log
>   reordered), wired in /jobs router
> - `backend/routes_jobs.py` NEW: POST /jobs, GET /jobs/{id}, GET
>   /jobs/{id}/events (SSE), POST /jobs/{id}/cancel
> - `backend/jobs.py` NEW: in-memory JobRegistry, JobState, JobEvent
> - `backend/Dockerfile`: --workers 1 + --timeout-keep-alive 600
> - `backend/telemetry.py`: added record_phase_timing + phase_timer
> - `frontend/app.py`: cuts over from /generate to /jobs
>
> Run the agent's full checklist:
> 1. Static scans (bandit, pip-audit, ruff S) on `backend/` — note any
>    new findings
> 2. Sandbox inspection on `backend/compiler.py` — confirm Worktree A did
>    NOT touch sandbox invariants
> 3. Endpoint hardening sweep on the new /jobs routes:
>    - Does each new POST have a rate limit (`@limiter.limit(...)`)?
>    - Does the JobsSubmitRequest enforce `max_length` on the prompt
>      field?
>    - Does any new route return raw tracebacks via `_safe_error()` or
>      equivalent?
> 4. Secrets sweep on the new files
> 5. Dangerous code patterns in routes_jobs.py and jobs.py (eval/exec,
>    subprocess without timeout, pickle, path traversal)
> 6. The new /jobs route accepts a job_id as a path parameter — is there
>    any path-injection or registry-key-injection risk?
> 7. The cancel route promotes QUEUED→CANCELLED eagerly and sets the
>    cancel_event. Is there any DoS concern (e.g. an attacker spamming
>    cancel on random IDs to learn which exist via 404 vs 200 timing)?
> 8. The SSE replay endpoint accepts `cursor: int = 0` from a query param
>    — any integer-overflow / negative-cursor / unbounded-loop concern?
>
> Return the structured report from the agent definition (CRITICAL /
> HIGH / MEDIUM / LOW sections). Be concrete with file:line references.
> If everything is clean, say so explicitly — empty CRITICAL and HIGH
> sections are fine.

## Re-dispatch the final zero-context reviewer (post-quota-reset)

```
Agent({
  description: "Final zero-context review A-06+A-12",
  subagent_type: "superpowers:code-reviewer",
  prompt: <see verbatim prompt below>
})
```

Verbatim prompt for the final reviewer:

> You are the final zero-context reviewer for Worktree A on branch
> mirum/backend-throughput in C:\Users\Kevin\Developments\T2CIII-
> worktrees\backend-throughput. You have no prior context.
>
> Review the most recent two commits, which together close out A-06
> (frontend cutover) and A-12 (live baseline + F-14 retag):
>
>   bfd7119 — backend+frontend: flatten SSE event format, cut frontend
>             over to /jobs (A-06)
>   5bc930d — eval_harness: live A baseline + F-14 retag from "A" to
>             "B" (A-12)
>
> Files to read in full:
> - backend/routes_jobs.py (the SSE flattening + _parse_pipeline_event)
> - backend/tests/test_jobs.py (the strengthened
>   test_get_events_replays_from_cursor_zero)
> - frontend/app.py (the new stream_pipeline that submits to /jobs and
>   reconnects with cursor)
> - backend/eval_harness/prompts.json (just the D_csv_f14_jet entry
>   around line 430)
> - output/qa-reports/2026-05-03-A-baseline-vs-postfix.md (the analysis
>   writeup)
>
> Answer the four standard Stage-3 questions plus the additional ones
> below:
>
> 1. Does the new test_get_events_replays_from_cursor_zero actually
>    exercise the flat-frame contract, or could it pass for the wrong
>    reason? Specifically: it asserts no `payload` key in the frame and
>    that {seq, step, status} are top-level. Would a route that just
>    renamed the nested key from "payload" to something else still pass?
>
> 2. Does the frontend cutover in stream_pipeline correctly handle the
>    legacy /generate response format vs the new /jobs/{id}/events flat
>    format? Does it fall back gracefully if the backend reverts?
>    (Probably not — but is that the right call for v1?)
>
> 3. The reconnect-with-cursor logic in stream_pipeline uses
>    `max_reconnects = 5` and `time.sleep(1)` between attempts. Is that
>    a sensible cap for the documented failure mode (ConnectionError on
>    a slow Planner phase)? Could a slow Planner that takes 10+ minutes
>    (within the new 600s keep-alive budget) trigger spurious reconnects
>    that exhaust the count?
>
> 4. The F-14 retag from regression_for=["A","C"] to ["B","C"] is
>    justified by the live baseline showing the failure is a CadQuery
>    chain-state error, not a timeout. Does the updated `notes` field
>    accurately describe the change AND remain useful for someone
>    reviewing the prompt later?
>
> 5. The diff document (2026-05-03-A-baseline-vs-postfix.md) claims "no
>    diff in measured pass-rate is the correct outcome" because the
>    runner bypasses every layer A touches. Is that argument sound, or
>    is there an A-tagged behaviour change the harness should have
>    measured but didn't?
>
> Output: PASS or FAIL plus 4-7 sentences. If FAIL, name the specific
> concern with file:line references.

## qa-stress-tester (optional)

Deferred — costs ~$1–3 Gemini per dispatch. The agent's job is to find
new failures by mutation; given the live baseline is already 8/8 clean,
the marginal value of stress-testing within Worktree A's scope is low.
Worth running once when ALL six worktrees have merged (Phase 3 in plan
§10), against the full corpus. Not blocking A's merge.

If you do want to dispatch it during A's merge gate:

```
Agent({
  description: "qa-stress on A_csv_dog_cupcake",
  subagent_type: "qa-stress-tester",
  prompt: "Stress-test A_csv_dog_cupcake for Worktree A. The base prompt passes today (lvm 4.7, 19.7s) but two of four expected_features are missing. Generate 5–10 mutations across scale/material/feature axes; surface any new failures into prompts.json with regression_for=['A'] (or other letter if the failure mode points elsewhere)."
})
```

## What to do next (post-compact, post-quota-reset)

1. Re-dispatch the security reviewer (verbatim prompt above)
2. Re-dispatch the final zero-context reviewer (verbatim prompt above)
3. If both PASS: Worktree A is mergeable. Rebase onto current main and
   ff-merge — same pattern used for Worktree F (see worktree-A-handoff.md).
4. If either FAILs: address findings, re-test, re-dispatch.
5. qa-stress-tester is optional / deferrable to Phase 3.

## Plan-§4 location

Plan file: `C:\Users\Kevin\.claude\plans\cached-questing-hartmanis.md`
Worktree A spec: §4
Plan-§10 execution order: F → (A,B,C,D parallel) → E → final integration
