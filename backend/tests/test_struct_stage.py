"""
backend/tests/test_struct_stage.py
====================================

End-to-end tests for ``main._run_struct_stage()``: gate logic and the
golden-path success.

The structural stage is pure-number (no subprocess, no STEP file, no
LLM call), so these tests run on real DesignRequirements / analysis
code without any mocking.  They exercise the full _run_struct_stage →
analyze_wing_structure → StructuralReport path.

Run directly:
    cd T2CIII/backend
    python tests/test_struct_stage.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

# main.py asserts GEMINI_API_KEY_HEAVY at import time.  Provide a dummy so
# the import succeeds in CI / bare-stdlib runs.  No LLM call is made by the
# structural stage path under test.
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-dummy-key-not-used")

# Path setup: backend/ on sys.path; repo root on sys.path so `analysis.*` resolves.
_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_ROOT))

from schemas import DesignRequirements  # noqa: E402


def _full_d_requirements() -> DesignRequirements:
    """Reference NACA 2412 GA wing — every structural field populated."""
    return DesignRequirements(
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


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Skip gates  (each must return (None, reason) — never raise)
# ---------------------------------------------------------------------------

class TestSkipGates(unittest.TestCase):
    def test_no_requirements_skips(self):
        from main import _run_struct_stage  # noqa: PLC0415
        report, msg = _run(_run_struct_stage(None))
        self.assertIsNone(report)
        self.assertIn("no REA requirements", msg)

    def test_non_domain_d_skips(self):
        from main import _run_struct_stage  # noqa: PLC0415
        req = DesignRequirements(primary_function="a box", inferred_domain="A")
        report, msg = _run(_run_struct_stage(req))
        self.assertIsNone(report)
        self.assertIn("not Domain D", msg)

    def test_missing_aircraft_spec_skips(self):
        from main import _run_struct_stage  # noqa: PLC0415
        req = DesignRequirements(primary_function="a wing", inferred_domain="D")
        report, msg = _run(_run_struct_stage(req))
        self.assertIsNone(report)
        self.assertIn("aircraft_spec", msg)

    def test_missing_wing_area_skips(self):
        from main import _run_struct_stage  # noqa: PLC0415
        req = DesignRequirements(
            primary_function="a wing",
            inferred_domain="D",
            aircraft_spec={"mtow_kg": 1200.0, "cruise_alt_m": 3000.0, "cruise_speed_ms": 60.0},
            wing_box_spec={
                "chord_root_m": 1.7881, "thickness_to_chord": 0.12,
                "cap_area_m2": 0.000497, "spar_web_thickness_m": 0.00080,
            },
        )
        report, msg = _run(_run_struct_stage(req))
        self.assertIsNone(report)
        self.assertIn("wing area unknown", msg)

    def test_missing_wing_box_skips(self):
        from main import _run_struct_stage  # noqa: PLC0415
        req = DesignRequirements(
            primary_function="a wing",
            inferred_domain="D",
            aircraft_spec={"mtow_kg": 1200.0, "cruise_alt_m": 3000.0, "cruise_speed_ms": 60.0},
            wing_spec={"area_m2": 14.39, "span_m": 10.7284},
        )
        report, msg = _run(_run_struct_stage(req))
        self.assertIsNone(report)
        self.assertIn("wing_box_spec", msg)

    def test_area_derived_from_airfoil_spec(self):
        """When wing_spec is absent, chord_mm × span_mm is used as a fallback."""
        from main import _run_struct_stage  # noqa: PLC0415
        req = DesignRequirements(
            primary_function="a NACA 2412 wing",
            inferred_domain="D",
            aircraft_spec={"mtow_kg": 1200.0, "cruise_alt_m": 3000.0, "cruise_speed_ms": 60.0},
            airfoil_spec={"naca_code": "2412", "chord_mm": 1341.0, "span_mm": 10728.4},
            wing_box_spec={
                "chord_root_m": 1.7881, "thickness_to_chord": 0.12,
                "cap_area_m2": 0.000497, "spar_web_thickness_m": 0.00080,
            },
        )
        report, msg = _run(_run_struct_stage(req))
        self.assertIsNotNone(report, f"expected a struct report, got skip: {msg}")
        # 1.341 m × 10.7284 m ≈ 14.39 m² → CL_cruise ≈ 0.499
        self.assertAlmostEqual(report["cl_cruise"], 0.499, delta=0.01)


# ---------------------------------------------------------------------------
# Golden-path success  (full reference wing)
# ---------------------------------------------------------------------------

class TestSuccessPath(unittest.TestCase):
    def test_full_pipeline_returns_flat_dict(self):
        from main import _run_struct_stage  # noqa: PLC0415
        req = _full_d_requirements()
        report, msg = _run(_run_struct_stage(req))
        self.assertIsNotNone(report, f"expected a struct report, got skip: {msg}")

        # Anchor values cross-checked in test_structures.py:
        self.assertAlmostEqual(report["cl_cruise"], 0.499, delta=0.005)
        self.assertAlmostEqual(report["n_ultimate_pos"], 5.7, delta=0.08)
        self.assertAlmostEqual(report["density_kgm3"], 0.9091, delta=0.002)
        self.assertEqual(report["material"], "AL2024-T3")

        # Flat shape — every value must be a JSON primitive or list.
        for k, v in report.items():
            self.assertNotIsInstance(v, dict, f"field {k!r} unexpectedly nested")

        # Status message carries the key numbers.
        self.assertIn("n_ult=", msg)
        self.assertIn("CL_cruise=", msg)
        self.assertIn("bending MS=", msg)
        self.assertIn("shear MS=", msg)


# ---------------------------------------------------------------------------
# Failure surfaces never raise — bad-material smoke check
# ---------------------------------------------------------------------------

class TestNeverRaises(unittest.TestCase):
    def test_unknown_material_skips_not_raises(self):
        from main import _run_struct_stage  # noqa: PLC0415
        req = _full_d_requirements()
        req.material_class = "unobtanium-1"
        report, msg = _run(_run_struct_stage(req))
        self.assertIsNone(report)
        self.assertIn("unknown material", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
