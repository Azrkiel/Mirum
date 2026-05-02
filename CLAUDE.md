# Mirum / T2CIII — Claude Working Notes

This file is always in your context. Keep it lean.

## What this project is

Mirum is a multi-agent text-to-CAD orchestrator: `Planner → REA → [Machinist + Critic] × N → Assembler → exporters`. Backend = FastAPI + Pydantic (Python 3.11). Geometry = CadQuery / OpenCASCADE. Frontend = Streamlit. The pipeline executes LLM-generated CadQuery scripts in a sandboxed subprocess.

## Critical files

| File | Role |
|------|------|
| `backend/main.py` | FastAPI endpoints, SSE pipeline, rate limits, `_safe_error` traceback redaction |
| `backend/agents.py` | Planner, Machinist, Critic, REA, Refiner, Assembler — all LLM calls |
| `backend/compiler.py` | **Security-critical sandbox.** AST allowlist, banned builtins, `RLIMIT_AS`, 30s timeout |
| `backend/schemas.py` | Pydantic models — `AssemblyManifest`, `MatingRule`, `MateType` |
| `backend/strategies.py` | Domain-specific system prompts injected into Machinist |
| `backend/cad_utils.py` | Deterministic geometry helpers (NACA, involute gears) — IS in sandbox allowlist, do not let it import dangerous modules |
| `backend/telemetry.py`, `backend/debug.py` | Append-only JSONL logs (debug log is local-only, never returned to clients) |
| `frontend/app.py` | Streamlit UI |
| `data_pipeline/`, `training/` | Dataset prep + RLAIF training |
| `backend/eval_harness/runner.py` | Reliability measurement floor |

## Runtime facts

- **Two Gemini key tiers**: `GEMINI_API_KEY_HEAVY` (Machinist, eval, RLAIF) and `GEMINI_API_KEY_LITE` (classifier, PMI extraction, dataset annotation). Default new agent calls to lite unless quality demands heavy.
- **Writable scratch**: `/tmp/mirum/work` (env: `MIRUM_WORK_DIR`). The container's `/app` is read-only; named volumes are root-owned. Never write outside `/tmp/mirum/...`.
- **Run**: `docker compose up --build` → backend at `:8000`, frontend at `:8501`.
- **Sandbox is Linux-only enforcement**: on Windows the `resource` module is missing, so `RLIMIT_AS` doesn't apply. Production must run in Docker.

## Read discipline (token cost)

1. **Never read** `backend/__pycache__/`, `backend/telemetry_logs/`, `/tmp/mirum/telemetry/*`, `/tmp/mirum/work/*` (binaries), or eval-harness JSON runs in `output/` whole — use `Grep` with `head_limit` or `Read` with `offset`/`limit`.
2. **`output/mirum-implementation-todo.md` is ~42K tokens.** Always `Grep` for headers (`^##? `) first, then read only the tier you need.
3. **`backend/main.py` is ~900 lines.** Full read OK for cross-endpoint changes; otherwise `Grep` for the endpoint name.
4. **Grep before Read; Glob before listing.** Parallelize independent reads in one message.
5. Detailed rules: `.claude/rules/token-optimization.md`. Compaction triggers: `.claude/skills/strategic-compact/SKILL.md`.

## Security guardrails

If you touch `backend/compiler.py`, `backend/main.py` endpoints, `backend/schemas.py`, or anything that handles `GEMINI_API_KEY_*`:

- Walk the checklist at `.claude/skills/mirum-security-review/SKILL.md` for the relevant section.
- Dispatch `mirum-security-reviewer` agent (`Agent` tool with `subagent_type: "mirum-security-reviewer"`) before merging.
- Sandbox invariants you must NOT regress: `_ALLOWED_MODULES = {"cadquery", "cq", "math", "cad_utils"}`, `_BANNED_BUILTINS` (eval/exec/open/getattr/etc.), 1.5GB `RLIMIT_AS`, 30s timeout, no Gemini keys in subprocess `env`.
- Path-safety reference pattern: `backend/main.py:870-873` (resolve + startswith `_WORK_DIR`).

## Workflow defaults

- **TDD when adding features** (per superpowers). Eval harness in `backend/eval_harness/` is the regression net for pipeline behaviour.
- **Verification before completion**: run the actual change end-to-end (`docker compose up`, hit the endpoint, eyeball the GLB) before claiming done. Type checks alone aren't sufficient for pipeline changes.
- **Plan-first for non-trivial work**: use `superpowers:writing-plans` or enter plan mode. The `output/mirum-implementation-todo.md` already breaks the roadmap into Tier 1/2/3 — pick from there when "what's next" is unclear.
- **Commits**: only when explicitly asked. Don't `--no-verify`. Don't commit `.env` or anything in `output/wiki-*` without confirmation.
