"""
analysis/tests/test_airfoil.py — Unit tests for the airfoil analysis module
============================================================================

Test plan
---------
Group A (pure Python — always run, no external deps):
  A1  naca4_to_dat: valid 4-digit codes produce well-formed .dat files
  A2  naca4_to_dat: symmetric airfoil (0012) has zero camber at all x
  A3  naca4_to_dat: invalid code raises ValueError
  A4  _sort_airfoil_points: output is monotone upper TE→LE, lower LE→TE
  A5  _parse_polar_file: golden mock polar file parsed correctly
  A6  predict_stall: sign-change detection on synthetic Cl curve
  A7  predict_stall: soft-stall (5% drop) on plateau curve
  A8  predict_stall: returns (None, None) for <3 points

Group B (XFOIL required — skipped if binary not found):
  B1  NACA 0012, Re=1e6: Cl at α=5° ≈ 0.57 ± 0.03
  B2  NACA 0012, Re=1e6: Cd at α=0° ≈ 0.006 ± 0.001
  B3  NACA 0012, Re=1e6: stall_alpha ∈ [15°, 17°] (XFOIL puts it at α≈16° step=1°)
  B4  NACA 0012, Re=1e6: PolarResult type contract (all required keys present)
  B5  NACA 0012: converged_count ≥ 18 over α=[0..20°, step=1°]
  B6  run_xfoil_polar: XfoilNotFoundError on bogus binary path
  B7  L/D positive for all converged lifting points pre-stall (skips α=0 zero-lift point)

Run directly:
    cd T2CIII
    python -m pytest analysis/tests/test_airfoil.py -v
    # or without pytest:
    python analysis/tests/test_airfoil.py
"""

from __future__ import annotations

import os
import sys
import math
import tempfile
import textwrap
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

from analysis.airfoil import (
    FlightEnvelope,
    PolarResult,
    XfoilNotFoundError,
    XfoilRunError,
    _abs_if_path,
    _parse_polar_file,
    _sort_airfoil_points,
    analyze_naca,
    naca4_to_dat,
    predict_stall,
    predict_stall_from_lists,
    run_xfoil_polar,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _xfoil_available() -> bool:
    """Return True if the XFOIL binary resolves and responds."""
    import subprocess
    candidates = [
        os.environ.get("MIRUM_XFOIL_BIN", ""),
        r"C:\Users\Kevin\Developments\T2CIII\bin\xfoil.exe",
        "xfoil",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate],
                input="QUIT\n",
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "XFOIL" in (result.stdout or "") or result.returncode == 0:
                return True
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
    return False


_XFOIL_PRESENT = _xfoil_available()

_MOCK_POLAR = textwrap.dedent("""\
 XFOIL         Version 6.99

 Calculated polar for: NACA 0012

 1 1 Reynolds number fixed          Mach number fixed

 xtrf =   1.000 (top)        1.000 (bottom)
 Mach =   0.000     Re =     1.000 e 6     Ncrit =   9.000

  alpha      CL       CD       CDp      CM     Top_Xtr  Bot_Xtr
 ------ -------- -------- -------- -------- -------- --------
   0.000   0.0000   0.00606   0.00000  -0.0001   1.0000   1.0000
   1.000   0.1130   0.00614   0.00000  -0.0003   1.0000   1.0000
   2.000   0.2260   0.00633   0.00001  -0.0005   1.0000   1.0000
   3.000   0.3390   0.00664   0.00002  -0.0007   1.0000   1.0000
   4.000   0.4510   0.00706   0.00003  -0.0009   1.0000   1.0000
   5.000   0.5720   0.00762   0.00005  -0.0012   1.0000   1.0000
   6.000   0.6820   0.00831   0.00008  -0.0014   1.0000   0.9940
   7.000   0.7920   0.00918   0.00013  -0.0016   1.0000   0.9810
   8.000   0.8990   0.01025   0.00021  -0.0018   1.0000   0.9640
   9.000   1.0030   0.01156   0.00033  -0.0020   1.0000   0.9410
  10.000   1.1010   0.01317   0.00051  -0.0022   1.0000   0.9120
  11.000   1.1900   0.01514   0.00079  -0.0024   1.0000   0.8720
  12.000   1.2540   0.01777   0.00125  -0.0025   1.0000   0.8120
  13.000   1.2180   0.03280   0.01420  -0.0120   0.4230   0.7500
  14.000   1.1600   0.05200   0.03200  -0.0200   0.3100   0.6800
  15.000   1.0900   0.07400   0.05200  -0.0300   0.2800   0.6200
""")

_GOLDEN_ENV: FlightEnvelope = {
    "alpha_start": 0.0,
    "alpha_end":   20.0,
    "alpha_step":  1.0,
    "reynolds":    1_000_000.0,
    "mach":        0.0,
    "n_crit":      9.0,
}


# ===========================================================================
# Group A — pure Python, always run
# ===========================================================================

class TestNaca4ToDat(unittest.TestCase):
    """A1–A3: naca4_to_dat analytical generator."""

    def test_a1_valid_dat_format(self) -> None:
        """A1: 0012 produces a well-formed .dat file."""
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as fh:
            path = fh.name
        try:
            naca4_to_dat("0012", path)
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            # First line is the label
            self.assertIn("NACA", lines[0].upper())
            # At least 150 coordinate rows (2*100 - duplicates at LE/TE)
            coord_lines = [ln for ln in lines[1:] if ln.strip()]
            self.assertGreater(len(coord_lines), 150)
            # Every coord line has 2 floats
            for ln in coord_lines:
                parts = ln.split()
                self.assertEqual(len(parts), 2, f"Bad coord line: {ln!r}")
                x, z = float(parts[0]), float(parts[1])
                self.assertGreaterEqual(x, -0.01)
                self.assertLessEqual(x, 1.01)
        finally:
            os.unlink(path)

    def test_a2_symmetric_zero_camber(self) -> None:
        """A2: NACA 0012 is symmetric — camber (average of upper/lower z) ≈ 0."""
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False, mode="w",
                                         encoding="utf-8") as fh:
            path = fh.name
        try:
            naca4_to_dat("0012", path, n_points=50)
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            coord_lines = [ln.split() for ln in lines[1:] if ln.strip()]
            xs = [float(r[0]) for r in coord_lines]
            zs = [float(r[1]) for r in coord_lines]
            # For a symmetric airfoil, sum of all z/c values ≈ 0
            mean_z = sum(zs) / len(zs)
            self.assertAlmostEqual(mean_z, 0.0, places=3,
                msg=f"NACA 0012 should have zero mean camber, got {mean_z:.6f}")
        finally:
            os.unlink(path)

    def test_a3_invalid_code_raises(self) -> None:
        """A3: Non-4-digit codes raise ValueError."""
        with self.assertRaises(ValueError):
            naca4_to_dat("012", "/tmp/dummy.dat")
        with self.assertRaises(ValueError):
            naca4_to_dat("ABCD", "/tmp/dummy.dat")
        with self.assertRaises(ValueError):
            naca4_to_dat("24120", "/tmp/dummy.dat")


