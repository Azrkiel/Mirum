"""
Mirum / T2CIII — Centralised runtime configuration.

All Gemini model strings, rate-limit constants, and tunable pipeline
knobs live here.  Import from this module instead of defining MODULE-level
MODEL constants in individual files.

Model strings were last validated 2026-05-13.  If a string starts returning
404 / PERMISSION_DENIED, check:
  1. The Gemini API model catalogue for your project tier.
  2. output/baseline-followup-todo.md for the documented 404-fix history.
"""

# ---------------------------------------------------------------------------
# Gemini model identifiers
# ---------------------------------------------------------------------------

# Pro tier — Planner, REA (deepest reasoning; slower, higher cost)
GEMINI_MODEL_PRO: str = "gemini-3.1-pro-preview"

# Heavy tier — Machinist, Critic, Refiner, eval (fast, capable flash)
GEMINI_MODEL_HEAVY: str = "gemini-3.5-flash"

# Lite tier — domain classifier, PMI LLM extraction (fast, cheap calls).
# Was gemini-3.1-flash-lite-preview, retired by Google (404) on/before 2026-05-26;
# migrated to gemini-3.5-flash (same as Heavy) — the cheapest reachable model.
GEMINI_MODEL_LITE: str = "gemini-3.5-flash"

# Checked beam preparation has its own qualification/configuration boundary.
# 2026-11-17: owner switched from gemini-3.6-flash (HTTP 503 "high demand" off the laptop,
# 45 s stalls on it) to gemini-4-hydrogen. Explicit, documented change; needs live requalification.
GEMINI_MODEL_ENGINEERING_PREPARATION: str = "gemini-4-hydrogen"
# Separate from legacy clarifier limits; live requalification is required.
GEMINI_ENGINEERING_MAX_OUTPUT_TOKENS: int = 4096
# google-genai ThinkingLevel for checked preparation. Qualification calls stalled to the 45 s
# deadline on the old SDK, which cannot cap thinking. "low" is the lowest level every Gemini 3+
# family accepts; "minimal" is not accepted by every model, so it is not used until requalified.
GEMINI_ENGINEERING_THINKING_LEVEL: str = "low"

# ---------------------------------------------------------------------------
# Pipeline tuning knobs
# ---------------------------------------------------------------------------

import os as _os_cfg


def _env_int(name: str, default: int) -> int:
    """Read an int env var; fall back to the default on missing/garbage."""
    try:
        return int(_os_cfg.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str) -> bool:
    """True only for an explicit "1"; anything else (or unset) is off."""
    return _os_cfg.environ.get(name, "").strip() == "1"


