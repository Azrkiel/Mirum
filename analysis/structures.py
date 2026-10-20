"""
analysis/structures.py — Wing structural & flight-envelope analysis
====================================================================

A standalone, pure-Python analysis module for fixed-wing aircraft.  It is the
structural/flight counterpart to ``analysis/airfoil.py`` (which owns the 2-D
XFOIL aerodynamic polar): where ``airfoil.py`` answers *"what are Cl/Cd/Cm vs
angle of attack?"*, this module answers *"given a weight and a cruise point,
what load factors does the wing see, and do the spar caps / webs survive
them?"*

No pipeline imports, no OCC, no CadQuery, no subprocess — only ``math`` from
the stdlib.  Every function is deterministic and independently callable, so
the module can be used from a one-off script, a notebook, a future subagent,
or wired into the SSE pipeline exactly the way ``analyze_wing_step`` is.

Capabilities
------------
  * ISA 1976 standard atmosphere (troposphere + lower stratosphere)
  * Dynamic pressure, cruise lift coefficient (lift = weight closure)
  * 1-g stall speed
  * FAR-23 / CS-23 style limit maneuvering load factors (by category)
  * FAR-23 discrete-gust load factor increment (mass-ratio alleviation)
  * Combined flight-envelope: limit & ultimate load factors, corner speed,
    V_NE
  * Single-cell wing-box bending & shear margins of safety against a small
    metallic-allowables table
  * ``analyze_wing_structure`` — ties it all together into a StructuralReport

Sign / unit conventions
------------------------
  * SI throughout: metres, m/s, kg, Pa, N, N·m, radians (lift-curve slope).
  * Load factor n is dimensionless (n = lift / weight).
  * "ultimate" = "limit" × 1.5 (the standard airworthiness safety factor).
  * Margin of safety MS = allowable / applied − 1 ; MS ≥ 0 means it survives.

The load estimation in ``analyze_wing_structure`` uses a first-order
elliptical-lift point-load model (root shear = half the total lift, root
bending = that half-lift acting at the spanwise centre of pressure).  It is a
sizing-grade estimate, not an FEA result — pass explicit ``root_bending_moment_nm``
/ ``root_shear_force_n`` when you have better loads.

Signed loads and validation
---------------------------
  * Loads are signed.  The margin model is a symmetric two-cap idealisation
    with a single tensile allowable Ftu and a sign-independent shear allowable
    Fsu, so margins use stress *magnitude*: a moment of either sign puts one
    cap in tension with |σ| = |M|·(h/2)/I, and |τ| = |V|/(n_spars·h·t).
    ``stress_pa`` is reported signed, ``stress_magnitude_pa`` as |stress|.
  * Cap compression / stability / buckling and web shear buckling are NOT
    evaluated (for either sign); ``compression_evaluated`` is False and the
    report carries a warning.  Results are an "analytic sizing estimate"
    (``engineering_certified`` False), not FEA or certification.
  * When loads are estimated, both the positive and negative ultimate cases
    are evaluated and the governing (lower-margin) case is reported.
  * Invalid inputs (non-finite, non-positive geometry/allowables, bad spar
    count, degenerate or overflowing computed quantities) raise
    ``StructuralInputError``; a zero stress is never substituted.  An exactly
    zero load with valid geometry is status "zero_load" with MS = inf.
"""

from __future__ import annotations

import math
import numbers
from typing import Optional, TypedDict

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

G0 = 9.80665            # standard gravity, m/s²
SAFETY_FACTOR = 1.5     # ultimate = limit × 1.5 (airworthiness standard)

# ISA 1976 sea-level reference + troposphere lapse
_T0 = 288.15            # K
_P0 = 101325.0          # Pa
_LAPSE = 0.0065         # K/m (troposphere temperature lapse rate)
_R_AIR = 287.05287      # J/(kg·K) specific gas constant for dry air
_GAMMA = 1.4            # ratio of specific heats
_TROPOPAUSE_M = 11000.0
_T_TROPOPAUSE = _T0 - _LAPSE * _TROPOPAUSE_M    # 216.65 K
_LB_PER_KG = 2.2046226218

