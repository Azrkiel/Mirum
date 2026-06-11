"""
backend/tests/test_design_closure.py
======================================

Loop-behavior tests for ``closure.run_design_closure``: bounded aero
refinement, zero-iteration pass, and the no-LLM analytic struct fix.

Targets closure.py directly with stubbed callbacks — no ``import main``
(FastAPI/limiter side effects), no LLM, no XFOIL.  Stdlib-runnable.

Run directly:
    cd T2CIII/backend
    python tests/test_design_closure.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# agents.py asserts GEMINI_API_KEY_HEAVY at import time.  Provide a dummy so
# transitive imports succeed in CI / bare-stdlib runs.  No LLM call is made.
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-dummy-key-not-used")

# Path setup: backend/ on sys.path; repo root on sys.path so `analysis.*` resolves.
_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_ROOT))

from schemas import DesignRequirements, DesignTargets, WingBoxSpec  # noqa: E402


def _req(**targets):
    return DesignRequirements(primary_function="w", inferred_domain="D",
                              design_targets=DesignTargets(**targets))


def test_loop_stops_after_max_iterations():
    from closure import run_design_closure
    calls = {"refine": 0}

    async def fake_refine_aero(prompt):
        calls["refine"] += 1
        return {"Cl_max": 1.2, "L_D": [9.0], "stall_alpha": 11.0,
                "converged_count": 8}                       # still failing

    async def fake_rerun_struct(requirements):
        raise AssertionError("no struct targets — must not be called")

    polar = {"Cl_max": 1.1, "L_D": [8.0], "stall_alpha": 10.0,
             "converged_count": 8}
    verdict, changed = asyncio.run(run_design_closure(
        _req(cl_max_min=1.5), polar, None,
        refine_aero=fake_refine_aero, rerun_struct=fake_rerun_struct,
        max_iters=2))
    assert calls["refine"] == 2
    assert verdict["overall"] == "fail" and verdict["iterations_used"] == 2


def test_loop_zero_iterations_when_pass():
    from closure import run_design_closure

    async def boom(_):
        raise AssertionError("must not refine")

    polar = {"Cl_max": 1.7, "L_D": [20.0], "stall_alpha": 14.0,
             "converged_count": 12}
    verdict, changed = asyncio.run(run_design_closure(
        _req(cl_max_min=1.5), polar, None,
        refine_aero=boom, rerun_struct=boom, max_iters=2))
    assert verdict["overall"] == "pass"
    assert verdict["iterations_used"] == 0 and changed is False


def test_struct_fail_resolved_by_resize_without_llm():
    from closure import run_design_closure

    async def boom(_):
        raise AssertionError("struct fix must not call LLM")

    reruns = {"n": 0}

    async def fake_rerun_struct(requirements):
        reruns["n"] += 1
        return {"bending_margin_of_safety": 0.52,
                "shear_margin_of_safety": 1.0, "v_ne_ms": 120.0}

    struct = {"bending_margin_of_safety": 0.2,
              "shear_margin_of_safety": 1.0, "v_ne_ms": 120.0}
    req = DesignRequirements(
        primary_function="w", inferred_domain="D",
        design_targets=DesignTargets(ms_min=0.5),
        wing_box_spec=WingBoxSpec(
            chord_root_m=1.1, thickness_to_chord=0.12,
            cap_area_m2=0.0008, spar_web_thickness_m=0.002))
    verdict, changed = asyncio.run(run_design_closure(
        req, None, struct,
        refine_aero=boom, rerun_struct=fake_rerun_struct, max_iters=2))
    assert reruns["n"] == 1
    assert verdict["overall"] == "pass" and changed is True


# ---------------------------------------------------------------------------
# stdlib runner
# ---------------------------------------------------------------------------

def _run_all() -> int:
    funcs = [
        test_loop_stops_after_max_iterations,
        test_loop_zero_iterations_when_pass,
        test_struct_fail_resolved_by_resize_without_llm,
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
