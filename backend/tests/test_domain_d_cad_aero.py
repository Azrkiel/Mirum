"""Pipeline wiring W2 — Machinist uses cad_aero builders + sandbox reachability.

Two checks:
  1. The Domain-D strategy prompt instructs the Machinist to parameterise the
     cad_aero structural builders (so /generate produces deterministic wing
     geometry instead of free-form B-rep). [TDD red-first]
  2. A representative cad_aero wing script actually executes through the REAL
     compiler sandbox (proves the allowlist + PYTHONPATH wiring reaches
     cad_aero). [integration regression]

Run: python backend/tests/test_domain_d_cad_aero.py
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import strategies  # noqa: E402

try:
    import cadquery  # noqa: F401

    _CQ = True
except ModuleNotFoundError:
    _CQ = False

_BUILDERS = [
    "make_spar", "make_rib", "make_wing_skin_panel",
    "make_stringer", "make_root_fitting",
]


class TestDomainDStrategyMentionsBuilders(unittest.TestCase):
    def test_strategy_teaches_cad_aero_builders(self):
        prompt = strategies.get_strategy(
            "D", "NACA 2412 wing with front and rear spars, 7 ribs, skins, stringers"
        )
        self.assertIn("cad_aero", prompt)
        for b in _BUILDERS:
            self.assertIn(b, prompt, f"Domain D strategy never mentions {b}")

    def test_other_domains_unaffected(self):
        # cad_aero guidance must be Domain-D only, not leaked into A.
        self.assertNotIn("cad_aero", strategies.get_strategy("A", "an L-bracket"))


@unittest.skipUnless(_CQ, "cadquery not importable")
class TestCadAeroRunsInSandbox(unittest.TestCase):
    def test_builder_script_executes_through_compiler(self):
        import compiler

        script = (
            "import cadquery as cq\n"
            "from cad_aero import naca4_points, make_rib\n"
            "af = naca4_points('2412', 1.0)\n"
            "result = make_rib(airfoil_points=af, chord_m=1.0, y_station_m=0.0,\n"
            "                  thickness_m=0.003, front_spar_x_frac=0.25,\n"
            "                  rear_spar_x_frac=0.65, n_lightening_holes=3)\n"
            "print('RIB_VOLUME', result.val().Volume())\n"
        )
        res = asyncio.run(compiler.execute_cad_script(script))
        self.assertEqual(res.get("status"), "success", res.get("traceback"))
        self.assertIn("RIB_VOLUME", res.get("output", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