# Default discrete gust velocity: 50 ft/s (FAR-23 high-speed gust ≤ 20 000 ft)
_DEFAULT_GUST_MS = 15.24
# Default 3-D wing lift-curve slope (per radian) — a reasonable AR≈8 value.
_DEFAULT_CL_ALPHA = 5.7


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class ISACondition(TypedDict):
    altitude_m:        float
    temperature_k:     float
    pressure_pa:       float
    density_kgm3:      float
    speed_of_sound_ms: float


class AircraftSpec(TypedDict, total=False):
    """Flight-condition + sizing inputs.  ``mtow_kg``, ``cruise_alt_m`` and
    ``cruise_speed_ms`` are required; the rest carry sensible defaults."""
    mtow_kg:          float   # required — maximum take-off weight (mass), kg
    cruise_alt_m:     float   # required — cruise altitude, m
    cruise_speed_ms:  float   # required — cruise true airspeed, m/s
    category:         str     # "normal" | "utility" | "acrobatic" (default normal)
    cl_max:           float   # max lift coefficient (default 1.5)
    lift_curve_slope: float   # 3-D wing CLα per radian (default 5.7)
    gust_velocity_ms: float   # derived gust velocity (default 15.24)
    vne_factor:       float   # V_NE / V_cruise (default 1.4)


class WingSizing(TypedDict, total=False):
    area_m2:      float   # required — reference wing area S
    span_m:       float   # full tip-to-tip span (needed for gust + bending est.)
    mean_chord_m: float   # mean aerodynamic/geometric chord (else S/b)


class BoxSizing(TypedDict, total=False):
    chord_root_m:         float   # required — chord at the analysed station
    thickness_to_chord:   float   # required — section t/c (sets box height)
    cap_area_m2:          float   # required — area of ONE spar cap
    spar_web_thickness_m: float   # required — shear web thickness
    n_spars:              int     # number of shear webs (default 2)


class MaterialAllowables(TypedDict):
    name:         str
    ftu_pa:       float   # ultimate tensile strength
    fsu_pa:       float   # ultimate shear strength
    density_kgm3: float


class LoadFactorEnvelope(TypedDict):
    category:         str
    n_maneuver_pos:   float
    n_maneuver_neg:   float
    n_gust_pos:       float
    n_gust_neg:       float
    n_limit_pos:      float
    n_limit_neg:      float
    n_ultimate_pos:   float
    n_ultimate_neg:   float
    v_stall_ms:       float
    v_corner_ms:      float
    v_ne_ms:          float


class _MarginRequired(TypedDict):
    stress_pa:           float   # signed stress (sign follows the load)
    stress_magnitude_pa: float   # |stress|; the margin is computed from this
    allowable_pa:        float
    margin_of_safety:    float
    passed:              bool
    status:              str     # "evaluated" | "zero_load" | "not_evaluated"


class MarginResult(_MarginRequired, total=False):
    evaluated:             bool   # set by analyze_wing_structure
    compression_evaluated: bool   # bending only; always False


class LoadCase(TypedDict):
    name:                   str     # positive_ultimate | negative_ultimate | explicit
    load_factor:            float
    root_bending_moment_nm: float   # signed
    root_shear_force_n:     float   # signed
    bending:                MarginResult
    shear:                  MarginResult
    passed:                 bool


class StructuralReport(TypedDict):
    cruise:                 ISACondition
    dynamic_pressure_pa:    float
    cl_cruise:              float
    wing_loading_pa:        float
    envelope:               LoadFactorEnvelope
    root_bending_moment_nm: float   # governing load case
    root_shear_force_n:     float   # governing load case
    bending:                MarginResult   # governing load case
    shear:                  MarginResult   # governing load case
    load_cases:             list[LoadCase]
    governing_load_case:    str
    material:               str
    method:                 str     # always "analytic_sizing_estimate"
    engineering_certified:  bool    # always False
    passed:                 bool
    warnings:               list[str]


