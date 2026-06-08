"""Track A / A1 — trailing-edge cusp recipe (CRITICAL path).

The known failure mode: ``cq.Workplane.polyline(airfoil_points).close().extrude(t)``
produces an invalid B-rep on NACA 4-digit profiles at the trailing-edge cusp.
A1 implements three candidate ``te_method`` strategies and the EMPIRICAL matrix
(3 NACA codes x 3 extrude thicknesses) selects the winner. Acceptance: the
default method yields a BRepCheck-clean, positive-volume solid across all 9 cells.

Cadquery-gated (runs locally — cadquery 2.4.0 is the real dependency).

Run: python backend/tests/test_te_cusp.py
"""

from __future__ import annotations

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

NACA_CODES = ["0012", "2412", "4415"]
EXTRUDE_THICKNESSES_M = [0.003, 0.012, 0.030]  # the `t` in extrude(t) — the cusp trigger
CHORD_M = 1.0


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestNacaPoints(unittest.TestCase):
    def test_points_closed_contour_in_xz(self):
        import cad_aero

        pts = cad_aero.naca4_points("2412", chord_m=CHORD_M)
        self.assertGreater(len(pts), 50)
        # x spans ~[0, chord]; z straddles zero (camber + thickness)
        xs = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        self.assertAlmostEqual(min(xs), 0.0, delta=0.02 * CHORD_M)
        self.assertAlmostEqual(max(xs), CHORD_M, delta=0.02 * CHORD_M)
        self.assertLess(min(zs), 0.0)
        self.assertGreater(max(zs), 0.0)


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestTeCuspMatrix(unittest.TestCase):
    """The empirical 3x3 matrix for the DEFAULT te_method must be 9/9 valid."""

    def test_default_method_valid_across_full_matrix(self):
        import cad_aero

        failures = []
        for code in NACA_CODES:
            pts = cad_aero.naca4_points(code, chord_m=CHORD_M)
            for t in EXTRUDE_THICKNESSES_M:
                try:
                    wp = cad_aero.extrude_airfoil_section(
                        pts, t, te_method=cad_aero._DEFAULT_TE_METHOD
                    )
                    shape = wp.val()
                    if shape is None or not shape.isValid() or shape.Volume() <= 0:
                        failures.append((code, t, "invalid/zero-vol"))
                except Exception as exc:  # noqa: BLE001
                    failures.append((code, t, repr(exc)))
        self.assertEqual(
            failures, [], f"default te_method '{cad_aero._DEFAULT_TE_METHOD}' failed cells: {failures}"
        )

    def test_default_is_the_empirical_winner(self):
        """The chosen default must score >= every other candidate on the matrix."""
        import cad_aero

        def pass_count(method: str) -> int:
            n = 0
            for code in NACA_CODES:
                pts = cad_aero.naca4_points(code, chord_m=CHORD_M)
                for t in EXTRUDE_THICKNESSES_M:
                    try:
                        wp = cad_aero.extrude_airfoil_section(pts, t, te_method=method)
                        shape = wp.val()
                        if shape is not None and shape.isValid() and shape.Volume() > 0:
                            n += 1
                    except Exception:  # noqa: BLE001
                        pass
            return n

        scores = {m: pass_count(m) for m in cad_aero.TE_METHODS}
        best = max(scores.values())
        self.assertEqual(
            scores[cad_aero._DEFAULT_TE_METHOD],
            best,
            f"default '{cad_aero._DEFAULT_TE_METHOD}' is not the winner; scores={scores}",
        )
        # The winner must clear the whole matrix.
        self.assertEqual(best, 9, f"no candidate cleared all 9 cells; scores={scores}")


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestTeMethodApi(unittest.TestCase):
    def test_unknown_method_raises(self):
        import cad_aero

        pts = cad_aero.naca4_points("0012", chord_m=CHORD_M)
        with self.assertRaises(cad_aero.CadAeroError):
            cad_aero.extrude_airfoil_section(pts, 0.01, te_method="banana")

    def test_losing_methods_kept_as_escape_hatches(self):
        # Per plan: winner is default, losers remain callable parameter values.
        import cad_aero

        for m in ("trim", "loft", "spline"):
            self.assertIn(m, cad_aero.TE_METHODS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
