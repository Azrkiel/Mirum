# Worktree A — Finish List

Snapshot 2026-05-04. The branch is **PASS-verified** on both the
mirum-security-reviewer (round 2) and the final zero-context-reviewer.
Outstanding work is bounded: commit one pending fix, optionally add one
regression test, run a final test sweep, then merge.

Branch: `mirum/backend-throughput` at `d7788ab`
Working tree: 1 uncommitted edit (M1 fix in `backend/routes_jobs.py`)
Plan §4 reference: `C:\Users\Kevin\.claude\plans\cached-questing-hartmanis.md`
State files:
- `output/worktree-A-handoff.md` — implementation snapshot
- `output/worktree-A-mergegate-state.md` — verbatim re-dispatch prompts
- `output/qa-reports/2026-05-03-A-baseline.md` — live runner data
- `output/qa-reports/2026-05-04-A-security-review.md` — manual review
- `output/qa-reports/2026-05-03-A-baseline-vs-postfix.md` — slice diff

---

## Phase 1 — commit the pending M1 fix (5 min)

**Status**: edit applied to `backend/routes_jobs.py` but NOT staged.

**Task 1.1 — verify the working tree state**

```bash
cd C:/Users/Kevin/Developments/T2CIII-worktrees/backend-throughput
git status
# Expect: backend/routes_jobs.py modified (only)
git diff backend/routes_jobs.py | head -40
# Expect: `_dep` changed from `def` to `async def` + a paragraph of
# why-comment naming the TOCTOU race that motivated the change
```

If the diff shows MORE than the async/comment change → don't commit yet,
inspect the extras first.

**Task 1.2 — confirm the test suite still passes**

```bash
python backend/tests/test_prompts_schema.py    # expect 9/9
python backend/tests/test_runner_filters.py    # expect 8/8
python backend/tests/test_md_report.py         # expect 11/11
python backend/tests/test_jobs.py              # expect 19/19
python backend/tests/test_telemetry_phase.py   # expect 6/6
```

If `test_jobs.py` regresses (any non-19/19 result):
- Read the failure message
- The async-dep change should NOT break tests because (a) the rate
  limiter is disabled in `_client()` and (b) the `_RATE_BUCKETS` is
  cleared per-test. If it does break, the most likely cause is FastAPI
  rejecting the async dep with the pattern used (Depends(rate_limit("...", 5)))
  — try reverting just the `async def` and committing the comment-only
  variant; M1 stays open as a follow-up.

**Task 1.3 — commit**

```bash
git add backend/routes_jobs.py
git commit -m "$(cat <<'EOF'
backend/routes_jobs: rate_limit dep async to close TOCTOU race (M1)

Security re-review on commit b1f89b9 flagged the in-house rate_limit
dependency as a sync function. FastAPI runs sync deps in
anyio.to_thread.run_sync, so multiple concurrent requests on the same
(scope, ip) key execute the bucket-check-and-append in different
threads. The deque ops are individually atomic in CPython but the
`len(bucket) >= per_minute` check followed by `bucket.append(now)` is
not — two threads can both observe N-1 and both append, soft-capping
at N+1 instead of N. For POST /jobs (5/min, Gemini-billing), an
attacker coordinating bursts could land ~10/min instead of ~5/min.

Converting `_dep` to `async def` makes FastAPI run it cooperatively on
the event loop. The body has no `await`, so the check + mutate sequence
is effectively atomic on a single event loop. CRITICAL/HIGH stay empty;
the two known follow-up gaps (M2 unbounded `_RATE_BUCKETS` growth, M3
proxy-header awareness) plus the pre-existing L1 (`_safe_error` Linux
path coverage) are documented in the function docstring with pointers
to the security report.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — OPTIONAL: TOCTOU regression test (15 min)

**Decision**: skip if you want to merge today; add if you want belt-and-
braces against future refactors that revert M1 to sync.

**Task 2.1 — write the test**

In `backend/tests/test_jobs.py`, after `test_rate_limit_returns_429_when_enabled`:

```python
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
```

Add to the `_run_all` list right after `test_rate_limit_returns_429_when_enabled`.

**Task 2.2 — run + verify**

```bash
python backend/tests/test_jobs.py     # expect 20/20
```

If the new test fails saying "got [200]*N + [429]*M with N>5":
- M1 may have been reverted between when you read this list and now
- Re-check that `rate_limit._dep` is `async def`
- If it's still `async def` and the test fails, FastAPI may have
  changed its async-dep concurrency model — investigate before
  blaming M1

**Task 2.3 — commit**

```bash
git add backend/tests/test_jobs.py
git commit -m "$(cat <<'EOF'
backend/tests: TOCTOU regression test for rate_limit dep (locks M1)

