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

__all__ = [
    "FlightEnvelope",
    "PolarResult",
    "analyze_naca",
    "analyze_wing_step",
    "predict_stall",
    "slice_step_to_dat",
    "naca4_to_dat",
]
