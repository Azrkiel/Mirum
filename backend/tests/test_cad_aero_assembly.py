"""Track A / A3-A4 — cross-builder assembly + OML reference artifact.

A3 asserts INTERFACE CONTACT via real boolean-intersection volume (not just
per-part validity + total mass — the old cross-check passed even when parts
floated apart; that was Hole #2). A4 mints the new OML reference artifact that
replaces the retired 67.6 kg box-beam number (Hole #1, #9).

Cadquery-gated. Run: python backend/tests/test_cad_aero_assembly.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import cadquery as cq  # noqa: F401

    _CQ_AVAILABLE = True
except ModuleNotFoundError:
    _CQ_AVAILABLE = False

_SKIP_MSG = "cadquery not importable in this environment."
_REF_PATH = Path(__file__).resolve().parents[1].parent / "output" / "wing_assembly_oml_reference.json"


def _intersect_vol(a, b) -> float:
    try:
        inter = a.val().intersect(b.val())
        return inter.Volume() if inter is not None else 0.0
    except Exception:  # noqa: BLE001 — disjoint solids can raise; treat as no contact
        return 0.0


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestCanonicalAssembly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cad_aero

        cls.parts = cad_aero.canonical_wing_parts()

    def test_fixed_part_count(self):
        import cad_aero

        self.assertEqual(len(self.parts), cad_aero.EXPECTED_PART_COUNT)

    def test_every_part_valid(self):
        for name, wp in self.parts.items():
            shape = wp.val()
            self.assertTrue(
                shape is not None and shape.isValid() and shape.Volume() > 0,
                f"{name} invalid",
            )

    def test_ribs_contact_both_spars(self):
        # Hole #2: each rib must actually intersect both spar webs.
        fs, rs = self.parts["front_spar"], self.parts["rear_spar"]
        ribs = [n for n in self.parts if n.startswith("rib_")]
        self.assertGreaterEqual(len(ribs), 5)
        for n in ribs:
            self.assertGreater(_intersect_vol(self.parts[n], fs), 0.0, f"{n}/front_spar gap")
            self.assertGreater(_intersect_vol(self.parts[n], rs), 0.0, f"{n}/rear_spar gap")

    def test_no_floating_parts(self):
        # Every part must intersect at least one other part — no disconnected piece.
        names = list(self.parts)
        for n in names:
            touches = any(
                m != n and _intersect_vol(self.parts[n], self.parts[m]) > 0.0
                for m in names
            )
            self.assertTrue(touches, f"{n} floats — touches nothing")


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestReferenceArtifact(unittest.TestCase):
    def test_reference_exists_and_is_self_consistent(self):
        # A4: the committed reference is the regression anchor; a fresh build must
        # reproduce its part count and total mass within epsilon (determinism).
        import cad_aero

        self.assertTrue(_REF_PATH.exists(), f"missing reference {_REF_PATH}")
        ref = json.loads(_REF_PATH.read_text())
        parts = cad_aero.canonical_wing_parts()
        self.assertEqual(ref["part_count"], len(parts))
        mass = cad_aero.wing_mass_kg(parts)
        self.assertAlmostEqual(mass, ref["total_mass_kg"], delta=0.02 * ref["total_mass_kg"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
