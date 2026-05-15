"""Phase 3 — XFOIL pipeline integration tests.

These tests cover the surfaces that wire the standalone analysis/ package
into the /generate response: schemas (PolarReport, AirfoilSpec,
FlightEnvelopeSpec, manifest/requirements field additions), the
GenerateRequest opt-in flag, and the _run_aero_stage skip-reason
contracts.

Live XFOIL tests live in analysis/tests/test_airfoil.py — this file is
pure Python, no XFOIL binary required, safe to run in CI.

Run:
    python backend/tests/test_aero_integration.py
or via pytest once installed:
    pytest backend/tests/test_aero_integration.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Make sure backend/ is on sys.path so `import schemas` / `import main` work
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

# Required by backend/main.py at import time. The stage tests never call
# Gemini, so a placeholder is fine. Set BEFORE importing main.
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-placeholder-not-used")

from schemas import (  # noqa: E402
    AirfoilSpec,
    AssemblyManifest,
    DesignRequirements,
    FlightEnvelopeSpec,
    PartDefinition,
    PolarReport,
)


# ---------------------------------------------------------------------------
# Group A — Schema round-trips
# ---------------------------------------------------------------------------

class TestPhase3Schemas(unittest.TestCase):
    """Pydantic models defined for Phase 3 round-trip cleanly."""

    def test_polar_report_round_trip(self) -> None:
        original = PolarReport(
            naca_code="0012",
            chord_mm=200.0,
            reynolds=1_000_000.0,
            mach=0.0,
            n_crit=9.0,
            alpha=[0.0, 5.0, 10.0],
            Cl=[0.0, 0.57, 1.08],
            Cd=[0.006, 0.008, 0.015],
            Cm=[0.0, -0.001, 0.005],
            L_D=[0.0, 71.0, 72.0],
            converged_count=3,
            stall_alpha=14.0,
            Cl_max=1.39,
        )
        dumped = original.model_dump()
        restored = PolarReport.model_validate(dumped)
        self.assertEqual(restored.naca_code, "0012")
        self.assertEqual(len(restored.alpha), 3)
        self.assertAlmostEqual(restored.Cl_max, 1.39, places=3)
        self.assertEqual(restored.converged_count, 3)

    def test_polar_report_optional_fields_default_none(self) -> None:
        polar = PolarReport(
            naca_code="2412", chord_mm=100.0, reynolds=5e5, mach=0.0, n_crit=9.0,
            alpha=[], Cl=[], Cd=[], Cm=[], L_D=[], converged_count=0,
        )
        self.assertIsNone(polar.stall_alpha)
        self.assertIsNone(polar.Cl_max)

    def test_airfoil_spec_all_fields_optional(self) -> None:
        spec = AirfoilSpec()
        self.assertIsNone(spec.naca_code)
        self.assertIsNone(spec.chord_mm)
        self.assertIsNone(spec.span_mm)
        # Round-trip when populated
        spec2 = AirfoilSpec(naca_code="2412", chord_mm=200.0, span_mm=1000.0)
        dumped = spec2.model_dump()
        restored = AirfoilSpec.model_validate(dumped)
        self.assertEqual(restored.naca_code, "2412")

    def test_flight_envelope_spec_all_fields_optional(self) -> None:
        env = FlightEnvelopeSpec()
        for field in ("alpha_start", "alpha_end", "alpha_step", "reynolds", "mach", "n_crit"):
            self.assertIsNone(getattr(env, field), f"{field} should default to None")

    def test_assembly_manifest_aero_report_default_none(self) -> None:
        manifest = AssemblyManifest(
            assembly_name="dummy",
            parts=[PartDefinition(
                part_id="p1",
                description="dummy part",
                anchor_tags=["face_a"],
            )],
            mating_rules=[],
        )
        self.assertIsNone(manifest.aero_report)

    def test_assembly_manifest_carries_aero_report(self) -> None:
        polar = PolarReport(
            naca_code="0012", chord_mm=200.0, reynolds=1e6, mach=0.0, n_crit=9.0,
            alpha=[0.0, 5.0], Cl=[0.0, 0.5], Cd=[0.006, 0.008],
            Cm=[0.0, 0.0], L_D=[0.0, 62.5], converged_count=2,
            stall_alpha=None, Cl_max=0.5,
        )
        manifest = AssemblyManifest(
            assembly_name="wing_demo",
            parts=[PartDefinition(part_id="oml", description="wing skin", anchor_tags=["root"])],
            mating_rules=[],
            aero_report=polar,
        )
        # Round-trip
        dumped = manifest.model_dump()
        restored = AssemblyManifest.model_validate(dumped)
        self.assertIsNotNone(restored.aero_report)
        self.assertEqual(restored.aero_report.naca_code, "0012")

    def test_design_requirements_default_aero_fields_none(self) -> None:
        reqs = DesignRequirements(
            primary_function="A wing",
            connecting_interfaces=[],
            confidence=0.8,
        )
        self.assertIsNone(reqs.flight_env)
        self.assertIsNone(reqs.airfoil_spec)

    def test_design_requirements_carries_aero_fields(self) -> None:
        reqs = DesignRequirements(
            primary_function="NACA 2412 wing, 500mm span, 200mm chord, cruise at Re=1.5e6",
            connecting_interfaces=[],
            confidence=0.9,
            inferred_domain="D",
            flight_env=FlightEnvelopeSpec(reynolds=1.5e6, mach=0.2, alpha_end=18.0),
            airfoil_spec=AirfoilSpec(naca_code="2412", chord_mm=200.0, span_mm=500.0),
        )
        dumped = reqs.model_dump()
        restored = DesignRequirements.model_validate(dumped)
        self.assertEqual(restored.inferred_domain, "D")
        self.assertEqual(restored.airfoil_spec.naca_code, "2412")
        self.assertAlmostEqual(restored.flight_env.reynolds, 1.5e6, places=0)


# ---------------------------------------------------------------------------
# Group B — GenerateRequest opt-in flag
# ---------------------------------------------------------------------------

class TestGenerateRequestRunAero(unittest.TestCase):
    """The opt-in flag is wired through Pydantic with the right default."""

    def test_run_aero_defaults_false(self) -> None:
        from main import GenerateRequest
        req = GenerateRequest(prompt="dummy")
        self.assertFalse(req.run_aero)

    def test_run_aero_accepts_true(self) -> None:
        from main import GenerateRequest
        req = GenerateRequest(prompt="dummy", run_aero=True)
        self.assertTrue(req.run_aero)


# ---------------------------------------------------------------------------
# Group C — _run_aero_stage skip-reason contracts
# ---------------------------------------------------------------------------

class TestRunAeroStageSkipReasons(unittest.IsolatedAsyncioTestCase):
    """Every failure path returns (None, reason_string) — never raises.

    The function's central contract is that aero failure must NEVER block
    GLB delivery. These tests pin the contract for each gate.
    """

    async def test_skip_when_requirements_none(self) -> None:
        from main import _run_aero_stage
        report, msg = await _run_aero_stage(step_files={}, requirements=None)
        self.assertIsNone(report)
        self.assertIn("no rea requirements", msg.lower())

    async def test_skip_when_not_domain_d(self) -> None:
        from main import _run_aero_stage
        reqs = DesignRequirements(
            primary_function="A bracket",
            connecting_interfaces=[],
            confidence=0.9,
            inferred_domain="A",
        )
        report, msg = await _run_aero_stage(step_files={}, requirements=reqs)
        self.assertIsNone(report)
        self.assertIn("not domain d", msg.lower())

    async def test_skip_when_no_airfoil_spec(self) -> None:
        from main import _run_aero_stage
        reqs = DesignRequirements(
            primary_function="A wing",
            connecting_interfaces=[],
            confidence=0.9,
            inferred_domain="D",
            # airfoil_spec missing entirely
        )
        report, msg = await _run_aero_stage(step_files={"oml": "/fake/path.step"}, requirements=reqs)
        self.assertIsNone(report)
        self.assertIn("airfoil_spec", msg.lower())

    async def test_skip_when_airfoil_spec_incomplete(self) -> None:
        from main import _run_aero_stage
        reqs = DesignRequirements(
            primary_function="A wing",
            connecting_interfaces=[],
            confidence=0.9,
            inferred_domain="D",
            airfoil_spec=AirfoilSpec(naca_code="2412"),  # missing chord_mm/span_mm
        )
        report, msg = await _run_aero_stage(step_files={"oml": "/fake/path.step"}, requirements=reqs)
        self.assertIsNone(report)
        self.assertIn("airfoil_spec", msg.lower())

    async def test_skip_when_no_step_files(self) -> None:
        from main import _run_aero_stage
        reqs = DesignRequirements(
            primary_function="A wing",
            connecting_interfaces=[],
            confidence=0.9,
            inferred_domain="D",
            airfoil_spec=AirfoilSpec(naca_code="2412", chord_mm=200.0, span_mm=500.0),
        )
        report, msg = await _run_aero_stage(step_files={}, requirements=reqs)
        self.assertIsNone(report)
        self.assertIn("no step file", msg.lower())

    async def test_skip_on_xfoil_failure_never_raises(self) -> None:
        """When everything passes the gates but the STEP file doesn't exist,
        analyze_wing_step will raise — _run_aero_stage must catch and return
        (None, reason), not propagate."""
        from main import _run_aero_stage
        reqs = DesignRequirements(
            primary_function="A wing",
            connecting_interfaces=[],
            confidence=0.9,
            inferred_domain="D",
            airfoil_spec=AirfoilSpec(naca_code="0012", chord_mm=200.0, span_mm=500.0),
        )
        # Bogus path — analyze_wing_step will fail (no STEP, but it has a
        # NACA fallback so it'll then need XFOIL; either way returns gracefully)
        report, msg = await _run_aero_stage(
            step_files={"oml": "/definitely/does/not/exist.step"},
            requirements=reqs,
        )
        # Acceptable outcomes:
        #   - skipped with a reason (XFOIL absent in CI, or slice failed)
        #   - or success if a local XFOIL + analytical fallback ran (still None on chord/span mismatch)
        # The hard guarantee: no exception escaped.
        if report is None:
            self.assertTrue(len(msg) > 0, "skip reason should be non-empty")
        else:
            self.assertEqual(report["naca_code"], "0012")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Phase 3 — aero integration test suite")
    print("=" * 60)
    unittest.main(verbosity=2)
