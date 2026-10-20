"""Pipeline wiring W1 — wing/aircraft spec models + 3-D aero report.

These feed the deterministic cad_aero assembly (wing_box_spec) and the 3-D aero
stage (aircraft_spec → velocity/density; Wing3DAeroReport ← lifting_line/avl).
Pydantic bounds must REJECT hallucinated nonsense (the plan's REA risk): if the
REA invents an absurd MTOW or an inverted spar layout, validation fails rather
than silently feeding garbage downstream.

Run: python backend/tests/test_wing_specs.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from schemas import (  # noqa: E402
    AircraftSpec,
    AssemblyManifest,
    DesignRequirements,
    PartDefinition,
    Wing3DAeroReport,
    WingBoxSpec,
)


class TestAircraftSpec(unittest.TestCase):
    def test_valid(self):
        s = AircraftSpec(mtow_kg=1200.0, cruise_alt_m=3000.0, cruise_speed_ms=60.0)
        self.assertEqual(s.mtow_kg, 1200.0)

    def test_rejects_nonpositive_mtow(self):
        with self.assertRaises(ValidationError):
            AircraftSpec(mtow_kg=0.0, cruise_alt_m=3000.0, cruise_speed_ms=60.0)

    def test_rejects_absurd_values(self):
        with self.assertRaises(ValidationError):
            AircraftSpec(mtow_kg=1e9, cruise_alt_m=3000.0, cruise_speed_ms=60.0)
        with self.assertRaises(ValidationError):
            AircraftSpec(mtow_kg=1200.0, cruise_alt_m=99000.0, cruise_speed_ms=60.0)
        with self.assertRaises(ValidationError):
            AircraftSpec(mtow_kg=1200.0, cruise_alt_m=3000.0, cruise_speed_ms=900.0)


class TestWingBoxSpec(unittest.TestCase):
    def test_valid(self):
        s = WingBoxSpec(front_spar_x_frac=0.25, rear_spar_x_frac=0.65, n_ribs=7)
        self.assertEqual(s.n_ribs, 7)

    def test_rejects_inverted_spars(self):
        # front must be forward of rear.
        with self.assertRaises(ValidationError):
            WingBoxSpec(front_spar_x_frac=0.65, rear_spar_x_frac=0.25, n_ribs=7)

    def test_rejects_out_of_range_fracs_and_ribs(self):
        with self.assertRaises(ValidationError):
            WingBoxSpec(front_spar_x_frac=-0.1, rear_spar_x_frac=0.65, n_ribs=7)
        with self.assertRaises(ValidationError):
            WingBoxSpec(front_spar_x_frac=0.25, rear_spar_x_frac=1.4, n_ribs=7)
        with self.assertRaises(ValidationError):
            WingBoxSpec(front_spar_x_frac=0.25, rear_spar_x_frac=0.65, n_ribs=1)


class TestWing3DAeroReport(unittest.TestCase):
    def test_valid_lifting_line(self):
        r = Wing3DAeroReport(
            CL_3D=0.42, CDi=0.008, span_efficiency_e=0.94, source="lifting_line"
        )
        self.assertEqual(r.source, "lifting_line")

    def test_rejects_bad_source(self):
        with self.assertRaises(ValidationError):
            Wing3DAeroReport(CL_3D=0.42, CDi=0.008, span_efficiency_e=0.94, source="magic")

    def test_rejects_impossible_efficiency(self):
        with self.assertRaises(ValidationError):
            Wing3DAeroReport(CL_3D=0.42, CDi=0.008, span_efficiency_e=1.6, source="avl")


class TestDesignRequirementsWiring(unittest.TestCase):
    def test_optional_specs_default_none_and_accept_values(self):
        dr = DesignRequirements(primary_function="A tapered NACA 2412 wing.")
        self.assertIsNone(dr.aircraft_spec)
        self.assertIsNone(dr.wing_box_spec)
        dr2 = DesignRequirements(
            primary_function="A tapered NACA 2412 wing.",
            aircraft_spec=AircraftSpec(mtow_kg=1200, cruise_alt_m=3000, cruise_speed_ms=60),
            wing_box_spec=WingBoxSpec(front_spar_x_frac=0.25, rear_spar_x_frac=0.65, n_ribs=7),
        )
        self.assertEqual(dr2.wing_box_spec.n_ribs, 7)


class TestManifestWing3DReport(unittest.TestCase):
    def test_wing_aero_report_optional(self):
        m = AssemblyManifest(assembly_name="wing", parts=[PartDefinition(
            part_id="wing", description="wing", anchor_tags=[">Z"])], mating_rules=[])
        self.assertIsNone(m.wing_aero_report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
