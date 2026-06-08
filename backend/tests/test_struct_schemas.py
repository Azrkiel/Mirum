"""
backend/tests/test_struct_schemas.py
=====================================

Schema-only tests for the Phase-4 structural / flight-envelope pipeline
integration.  Pure-stdlib + pydantic; no FastAPI, no analysis module
dependency.

Covers the contracts:
  * AircraftSpec / WingSpec / WingBoxSpec optional-with-bounds
  * StructuralReport flat-shape (one level deep, JSON-friendly)
  * DesignRequirements gains aircraft_spec / wing_spec / wing_box_spec
  * GenerateRequest gains run_struct (bool, defaults False)

Run directly:
    cd T2CIII/backend
    python tests/test_struct_schemas.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# main.py asserts GEMINI_API_KEY_HEAVY at import time.  Provide a dummy so
# the GenerateRequest import succeeds in CI / bare-stdlib runs.  No LLM call
# is made by the schema or run_struct paths under test.
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-dummy-key-not-used")

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from schemas import (  # noqa: E402
    AircraftSpec,
    DesignRequirements,
    StructuralReport,
    WingBoxSpec,
    WingSpec,
)


# ---------------------------------------------------------------------------
# AircraftSpec / WingSpec / WingBoxSpec — bounds + defaults
# ---------------------------------------------------------------------------

class TestAircraftSpec(unittest.TestCase):
    def test_all_optional_default_none(self):
        ac = AircraftSpec()
        self.assertIsNone(ac.mtow_kg)
        self.assertIsNone(ac.cruise_alt_m)
        self.assertIsNone(ac.cruise_speed_ms)

    def test_valid_normal_aircraft(self):
        ac = AircraftSpec(
            mtow_kg=1200.0, cruise_alt_m=3000.0, cruise_speed_ms=60.0,
            category="normal",
        )
        self.assertEqual(ac.mtow_kg, 1200.0)
        self.assertEqual(ac.category, "normal")

    def test_mtow_must_be_positive(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            AircraftSpec(mtow_kg=0.0)
        with self.assertRaises(ValidationError):
            AircraftSpec(mtow_kg=-50.0)

    def test_altitude_bounded(self):
        from pydantic import ValidationError
        # 0 is allowed (ge=0); negative + above-20km are rejected.
        AircraftSpec(cruise_alt_m=0.0)
        with self.assertRaises(ValidationError):
            AircraftSpec(cruise_alt_m=-1.0)
        with self.assertRaises(ValidationError):
            AircraftSpec(cruise_alt_m=25000.0)

    def test_vne_factor_must_exceed_one(self):
        from pydantic import ValidationError
        AircraftSpec(vne_factor=1.4)
        with self.assertRaises(ValidationError):
            AircraftSpec(vne_factor=1.0)        # gt=1
        with self.assertRaises(ValidationError):
            AircraftSpec(vne_factor=3.5)        # lt=3

    def test_category_max_length(self):
        from pydantic import ValidationError
        AircraftSpec(category="acrobatic")
        with self.assertRaises(ValidationError):
            AircraftSpec(category="x" * 50)


class TestWingSpec(unittest.TestCase):
    def test_optional(self):
        self.assertIsNone(WingSpec().area_m2)

    def test_positive(self):
        from pydantic import ValidationError
        WingSpec(area_m2=14.39, span_m=10.7)
        with self.assertRaises(ValidationError):
            WingSpec(area_m2=0.0)
        with self.assertRaises(ValidationError):
            WingSpec(span_m=600.0)


class TestWingBoxSpec(unittest.TestCase):
    def test_full_load(self):
        wb = WingBoxSpec(
            chord_root_m=1.7881, thickness_to_chord=0.12,
            cap_area_m2=0.000497, spar_web_thickness_m=0.00080, n_spars=2,
        )
        self.assertEqual(wb.n_spars, 2)

    def test_thickness_to_chord_below_one(self):
        from pydantic import ValidationError
        WingBoxSpec(thickness_to_chord=0.99)
        with self.assertRaises(ValidationError):
            WingBoxSpec(thickness_to_chord=1.0)

    def test_n_spars_positive(self):
        from pydantic import ValidationError
        WingBoxSpec(n_spars=1)
        with self.assertRaises(ValidationError):
            WingBoxSpec(n_spars=0)
        with self.assertRaises(ValidationError):
            WingBoxSpec(n_spars=11)


# ---------------------------------------------------------------------------
# StructuralReport — flat shape (one level deep, JSON-friendly)
# ---------------------------------------------------------------------------

class TestStructuralReportShape(unittest.TestCase):
    REF = {
        "altitude_m": 3000.0,
        "density_kgm3": 0.9091,
        "dynamic_pressure_pa": 1636.0,
        "cl_cruise": 0.5,
        "wing_loading_pa": 817.8,
        "category": "normal",
        "n_maneuver_pos": 3.8,
        "n_maneuver_neg": -1.52,
        "n_gust_pos": 3.09,
        "n_gust_neg": -1.09,
        "n_limit_pos": 3.8,
        "n_limit_neg": -1.52,
        "n_ultimate_pos": 5.7,
        "n_ultimate_neg": -2.28,
        "v_stall_ms": 34.6,
        "v_corner_ms": 67.5,
        "v_ne_ms": 84.0,
        "root_bending_moment_nm": 76353.0,
        "root_shear_force_n": 33539.0,
        "bending_stress_pa": 7.16e8,
        "bending_allowable_pa": 4.83e8,
        "bending_margin_of_safety": -0.325,
        "bending_passed": False,
        "shear_stress_pa": 9.78e7,
        "shear_allowable_pa": 2.90e8,
        "shear_margin_of_safety": 1.969,
        "shear_passed": True,
        "material": "AL2024-T3",
        "passed": False,
        "warnings": [],
    }

    def test_constructs_with_all_fields(self):
        rep = StructuralReport(**self.REF)
        self.assertEqual(rep.material, "AL2024-T3")
        self.assertFalse(rep.bending_passed)
        self.assertTrue(rep.shear_passed)

    def test_model_dump_is_one_level_deep(self):
        rep = StructuralReport(**self.REF)
        dumped = rep.model_dump()
        # Every value must be a JSON primitive or list — no nested dicts.
        for k, v in dumped.items():
            self.assertNotIsInstance(v, dict, f"field {k!r} is nested")

    def test_warnings_default_can_be_empty(self):
        rep = StructuralReport(**self.REF)
        self.assertEqual(rep.warnings, [])


# ---------------------------------------------------------------------------
# DesignRequirements — gains the three structural specs
# ---------------------------------------------------------------------------

class TestDesignRequirementsIntegration(unittest.TestCase):
    def test_specs_default_none(self):
        dr = DesignRequirements(primary_function="placeholder")
        self.assertIsNone(dr.aircraft_spec)
        self.assertIsNone(dr.wing_spec)
        self.assertIsNone(dr.wing_box_spec)

    def test_full_structural_payload(self):
        dr = DesignRequirements(
            primary_function="NACA 2412 wing for a 1200 kg light aircraft",
            inferred_domain="D",
            material_class="AL2024-T3",
            aircraft_spec={
                "mtow_kg": 1200.0,
                "cruise_alt_m": 3000.0,
                "cruise_speed_ms": 60.0,
                "category": "normal",
                "cl_max": 1.5,
            },
            wing_spec={"area_m2": 14.39, "span_m": 10.7284},
            wing_box_spec={
                "chord_root_m": 1.7881,
                "thickness_to_chord": 0.12,
                "cap_area_m2": 0.000497,
                "spar_web_thickness_m": 0.00080,
                "n_spars": 2,
            },
        )
        self.assertEqual(dr.aircraft_spec.mtow_kg, 1200.0)
        self.assertEqual(dr.wing_spec.area_m2, 14.39)
        self.assertEqual(dr.wing_box_spec.n_spars, 2)


# ---------------------------------------------------------------------------
# GenerateRequest — gains run_struct
# ---------------------------------------------------------------------------

class TestGenerateRequestRunStruct(unittest.TestCase):
    def test_run_struct_defaults_false(self):
        # Import lazily — main.py pulls in FastAPI etc.
        from main import GenerateRequest  # noqa: PLC0415
        body = GenerateRequest(prompt="a wing")
        self.assertFalse(body.run_struct)
        self.assertFalse(body.run_aero)

    def test_run_struct_can_be_set(self):
        from main import GenerateRequest  # noqa: PLC0415
        body = GenerateRequest(prompt="a wing", run_struct=True)
        self.assertTrue(body.run_struct)

    def test_prompt_max_length_unchanged(self):
        from main import GenerateRequest  # noqa: PLC0415
        from pydantic import ValidationError
        # Still capped at 5000 characters.
        GenerateRequest(prompt="x" * 5000)
        with self.assertRaises(ValidationError):
            GenerateRequest(prompt="x" * 5001)


if __name__ == "__main__":
    unittest.main(verbosity=2)
