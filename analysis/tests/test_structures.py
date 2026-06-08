"""
analysis/tests/test_structures.py — Unit tests for the wing structural/flight
analysis module
==============================================================================

All tests are pure Python (stdlib only) — no XFOIL, no OCC, no CadQuery.
The whole suite always runs.

Several anchor values are cross-checked against the standalone reference
``output/wing_assembly_NACA2412.py`` (MTOW 1200 kg, 60 m/s @ 3000 m,
S = 14.39 m², AL 2024-T3):

  * ISA density at 3000 m ≈ 0.909 kg/m³        (script comment: 0.9093)
  * dynamic pressure at that condition ≈ 1637 Pa (script: q = 0.5·0.9093·3600)
  * cruise CL ≈ 0.499                           (script: CL_cruise = 0.499)
  * positive maneuvering load factor = 3.8      (FAR-23 formula, clamped)
  * ultimate load factor = 5.7                  (script header: "5.7g ult")
  * AL 2024-T3 allowables Ftu=483 MPa, Fsu=290 MPa (script: MIL-HDBK-5)

Run directly:
    cd T2CIII
    python -m pytest analysis/tests/test_structures.py -v
    # or without pytest:
    python analysis/tests/test_structures.py
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup so imports work from T2CIII root or analysis/
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent          # analysis/tests/
_ANALYSIS = _HERE.parent                          # analysis/
_ROOT = _ANALYSIS.parent                          # T2CIII/
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ANALYSIS))

from analysis.structures import (  # noqa: E402
    G0,
    SAFETY_FACTOR,
    analyze_wing_structure,
    bending_margin,
    cruise_cl,
    dynamic_pressure,
    get_material,
    gust_load_factor_increment,
    isa_atmosphere,
    load_factor_envelope,
    maneuvering_load_factors,
    shear_margin,
    stall_speed,
)


# ---------------------------------------------------------------------------
# Shared fixture — the reference NACA 2412 GA wing
# ---------------------------------------------------------------------------

REF_SPEC = {
    "mtow_kg": 1200.0,
    "cruise_alt_m": 3000.0,
    "cruise_speed_ms": 60.0,
    "category": "normal",
    "cl_max": 1.5,
}
REF_WING = {
    "area_m2": 14.39,
    "span_m": 10.7284,          # 2 × SEMI_SPAN (5.3642)
}
REF_BOX = {
    "chord_root_m": 1.7881,
    "thickness_to_chord": 0.12,
    "cap_area_m2": 0.000497,
    "spar_web_thickness_m": 0.00080,
    "n_spars": 2,
}


# ---------------------------------------------------------------------------
# Group A — ISA atmosphere
# ---------------------------------------------------------------------------

class TestISA(unittest.TestCase):
    def test_sea_level(self):
        isa = isa_atmosphere(0.0)
        self.assertAlmostEqual(isa["temperature_k"], 288.15, places=2)
        self.assertAlmostEqual(isa["pressure_pa"], 101325.0, delta=1.0)
        self.assertAlmostEqual(isa["density_kgm3"], 1.225, delta=0.001)
        self.assertAlmostEqual(isa["speed_of_sound_ms"], 340.29, delta=0.5)

    def test_3000m_matches_reference(self):
        isa = isa_atmosphere(3000.0)
        # T = 288.15 - 0.0065*3000 = 268.65 K
        self.assertAlmostEqual(isa["temperature_k"], 268.65, places=2)
        self.assertAlmostEqual(isa["density_kgm3"], 0.9091, delta=0.002)
        self.assertAlmostEqual(isa["speed_of_sound_ms"], 328.6, delta=1.0)

    def test_tropopause_11km(self):
        isa = isa_atmosphere(11000.0)
        self.assertAlmostEqual(isa["temperature_k"], 216.65, places=1)
        self.assertAlmostEqual(isa["density_kgm3"], 0.3639, delta=0.003)

    def test_lower_stratosphere_isothermal(self):
        # Above 11 km the standard model is isothermal at 216.65 K.
        isa = isa_atmosphere(15000.0)
        self.assertAlmostEqual(isa["temperature_k"], 216.65, places=1)
        # Density keeps dropping above the tropopause.
        self.assertLess(isa["density_kgm3"], isa_atmosphere(11000.0)["density_kgm3"])

    def test_density_monotonic_decreasing(self):
        rhos = [isa_atmosphere(h)["density_kgm3"] for h in (0, 1000, 3000, 6000, 9000, 11000)]
        for a, b in zip(rhos, rhos[1:]):
            self.assertGreater(a, b)


# ---------------------------------------------------------------------------
# Group B — basic aero relations
# ---------------------------------------------------------------------------

class TestAeroRelations(unittest.TestCase):
    def test_dynamic_pressure(self):
        # 0.5 * 0.9093 * 60^2 = 1636.74 Pa (the script's q)
        self.assertAlmostEqual(dynamic_pressure(0.9093, 60.0), 1636.74, delta=0.5)

    def test_cruise_cl_matches_reference(self):
        rho = isa_atmosphere(3000.0)["density_kgm3"]
        cl = cruise_cl(1200.0, rho, 60.0, 14.39)
        # W = 1200*9.80665 = 11767.98 N; q*S ≈ 23545; CL ≈ 0.4998
        self.assertAlmostEqual(cl, 0.499, delta=0.005)

    def test_cruise_cl_scales_with_weight(self):
        rho = 0.9093
        cl1 = cruise_cl(1200.0, rho, 60.0, 14.39)
        cl2 = cruise_cl(2400.0, rho, 60.0, 14.39)
        self.assertAlmostEqual(cl2, 2.0 * cl1, delta=1e-6)

    def test_stall_speed(self):
        rho = isa_atmosphere(3000.0)["density_kgm3"]
        # V_s = sqrt(2W / (rho*S*CLmax))
        vs = stall_speed(1200.0, rho, 14.39, 1.5)
        self.assertAlmostEqual(vs, 34.6, delta=0.5)
        # Lower CL_max -> higher stall speed
        self.assertGreater(stall_speed(1200.0, rho, 14.39, 1.0), vs)


# ---------------------------------------------------------------------------
# Group C — load factors
# ---------------------------------------------------------------------------

class TestLoadFactors(unittest.TestCase):
    def test_maneuvering_normal_clamped_to_3p8(self):
        # FAR-23: n = 2.1 + 24000/(W_lb + 10000), clamp [2.5, 3.8].
        # 1200 kg = 2645.5 lb -> 3.998 -> clamped to 3.8.
        n_pos, n_neg = maneuvering_load_factors(1200.0, "normal")
        self.assertAlmostEqual(n_pos, 3.8, places=3)
        self.assertAlmostEqual(n_neg, -0.4 * 3.8, places=3)

    def test_maneuvering_normal_lower_bound(self):
        # A very heavy aircraft drives the formula below 2.5 -> clamp up.
        n_pos, _ = maneuvering_load_factors(50000.0, "normal")
        self.assertAlmostEqual(n_pos, 2.5, places=3)

    def test_maneuvering_normal_unclamped_midrange(self):
        # Pick a weight where the raw formula sits inside (2.5, 3.8).
        # Need 2.5 < 2.1 + 24000/(W_lb+10000) < 3.8
        #   -> 24000/(W_lb+10000) = 1.0 at W_lb = 14000 -> n = 3.1
        n_pos, _ = maneuvering_load_factors(14000.0 / 2.2046226, "normal")
        self.assertAlmostEqual(n_pos, 3.1, delta=0.01)

    def test_categories(self):
        self.assertAlmostEqual(maneuvering_load_factors(1000.0, "utility")[0], 4.4, places=3)
        self.assertAlmostEqual(maneuvering_load_factors(1000.0, "acrobatic")[0], 6.0, places=3)
        # Acrobatic negative is -0.5 * positive
        self.assertAlmostEqual(maneuvering_load_factors(1000.0, "acrobatic")[1], -3.0, places=3)

    def test_gust_increment_positive_and_reasonable(self):
        rho = isa_atmosphere(3000.0)["density_kgm3"]
        mean_chord = REF_WING["area_m2"] / REF_WING["span_m"]
        dn = gust_load_factor_increment(
            1200.0, 14.39, rho, 60.0, mean_chord,
            gust_velocity_ms=15.24, lift_curve_slope=5.7,
        )
        # Computed by hand for these inputs ≈ 2.09 (local-density, TAS form)
        self.assertGreater(dn, 0.0)
        self.assertAlmostEqual(dn, 2.09, delta=0.2)

    def test_envelope_ultimate_is_5p7(self):
        env = load_factor_envelope(REF_SPEC, REF_WING)
        # Maneuvering (3.8) governs over gust here -> n_ult = 3.8 * 1.5 = 5.7
        self.assertAlmostEqual(env["n_limit_pos"], 3.8, delta=0.05)
        self.assertAlmostEqual(env["n_ultimate_pos"], 5.7, delta=0.08)
        self.assertEqual(SAFETY_FACTOR, 1.5)

    def test_envelope_has_speeds(self):
        env = load_factor_envelope(REF_SPEC, REF_WING)
        self.assertGreater(env["v_stall_ms"], 0.0)
        # Corner speed = V_stall * sqrt(n_limit_pos)
        self.assertAlmostEqual(
            env["v_corner_ms"],
            env["v_stall_ms"] * math.sqrt(env["n_limit_pos"]),
            delta=0.1,
        )
        self.assertGreater(env["v_ne_ms"], REF_SPEC["cruise_speed_ms"])


# ---------------------------------------------------------------------------
# Group D — materials + structural margins
# ---------------------------------------------------------------------------

class TestMaterialsAndMargins(unittest.TestCase):
    def test_material_al2024(self):
        mat = get_material("AL2024-T3")
        self.assertAlmostEqual(mat["ftu_pa"], 483e6, delta=1e6)
        self.assertAlmostEqual(mat["fsu_pa"], 290e6, delta=1e6)

    def test_material_unknown_raises(self):
        with self.assertRaises(KeyError):
            get_material("unobtanium")

    def test_bending_margin_reproduces_quarter_margin(self):
        # Construct the ultimate moment that yields sigma = Ftu / 1.25,
        # i.e. MS = 0.25, using the reference box section.
        mat = get_material("AL2024-T3")
        h = REF_BOX["thickness_to_chord"] * REF_BOX["chord_root_m"]   # 0.214572 m
        I = 2.0 * REF_BOX["cap_area_m2"] * (h / 2.0) ** 2
        m_ult = (mat["ftu_pa"] / 1.25) * I / (h / 2.0)
        # load_factor=1.0 -> treat m_ult as already-ultimate.
        res = bending_margin(REF_BOX, m_ult, mat, load_factor=1.0)
        self.assertAlmostEqual(res["margin_of_safety"], 0.25, delta=0.01)
        self.assertTrue(res["passed"])

    def test_bending_margin_negative_when_overloaded(self):
        mat = get_material("AL2024-T3")
        res = bending_margin(REF_BOX, 1.0e6, mat, load_factor=1.0)
        self.assertLess(res["margin_of_safety"], 0.0)
        self.assertFalse(res["passed"])

    def test_bending_load_factor_scales_moment(self):
        mat = get_material("AL2024-T3")
        a = bending_margin(REF_BOX, 1.0e4, mat, load_factor=1.0)
        b = bending_margin(REF_BOX, 1.0e4, mat, load_factor=2.0)
        # Double the load factor -> double the stress.
        self.assertAlmostEqual(b["stress_pa"], 2.0 * a["stress_pa"], delta=1.0)

    def test_shear_margin_formula(self):
        mat = get_material("AL2024-T3")
        h = REF_BOX["thickness_to_chord"] * REF_BOX["chord_root_m"]
        v = 1.0e4
        res = shear_margin(REF_BOX, v, mat, load_factor=1.0)
        expected_tau = v / (REF_BOX["n_spars"] * h * REF_BOX["spar_web_thickness_m"])
        self.assertAlmostEqual(res["stress_pa"], expected_tau, delta=1.0)
        self.assertAlmostEqual(
            res["margin_of_safety"], mat["fsu_pa"] / expected_tau - 1.0, delta=1e-6
        )


# ---------------------------------------------------------------------------
# Group E — high-level analyze_wing_structure
# ---------------------------------------------------------------------------

class TestAnalyzeWingStructure(unittest.TestCase):
    def test_report_contract(self):
        rep = analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX, material="AL2024-T3")
        for key in (
            "cruise", "dynamic_pressure_pa", "cl_cruise", "wing_loading_pa",
            "envelope", "root_bending_moment_nm", "root_shear_force_n",
            "bending", "shear", "material", "passed", "warnings",
        ):
            self.assertIn(key, rep)
        self.assertEqual(rep["material"], "AL2024-T3")
        self.assertIsInstance(rep["warnings"], list)

    def test_report_cruise_values(self):
        rep = analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX)
        self.assertAlmostEqual(rep["cl_cruise"], 0.499, delta=0.005)
        self.assertAlmostEqual(rep["envelope"]["n_ultimate_pos"], 5.7, delta=0.08)
        self.assertAlmostEqual(rep["cruise"]["density_kgm3"], 0.9091, delta=0.002)

    def test_report_loads_positive(self):
        rep = analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX)
        self.assertGreater(rep["root_bending_moment_nm"], 0.0)
        self.assertGreater(rep["root_shear_force_n"], 0.0)
        # Shear at the root carries half the ultimate lift (one semi-span).
        w = REF_SPEC["mtow_kg"] * G0
        n_ult = rep["envelope"]["n_ultimate_pos"]
        self.assertAlmostEqual(rep["root_shear_force_n"], n_ult * w / 2.0, delta=1.0)

    def test_explicit_loads_override_estimate(self):
        rep = analyze_wing_structure(
            REF_SPEC, REF_WING, REF_BOX,
            root_bending_moment_nm=40000.0,
            root_shear_force_n=30000.0,
        )
        # When provided, the loads are used verbatim (already ultimate).
        self.assertAlmostEqual(rep["root_bending_moment_nm"], 40000.0, delta=1.0)
        self.assertAlmostEqual(rep["root_shear_force_n"], 30000.0, delta=1.0)

    def test_missing_span_warns_but_survives(self):
        wing_no_span = {"area_m2": 14.39}
        rep = analyze_wing_structure(REF_SPEC, wing_no_span, REF_BOX)
        # Cannot estimate a bending moment without span -> warning recorded.
        self.assertTrue(any("span" in w.lower() for w in rep["warnings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
