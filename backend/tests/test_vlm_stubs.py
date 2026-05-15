"""
T3-09 tests: VLM Zebra Rendering Pipeline stubs.

Tests cover:
  1. ``rendering.render_zebra()`` — synthetic fallback always produces a PNG
  2. ``rendering.is_rendering_available()`` — returns bool without crashing
  3. ``rendering._write_minimal_png()`` — stdlib-only fallback PNG is valid
  4. ``continuity_check.check_g2_vlm_zebra()`` — mocked Gemini Vision; pass and fail paths
  5. ``geometry_analysis.check_curvature_via_vlm()`` — mocked Gemini Vision; severity mapping
  6. Integration: both stubs no longer return the old stub sentinels
     ("vlm_zebra_stub", "vlm_stub") — they reach the rendering or API path

All tests run without a display, a Gemini API key, or pyrender installed.
The MIRUM_NO_PYRENDER=1 env var forces the synthetic fallback in all tests.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# Force synthetic fallback path — no pyrender needed in test env
os.environ.setdefault("MIRUM_NO_PYRENDER", "1")

# Ensure backend/ is on sys.path
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ---------------------------------------------------------------------------
# Inject a stub google.generativeai into sys.modules so tests run without
# the real google-generativeai package installed (it's only in Docker).
# The real production code does a lazy ``import google.generativeai as genai``
# inside function bodies, so the stub is resolved at call time.
# ---------------------------------------------------------------------------

def _make_genai_stub():
    """Return a MagicMock configured to look like google.generativeai."""
    stub = MagicMock()
    stub.GenerativeModel = MagicMock  # replaced per-test as needed
    return stub


if "google.generativeai" not in sys.modules:
    import types as _types

    _google_pkg = sys.modules.get("google")
    if _google_pkg is None:
        _google_pkg = _types.ModuleType("google")
        _google_pkg.__path__ = []  # namespace package
        sys.modules["google"] = _google_pkg

    _genai_stub = _make_genai_stub()
    sys.modules["google.generativeai"] = _genai_stub
    _google_pkg.generativeai = _genai_stub  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_vision_response(data: dict) -> MagicMock:
    """Return a mock that looks like a Gemini GenerateContentResponse."""
    resp = MagicMock()
    resp.text = json.dumps(data)
    return resp


def _minimal_png_path(tmp_dir: str, name: str = "test.png") -> str:
    """Return path to a tiny valid PNG written to tmp_dir."""
    from rendering import _write_minimal_png
    path = os.path.join(tmp_dir, name)
    _write_minimal_png(path, 8, 8)
    return path


# ---------------------------------------------------------------------------
# 1. rendering module
# ---------------------------------------------------------------------------

class TestRenderingModule(unittest.TestCase):

    def test_is_rendering_available_returns_bool(self):
        """is_rendering_available() must return a bool without raising."""
        # Reset cached value so the env var is re-read
        import rendering as rmod
        rmod._PYRENDER_OK = None
        result = rmod.is_rendering_available()
        self.assertIsInstance(result, bool)
        # With MIRUM_NO_PYRENDER=1 it must be False
        self.assertFalse(result)

    def test_render_zebra_synthetic_fallback_produces_png(self):
        """render_zebra() must write a valid PNG even without pyrender."""
        from rendering import render_zebra
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "zebra.png")
            returned = render_zebra(
                "/nonexistent/step.step",  # invalid path — triggers fallback
                out_path,
                n_stripes=10,
                img_width=64,
                img_height=48,
            )
            self.assertEqual(returned, out_path)
            self.assertTrue(os.path.isfile(out_path), "PNG file should exist")
            size = os.path.getsize(out_path)
            self.assertGreater(size, 0, "PNG should be non-empty")
            # Verify PNG magic bytes
            with open(out_path, "rb") as f:
                magic = f.read(8)
            self.assertEqual(magic, b"\x89PNG\r\n\x1a\n", "File should be a valid PNG")

    def test_render_zebra_uses_imageio_when_available(self):
        """Synthetic renderer writes a proper PNG using imageio (if available)."""
        try:
            import imageio  # noqa: F401
            import numpy as np  # noqa: F401
        except ImportError:
            self.skipTest("imageio/numpy not installed — synthetic PNG unavailable")

        from rendering import _render_synthetic
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "synthetic.png")
            _render_synthetic(out_path, n_stripes=20, img_width=80, img_height=60)
            self.assertTrue(os.path.isfile(out_path))
            with open(out_path, "rb") as f:
                self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

    def test_write_minimal_png_is_stdlib_only(self):
        """_write_minimal_png() must produce a valid PNG using no third-party libs."""
        from rendering import _write_minimal_png
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "minimal.png")
            _write_minimal_png(path, 16, 12)
            with open(path, "rb") as f:
                data = f.read()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", "Should be a valid PNG")
            self.assertGreater(len(data), 50, "PNG should have IHDR + IDAT + IEND")

    def test_render_zebra_stripe_count_parameter(self):
        """render_zebra() with different n_stripes still produces a PNG."""
        from rendering import render_zebra
        with tempfile.TemporaryDirectory() as tmp:
            for n in (5, 20, 50):
                out_path = os.path.join(tmp, f"zebra_{n}.png")
                render_zebra("/nonexistent.step", out_path, n_stripes=n,
                             img_width=32, img_height=32)
                self.assertTrue(os.path.isfile(out_path), f"n_stripes={n}: PNG missing")


# ---------------------------------------------------------------------------
# 2. continuity_check.check_g2_vlm_zebra()
# ---------------------------------------------------------------------------

class TestCheckG2VlmZebra(unittest.IsolatedAsyncioTestCase):

    async def _call_with_mock_vision(self, vision_response: dict) -> "G2CheckResult":  # type: ignore[name-defined]
        """Run check_g2_vlm_zebra() with a mocked Gemini Vision response."""
        from continuity_check import check_g2_vlm_zebra

        mock_resp = _make_mock_vision_response(vision_response)
        mock_model = AsyncMock()
        mock_model.generate_content_async.return_value = mock_resp

        # Patch GenerativeModel on the stub module already in sys.modules
        sys.modules["google.generativeai"].GenerativeModel = MagicMock(return_value=mock_model)

        with patch("rendering.render_zebra") as mock_render:
            # Make render_zebra write a real minimal PNG
            def _fake_render(step_path, out_path, **kw):
                from rendering import _write_minimal_png
                _write_minimal_png(out_path, 8, 8)
                return out_path

            mock_render.side_effect = _fake_render
            # Patch asyncio.to_thread to call render synchronously
            with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                result = await check_g2_vlm_zebra("/fake/part.step")

        return result

    async def test_g2_pass_response(self):
        """VLM response g2_pass=true → G2CheckResult.passed=True, method='vlm_zebra'."""
        result = await self._call_with_mock_vision(
            {"g2_pass": True, "violation_description": ""}
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.method, "vlm_zebra")
        self.assertEqual(result.violation_count, 0)

    async def test_g2_fail_response(self):
        """VLM response g2_pass=false → G2CheckResult.passed=False with description."""
        result = await self._call_with_mock_vision(
            {"g2_pass": False, "violation_description": "Kink at front-to-side surface boundary"}
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.method, "vlm_zebra")
        self.assertEqual(result.violation_count, 1)
        self.assertIn("Kink", result.violation_descriptions[0])

    async def test_method_is_not_old_stub_sentinel(self):
        """check_g2_vlm_zebra() must NOT return method='vlm_zebra_stub'."""
        result = await self._call_with_mock_vision({"g2_pass": True, "violation_description": ""})
        self.assertNotEqual(result.method, "vlm_zebra_stub",
                            "Old stub sentinel should no longer appear")

    async def test_render_failure_returns_pass_not_raises(self):
        """If render_zebra raises, check_g2_vlm_zebra must return passed=True, never raise."""
        from continuity_check import check_g2_vlm_zebra

        with patch("rendering.render_zebra", side_effect=RuntimeError("OSMesa missing")):
            with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                result = await check_g2_vlm_zebra("/fake/part.step")

        self.assertTrue(result.passed)  # Must not fail the Critic Loop
        self.assertIn("render_failed", result.method)

    async def test_vision_api_failure_returns_pass_not_raises(self):
        """If Gemini Vision raises, check_g2_vlm_zebra must return passed=True, never raise."""
        from continuity_check import check_g2_vlm_zebra

        mock_model = AsyncMock()
        mock_model.generate_content_async.side_effect = RuntimeError("API quota exceeded")
        sys.modules["google.generativeai"].GenerativeModel = MagicMock(return_value=mock_model)

        with patch("rendering.render_zebra") as mock_render:
            def _fake_render(step_path, out_path, **kw):
                from rendering import _write_minimal_png
                _write_minimal_png(out_path, 8, 8)
                return out_path

            mock_render.side_effect = _fake_render
            with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                result = await check_g2_vlm_zebra("/fake/part.step")

        self.assertTrue(result.passed)
        self.assertIn("api_failed", result.method)


# ---------------------------------------------------------------------------
# 3. geometry_analysis.check_curvature_via_vlm()
# ---------------------------------------------------------------------------

class TestCheckCurvatureViaVlm(unittest.IsolatedAsyncioTestCase):

    async def _call_with_mock(
        self,
        vision_response: dict,
        image_path: str,
    ) -> "GaussianCurvatureResult":  # type: ignore[name-defined]
        """Run check_curvature_via_vlm() with a mocked Gemini Vision response."""
        from geometry_analysis import check_curvature_via_vlm

        mock_resp = _make_mock_vision_response(vision_response)
        mock_model = AsyncMock()
        mock_model.generate_content_async.return_value = mock_resp

        # Patch on the stub module already in sys.modules
        sys.modules["google.generativeai"].GenerativeModel = MagicMock(return_value=mock_model)
        result = await check_curvature_via_vlm(image_path, "ergonomic grip handle")
        return result

    async def test_severity_0_is_pass(self):
        """Severity 0 → GaussianCurvatureResult.passed=True, method='vlm_zebra'."""
        with tempfile.TemporaryDirectory() as tmp:
            img_path = _minimal_png_path(tmp)
            result = await self._call_with_mock(
                {"severity": 0, "regions": [], "feedback": "Smooth and uniform stripes."},
                img_path,
            )
        self.assertTrue(result.passed)
        self.assertEqual(result.method, "vlm_zebra")

    async def test_severity_1_is_pass(self):
        """Severity 1 → still passes (minor variation only)."""
        with tempfile.TemporaryDirectory() as tmp:
            img_path = _minimal_png_path(tmp)
            result = await self._call_with_mock(
                {"severity": 1, "regions": ["slight undulation on top surface"],
                 "feedback": "Minor waviness."},
                img_path,
            )
        self.assertTrue(result.passed)

    async def test_severity_2_is_fail(self):
        """Severity 2 → GaussianCurvatureResult.passed=False."""
        with tempfile.TemporaryDirectory() as tmp:
            img_path = _minimal_png_path(tmp)
            result = await self._call_with_mock(
                {"severity": 2, "regions": ["saddle region near the grip"],
                 "feedback": "Noticeable stripe waviness on main grip surface."},
                img_path,
            )
        self.assertFalse(result.passed)
        self.assertEqual(result.method, "vlm_zebra")
        self.assertGreater(len(result.saddle_regions), 0)

    async def test_severity_3_is_fail_with_feedback(self):
        """Severity 3 → fail, feedback includes fix hint."""
        with tempfile.TemporaryDirectory() as tmp:
            img_path = _minimal_png_path(tmp)
            result = await self._call_with_mock(
                {"severity": 3, "regions": ["top-to-side boundary"],
                 "feedback": "Sharp kink between top dome and lateral wall."},
                img_path,
            )
        self.assertFalse(result.passed)
        self.assertIn("loft", result.feedback_message.lower())  # fix hint injected

    async def test_method_not_old_stub_sentinel(self):
        """check_curvature_via_vlm() must NOT return method='vlm_stub'."""
        with tempfile.TemporaryDirectory() as tmp:
            img_path = _minimal_png_path(tmp)
            result = await self._call_with_mock(
                {"severity": 0, "regions": [], "feedback": "OK"},
                img_path,
            )
        self.assertNotEqual(result.method, "vlm_stub",
                            "Old stub sentinel should no longer appear")

    async def test_missing_image_returns_pass_not_raises(self):
        """If the image file doesn't exist, return passed=True, never raise."""
        from geometry_analysis import check_curvature_via_vlm
        result = await check_curvature_via_vlm("/nonexistent/image.png", "some part")
        self.assertTrue(result.passed)
        self.assertIn("missing", result.method)

    async def test_vision_api_failure_returns_pass_not_raises(self):
        """If Gemini Vision raises, return passed=True (non-blocking)."""
        from geometry_analysis import check_curvature_via_vlm

        mock_model = AsyncMock()
        mock_model.generate_content_async.side_effect = RuntimeError("network error")
        sys.modules["google.generativeai"].GenerativeModel = MagicMock(return_value=mock_model)

        with tempfile.TemporaryDirectory() as tmp:
            img_path = _minimal_png_path(tmp)
            result = await check_curvature_via_vlm(img_path, "part description")

        self.assertTrue(result.passed)
        self.assertIn("api_failed", result.method)


