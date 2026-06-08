"""Schema reconcile pin (2026-06-08) — AircraftSpec / WingBoxSpec are the single
canonical merge of the structures stack and the aero stack.

This locks the reconcile decisions so a future edit cannot silently re-diverge
the two classes (which is exactly what produced the merge collision):

  * all fields Optional (each consumer sets only its own subset)
  * bounds are the INTERSECTION (tightest) of both branches
  * fields are the UNION of both branches (incl. structures' cap_area_m2)
  * WingBoxSpec._front_before_rear is preserved AND None-guarded
  * the wing_aero stage tolerates an all-None / cruise-less AircraftSpec

Run: python backend/tests/test_schema_reconcile.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))          # backend/ (schemas, wing_aero)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))   # repo root (analysis.*)

from pydantic import ValidationError  # noqa: E402

from schemas import AircraftSpec, WingBoxSpec  # noqa: E402


class TestCanonicalUnion(unittest.TestCase):
    def test_all_optional_construct_empty(self):
        # structures contract: bare construction must succeed with None fields.
        self.assertIsNone(AircraftSpec().mtow_kg)
        self.assertIsNone(WingBoxSpec().front_spar_x_frac)
        self.assertIsNone(WingBoxSpec().cap_area_m2)

    def test_union_has_both_branches_fields(self):
        ac = set(AircraftSpec.model_fields)
        # aero-origin + structures-origin AircraftSpec fields both present
        self.assertTrue({"mtow_kg", "cruise_alt_m", "cruise_speed_ms", "cl_max"} <= ac)
        self.assertTrue({"lift_curve_slope", "gust_velocity_ms", "vne_factor"} <= ac)
        wb = set(WingBoxSpec.model_fields)
        # structures (analysis) fields — cap_area_m2 was dropped in the first
        # draft of the canonical class; this is the regression guard.
        self.assertTrue({"chord_root_m", "thickness_to_chord", "cap_area_m2",
                         "spar_web_thickness_m", "n_spars"} <= wb)
        # aero (geometry) fields
        self.assertTrue({"front_spar_x_frac", "rear_spar_x_frac", "n_ribs",
                         "web_thickness_m", "skin_thickness_m"} <= wb)


class TestIntersectionBounds(unittest.TestCase):
    """Tightest bound of each branch — so BOTH suites' rejection tests hold."""

    def _rejects(self, **kw):
        with self.assertRaises(ValidationError):
            AircraftSpec(**kw)

    def test_cruise_alt_uses_structures_tighter_bound(self):
        AircraftSpec(cruise_alt_m=20000.0)        # at the intersection ceiling
        self._rejects(cruise_alt_m=25000.0)       # aero's 30000 would have allowed this

    def test_cruise_speed_uses_aero_tighter_bound(self):
        AircraftSpec(cruise_speed_ms=400.0)       # at the intersection ceiling
        self._rejects(cruise_speed_ms=900.0)      # structures' 1000 would have allowed this

    def test_mtow_uses_aero_tighter_bound(self):
        AircraftSpec(mtow_kg=1.0e6)               # at the intersection ceiling
        self._rejects(mtow_kg=1.0e9)


class TestFrontBeforeRearValidator(unittest.TestCase):
    def test_fires_when_both_present(self):
        with self.assertRaises(ValidationError):
            WingBoxSpec(front_spar_x_frac=0.7, rear_spar_x_frac=0.3, n_ribs=7)

    def test_valid_layout_ok(self):
        wb = WingBoxSpec(front_spar_x_frac=0.25, rear_spar_x_frac=0.65, n_ribs=7)
        self.assertEqual(wb.n_ribs, 7)

    def test_none_guard_skips(self):
        # structures-style construction omits spar fractions -> must NOT raise.
        WingBoxSpec(chord_root_m=1.7, thickness_to_chord=0.12, cap_area_m2=0.0005)
        WingBoxSpec(front_spar_x_frac=0.25)       # only one set -> guard skips


class TestWingAeroToleratesNoneSpec(unittest.TestCase):
    """The relaxed-to-Optional cruise fields must not crash the 3-D aero stage."""

    def _call(self, aircraft_spec):
        from wing_aero import compute_wing_3d_report
        return compute_wing_3d_report(
            span_m=10.0, root_chord_m=1.5, tip_chord_m=0.9, twist_tip_deg=-2.0,
            aircraft_spec=aircraft_spec,
            sectional_cl_alpha_per_rad=6.28, sectional_alpha0_deg=-2.0,
            aoa_root_deg=3.0,
        )

    def test_cruiseless_spec_returns_none(self):
        # AircraftSpec() has cruise_alt_m / cruise_speed_ms == None.
        self.assertIsNone(self._call(AircraftSpec()))

    def test_full_spec_returns_report(self):
        rpt = self._call(AircraftSpec(mtow_kg=1200, cruise_alt_m=3000, cruise_speed_ms=60))
        self.assertIsNotNone(rpt)
        self.assertIn(rpt.source, ("lifting_line", "avl"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