class TestAbsIfPath(unittest.TestCase):
    """Binary-path resolution contract for _resolve_xfoil_bin / _resolve_avl_bin.

    Regression: run_xfoil_polar runs the subprocess with cwd=<tmpdir>, so a
    *relative* MIRUM_XFOIL_BIN (e.g. analysis/tests/mock_xfoil) must be made
    absolute or the kernel cannot exec it from the temp dir (FileNotFoundError
    -> XfoilNotFoundError). A bare command name must stay untouched for PATH
    lookup.
    """

    def test_relative_path_becomes_absolute(self):
        result = _abs_if_path(os.path.join("analysis", "tests", "mock_xfoil"))
        self.assertTrue(os.path.isabs(result))
        self.assertTrue(result.endswith("mock_xfoil"))

    def test_bare_command_name_unchanged(self):
        # No separator => PATH lookup; must not be turned into <cwd>/xfoil.
        self.assertEqual(_abs_if_path("xfoil"), "xfoil")

    def test_already_absolute_path_stays_absolute(self):
        # resolve() may canonicalize symlinks, so assert absolute + basename
        # rather than byte-equality with the input.
        abs_in = os.path.abspath(os.path.join("bin", "xfoil"))
        result = _abs_if_path(abs_in)
        self.assertTrue(os.path.isabs(result))
        self.assertEqual(os.path.basename(result), "xfoil")


