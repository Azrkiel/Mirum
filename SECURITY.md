# Security Policy

## Reporting a vulnerability

**Preferred channel: [GitHub Security Advisories](https://github.com/Azrkiel/T2CIII/security/advisories/new).**

GHSA gives us a private workspace to triage, develop a fix, and coordinate disclosure. It also lets us issue a CVE if warranted. Use this channel for any finding that could enable RCE, secret exfiltration, sandbox escape, or denial-of-service against a hosted Mirum instance.

If GHSA is unavailable to you, email [jiang.kevin28@gmail.com](mailto:jiang.kevin28@gmail.com). Encrypt with PGP if the finding is exploitable in the wild — public key in `SECURITY-PGP.asc` (TODO).

Please **do not** open public GitHub issues or PRs for security findings before we've had a chance to fix them.

## Scope

The following components are in scope for security reports:

| Component | Path | Notes |
|-----------|------|-------|
| FastAPI backend | `backend/main.py`, `backend/agents.py` | Endpoint auth, rate limits, input validation, traceback leakage |
| **CadQuery sandbox** | `backend/compiler.py` | Primary attack surface — sandbox escape, AST-allowlist bypass, resource exhaustion |
| Deterministic geometry | `backend/cad_utils.py` | Re-exported imports that could bypass the allowlist |
| Pydantic schemas | `backend/schemas.py` | Validation gaps |
| Gemini key handling | `backend/main.py`, env, Dockerfile | Key leakage to logs/telemetry/subprocess env |
| Telemetry / debug | `backend/telemetry.py`, `backend/debug.py` | Sensitive-data exposure, disk-fill DoS |
| Container | `backend/Dockerfile`, `docker-compose.yml` | Privilege escalation, exposed ports, secrets baked into layers |
| Data pipeline | `data_pipeline/` | Code that processes external datasets |

## Out of scope

- Streamlit frontend rendering (Streamlit handles its own DOM; we have no custom JS)
- Findings that require an attacker with shell access to the container
- Issues in Gemini, CadQuery, OpenCASCADE, or other upstream dependencies — please report those upstream, then optionally CC us
- Generated `.glb` / `.step` files (output, not attack surface)
- Theoretical timing-side-channels in the LLM responses
- Missing security headers on `localhost`-only deployments

## Known-risk acknowledgments

Mirum's design intentionally executes Python source produced by an LLM. The mitigations below are what makes that acceptable:

- **AST allowlist** in `compiler.py` restricts imports to `{cadquery, cq, math, cad_utils}` and bans builtins like `eval`, `exec`, `open`, `compile`, `getattr`, `__import__`, plus dangerous dunders (`__subclasses__`, `__globals__`, etc.)
- **Subprocess isolation** with stdin script delivery (no temp file)
- **Resource limits**: 1.5 GB virtual memory cap (POSIX `RLIMIT_AS`), 30-second wall-clock timeout
- **Process-group kill** on timeout to prevent orphan OCCT processes
- **Minimal subprocess env** — Gemini API keys are explicitly *not* propagated
- **Tmpfs cwd** (`/tmp/mirum/work`) — sandboxed scripts cannot write into source or named volumes
- **Rate limits** on every endpoint via `slowapi`
- **Path-traversal gate** on endpoints that take server-side paths (resolve + startswith check)
- **Sanitized error responses** via `_safe_error()` — paths and source filenames are redacted before being returned to clients

We are aware of and accept the following residual risk:

- On Windows the `resource` module is unavailable, so `RLIMIT_AS` is not enforced. **Production deployments must run on Linux/Docker.** Windows is a developer-only environment.

## Disclosure timeline

We aim for the following turnaround:

| Phase | Target |
|-------|--------|
| Acknowledge receipt | 3 business days |
| Initial triage / severity assessment | 7 days |
| Patch released for CRITICAL/HIGH | 30 days from triage |
| Patch released for MEDIUM | 90 days from triage |
| Public disclosure / CVE filing | After patch + 14 days, coordinated with reporter |

If a finding is being actively exploited, we will move faster and coordinate disclosure timing with the reporter.

## Hall of fame

Researchers who have responsibly disclosed verified vulnerabilities will be credited here (with permission):

_None yet — be the first._

## Supported versions

Mirum is pre-1.0 and ships from `main`. Only the latest commit on `main` receives security updates. We will tag a `v0.x` release line once API stability is reached and document supported versions here.