# ---------------------------------------------------------------------------
# Material allowables table  (MIL-HDBK-5 / MMPDS room-temperature values)
# ---------------------------------------------------------------------------

_MATERIALS: dict[str, MaterialAllowables] = {
    "AL2024-T3":  {"name": "AL 2024-T3",  "ftu_pa": 483e6,  "fsu_pa": 290e6,  "density_kgm3": 2780.0},
    "AL7075-T6":  {"name": "AL 7075-T6",  "ftu_pa": 572e6,  "fsu_pa": 331e6,  "density_kgm3": 2810.0},
    "AL6061-T6":  {"name": "AL 6061-T6",  "ftu_pa": 310e6,  "fsu_pa": 207e6,  "density_kgm3": 2700.0},
    "TI-6AL-4V":  {"name": "Ti-6Al-4V",   "ftu_pa": 950e6,  "fsu_pa": 550e6,  "density_kgm3": 4430.0},
    "STEEL-4130": {"name": "AISI 4130",   "ftu_pa": 670e6,  "fsu_pa": 400e6,  "density_kgm3": 7850.0},
}


def get_material(name: str) -> MaterialAllowables:
    """Return allowables for a material name (case-insensitive).

    Raises ``KeyError`` for an unknown material so callers fail loudly rather
    than silently sizing against a wrong allowable.
    """
    key = name.strip().upper()
    if key not in _MATERIALS:
        raise KeyError(
            f"Unknown material {name!r}. Known: {sorted(_MATERIALS)}"
        )
    return _MATERIALS[key]


# ---------------------------------------------------------------------------
# ISA atmosphere
# ---------------------------------------------------------------------------

def isa_atmosphere(altitude_m: float) -> ISACondition:
    """ISA 1976 standard atmosphere at a geopotential altitude.

    Troposphere (0–11 km): linear temperature lapse.
    Lower stratosphere (11–20 km): isothermal at 216.65 K.

    Above 20 km the isothermal model is extrapolated (a warning is *not*
    raised here — callers analysing such altitudes should know the limitation).
    """
    n_exp = G0 / (_LAPSE * _R_AIR)   # ≈ 5.2559

    if altitude_m <= _TROPOPAUSE_M:
        temp = _T0 - _LAPSE * altitude_m
        pressure = _P0 * (temp / _T0) ** n_exp
    else:
        temp = _T_TROPOPAUSE
        p_tropopause = _P0 * (_T_TROPOPAUSE / _T0) ** n_exp
        pressure = p_tropopause * math.exp(
            -G0 * (altitude_m - _TROPOPAUSE_M) / (_R_AIR * _T_TROPOPAUSE)
        )

    density = pressure / (_R_AIR * temp)
    speed_of_sound = math.sqrt(_GAMMA * _R_AIR * temp)

    return {
        "altitude_m":        altitude_m,
        "temperature_k":     temp,
        "pressure_pa":       pressure,
        "density_kgm3":      density,
        "speed_of_sound_ms": speed_of_sound,
    }


# ---------------------------------------------------------------------------
# Basic aero relations
# ---------------------------------------------------------------------------

def dynamic_pressure(density_kgm3: float, speed_ms: float) -> float:
    """q = ½·ρ·V²  [Pa]."""
    return 0.5 * density_kgm3 * speed_ms ** 2


def cruise_cl(
    mtow_kg: float,
    density_kgm3: float,
    speed_ms: float,
    wing_area_m2: float,
) -> float:
    """Lift coefficient required for level cruise (lift = weight closure).

    CL = W / (q·S),  W = m·g₀.
    """
    weight_n = mtow_kg * G0
    q = dynamic_pressure(density_kgm3, speed_ms)
    return weight_n / (q * wing_area_m2)


def stall_speed(
    mtow_kg: float,
    density_kgm3: float,
    wing_area_m2: float,
    cl_max: float,
) -> float:
    """1-g stall speed  V_s = √(2·W / (ρ·S·CL_max))  [m/s]."""
    weight_n = mtow_kg * G0
    return math.sqrt(2.0 * weight_n / (density_kgm3 * wing_area_m2 * cl_max))


