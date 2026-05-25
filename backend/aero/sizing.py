"""Conceptual wing sizing — Module 4 (Raymer)."""
from __future__ import annotations
import math
from dataclasses import dataclass
from .isa import isa
from .regime import WingSpec


@dataclass
class WingSizing:
    S: float            # reference area m²
    b: float            # span m
    MAC: float          # mean aerodynamic chord m
    root_chord: float   # m
    tip_chord: float    # m
    W_wing: float       # Raymer wing weight estimate kg
    CL_cruise: float
    CD_cruise: float    # rough estimate
    LD: float           # L/D


def size_wing(
    spec: WingSpec,
    CL_cruise: float = 0.4,
    AR: float | None = None,
    taper: float | None = None,
    twist_tip: float = -3.0,
    CD0: float = 0.02,
    e: float = 0.85,
) -> WingSizing:
    """Raymer-style conceptual sizing."""
    AR = AR if AR is not None else spec.AR
    taper = taper if taper is not None else spec.taper

    atm = isa(spec.cruise_alt)
    W = spec.MTOW * 9.80665   # N
    q = 0.5 * atm.rho * spec.cruise_V**2

    S = W / (q * CL_cruise)
    b = math.sqrt(AR * S)
    MAC = (2 / 3) * (S / b) * (1 + taper + taper**2) / (1 + taper)
    root_chord = 2 * S / (b * (1 + taper))
    tip_chord = taper * root_chord

    # Raymer Eq 15.1 simplified (transport category)
    # W_wing/W_TO = 0.0051*(W_TO*n_ult)^0.557 * S^0.649 * AR^0.5 * (t/c)^-0.4 * (1+taper)^0.1
    t_c = 0.12   # assume 12% if not computed from polar
    n_ult = 1.5 * spec.load_factors[0]
    W_TO_lb = spec.MTOW * 2.20462
    S_ft2 = S * 10.7639
    W_wing_lb = (0.0051 * (W_TO_lb * n_ult) ** 0.557
                 * S_ft2 ** 0.649
                 * AR ** 0.5
                 * t_c ** -0.4
                 * (1 + taper) ** 0.1)
    W_wing = W_wing_lb / 2.20462

    CDi = CL_cruise**2 / (math.pi * AR * e)
    CD = CD0 + CDi
    LD = CL_cruise / CD

    return WingSizing(
        S=S, b=b, MAC=MAC,
        root_chord=root_chord, tip_chord=tip_chord,
        W_wing=W_wing,
        CL_cruise=CL_cruise,
        CD_cruise=CD,
        LD=LD,
    )
