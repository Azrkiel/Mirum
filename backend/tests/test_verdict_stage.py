"""
backend/tests/test_verdict_stage.py
====================================

Tests for ``main._run_verdict_stage()`` (Phase 2 of the design-closure
plan) and the ``main._targets_imply_stages()`` auto-enable helper.

The verdict stage is pure-number (no subprocess, no STEP file, no LLM
call), so these tests run on real DesignRequirements / verdict code
without any mocking.

Run directly:
    cd T2CIII/backend
    python tests/test_verdict_stage.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# main.py asserts GEMINI_API_KEY_HEAVY at import time.  Provide a dummy so
# the import succeeds in CI / bare-stdlib runs.  No LLM call is made by the
# verdict stage path under test.
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-dummy-key-not-used")

# Path setup: backend/ on sys.path; repo root on sys.path so `analysis.*` resolves.
_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_ROOT))


POLAR = {"Cl_max": 1.7, "L_D": [15.0], "stall_alpha": 13.0,
         "converged_count": 10}


def _req_with_targets(**targets):
    from schemas import DesignRequirements, DesignTargets
    return DesignRequirements(
        primary_function="test wing", inferred_domain="D",
        design_targets=DesignTargets(**targets))


# ---------------------------------------------------------------------------
# 2.1 _run_verdict_stage
# ---------------------------------------------------------------------------

def test_verdict_stage_returns_verdict_dict():
    import main
    req = _req_with_targets(cl_max_min=1.5)
    out, msg = main._run_verdict_stage(req, POLAR, None)
    assert out is not None and out["overall"] == "pass"
    assert "1/1 criteria" in msg


def test_verdict_stage_no_targets_returns_none():
    from schemas import DesignRequirements
    import main
    req = DesignRequirements(primary_function="test wing", inferred_domain="D")
    out, msg = main._run_verdict_stage(req, POLAR, None)
    assert out is None
    assert msg  # human-readable skip reason


def test_verdict_stage_no_requirements_returns_none():
    import main
    out, msg = main._run_verdict_stage(None, POLAR, None)
    assert out is None
    assert msg


# ---------------------------------------------------------------------------
# 2.3 _targets_imply_stages auto-enable helper
# ---------------------------------------------------------------------------

def test_targets_imply_aero_stage():
    import main
    req = _req_with_targets(cl_max_min=1.5)
    run_aero, run_struct = main._targets_imply_stages(req, False, False)
    assert run_aero is True
    assert run_struct is False


def test_targets_imply_struct_stage():
    import main
    req = _req_with_targets(ms_min=0.5)
    run_aero, run_struct = main._targets_imply_stages(req, False, False)
    assert run_aero is False
    assert run_struct is True


def test_no_targets_leaves_flags_unchanged():
    from schemas import DesignRequirements
    import main
    req = DesignRequirements(primary_function="a box", inferred_domain="A")
    assert main._targets_imply_stages(req, False, False) == (False, False)
    assert main._targets_imply_stages(req, True, False) == (True, False)
    assert main._targets_imply_stages(None, False, True) == (False, True)


# ---------------------------------------------------------------------------
# stdlib runner
# ---------------------------------------------------------------------------

def _run_all() -> int:
    funcs = [
        test_verdict_stage_returns_verdict_dict,
        test_verdict_stage_no_targets_returns_none,
        test_verdict_stage_no_requirements_returns_none,
        test_targets_imply_aero_stage,
        test_targets_imply_struct_stage,
        test_no_targets_leaves_flags_unchanged,
    ]
    failures = 0
    for fn in funcs:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {fn.__name__}")
    print(f"\n{len(funcs) - failures}/{len(funcs)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
