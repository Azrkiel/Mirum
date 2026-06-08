"""
analysis — Airfoil Aerodynamic Analysis Package
================================================

Standalone module: no dependency on the live pipeline (agents, LLM, FastAPI).
Requires: pythonOCC (via cadquery env) for STEP slicing, XFOIL binary for polars.

Public API
----------
analyse_naca(naca_code, chord_mm, flight_env, *, xfoil_bin, ...) -> PolarResult
    Run a full XFOIL polar for a NACA 4-digit airfoil by code.

analyze_wing_step(step_path, naca_code, span_mm, chord_mm, flight_env, ...) -> PolarResult
    Slice a T2CIII-generated STEP file at mid-span, then run XFOIL.

predict_stall(polar) -> (stall_alpha, Cl_max)
    Post-process a PolarResult to extract stall angle and peak lift.

Types
-----
FlightEnvelope  TypedDict  — analysis conditions (Re, Mach, alpha sweep, n_crit)
PolarResult     TypedDict  — full polar output with L/D, stall, convergence info
"""

from analysis.airfoil import (
    FlightEnvelope,
    PolarResult,
    analyze_naca,
    analyze_wing_step,
    predict_stall,
    slice_step_to_dat,
    naca4_to_dat,
)
from analysis.lifting_line import (
    WingPolarPoint,
    lift_curve_slope_and_alpha0,
    prandtl_lifting_line,
)
from analysis.avl import (
    AvlNotFoundError,
    AvlResult,
    AvlRunError,
    WingSection,
    WingSpec,
    analyze_wing_3d,
    run_avl_analysis,
)

__all__ = [
    # airfoil (XFOIL / 2-D)
    "FlightEnvelope",
    "PolarResult",
    "analyze_naca",
    "analyze_wing_step",
    "predict_stall",
    "slice_step_to_dat",
    "naca4_to_dat",
    # lifting-line (3-D analytic baseline)
    "WingPolarPoint",
    "prandtl_lifting_line",
    "lift_curve_slope_and_alpha0",
    # AVL (3-D higher-fidelity, opt-in)
    "AvlResult",
    "AvlNotFoundError",
    "AvlRunError",
    "WingSection",
    "WingSpec",
    "run_avl_analysis",
    "analyze_wing_3d",
]
