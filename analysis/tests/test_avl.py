"""
analysis/tests/test_avl.py — Unit tests for the AVL wrapper (analysis/avl.py)
=============================================================================

AVL is INVISCID: it yields CL / CDi / e / stability & control derivatives but
NO viscous Cd_total and NO stall.  No AVL binary is installed in this
environment, so the real execution path is exercised via the ``mock_avl`` test
double (mirroring ``mock_xfoil``); the missing-binary error path is checked
against a bogus path.

Test plan
---------
Group A (always run — no binary needed):
  A1  _resolve_avl_bin priority chain (override → MIRUM_AVL_BIN → default).
  A2  _abs_if_path: relative path → absolute; bare name untouched.
  A3  run_avl_analysis raises AvlNotFoundError on a bogus binary path.
  A4  _build_avl_geometry: .avl file has the right SECTION count, surface name,
      reference S/c/b, and a CONTROL line when a control surface is requested.
  A5  _parse_avl_forces: a canned .st block parses into the TypedDict.
  A6  B4 fallback: analyze_wing_3d with prefer_avl=True but no binary falls
      back to lifting_line and returns a passing WingPolarPoint.

Group B (mock_avl shim — runs the subprocess path end to end):
  B1  run_avl_analysis via mock_avl returns CL_3D≈0.451, CDi≈0.0085, e≈0.96.
  B2  stability derivatives (CLa, Cma, CLq) and the elevator control
      derivative (CLd1/Cmd1) are populated.

Run directly:
    python analysis/tests/test_avl.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent          # analysis/tests/
_ANALYSIS = _HERE.parent                          # analysis/
_ROOT = _ANALYSIS.parent                          # T2CIII/
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ANALYSIS))

from analysis.avl import (  # noqa: E402
    AvlNotFoundError,
    AvlResult,
    AvlRunError,
    WingSection,
    WingSpec,
    _abs_if_path,
    _build_avl_geometry,
    _parse_avl_forces,
    _resolve_avl_bin,
    analyze_wing_3d,
    run_avl_analysis,
)

# On POSIX/CI the executable shim ``mock_avl`` (shebang) is preferred; on
# Windows a shebang script cannot be exec'd directly, so point at the ``.py``
# module — run_avl_analysis runs a ``.py`` binary via sys.executable (see
# avl._as_argv).  Both routes exercise the same subprocess code path.
if os.name == "nt":
    _MOCK_AVL = str(_HERE / "mock_avl.py")
else:
    _MOCK_AVL = str(_HERE / "mock_avl")


def _example_wing(with_control: bool = False) -> WingSpec:
    """A simple AR≈8 rectangular wing: span 8 m, chord 1 m, 3 sections."""
    sections = [
        WingSection(le_x=0.0, le_y=0.0, le_z=0.0, chord=1.0, incidence_deg=0.0),
        WingSection(le_x=0.0, le_y=2.0, le_z=0.0, chord=1.0, incidence_deg=0.0),
        WingSection(le_x=0.0, le_y=4.0, le_z=0.0, chord=1.0, incidence_deg=0.0),
    ]
    if with_control:
        for s in sections:
            s["control_name"] = "elevator"
            s["control_gain"] = 1.0
            s["control_xhinge"] = 0.7
    return WingSpec(
        name="Mirum wing",
        sections=sections,
        ref_area=8.0,
        ref_chord=1.0,
        ref_span=8.0,
        mach=0.0,
    )


# ===========================================================================
# Group A — always run
# ===========================================================================

class TestResolveAvlBin(unittest.TestCase):

    def test_a1_override_wins(self) -> None:
        got = _resolve_avl_bin(os.path.join("analysis", "tests", "mock_avl"))
        self.assertTrue(os.path.isabs(got))
        self.assertTrue(got.endswith("mock_avl"))

    def test_a1_env_var_used_when_no_override(self) -> None:
        old = os.environ.get("MIRUM_AVL_BIN")
        try:
            os.environ["MIRUM_AVL_BIN"] = os.path.join("analysis", "tests", "mock_avl")
            got = _resolve_avl_bin(None)
            self.assertTrue(os.path.isabs(got))
            self.assertTrue(got.endswith("mock_avl"))
        finally:
            if old is None:
                os.environ.pop("MIRUM_AVL_BIN", None)
            else:
                os.environ["MIRUM_AVL_BIN"] = old

    def test_a1_default_when_nothing_set(self) -> None:
        old = os.environ.get("MIRUM_AVL_BIN")
        try:
            os.environ.pop("MIRUM_AVL_BIN", None)
            got = _resolve_avl_bin(None)
            # nt → bin/avl.exe ; posix → 'avl'
            self.assertTrue(got.endswith("avl.exe") or got == "avl")
        finally:
            if old is not None:
                os.environ["MIRUM_AVL_BIN"] = old


class TestAbsIfPath(unittest.TestCase):

    def test_a2_relative_path_becomes_absolute(self) -> None:
        result = _abs_if_path(os.path.join("analysis", "tests", "mock_avl"))
        self.assertTrue(os.path.isabs(result))
        self.assertTrue(result.endswith("mock_avl"))

    def test_a2_bare_command_name_unchanged(self) -> None:
        self.assertEqual(_abs_if_path("avl"), "avl")


class TestNotFound(unittest.TestCase):

    def test_a3_bogus_binary_raises(self) -> None:
        spec = _example_wing()
        with self.assertRaises(AvlNotFoundError):
            run_avl_analysis(spec, alpha_deg=5.0, velocity_ms=50.0,
                             density_kgm3=1.225,
                             avl_bin="/no/such/avl_binary_xyz")


class TestBuildGeometry(unittest.TestCase):

    def test_a4_section_count_and_refs(self) -> None:
        spec = _example_wing()
        avl_text, run_text = _build_avl_geometry(
            spec, alpha_deg=5.0, velocity_ms=50.0, density_kgm3=1.225,
        )
        # One SURFACE, three SECTIONs.
        self.assertEqual(avl_text.count("\nSECTION"), 3,
                         f"expected 3 SECTIONs, got:\n{avl_text}")
        self.assertIn("SURFACE", avl_text)
        self.assertIn("Mirum wing", avl_text)
        # Reference quantities Sref Cref Bref appear (8 / 1 / 8).
        self.assertRegex(avl_text, r"8\.0+\s+1\.0+\s+8\.0+")
        # Run-case carries the alpha and density/velocity.
        self.assertIn("alpha", run_text.lower())
        self.assertRegex(run_text, r"5\.0")

    def test_a4_control_line_present(self) -> None:
        spec = _example_wing(with_control=True)
        avl_text, _ = _build_avl_geometry(
            spec, alpha_deg=5.0, velocity_ms=50.0, density_kgm3=1.225,
        )
        self.assertIn("CONTROL", avl_text)
        self.assertIn("elevator", avl_text)

    def test_a4_no_control_line_when_absent(self) -> None:
        spec = _example_wing(with_control=False)
        avl_text, _ = _build_avl_geometry(
            spec, alpha_deg=5.0, velocity_ms=50.0, density_kgm3=1.225,
        )
        self.assertNotIn("CONTROL", avl_text)


class TestParseForces(unittest.TestCase):

    _ST = (_HERE.parent / "tests" / "_fixture.st")

    def test_a5_parse_canned_block(self) -> None:
        # Build the canned block via the mock module so the fixture stays in
        # one place.
        sys.path.insert(0, str(_HERE))
        import mock_avl  # noqa: WPS433  (test-local import)
        block = mock_avl._ST_BLOCK

        with tempfile.NamedTemporaryFile(
            suffix=".st", delete=False, mode="w", encoding="utf-8"
        ) as fh:
            fh.write(block)
            path = fh.name
        try:
            res = _parse_avl_forces(path)
            self.assertAlmostEqual(res["CL_3D"], 0.45120, places=4)
            self.assertAlmostEqual(res["CDi"], 0.00854, places=4)
            self.assertAlmostEqual(res["span_efficiency_e"], 0.96, places=3)
            # Stability derivatives present.
            self.assertAlmostEqual(res["stability_derivatives"]["CLa"], 5.123456, places=4)
            self.assertAlmostEqual(res["stability_derivatives"]["Cma"], -0.812000, places=4)
            self.assertAlmostEqual(res["stability_derivatives"]["CLq"], 7.512000, places=4)
        finally:
            os.unlink(path)


class TestFallback(unittest.TestCase):

    def test_a6_no_binary_falls_back_to_lifting_line(self) -> None:
        spec = _example_wing()
        old = os.environ.get("MIRUM_AVL_BIN")
        try:
            os.environ.pop("MIRUM_AVL_BIN", None)
            res = analyze_wing_3d(
                spec,
                alpha_deg=5.0,
                velocity_ms=50.0,
                density_kgm3=1.225,
                sectional_cl_alpha_per_rad=2.0 * 3.141592653589793,
                sectional_cl_alpha0_deg=0.0,
                prefer_avl=True,
                avl_bin="/no/such/avl_binary_xyz",   # force the missing-binary path
            )
            self.assertTrue(res["passed"], f"warnings: {res.get('warnings')}")
            self.assertGreater(res["CL_3D"], 0.0)
            self.assertIn("span_efficiency_e", res)
            # The fallback must announce itself.
            self.assertTrue(
                any("lifting" in w.lower() or "avl" in w.lower()
                    for w in res["warnings"]),
                f"expected a fallback note, got {res['warnings']}",
            )
        finally:
            if old is not None:
                os.environ["MIRUM_AVL_BIN"] = old


# ===========================================================================
# Group B — exercise the subprocess path via the mock_avl shim
# ===========================================================================

class TestRunViaMock(unittest.TestCase):

    def test_b1_forces_via_mock(self) -> None:
        spec = _example_wing()
        res = run_avl_analysis(
            spec, alpha_deg=5.0, velocity_ms=50.0, density_kgm3=1.225,
            avl_bin=_MOCK_AVL,
        )
        self.assertAlmostEqual(res["CL_3D"], 0.45120, places=4)
        self.assertAlmostEqual(res["CDi"], 0.00854, places=4)
        self.assertAlmostEqual(res["span_efficiency_e"], 0.96, places=3)
        self.assertTrue(res["passed"])

    def test_b2_derivatives_via_mock(self) -> None:
        spec = _example_wing(with_control=True)
        res = run_avl_analysis(
            spec, alpha_deg=5.0, velocity_ms=50.0, density_kgm3=1.225,
            avl_bin=_MOCK_AVL,
        )
        derivs = res["stability_derivatives"]
        self.assertIn("CLa", derivs)
        self.assertIn("Cma", derivs)
        self.assertIn("CLq", derivs)
        # Control derivative captured (elevator → d1).
        ctrl = res["control_derivatives"]
        self.assertTrue(ctrl, "expected a non-empty control-derivative map")
        # CLd1 / Cmd1 from the canned block.
        self.assertAlmostEqual(ctrl.get("CLd1", 0.0), 0.011500, places=4)
        self.assertAlmostEqual(ctrl.get("Cmd1", 0.0), -0.024500, places=4)


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("AVL wrapper — unit test suite (mock_avl shim; no real binary)")
    print("=" * 60)
    unittest.main(verbosity=2)
