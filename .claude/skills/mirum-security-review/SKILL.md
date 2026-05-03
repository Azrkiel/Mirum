---
name: mirum-security-review
description: Use when adding or modifying FastAPI endpoints in `backend/main.py`, changing `backend/compiler.py` (CadQuery sandbox), touching Pydantic schemas in `backend/schemas.py`, handling Gemini API keys, modifying telemetry/debug logging, accepting user-supplied scripts (`/run`, `/refine`, `/pmi/annotate`), or changing rate limits. Mirum-specific OWASP-style checklist; not a generic web security skill.
---

# Mirum Security Review

A targeted checklist for Mirum's actual attack surface — adapted from [everything-claude-code](https://github.com/affaan-m/everything-claude-code) `security-review` (which is TypeScript/Next.js/Supabase-focused) for Python / FastAPI / CadQuery / OpenCASCADE / Docker.

**Use this skill alongside, not instead of, the built-in `/security-review` slash command.** That command audits the current branch's changes; this skill is the proactive checklist while *writing* the code.

## The primary attack surface

Mirum executes **LLM-generated Python code** in a subprocess. That is the dominant risk. Everything else (rate limits, key handling, etc.) is secondary. Read [backend/compiler.py](backend/compiler.py) before reviewing anything that affects script execution — that file already implements multiple defences that you must not weaken.

### What `compiler.py` already enforces (do not regress)

| Defence | Mechanism | Don't break by |
|---------|-----------|----------------|
| Module allowlist | `_ALLOWED_MODULES = {"cadquery", "cq", "math", "cad_utils"}` | Adding `os`, `sys`, `subprocess`, `pickle`, `socket`, `pathlib`, `importlib`, `ctypes`, `shutil` to the allowlist without a written justification |
| Banned builtins | `_BANNED_BUILTINS = {eval, exec, open, compile, __import__, getattr, setattr, delattr, globals, locals, vars, breakpoint, input, memoryview, type}` | Removing entries; in particular `getattr` removal would re-open attribute-based escape |
| Banned dunders | `_BANNED_DUNDERS = {__import__, __subclasses__, __globals__, __builtins__, __loader__, __spec__, __code__}` | Removing entries |
| Memory cap | `RLIMIT_AS = 1.5 GB` via `_preexec_sandbox` (POSIX only) | Removing the preexec_fn or relaxing the limit without a reason |
| Wall-clock timeout | 30s `proc.communicate(timeout=30)` | Increasing without rate-limit / queue analysis |
| Process group kill | `os.killpg(os.getpgid(proc.pid), signal.SIGKILL)` on timeout | Removing — leaves orphan OCCT processes |
| Minimal env | Only `PATH`, `PYTHONPATH`, `HOME`, `LANG` (+ Windows minimums) | Adding env vars that contain secrets (`GEMINI_API_KEY_*` MUST NOT leak into subprocess env) |
| Stdin delivery | `python -` reads script from stdin, no temp file | Switching back to `NamedTemporaryFile` (re-introduces AV / TOCTOU race) |
| Tmpfs cwd | `cwd=/tmp/mirum/work` | Changing to a path that contains source code or secrets |

### Open questions to flag (potential weak spots)

- `__class__`, `__base__`, `__bases__`, `__mro__` are **not** in `_BANNED_DUNDERS`. Combined with banned `getattr`, direct attribute access via `().__class__.__bases__[0].__subclasses__()` is still possible. If you touch `_check_ast_security`, consider adding these. Not a shipping-blocker because `__subclasses__` itself *is* banned.
- `_ALLOWED_MODULES` includes `cad_utils`. Verify `backend/cad_utils.py` does not re-export `os`, `subprocess`, `open`, `eval`, `exec`, or any function that calls them. If `cad_utils.py` ever imports something dangerous, the allowlist is bypassed.
- Windows path: `_HAS_RESOURCE = False` means **no memory limit**. Production must run on Linux/Docker. Confirm any deployment doc reiterates this.

---

## Per-category checklist

### 1. Secrets — Gemini API keys

**FAIL**
```python
genai.configure(api_key="AIzaSy...")  # hardcoded
logger.info("Using key %s", api_key)  # logged
```

