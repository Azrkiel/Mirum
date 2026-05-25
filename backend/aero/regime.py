"""Flight regime classification and guard rules."""
from dataclasses import dataclass, field
from typing import Optional
from .isa import isa


@dataclass
class WingSpec:
    airfoil: str           # e.g. "NACA2412" or path to .dat
    MTOW: float            # kg
    cruise_V: float        # m/s TAS
    cruise_alt: float      # m
    chord: float           # m mean aerodynamic chord
    AR: float = 8.0
    taper: float = 0.45
    load_factors: tuple = (2.5, -1.0)
    mission: str = "cruise"
    naca_cambered: bool = True   # set False for symmetric


REGIMES = {
    "incompressible": (0.0, 0.3),
    "subsonic_compressible": (0.3, 0.8),
    "transonic": (0.8, 1.2),
    "supersonic": (1.2, 5.0),
    "hypersonic": (5.0, float("inf")),
}


def classify_regime(M: float) -> str:
    for name, (lo, hi) in REGIMES.items():
        if lo <= M < hi:
            return name
    return "hypersonic"


def compute_mach_re(spec: WingSpec) -> tuple:
    s = isa(spec.cruise_alt)
    M = spec.cruise_V / s.a
    Re = s.rho * spec.cruise_V * spec.chord / s.mu
    return M, Re, s


class RegimeError(ValueError):
    pass


class RegimeWarning(UserWarning):
    pass


def validate_regime(spec: WingSpec, method: str = "xfoil") -> tuple:
    M, Re, atm = compute_mach_re(spec)
    regime = classify_regime(M)
    if method.lower() == "xfoil" and M > 0.7:
        raise RegimeError(
            f"XFOIL invalid for M={M:.2f} (panel method requires M≤0.7). "
            "Use a transonic code."
        )
    if spec.naca_cambered and M >= 1.0:
        raise RegimeError(
            f"Cambered subsonic section invalid at M={M:.2f}. "
            "Requires sharp-LE section (wedge/biconvex)."
        )
    warnings = []
    if Re < 1e4 or Re > 1e8:
        warnings.append(
            f"Re={Re:.2e} outside typical airfoil data range [1e4, 1e8]"
        )
    return M, Re, regime, atm, warnings