# ---------------------------------------------------------------------------
# Load factors
# ---------------------------------------------------------------------------

def maneuvering_load_factors(
    mtow_kg: float,
    category: str = "normal",
) -> tuple[float, float]:
    """FAR-23 / CS-23 §23.337 limit maneuvering load factors (positive, negative).

    * normal / commuter: n⁺ = 2.1 + 24000/(W_lb + 10000), clamped to [2.5, 3.8];
      n⁻ = −0.4·n⁺
    * utility:           n⁺ = 4.4 ; n⁻ = −0.4·n⁺
    * acrobatic:         n⁺ = 6.0 ; n⁻ = −0.5·n⁺
    """
    cat = category.strip().lower()
    if cat == "acrobatic":
        n_pos = 6.0
        n_neg = -0.5 * n_pos
    elif cat == "utility":
        n_pos = 4.4
        n_neg = -0.4 * n_pos
    else:  # normal / commuter (default)
        w_lb = mtow_kg * _LB_PER_KG
        n_pos = 2.1 + 24000.0 / (w_lb + 10000.0)
        n_pos = max(2.5, min(3.8, n_pos))
        n_neg = -0.4 * n_pos
    return n_pos, n_neg


def gust_load_factor_increment(
    mtow_kg: float,
    wing_area_m2: float,
    density_kgm3: float,
    speed_ms: float,
    mean_chord_m: float,
    *,
    gust_velocity_ms: float = _DEFAULT_GUST_MS,
    lift_curve_slope: float = _DEFAULT_CL_ALPHA,
) -> float:
    """Discrete-gust load-factor increment Δn (FAR-23 §23.341 form).

    Uses the gust-alleviation factor K_g derived from the aircraft mass ratio
    μ_g, evaluated with the *local* air density and true airspeed so the result
    is internally consistent:

        μ_g = 2·(W/S) / (ρ·c̄·a·g₀)
        K_g = 0.88·μ_g / (5.3 + μ_g)
        Δn  = ρ·V·a·K_g·U_de / (2·(W/S))

    The total gust load factor is n = 1 ± Δn.  ``a`` is the lift-curve slope
    per radian; ``U_de`` the derived gust velocity (m/s).
    """
    weight_n = mtow_kg * G0
    wing_loading = weight_n / wing_area_m2   # W/S, N/m²

    mu_g = (2.0 * wing_loading) / (
        density_kgm3 * mean_chord_m * lift_curve_slope * G0
    )
    k_g = 0.88 * mu_g / (5.3 + mu_g)

    delta_n = (
        density_kgm3 * speed_ms * lift_curve_slope * k_g * gust_velocity_ms
    ) / (2.0 * wing_loading)
    return delta_n


