"""Design-closure orchestration: verdict → fix → re-analyze, bounded.

Pure control flow — every side effect (LLM refine, geometry re-export,
XFOIL, struct math) arrives as an injected async callback so main.py, the
/jobs path, the eval harness, and tests share one implementation.
Never raises: every failure path degrades to "return the last verdict".
"""
from __future__ import annotations

from verdict import (evaluate_design, refinement_prompt_for,
                     resize_wing_box_for_margin)


def _score(verdict) -> tuple:
    """Badness of a verdict: (failed count, total shortfall). Lower = better."""
    failed = [c for c in verdict.criteria if c.passed is False]
    shortfall = sum((c.target - c.actual) for c in failed
                    if c.actual is not None)
    return (len(failed), shortfall)


async def run_design_closure(
    requirements,
    aero_report: dict | None,
    struct_report: dict | None,
    *,
    refine_aero,        # async (prompt: str) -> new_aero_report | None
    rerun_struct,       # async (requirements) -> new_struct_report | None
    commit_geometry=None,  # async () -> None; called ONLY when an iteration improves
    max_iters: int = 2,
) -> tuple[dict | None, bool, dict | None, dict | None]:
    """Returns (DesignVerdict dict | None, geometry_changed flag,
    final_aero_report, final_struct_report).

    Best-attempt contract: a refinement that does not strictly improve the
    verdict is DISCARDED (reports revert, commit_geometry not called), so
    the caller never delivers geometry worse than what it already had. The
    final reports are the ones the verdict describes — callers must deliver
    THESE, not the pre-closure reports, or the payload contradicts the
    verdict.
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

    # --- Aero criteria: LLM refine + re-analyze, bounded, best-attempt ---
    while verdict.overall == "fail" and iters < max_iters:
        prompt = refinement_prompt_for(verdict)
        if not prompt:                       # nothing LLM-fixable left
            break
        prev_score = _score(verdict)
        try:
            new_aero = await refine_aero(prompt)
        except Exception:
            break
        if new_aero is None:
            break
        iters += 1
        candidate = evaluate_design(targets, new_aero, struct_report)
        improved = (candidate.overall == "pass"
                    or (candidate.overall == "fail"
                        and _score(candidate) < prev_score))
        if not improved:
            # Equal or worse (or now-unevaluable): discard the candidate —
            # the caller keeps the best geometry seen so far.
            break
        aero_report = new_aero
        verdict = candidate
        changed = True
        if commit_geometry is not None:
            await commit_geometry()

    out = verdict.model_dump()
    out["iterations_used"] = iters
    return out, changed, aero_report, struct_report
