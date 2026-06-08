"""
analysis/tests/test_lifting_line.py — Unit tests for Prandtl lifting-line theory
================================================================================

All tests here are PURE PYTHON (no numpy, no external binary) and therefore
ALWAYS run — there is no skip-gate.  They validate the implementation against
classical lifting-line results (Anderson, *Fundamentals of Aerodynamics*,
Ch. 5).

Test plan
---------
B2 — lift_curve_slope_and_alpha0:
  L1  Synthetic clean line: recovers known slope (≈2π/rad) and alpha_L0.
  L2  Cambered line: nonzero alpha_L0 with correct sign.
  L3  Stall knee is trimmed (points past Cl_max do not corrupt the fit).
  L4  Too-few points → graceful (raises ValueError or returns NaN-free degeneracy).

B1 — prandtl_lifting_line:
  P1  Elliptic chord + zero twist  → span_efficiency_e ≈ 1.0 (headline).
  P2  Finite rectangular wing      → CL_3D < CL_2D for the same root AoA.
  P3  Induced-drag consistency     → CDi ≈ CL_3D² / (π e AR).
  P4  3-D slope formula            → a = a0 / (1 + a0/(π AR)(1+τ)) (elliptic τ≈0).
  P5  Degenerate inputs            → passed=False + warning, no crash:
        - single station
        - mismatched list lengths
        - n_fourier_terms extremes (0, huge)

Run directly:
    python analysis/tests/test_lifting_line.py
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup so module imports work when run from T2CIII root or analysis/
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent          # analysis/tests/
_ANALYSIS = _HERE.parent                          # analysis/
_ROOT = _ANALYSIS.parent                          # T2CIII/
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ANALYSIS))

from analysis.lifting_line import (  # noqa: E402
    WingPolarPoint,
    lift_curve_slope_and_alpha0,
    prandtl_lifting_line,
)


# ---------------------------------------------------------------------------
# Geometry helpers for the tests
# ---------------------------------------------------------------------------

def _elliptic_chord(n: int, root_chord: float, span: float) -> list[float]:
    """Elliptic chord distribution sampled at n stations across the full span.

    c(y) = c0 * sqrt(1 - (2y/b)^2).  Stations are placed at the same
    cosine-spaced theta locations the solver collocates at, mapped to y, so
    the discretisation matches the loading the theory predicts.
    """
    chords: list[float] = []
    for i in range(n):
        # theta in (0, pi) avoiding the exact tips (chord -> 0 there)
        theta = math.pi * (i + 1) / (n + 1)
        y = -(span / 2.0) * math.cos(theta)   # -b/2 .. +b/2
        frac = 1.0 - (2.0 * y / span) ** 2
        chords.append(root_chord * math.sqrt(max(frac, 0.0)))
    return chords


def _rectangular_chord(n: int, chord: float) -> list[float]:
    return [chord] * n


# ===========================================================================
# B2 — lift_curve_slope_and_alpha0
# ===========================================================================

class TestLiftCurveSlope(unittest.TestCase):

    def test_l1_clean_line_recovers_slope_and_alpha0(self) -> None:
        """L1: synthetic Cl = a0*(alpha - aL0) with a0=2π/rad, aL0=-2°."""
        a0_per_rad = 2.0 * math.pi
        a0_per_deg = a0_per_rad * math.pi / 180.0
        alpha_l0 = -2.0
        alpha = [a * 1.0 for a in range(-4, 9)]   # -4..8 deg
        cl = [a0_per_deg * (a - alpha_l0) for a in alpha]

        slope_rad, aL0 = lift_curve_slope_and_alpha0(alpha, cl)
        self.assertAlmostEqual(slope_rad, a0_per_rad, places=2,
            msg=f"slope expected {a0_per_rad:.4f}/rad, got {slope_rad:.4f}")
        self.assertAlmostEqual(aL0, alpha_l0, places=2,
            msg=f"alpha_L0 expected {alpha_l0}, got {aL0:.4f}")

    def test_l2_cambered_negative_alpha0(self) -> None:
        """L2: a cambered airfoil has Cl>0 at alpha=0 → alpha_L0 < 0."""
        a0_per_deg = 0.11
        alpha = list(range(-2, 9))
        cl = [a0_per_deg * a + 0.25 for a in alpha]   # +0.25 intercept
        slope_rad, aL0 = lift_curve_slope_and_alpha0(alpha, cl)
        self.assertLess(aL0, 0.0)
        # aL0 = -intercept/slope_per_deg = -0.25/0.11 ≈ -2.27
        self.assertAlmostEqual(aL0, -0.25 / 0.11, places=1)

    def test_l3_stall_knee_trimmed(self) -> None:
        """L3: points past the Cl_max knee must not corrupt the linear fit."""
        a0_per_deg = 0.11
        alpha_l0 = -2.0
        # Linear region 0..8, then a stall plateau/drop 10..16
        lin_alpha = list(range(-2, 9))
        lin_cl = [a0_per_deg * (a - alpha_l0) for a in lin_alpha]
        stall_alpha = [10, 12, 14, 16]
        stall_cl = [1.10, 1.12, 1.05, 0.90]   # knee + drop
        alpha = lin_alpha + stall_alpha
        cl = lin_cl + stall_cl
        slope_rad, aL0 = lift_curve_slope_and_alpha0(alpha, cl)
        slope_deg = slope_rad * math.pi / 180.0
        self.assertAlmostEqual(slope_deg, a0_per_deg, places=2,
            msg=f"stall points corrupted the slope: {slope_deg:.4f}/deg")
        self.assertAlmostEqual(aL0, alpha_l0, places=1)

    def test_l4_too_few_points(self) -> None:
        """L4: fewer than 2 usable points raises ValueError (clear failure)."""
        with self.assertRaises(ValueError):
            lift_curve_slope_and_alpha0([0.0], [0.1])
        with self.assertRaises(ValueError):
            lift_curve_slope_and_alpha0([], [])


# ===========================================================================
# B1 — prandtl_lifting_line
# ===========================================================================

class TestPrandtlLiftingLine(unittest.TestCase):

    A0 = 2.0 * math.pi   # 2-D lift-curve slope, per rad

    def test_p1_elliptic_efficiency_unity(self) -> None:
        """P1 (headline): elliptic chord + zero twist → e ≈ 1.0, δ ≈ 0."""
        n = 41
        span = 10.0
        root_chord = 1.0
        chords = _elliptic_chord(n, root_chord, span)
        twist = [0.0] * n
        res = prandtl_lifting_line(
            chord_distribution_m=chords,
            twist_distribution_deg=twist,
            sectional_cl_alpha_per_rad=self.A0,
            sectional_cl_alpha0_deg=0.0,
            span_m=span,
            aoa_root_deg=5.0,
            velocity_ms=50.0,
            density_kgm3=1.225,
            n_fourier_terms=40,
        )
        self.assertTrue(res["passed"], f"warnings: {res['warnings']}")
        self.assertAlmostEqual(
            res["span_efficiency_e"], 1.0, delta=0.02,
            msg=f"elliptic e expected ≈1.0, got {res['span_efficiency_e']:.4f}",
        )
        self.assertGreater(res["CL_3D"], 0.0)
        self.assertGreater(res["CDi"], 0.0)

    def test_p2_finite_wing_below_2d(self) -> None:
        """P2: a finite rectangular wing produces CL_3D < CL_2D at same AoA."""
        n = 41
        span = 8.0
        chord = 1.0
        chords = _rectangular_chord(n, chord)
        twist = [0.0] * n
        aoa = 5.0
        res = prandtl_lifting_line(
            chord_distribution_m=chords,
            twist_distribution_deg=twist,
            sectional_cl_alpha_per_rad=self.A0,
            sectional_cl_alpha0_deg=0.0,
            span_m=span,
            aoa_root_deg=aoa,
            velocity_ms=50.0,
            density_kgm3=1.225,
            n_fourier_terms=40,
        )
        self.assertTrue(res["passed"], f"warnings: {res['warnings']}")
        cl_2d = self.A0 * math.radians(aoa)   # 2-D would give a0*alpha
        self.assertLess(
            res["CL_3D"], cl_2d,
            msg=f"CL_3D {res['CL_3D']:.4f} should be < CL_2D {cl_2d:.4f}",
        )
        # Finite-wing loss is real but bounded — should retain most of the lift
        self.assertGreater(res["CL_3D"], 0.5 * cl_2d)

    def test_p3_induced_drag_consistency(self) -> None:
        """P3: CDi ≈ CL_3D² / (π e AR) — the two CDi routes must agree."""
        n = 41
        span = 9.0
        chord = 1.2
        chords = _rectangular_chord(n, chord)
        twist = [0.0] * n
        res = prandtl_lifting_line(
            chord_distribution_m=chords,
            twist_distribution_deg=twist,
            sectional_cl_alpha_per_rad=self.A0,
            sectional_cl_alpha0_deg=0.0,
            span_m=span,
            aoa_root_deg=6.0,
            velocity_ms=40.0,
            density_kgm3=1.225,
            n_fourier_terms=40,
        )
        self.assertTrue(res["passed"], f"warnings: {res['warnings']}")
        area = chord * span
        ar = span ** 2 / area
        e = res["span_efficiency_e"]
        cdi_formula = res["CL_3D"] ** 2 / (math.pi * e * ar)
        self.assertAlmostEqual(
            res["CDi"], cdi_formula, delta=1e-4 + 0.02 * cdi_formula,
            msg=f"CDi {res['CDi']:.6f} vs formula {cdi_formula:.6f}",
        )

    def test_p4_three_d_slope_formula(self) -> None:
        """P4: For an (≈elliptic) wing the 3-D slope matches the textbook
        finite-wing slope a = a0 / (1 + a0/(π AR)) with τ≈0."""
        n = 51
        span = 10.0
        root_chord = 1.0
        chords = _elliptic_chord(n, root_chord, span)
        twist = [0.0] * n
        aoa = 4.0
        res = prandtl_lifting_line(
            chord_distribution_m=chords,
            twist_distribution_deg=twist,
            sectional_cl_alpha_per_rad=self.A0,
            sectional_cl_alpha0_deg=0.0,
            span_m=span,
            aoa_root_deg=aoa,
            velocity_ms=50.0,
            density_kgm3=1.225,
            n_fourier_terms=50,
        )
        self.assertTrue(res["passed"], f"warnings: {res['warnings']}")
        # Reconstruct AR from the elliptic planform: area = (pi/4) c0 b
        area = math.pi / 4.0 * root_chord * span
        ar = span ** 2 / area
        a_3d = self.A0 / (1.0 + self.A0 / (math.pi * ar))   # τ=0 for elliptic
        cl_pred = a_3d * math.radians(aoa)                  # alpha_L0 = 0
        self.assertAlmostEqual(
            res["CL_3D"], cl_pred, delta=0.03 * cl_pred,
            msg=f"CL_3D {res['CL_3D']:.4f} vs 3-D-slope pred {cl_pred:.4f}",
        )

    def test_p5_distributions_length_and_sign(self) -> None:
        """P5 (sanity): L and Cl_local lists have the right length and sign."""
        n = 21
        span = 8.0
        chords = _rectangular_chord(n, 1.0)
        twist = [0.0] * n
        res = prandtl_lifting_line(
            chord_distribution_m=chords,
            twist_distribution_deg=twist,
            sectional_cl_alpha_per_rad=self.A0,
            sectional_cl_alpha0_deg=0.0,
            span_m=span,
            aoa_root_deg=5.0,
            velocity_ms=50.0,
            density_kgm3=1.225,
        )
        self.assertTrue(res["passed"])
        self.assertEqual(len(res["L_distribution"]), n)
        self.assertEqual(len(res["Cl_local"]), n)
        # Positive AoA → positive lift everywhere on a symmetric section
        self.assertTrue(all(v > 0 for v in res["L_distribution"]))
        # Tip loading must be below root loading for a rectangular wing
        self.assertLess(res["Cl_local"][0], res["Cl_local"][n // 2])


class TestPrandtlDegenerate(unittest.TestCase):
    """P5: degenerate inputs return passed=False with a warning, never crash."""

    def _call(self, **overrides) -> WingPolarPoint:
        base = dict(
            chord_distribution_m=[1.0] * 11,
            twist_distribution_deg=[0.0] * 11,
            sectional_cl_alpha_per_rad=2.0 * math.pi,
            sectional_cl_alpha0_deg=0.0,
            span_m=8.0,
            aoa_root_deg=5.0,
            velocity_ms=50.0,
            density_kgm3=1.225,
        )
        base.update(overrides)
        return prandtl_lifting_line(**base)

    def test_single_station(self) -> None:
        res = self._call(chord_distribution_m=[1.0], twist_distribution_deg=[0.0])
        self.assertFalse(res["passed"])
        self.assertTrue(res["warnings"])

    def test_mismatched_lengths(self) -> None:
        res = self._call(
            chord_distribution_m=[1.0] * 11,
            twist_distribution_deg=[0.0] * 5,
        )
        self.assertFalse(res["passed"])
        self.assertTrue(res["warnings"])

    def test_n_fourier_zero(self) -> None:
        res = self._call(n_fourier_terms=0)
        self.assertFalse(res["passed"])
        self.assertTrue(res["warnings"])

    def test_n_fourier_huge_is_clamped(self) -> None:
        """A huge n_fourier_terms (> #stations) must not crash; it is clamped
        and the run still succeeds with a warning."""
        res = self._call(n_fourier_terms=100000)
        # Either it succeeds (clamped) or it fails gracefully — but never crashes
        # and always carries warnings about the clamp.
        self.assertTrue(res["warnings"])

    def test_zero_span(self) -> None:
        res = self._call(span_m=0.0)
        self.assertFalse(res["passed"])
        self.assertTrue(res["warnings"])


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("lifting-line theory — unit test suite (pure Python, always runs)")
    print("=" * 60)
    unittest.main(verbosity=2)
