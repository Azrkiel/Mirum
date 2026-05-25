"""Aerospace wing analysis pipeline orchestrator."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
from .isa import isa
from .regime import WingSpec, validate_regime
from .airfoil import NACAProfile
from .xfoil import run_polar, XFOILNotFoundError, PolarResult
from .sizing import size_wing, WingSizing
from .avl import compute_spanwise, SpanwiseLoads
from .loads import compute_vn, compute_loads, VnDiagram, LoadCase
from ..struct.sizing import size_box, BoxSizing, AL_2024_T3


@dataclass
class PipelineResult:
    spec: WingSpec
    regime: str
    Mach: float
    Re: float
    sizing: WingSizing
    polar: Optional[PolarResult]
    spanwise: SpanwiseLoads
    vn: VnDiagram
    load_case: LoadCase
    box: BoxSizing
    converged: bool
    iterations: int
    warnings: list = field(default_factory=list)


def run(
    spec: WingSpec,
    CL_cruise: float = 0.4,
    AR: float = 8.0,
    taper: float = 0.45,
    max_iter: int = 10,
) -> PipelineResult:
    """Execute the full wing analysis pipeline and return consolidated results."""
    warnings: list = []

    # Module 0+1: atmosphere + regime validation
    M, Re, regime, atm, w = validate_regime(spec, method="xfoil")
    warnings.extend(w)

    # Module 2: airfoil geometry
    airfoil = NACAProfile(spec.airfoil, chord=spec.chord)

    # Module 3: XFOIL polar (graceful fallback if binary absent)
    polar: Optional[PolarResult] = None
    try:
        polar = run_polar(airfoil.coords / spec.chord, Re, M)
        cl_max = polar.cl_max
    except (XFOILNotFoundError, Exception) as e:
        warnings.append(f"XFOIL unavailable ({e}), using cl_max=1.4")
        cl_max = 1.4

    # Module 4: conceptual wing sizing
    sizing = size_wing(spec, CL_cruise=CL_cruise, AR=AR, taper=taper)

    # Module 5: spanwise load distribution (Schrenk fallback)
    spanwise = compute_spanwise(spec, sizing, polar)

    # Module 6: V-n diagram + critical load case
    vn = compute_vn(spec, sizing, cl_max)
    load_case = compute_loads(spanwise, sizing, spec, n=vn.n_max)

    # Module 7: structural sizing loop
    chord_y = (
        sizing.c_root
        + (sizing.c_tip - sizing.c_root)
        * spanwise.y
        / (sizing.b / 2)
    )
    box = size_box(load_case, airfoil, chord_y)

    # Iterate until margins are in the target band [0.05, 0.50]
    converged = False
    iteration = 0
    for iteration in range(max_iter):
        ms_min = min(
            box.MS_bending.min(),
            box.MS_shear.min(),
            box.MS_torsion.min(),
            box.MS_buckling.min(),
        )
        ms_max = max(box.MS_bending.max(), box.MS_shear.max())
        if 0.05 <= ms_min and ms_max <= 0.50:
            converged = True
            break
        box = size_box(load_case, airfoil, chord_y)

    return PipelineResult(
        spec=spec,
        regime=regime,
        Mach=M,
        Re=Re,
        sizing=sizing,
        polar=polar,
        spanwise=spanwise,
        vn=vn,
        load_case=load_case,
        box=box,
        converged=converged,
        iterations=iteration + 1,
        warnings=warnings,
    )
