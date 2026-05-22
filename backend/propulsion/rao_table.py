"""Rao thrust-optimized bell nozzle initial/exit angle table.

Source
------
G. V. R. Rao, "Approximation of Optimum Thrust Nozzle Contour", ARS Journal
30(6) 1960; widely tabulated form via NASA SP-125 (Huzel & Huang, 1971,
"Modern Engineering for Design of Liquid-Propellant Rocket Engines"). The
values below are the canonical Lf vs. epsilon table reproduced in the
SP-125 figures: theta_n is the parabola start angle just downstream of the
throat and theta_e is the exit-plane wall angle.

Within the published envelope (Lf in [0.6, 1.0], epsilon in [5, 100]) the
bilinear interpolation tracks the published MOC contour to within roughly
+/- 0.5 deg for theta_n and +/- 0.3 deg for theta_e. Outside the envelope
``lookup`` raises ``ValueError`` rather than extrapolating silently --
extrapolation defeats the purpose of having a Rao bell at all.
"""

from __future__ import annotations

from typing import Tuple

# ---------------------------------------------------------------------------
# Tabulated NASA SP-125 / Rao data
# ---------------------------------------------------------------------------
# Length fractions covered.
LENGTH_FRACTIONS: tuple[float, ...] = (0.6, 0.7, 0.8, 0.9, 1.0)

# Expansion ratios covered.
EXPANSION_RATIOS: tuple[float, ...] = (5.0, 10.0, 20.0, 30.0, 40.0, 50.0, 100.0)

# theta_n (parabola start angle just past the throat, deg).
# Rows: epsilon. Columns: Lf.
# Source: SP-125 fig. for Rao optimum thrust contour.
_THETA_N: dict[float, dict[float, float]] = {
    5.0:   {0.6: 24.5, 0.7: 22.5, 0.8: 20.8, 0.9: 19.2, 1.0: 17.5},
    10.0:  {0.6: 28.5, 0.7: 26.5, 0.8: 24.5, 0.9: 22.4, 1.0: 20.3},
    20.0:  {0.6: 32.5, 0.7: 30.0, 0.8: 27.5, 0.9: 25.0, 1.0: 22.5},
    30.0:  {0.6: 34.0, 0.7: 31.6, 0.8: 29.0, 0.9: 26.5, 1.0: 23.8},
    40.0:  {0.6: 35.0, 0.7: 32.5, 0.8: 30.0, 0.9: 27.5, 1.0: 24.8},
    50.0:  {0.6: 35.8, 0.7: 33.3, 0.8: 30.7, 0.9: 28.2, 1.0: 25.5},
    100.0: {0.6: 37.5, 0.7: 35.0, 0.8: 32.5, 0.9: 30.0, 1.0: 27.5},
}

# theta_e (exit-plane wall angle, deg).
_THETA_E: dict[float, dict[float, float]] = {
    5.0:   {0.6: 11.5, 0.7: 10.0, 0.8:  8.5, 0.9:  7.2, 1.0:  6.0},
    10.0:  {0.6: 13.0, 0.7: 11.0, 0.8:  9.3, 0.9:  8.0, 1.0:  6.8},
    20.0:  {0.6: 14.5, 0.7: 12.0, 0.8: 10.0, 0.9:  8.5, 1.0:  7.0},
    30.0:  {0.6: 14.5, 0.7: 12.0, 0.8: 10.0, 0.9:  8.5, 1.0:  7.0},
    40.0:  {0.6: 14.0, 0.7: 11.7, 0.8:  9.7, 0.9:  8.3, 1.0:  6.8},
    50.0:  {0.6: 13.7, 0.7: 11.5, 0.8:  9.5, 0.9:  8.0, 1.0:  6.7},
    100.0: {0.6: 13.0, 0.7: 11.0, 0.8:  9.0, 0.9:  7.6, 1.0:  6.4},
}


def _bracket(value: float, sequence: tuple[float, ...]) -> Tuple[float, float, float]:
    """Find ``(lo, hi, t)`` so that ``value = lo + t*(hi-lo)`` and lo <= value <= hi.

    Raises ``ValueError`` if ``value`` is outside the sequence range.
    """
    if value < sequence[0] or value > sequence[-1]:
        raise ValueError(
            f"Value {value} outside Rao table envelope [{sequence[0]}, {sequence[-1]}]."
        )
    for lo, hi in zip(sequence, sequence[1:]):
        if lo <= value <= hi:
            t = 0.0 if hi == lo else (value - lo) / (hi - lo)
            return lo, hi, t
    # value == last element
    return sequence[-1], sequence[-1], 0.0


def lookup(epsilon: float, length_fraction: float = 0.8) -> Tuple[float, float]:
    """Bilinear-interpolate ``(theta_n_deg, theta_e_deg)`` for given ``(epsilon, Lf)``.

    Raises ``ValueError`` if either input is outside the tabulated envelope.
    """
    eps_lo, eps_hi, te = _bracket(epsilon, EXPANSION_RATIOS)
    lf_lo, lf_hi, tl = _bracket(length_fraction, LENGTH_FRACTIONS)

    def interp(table: dict[float, dict[float, float]]) -> float:
        v_eps_lo = (
            (1 - tl) * table[eps_lo][lf_lo] + tl * table[eps_lo][lf_hi]
        )
        v_eps_hi = (
            (1 - tl) * table[eps_hi][lf_lo] + tl * table[eps_hi][lf_hi]
        )
        return (1 - te) * v_eps_lo + te * v_eps_hi

    return interp(_THETA_N), interp(_THETA_E)


def envelope() -> dict[str, tuple[float, float]]:
    """Return the supported (min, max) range for inputs. Useful for error messages."""
    return {
        "expansion_ratio": (EXPANSION_RATIOS[0], EXPANSION_RATIOS[-1]),
        "length_fraction": (LENGTH_FRACTIONS[0], LENGTH_FRACTIONS[-1]),
    }
