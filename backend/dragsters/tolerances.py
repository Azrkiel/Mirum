"""Loft and verification tolerances for CO2 dragster bodies (pure constants, no imports).

Fixed by the D2 loft spike; the measurements behind every value are in
``docs/plans/co2-dragster/evidence/D2-loft-spike.md`` (re-run with
``docs/plans/co2-dragster/evidence/tools/d2_loft_spike.py``). Validation (D5) imports this
module and never ``kernel``. Never loosen a value here to make a test pass (master invariant 4).
"""

# Loft construction (kernel defaults). Spike: 14 cases (2 presets + 12 boundary cases) x
# ruled/smooth x step {10, 7.5, 5} x crown points {17, 33}; every build valid before and after
# the STEP round trip. Smooth lofts overshoot the narrow-waist law bounds by up to 0.54 mm at the
# housing hold step (needs > 0.25 mm hard max), so the loft is ruled (bounds error 0). Among
# ruled settings within the time rule (median <= 6 s, max <= 9 s build+GLB), 10 mm / 33 points
# has the smallest volume error. Crown is a polyline: spline crowns break OCC volume integration.
LOFT_RULED: bool = True
STATION_STEP_MM: float = 10.0
CROWN_POINTS: int = 33   # odd, so the crown apex is a vertex

# Lofted bodies vs the profile law, measured on the reimported STEP of the uncut loft.
# Volume vs profiles.law_volume: worst observed -0.225 % (nw-housingl110);
# max(0.010, 1.5 x 0.00225) = 0.010 (hard max 0.020).
LOFT_VOLUME_REL_TOL: float = 0.010
# Optimal bounding box vs law bounds sampled at 0.25 mm: worst observed 1e-7 mm;
# max(0.05, 1.5 x 1e-7) = 0.05 (hard max 0.25).
LOFT_BOUNDS_TOL_MM: float = 0.05

# Unchanged v1 values.
WEDGE_BOUNDS_TOL_MM = 0.01           # v1 value — do not change
WALL_DISTANCE_TOL_MM = 0.01
VOID_TOLERANCE_MM3 = 0.001           # v1 value — do not change