def load_factor_envelope(
    spec: AircraftSpec,
    wing: WingSizing,
) -> LoadFactorEnvelope:
    """Combine maneuvering and gust load factors into a flight envelope.

    The governing limit load factor is the larger-magnitude of the maneuvering
    and gust cases; ultimate = limit × 1.5.  Also returns 1-g stall speed,
    corner speed (V_A = V_s·√n⁺), and V_NE.
    """
    mtow = spec["mtow_kg"]
    alt = spec["cruise_alt_m"]
    speed = spec["cruise_speed_ms"]
    category = spec.get("category", "normal")
    cl_max = spec.get("cl_max", 1.5)
    cl_alpha = spec.get("lift_curve_slope", _DEFAULT_CL_ALPHA)
    gust_ms = spec.get("gust_velocity_ms", _DEFAULT_GUST_MS)
    vne_factor = spec.get("vne_factor", 1.4)

    rho = isa_atmosphere(alt)["density_kgm3"]
    n_man_pos, n_man_neg = maneuvering_load_factors(mtow, category)

    # Mean chord for the gust mass ratio: explicit, else S/b, else no gust case.
    mean_chord = wing.get("mean_chord_m")
    if mean_chord is None and wing.get("span_m"):
        mean_chord = wing["area_m2"] / wing["span_m"]

    if mean_chord:
        delta_n = gust_load_factor_increment(
            mtow, wing["area_m2"], rho, speed, mean_chord,
            gust_velocity_ms=gust_ms, lift_curve_slope=cl_alpha,
        )
        n_gust_pos = 1.0 + delta_n
        n_gust_neg = 1.0 - delta_n
    else:
        # No chord available — fall back to the maneuvering case.
        n_gust_pos, n_gust_neg = n_man_pos, n_man_neg

    n_limit_pos = max(n_man_pos, n_gust_pos)
    n_limit_neg = min(n_man_neg, n_gust_neg)   # most-negative governs

    v_stall = stall_speed(mtow, rho, wing["area_m2"], cl_max)
    v_corner = v_stall * math.sqrt(n_limit_pos)
    v_ne = vne_factor * speed

    return {
        "category":       category,
        "n_maneuver_pos": n_man_pos,
        "n_maneuver_neg": n_man_neg,
        "n_gust_pos":     n_gust_pos,
        "n_gust_neg":     n_gust_neg,
        "n_limit_pos":    n_limit_pos,
        "n_limit_neg":    n_limit_neg,
        "n_ultimate_pos": n_limit_pos * SAFETY_FACTOR,
        "n_ultimate_neg": n_limit_neg * SAFETY_FACTOR,
        "v_stall_ms":     v_stall,
        "v_corner_ms":    v_corner,
        "v_ne_ms":        v_ne,
    }


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class StructuralInputError(ValueError):
    """Invalid input (or non-finite / degenerate computed quantity) in the
    structural analysis.  Raised instead of ever substituting a zero stress,
    so an invalid analysis can never be mistaken for a safe one."""