# ---------------------------------------------------------------------------
# 4. Integration: check_g2_continuity() falls back to VLM when OCCT missing
# ---------------------------------------------------------------------------

class TestG2ContinuityFallbackIntegration(unittest.TestCase):

    def test_check_g2_continuity_skips_non_c_domain(self):
        """Non-Domain-C parts must return passed=True immediately."""
        from continuity_check import check_g2_continuity
        for domain in ("A", "B", "D", "E"):
            result = check_g2_continuity("/fake/part.step", domain=domain)
            self.assertTrue(result.passed, f"domain={domain} should pass immediately")
            self.assertEqual(result.method, "skipped")

    def test_check_g2_continuity_returns_g2checkresult(self):
        """check_g2_continuity() must return a G2CheckResult without raising."""
        from continuity_check import check_g2_continuity, G2CheckResult
        # With no valid STEP file and no OCC, it should return a skipped result
        result = check_g2_continuity("/nonexistent/part.step", domain="C")
        self.assertIsInstance(result, G2CheckResult)
        self.assertIsInstance(result.passed, bool)


# ---------------------------------------------------------------------------
# 5. rendering._normals_to_zebra() unit test (when numpy available)
# ---------------------------------------------------------------------------

class TestNormalsToZebra(unittest.TestCase):

    def test_white_pixels_on_forward_facing_normal(self):
        """A pixel with normal pointing straight up (green=255) should be white."""
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not available")

        from rendering import _normals_to_zebra

        # Create a 10×10 image where green=255 (normal_y = +1.0)
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        img[:, :, 1] = 255  # green channel = 1.0 → normal_y = 1.0

        zebra = _normals_to_zebra(img, n_stripes=10)
        # normal_y=1.0, n_stripes=10 → stripe_idx = int(1.0 * 10) = 10 → 10%2=0 → white
        self.assertEqual(tuple(zebra[5, 5]), (255, 255, 255))

    def test_background_pixels_are_grey(self):
        """Achromatic pixels (R=G=B) should be treated as background (grey)."""
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not available")

        from rendering import _normals_to_zebra

        img = np.full((10, 10, 3), 128, dtype=np.uint8)  # all grey
        zebra = _normals_to_zebra(img, n_stripes=10)
        self.assertEqual(tuple(zebra[5, 5]), (128, 128, 128))

    def test_output_shape_matches_input(self):
        """Output image has same H×W as input."""
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not available")

        from rendering import _normals_to_zebra

        h, w = 48, 64
        img = np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)
        # Make non-achromatic
        img[:, :, 0] = np.clip(img[:, :, 0].astype(int) + 20, 0, 255).astype(np.uint8)
        zebra = _normals_to_zebra(img, n_stripes=15)
        self.assertEqual(zebra.shape, (h, w, 3))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
