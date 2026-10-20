"""Defect E: legacy structural false passes (invalid / signed / zero inputs)."""
import math

import pytest

from analysis.structures import (
    StructuralInputError,
    analyze_wing_structure,
    bending_margin,
    get_material,
    shear_margin,
)
from analysis.tests.test_structures import REF_BOX, REF_SPEC, REF_WING

MAT = get_material("AL2024-T3")


# --- recorded high-level false passes --------------------------------------

def test_recorded_negative_load_false_pass_now_fails_with_finite_margin():
    rep = analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX,
                                 root_bending_moment_nm=-1e12, root_shear_force_n=-1e12)
    assert rep["passed"] is False
    for key in ("bending", "shear"):
        assert math.isfinite(rep[key]["margin_of_safety"])
        assert rep[key]["margin_of_safety"] < 0
        assert rep[key]["stress_pa"] < 0                      # signed
        assert rep[key]["stress_magnitude_pa"] > 0
    assert rep["load_cases"][0]["name"] == "explicit"


def test_recorded_zero_section_false_pass_now_raises():
    with pytest.raises(StructuralInputError):
        analyze_wing_structure(REF_SPEC, REF_WING,
                               dict(REF_BOX, cap_area_m2=0, spar_web_thickness_m=0))


# --- direct helper calls with signed loads ---------------------------------

def test_bending_negative_equals_positive_magnitude():
    pos = bending_margin(REF_BOX, 1e4, MAT)
    neg = bending_margin(REF_BOX, -1e4, MAT)
    assert neg["margin_of_safety"] == pytest.approx(pos["margin_of_safety"])
    assert neg["passed"] and neg["status"] == "evaluated"
    assert neg["stress_pa"] == pytest.approx(-pos["stress_pa"])
    assert neg["compression_evaluated"] is False


def test_shear_negative_equals_positive_magnitude():
    pos = shear_margin(REF_BOX, 1e3, MAT)
    neg = shear_margin(REF_BOX, -1e3, MAT)
    assert neg["margin_of_safety"] == pytest.approx(pos["margin_of_safety"])
    assert neg["passed"]


@pytest.mark.parametrize("fn", [bending_margin, shear_margin])
def test_overloaded_negative_fails_finite(fn):
    r = fn(REF_BOX, -1e12, MAT)
    assert r["passed"] is False and math.isfinite(r["margin_of_safety"])
    assert r["margin_of_safety"] < 0


@pytest.mark.parametrize("fn", [bending_margin, shear_margin])
def test_zero_load_status(fn):
    r = fn(REF_BOX, 0.0, MAT)
    assert r["status"] == "zero_load" and r["margin_of_safety"] == math.inf
    assert r["passed"] is True and r["stress_pa"] == 0


# --- invalid inputs --------------------------------------------------------

BAD_NUM = [0, -1.0, math.nan, math.inf, -math.inf]
BAD_LOAD = [math.nan, math.inf, -math.inf, True, "1"]


@pytest.mark.parametrize("field", ["chord_root_m", "thickness_to_chord",
                                   "cap_area_m2", "spar_web_thickness_m"])
@pytest.mark.parametrize("bad", BAD_NUM)
@pytest.mark.parametrize("fn", [bending_margin, shear_margin])
def test_invalid_geometry_helpers(fn, field, bad):
    with pytest.raises(StructuralInputError, match=field):
        fn(dict(REF_BOX, **{field: bad}), 1e4, MAT)


@pytest.mark.parametrize("field", ["chord_root_m", "thickness_to_chord",
                                   "cap_area_m2", "spar_web_thickness_m"])
@pytest.mark.parametrize("bad", BAD_NUM)
def test_invalid_geometry_high_level(field, bad):
    with pytest.raises(StructuralInputError):
        analyze_wing_structure(REF_SPEC, REF_WING, dict(REF_BOX, **{field: bad}))


@pytest.mark.parametrize("n", [0, -1, 2.5, True, math.nan])
@pytest.mark.parametrize("fn", [bending_margin, shear_margin])
def test_invalid_n_spars(fn, n):
    with pytest.raises(StructuralInputError, match="n_spars"):
        fn(dict(REF_BOX, n_spars=n), 1e4, MAT)


def test_invalid_n_spars_high_level():
    with pytest.raises(StructuralInputError, match="n_spars"):
        analyze_wing_structure(REF_SPEC, REF_WING, dict(REF_BOX, n_spars=0))


@pytest.mark.parametrize("bad", BAD_LOAD)
@pytest.mark.parametrize("fn", [bending_margin, shear_margin])
def test_invalid_loads_helpers(fn, bad):
    with pytest.raises(StructuralInputError):
        fn(REF_BOX, bad, MAT)


@pytest.mark.parametrize("bad", BAD_LOAD)
def test_invalid_loads_high_level(bad):
    with pytest.raises(StructuralInputError):
        analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX, root_bending_moment_nm=bad)
    with pytest.raises(StructuralInputError):
        analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX, root_shear_force_n=bad)


