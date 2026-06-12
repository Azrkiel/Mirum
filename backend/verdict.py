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
    # Margins can be None: _run_struct_stage sanitizes infinite MS (zero
    # stress) to None. min() over the present values; None when none present.
    ("ms_min",          "struct", lambda s: min(
        (v for v in (s.get("bending_margin_of_safety"),
                     s.get("shear_margin_of_safety")) if v is not None),
        default=None)),
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
    # NOTE: l_d_min is evaluated on the 2-D XFOIL section polar, which is
    # insensitive to planform (span/AR) changes at fixed Re — the lever must
    # change the SECTION, not the planform.
    "l_d_min": (
        "peak section lift-to-drag ratio L/D",
        "increase airfoil camber moderately (e.g. NACA 2412 → 4412) or "
        "reduce thickness-to-chord; keep the planform unchanged"),
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


# Resize guardrails: scale is clamped (a margin near -1 implies the analysis
# inputs are degenerate, not that a 30x spar belongs in the design), and the
# 2% headroom keeps the re-analyzed margin from landing epsilon-below the
# target through FP rounding (pass check is `actual >= target`).
_RESIZE_MAX_SCALE = 10.0
_RESIZE_HEADROOM = 1.02


def resize_wing_box_for_margin(targets, struct: dict | None, wing_box):
    """Analytically re-size spar caps/webs so MS meets targets.ms_min.

    MS = allowable/stress - 1 and stress ∝ 1/section_property, so scaling
    the property by (1 + MS_target)/(1 + MS_actual) meets the target (plus
    headroom) in one step. Returns an updated, re-validated WingBoxSpec
    copy; None when nothing needs resizing, inputs are missing, or the
    resized spec would violate WingBoxSpec validation (refuse rather than
    emit garbage).
    """
    if targets is None or targets.ms_min is None or struct is None:
        return None
    need = 1.0 + targets.ms_min

    def _scale(ms: float) -> float:
        return min(_RESIZE_MAX_SCALE, (need / (1.0 + ms)) * _RESIZE_HEADROOM)

    updates: dict = {}
    ms_b = struct.get("bending_margin_of_safety")
    if ms_b is not None and ms_b < targets.ms_min and ms_b > -1.0:
        updates["cap_area_m2"] = wing_box.cap_area_m2 * _scale(ms_b)
    ms_s = struct.get("shear_margin_of_safety")
    if ms_s is not None and ms_s < targets.ms_min and ms_s > -1.0:
        updates["spar_web_thickness_m"] = (
            wing_box.spar_web_thickness_m * _scale(ms_s))
    if not updates:
        return None
    # Rebuild through the constructor so Pydantic bounds re-run —
    # model_copy(update=...) skips validation.
    try:
        return wing_box.__class__(**{**wing_box.model_dump(), **updates})
    except Exception:
        return None
