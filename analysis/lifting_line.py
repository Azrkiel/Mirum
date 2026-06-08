"""
analysis/lifting_line.py — Prandtl lifting-line theory (pure-Python)
====================================================================

A self-contained implementation of classical Prandtl lifting-line theory via
the Glauert Fourier-sine-series (monoplane) method.  Imports are limited to
``math`` and ``typing`` — NO numpy, NO external binaries — mirroring the
standalone-analysis pattern of ``analysis/airfoil.py``.

Theory (Anderson, *Fundamentals of Aerodynamics*, Ch. 5)
--------------------------------------------------------
The spanwise circulation is expanded as a Fourier sine series in the
coordinate θ defined by ``y = -(b/2)·cos θ``, θ ∈ (0, π):

    Γ(θ) = 2 b V Σ_n A_n sin(n θ)

Substituting into the fundamental monoplane wing equation and collocating at
N spanwise stations gives a linear system for the coefficients A_n:

    Σ_n A_n sin(nθ) [ μ n / sin θ + 1 ] = μ (α(θ) − α_L0)

where μ = a0·c(θ) / (4 b) is the local geometry parameter, a0 is the 2-D
lift-curve slope, c is the local chord, and α(θ) is the local geometric AoA
(root AoA plus local twist).  Once the A_n are known:

    CL_3D = π · AR · A1
    CDi   = π · AR · Σ_n n · A_n²
    δ     = Σ_{n≥2} n · (A_n / A1)²            (induced-drag factor)
    e     = 1 / (1 + δ)                        (span efficiency)

so that, equivalently, CDi = CL_3D² / (π · e · AR).

For a SYMMETRIC planform/loading only the odd coefficients are nonzero; this
implementation collocates with all n (1..N) but a symmetric, untwisted,
elliptic wing recovers the textbook result that only A1 is nonzero, hence
δ → 0 and e → 1.

v1 LIMITATION (documented per the milestone spec)
-------------------------------------------------
A single CONSTANT ``sectional_cl_alpha_per_rad`` (a0) and a single constant
``sectional_cl_alpha0_deg`` (α_L0) are applied at EVERY spanwise station.  Real
wings see a Reynolds-number variation along the span (chord and local velocity
change), which makes both the 2-D lift-curve slope and the zero-lift angle vary
station-to-station.  Capturing that would require a per-station XFOIL polar (or
an interpolated a0(Re) table).  That refinement is intentionally deferred — v1
is the analytic baseline.  AVL (``analysis/avl.py``) is the opt-in
higher-fidelity path.
"""

from __future__ import annotations

import math
from typing import TypedDict


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

class WingPolarPoint(TypedDict):
    """One finite-wing operating point from lifting-line theory.

    Shares ``CL_3D`` / ``CDi`` / ``span_efficiency_e`` with the AVL wrapper
    (``analysis/avl.py``) so the two analyses are interchangeable on those
    headline quantities.
    """
    CL_3D:             float        # 3-D wing lift coefficient
    CDi:               float        # induced drag coefficient (inviscid)
    span_efficiency_e: float        # Oswald-style span efficiency, 1/(1+δ)
    L_distribution:    list[float]  # lift per unit span [N/m] at the N stations
    Cl_local:          list[float]  # local section Cl at the N stations
    passed:            bool         # False if inputs were degenerate
    warnings:          list[str]    # human-readable notes


# ---------------------------------------------------------------------------
# B2 — lift-curve slope + zero-lift angle from a Cl–alpha polar
# ---------------------------------------------------------------------------