def _env_float(name: str, default: float) -> float:
    try:
        return float(_os_cfg.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# Process-wide cap on provider calls per rolling 24 h for POST /dragsters/interpret
# (in addition to the per-IP rate limit). 0 disables the endpoint's provider path.
DRAGSTER_INTERPRET_DAILY_CAP: int = max(0, _env_int("MIRUM_DRAGSTER_INTERPRET_DAILY_CAP", 200))

def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env var; only "1"/"true" (any case) count as on."""
    raw = _os_cfg.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true")


# Checked-beam AI draft preparation (POST /engineering/v1/drafts with mode "ai").
# OFF by default: not live-qualified. When off, no provider is constructed and
# no API key is read for this feature. Typed/form mode is unaffected.
ENGINEERING_AI_DRAFTS: bool = _env_flag("MIRUM_ENGINEERING_AI_DRAFTS", False)

# --- Checked-beam abuse limits (public, anonymous API). Every value is a
# deliberate quota; none changes an engineering tolerance or acceptance rule.
# AI provider spend: calls (including repair calls) per rolling 24 h, process-wide.
# 0 means no provider calls at all.
ENGINEERING_AI_DAILY_CALLS: int = max(0, _env_int("MIRUM_ENGINEERING_AI_DAILY_CALLS", 200))
# Provider calls allowed in flight at once (process-wide).
ENGINEERING_AI_CONCURRENCY: int = max(1, _env_int("MIRUM_ENGINEERING_AI_CONCURRENCY", 2))
# Local diagnostic only: build gRPC roots from the OS trust store instead of
# gRPC's bundled roots (needed behind TLS-intercepting antivirus). Default off.
ENGINEERING_TLS_OS_TRUST: bool = _env_flag("MIRUM_ENGINEERING_TLS_OS_TRUST", False)
# Native-slot fairness: jobs one client may submit per rolling hour, and how long
# a refused submit is remembered when deciding whether to re-admit another client.
ENGINEERING_JOBS_PER_CLIENT_HOUR: int = max(1, _env_int("MIRUM_ENGINEERING_JOBS_PER_CLIENT_HOUR", 6))
ENGINEERING_REFUSAL_MEMORY_SECONDS: int = max(1, _env_int("MIRUM_ENGINEERING_REFUSAL_MEMORY_SECONDS", 120))
# Unfinalized drafts: expiry, global and per-client caps. Audit and finalized-brief
# records have their own caps, accounted apart from job artifact storage.
ENGINEERING_DRAFT_TTL_SECONDS: int = max(60, _env_int("MIRUM_ENGINEERING_DRAFT_TTL_SECONDS", 4 * 3600))
ENGINEERING_MAX_DRAFTS: int = max(1, _env_int("MIRUM_ENGINEERING_MAX_DRAFTS", 500))
ENGINEERING_MAX_DRAFT_BYTES: int = max(1, _env_int("MIRUM_ENGINEERING_MAX_DRAFT_BYTES", 16 * 1024**2))
ENGINEERING_MAX_DRAFTS_PER_CLIENT: int = max(1, _env_int("MIRUM_ENGINEERING_MAX_DRAFTS_PER_CLIENT", 20))
ENGINEERING_MAX_AUDITS: int = max(1, _env_int("MIRUM_ENGINEERING_MAX_AUDITS", 1000))
ENGINEERING_MAX_AUDIT_BYTES: int = max(1, _env_int("MIRUM_ENGINEERING_MAX_AUDIT_BYTES", 32 * 1024**2))
ENGINEERING_MAX_BRIEFS: int = max(1, _env_int("MIRUM_ENGINEERING_MAX_BRIEFS", 2000))
ENGINEERING_MAX_BRIEFS_PER_CLIENT: int = max(1, _env_int("MIRUM_ENGINEERING_MAX_BRIEFS_PER_CLIENT", 100))
# Largest request body accepted on /engineering/v1 before parsing (JSON overhead
# included; the brief itself is bounded to 16 KiB and prompts to 8000 characters).
ENGINEERING_MAX_BODY_BYTES: int = max(1024, _env_int("MIRUM_ENGINEERING_MAX_BODY_BYTES", 64 * 1024))

# Maximum Machinist retries per part before the Critic Loop gives up
CRITIC_MAX_RETRIES: int = _env_int("MIRUM_CRITIC_MAX_RETRIES", 3)

# Timeout (seconds) for a single CadQuery subprocess execution.
# Clamped to [5, 60]: the sandbox contract caps script runtime so a
# misconfigured env var can never silently disable the timeout invariant.
CADQUERY_TIMEOUT_S: int = max(5, min(_env_int("MIRUM_CADQUERY_TIMEOUT_S", 30), 60))

# Dimension-vs-spec gate: relative tolerance on REA key_dimensions vs the
# measured bounding box, plus an absolute floor so tiny dims don't false-fail.
DIM_TOLERANCE_REL: float = _env_float("MIRUM_DIM_TOL", 0.05)
DIM_TOLERANCE_ABS_MM: float = 0.1

# RLAIF hybrid reward weights (must sum to 1.0)
RLAIF_EXEC_WEIGHT: float = 0.7
RLAIF_PREF_WEIGHT: float = 0.3

# ---------------------------------------------------------------------------
# XFOIL integration (Phase 2)
# ---------------------------------------------------------------------------
#
# WINDOWS INSTALL (one-time, run from PowerShell in the project root):
#   .\bin\install_xfoil_windows.ps1
#   This downloads XFOIL6.99.zip from https://web.mit.edu/drela/Public/web/xfoil/
#   and extracts xfoil.exe into T2CIII/bin/xfoil.exe.
#
# After installing, set the env var so the pipeline finds the binary:
#   $env:MIRUM_XFOIL_BIN = "C:\Users\Kevin\Developments\T2CIII\bin\xfoil.exe"
#   (add to your conda activate script or .env file for persistence)
#
# LINUX (Docker / WSL):
#   apt-get install xfoil     # adds /usr/bin/xfoil — no env var needed
#
# macOS:
#   brew install xfoil        # adds to /usr/local/bin/xfoil — no env var needed
#
# Verify: python -c "import subprocess; subprocess.run(['xfoil'], input='QUIT\n', text=True, timeout=5)"

import os as _os

_WINDOWS_DEFAULT = r"C:\Users\Kevin\Developments\T2CIII\bin\xfoil.exe"

XFOIL_BIN: str = _os.environ.get(
    "MIRUM_XFOIL_BIN",
    # On Windows, default to the expected post-install location.
    # On Linux/macOS, fall back to "xfoil" on PATH.
    _WINDOWS_DEFAULT if _os.name == "nt" else "xfoil",
)

# Honour X-Mirum-Client-IP as the rate-limit key (see backend/client_key.py).
# Default OFF. Turn on ONLY when the backend is unreachable from the public
# internet (private Fly backend behind the public frontend): any direct caller
# could otherwise forge the header.
TRUSTED_CLIENT_HEADER: bool = _env_flag("MIRUM_TRUSTED_CLIENT_HEADER")
