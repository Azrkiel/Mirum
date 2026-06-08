"""Unit tests for the Phase-6 V-n diagram builder (headless, Agg).

Run: python frontend/test_vn_diagram.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # frontend/ (vn_diagram)

from vn_diagram import vn_diagram_figure  # noqa: E402

_SAMPLE = {
    "v_stall_ms": 28.0,
    "v_corner_ms": 28.0 * (3.8 ** 0.5),   # V_A = Vs*sqrt(n)
    "v_ne_ms": 28.0 * 1.4 * (3.8 ** 0.5),
    "n_maneuver_pos": 3.8,
    "n_maneuver_neg": -1.5,
    "n_gust_pos": 3.2,
    "n_gust_neg": -1.0,
    "n_ultimate_pos": 5.7,
}


class TestVnDiagram(unittest.TestCase):
    def test_builds_figure_with_axes_and_curves(self):
        fig = vn_diagram_figure(_SAMPLE)
        self.assertEqual(len(fig.axes), 1)
        ax = fig.axes[0]
        self.assertEqual(ax.get_xlabel(), "Airspeed V (m/s)")
        self.assertEqual(ax.get_ylabel(), "Load factor n")
        # maneuver-envelope outline + V_NE wall + 2 gust lines -> several Line2D
        self.assertGreaterEqual(len(ax.lines), 4)
        # x extends to V_NE, y spans negative..positive limit
        self.assertGreater(ax.get_xlim()[1], _SAMPLE["v_ne_ms"] * 0.9)
        self.assertLess(ax.get_ylim()[0], 0.0)
        self.assertGreater(ax.get_ylim()[1], 3.0)
        import matplotlib.pyplot as plt
        plt.close(fig)

    def test_stall_parabola_passes_through_corner_point(self):
        # at V_A the positive boundary should equal n_maneuver_pos: (V_A/Vs)^2
        vs, n = _SAMPLE["v_stall_ms"], _SAMPLE["n_maneuver_pos"]
        va = _SAMPLE["v_corner_ms"]
        self.assertAlmostEqual((va / vs) ** 2, n, places=6)

    def test_insufficient_data_returns_placeholder_figure(self):
        fig = vn_diagram_figure({"v_stall_ms": 0, "v_ne_ms": 0})
        self.assertEqual(len(fig.axes), 1)
        # placeholder path draws no envelope lines
        self.assertEqual(len(fig.axes[0].lines), 0)
        import matplotlib.pyplot as plt
        plt.close(fig)


if __name__ == "__main__":
    unittest.main(verbosity=2)
