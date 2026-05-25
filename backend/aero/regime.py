"""Regime guard — Module 1."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
from .isa import isa


@dataclass
class WingSpec:
    airfoil: str                          # e.g. "NACA2412" or path to .dat
    MTOW: float                           # kg
    cruise_V: float                       # m/s TAS
    cruise_alt: float                     # m
    chord: float                          # mean aerodynamic chord (m)
    AR: float = 8.0
    taper: float = 0.45
    load_factors: tuple = (2.5, -1.0)    # (n_pos, n_neg)
    mission: str = "cruise"
    naca_cambered: bool = True


class RegimeError(ValueError):
    pass


def validate_regime(spec: WingSpec, method: str = "xfoil") -> tuple[float, float]:
    """
    Return (M_cruise, Re_cruise) or raise RegimeError.
    Guards:
      - XFOIL only valid for M <= 0.7
      - Cambered airfoil + M > 1.0 → reject (no subsonic assumption)
    """
    atm = isa(spec.cruise_alt)
    M = spec.cruise_V / atm.a
    Re = atm.rho * spec.cruise_V * spec.chord / atm.mu

    if method == "xfoil" and M > 0.7:
        raise RegimeError(
            f"XFOIL only valid for M <= 0.7; got M={M:.3f} at {spec.cruise_alt} m. "
            "Use a compressibility correction or switch method='avl'."
        )
    if spec.naca_cambered and M > 1.0:
        raise RegimeError(
            f"Cambered airfoil invalid for M > 1.0 (got M={M:.3f}). "
            "Use a symmetric supersonic profile."
        )
    return M, Re
