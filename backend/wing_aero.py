"""3-D aero pipeline stage — bridge from analysis.lifting_line to the schema.

Connects Track B's whole-wing aerodynamics into /generate: given a wing planform
+ aircraft cruise conditions + sectional lift-curve data (the latter from an
XFOIL polar via analysis.lifting_line.lift_curve_slope_and_alpha0), run the
always-available Prandtl lifting-line baseline and map the result to a
``Wing3DAeroReport``.

Pure compute — no LLM, no subprocess. AVL (higher fidelity, needs the binary)
routes through ``analysis.avl.analyze_wing_3d`` and is a documented extension;
the lifting-line baseline is the default so the stage always produces a result.
"""

from __future__ import annotations

import math
from typing import Optional

from analysis.lifting_line import prandtl_lifting_line
from schemas import AircraftSpec, Wing3DAeroReport


def isa_density(altitude_m: float) -> float:
    """ISA 1976 troposphere air density [kg/m^3] (valid for h < 11 000 m)."""
    t = 288.15 - 0.0065 * altitude_m
    return 1.225 * (t / 288.15) ** 4.2561


def _symmetric_planform(
    root_chord_m: float, tip_chord_m: float, twist_tip_deg: float, n_stations: int
):
    """Chord + twist sampled at cosine-spaced full-span stations.

    lifting_line takes the stations as cosine-spaced in theta over the FULL span
    (theta=pi/2 -> centre/root, theta=0|pi -> tips). The distribution must be
    symmetric about the centre, so we sample chord/twist by |cos(theta)| =
    fractional distance from root to tip.
    """
    chords, twists = [], []
    for i in range(n_stations):
        theta = i * math.pi / (n_stations - 1)
        frac = abs(math.cos(theta))  # 0 at root (centre), 1 at the tips
        chords.append(root_chord_m + (tip_chord_m - root_chord_m) * frac)
        twists.append(twist_tip_deg * frac)
    return chords, twists


def compute_wing_3d_report(
    *,
    span_m: float,
    root_chord_m: float,
    tip_chord_m: float,
    twist_tip_deg: float,
    aircraft_spec: AircraftSpec,
    sectional_cl_alpha_per_rad: float,
    sectional_alpha0_deg: float,
    aoa_root_deg: float,
    n_stations: int = 21,
    n_fourier_terms: int = 40,
) -> Optional[Wing3DAeroReport]:
    """Run the lifting-line baseline and map it to a Wing3DAeroReport.

    Velocity/density come from the aircraft cruise condition (ISA). Returns None
    on degenerate input (so the pipeline simply omits wing_aero_report rather
    than emitting a garbage point)."""
    if span_m <= 0 or root_chord_m <= 0 or tip_chord_m <= 0 or n_stations < 3:
        return None
    # AircraftSpec cruise fields are Optional after the 2026-06-08 schema
    # reconcile; the cruise condition is required here, so omit the report
    # (matching the degenerate-input contract) rather than crash on None.
    if aircraft_spec.cruise_alt_m is None or aircraft_spec.cruise_speed_ms is None:
        return None

    chords, twists = _symmetric_planform(
        root_chord_m, tip_chord_m, twist_tip_deg, n_stations
    )
    density = isa_density(aircraft_spec.cruise_alt_m)
    velocity = aircraft_spec.cruise_speed_ms

    pt = prandtl_lifting_line(
        chord_distribution_m=chords,
        twist_distribution_deg=twists,
        sectional_cl_alpha_per_rad=sectional_cl_alpha_per_rad,
        sectional_cl_alpha0_deg=sectional_alpha0_deg,
        span_m=span_m,
        aoa_root_deg=aoa_root_deg,
        velocity_ms=velocity,
        density_kgm3=density,
        n_fourier_terms=n_fourier_terms,
    )

    if not pt.get("passed"):
        return None
    e = pt["span_efficiency_e"]
    if not (0.0 < e <= 1.05):
        return None

    return Wing3DAeroReport(
        CL_3D=pt["CL_3D"],
        CDi=max(0.0, pt["CDi"]),
        span_efficiency_e=e,
        source="lifting_line",
        Cl_local=pt.get("Cl_local"),
        L_distribution=pt.get("L_distribution"),
    )