class TestSortAirfoilPoints(unittest.TestCase):
    """A4: _sort_airfoil_points ordering contract."""

    def _make_0012_raw(self, n: int = 40) -> list[tuple[float, float]]:
        """Generate raw (X, Z) points for a unit-chord NACA 0012 (mm not normalised)."""
        import math
        t = 0.12
        xs, upper, lower = [], [], []
        for i in range(n):
            x = (1 - math.cos(math.pi * i / (n - 1))) / 2
            th = (t / 0.2) * (
                0.2969 * math.sqrt(max(x, 1e-12))
                - 0.1260 * x
                - 0.3516 * x**2
                + 0.2843 * x**3
                - 0.1015 * x**4
            )
            upper.append((x, th))
            lower.append((x, -th))
        return upper + lower

    def test_a4_upper_surface_monotone(self) -> None:
        """A4: After sorting, upper surface (z≥0) is monotone descending in x."""
        raw = self._make_0012_raw()
        sorted_pts = _sort_airfoil_points(raw)
        upper = [(x, z) for x, z in sorted_pts if z >= 0]
        xs = [p[0] for p in upper]
        for i in range(len(xs) - 1):
            self.assertGreaterEqual(
                xs[i] + 1e-6, xs[i + 1],
                f"Upper surface not monotone at index {i}: {xs[i]:.4f} → {xs[i+1]:.4f}"
            )

    def test_a4_lower_surface_monotone(self) -> None:
        """A4: Lower surface (z<0) is monotone ascending in x."""
        raw = self._make_0012_raw()
        sorted_pts = _sort_airfoil_points(raw)
        lower = [(x, z) for x, z in sorted_pts if z < 0]
        xs = [p[0] for p in lower]
        for i in range(len(xs) - 1):
            self.assertLessEqual(
                xs[i] - 1e-6, xs[i + 1],
                f"Lower surface not monotone at index {i}: {xs[i]:.4f} → {xs[i+1]:.4f}"
            )

    def test_a4_x_range(self) -> None:
        """A4: All normalised x values in [0, 1]."""
        raw = self._make_0012_raw()
        sorted_pts = _sort_airfoil_points(raw)
        for x, z in sorted_pts:
            self.assertGreaterEqual(x, -0.001, f"x={x:.6f} below 0")
            self.assertLessEqual(x, 1.001, f"x={x:.6f} above 1")

    def test_a4_too_few_points_raises(self) -> None:
        """A4: Fewer than 10 points raises StepSliceError."""
        from analysis.airfoil import StepSliceError
        with self.assertRaises(StepSliceError):
            _sort_airfoil_points([(0.0, 0.0), (1.0, 0.0)])


class TestParsePolarFile(unittest.TestCase):
    """A5: _parse_polar_file correctly parses the mock polar."""

    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(
            suffix=".txt", delete=False, mode="w", encoding="utf-8"
        )
        self._tmp.write(_MOCK_POLAR)
        self._tmp.close()
        self._path = self._tmp.name

    def tearDown(self) -> None:
        os.unlink(self._path)

    def test_a5_row_count(self) -> None:
        """A5: 16 data rows (α=0..15) are parsed."""
        parsed = _parse_polar_file(self._path)
        self.assertEqual(len(parsed["alpha"]), 16)
        self.assertEqual(len(parsed["Cl"]),    16)
        self.assertEqual(len(parsed["Cd"]),    16)
        self.assertEqual(len(parsed["Cm"]),    16)

    def test_a5_alpha_values(self) -> None:
        """A5: Alpha column matches 0..15 integer sequence."""
        parsed = _parse_polar_file(self._path)
        expected = list(range(16))
        for got, exp in zip(parsed["alpha"], expected):
            self.assertAlmostEqual(got, float(exp), places=2)

    def test_a5_cl_at_5deg(self) -> None:
        """A5: Cl at α=5° matches mock value 0.5720."""
        parsed = _parse_polar_file(self._path)
        idx = parsed["alpha"].index(5.0)
        self.assertAlmostEqual(parsed["Cl"][idx], 0.5720, places=3)

    def test_a5_cd_at_0deg(self) -> None:
        """A5: Cd at α=0° matches mock value 0.00606."""
        parsed = _parse_polar_file(self._path)
        idx = parsed["alpha"].index(0.0)
        self.assertAlmostEqual(parsed["Cd"][idx], 0.00606, places=5)

    def test_a5_missing_file_returns_empty(self) -> None:
        """A5: Non-existent file returns empty lists, no exception."""
        parsed = _parse_polar_file("/tmp/does_not_exist_xfoil_polar.txt")
        self.assertEqual(parsed["alpha"], [])
        self.assertEqual(parsed["Cl"], [])

    def test_a5_cm_sign(self) -> None:
        """A5: Cm values at α=0 should be negative for NACA 0012."""
        parsed = _parse_polar_file(self._path)
        # Cm at α=0 is -0.0001 in the mock
        self.assertLess(parsed["Cm"][0], 0.0)


