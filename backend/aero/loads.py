"""V-n diagram and distributed loads integration (shear, BM, torsion)."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
from .isa import isa
from .regime import WingSpec
from .sizing import WingSizing
from .avl import SpanwiseLoads


@dataclass
class VnDiagram:
    VA: float    # maneuver / corner speed m/s
    VC: float    # cruise speed m/s
    VD: float    # dive speed m/s
    n_max: float
    n_min: float
    rho: float


@dataclass
class LoadCase:
    y: np.ndarray
    w_aero: np.ndarray       # N/m lift (at load factor n)
    w_inertia: np.ndarray    # N/m inertial relief (positive = down)
    w_net: np.ndarray        # N/m net upward load
    V_shear: np.ndarray      # N shear force (root = max)
    BM: np.ndarray           # N·m bending moment (root = max)
    torsion: np.ndarray      # N·m torsion (root = max)
    n: float
    ultimate: bool = True


def compute_vn(
    spec: WingSpec,
    sizing: WingSizing,
    polar_cl_max: float,
) -> VnDiagram:
    atm = isa(spec.cruise_alt)
    W = spec.MTOW * 9.80665
    n_max = max(spec.load_factors)
    # Maneuver speed (corner speed)
    VA = math.sqrt(2 * n_max * W / (atm.rho * sizing.S * polar_cl_max))
    VC = spec.cruise_V
    VD = 1.4 * VC   # dive speed (simplified, FAR 23 §23.335)
    return VnDiagram(
        VA=VA, VC=VC, VD=VD,
        n_max=n_max, n_min=min(spec.load_factors),
        rho=atm.rho,
    )


def compute_loads(
    spanwise: SpanwiseLoads,
    sizing: WingSizing,
    spec: WingSpec,
    n: float,
    m_struct_kg_per_m: Optional[float] = None,
    m_fuel_kg_per_m: Optional[float] = None,
    x_ac_frac: float = 0.25,
    x_sc_frac: float = 0.40,
) -> LoadCase:
    y = spanwise.y
    L_y = spanwise.L_y * n   # scale to load factor
    b2 = sizing.b / 2
    c_y = sizing.c_root + (sizing.c_tip - sizing.c_root) * y / b2

    if m_struct_kg_per_m is None:
        m_struct_kg_per_m = sizing.W_wing_raymer / sizing.b
    if m_fuel_kg_per_m is None:
        m_fuel_kg_per_m = 0.0

    w_inertia = n * (m_struct_kg_per_m + m_fuel_kg_per_m) * 9.80665
    w_net = L_y - w_inertia

    # Integrate tip → root for shear and bending moment
    V_shear = np.array([np.trapz(w_net[i:], y[i:]) for i in range(len(y))])
    BM = np.array([np.trapz(V_shear[i:], y[i:]) for i in range(len(y))])

    # Torsion: lift force offset from shear centre
    atm = isa(spec.cruise_alt)
    moment_arm = (x_ac_frac - x_sc_frac) * c_y
    torsion_dist = L_y * moment_arm
    torsion = np.array([np.trapz(torsion_dist[i:], y[i:]) for i in range(len(y))])

    return LoadCase(
        y=y,
        w_aero=L_y,
        w_inertia=np.full_like(y, w_inertia),
        w_net=w_net,
        V_shear=V_shear,
        BM=BM,
        torsion=torsion,
        n=n,
    )
