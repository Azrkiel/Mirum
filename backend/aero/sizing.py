"""Conceptual wing sizing (Raymer Chapter 6 methods)."""
from __future__ import annotations
from dataclasses import dataclass
import math
from .isa import isa
from .regime import WingSpec


@dataclass
class WingSizing:
    """All outputs: wing-level scalars for a given WingSpec."""
    S: float          # wing area m2
    b: float          # span m
    AR: float
    c_root: float     # root chord m
    c_tip: float      # tip chord m
    MAC: float        # mean aerodynamic chord m
    taper: float
    twist_root: float # deg
    twist_tip: float  # deg (negative = washout)
    CL_cruise: float
    W_wing_raymer: float  # kg, Raymer statistical estimate


def size_wing(
    spec: WingSpec,
    CL_cruise: float = 0.4,
    AR: float = 8.0,
    taper: float = 0.45,
    twist_tip: float = -3.0,
) -> WingSizing:
    atm = isa(spec.cruise_alt)
    W = spec.MTOW * 9.80665
    V = spec.cruise_V

    # Wing area from cruise lift balance: L = W
    S = 2 * W / (atm.rho * V**2 * CL_cruise)
    b = math.sqrt(AR * S)
    c_root = 2 * S / (b * (1 + taper))
    c_tip = taper * c_root
    MAC = (2 / 3) * c_root * (1 + taper + taper**2) / (1 + taper)

    # Raymer wing weight (Eq 15.25): W in lb, S in ft2
    W_lb = spec.MTOW * 2.20462
    S_ft2 = S * 10.7639
    n_ult = max(spec.load_factors) * 1.5
    t_c = 0.12  # assumed NACA 4-digit mean
    sweep_cos = math.cos(math.radians(0))  # unswept
    W_wing_lb = (
        0.036
        * S_ft2**0.758
        * (AR / sweep_cos**2)**0.6
        * (n_ult * W_lb)**0.49
        / (100 * t_c / sweep_cos)**0.4
    )
    W_wing_kg = W_wing_lb / 2.20462

    # Sanity check: recompute CL from sized area
    CL_check = 2 * W / (atm.rho * V**2 * S)
    assert abs(CL_check - CL_cruise) < 0.01, (
        f"CL mismatch: {CL_check:.4f} vs {CL_cruise:.4f}"
    )

    return WingSizing(
        S=S, b=b, AR=AR,
        c_root=c_root, c_tip=c_tip, MAC=MAC,
        taper=taper, twist_root=0.0, twist_tip=twist_tip,
        CL_cruise=CL_cruise, W_wing_raymer=W_wing_kg,
    )