class TestPredictStall(unittest.TestCase):
    """A6–A8: predict_stall edge cases."""

    def _make_polar_result(
        self, alpha: list[float], cl: list[float]
    ) -> PolarResult:
        """Helper: build a minimal PolarResult from alpha + Cl."""
        cd = [0.01] * len(alpha)
        cm = [0.0]  * len(alpha)
        ld = [c / d for c, d in zip(cl, cd)]
        sa, cl_max = predict_stall_from_lists(alpha, cl)
        return PolarResult(
            naca_code="0012", chord_mm=200.0, reynolds=1e6, mach=0.0, n_crit=9.0,
            alpha=alpha, Cl=cl, Cd=cd, Cm=cm, L_D=ld,
            converged_count=len(alpha),
            stall_alpha=sa, Cl_max=cl_max,
            dat_path=None, polar_path=None,
        )

    def test_a6_sign_change_detection(self) -> None:
        """A6: Sign change in dCl/dα detected at correct alpha.

        Data: Cl linear from α=0..12 (slope 0.11/°), then sharp drop at α=13+.
        Hard-stall criterion: dCl/dα goes positive→negative at α=12.
        """
        alpha = list(range(16))
        # Cl rises linearly 0→1.32 over α=0..12, then drops sharply
        cl = [0.11 * a for a in range(13)] + [1.20, 1.05, 0.90]
        # Verify the test data itself is as intended
        self.assertAlmostEqual(cl[12], 1.32, places=2)  # peak at α=12
        self.assertLess(cl[13], cl[12])                 # drop at α=13
        sa, cl_max = predict_stall_from_lists(alpha, cl)
        self.assertIsNotNone(sa, "stall_alpha should not be None")
        assert sa is not None
        # Hard stall fires at α=12 (dCl_back>0, dCl_fwd<0)
        self.assertGreaterEqual(sa, 11.0)
        self.assertLessEqual(sa, 13.0)
        self.assertAlmostEqual(cl_max, 1.32, places=2)

    def test_a7_soft_stall_plateau(self) -> None:
        """A7: Monotone plateau triggers soft stall (5% drop criterion)."""
        # Cl plateaus then slowly drops — no sign change in dCl/dα until very late
        alpha = [0, 2, 4, 6, 8, 10, 12, 14, 16]
        cl    = [0.0, 0.22, 0.44, 0.66, 0.88, 1.05, 1.08, 1.02, 0.95]
        sa, cl_max = predict_stall_from_lists(alpha, cl)
        self.assertIsNotNone(sa)
        # Cl_max = 1.08 at α=12; 5% drop → 1.026; first below that is 1.02 at α=14
        self.assertAlmostEqual(cl_max, 1.08, places=2)

    def test_a8_too_few_returns_none(self) -> None:
        """A8: Fewer than 3 data points → (None, None)."""
        self.assertEqual(predict_stall_from_lists([], []), (None, None))
        self.assertEqual(predict_stall_from_lists([0.0], [0.0]), (None, None))
        self.assertEqual(predict_stall_from_lists([0.0, 5.0], [0.5, 0.6]), (None, None))

    def test_a8_predict_stall_wrapper(self) -> None:
        """A8: predict_stall(polar) wrapper matches predict_stall_from_lists."""
        alpha = list(range(16))
        cl    = [0.11 * a for a in range(13)] + [1.32, 1.20, 1.05]
        polar = self._make_polar_result(alpha, cl)
        # The wrapper should give the same answer that was stored in the dict
        sa, cl_max = predict_stall(polar)
        self.assertEqual(sa, polar["stall_alpha"])
        self.assertEqual(cl_max, polar["Cl_max"])


# ===========================================================================
# Group B — XFOIL required (golden value tests)
# ===========================================================================

