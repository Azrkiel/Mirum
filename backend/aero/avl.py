"""AVL wrapper with Schrenk fallback for spanwise loading."""
from __future__ import annotations
import shutil
import math
from dataclasses import dataclass
from typing import Optional
import numpy as np
from .regime import WingSpec
from .sizing import WingSizing
from .xfoil import PolarResult


@dataclass
class SpanwiseLoads:
    y: np.ndarray       # spanwise stations 0..b/2
    L_y: np.ndarray     # lift per unit span N/m
    CDi: float
    CL: float
    e: float            # Oswald efficiency


def _schrenk(sizing: WingSizing, W_total: float) -> SpanwiseLoads:
    """Schrenk approximation: average of elliptic and trapezoidal distributions."""
    b2 = sizing.b / 2
    y = np.linspace(0, b2, 101)

    # Chord distribution (linear taper)
    c_y = sizing.c_root + (sizing.c_tip - sizing.c_root) * y / b2

    # Elliptic: normalise so integral over half-span = 1
    elliptic = np.sqrt(np.maximum(0, 1 - (2 * y / sizing.b)**2))
    denom_e = np.trapz(elliptic, y) * 2
    if denom_e > 0:
        elliptic /= denom_e
    else:
        elliptic = np.zeros_like(y)

    # Trapezoidal: proportional to chord
    denom_t = np.trapz(c_y, y) * 2
    if denom_t > 0:
        trapz = c_y / denom_t
    else:
        trapz = np.zeros_like(y)

    # Schrenk average (N/m when multiplied by total weight)
    gamma = 0.5 * (elliptic + trapz)
    L_y = gamma * W_total

    CL = sizing.CL_cruise
    e = 0.85
    CDi = CL**2 / (math.pi * sizing.AR * e)

    return SpanwiseLoads(y=y, L_y=L_y, CDi=CDi, CL=CL, e=e)


def compute_spanwise(
    spec: WingSpec,
    sizing: WingSizing,
    polar: Optional[PolarResult] = None,
) -> SpanwiseLoads:
    """Compute spanwise load distribution. Falls back to Schrenk if AVL absent."""
    W = spec.MTOW * 9.80665
    avl_bin = shutil.which("avl")
    if avl_bin is None:
        return _schrenk(sizing, W)
    # Full AVL implementation would go here
    raise NotImplementedError(
        "AVL binary found but full wrapper not yet implemented; "
        "using Schrenk fallback"
    )