def lift_curve_slope_and_alpha0(
    alpha_deg: list[float],
    cl: list[float],
    *,
    linear_window_deg: float = 8.0,
) -> tuple[float, float]:
    """Least-squares linear fit of the LINEAR region of a Cl–alpha polar.

    Bridges an XFOIL ``PolarResult`` to lifting-line theory: pass
    ``PolarResult["alpha"]`` and ``PolarResult["Cl"]`` directly (this function
    does not import ``airfoil`` to stay decoupled).

    The linear pre-stall region is isolated by (a) restricting to
    ``|alpha| <= linear_window_deg`` and (b) trimming any points at or beyond
    the Cl_max knee (so a post-stall drop cannot tilt the fit).  The slope is
    fitted in per-degree then converted to per-radian; the zero-lift angle is
    ``alpha_L0 = -intercept / slope``.

    Parameters
    ----------
    alpha_deg, cl : list[float]
        Equal-length angle-of-attack [deg] and lift-coefficient samples.
    linear_window_deg : float
        Half-width of the AoA window kept for the fit (default 8°).

    Returns
    -------
    (cl_alpha_per_rad, alpha_L0_deg) : tuple[float, float]

    Raises
    ------
    ValueError
        If fewer than two usable points remain after trimming, or the fitted
        slope is non-positive (degenerate / vertical data).
    """
    if len(alpha_deg) != len(cl):
        raise ValueError(
            f"alpha_deg and cl length mismatch: {len(alpha_deg)} vs {len(cl)}"
        )
    if len(alpha_deg) < 2:
        raise ValueError("need at least 2 (alpha, Cl) points to fit a slope")

    # Trim everything at or past the Cl_max knee (post-stall drop).
    cl_max = max(cl)
    knee_idx = cl.index(cl_max)
    a_trim = alpha_deg[: knee_idx + 1]
    c_trim = cl[: knee_idx + 1]

    # Keep only the linear window around alpha=0.
    pts = [
        (a, c)
        for a, c in zip(a_trim, c_trim)
        if abs(a) <= linear_window_deg
    ]
    # If the window is too aggressive (e.g. all data above 8°), fall back to
    # the pre-knee points so we still have something to fit.
    if len(pts) < 2:
        pts = list(zip(a_trim, c_trim))
    if len(pts) < 2:
        raise ValueError("not enough linear-region points to fit a slope")

    n = len(pts)
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        raise ValueError("degenerate alpha distribution (zero variance)")

    slope_per_deg = (n * sxy - sx * sy) / denom
    intercept = (sy - slope_per_deg * sx) / n
    if slope_per_deg <= 0.0:
        raise ValueError(
            f"fitted lift-curve slope is non-positive ({slope_per_deg:.4f}/deg)"
        )

    slope_per_rad = slope_per_deg * 180.0 / math.pi
    alpha_l0_deg = -intercept / slope_per_deg
    return slope_per_rad, alpha_l0_deg


# ---------------------------------------------------------------------------
# Pure-Python linear-algebra helpers (no numpy)
# ---------------------------------------------------------------------------

def _solve_linear_system(
    a_matrix: list[list[float]],
    b_vector: list[float],
) -> list[float]:
    """Solve A x = b by Gaussian elimination with partial pivoting.

    Square dense system; ``a_matrix`` is modified in place on a local copy.
    Raises ZeroDivisionError-free: a singular pivot raises ValueError.
    """
    n = len(b_vector)
    # Build an augmented copy so the caller's data is untouched.
    m = [row[:] + [b_vector[i]] for i, row in enumerate(a_matrix)]

    for col in range(n):
        # Partial pivot: largest magnitude in this column at/below the diagonal.
        pivot_row = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot_row][col]) < 1e-15:
            raise ValueError("singular matrix in lifting-line solve")
        if pivot_row != col:
            m[col], m[pivot_row] = m[pivot_row], m[col]

        pivot = m[col][col]
        for r in range(col + 1, n):
            factor = m[r][col] / pivot
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]

    # Back-substitution.
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = m[i][n]
        for j in range(i + 1, n):
            s -= m[i][j] * x[j]
        x[i] = s / m[i][i]
    return x


def _trapz(y: list[float], x: list[float]) -> float:
    """Trapezoidal integral of y over the (monotone) x stations."""
    total = 0.0
    for i in range(len(x) - 1):
        total += 0.5 * (y[i] + y[i + 1]) * (x[i + 1] - x[i])
    return total


# ---------------------------------------------------------------------------
# B1 — Prandtl lifting-line orchestrator
# ---------------------------------------------------------------------------

def _degenerate(warnings: list[str]) -> WingPolarPoint:
    """Build a uniform failure result carrying the accumulated warnings."""
    return WingPolarPoint(
        CL_3D=0.0,
        CDi=0.0,
        span_efficiency_e=0.0,
        L_distribution=[],
        Cl_local=[],
        passed=False,
        warnings=warnings,
    )