def _real(name: str, value: object) -> float:
    """Return ``value`` as a finite float; reject bool, non-numbers, NaN, ±inf."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise StructuralInputError(f"{name} must be a finite real number, got {value!r}")
    try:
        out = float(value)
    except (OverflowError, ValueError, TypeError):
        raise StructuralInputError(f"{name} must be a finite real number, got {value!r}") from None
    if not math.isfinite(out):
        raise StructuralInputError(f"{name} must be finite, got {value!r}")
    return out


def _positive(name: str, value: object) -> float:
    out = _real(name, value)
    if not out > 0.0:
        raise StructuralInputError(f"{name} must be > 0, got {value!r}")
    return out


def _nonnegative(name: str, value: object) -> float:
    out = _real(name, value)
    if out < 0.0:
        raise StructuralInputError(f"{name} must be >= 0, got {value!r}")
    return out


def _computed_positive(name: str, value: float) -> float:
    """A derived quantity (section property) must be finite and strictly > 0."""
    if not (math.isfinite(value) and value > 0.0):
        raise StructuralInputError(
            f"computed {name} is not finite and positive ({value!r}); "
            "check geometry for underflow/overflow"
        )
    return value


def _validate_box(box: BoxSizing) -> tuple[float, float, float, float, int]:
    """Validate wing-box geometry → (chord, t/c, cap_area, web_t, n_spars)."""
    chord = _positive("box.chord_root_m", box.get("chord_root_m"))
    tc = _positive("box.thickness_to_chord", box.get("thickness_to_chord"))
    cap = _positive("box.cap_area_m2", box.get("cap_area_m2"))
    web_t = _positive("box.spar_web_thickness_m", box.get("spar_web_thickness_m"))
    n_spars = box.get("n_spars", 2)
    if isinstance(n_spars, bool) or not isinstance(n_spars, numbers.Integral) or n_spars < 1:
        raise StructuralInputError(f"box.n_spars must be an integer >= 1, got {n_spars!r}")
    return chord, tc, cap, web_t, int(n_spars)


def _validate_load_factor(load_factor: object) -> float:
    return _positive("load_factor", load_factor)


def _box_height(box: BoxSizing) -> float:
    """Structural box height h = (t/c)·chord at the analysed station."""
    return box["thickness_to_chord"] * box["chord_root_m"]


def _signed_stress_result(
    stress: float, allowable: float, *, compression_evaluated: Optional[bool] = None,
) -> MarginResult:
    """Build a MarginResult from a signed stress and a sign-independent allowable."""
    if not math.isfinite(stress):
        raise StructuralInputError(
            "computed stress is not finite (load too large for the section)"
        )
    magnitude = abs(stress)
    if magnitude == 0.0:
        ms, status = math.inf, "zero_load"
    else:
        ms = allowable / magnitude - 1.0
        if not math.isfinite(ms):
            raise StructuralInputError(
                "computed margin of safety is not finite (stress magnitude underflow)"
            )
        status = "evaluated"
    result: MarginResult = {
        "stress_pa":           stress,
        "stress_magnitude_pa": magnitude,
        "allowable_pa":        allowable,
        "margin_of_safety":    ms,
        "passed":              ms >= 0.0,
        "status":              status,
    }
    if compression_evaluated is not None:
        result["compression_evaluated"] = compression_evaluated
    return result


# ---------------------------------------------------------------------------
# Structural margins (single-cell wing box at one station)
# ---------------------------------------------------------------------------

def bending_margin(
    box: BoxSizing,
    bending_moment_nm: float,
    material: MaterialAllowables,
    *,
    load_factor: float = 1.0,
) -> MarginResult:
    """Spar-cap bending margin of safety (tension-cap check).

    Idealisation: two spar caps of area ``cap_area_m2`` separated by the box
    height h, so the second moment of area is I = 2·A_cap·(h/2)².  Extreme-fibre
    stress σ = M·(h/2)/I with M scaled by ``load_factor`` (pass the limit moment
    with load_factor = n_ult, or an already-ultimate moment with load_factor = 1).

    Sign convention: the moment is signed.  Either sign puts one cap in
    tension with |σ| = |M|·(h/2)/I, so the margin uses the magnitude:
    MS = Ftu/|σ| − 1.  ``stress_pa`` keeps the sign; ``stress_magnitude_pa``
    is |σ|.  Cap compression/buckling is NOT evaluated for either sign
    (``compression_evaluated`` is False).  ``load_factor`` must be > 0; the
    sign belongs to the load.  An exactly-zero moment gives status
    "zero_load" with MS = inf.

    Raises ``StructuralInputError`` for invalid inputs; never substitutes zero
    stress for a degenerate section.
    """
    chord, tc, cap, _web, _n = _validate_box(box)
    allowable = _positive("material.ftu_pa", material["ftu_pa"])
    lf = _validate_load_factor(load_factor)
    moment = _real("bending_moment_nm", bending_moment_nm) * lf

    h = _computed_positive("box height", tc * chord)
    inertia = _computed_positive("section inertia", 2.0 * cap * (h / 2.0) * (h / 2.0))
    sigma = moment * (h / 2.0) / inertia
    return _signed_stress_result(sigma, allowable, compression_evaluated=False)


def shear_margin(
    box: BoxSizing,
    shear_force_n: float,
    material: MaterialAllowables,
    *,
    load_factor: float = 1.0,
) -> MarginResult:
    """Spar-web shear margin of safety.

    Average web shear stress τ = V / (n_spars·h·t_web), V scaled by
    ``load_factor``.  The shear allowable is sign-independent, so the margin
    uses the magnitude: MS = Fsu/|τ| − 1.  ``stress_pa`` keeps the sign.
    Web shear buckling is NOT evaluated.  Zero shear gives status "zero_load".

    Raises ``StructuralInputError`` for invalid inputs.
    """
    chord, tc, _cap, web_t, n_spars = _validate_box(box)
    allowable = _positive("material.fsu_pa", material["fsu_pa"])
    lf = _validate_load_factor(load_factor)
    shear = _real("shear_force_n", shear_force_n) * lf

    h = _computed_positive("box height", tc * chord)
    web_area = _computed_positive("web area", n_spars * h * web_t)
    tau = shear / web_area
    return _signed_stress_result(tau, allowable)


# ---------------------------------------------------------------------------
# High-level convenience
# ---------------------------------------------------------------------------

def _validate_inputs(
    spec: AircraftSpec,
    wing: WingSizing,
    cop_frac: object,
) -> None:
    _positive("spec.mtow_kg", spec["mtow_kg"])
    _positive("spec.cruise_speed_ms", spec["cruise_speed_ms"])
    _real("spec.cruise_alt_m", spec["cruise_alt_m"])
    for key in ("cl_max", "lift_curve_slope", "vne_factor"):
        if spec.get(key) is not None:
            _positive(f"spec.{key}", spec[key])
    if spec.get("gust_velocity_ms") is not None:
        _nonnegative("spec.gust_velocity_ms", spec["gust_velocity_ms"])
    _positive("wing.area_m2", wing["area_m2"])
    for key in ("span_m", "mean_chord_m"):
        if wing.get(key) is not None:
            _positive(f"wing.{key}", wing[key])
    cop = _real("center_of_pressure_frac", cop_frac)
    if not 0.0 < cop <= 1.0:
        raise StructuralInputError(
            f"center_of_pressure_frac must be in (0, 1], got {cop_frac!r}"
        )


def _case_margin(case: LoadCase) -> float:
    ms = case["shear"]["margin_of_safety"]
    if case["bending"].get("status") != "not_evaluated":
        ms = min(ms, case["bending"]["margin_of_safety"])
    return ms


def analyze_wing_structure(
    spec: AircraftSpec,
    wing: WingSizing,
    box: BoxSizing,
    *,
    material: str = "AL2024-T3",
    center_of_pressure_frac: float = 0.4244,
    root_bending_moment_nm: Optional[float] = None,
    root_shear_force_n: Optional[float] = None,
) -> StructuralReport:
    """Full cruise + envelope + structural pass for one wing-box station.

    Pipeline:
      1. ISA atmosphere at cruise altitude → ρ, q.
      2. Cruise CL from lift = weight.
      3. Flight envelope → ultimate load factors n_ult⁺ and n_ult⁻ (< 0).
      4. Estimate ultimate root loads (unless supplied), for each of the
         positive and negative ultimate cases:
           total ultimate lift  L = n_ult·W        (signed)
           root shear            V = L / 2          (one semi-span)
           root bending          M = (L/2)·(cop·semi_span) (elliptical default)
      5. Bending & shear margins against the chosen material.

    Load cases: with estimated loads both ``positive_ultimate`` and
    ``negative_ultimate`` are evaluated and reported in ``load_cases``; the
    case with the lower margin governs (ties prefer positive) and populates
    the top-level ``root_*``/``bending``/``shear`` keys.  Explicitly supplied
    loads form one signed case named ``explicit`` used verbatim (already
    ultimate); a component not supplied is estimated from the positive case.
    Overall ``passed`` needs every case to pass, bending to be evaluated and
    the operating condition to be feasible.

    Signed-load model: symmetric two-cap idealisation, one tensile allowable
    Ftu and a sign-independent shear allowable Fsu; margins use stress
    magnitude.  Cap compression/buckling and web shear buckling are not
    evaluated.  Results are an ``analytic_sizing_estimate`` (not certified).

    ``center_of_pressure_frac`` defaults to 0.4244 (centroid of an elliptical
    lift distribution over the semi-span).

    Raises ``StructuralInputError`` for invalid inputs, ``KeyError`` for an
    unknown material.
    """
    warnings: list[str] = []
    material_id = material.strip().upper()
    mat = get_material(material_id)

    _validate_inputs(spec, wing, center_of_pressure_frac)
    _validate_box(box)
    if root_bending_moment_nm is not None:
        _real("root_bending_moment_nm", root_bending_moment_nm)
    if root_shear_force_n is not None:
        _real("root_shear_force_n", root_shear_force_n)

    cruise = isa_atmosphere(spec["cruise_alt_m"])
    rho = cruise["density_kgm3"]
    speed = spec["cruise_speed_ms"]
    weight_n = spec["mtow_kg"] * G0

    q = dynamic_pressure(rho, speed)
    cl = cruise_cl(spec["mtow_kg"], rho, speed, wing["area_m2"])
    wing_loading = weight_n / wing["area_m2"]

    cl_max = spec.get("cl_max", 1.5)
    if cl > cl_max:
        warnings.append(
            f"cruise CL {cl:.3f} exceeds CL_max {cl_max:.3f} — cannot sustain "
            "level flight at this speed/altitude/weight."
        )

    env = load_factor_envelope(spec, wing)
    span = wing.get("span_m")
    bending_evaluated = root_bending_moment_nm is not None or bool(span)
    if not bending_evaluated:
        warnings.append(
            "wing span_m not provided — cannot estimate root bending "
            "moment; pass root_bending_moment_nm explicitly."
        )

    def _estimate(n_ult: float) -> tuple[float, float]:
        """Signed (shear, bending) estimate for ultimate load factor n_ult."""
        lift_ult = n_ult * weight_n
        shear_est = lift_ult / 2.0
        if span:
            bending_est = (lift_ult / 2.0) * (center_of_pressure_frac * span / 2.0)
        else:
            bending_est = 0.0
        return shear_est, bending_est

    def _make_case(name: str, load_factor: float, shear_n: float, bending_nm: float) -> LoadCase:
        # Loads are already ultimate → load_factor = 1.0 in the margin calls.
        bending = bending_margin(box, bending_nm, mat, load_factor=1.0)
        shear = shear_margin(box, shear_n, mat, load_factor=1.0)
        # A compatibility zero placeholder is not an evaluated load case.
        # Preserve numeric fields for existing consumers, but never expose it
        # as a pass.
        bending["evaluated"] = bending_evaluated
        if not bending_evaluated:
            bending["passed"] = False
            bending["status"] = "not_evaluated"
        return {
            "name":                   name,
            "load_factor":            load_factor,
            "root_bending_moment_nm": bending_nm,
            "root_shear_force_n":     shear_n,
            "bending":                bending,
            "shear":                  shear,
            "passed":                 bending["passed"] and shear["passed"],
        }

    cases: list[LoadCase] = []
    if root_bending_moment_nm is None and root_shear_force_n is None:
        for case_name, n_ult in (("positive_ultimate", env["n_ultimate_pos"]),
                                 ("negative_ultimate", env["n_ultimate_neg"])):
            shear_n, bending_nm = _estimate(n_ult)
            cases.append(_make_case(case_name, n_ult, shear_n, bending_nm))
    else:
        shear_est, bending_est = _estimate(env["n_ultimate_pos"])
        cases.append(_make_case(
            "explicit", 1.0,
            shear_est if root_shear_force_n is None else root_shear_force_n,
            bending_est if root_bending_moment_nm is None else root_bending_moment_nm,
        ))

    governing = cases[0]
    for case in cases[1:]:
        if _case_margin(case) < _case_margin(governing):
            governing = case

    warnings.append(
        "spar-cap compression/buckling and web shear buckling are not "
        "evaluated; tension-cap and average web shear checks only "
        "(analytic sizing estimate)."
    )
    operating_condition_feasible = math.isfinite(cl) and 0 < cl <= cl_max

    return {
        "cruise":                 cruise,
        "dynamic_pressure_pa":    q,
        "cl_cruise":              cl,
        "wing_loading_pa":        wing_loading,
        "envelope":               env,
        "root_bending_moment_nm": governing["root_bending_moment_nm"],
        "root_shear_force_n":     governing["root_shear_force_n"],
        "bending":                governing["bending"],
        "shear":                  governing["shear"],
        "load_cases":             cases,
        "governing_load_case":    governing["name"],
        "material":               material_id,
        "method":                 "analytic_sizing_estimate",
        "engineering_certified":  False,
        "passed":                 all(c["passed"] for c in cases) and operating_condition_feasible,
        "warnings":               warnings,
    }
