"""Track A — backend/cad_aero.py: deterministic OML wing builders.

Tests are CADQUERY-gated (skip the geometry suites if `cadquery` is not
importable). NOTE: we gate on `cadquery`, NOT on `OCC` — cad_aero builds
geometry through the cadquery API (OCP bindings), which is the actual runtime
dependency. The allowlist test is pure-AST and runs ungated.

Run from repo root:
    python backend/tests/test_cad_aero.py
or via pytest:
    pytest backend/tests/test_cad_aero.py -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# backend/ on the path so `import cad_aero`, `import compiler`, `import cad_utils` resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import cadquery as cq  # noqa: F401

    _CQ_AVAILABLE = True
except ModuleNotFoundError:
    _CQ_AVAILABLE = False

_SKIP_MSG = "cadquery not importable in this environment (needed for OML geometry)."


# ---------------------------------------------------------------------------
# A0 — sandbox allowlist (pure AST, ungated)
# ---------------------------------------------------------------------------
class TestAllowlist(unittest.TestCase):
    """`cad_aero` must be importable by sandboxed LLM-generated scripts."""

    def test_import_cad_aero_allowed(self):
        from compiler import _check_ast_security

        # None == no security violation == allowed.
        self.assertIsNone(_check_ast_security("import cad_aero"))

    def test_from_cad_aero_import_allowed(self):
        from compiler import _check_ast_security

        self.assertIsNone(_check_ast_security("from cad_aero import make_spar"))

    def test_unrelated_module_still_blocked(self):
        # Guard against an over-broad allowlist edit.
        from compiler import _check_ast_security

        self.assertIsNotNone(_check_ast_security("import os"))


# ---------------------------------------------------------------------------
# A0 — _validate_solid + frozen frame (cadquery-gated)
# ---------------------------------------------------------------------------
@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestValidateSolid(unittest.TestCase):
    def test_good_box_passes_and_returns_obj(self):
        import cad_aero

        box = cq.Workplane("XY").box(1.0, 1.0, 1.0)
        self.assertIs(cad_aero._validate_solid(box, "unit_box"), box)

    def test_none_raises(self):
        import cad_aero

        with self.assertRaises(cad_aero.CadAeroError):
            cad_aero._validate_solid(None, "nothing")

    def test_absurd_bounding_box_raises(self):
        import cad_aero

        # 100 m chord blows the 50 m envelope guard — a sign the builder ran away.
        big = cq.Workplane("XY").box(100.0, 1.0, 1.0)
        with self.assertRaises(cad_aero.CadAeroError):
            cad_aero._validate_solid(big, "runaway")


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestFrozenFrame(unittest.TestCase):
    def test_frame_axes_documented(self):
        import cad_aero

        self.assertEqual(cad_aero._FRAME["span_axis"], "Y")
        self.assertEqual(cad_aero._FRAME["chord_axis"], "X")
        self.assertEqual(cad_aero._FRAME["thickness_axis"], "Z")

    def test_dedupe_removes_consecutive_and_wraparound(self):
        import cad_aero

        pts = [(0.0, 0.0), (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
        out = cad_aero._dedupe_pts(pts)
        # consecutive LE dup removed AND wrap-around dup removed
        self.assertEqual(out, [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
