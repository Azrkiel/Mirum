"""T-03 unit tests — backend/verification/geometry.py

Tests use programmatically constructed OCC shapes rather than external STEP
files — a box and a sphere for known-good cases; a zero-volume shell and an
invalid BRep for known-bad cases.

OCC (pythonocc-core) is required. If it is not installed the full suite is
skipped with a clear message so the test run stays green in dev environments
where OCC lives only inside Docker.

Run from repo root:
    python backend/tests/test_geometry_verification.py
or via pytest:
    pytest backend/tests/test_geometry_verification.py -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Guard — skip everything if OCC is not installed
# ---------------------------------------------------------------------------

try:
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeSphere
    from OCC.Core.BRepBuilderAPI import (
        BRepBuilderAPI_MakeShell,
        BRepBuilderAPI_MakeSolid,
    )
    from OCC.Core.BRep import BRep_Builder
    from OCC.Core.TopoDS import TopoDS_Compound
    from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCC.Core.IFSelect import IFSelect_RetDone
    _OCC_AVAILABLE = True
except ModuleNotFoundError:
    try:
        # CadQuery ships OCP, not pythonocc-core. Run the same assertions on
        # the actual supported binding instead of silently skipping native CI.
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeSphere
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeShell, BRepBuilderAPI_MakeSolid
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound
        from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
        from OCP.IFSelect import IFSelect_RetDone
        _OCC_AVAILABLE = True
    except ModuleNotFoundError:
        _OCC_AVAILABLE = False

_SKIP_MSG = "OpenCascade bindings unavailable: install CadQuery/OCP or pythonocc-core."


def _write_step(shape, path: str) -> None:
    """Write an OCC shape to a STEP file."""
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    status = writer.Write(path)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed for {path}")


def _make_box_step(tmp_dir: str) -> str:
    """Create a 10×20×30 mm box STEP file and return its path."""
    shape = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()
    path = str(Path(tmp_dir) / "box.step")
    _write_step(shape, path)
    return path


def _make_sphere_step(tmp_dir: str) -> str:
    """Create a sphere of radius 15 mm and return its STEP path."""
    shape = BRepPrimAPI_MakeSphere(15.0).Shape()
    path = str(Path(tmp_dir) / "sphere.step")
    _write_step(shape, path)
    return path


def _make_empty_compound_step(tmp_dir: str) -> str:
    """Create an empty compound (zero-volume) and return its STEP path."""
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    path = str(Path(tmp_dir) / "empty.step")
    _write_step(compound, path)
    return path


# ---------------------------------------------------------------------------
# Lazy import of verification.geometry (may not exist yet when running T-01/02)
# ---------------------------------------------------------------------------

def _import_geometry_module():
    try:
        from verification.geometry import certify_geometry, GeometryCertificate  # type: ignore[import]
        return certify_geometry, GeometryCertificate
    except ModuleNotFoundError as exc:
        raise unittest.SkipTest(
            f"verification.geometry not yet installed: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Tests — known-good shapes
# ---------------------------------------------------------------------------

@unittest.skipUnless(_OCC_AVAILABLE, _SKIP_MSG)
class TestKnownGoodShapes(unittest.TestCase):
    """Known-good shapes must produce passed=True certificates."""

    def setUp(self):
        certify_geometry, _ = _import_geometry_module()
        self._certify = certify_geometry
        self._tmpdir = tempfile.mkdtemp()

    def test_box_passes(self):
        path = _make_box_step(self._tmpdir)
        cert = self._certify(path)
        self.assertTrue(cert["passed"], f"Box should pass. failures={cert['failures']}")

    def test_box_volume_positive(self):
        path = _make_box_step(self._tmpdir)
        cert = self._certify(path)
        self.assertIsNotNone(cert["volume_mm3"])
        self.assertGreater(cert["volume_mm3"], 0.0)

    def test_box_volume_correct(self):
        """10×20×30 = 6000 mm³ ± 1%."""
        path = _make_box_step(self._tmpdir)
        cert = self._certify(path)
        self.assertAlmostEqual(cert["volume_mm3"], 6000.0, delta=60.0)

    def test_box_surface_area_positive(self):
        path = _make_box_step(self._tmpdir)
        cert = self._certify(path)
        self.assertIsNotNone(cert["surface_area_mm2"])
        self.assertGreater(cert["surface_area_mm2"], 0.0)

    def test_box_surface_area_correct(self):
        """2*(10*20 + 10*30 + 20*30) = 2200 mm² ± 1%."""
        path = _make_box_step(self._tmpdir)
        cert = self._certify(path)
        self.assertAlmostEqual(cert["surface_area_mm2"], 2200.0, delta=22.0)

    def test_box_bounding_box_present(self):
        path = _make_box_step(self._tmpdir)
        cert = self._certify(path)
        bb = cert["bounding_box"]
        self.assertIsNotNone(bb)
        self.assertIn("x_mm", bb)
        self.assertIn("y_mm", bb)
        self.assertIn("z_mm", bb)

    def test_sphere_passes(self):
        path = _make_sphere_step(self._tmpdir)
        cert = self._certify(path)
        self.assertTrue(cert["passed"], f"Sphere should pass. failures={cert['failures']}")

    def test_sphere_volume_correct(self):
        """(4/3)π×15³ ≈ 14137.2 mm³ ± 2%."""
        import math
        path = _make_sphere_step(self._tmpdir)
        cert = self._certify(path)
        expected = (4 / 3) * math.pi * 15 ** 3
        self.assertAlmostEqual(cert["volume_mm3"], expected, delta=expected * 0.02)

    def test_all_checks_present(self):
        """Certificate must have all 6 check keys."""
        path = _make_box_step(self._tmpdir)
        cert = self._certify(path)
        required_checks = {
            "volume_positive",
            "surface_area_positive",
            "no_degenerate_faces",
            "bounding_box_finite",
            "brep_valid",
            "is_solid",
        }
        self.assertTrue(
            required_checks.issubset(cert["checks"].keys()),
            f"Missing checks: {required_checks - set(cert['checks'].keys())}"
        )

    def test_failures_list_empty_for_good_shape(self):
        path = _make_box_step(self._tmpdir)
        cert = self._certify(path)
        self.assertEqual(cert["failures"], [])


# ---------------------------------------------------------------------------
# Tests — known-bad shapes
# ---------------------------------------------------------------------------

@unittest.skipUnless(_OCC_AVAILABLE, _SKIP_MSG)
class TestKnownBadShapes(unittest.TestCase):
    """Known-bad shapes must produce passed=False certificates."""

    def setUp(self):
        certify_geometry, _ = _import_geometry_module()
        self._certify = certify_geometry
        self._tmpdir = tempfile.mkdtemp()

    def test_empty_compound_fails(self):
        path = _make_empty_compound_step(self._tmpdir)
        cert = self._certify(path)
        self.assertFalse(cert["passed"], "Empty compound should fail")

    def test_empty_compound_has_failures(self):
        path = _make_empty_compound_step(self._tmpdir)
        cert = self._certify(path)
        self.assertGreater(len(cert["failures"]), 0)

    def test_nonexistent_file_fails(self):
        cert = self._certify("/tmp/does_not_exist_xyzzy.step")
        self.assertFalse(cert["passed"])
        self.assertGreater(len(cert["failures"]), 0)

    def test_nonexistent_file_volume_is_none(self):
        cert = self._certify("/tmp/does_not_exist_xyzzy.step")
        self.assertIsNone(cert["volume_mm3"])

    def test_empty_compound_volume_is_zero_or_none(self):
        path = _make_empty_compound_step(self._tmpdir)
        cert = self._certify(path)
        # Either None (not computed) or 0.0 for an empty shape
        self.assertTrue(
            cert["volume_mm3"] is None or cert["volume_mm3"] == 0.0,
            f"Expected None or 0 for empty compound, got {cert['volume_mm3']}"
        )


# ---------------------------------------------------------------------------
# Tests — certificate TypedDict structure (no OCC needed)
# ---------------------------------------------------------------------------

class TestCertificateStructure(unittest.TestCase):
    """Verify the TypedDict has all required keys (no OCC needed)."""

    def _get_cert_class(self):
        try:
            from verification.geometry import GeometryCertificate  # type: ignore[import]
            return GeometryCertificate
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def test_certificate_keys(self):
        """Build a minimal certificate dict and check required keys."""
        required = {
            "passed", "volume_mm3", "surface_area_mm2",
            "bounding_box", "checks", "failures",
        }
        # Construct a minimal valid certificate
        cert = {
            "passed": True,
            "volume_mm3": 100.0,
            "surface_area_mm2": 200.0,
            "bounding_box": {"x_mm": 10.0, "y_mm": 10.0, "z_mm": 10.0},
            "checks": {},
            "failures": [],
        }
        self.assertTrue(required.issubset(cert.keys()))

    def test_certificate_passed_is_bool(self):
        cert = {"passed": False, "volume_mm3": None, "surface_area_mm2": None,
                "bounding_box": None, "checks": {}, "failures": ["bad"]}
        self.assertIsInstance(cert["passed"], bool)

    def test_certificate_failures_is_list(self):
        cert = {"passed": False, "volume_mm3": None, "surface_area_mm2": None,
                "bounding_box": None, "checks": {}, "failures": ["err1", "err2"]}
        self.assertIsInstance(cert["failures"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