@pytest.mark.parametrize("bad", BAD_NUM)
@pytest.mark.parametrize("fn,key", [(bending_margin, "ftu_pa"), (shear_margin, "fsu_pa")])
def test_invalid_allowables(fn, key, bad):
    with pytest.raises(StructuralInputError, match=key):
        fn(REF_BOX, 1e4, dict(MAT, **{key: bad}))


@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf])
@pytest.mark.parametrize("fn", [bending_margin, shear_margin])
def test_invalid_load_factor(fn, bad):
    with pytest.raises(StructuralInputError, match="load_factor"):
        fn(REF_BOX, 1e4, MAT, load_factor=bad)


@pytest.mark.parametrize("fn", [bending_margin, shear_margin])
def test_underflow_section_raises(fn):
    with pytest.raises(StructuralInputError):
        fn(dict(REF_BOX, cap_area_m2=1e-320, spar_web_thickness_m=1e-320), 1e4, MAT)


def test_underflow_high_level_raises():
    with pytest.raises(StructuralInputError):
        analyze_wing_structure(REF_SPEC, REF_WING, dict(REF_BOX, cap_area_m2=1e-320))


def test_overflow_stress_raises():
    with pytest.raises(StructuralInputError):
        bending_margin(dict(REF_BOX, cap_area_m2=1e-12), 1e308, MAT)
    with pytest.raises(StructuralInputError):
        shear_margin(dict(REF_BOX, spar_web_thickness_m=1e-12), 1e308, MAT)


@pytest.mark.parametrize("which,key,bad", [
    ("spec", "mtow_kg", 0), ("spec", "mtow_kg", math.nan),
    ("spec", "cruise_speed_ms", -5), ("spec", "cruise_alt_m", math.inf),
    ("spec", "cl_max", 0), ("spec", "gust_velocity_ms", -1),
    ("spec", "vne_factor", math.nan), ("spec", "lift_curve_slope", 0),
    ("wing", "area_m2", 0), ("wing", "span_m", -1), ("wing", "mean_chord_m", 0),
])
def test_invalid_spec_and_wing(which, key, bad):
    spec, wing = dict(REF_SPEC), dict(REF_WING)
    (spec if which == "spec" else wing)[key] = bad
    with pytest.raises(StructuralInputError, match=key):
        analyze_wing_structure(spec, wing, REF_BOX)


@pytest.mark.parametrize("bad", [0, -0.1, 1.5, math.nan])
def test_invalid_cop(bad):
    with pytest.raises(StructuralInputError, match="center_of_pressure_frac"):
        analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX, center_of_pressure_frac=bad)


def test_unknown_material_still_keyerror():
    with pytest.raises(KeyError):
        analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX, material="UNOBTAINIUM")


# --- load controls, zero load, envelope ------------------------------------

@pytest.mark.parametrize("sign", [1, -1])
def test_large_load_controls_fail_finite(sign):
    rep = analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX,
                                 root_bending_moment_nm=sign * 1e9,
                                 root_shear_force_n=sign * 1e9)
    assert rep["passed"] is False
    assert math.isfinite(rep["bending"]["margin_of_safety"])
    assert math.isfinite(rep["shear"]["margin_of_safety"])


def test_explicit_zero_load_is_zero_load_status():
    rep = analyze_wing_structure(REF_SPEC, {"area_m2": 14.39}, REF_BOX,
                                 root_bending_moment_nm=0.0, root_shear_force_n=0.0)
    assert rep["bending"]["status"] == "zero_load"
    assert rep["bending"]["margin_of_safety"] == math.inf
    assert rep["shear"]["status"] == "zero_load"
    assert rep["passed"] is True


def test_missing_moment_status_not_evaluated():
    rep = analyze_wing_structure(REF_SPEC, {"area_m2": 14.39}, REF_BOX)
    assert rep["bending"]["status"] == "not_evaluated"
    assert rep["bending"]["passed"] is False and rep["passed"] is False


def test_load_envelope_positive_governs_for_reference():
    rep = analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX)
    names = [c["name"] for c in rep["load_cases"]]
    assert names == ["positive_ultimate", "negative_ultimate"]
    pos, neg = rep["load_cases"]
    assert pos["load_factor"] > 0 > neg["load_factor"]
    assert neg["root_bending_moment_nm"] < 0 and neg["root_shear_force_n"] < 0
    assert rep["governing_load_case"] == "positive_ultimate"
    assert rep["root_bending_moment_nm"] == pos["root_bending_moment_nm"]
    assert rep["bending"] == pos["bending"]
    assert rep["passed"] == (pos["passed"] and neg["passed"])
    assert rep["method"] == "analytic_sizing_estimate"
    assert rep["engineering_certified"] is False
    assert any("buckling" in w for w in rep["warnings"])


def test_negative_case_has_larger_margin_than_positive():
    rep = analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX)
    pos, neg = rep["load_cases"]
    assert neg["bending"]["margin_of_safety"] > pos["bending"]["margin_of_safety"]