**PASS** (matches existing pattern in `backend/main.py:61-68`)
```python
_GEMINI_KEY_HEAVY = os.environ.get("GEMINI_API_KEY_HEAVY")
if not _GEMINI_KEY_HEAVY:
    raise RuntimeError("GEMINI_API_KEY_HEAVY must be set...")
genai.configure(api_key=_GEMINI_KEY_HEAVY)
```

- [ ] No `GEMINI_API_KEY_HEAVY` literal in source, tests, fixtures, or `eval_harness/prompts.json` (also grep `GEMINI_API_KEY_LITE` to catch leftover references — only legitimate uses are in `data_pipeline/`)
- [ ] `.env` and `.env.local` in `.gitignore` (verify with `git check-ignore .env`)
- [ ] No keys logged via `logger.info`/`logger.debug` or written to `telemetry_logs/` / `/tmp/mirum_debug.jsonl`
- [ ] Subprocess env in `compiler.py` does NOT include `GEMINI_API_KEY_HEAVY` (confirmed in current code: env is built from `PATH`/`PYTHONPATH`/`HOME`/`LANG` only)
- [ ] No keys in Dockerfile or `docker-compose.yml` — only env-var references

### 2. Input validation (Pydantic)

All FastAPI request bodies use Pydantic. Verify:

- [ ] `max_length` set on every user-string field (current: `prompt: str = Field(..., max_length=5000)`, `script: str = Field(..., max_length=50000)`, `refinement_prompt: str = Field(..., max_length=2000)`)
- [ ] Any new `script` field has a max_length — this caps the AST-scan workload
- [ ] No `dict[str, Any]` fields that bypass validation (the `original_manifest: dict` in `RefineRequest` is re-validated via `AssemblyManifest.model_validate` — confirm any new pattern does the same)
- [ ] Enum fields use the `MateType`-style str enum, not free strings

### 3. The CadQuery sandbox (`compiler.py`)

If your change touches this file:

- [ ] You did NOT add a module to `_ALLOWED_MODULES` without writing justification in this checklist or a commit message
- [ ] You did NOT remove an entry from `_BANNED_BUILTINS` or `_BANNED_DUNDERS`
- [ ] You did NOT increase the timeout above 30s without coordinating with the rate-limit on `/run` (currently 10/min) and `/generate` (5/min)
- [ ] You did NOT relax `RLIMIT_AS` below 1.5 GB
- [ ] You did NOT propagate any new env var into the subprocess `env` dict that could carry a secret
- [ ] If you switched away from stdin delivery, you wrote a regression test for the AV/TOCTOU race documented in the file's docstring

If your change *adds new endpoints that execute scripts*: verify they call `execute_cad_script(...)` (which runs the AST gate). Bypassing it directly via `subprocess.run([sys.executable, ...])` is a regression.

### 4. Path safety

`/pmi/annotate` already has the right pattern at [backend/main.py:870-873](backend/main.py:870):

```python
step_resolved = pathlib.Path(body.step_path).resolve()
if not str(step_resolved).startswith(str(_WORK_DIR.resolve())):
    return {"status": "error", "detail": "step_path must be within the work directory"}
```

