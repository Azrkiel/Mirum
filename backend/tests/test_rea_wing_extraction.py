"""Phase 4 — REA extracts the structural / flight-envelope specs (Domain D).

Live test: drives `run_rea` against Gemini and asserts a wing-assembly brief
yields a DesignRequirements whose aircraft_spec / wing_spec / wing_box_spec
SECTION-SIZING fields are populated (so the opt-in run_struct / run_aero stages
stop silently skipping on real prompts). Skips cleanly without a key.

Run: python backend/tests/test_rea_wing_extraction.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))          # backend
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))   # repo root

_HAS_KEY = bool(os.environ.get("GEMINI_API_KEY_HEAVY"))


@unittest.skipUnless(_HAS_KEY, "needs GEMINI_API_KEY_HEAVY (live REA call)")
class TestREAWingExtraction(unittest.TestCase):
    def test_extracts_aircraft_wing_and_box_specs(self):
        # Mirror main.py:78 / runner.py:327 — configure genai (PRO falls back to HEAVY).
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY_PRO")
                        or os.environ["GEMINI_API_KEY_HEAVY"])
        from agents import run_rea

        prompt = (
            "Design a NACA 2412 wing for a 1200 kg utility-category light aircraft "
            "cruising at 60 m/s at 3000 m altitude. Reference wing area 16 m^2, "
            "span 10.7 m. Wing box: 1.8 m root chord, 12% thickness-to-chord, 2 spars."
        )
        req = asyncio.run(run_rea(prompt))
        self.assertIsNotNone(req, "run_rea returned None")

        ac = req.aircraft_spec
        self.assertIsNotNone(ac, "aircraft_spec not extracted")
        self.assertAlmostEqual(ac.mtow_kg, 1200.0, delta=1.0)
        self.assertAlmostEqual(ac.cruise_alt_m, 3000.0, delta=1.0)
        self.assertAlmostEqual(ac.cruise_speed_ms, 60.0, delta=1.0)

        ws = req.wing_spec
        self.assertIsNotNone(ws, "wing_spec not extracted")
        self.assertAlmostEqual(ws.area_m2, 16.0, delta=0.5)
        self.assertAlmostEqual(ws.span_m, 10.7, delta=0.3)

        wb = req.wing_box_spec
        self.assertIsNotNone(wb, "wing_box_spec not extracted")
        self.assertAlmostEqual(wb.chord_root_m, 1.8, delta=0.15)
        self.assertAlmostEqual(wb.thickness_to_chord, 0.12, delta=0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