def prandtl_lifting_line(
    chord_distribution_m: list[float],
    twist_distribution_deg: list[float],
    sectional_cl_alpha_per_rad: float,
    sectional_cl_alpha0_deg: float,
    span_m: float,
    aoa_root_deg: float,
    velocity_ms: float,
    density_kgm3: float,
    n_fourier_terms: int = 40,
) -> WingPolarPoint:
    """Solve classical Prandtl lifting-line theory at one operating point.

    Parameters
    ----------
    chord_distribution_m : list[float]
        Local chord [m] at N spanwise stations (the stations are taken to be
        cosine-spaced in θ over the FULL span; see module docstring).
    twist_distribution_deg : list[float]
        Local geometric twist [deg] at the same N stations (added to the root
        AoA to give the local geometric angle of attack).
    sectional_cl_alpha_per_rad : float
        2-D lift-curve slope a0 [1/rad] (≈ 2π).  Applied at every station (v1).
    sectional_cl_alpha0_deg : float
        2-D zero-lift angle α_L0 [deg].  Applied at every station (v1).
    span_m : float
        Full wing span b [m].
    aoa_root_deg : float
        Root geometric angle of attack [deg].
    velocity_ms, density_kgm3 : float
        Freestream speed [m/s] and density [kg/m³] (for dimensional L'(y)).
    n_fourier_terms : int
        Number of Fourier-sine terms / collocation stations (default 40).
        Clamped to the number of chord stations supplied.

    Returns
    -------
    WingPolarPoint
        On success ``passed=True`` with CL_3D / CDi / span_efficiency_e and the
        spanwise distributions.  On degenerate input ``passed=False`` with a
        non-empty ``warnings`` list and no exception raised.
    """
    warnings: list[str] = []

    # ── Input validation (graceful degradation, no crashes) ─────────────────
    n_stations = len(chord_distribution_m)
    if n_stations != len(twist_distribution_deg):
        warnings.append(
            f"chord ({n_stations}) and twist ({len(twist_distribution_deg)}) "
            "station counts differ"
        )
        return _degenerate(warnings)
    if n_stations < 2:
        warnings.append(
            f"need at least 2 spanwise stations, got {n_stations}"
        )
        return _degenerate(warnings)
    if span_m <= 0.0:
        warnings.append(f"span must be positive, got {span_m}")
        return _degenerate(warnings)
    if velocity_ms <= 0.0 or density_kgm3 <= 0.0:
        warnings.append("velocity and density must be positive")
        return _degenerate(warnings)
    if sectional_cl_alpha_per_rad <= 0.0:
        warnings.append(
            f"sectional_cl_alpha must be positive, got {sectional_cl_alpha_per_rad}"
        )
        return _degenerate(warnings)
    if any(c <= 0.0 for c in chord_distribution_m):
        warnings.append("all chords must be strictly positive")
        return _degenerate(warnings)

    if n_fourier_terms < 1:
        warnings.append(
            f"n_fourier_terms must be >= 1, got {n_fourier_terms}"
        )
        return _degenerate(warnings)

    # We collocate at exactly as many stations as Fourier terms, but cannot
    # use more terms than chord stations supplied.
    n_terms = n_fourier_terms
    if n_terms > n_stations:
        warnings.append(
            f"n_fourier_terms ({n_fourier_terms}) clamped to station count "
            f"({n_stations})"
        )
        n_terms = n_stations

    b = span_m
    a0 = sectional_cl_alpha_per_rad
    alpha_l0_rad = math.radians(sectional_cl_alpha0_deg)

    # ── Collocation stations: θ_k in (0, π) matching the chord sampling ──────
    # The chord list is defined at θ_i = π·(i+1)/(N+1); for the solve we use the
    # first n_terms of those same stations so chord[i] aligns with θ_i.
    thetas = [math.pi * (i + 1) / (n_stations + 1) for i in range(n_stations)]

    # Pick the subset of stations used as collocation points (evenly spread).
    if n_terms == n_stations:
        coll_idx = list(range(n_stations))
    else:
        coll_idx = [
            round(i * (n_stations - 1) / (n_terms - 1)) if n_terms > 1 else 0
            for i in range(n_terms)
        ]
        # De-duplicate while preserving order (rounding can collide).
        seen: set[int] = set()
        uniq: list[int] = []
        for idx in coll_idx:
            if idx not in seen:
                seen.add(idx)
                uniq.append(idx)
        coll_idx = uniq
        if len(coll_idx) < n_terms:
            n_terms = len(coll_idx)
            warnings.append(
                f"collocation stations reduced to {n_terms} after de-duplication"
            )

    # ── Assemble the linear system  M·A = rhs  for coefficients A_1..A_n ─────
    # Fundamental monoplane equation, collocated at θ_k:
    #   Σ_n A_n sin(nθ_k) [ 1 + μ_k · n / sin θ_k ] = μ_k · (α_k − α_L0)
    # with μ_k = a0 · c_k / (4 b).
    m_matrix: list[list[float]] = []
    rhs: list[float] = []
    for k in coll_idx:
        theta = thetas[k]
        sin_t = math.sin(theta)
        c_k = chord_distribution_m[k]
        mu = a0 * c_k / (4.0 * b)
        alpha_k = math.radians(aoa_root_deg + twist_distribution_deg[k])

        row: list[float] = []
        for j in range(1, n_terms + 1):
            row.append(math.sin(j * theta) * (1.0 + mu * j / sin_t))
        m_matrix.append(row)
        rhs.append(mu * (alpha_k - alpha_l0_rad))

    try:
        coeffs = _solve_linear_system(m_matrix, rhs)
    except ValueError as exc:
        warnings.append(f"linear solve failed: {exc}")
        return _degenerate(warnings)

    a1 = coeffs[0]
    if abs(a1) < 1e-12:
        # Zero net circulation (e.g. zero effective AoA) — valid but produces a
        # degenerate efficiency.  Report a clean zero-lift point.
        warnings.append("A1 ≈ 0 (zero net lift at this condition)")

    # ── Planform area + aspect ratio (trapezoidal over the span) ─────────────
    # y stations: y = -(b/2) cos θ.
    ys = [-(b / 2.0) * math.cos(t) for t in thetas]
    # Augment with the exact tips so the trapezoid captures the full planform.
    ys_full = [-b / 2.0] + ys + [b / 2.0]
    chords_full = [0.0] + list(chord_distribution_m) + [0.0]
    area = _trapz(chords_full, ys_full)
    if area <= 0.0:
        warnings.append("computed planform area is non-positive")
        return _degenerate(warnings)
    aspect_ratio = b * b / area

    # ── Headline coefficients ────────────────────────────────────────────────
    cl_3d = math.pi * aspect_ratio * a1

    sum_n_an2 = sum((j + 1) * coeffs[j] ** 2 for j in range(n_terms))
    cdi = math.pi * aspect_ratio * sum_n_an2

    if abs(a1) > 1e-12:
        delta = sum(
            (j + 1) * (coeffs[j] / a1) ** 2 for j in range(1, n_terms)
        )
    else:
        delta = 0.0
    span_eff = 1.0 / (1.0 + delta) if (1.0 + delta) > 0 else 0.0

    # ── Spanwise distributions at ALL supplied stations ──────────────────────
    # Γ(θ) = 2 b V Σ_n A_n sin(nθ);  L'(y) = ρ V Γ;  Cl_local = L'/(½ρV²c)
    l_dist: list[float] = []
    cl_local: list[float] = []
    q = 0.5 * density_kgm3 * velocity_ms * velocity_ms
    for i, theta in enumerate(thetas):
        gamma = 2.0 * b * velocity_ms * sum(
            coeffs[j] * math.sin((j + 1) * theta) for j in range(n_terms)
        )
        l_prime = density_kgm3 * velocity_ms * gamma
        l_dist.append(l_prime)
        c_i = chord_distribution_m[i]
        cl_local.append(l_prime / (q * c_i) if c_i > 0 else 0.0)

    passed = abs(a1) > 1e-12 and not any(
        w.startswith(("chord", "need", "span", "velocity", "sectional", "all"))
        for w in warnings
    )

    return WingPolarPoint(
        CL_3D=cl_3d,
        CDi=cdi,
        span_efficiency_e=span_eff,
        L_distribution=l_dist,
        Cl_local=cl_local,
        passed=passed,
        warnings=warnings,
    )