10 concurrent POST /jobs against a 5/minute bucket must split exactly
5 × 200 + 5 × 429. A future refactor that drops `async def` from
rate_limit._dep would let FastAPI run it in anyio.to_thread.run_sync
again, the read-then-mutate sequence on the bucket would race, and
this test would surface 6+ 200s — failing loudly and pointing at M1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — final verification sweep (5 min)

**Task 3.1 — re-run every test file from scratch**

```bash
python backend/tests/test_prompts_schema.py
python backend/tests/test_runner_filters.py
python backend/tests/test_md_report.py
python backend/tests/test_jobs.py
python backend/tests/test_telemetry_phase.py
```

Expected total: 53/53 (or 54/54 if you did Phase 2).

**Task 3.2 — eyeball the diff stat**

```bash
git log --oneline mirum/backend-throughput ^main
git diff --stat main..HEAD
```

Expected: ~14 commits, ~700-800 line diff across:
- backend/jobs.py (NEW)
- backend/routes_jobs.py (NEW)
- backend/main.py (modified — middleware + /jobs wiring)
- backend/Dockerfile (modified — workers + keep-alive)
- backend/telemetry.py (modified — phase_timer)
- backend/tests/test_jobs.py (NEW)
- backend/tests/test_telemetry_phase.py (NEW)
- frontend/app.py (modified — /jobs cutover)
- backend/eval_harness/prompts.json (F-14 retag)
- output/qa-reports/* (3 new reports)

If anything OUTSIDE this list is staged for the merge, investigate before
proceeding.

---

## Phase 4 — OPTIONAL: qa-stress-tester dispatch (10 min, ~$1–3 Gemini)

**Decision**: defer to Phase 3 (post-Worktree-E final integration). The
live baseline is already 8/8 clean on Domain A — the marginal value of
mutation-stressing within Worktree A's scope is low. Worth running once
when ALL six worktrees have merged.

**If you want to run it anyway** before A merges (to surface any A-
adjacent failures we missed):

```
Agent({
  description: "qa-stress on A_csv_dog_cupcake",
  subagent_type: "qa-stress-tester",
  prompt: "Stress-test A_csv_dog_cupcake for Worktree A. Base prompt passes today (lvm 4.7, 19.7s, 2 of 4 expected_features missing). Generate 5–10 mutations across scale/material/feature axes. Surface any new failures into prompts.json with the right regression_for tag based on failure mode (timeout → A; CadQuery error → B; scale → C; topology → D; force-fit → E)."
})
```

### What to do if qa-stress finds NEW failures

The agent appends new entries to `prompts.json` with `id`s of the form
`<DOMAIN>_qa_<short-slug>` and tags them with the worktree letter whose
fix would close them.

**Decision tree** based on the agent's regression_for tag:

| Tag added | Action |
|-----------|--------|
| `regression_for: ["A"]` | A's fix is supposed to cover this. **BLOCK MERGE**, fix in A, redo Phase 3, re-dispatch qa-stress to confirm. |
| `regression_for: ["B"]` | CadQuery validation territory. Note in commit message; merge A; ticket B's worktree to address. |
| `regression_for: ["C"]` | Scale/decomposition territory. Same — merge A; ticket C. |
| `regression_for: ["D"]` | Topology / visual-verifier territory. Merge A; ticket D. |
| `regression_for: ["E"]` | Domain-classification escape-hatch territory. Merge A; ticket E. |
| Multi-tag with "A" | If A is one of the tags, treat as A-blocking. |

**If the agent's report flags an A-tagged failure** (the only blocking case):

1. Read `output/qa-reports/<date>-<base-id>-stress.md` and the new
   prompts.json entry to understand the failure mode.
2. Reproduce against the live runner:
   ```bash
   python -m eval_harness.runner --filter <new_id> --strict-lvm \
     --report-md /tmp/qa-stress-repro.md
   ```
3. Categorize:
   - **Pipeline timeout / connection error** → A's job-queue fix should
     have closed this. The /jobs route is in place; check whether the
     test prompt actually goes through /jobs (the runner bypasses /jobs,
     so timeout failures in the runner = real pipeline slowness, not
     SSE-layer). May need A-08 (Gemini prompt cache) after all.
   - **CadQuery validation error** → wrong tag; should be B. Open a PR
     against the agent's tag and re-tag.
   - **Visual mismatch** → wrong tag; should be D.
4. If the failure is genuinely A's responsibility, write a failing test
   in test_jobs.py that reproduces it, fix in routes_jobs.py / jobs.py,
   re-run the full test sweep (Phase 3), re-dispatch security re-review
   (verbatim prompt in `output/worktree-A-mergegate-state.md`), then
   re-dispatch qa-stress to confirm.

### What to do if qa-stress hits Gemini quota mid-run

The agent will return an incomplete report with an error like
`RESOURCE_EXHAUSTED`. State preserved in
`/tmp/mirum/qa-stress/<base-id>-mutations.json` (the agent's scratch
file). Wait for quota reset, re-dispatch with the same prompt — the
agent re-checks for already-appended entries and skips them.

---

## Phase 5 — merge to main (10 min)

**Task 5.1 — verify primary tree state**

```bash
cd C:/Users/Kevin/Developments/T2CIII
git status
# Should show: "On branch main" + (possibly some untracked files in
# output/ and .claude/, those are fine)
git worktree list
# Should show: T2CIII at <main-sha>, 2 worktrees
```

**Task 5.2 — rebase A onto main**

(Same pattern as the F-merge. If main has new commits since `47e20b2`,
A needs to be rebased on top.)

```bash
cd C:/Users/Kevin/Developments/T2CIII-worktrees/backend-throughput
git rebase main
```

If rebase has conflicts:
- The most likely conflict is in `backend/main.py` (multiple worktrees
  modifying the bottom of the file)
- Resolve preserving BOTH the new wiring you added AND any unrelated
  changes on main
- `git rebase --continue` after resolving

**Task 5.3 — fast-forward main**

```bash
cd C:/Users/Kevin/Developments/T2CIII
git merge --ff-only mirum/backend-throughput
git log --oneline -20    # confirm linear history with A's commits on top
```

**Task 5.4 — fast-forward sibling worktrees** (if any new ones exist)

```bash
git worktree list
# For each worktree on a Phase-1 branch (B/C/D), cd in and:
cd <worktree-path>
git merge --ff-only main
```

(qa-harness is already merged. backend-throughput is what you just
merged. Other worktrees come later.)

### What to do if `git rebase main` fails

If a conflict can't be resolved cleanly:
1. `git rebase --abort`
2. Inspect what's on main that conflicts: `git log main ^mirum/backend-throughput --oneline`
3. If main has only docs / unrelated changes, the conflict is mechanical
   — re-rebase, take both sides
4. If main has a feature change that overlaps A's surface (e.g. someone
   added a /jobs-like endpoint independently), STOP — coordinate with
   the human before resolving

### What to do if a sibling worktree has uncommitted work

Don't ff a sibling that's mid-flight. Note it in `worktree-A-handoff.md`,
move on. Sibling will catch up itself when its owner runs `git merge --ff-only main`.

---

## Phase 6 — follow-up backlog (capture before forgetting)

Append to `output/baseline-followup-todo.md` (or wherever the team's
backlog lives):

```markdown
## Worktree A follow-up — security report 2026-05-04

- [ ] **starlette CVE bump** — pin starlette >= 0.47.2 in
  backend/requirements.txt. CVE-2024-47874 + CVE-2025-54121.
  Run pip-audit after to confirm clean. Touches the whole FastAPI
  surface; lands in its own PR before the next worktree merges.

- [ ] **M2: prune empty rate-limit buckets** —
  `backend/routes_jobs.py:_RATE_BUCKETS` grows unbounded across unique
  IPs. Add a periodic prune (e.g. once every 1000 requests, drop keys
  whose deque is empty) or LRU cap. Memory-pressure DoS if Mirum is
  ever exposed publicly.

- [ ] **M3: proxy-aware rate-limit key** —
  `backend/routes_jobs.py:rate_limit._dep` uses `request.client.host`
  (TCP peer). When a reverse proxy fronts the deployment, switch to
  `X-Forwarded-For` / `X-Real-IP` reads gated by a trusted-proxy
  allowlist. Document at deploy time.

- [ ] **L1: extend `_safe_error` to redact Linux paths** —
  `backend/main.py:86-90` regex covers /app/, /opt/, C:/, C:\\\\
  but misses /home/, /root/, /var/, /etc/, /usr/, /tmp/. Worktree A
  added new client-visible error surfaces (state.error in SSE +
  polling) that inherit this gap. One-line regex extension; covered
  by adding a test case to test_jobs.py with each new prefix.

- [ ] **B110 in routes_jobs.py:200** — wrap `await
  request.is_disconnected()` exception handler with a `logger.debug`
  so transport-quirk failures are observable.

- [ ] **Multi-worker job affinity** — JobRegistry is per-process.
  Dockerfile pins --workers 1. Before bumping workers, add Redis-
  backed JobRegistry or sticky session routing.
```

---

## Failure-recovery quick reference

| If this fails… | Do this. |
|----------------|----------|
| Phase 1.2 tests | Inspect the diff; revert just the `async def` change, leave the comment, ship without M1 (M1 becomes a follow-up). |
| Phase 2.2 TOCTOU test | If `async def` is in place and the test still fails, this is a FastAPI version regression — open an upstream issue, skip Phase 2 for now. |
| Phase 3 schema/filter/reporter | These haven't changed in this round; if they fail, run `git diff main..HEAD -- backend/eval_harness/ backend/tests/test_prompts_schema.py` — something unexpected got staged. |
| Phase 4 qa-stress with A-tagged failure | Block merge, fix, redo Phase 3, redo Phase 4. |
| Phase 4 qa-stress with B/C/D/E-tagged failure | Don't block merge; ticket the responsible worktree. |
| Phase 5 rebase conflict | See Phase 5.2's "What to do if rebase fails". |
| Phase 5.3 ff-only | If `git merge --ff-only` errors with "Not possible to fast-forward", main has new commits the rebase didn't pick up. Repeat Phase 5.2. |

---

## What `done` looks like

When everything above is green:

- Local main has Worktree A merged, linear history.
- 53/53 (or 54/54) tests passing.
- 3 markdown reports in `output/qa-reports/` documenting the slice
  baseline, the security review, and the post-fix diff.
- `output/baseline-followup-todo.md` has the M2/M3/L1/starlette-CVE
  items captured.
- The Streamlit frontend, on `docker compose up`, talks to the new
  /jobs route and survives a mid-stream disconnect-and-reconnect via
  cursor.
- The next worktree (B, C, or D — whichever Kevin picks for Phase 1
  parallel work) can branch off this main and inherit /jobs, the
  rate limiter, and the harness without any rebase pain.

After all phases, mark task #27 complete in TaskList.
