"""Thin-walled wing box structural sizing (Bruhn/Niu methods)."""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from ..aero.loads import LoadCase
from ..aero.airfoil import NACAProfile


@dataclass
class Material:
    name: str
    E: float            # Pa Young's modulus
    G: float            # Pa shear modulus
    sigma_allow: float  # Pa tensile allowable (ultimate)
    tau_allow: float    # Pa shear allowable (ultimate)
    rho: float          # kg/m3


AL_2024_T3 = Material(
    name="2024-T3",
    E=73.1e9,
    G=28e9,
    sigma_allow=345e6,
    tau_allow=200e6,
    rho=2780.0,
)


@dataclass
class BoxSizing:
    """Structural sizing output arrays over spanwise stations y."""
    y: np.ndarray
    cap_area: np.ndarray         # m2 per spar cap
    skin_t_upper: np.ndarray     # m upper skin thickness
    skin_t_lower: np.ndarray     # m lower skin thickness
    web_t_front: np.ndarray      # m front spar web thickness
    web_t_rear: np.ndarray       # m rear spar web thickness
    rib_spacing: float            # m
    stringer_area: float          # m2 per stringer
    MS_bending: np.ndarray       # margin of safety (>0 = safe)
    MS_shear: np.ndarray
    MS_torsion: np.ndarray
    MS_buckling: np.ndarray


def size_box(
    load_case: LoadCase,
    airfoil: NACAProfile,
    chord_y: np.ndarray,
    mat: Material = AL_2024_T3,
    SF: float = 1.5,
    front_spar_frac: float = 0.15,
    rear_spar_frac: float = 0.60,
    rib_spacing: float = 0.3,
) -> BoxSizing:
    y = load_case.y
    # Ultimate loads (×1.5 already baked in for ultimate sizing)
    M = load_case.BM * SF
    V = load_case.V_shear * SF
    T = load_case.torsion * SF

    t_c = airfoil.t_c
    h_y = t_c * chord_y                              # box height (approx)
    b_box_y = (rear_spar_frac - front_spar_frac) * chord_y  # box width

    # --- Bending: I ≈ 2·A_cap·(h/2)², σ = M·(h/2)/I = M/(A·h) ---
    sigma_allow_sf = mat.sigma_allow / SF
    cap_area = np.maximum(1e-6, M / (h_y * sigma_allow_sf + 1e-12))
    sigma_applied = M / (cap_area * h_y + 1e-12)
    MS_bending = mat.sigma_allow / (SF * sigma_applied + 1e-12) - 1

    # --- Shear web: q = V / (2·h), τ = q / t ---
    q_shear = V / (2 * h_y + 1e-12)
    web_t = np.maximum(5e-4, q_shear / (mat.tau_allow / SF))
    MS_shear = mat.tau_allow / (SF * q_shear / (web_t + 1e-12) + 1e-12) - 1

    # --- Torsion (Bredt): A_enc = h·b_box, q_T = T/(2·A_enc) ---
    A_enc = h_y * b_box_y
    q_T = T / (2 * A_enc + 1e-12)
    web_t_torsion = np.maximum(5e-4, q_T / (mat.tau_allow / SF))
    web_t_final = np.maximum(web_t, web_t_torsion)
    MS_torsion = mat.tau_allow / (SF * q_T / (web_t_final + 1e-12) + 1e-12) - 1

    # --- Skin buckling: σ_cr = k·π²·E / (12·(1-ν²)) · (t/b)² ---
    nu = 0.33
    k_buck = 4.0
    sigma_cr = (
        k_buck * math.pi**2 * mat.E
        / (12 * (1 - nu**2))
        * (web_t_final / rib_spacing)**2
    )
    MS_buckling = sigma_cr / (SF * sigma_applied + 1e-12) - 1

    return BoxSizing(
        y=y,
        cap_area=cap_area,
        skin_t_upper=web_t_final,
        skin_t_lower=web_t_final,
        web_t_front=web_t_final,
        web_t_rear=web_t_final,
        rib_spacing=rib_spacing,
        stringer_area=0.0005,
        MS_bending=MS_bending,
        MS_shear=MS_shear,
        MS_torsion=MS_torsion,
        MS_buckling=MS_buckling,
    )
