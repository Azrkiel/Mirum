"""Design-closure orchestration: verdict → fix → re-analyze, bounded.

Pure control flow — every side effect (LLM refine, geometry re-export,
XFOIL, struct math) arrives as an injected async callback so main.py, the
/jobs path, the eval harness, and tests share one implementation.
Never raises: every failure path degrades to "return the last verdict".
"""
from __future__ import annotations

from verdict import (evaluate_design, refinement_prompt_for,
                     resize_wing_box_for_margin)


def _fail_signature(verdict) -> tuple:
    """Stable key for 'has anything improved?': overall + failing actuals."""
    return (verdict.overall,
            tuple((c.name, c.actual) for c in verdict.criteria
                  if c.passed is False))


async def run_design_closure(
    requirements,
    aero_report: dict | None,
    struct_report: dict | None,
    *,
    refine_aero,        # async (prompt: str) -> new_aero_report | None
    rerun_struct,       # async (requirements) -> new_struct_report | None
    max_iters: int = 2,
) -> tuple[dict | None, bool, dict | None, dict | None]:
    """Returns (DesignVerdict dict | None, geometry_changed flag,
    final_aero_report, final_struct_report).

    The final reports are the ones the verdict describes — callers must
    deliver THESE, not the pre-closure reports, or the payload contradicts
    the verdict.
    """
    targets = getattr(requirements, "design_targets", None)
    verdict = evaluate_design(targets, aero_report, struct_report)
    if verdict.overall in ("not_applicable",):
        return None, False, aero_report, struct_report
    changed = False
    iters = 0

    # --- Struct criteria: one analytic re-size, no LLM, free ---
    if any(c.passed is False and c.source == "struct"
           for c in verdict.criteria):
        old_wb = getattr(requirements, "wing_box_spec", None)
        new_wb = (resize_wing_box_for_margin(targets, struct_report, old_wb)
                  if old_wb is not None else None)
        if new_wb is not None:
            requirements.wing_box_spec = new_wb
            try:
                new_struct = await rerun_struct(requirements)
            except Exception:
                new_struct = None
            if new_struct is not None:
                struct_report = new_struct
                changed = True
            else:
                # Re-analysis failed: the resized spec is unverified — roll
                # back so the shared requirements never keeps phantom values.
                requirements.wing_box_spec = old_wb
            verdict = evaluate_design(targets, aero_report, struct_report)

    # --- Aero criteria: LLM refine + re-analyze, bounded ---
    while verdict.overall == "fail" and iters < max_iters:
        prompt = refinement_prompt_for(verdict)
        if not prompt:                       # nothing LLM-fixable left
            break
        prev_sig = _fail_signature(verdict)
        try:
            new_aero = await refine_aero(prompt)
        except Exception:
            break
        if new_aero is None:
            break
        aero_report = new_aero
        changed = True
        iters += 1
        verdict = evaluate_design(targets, aero_report, struct_report)
        if _fail_signature(verdict) == prev_sig:
            break  # no progress — further iterations are provably wasted

    out = verdict.model_dump()
    out["iterations_used"] = iters
    return out, changed, aero_report, struct_report
