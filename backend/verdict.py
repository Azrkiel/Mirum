"""Pure design-verdict logic: DesignTargets + analysis reports → DesignVerdict.

No LLM calls, no subprocess, no I/O. Reports arrive as plain dicts
(model_dump() output of PolarReport / StructuralReport) so this module
stays import-light and stdlib-testable.
"""
from __future__ import annotations

from schemas import CriterionResult, DesignTargets, DesignVerdict

# (criterion field, source report, extractor) — extractor returns None if
# the value is absent from the report (e.g. XFOIL never converged).
_CRITERIA = [
    ("cl_max_min",      "polar",  lambda p: p.get("Cl_max")),
    # L_D arrives through _sanitize_floats: unconverged points are None, not
    # NaN — bare max() would TypeError. Filter first.
    ("l_d_min",         "polar",  lambda p: max(
        (x for x in p.get("L_D") or [] if x is not None), default=None)),
    ("stall_alpha_min", "polar",  lambda p: p.get("stall_alpha")),
    ("ms_min",          "struct", lambda s: min(
        s["bending_margin_of_safety"], s["shear_margin_of_safety"])
        if "bending_margin_of_safety" in s else None),
    ("v_ne_min_ms",     "struct", lambda s: s.get("v_ne_ms")),
]


def evaluate_design(
    targets: DesignTargets | None,
    polar: dict | None,
    struct: dict | None,
) -> DesignVerdict:
    if targets is None:
        return DesignVerdict(overall="not_applicable")
    reports = {"polar": polar, "struct": struct}
    criteria: list[CriterionResult] = []
    for name, source, extract in _CRITERIA:
        target = getattr(targets, name)
        if target is None:
            continue
        report = reports[source]
        actual = extract(report) if report is not None else None
        passed = (actual >= target) if actual is not None else None
        criteria.append(CriterionResult(
            name=name, target=target, actual=actual,
            passed=passed, source=source))
    if not criteria:
        return DesignVerdict(overall="not_applicable")
    if any(c.passed is False for c in criteria):
        overall = "fail"
    elif any(c.passed is None for c in criteria):
        overall = "incomplete"
    else:
        overall = "pass"
    return DesignVerdict(overall=overall, criteria=criteria)


# LLM-refinable criteria only. ms_min is solved analytically by
# resize_wing_box_for_margin (spec-level, no LLM); v_ne_min_ms has no
# geometry lever at all (envelope is aircraft-spec-driven) — check-only.
_AERO_LEVERS = {
    "cl_max_min": (
        "peak lift coefficient Cl_max",
        "increase airfoil camber (e.g. NACA 2412 → 4412) or increase "
        "wing chord; do not change span"),
    "l_d_min": (
        "peak lift-to-drag ratio L/D",
        "increase aspect ratio: extend span and reduce chord at constant area"),
    "stall_alpha_min": (
        "stall angle of attack",
        "reduce camber slightly or add washout (negative tip twist)"),
}


def refinement_prompt_for(verdict: DesignVerdict) -> str:
    """Failed AERO criteria → one NL instruction for run_refiner.

    Returns "" when no failed criterion is LLM-fixable (caller skips the
    refine call entirely).
    """
    items = []
    for c in verdict.criteria:
        if c.passed is False and c.name in _AERO_LEVERS:
            quantity, lever = _AERO_LEVERS[c.name]
            items.append(
                f"- {quantity} is {c.actual:.3g}, required ≥ {c.target:.3g}. "
                f"Fix: {lever}.")
    if not items:
        return ""
    return "\n".join(
        ["The generated design fails its stated performance requirements. "
         "Apply the SMALLEST geometry change that fixes each item:"]
        + items
        + ["Keep every dimension not named above unchanged."])
