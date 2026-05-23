"""
backend/tests/test_pdf_report.py — Smoke tests for analysis/pdf_report.py (PDF-05).

Tests
-----
  TestGenerateXfoilPdf
      smoke_minimal       — 3-point polar, no Cp chart → valid PDF bytes
      smoke_with_cp       — 3-point polar + Cp chart → valid PDF bytes
      output_is_bytes     — return type is bytes
      output_non_trivial  — len > 1000 (not an empty stub)
      output_pdf_header   — starts with b"%PDF"
      handles_none_cp     — cp_dist_b64=None does not raise
      handles_none_optionals — stall_alpha=None, Cl_max=None → no crash
      large_polar         — 50-point polar → paginated table, still valid PDF
"""

from __future__ import annotations

import base64
import math
import struct
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup: analysis/ lives at repo root (parent of backend/)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Check that fpdf2 is available; skip all tests gracefully if not installed
# ---------------------------------------------------------------------------

try:
    import fpdf  # noqa: F401
    FPDF2_AVAILABLE = True
except ImportError:
    FPDF2_AVAILABLE = False


# ---------------------------------------------------------------------------
# Minimal 1×1 white PNG as base64 (valid PNG, 68 bytes)
# ---------------------------------------------------------------------------

def _white_1x1_png_b64() -> str:
    """Return a base64-encoded 1×1 white PNG for use as a chart stub."""
    # Hand-crafted minimal PNG: IHDR + IDAT + IEND
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        body = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        return length + body + crc

    png_sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1×1 RGB
    ihdr = _chunk(b"IHDR", ihdr_data)
    # One scanline: filter byte 0 + RGB (255, 255, 255)
    raw_row = b"\x00\xff\xff\xff"
    idat = _chunk(b"IDAT", zlib.compress(raw_row))
    iend = _chunk(b"IEND", b"")
    png_bytes = png_sig + ihdr + idat + iend
    return base64.b64encode(png_bytes).decode("ascii")


def _make_polar(n: int = 3) -> dict:
    """Return a minimal PolarReport dict with *n* alpha points."""
    alphas = [float(i) for i in range(n)]
    Cl = [0.05 * a for a in alphas]
    Cd = [0.01 + 0.001 * a * a for a in alphas]
    Cm = [-0.02 - 0.001 * a for a in alphas]
    L_D = [cl / cd for cl, cd in zip(Cl, Cd)]
    return {
        "naca_code": "0012",
        "chord_mm": 200.0,
        "reynolds": 1_000_000.0,
        "mach": 0.0,
        "n_crit": 9.0,
        "alpha": alphas,
        "Cl": Cl,
        "Cd": Cd,
        "Cm": Cm,
        "L_D": L_D,
        "converged_count": n,
        "stall_alpha": float(n - 1),
        "Cl_max": max(Cl),
    }


@unittest.skipUnless(FPDF2_AVAILABLE, "fpdf2 not installed — skip PDF smoke tests")
class TestGenerateXfoilPdf(unittest.TestCase):

    def setUp(self):
        from analysis.pdf_report import generate_xfoil_pdf
        self._gen = generate_xfoil_pdf
        self._b64 = _white_1x1_png_b64()
        self._polar = _make_polar(3)

    # ── Core contract ────────────────────────────────────────

    def test_output_is_bytes(self):
        result = self._gen(self._polar, self._b64, self._b64, None)
        self.assertIsInstance(result, bytes)

    def test_output_pdf_header(self):
        result = self._gen(self._polar, self._b64, self._b64, None)
        self.assertEqual(result[:4], b"%PDF", "Output does not start with %PDF")

    def test_output_non_trivial(self):
        result = self._gen(self._polar, self._b64, self._b64, None)
        self.assertGreater(len(result), 1000, "PDF too small — likely an empty stub")

    # ── Cp handling ──────────────────────────────────────────

    def test_handles_none_cp(self):
        """cp_dist_b64=None must not raise and must still return valid PDF."""
        result = self._gen(self._polar, self._b64, self._b64, None)
        self.assertEqual(result[:4], b"%PDF")

    def test_smoke_with_cp(self):
        """Providing a Cp chart should also produce a valid PDF."""
        result = self._gen(self._polar, self._b64, self._b64, self._b64)
        self.assertEqual(result[:4], b"%PDF")
        self.assertGreater(len(result), 1000)

    # ── Edge cases ───────────────────────────────────────────

    def test_handles_none_optionals(self):
        """stall_alpha=None and Cl_max=None should not crash (n/a shown instead)."""
        polar = dict(self._polar)
        polar["stall_alpha"] = None
        polar["Cl_max"] = None
        result = self._gen(polar, self._b64, self._b64, None)
        self.assertEqual(result[:4], b"%PDF")

    def test_large_polar_paginated(self):
        """50-point polar triggers the paginated table path (>40 rows)."""
        polar = _make_polar(50)
        result = self._gen(polar, self._b64, self._b64, None)
        self.assertEqual(result[:4], b"%PDF")
        self.assertGreater(len(result), 1000)

    def test_polar_with_nan_values(self):
        """NaN/None in polar lists should not crash (non-finite values shown as —)."""
        polar = dict(self._polar)
        polar["Cd"] = [None, math.nan, 0.01]
        polar["L_D"] = [None, None, 50.0]
        result = self._gen(polar, self._b64, self._b64, None)
        self.assertEqual(result[:4], b"%PDF")


if __name__ == "__main__":
    unittest.main()
