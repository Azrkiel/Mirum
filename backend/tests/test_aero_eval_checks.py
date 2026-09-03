"""Tests for the Domain D eval verification added to the harness.

Covers the NACA-profile check, the XFOIL convergence gate (including its
analytical-fallback trap), and the corrected features-matched arithmetic.

The real XFOIL invocation is stubbed — this pins the DECISION logic, which
is what determines pass/fail in the merge gate. XFOIL itself is exercised by
the airfoil test suite.

    python -m pytest backend/tests/test_aero_eval_checks.py
"""

import asyncio
import os
import pathlib
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from eval_harness.runner import (  # noqa: E402
    _check_aero,
    _check_naca,
    _count_matched_features,
    _naca_codes_in,
)
from eval_harness._md_report import _fmt_features  # noqa: E402


# ---------------------------------------------------------------------------
# NACA profile detection
# ---------------------------------------------------------------------------

def test_finds_code_in_helper_calls():
    assert _naca_codes_in('w = make_naca_wire("2412", 200)') == {"2412"}
    assert _naca_codes_in("make_naca_airfoil('0012', 200, 500)") == {"0012"}
    assert _naca_codes_in('naca4_points("4415")') == {"4415"}
    assert _naca_codes_in("make_planform_wires(naca_code='0018', root_chord=1)") == {"0018"}


def test_ignores_bare_four_digit_dimensions():
    """'0012' as a plain number must not be mistaken for a profile."""
    assert _naca_codes_in("result = cq.Workplane().box(1000, 2412, 5)") == set()
    assert _naca_codes_in("thickness = 0.0012") == set()


def test_correct_profile_passes():
    rec = {"status": "pass"}
    _check_naca(rec, {"expected_naca_code": "0012"},
                'make_naca_airfoil("0012", 200, 500)')
    assert rec["status"] == "pass"
    assert "error" not in rec


def test_wrong_profile_fails_with_both_codes_named():
    """The bug this whole check exists for: a clean solid, wrong airfoil."""
    rec = {"status": "pass"}
    _check_naca(rec, {"expected_naca_code": "0012"},
                'make_naca_airfoil("0024", 200, 500)')
    assert rec["status"] == "fail"
    assert "NACA_MISMATCH" in rec["error"]
    assert "0012" in rec["error"] and "0024" in rec["error"]


def test_no_profile_reference_fails():
    rec = {"status": "pass"}
    _check_naca(rec, {"expected_naca_code": "0012"},
                "result = cq.Workplane().box(200, 500, 20)")
    assert rec["status"] == "fail"
    assert "NACA_NOT_FOUND" in rec["error"]


def test_prompts_without_the_field_are_untouched():
    rec = {"status": "pass"}
    _check_naca(rec, {}, "result = cq.Workplane().box(1, 1, 1)")
    assert rec["status"] == "pass"


# ---------------------------------------------------------------------------
# XFOIL convergence gate
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_analysis_modules():
    """Undo the fake `analysis` package after every test.

    _check_aero imports analysis.airfoil lazily, so stubbing it means writing
    into sys.modules. Leaving that in place poisons any later test that
    imports the real package — test_struct_stage imports analysis.structures
    and would fail with a bare ModuleType standing in for it.
    """
    saved = {name: sys.modules.get(name)
             for name in ("analysis", "analysis.airfoil", "analysis.structures")}
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _install_fake_xfoil(polar):
    """Inject a stub analysis.airfoil so _check_aero's lazy import resolves."""
    _install_fake_module(lambda *a, **kw: polar)


def _install_fake_module(fn):
    pkg = types.ModuleType("analysis")
    pkg.__path__ = []
    mod = types.ModuleType("analysis.airfoil")
    mod.analyze_wing_step = fn
    sys.modules["analysis"] = pkg
    sys.modules["analysis.airfoil"] = mod


def _step_file():
    fd, path = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    return path


def _run_aero(record, prompt, step_path):
    asyncio.run(_check_aero(record, prompt, step_path))


_PROMPT = {"min_converged_alpha": 14, "expected_naca_code": "0012"}
_REC_BASE = {"status": "pass", "bbox_extents": [500.0, 200.0, 24.0]}


def test_enough_converged_points_passes():
    _install_fake_xfoil({"converged_count": 18, "dat_path": "/tmp/section.dat"})
    rec = dict(_REC_BASE)
    path = _step_file()
    try:
        _run_aero(rec, _PROMPT, path)
    finally:
        os.remove(path)
    assert rec["status"] == "pass"
    assert rec["converged_count"] == 18
    assert rec["aero_from_geometry"] is True


