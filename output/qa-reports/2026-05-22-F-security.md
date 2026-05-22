# Security Review — Propulsion Domain F (ac55c34..db94494) — 2026-05-22

Gate (c) of the worktree merge gate. Required because the diff touches
`backend/cad_utils.py` (sandbox-allowlisted module). Run as the
`mirum-security-reviewer` checklist (general-purpose agent; repo agents aren't
registered from the session root).

## Verdict: PASS — CRITICAL / HIGH / MEDIUM all empty

- **Sandbox intact.** `git diff ac55c34..HEAD -- backend/compiler.py` is empty:
  `_ALLOWED_MODULES`, `_BANNED_BUILTINS`, `_BANNED_DUNDERS`, `RLIMIT_AS`, 30s
  timeout, and subprocess `env` are unchanged.
- **`cad_utils.py` clean.** Module scope imports only `math` + `cadquery`. No
  `os/sys/subprocess/pickle/socket/pathlib/importlib/ctypes/shutil/open/eval/
  exec/getattr/__import__`. The only function-scope import is the documented
  `from propulsion.rao_table import lookup, envelope` (cad_utils.py:1308).
- **Transitive import safe.** That import pulls `propulsion/__init__` →
  `specs`, `rao_table`, `validation`, `rea_schema` — all import only
  `__future__/typing/math/re/pydantic` + intra-package. `rao_table` is pure
  arithmetic. So the allowlisted module gains no dangerous transitive surface.
- **Secrets.** No `GEMINI_API_KEY` literal in added lines, tests, or
  `prompts.json`. No new path injects a key into the compiler subprocess env.
- **No new endpoints.** `main.py` changes are internal multi-part cert wiring
  (`_propulsion_fields(_prop_spec)`); no new `@app.post`/`@app.get`, no
  rate-limit / `_safe_error` change.
- **Validation solid.** `PropulsionSpec` + sub-specs are Pydantic with per-type
  `@model_validator`; `DesignRequirements.propulsion_spec` is typed
  `Optional[PropulsionSpec]` (not `dict[str,Any]`); LLM output is re-validated
  on `DesignRequirements(**raw)`. Numeric fields lack explicit min/max bounds
  but geometric sanity is enforced downstream (validator + cad_utils guards) —
  defense-in-depth note, not a hole.
- **No error leakage.** New REA retry path logs locally and returns
  `DesignRequirements`/`None`; no raw traceback/abs-path returned to clients.

## Note
- `strategies.py` helix-sweep re-scoping is advisory prompt text only; the sweep
  executes inside the allowlisted `cad_utils` helper, adding no sandbox surface.

## Delta re-review (db94494..HEAD) — 2026-05-22
Three later commits were re-reviewed (the original review covered ac55c34..db94494):
`dfda24b` (eval runner 3-tuple unpack + agents.py type hint), `01d414b`
(failure_analysis propulsion retry-hint), `2a8adfa` (cad_utils batched helix cut).
**CRITICAL / HIGH / MEDIUM all empty.** The only new import is a function-scope
`import gc` in `make_helical_cooling_channels` (benign, bounds peak RSS); the
batched-cut loop is pure cadquery/math; `compiler.py` remains unchanged across the
entire `ac55c34..HEAD` range; the generated-script import allowlist is untouched.
Gate (c) holds for the full branch HEAD.
