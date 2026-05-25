"""V-n diagram and structural load cases — Module 6."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from .isa import isa
from .regime import WingSpec
from .sizing import WingSizing
from .avl import SpanwiseLoads


@dataclass
class VnDiagram:
    n_pos_limit: float
    n_neg_limit: float
    V_A: float      # corner speed m/s (maneuver)
    V_C: float      # cruise speed m/s
    V_D: float      # dive speed m/s
    V_S: float      # stall speed m/s
    gust_n_pos: float
    gust_n_neg: float


@dataclass
class LoadCase:
    name: str
    n: float                        # load factor
    y: list[float]                  # spanwise stations
    shear: list[float]              # N
    bending: list[float]            # N·m
    torsion: list[float]            # N·m
    w_net: list[float]              # net distributed load N/m


def compute_vn(spec: WingSpec, sizing: WingSizing, polar_cl_max: float = 1.4) -> VnDiagram:
    """FAR 25 / CS25 V-n diagram basics."""
    atm = isa(spec.cruise_alt)
    rho = atm.rho
    W = spec.MTOW * 9.80665
    S = sizing.S

    V_S = math.sqrt(2 * W / (rho * S * polar_cl_max))
    n_pos = spec.load_factors[0]
    n_neg = spec.load_factors[1]
    V_A = V_S * math.sqrt(n_pos)
    V_C = spec.cruise_V
    V_D = 1.25 * V_C

    # FAR 25.341 gust loads (simplified)
    U_de = 15.24   # m/s at cruise alt (50 ft/s)
    mu_g = 2 * (spec.MTOW / sizing.S) / (rho * spec.chord * 2 * math.pi * 1.0)
    K_g = 0.88 * mu_g / (5.3 + mu_g)
    delta_n = K_g * rho * V_C * U_de * math.pi * 1.0 * S / (2 * W)
    gust_n_pos = 1.0 + delta_n
    gust_n_neg = 1.0 - delta_n

    return VnDiagram(
        n_pos_limit=n_pos, n_neg_limit=n_neg,
        V_A=V_A, V_C=V_C, V_D=V_D, V_S=V_S,
        gust_n_pos=gust_n_pos, gust_n_neg=gust_n_neg,
    )


def _integrate_loads(y: list[float], w_net: list[float]) -> tuple[list[float], list[float]]:
    """Integrate distributed net load tip-to-root for shear and bending."""
    n = len(y)
    shear = [0.0] * n
    bending = [0.0] * n
    # tip is index n-1, root is index 0
    for i in range(n - 2, -1, -1):
        dy = y[i + 1] - y[i]
        w_avg = 0.5 * (w_net[i] + w_net[i + 1])
        shear[i] = shear[i + 1] + w_avg * dy
        bending[i] = bending[i + 1] + shear[i + 1] * dy + 0.5 * w_avg * dy**2
    return shear, bending


def compute_loads(
    spanwise: SpanwiseLoads,
    sizing: WingSizing,
    spec: WingSpec,
    n: float,
    m_struct_frac: float = 0.12,
    m_fuel_frac: float = 0.15,
) -> LoadCase:
    """
    Compute shear, bending, torsion for one load factor n.
    w_net = L(y) - n*(m_struct + m_fuel) * g / half_span
    Torsion = L * (0.25c - 0.40c) = -0.15 * L * c  (aero centre vs shear centre)
    """
    g = 9.80665
    half_span = sizing.b / 2
    m_struct = spec.MTOW * m_struct_frac
    m_fuel = spec.MTOW * m_fuel_frac
    inertia_per_m = n * (m_struct + m_fuel) * g / half_span

    w_net = [lpm - inertia_per_m for lpm in spanwise.L_per_m]
    shear, bending = _integrate_loads(spanwise.y, w_net)

    # Torsion: aero centre at 0.25c, shear centre at 0.40c → moment arm = 0.15c
    torsion = [0.0] * len(spanwise.y)
    for i in range(len(spanwise.y) - 2, -1, -1):
        dy = spanwise.y[i + 1] - spanwise.y[i]
        tau_i = spanwise.L_per_m[i] * spanwise.chord[i] * 0.15
        tau_i1 = spanwise.L_per_m[i + 1] * spanwise.chord[i + 1] * 0.15
        torsion[i] = torsion[i + 1] + 0.5 * (tau_i + tau_i1) * dy

    return LoadCase(
        name=f"n={n:.2f}",
        n=n,
        y=spanwise.y,
        shear=shear,
        bending=bending,
        torsion=torsion,
        w_net=w_net,
    )
