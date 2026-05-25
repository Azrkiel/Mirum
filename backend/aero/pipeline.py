"""Aerospace analysis pipeline orchestrator — Module 11."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any

from .isa import isa, AtmState
from .regime import WingSpec, validate_regime, RegimeError
from .airfoil import get_profile, NACAProfile
from .sizing import size_wing, WingSizing
from .avl import compute_spanwise, SpanwiseLoads
from .loads import compute_vn, compute_loads, VnDiagram, LoadCase
from ..struct.sizing import size_box, BoxSizing, AL_2024_T3


@dataclass
class PipelineResult:
    spec: WingSpec
    atm: AtmState
    M_cruise: float
    Re_cruise: float
    sizing: WingSizing
    vn: VnDiagram
    design_load: LoadCase
    spanwise: SpanwiseLoads
    box: BoxSizing
    converged: bool
    iterations: int
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    polar: Any = None               # PolarResult or None if XFOIL unavailable


def run(
    spec: WingSpec,
    CL_cruise: float = 0.4,
    AR: float | None = None,
    taper: float | None = None,
    max_iter: int = 10,
    target_MS_lo: float = 0.05,
    target_MS_hi: float = 0.15,
) -> PipelineResult:
    """
    DAG pipeline:
      0. ISA atmosphere
      1. Regime guard
      2. Airfoil geometry
      3. XFOIL polar (optional — graceful fallback)
      4. Conceptual sizing (Raymer)
      5. Spanwise loads (AVL / Schrenk)
      6. V-n diagram
      7. Structural loads
      8. Box sizing
      9. Convergence check (iterate 7→8→9 until MS ∈ target range)
    """
    warnings: list[str] = []
    errors: list[str] = []

    # --- Module 0: ISA ---
    atm = isa(spec.cruise_alt)

    # --- Module 1: Regime guard ---
    try:
        M_cruise, Re_cruise = validate_regime(spec, method="xfoil")
    except RegimeError as e:
        warnings.append(f"Regime: {e}. Falling back to Schrenk without XFOIL polar.")
        atm2 = isa(spec.cruise_alt)
        M_cruise = spec.cruise_V / atm2.a
        Re_cruise = atm2.rho * spec.cruise_V * spec.chord / atm2.mu

    # --- Module 2: Airfoil ---
    try:
        profile = get_profile(spec.airfoil)
        airfoil_pts = profile.points
    except Exception as e:
        warnings.append(f"Airfoil load failed ({e}). Using NACA2412 default.")
        profile = NACAProfile("NACA2412")
        airfoil_pts = profile.points

    # --- Module 3: XFOIL polar (optional) ---
    polar = None
    polar_cl_max = 1.4   # conservative default
    try:
        from .xfoil import run_polar, XFOILNotFoundError
        polar = run_polar(airfoil_pts, Re_cruise, M_cruise)
        if polar.CL_max and not math.isnan(polar.CL_max):
            polar_cl_max = polar.CL_max
    except Exception as e:
        warnings.append(f"XFOIL unavailable ({type(e).__name__}). Using CL_max={polar_cl_max:.2f}.")

    # --- Module 4: Conceptual sizing ---
    AR_val = AR if AR is not None else spec.AR
    taper_val = taper if taper is not None else spec.taper
    sizing = size_wing(spec, CL_cruise=CL_cruise, AR=AR_val, taper=taper_val)

    # --- Module 5: Spanwise loads ---
    n_design = spec.load_factors[0]
    spanwise = compute_spanwise(spec, sizing, polar=polar, n=n_design)

    # --- Module 6: V-n diagram ---
    vn = compute_vn(spec, sizing, polar_cl_max=polar_cl_max)

    # --- Modules 7-9: Structural convergence loop ---
    chords_at_y = spanwise.chord
    box = None
    converged = False
    iteration = 0

    for iteration in range(1, max_iter + 1):
        load_case = compute_loads(spanwise, sizing, spec, n=n_design)
        box = size_box(
            load_case,
            airfoil_t_c=getattr(profile, "t", 0.12),
            chord_y=chords_at_y,
            target_MS_lo=target_MS_lo,
            target_MS_hi=target_MS_hi,
        )

        # Check convergence: all MS_bending in [target_MS_lo, target_MS_hi]
        ms_vals = [ms for ms in box.MS_bending if ms < 1e6]  # exclude sentinel
        in_range = all(target_MS_lo <= ms <= target_MS_hi for ms in ms_vals)
        if in_range:
            converged = True
            break

        # Adjust: scale up if any MS < lo, scale down if all MS > hi
        any_low = any(ms < target_MS_lo for ms in ms_vals)
        if any_low:
            # Re-run with tighter design — increase n slightly to drive up loads
            n_design = min(n_design * 1.02, spec.load_factors[0] * 1.5)
        else:
            n_design = max(n_design * 0.99, spec.load_factors[0])

    if not converged:
        warnings.append(f"Box sizing did not converge in {max_iter} iterations.")

    return PipelineResult(
        spec=spec,
        atm=atm,
        M_cruise=M_cruise,
        Re_cruise=Re_cruise,
        sizing=sizing,
        vn=vn,
        design_load=load_case,
        spanwise=spanwise,
        box=box,
        converged=converged,
        iterations=iteration,
        warnings=warnings,
        errors=errors,
        polar=polar,
    )
