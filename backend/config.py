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

# ---------------------------------------------------------------------------
# Pipeline tuning knobs
# ---------------------------------------------------------------------------

import os as _os_cfg

# Maximum Machinist retries per part before the Critic Loop gives up
CRITIC_MAX_RETRIES: int = int(_os_cfg.environ.get("MIRUM_CRITIC_MAX_RETRIES", "3"))

# Timeout (seconds) for a single CadQuery subprocess execution
CADQUERY_TIMEOUT_S: int = int(_os_cfg.environ.get("MIRUM_CADQUERY_TIMEOUT_S", "30"))

# Dimension-vs-spec gate: relative tolerance on REA key_dimensions vs the
# measured bounding box, plus an absolute floor so tiny dims don't false-fail.
DIM_TOLERANCE_REL: float = float(_os_cfg.environ.get("MIRUM_DIM_TOL", "0.05"))
DIM_TOLERANCE_ABS_MM: float = 0.1

# LVM code-judge in the production hot path. Off by default: the score is
# analytics-only (nothing reads it at runtime) and costs one Gemini call per
# successful part. The eval harness calls compute_lvm_score directly and is
# unaffected by this flag.
LVM_SCORE_IN_PROD: bool = _os_cfg.environ.get("MIRUM_LVM_IN_PROD", "0") in ("1", "true", "yes")

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
