"""ISA standard atmosphere — Module 0."""
from __future__ import annotations
from dataclasses import dataclass
import math

R = 287.05
g = 9.80665
GAMMA = 1.4
T0 = 288.15
P0 = 101325.0
L_TROP = 0.0065
H_TROP = 11000.0
T_STRAT = 216.65


@dataclass
class AtmState:
    h: float
    T: float
    P: float
    rho: float
    a: float
    mu: float
    nu: float


def isa(h: float) -> AtmState:
    """Return ISA atmosphere state at geometric altitude h (m)."""
    if h < 0:
        raise ValueError(f"Altitude must be >= 0, got {h}")
    if h <= H_TROP:
        T = T0 - L_TROP * h
        P = P0 * (T / T0) ** (g / (R * L_TROP))
    elif h <= 20000.0:
        T = T_STRAT
        P_trop = P0 * (T_STRAT / T0) ** (g / (R * L_TROP))
        P = P_trop * math.exp(-g * (h - H_TROP) / (R * T_STRAT))
    else:
        raise ValueError(f"Altitude {h} m above supported range (0-20000 m)")
    rho = P / (R * T)
    a = math.sqrt(GAMMA * R * T)
    # Sutherland viscosity
    C1 = 1.458e-6
    S = 110.4
    mu = C1 * T**1.5 / (T + S)
    nu = mu / rho
    return AtmState(h=h, T=T, P=P, rho=rho, a=a, mu=mu, nu=nu)


def density_altitude(rho: float) -> float:
    """Inverse: find h given rho (simple iteration in troposphere)."""
    lo, hi = 0.0, 11000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if isa(mid).rho > rho:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
