"""Pipeline wiring W5 — 3-D aero stage bridge (lifting-line -> Wing3DAeroReport).

Pure compute (no LLM, no Gemini key, no binary): given wing planform + aircraft
conditions + sectional lift-curve data, produce the whole-wing 3-D aero report
that the SSE 'complete' event carries as wing_aero_report. ISA density is derived
from cruise altitude; the spanwise chord/twist distributions are built from the
planform; the lifting-line baseline is always available.

Run: python backend/tests/test_wing_aero_stage.py
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# analysis/ lives one level up from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import wing_aero  # noqa: E402
from schemas import AircraftSpec, Wing3DAeroReport  # noqa: E402


class TestIsaDensity(unittest.TestCase):
    def test_sea_level(self):
        self.assertAlmostEqual(wing_aero.isa_density(0.0), 1.225, delta=0.001)

    def test_3000m(self):
        self.assertAlmostEqual(wing_aero.isa_density(3000.0), 0.909, delta=0.01)


class TestComputeWing3DReport(unittest.TestCase):
    def _spec(self):
        return AircraftSpec(mtow_kg=1200.0, cruise_alt_m=3000.0, cruise_speed_ms=60.0)

    def test_returns_valid_report(self):
        rep = wing_aero.compute_wing_3d_report(
            span_m=10.73, root_chord_m=1.7881, tip_chord_m=0.8940,
            twist_tip_deg=-3.0, aircraft_spec=self._spec(),
            sectional_cl_alpha_per_rad=2 * math.pi, sectional_alpha0_deg=-2.0,
            aoa_root_deg=4.0,
        )
        self.assertIsInstance(rep, Wing3DAeroReport)
        self.assertEqual(rep.source, "lifting_line")
        self.assertGreater(rep.CL_3D, 0.0)
        self.assertGreaterEqual(rep.CDi, 0.0)
        self.assertTrue(0.0 < rep.span_efficiency_e <= 1.05)

    def test_higher_aoa_more_lift(self):
        # Bridge-level: aoa_root must be wired through — more AoA, more CL_3D.
        # (The CDi = CL^2/(pi e AR) identity is lifting_line's internal invariant,
        # covered by the Track B suite; not re-tested here.)
        kw = dict(
            span_m=10.73, root_chord_m=1.7881, tip_chord_m=0.8940,
            twist_tip_deg=0.0, aircraft_spec=self._spec(),
            sectional_cl_alpha_per_rad=2 * math.pi, sectional_alpha0_deg=0.0,
        )
        lo = wing_aero.compute_wing_3d_report(aoa_root_deg=2.0, **kw)
        hi = wing_aero.compute_wing_3d_report(aoa_root_deg=6.0, **kw)
        self.assertGreater(hi.CL_3D, lo.CL_3D)

    def test_degenerate_returns_none(self):
        rep = wing_aero.compute_wing_3d_report(
            span_m=0.0, root_chord_m=1.0, tip_chord_m=1.0, twist_tip_deg=0.0,
            aircraft_spec=self._spec(), sectional_cl_alpha_per_rad=2 * math.pi,
            sectional_alpha0_deg=0.0, aoa_root_deg=4.0,
        )
        self.assertIsNone(rep)


if __name__ == "__main__":
    unittest.main(verbosity=2)
