# Mirum Security Review — Worktree A — 2026-05-04

Run manually (subagent dispatch was blocked by usage quota at the time of
the merge gate; the agent definition's checklist was executed directly via
Bash + Grep with the same workflow). Resumable via the verbatim agent
prompt in `output/worktree-A-mergegate-state.md` once quota resets.

Scope: the diff between `main` (47e20b2) and `mirum/backend-throughput`
HEAD (now b1f89b9 after security fixes). Worktree A's purpose:
non-blocking job submission to fix the user-CSV "Could not reach backend
... read timeout=180" failures.

## CRITICAL (sandbox / secrets)

(none)

`backend/compiler.py` was UNCHANGED by Worktree A — `git diff main..HEAD
-- backend/compiler.py` returns empty. The sandbox invariants
(`_ALLOWED_MODULES`, `_BANNED_BUILTINS`, `RLIMIT_AS`, 30s timeout, no
GEMINI_API_KEY in subprocess env) are not regressed.

Secrets sweep on the new files (`grep -nE "AIzaSy[A-Za-z0-9_-]{33}|
GEMINI_API_KEY|api_key|secret|password|token"` against `backend/jobs.py`
and `backend/routes_jobs.py`) returned zero matches. No hardcoded keys.

## HIGH (endpoint hardening, traceback leakage)

### 1. state.error exposed raw exception strings — FIXED

`backend/routes_jobs.py:94` (pre-fix) stored `str(e)[:1000]` directly on
`state.error`, then exposed it verbatim via:

- The SSE `_stream_end` terminal frame (routes_jobs.py:191)
- The `GET /jobs/{id}` polling response (routes_jobs.py:260)

A `FileNotFoundError(...'/app/secret/...')` would have leaked the host
filesystem path to any client watching the polling endpoint. CLAUDE.md
explicitly requires this to go through `_safe_error` (which redacts
`/app/`, `/opt/`, `C:/`, `C:\\`).

**Fix (commit b1f89b9):** import `main._safe_error` lazily inside
`run_pipeline_into`, wrap the exception with it before assignment to
`state.error`. New regression test `test_run_pipeline_into_sanitizes_
error_paths` raises a FileNotFoundError carrying a literal
`C:\Users\Kevin\secret\internal.py` path and asserts that path does NOT
appear in `state.error` while `<internal>` (the redaction marker) DOES.

## MEDIUM (endpoint hardening, dependency vulns)

### 1. No rate limit on the new /jobs routes — FIXED

`POST /jobs` spawns a Gemini-billing background task per request. Without
a rate limit, a client could trivially exhaust per-day quota. The legacy
`/generate` route has `@limiter.limit("5/minute")`; the new routes had
nothing.

**Fix (commit b1f89b9):** added per-route, per-IP buckets via a small
in-house dependency (`rate_limit(scope, per_minute)` returns a
`Depends`-able callable). slowapi was tried first but its decorator
breaks FastAPI's body-parameter inference on `APIRouter` routes — POST
/jobs returned 422 for every well-formed request. The 30-line bucket
implementation in `routes_jobs.py:39-86` avoids the compatibility tax.
Buckets:

| Route                       | Limit       |
|-----------------------------|-------------|
| POST /jobs                  | 5/minute    |
| POST /jobs/{id}/cancel      | 30/minute   |
| GET  /jobs/{id}             | 60/minute   |
| GET  /jobs/{id}/events      | 30/minute   |

Regression test `test_rate_limit_returns_429_when_enabled` proves the
6th POST in a 60s window returns 429.

### 2. starlette CVEs in pinned dependency — pre-existing, not in scope for A

`pip-audit -r backend/requirements.txt` reports:

  starlette 0.38.6 — CVE-2024-47874 (DoS in MultipartParser)
  starlette 0.38.6 — CVE-2025-54121

Both pre-existed Worktree A. Mirum doesn't accept multipart bodies on
any route Worktree A added; CVE-2024-47874 isn't reachable via the new
/jobs surface. CVE-2025-54121 is also unrelated to /jobs.
Recommendation: bundle a `starlette>=0.47.2` bump into a separate
follow-up — it touches the entire FastAPI surface and shouldn't ride
inside Worktree A's PR.

## LOW / informational

### 1. B108 hardcoded /tmp paths — pre-existing, intentional

bandit B108 flags `_WORK_DIR = /tmp/mirum/work` (main.py:58) and
`_LOG_DIR = /tmp/mirum/telemetry` (telemetry.py:38). Both pre-existed
Worktree A. Both have `MIRUM_WORK_DIR` / `MIRUM_TELEMETRY_DIR` env
overrides and are appropriate for a containerized service that runs
under a dedicated user (caduser) inside Docker tmpfs. Suppressing
inline (`# nosec B108`) is the conventional fix; left as-is for the
follow-up that addresses the starlette CVEs.

### 2. B110 try/except/pass

Three instances:

- `main.py:807` — pre-existing.
- `main.py:842` — pre-existing.
- `routes_jobs.py:200` — NEW from Worktree A. Wraps
  `await request.is_disconnected()` in the SSE generator. Catching
  here is intentional: certain ASGI transports raise on the
  disconnect-check itself, and the next 100ms tick will retry. A
  one-line `logger.debug(...)` would surface it without changing
  behaviour; left as-is to match the file's existing tolerance for
  transport quirks.

## Tooling output

- bandit (`-l -i` on the four touched files): 5 findings (3 Low B110, 2
  Medium B108) — all addressed above.
- ruff `--select S` (same files): identical findings, S110 + S108.
- pip-audit (-r backend/requirements.txt): 2 starlette CVEs, both
  pre-existing.

## Files inspected

- `backend/compiler.py` (verified unchanged)
- `backend/main.py` (middleware + /jobs wiring at lines 130-180, 925)
- `backend/jobs.py` (JobState, JobRegistry, TERMINAL_STATUSES)
- `backend/routes_jobs.py` (4 routes + run_pipeline_into + rate_limit)
- `backend/telemetry.py` (record_phase_timing + phase_timer)
- `backend/Dockerfile` (workers + keep-alive)
- `frontend/app.py` (stream_pipeline cutover)
- `backend/eval_harness/prompts.json` (F-14 retag only)

## Defence-in-depth principles applied

1. Sandbox is untouched — confirmed by `git diff` on compiler.py.
2. Secret leakage to logs/telemetry: zero matches on the new files.
3. Path traversal on script-execution endpoints: not applicable —
   `/jobs` accepts no server-side path fields. The job_id path param
   is used only as a `dict.get(...)` lookup key.
4. Rate-limit gaps: closed (per-route per-IP buckets).
5. Style-only findings: noted, deferred to a follow-up PR.

## Open issues for downstream consideration (not blocking A's merge)

- **starlette CVE bump** — separate PR, touches the whole FastAPI
  surface.
- **slowapi vs APIRouter compatibility** — captured in
  `routes_jobs.py:39-50` comment block. Worth filing upstream.
- **Multi-worker job affinity** — JobRegistry is per-process; the
  Dockerfile pins `--workers 1`. The constraint is comment-locked at
  `Dockerfile:39-46` and `jobs.py:1-19`. Add Redis or a sticky cookie
  before bumping workers.

## Verdict

PASS for merge once the qa-stress-tester sweep and the final zero-
context-reviewer (both currently quota-blocked, prompts captured at
`output/worktree-A-mergegate-state.md`) also return clean. The
audit-log + 19/19 unit tests provide independent coverage of the
two fixes.