@unittest.skipUnless(_XFOIL_PRESENT, "XFOIL binary not found — skipping live polar tests")
class TestXfoilGoldenNaca0012(unittest.TestCase):
    """B1–B7: Live XFOIL golden values for NACA 0012, Re=1e6."""

    _polar: PolarResult | None = None    # cached across test methods

    @classmethod
    def setUpClass(cls) -> None:
        """Run XFOIL once; share the result across all B tests."""
        cls._polar = analyze_naca(
            naca_code="0012",
            chord_mm=200.0,
            flight_env=_GOLDEN_ENV,
            timeout_s=60,
        )

    def _alpha_index(self, target_alpha: float) -> int:
        """Return index of the closest alpha to target in the polar."""
        assert self._polar is not None
        alphas = self._polar["alpha"]
        return min(range(len(alphas)), key=lambda i: abs(alphas[i] - target_alpha))

    def test_b1_cl_at_5deg(self) -> None:
        """B1: Cl at α=5° ≈ 0.57 ± 0.03 (thin-airfoil theory + XFOIL correction)."""
        assert self._polar is not None
        idx = self._alpha_index(5.0)
        cl = self._polar["Cl"][idx]
        self.assertAlmostEqual(
            cl, 0.57, delta=0.03,
            msg=f"Cl at α=5°: expected 0.57±0.03, got {cl:.4f}"
        )

    def test_b2_cd_at_0deg(self) -> None:
        """B2: Cd at α=0° ≈ 0.006 ± 0.001."""
        assert self._polar is not None
        idx = self._alpha_index(0.0)
        cd = self._polar["Cd"][idx]
        self.assertAlmostEqual(
            cd, 0.006, delta=0.001,
            msg=f"Cd at α=0°: expected 0.006±0.001, got {cd:.5f}"
        )

    def test_b3_stall_alpha_range(self) -> None:
        """B3: stall_alpha ∈ [15°, 17°] for NACA 0012 at Re=1e6, N_crit=9.

        XFOIL on NACA 0012 at Re=1e6 puts Cl_max at α≈15.5°.  With step=1°
        the hard-stall sign-change fires at α=16° (Cl 15→16 rises, 16→17 drops).
        """
        assert self._polar is not None
        sa = self._polar["stall_alpha"]
        self.assertIsNotNone(sa, "stall_alpha should not be None")
        assert sa is not None
        self.assertGreaterEqual(sa, 15.0, f"stall_alpha={sa:.1f}° below expected range [15,17]")
        self.assertLessEqual(sa, 17.0, f"stall_alpha={sa:.1f}° above expected range [15,17]")

    def test_b4_polar_result_type_contract(self) -> None:
        """B4: PolarResult contains all required keys with correct types."""
        assert self._polar is not None
        required_keys = {
            "naca_code", "chord_mm", "reynolds", "mach", "n_crit",
            "alpha", "Cl", "Cd", "Cm", "L_D",
            "converged_count", "stall_alpha", "Cl_max",
            "dat_path", "polar_path",
        }
        for key in required_keys:
            self.assertIn(key, self._polar, f"Missing key: {key!r}")

        self.assertIsInstance(self._polar["alpha"], list)
        self.assertIsInstance(self._polar["Cl"],    list)
        self.assertIsInstance(self._polar["Cd"],    list)
        self.assertIsInstance(self._polar["L_D"],   list)
        self.assertIsInstance(self._polar["converged_count"], int)
        self.assertEqual(self._polar["naca_code"], "0012")
        self.assertAlmostEqual(self._polar["reynolds"], 1e6, places=0)

    def test_b5_converged_count(self) -> None:
        """B5: At least 18 alpha points converge over [0..20°, step=1°]."""
        assert self._polar is not None
        self.assertGreaterEqual(
            self._polar["converged_count"], 18,
            f"Expected ≥18 converged points, got {self._polar['converged_count']}"
        )

    def test_b6_bogus_binary_raises_not_found(self) -> None:
        """B6: Non-existent binary path raises XfoilNotFoundError."""
        with self.assertRaises(XfoilNotFoundError):
            run_xfoil_polar(
                "NACA 0012",
                _GOLDEN_ENV,
                xfoil_bin="/no/such/xfoil_binary_xyz",
            )

    def test_b7_l_d_positive(self) -> None:
        """B7: L/D should be positive for all converged lifting points pre-stall.

        Skips α=0° — NACA 0012 is symmetric, so Cl(0°)=0 by physics and L/D=0
        is the correct value there, not a failure.
        """
        assert self._polar is not None
        sa = self._polar["stall_alpha"] or 15.0
        for a, ld in zip(self._polar["alpha"], self._polar["L_D"]):
            if not (0.0 < a < sa):
                continue
            self.assertGreater(
                ld, 0.0,
                f"L/D={ld:.2f} should be positive at α={a:.1f}° (pre-stall)"
            )


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    # Pretty header
    print("=" * 60)
    print("airfoil analysis — unit test suite")
    print(f"XFOIL present: {_XFOIL_PRESENT}")
    if not _XFOIL_PRESENT:
        print("  ↳ Group B (golden value) tests will be SKIPPED.")
        print("  ↳ Run bin/install_xfoil_windows.ps1 to install XFOIL.")
    print("=" * 60)
    unittest.main(verbosity=2)