def test_under_converged_fails():
    _install_fake_xfoil({"converged_count": 5, "dat_path": "/tmp/section.dat"})
    rec = dict(_REC_BASE)
    path = _step_file()
    try:
        _run_aero(rec, _PROMPT, path)
    finally:
        os.remove(path)
    assert rec["status"] == "fail"
    assert "AERO_UNDER_CONVERGED" in rec["error"]
    assert "5" in rec["error"] and "14" in rec["error"]


def test_analytical_fallback_fails_even_when_convergence_is_great():
    """dat_path=None means XFOIL read the textbook profile, not the model's solid.

    Without this the gate would happily report a perfect polar for geometry
    that could not even be sliced — measuring NACA's definition, not Mirum.
    """
    _install_fake_xfoil({"converged_count": 20, "dat_path": None})
    rec = dict(_REC_BASE)
    path = _step_file()
    try:
        _run_aero(rec, _PROMPT, path)
    finally:
        os.remove(path)
    assert rec["status"] == "fail"
    assert "AERO_ANALYTICAL_FALLBACK" in rec["error"]
    assert rec["aero_from_geometry"] is False


def test_missing_step_fails():
    _install_fake_xfoil({"converged_count": 20, "dat_path": "/tmp/x.dat"})
    rec = dict(_REC_BASE)
    _run_aero(rec, _PROMPT, "/nonexistent/path.step")
    assert rec["status"] == "fail"
    assert "AERO_NO_STEP" in rec["error"]


def test_xfoil_exception_fails_cleanly():
    def _boom(*a, **kw):
        raise RuntimeError("xfoil not found")
    _install_fake_module(_boom)

    rec = dict(_REC_BASE)
    path = _step_file()
    try:
        _run_aero(rec, _PROMPT, path)
    finally:
        os.remove(path)
    assert rec["status"] == "fail"
    assert "AERO_FAILED" in rec["error"]


def test_prompt_without_threshold_is_skipped():
    _install_fake_xfoil({"converged_count": 0, "dat_path": None})
    rec = {"status": "pass"}
    _run_aero(rec, {}, "/nonexistent.step")
    assert rec["status"] == "pass"


def test_already_failed_record_is_not_overwritten():
    _install_fake_xfoil({"converged_count": 20, "dat_path": "/tmp/x.dat"})
    rec = {"status": "fail", "error": "compile failure"}
    _run_aero(rec, _PROMPT, "/nonexistent.step")
    assert rec["error"] == "compile failure"


def test_unknown_span_chord_skips_rather_than_failing():
    _install_fake_xfoil({"converged_count": 20, "dat_path": "/tmp/x.dat"})
    rec = {"status": "pass"}          # no bbox_extents
    path = _step_file()
    try:
        _run_aero(rec, _PROMPT, path)
    finally:
        os.remove(path)
    assert rec["status"] == "pass"
    assert "aero_skipped" in rec


# ---------------------------------------------------------------------------
# features-matched arithmetic
# ---------------------------------------------------------------------------

def _features(expected, missing):
    return _fmt_features({"missing_features": missing},
                         {"expected_features": expected})


def test_features_all_present():
    assert _features(["loft", "fillet"], []) == "2/2"


def test_features_partial():
    assert _features(["loft", "spline", "fillet", "shell"],
                     ["fillet", "shell"]) == "2/4"


def test_judge_naming_a_gap_outside_expected_no_longer_docks_a_feature():
    """Old arithmetic: 2 - 1 = 1/2. The judge's gripe matches no expected item."""
    assert _features(["loft", "fillet"], ["chamfer radius too large"]) == "2/2"


def test_multiple_judge_entries_for_one_feature_cannot_go_negative():
    """Old arithmetic: 1 - 3 = clamped 0/1, over-penalising a single gap."""
    assert _features(["fillet"], ["fillet on top", "fillet radius", "fillet edges"]) == "0/1"


def test_features_blank_when_judge_did_not_run():
    assert _fmt_features({}, {"expected_features": ["loft"]}) == ""


def test_count_matched_features_helper_matches_reporter():
    assert _count_matched_features(["loft", "fillet"], ["fillet"]) == (1, 2)
    assert _count_matched_features([], ["x"]) is None
    assert _count_matched_features(["loft"], None) is None
