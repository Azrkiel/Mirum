"""
backend/tests/test_verdict.py
==============================

Unit tests for the design-closure verdict layer: the ``DesignTargets``
criteria schema, the pure ``verdict.evaluate_design`` function, and the
REA structured-output schema's ``design_targets`` extraction block.

No LLM calls, no XFOIL, no subprocess — pure logic, stdlib-runnable.

Run directly:
    cd T2CIII/backend
    python tests/test_verdict.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# agents.py / main.py assert GEMINI_API_KEY_HEAVY at import time.  Provide a
# dummy so imports succeed in CI / bare-stdlib runs.  No LLM call is made.
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-dummy-key-not-used")

# Path setup: backend/ on sys.path; repo root on sys.path so `analysis.*` resolves.
_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# 1.1 DesignTargets schema
# ---------------------------------------------------------------------------

def test_design_targets_schema():
    from schemas import DesignTargets
    t = DesignTargets(cl_max_min=1.5, ms_min=0.5)
    assert t.cl_max_min == 1.5
    assert t.l_d_min is None          # unset criteria are None
    assert t.ms_min == 0.5


def test_design_targets_rejects_negative_cl():
    from schemas import DesignTargets
    try:
        DesignTargets(cl_max_min=-1.0)
        assert False, "should reject"
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1.3 evaluate_design verdict logic
# ---------------------------------------------------------------------------

POLAR = {"Cl_max": 1.62, "stall_alpha": 14.5,
         "L_D": [10.0, 18.0, 22.5, 19.0], "converged_count": 18}
STRUCT = {"bending_margin_of_safety": 0.41, "shear_margin_of_safety": 1.2,
          "bending_passed": True, "shear_passed": True, "v_ne_ms": 120.0}


def test_verdict_all_pass():
    from schemas import DesignTargets
    from verdict import evaluate_design
    v = evaluate_design(DesignTargets(cl_max_min=1.5, ms_min=0.2), POLAR, STRUCT)
    assert v.overall == "pass"
    assert {c.name for c in v.criteria} == {"cl_max_min", "ms_min"}
    assert all(c.passed for c in v.criteria)


def test_verdict_fail_lists_failed_criterion():
    from schemas import DesignTargets
    from verdict import evaluate_design
    v = evaluate_design(DesignTargets(ms_min=0.5), POLAR, STRUCT)
    assert v.overall == "fail"
    failed = [c for c in v.criteria if not c.passed]
    assert failed[0].name == "ms_min"
    assert failed[0].actual == 0.41      # min(bending, shear)
    assert failed[0].target == 0.5


def test_verdict_missing_report_is_incomplete_not_fail():
    from schemas import DesignTargets
    from verdict import evaluate_design
    v = evaluate_design(DesignTargets(cl_max_min=1.5), None, STRUCT)
    assert v.overall == "incomplete"
    assert v.criteria[0].passed is None   # unevaluated, not failed


def test_verdict_no_targets_is_not_applicable():
    from verdict import evaluate_design
    v = evaluate_design(None, POLAR, STRUCT)
    assert v.overall == "not_applicable"
    assert v.criteria == []


def test_verdict_ms_min_tolerates_none_margin():
    # _run_struct_stage sanitizes infinite margins (zero stress) to None;
    # the ms_min extractor must min() over the present values, not crash.
    from schemas import DesignTargets
    from verdict import evaluate_design
    struct = {"bending_margin_of_safety": 0.41,
              "shear_margin_of_safety": None, "v_ne_ms": 120.0}
    v = evaluate_design(DesignTargets(ms_min=0.5), POLAR, struct)
    assert v.overall == "fail"
    assert v.criteria[0].actual == 0.41


def test_verdict_ms_min_unevaluated_when_all_margins_absent():
    from schemas import DesignTargets
    from verdict import evaluate_design
    for struct in ({"bending_margin_of_safety": None,
                    "shear_margin_of_safety": None},
                   {"v_ne_ms": 120.0}):   # keys missing entirely
        v = evaluate_design(DesignTargets(ms_min=0.5), POLAR, struct)
        assert v.overall == "incomplete"
        assert v.criteria[0].passed is None


# ---------------------------------------------------------------------------
# 1.6 REA structured-output schema extracts design_targets
# ---------------------------------------------------------------------------

def test_rea_schema_includes_design_targets():
    import agents
    schema = agents._REA_BASE_SCHEMA  # the structured-output dict
    props = schema["properties"]
    assert "design_targets" in props
    dt = props["design_targets"]["properties"]
    assert set(dt) == {"cl_max_min", "l_d_min", "stall_alpha_min",
                       "ms_min", "v_ne_min_ms"}


# ---------------------------------------------------------------------------
# 3.1 refinement_prompt_for — aero lever table + struct exclusion
# ---------------------------------------------------------------------------

def test_refinement_prompt_names_aero_criterion_and_lever():
    from schemas import CriterionResult, DesignVerdict
    from verdict import refinement_prompt_for
    v = DesignVerdict(overall="fail", criteria=[
        CriterionResult(name="cl_max_min", target=1.5, actual=1.31,
                        passed=False, source="polar"),
    ])
    p = refinement_prompt_for(v)
    assert "Cl_max" in p and "1.31" in p and "1.5" in p
    assert "camber" in p            # the engineering lever


def test_refinement_prompt_skips_struct_and_vne_criteria():
    from schemas import CriterionResult, DesignVerdict
    from verdict import refinement_prompt_for
    v = DesignVerdict(overall="fail", criteria=[
        CriterionResult(name="ms_min", target=0.5, actual=0.41,
                        passed=False, source="struct"),
        CriterionResult(name="v_ne_min_ms", target=150.0, actual=120.0,
                        passed=False, source="struct"),
    ])
    assert refinement_prompt_for(v) == ""   # nothing LLM-fixable


# ---------------------------------------------------------------------------
# 3.3 resize_wing_box_for_margin — analytic spar re-sizing
# ---------------------------------------------------------------------------

def test_resize_wing_box_scales_cap_area_to_meet_margin():
    from schemas import DesignTargets, WingBoxSpec
    from verdict import resize_wing_box_for_margin
    wb = WingBoxSpec(chord_root_m=1.1, thickness_to_chord=0.12,
                     cap_area_m2=0.0008, spar_web_thickness_m=0.002)
    struct = {"bending_margin_of_safety": 0.2, "shear_margin_of_safety": 1.0}
    new = resize_wing_box_for_margin(DesignTargets(ms_min=0.5), struct, wb)
    assert new is not None
    # 2% headroom so FP rounding in re-analysis can't land epsilon-below target
    assert abs(new.cap_area_m2 - 0.0008 * (1.5 / 1.2) * 1.02) < 1e-9
    assert new.spar_web_thickness_m == wb.spar_web_thickness_m  # shear already OK


def test_resize_wing_box_clamps_scale_at_10x():
    from schemas import DesignTargets, WingBoxSpec
    from verdict import resize_wing_box_for_margin
    wb = WingBoxSpec(chord_root_m=1.1, thickness_to_chord=0.12,
                     cap_area_m2=0.0008, spar_web_thickness_m=0.002)
    # ms close to -1 -> raw scale ~30x; must clamp to 10x
    struct = {"bending_margin_of_safety": -0.95, "shear_margin_of_safety": 1.0}
    new = resize_wing_box_for_margin(DesignTargets(ms_min=0.5), struct, wb)
    assert new is not None
    assert abs(new.cap_area_m2 - 0.0008 * 10.0) < 1e-12


def test_resize_wing_box_refuses_validation_violating_resize():
    from schemas import DesignTargets, WingBoxSpec
    from verdict import resize_wing_box_for_margin
    # cap_area near its schema upper bound: any meaningful scale would
    # violate WingBoxSpec validation -> refuse (None), don't emit garbage.
    wb = WingBoxSpec(chord_root_m=1.1, thickness_to_chord=0.12,
                     cap_area_m2=0.5, spar_web_thickness_m=0.002)
    struct = {"bending_margin_of_safety": -0.9, "shear_margin_of_safety": 1.0}
    assert resize_wing_box_for_margin(DesignTargets(ms_min=0.5), struct, wb) is None


def test_resize_wing_box_none_when_margins_already_met():
    from schemas import DesignTargets, WingBoxSpec
    from verdict import resize_wing_box_for_margin
    wb = WingBoxSpec(chord_root_m=1.1, thickness_to_chord=0.12,
                     cap_area_m2=0.0008, spar_web_thickness_m=0.002)
    struct = {"bending_margin_of_safety": 0.9, "shear_margin_of_safety": 1.0}
    assert resize_wing_box_for_margin(DesignTargets(ms_min=0.5), struct, wb) is None


# ---------------------------------------------------------------------------
# stdlib runner
# ---------------------------------------------------------------------------

def _run_all() -> int:
    funcs = [
        test_design_targets_schema,
        test_design_targets_rejects_negative_cl,
        test_verdict_all_pass,
        test_verdict_fail_lists_failed_criterion,
        test_verdict_missing_report_is_incomplete_not_fail,
        test_verdict_no_targets_is_not_applicable,
        test_verdict_ms_min_tolerates_none_margin,
        test_verdict_ms_min_unevaluated_when_all_margins_absent,
        test_rea_schema_includes_design_targets,
        test_refinement_prompt_names_aero_criterion_and_lever,
        test_refinement_prompt_skips_struct_and_vne_criteria,
        test_resize_wing_box_scales_cap_area_to_meet_margin,
        test_resize_wing_box_clamps_scale_at_10x,
        test_resize_wing_box_refuses_validation_violating_resize,
        test_resize_wing_box_none_when_margins_already_met,
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