- [ ] Any new endpoint that takes a server-side path applies the same `resolve()` + `startswith(_WORK_DIR.resolve())` gate
- [ ] Any new write to disk goes inside `_WORK_DIR` (no writes to `/app`, `/etc`, `~`)
- [ ] Part-ID sanitization (commit `c680524`): verify part_ids cannot contain `/`, `\`, `..`, or null bytes before being used in filename construction

### 5. Rate limits (`slowapi`)

Current limits:

| Endpoint | Limit |
|----------|-------|
| `/clarify` | 15/min |
| `/requirements` | 15/min |
| `/plan` | 10/min |
| `/generate` | 5/min |
| `/run` | 10/min |
| `/refine` | 10/min |
| `/pmi/extract` | 30/min |
| `/pmi/annotate` | 10/min |
| `/export/parasolid` | 5/min |

- [ ] Every new endpoint has a `@limiter.limit(...)` decorator
- [ ] Endpoints that trigger Gemini calls or subprocess execution use ≤ 10/min
- [ ] Endpoints that only do regex/JSON work can use 30/min
- [ ] `/stats` and other read-only endpoints — consider adding a limit (currently unlimited)

### 6. Error handling — no traceback leakage

Current pattern uses `_safe_error()` at [backend/main.py:71-91](backend/main.py:71) which redacts:
- `File "..."` paths
- Absolute paths matching `(/app/|/opt/|[A-Za-z]:(?:/|\\{1,2}))[^\s\"']+`

- [ ] Every `yield _event(..., "error", ...)` and every error-response `detail=...` runs through `_safe_error()`
- [ ] Raw tracebacks are written ONLY to `debug_log_error` / `debug_log_event` (server-side `/tmp/mirum_debug.jsonl`), never returned to clients
- [ ] If you add a new error path, it must NOT include `traceback.format_exc()` directly in the response

### 7. SSE streaming (`/generate`)

- [ ] No secret/PII in any `_event(...)` payload
- [ ] Per-step `detail` field is sanitized via `_safe_error` if derived from an exception
- [ ] No raw Gemini response text containing system-prompt fragments leaks to the client (Machinist's intermediate scripts are intentionally exposed via the `script` field — that's fine, but confirm no system prompt is interleaved)

### 8. Telemetry / debug logs

- [ ] `backend/telemetry.py` rotation is enforced; an unbounded JSONL is a DoS vector via disk fill
- [ ] `/tmp/mirum_debug.jsonl` is **never** read by any HTTP handler — it's local-debug only
- [ ] Telemetry payloads do not include the user's full prompt unless explicitly wanted for fine-tuning, and never API keys / env vars

### 9. CORS / middleware

Current at [backend/main.py:105-115](backend/main.py:105):

```python
_ALLOWED_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:8501,http://frontend:8501").split(",")
app.add_middleware(CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
```

- [ ] Don't change `allow_credentials` to `True` unless you've also added per-origin pinning and a CSRF strategy (Mirum is currently stateless — keep it that way)
- [ ] Don't add `*` to `allow_origins` in production
- [ ] Don't add `GET` to `allow_methods` for state-changing operations

### 10. Container hardening

- [ ] `backend/Dockerfile` runs as non-root (the `caduser` already referenced in `compiler.py` comments)
- [ ] No secrets baked into image layers (`docker history` check)
- [ ] Named volumes (`/app/tmp_parts`, `/app/telemetry_logs`) — note from project memory: these are root-owned. Writes go to `/tmp/mirum/work` instead
- [ ] Base image pinned by digest, not just tag

### 11. Dependency security

Run before merging dependency updates:

```bash
python -m pip_audit
python -m bandit -r backend/ -ll
python -m ruff check --select S backend/ data_pipeline/ training/
```

- [ ] `pip-audit` reports no HIGH severity vulns (or each one has a justified ignore)
- [ ] `bandit` shows no new findings vs. baseline
- [ ] `ruff --select S` (Bandit-equivalent ruleset) clean

For automation, dispatch the `mirum-security-reviewer` agent at `.claude/agents/mirum-security-reviewer.md`.

---

## Pre-PR checklist

When closing out a feature branch:

- [ ] Run `/security-review` (built-in, branch-level audit)
- [ ] Dispatch `mirum-security-reviewer` agent on `backend/`
- [ ] Walk this checklist for sections relevant to the diff
- [ ] If `compiler.py` changed: explicit reviewer sign-off in PR description
- [ ] If a new endpoint: confirm rate limit + Pydantic max_length + `_safe_error` wrapping

## Out of scope

- Frontend XSS — Streamlit handles its own rendering; if you switch to a custom JS frontend, expand this skill
- Database / SQL injection — Mirum has no DB layer (yet)
- JWT / session — Mirum is stateless; revisit if auth is added
- Blockchain / wallet — not applicable

## Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [OWASP Python Security Cheatsheet](https://cheatsheetseries.owasp.org/cheatsheets/)
- [Bandit docs](https://bandit.readthedocs.io/)
- [pip-audit](https://pypi.org/project/pip-audit/)
- [FastAPI security docs](https://fastapi.tiangolo.com/tutorial/security/)
