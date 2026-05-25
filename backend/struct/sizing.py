"""Thin-walled box structural sizing — Module 7."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class Material:
    name: str
    E: float            # Pa
    G: float            # Pa
    sigma_allow: float  # Pa
    tau_allow: float    # Pa
    rho: float          # kg/m³


AL_2024_T3 = Material("2024-T3", 73.1e9, 28.0e9, 345e6, 200e6, 2780.0)
AL_7075_T6 = Material("7075-T6", 71.7e9, 26.9e9, 503e6, 290e6, 2810.0)
CFRP_UD   = Material("CFRP-UD", 135e9, 5.0e9, 600e6, 80e6, 1600.0)


@dataclass
class BoxSizing:
    y: list[float]              # spanwise stations
    chord: list[float]          # m
    cap_area: list[float]       # boom area m² (upper + lower)
    skin_t: list[float]         # skin thickness m
    spar_t: list[float]         # spar web thickness m
    MS_bending: list[float]     # margin of safety
    MS_shear: list[float]
    MS_torsion: list[float]
    MS_buckling: list[float]
    W_box: float                # total box structural weight kg
    material: Material
    front_spar_frac: float = 0.25
    rear_spar_frac: float = 0.65


def size_box(
    load_case,                  # LoadCase from loads.py
    airfoil_t_c: float = 0.12,
    chord_y: list[float] | None = None,
    mat: Material = AL_2024_T3,
    SF: float = 1.5,
    front_spar_frac: float = 0.25,
    rear_spar_frac: float = 0.65,
    target_MS_lo: float = 0.05,
    target_MS_hi: float = 0.15,
    max_iter: int = 20,
) -> BoxSizing:
    """
    Size a two-spar wing box at each spanwise station.
    Converges cap area and skin thickness so MS ∈ [target_MS_lo, target_MS_hi].
    """
    y = load_case.y
    chords = chord_y if chord_y is not None else load_case.y  # fallback
    n = len(y)
    box_width = (rear_spar_frac - front_spar_frac)  # fraction of chord

    cap_areas = []
    skin_ts = []
    spar_ts = []
    MS_bend = []
    MS_shear = []
    MS_tors = []
    MS_buck = []

    sigma_all = mat.sigma_allow / SF
    tau_all = mat.tau_allow / SF

    for i in range(n):
        M = abs(load_case.bending[i])
        V = abs(load_case.shear[i])
        T = abs(load_case.torsion[i])
        c = chords[i] if chord_y else (load_case.y[i] * 0.1 + 0.3)  # fallback
        h = airfoil_t_c * c          # box height (spar height)
        w = box_width * c            # box width
        A_box = h * w                # enclosed area for Bredt

        # --- Bending: sigma = M*h/2 / I, I = A_cap * (h/2)^2 * 2 ---
        # Solve for A_cap: A_cap = M / (sigma_all * h)
        A_cap = M / (sigma_all * h) if h > 1e-6 else 1e-6

        # Iterate to converge MS
        for _ in range(max_iter):
            I = A_cap * (h / 2) ** 2 * 2
            sigma_bend = M * (h / 2) / I if I > 1e-12 else sigma_all
            ms_b = sigma_all / sigma_bend - 1 if sigma_bend > 1e-9 else target_MS_hi

            if ms_b < target_MS_lo:
                A_cap *= 1.1
            elif ms_b > target_MS_hi:
                A_cap = max(A_cap * 0.95, 1e-8)
            else:
                break

        # --- Shear: q = V*Q/I, Q ≈ A_cap*(h/2) for each boom ---
        I = A_cap * (h / 2) ** 2 * 2
        Q = A_cap * (h / 2)
        q_shear = V * Q / I if I > 1e-12 else 0.0
        # Shear flow in spar webs; assume two webs, each carries half
        t_spar = q_shear / tau_all if tau_all > 1e-9 else 1e-4
        t_spar = max(t_spar, 5e-4)  # min 0.5mm

        # --- Torsion (Bredt): q_T = T / (2*A_box) ---
        q_tors = T / (2 * A_box) if A_box > 1e-9 else 0.0
        t_skin_tors = q_tors / tau_all if tau_all > 1e-9 else 1e-4

        # --- Skin buckling: sigma_cr = k*pi^2*E / (12*(1-nu^2)) * (t/b)^2 ---
        # Use b = w (bay width), k = 4 (simply supported)
        nu = 0.33
        k_buck = 4.0
        # Required: sigma_cr >= sigma_bend → solve for t
        # sigma_cr = k*pi^2*E / (12*(1-nu^2)) * (t/b)^2
        # t = b * sqrt(sigma_bend * 12*(1-nu^2) / (k*pi^2*E))
        sigma_bend_actual = M * (h / 2) / max(I, 1e-12)
        denom = k_buck * math.pi**2 * mat.E / (12 * (1 - nu**2))
        t_skin_buck = w * math.sqrt(sigma_bend_actual / denom) if denom > 0 else 1e-4
        t_skin = max(t_skin_tors, t_skin_buck, 5e-4)  # floor 0.5mm

        # Recompute MS
        I_final = A_cap * (h / 2) ** 2 * 2
        sigma_final = M * (h / 2) / max(I_final, 1e-12)
        ms_b_final = sigma_all / sigma_final - 1 if sigma_final > 0 else 99.0
        ms_s = tau_all / (q_shear / max(t_spar, 1e-9)) - 1 if q_shear > 0 else 99.0
        ms_t = tau_all / (q_tors / max(t_skin, 1e-9)) - 1 if q_tors > 0 else 99.0
        sigma_cr = denom * (t_skin / w) ** 2
        ms_buck = sigma_cr / max(sigma_final, 1e-9) - 1

        cap_areas.append(A_cap)
        skin_ts.append(t_skin)
        spar_ts.append(t_spar)
        MS_bend.append(ms_b_final)
        MS_shear.append(ms_s)
        MS_tors.append(ms_t)
        MS_buck.append(ms_buck)

    # Estimate box weight
    W_box = 0.0
    for i in range(n - 1):
        dy = y[i + 1] - y[i]
        c_avg = 0.5 * (chords[i] + chords[i + 1]) if chord_y else 0.5
        h_avg = airfoil_t_c * c_avg
        w_avg = box_width * c_avg
        perimeter = 2 * (h_avg + w_avg)
        t_avg = 0.5 * (skin_ts[i] + skin_ts[i + 1])
        cap_avg = 0.5 * (cap_areas[i] + cap_areas[i + 1])
        vol = (perimeter * t_avg + 4 * cap_avg) * dy
        W_box += vol * mat.rho

    return BoxSizing(
        y=list(y),
        chord=chords if chord_y else [0.3] * n,
        cap_area=cap_areas,
        skin_t=skin_ts,
        spar_t=spar_ts,
        MS_bending=MS_bend,
        MS_shear=MS_shear,
        MS_torsion=MS_tors,
        MS_buckling=MS_buck,
        W_box=W_box,
        material=mat,
        front_spar_frac=front_spar_frac,
        rear_spar_frac=rear_spar_frac,
    )
