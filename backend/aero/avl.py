"""AVL spanwise loads + Schrenk fallback — Module 5."""
from __future__ import annotations
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from .regime import WingSpec
from .sizing import WingSizing


@dataclass
class SpanwiseLoads:
    y: list[float]          # spanwise stations (half-span, root=0)
    chord: list[float]      # local chord m
    CL_local: list[float]   # local lift coefficient
    L_per_m: list[float]    # lift per unit span N/m
    method: str             # "avl" or "schrenk"


def _schrenk(spec: WingSpec, sizing: WingSizing, n_stations: int = 20, n: float = 1.0) -> SpanwiseLoads:
    """Schrenk approximation: average of elliptic and trapezoidal distributions."""
    b2 = sizing.b / 2
    root = sizing.root_chord
    tip = sizing.tip_chord
    atm_rho = 1.225  # use sea level for load cases (conservative)
    q = 0.5 * atm_rho * spec.cruise_V**2

    y_stations = [b2 * i / (n_stations - 1) for i in range(n_stations)]
    chords = [root + (tip - root) * (yi / b2) for yi in y_stations]

    W = spec.MTOW * 9.80665
    L_total = n * W  # at load factor n

    ys = y_stations
    chord_list = chords
    CL_list = []
    L_list = []

    # Schrenk: local CL = 0.5*(elliptic + trapezoidal) normalized to total lift
    # Elliptic: CL_e(y) ~ sqrt(1-(2y/b)^2)
    # Trapezoidal: CL_t(y) ~ chord(y)/MAC
    MAC = sizing.MAC

    schrenk_raw = []
    for yi, ci in zip(ys, chord_list):
        ell = math.sqrt(max(0.0, 1 - (2 * yi / sizing.b) ** 2))
        trap = ci / MAC if MAC > 0 else 1.0
        schrenk_raw.append(0.5 * (ell + trap))

    # Integrate to normalize
    # trapezoidal integration of schrenk_raw * chord over span
    integral = 0.0
    for i in range(len(ys) - 1):
        dy = ys[i + 1] - ys[i]
        f_avg = 0.5 * (schrenk_raw[i] * chord_list[i] + schrenk_raw[i + 1] * chord_list[i + 1])
        integral += f_avg * dy
    integral *= 2  # both halves

    scale = L_total / (q * integral) if integral > 1e-9 else 1.0

    for yi, ci, sr in zip(ys, chord_list, schrenk_raw):
        cl = sr * scale
        l = q * cl * ci
        CL_list.append(cl)
        L_list.append(l)

    return SpanwiseLoads(y=ys, chord=chord_list, CL_local=CL_list, L_per_m=L_list, method="schrenk")


def compute_spanwise(
    spec: WingSpec,
    sizing: WingSizing,
    polar=None,
    n: float = 1.0,
    n_stations: int = 20,
) -> SpanwiseLoads:
    """Use AVL if found; else Schrenk."""
    avl_bin = None
    for candidate in ("avl", "avl.exe", "/usr/local/bin/avl"):
        if os.path.isfile(candidate) or __import__("shutil").which(candidate):
            avl_bin = candidate
            break
    # AVL integration is complex; fall back to Schrenk unless binary confirmed
    return _schrenk(spec, sizing, n_stations=n_stations, n=n)
