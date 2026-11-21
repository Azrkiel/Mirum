"""Independent reference solutions for a rectangular Kirchhoff plate under uniform pressure.

Written separately from the finite-element solver. Imports only the standard
library and NumPy (no SciPy, no CAD kernel).

Conventions: the plate occupies [0, a] x [0, b], a >= b, a along X, b along Y;
uniform pressure q acts in -Z; the deflection w is reported as a positive
magnitude in the load direction; D = E h^3 / (12 (1 - nu^2));

    Mx  = -D (w_xx + nu w_yy)
    My  = -D (w_yy + nu w_xx)
    Mxy = -D (1 - nu) w_xy

with w measured in the load direction, so the simply supported (SS) centre
moments are positive (sagging). Surface stresses are sigma = 6 M / h^2 and the
von Mises stress is sqrt(sx^2 - sx sy + sy^2 + 3 txy^2). Because b is the short
side, the large moment of a long plate is My (curvature across the short side).

Contents
- R1 `navier_ss`: SS Navier double sine series, full field.
- R2 `fd_plate`: finite-difference biharmonic, SS or clamped, Richardson extrapolated.
- R4 `strip_limits` / `check_strip_limit`: a/b -> infinity limits.

All functions take plain floats, validate them, and return plain dicts of
floats, ints, bools and lists (JSON-serialisable).
"""

from __future__ import annotations

import math

import numpy as np

from .plate_spec import PLATE_NUMERICAL_V1

# Contract numbers (E03 contract, "plate_numerical_v1" and the reference table).
NAVIER_RELATIVE_STOP = PLATE_NUMERICAL_V1['navier_relative_stop']
NAVIER_TERM_CAP = PLATE_NUMERICAL_V1['navier_term_cap']
FD_GRID_MULTIPLIERS = PLATE_NUMERICAL_V1['fd_grid_multipliers']
STRIP_LIMIT_RELATIVE = PLATE_NUMERICAL_V1['strip_limit_relative']

_FD_MAX_UNKNOWNS = 6000
# Default grids are chosen so the finest system stays well inside the cap (time, memory).
_FD_DEFAULT_TARGET_UNKNOWNS = 4200
_FD_MAX_DEFAULT_N = 16
_NAVIER_TERM_STEPS = (13, 25, 51, 101, 201, 401)
_EDGE_CONDITIONS = ("simply_supported", "clamped")


# ---------------------------------------------------------------- validation


def _positive_finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise ValueError(f"{name} must be a real number")
    v = float(value)
    if not math.isfinite(v) or v <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return v


def _validate(a, b, h, q, E, nu):
    a = _positive_finite("a", a)
    b = _positive_finite("b", b)
    h = _positive_finite("h", h)
    q = _positive_finite("q", q)
    E = _positive_finite("E", E)
    nu = _positive_finite("nu", nu)
    if a < b:
        raise ValueError("a must be >= b")
    if not nu < 0.5:
        raise ValueError("nu must be in (0, 0.5)")
    return a, b, h, q, E, nu


def _flexural_rigidity(E: float, h: float, nu: float) -> float:
    return E * h**3 / (12.0 * (1.0 - nu * nu))


def _von_mises(sx, sy, txy):
    return np.sqrt(np.maximum(sx * sx - sx * sy + sy * sy + 3.0 * txy * txy, 0.0))


# ---------------------------------------------------------------- R1: Navier series


def _navier_pass(a, b, h, q, D, nu, x, y, terms):
    """Fields on the grid x (rows) by y (columns) with `terms` odd terms in m and n."""
    k = np.arange(terms, dtype=np.float64)
    m = 2.0 * k + 1.0
    al = m * math.pi / a
    be = m * math.pi / b
    sx = np.sin(np.outer(x, al))
    cx = np.cos(np.outer(x, al))
    sy = np.sin(np.outer(y, be))
    cy = np.cos(np.outer(y, be))
    denom = (al[:, None] ** 2 + be[None, :] ** 2) ** 2
    # w_mn = 16 q / (pi^6 D m n (m^2/a^2 + n^2/b^2)^2) = 16 q / (pi^2 D m n (al^2 + be^2)^2 ) / ... (pi folded in al, be)
    wmn = 16.0 * q / (D * math.pi**2 * (m[:, None] * m[None, :]) * denom)
    w = sx @ wmn @ sy.T
    mx = sx @ (D * wmn * (al[:, None] ** 2 + nu * be[None, :] ** 2)) @ sy.T
    my = sx @ (D * wmn * (be[None, :] ** 2 + nu * al[:, None] ** 2)) @ sy.T
    mxy = -(1.0 - nu) * D * (cx @ (wmn * al[:, None] * be[None, :]) @ cy.T)
    return w, mx, my, mxy


def _navier_summary(w, mx, my, mxy, h):
    vm = _von_mises(6.0 * mx / h**2, 6.0 * my / h**2, 6.0 * mxy / h**2)
    return {
        "w_max": float(w[-1, -1]),
        "max_abs_mx": float(np.abs(mx).max()),
        "max_abs_my": float(np.abs(my).max()),
        "max_abs_mxy": float(np.abs(mxy).max()),
        "vm_max": float(vm.max()),
        "mx_centre": float(mx[-1, -1]),
        "my_centre": float(my[-1, -1]),
    }, vm


def navier_ss(a, b, h, q, E, nu, grid=101, return_fields=False):
    """R1: all-edges simply supported plate, Navier double sine series (odd m, n).

    The field is evaluated by matrix products of sine/cosine tables with the
    coefficient matrix, on the quarter plate [0, a/2] x [0, b/2] (the field is
    symmetric about both mid-lines, so the maxima of every magnitude are in the
    quarter). The grid has `grid` points each way (default 101, minimum 11),
    includes both edges of the quarter, hence the plate corner (0, 0) and the
    centre (a/2, b/2) exactly.

    Truncation: the odd-term count K (same in m and n) steps through
    13, 25, 51, 101, 201, 401 until the relative change of every reported
    quantity (w_max, max |Mx|, max |My|, max |Mxy|, max von Mises, centre Mx,
    centre My) between consecutive steps is below NAVIER_RELATIVE_STOP, or K
    reaches NAVIER_TERM_CAP. The twisting moment converges only like 1/K^2 at
    the corners, so the cap is usually reached (converged False) with a
    last relative change near 1e-6 to 1e-5, driven by Mxy and the von Mises
    value at the corner; deflection and the centre moments are converged to
    near machine precision.

    Returns a dict: w_max (centre), mx_centre, my_centre, mxy_max_abs and
    mxy_max_location, vm_max with vm_max_location {x, y}, vm_max_index
    [ix, iy], vm_max_kind ('centre', 'corner' or 'other'), corner_force
    (R = 2 |Mxy(corner)|), terms_used, last_relative_change, converged, the
    grid lines and, if return_fields, the fields Mx, My, Mxy, w, vm.
    """
    a, b, h, q, E, nu = _validate(a, b, h, q, E, nu)
    if isinstance(grid, bool) or not isinstance(grid, (int, np.integer)) or grid < 11:
        raise ValueError("grid must be an integer >= 11")
    grid = int(grid)
    D = _flexural_rigidity(E, h, nu)
    x = np.linspace(0.0, a / 2.0, grid)
    y = np.linspace(0.0, b / 2.0, grid)

    previous = None
    last_change = math.inf
    converged = False
    fields = None
    summary = None
    terms_used = 0
    for terms in _NAVIER_TERM_STEPS:
        terms = min(terms, NAVIER_TERM_CAP)
        fields = _navier_pass(a, b, h, q, D, nu, x, y, terms)
        summary, vm = _navier_summary(*fields, h)
        terms_used = terms
        if previous is not None:
            last_change = max(
                abs(summary[k] - previous[k]) / max(abs(summary[k]), 1e-300) for k in summary
            )
            if last_change < NAVIER_RELATIVE_STOP:
                converged = True
                break
        previous = summary
        if terms >= NAVIER_TERM_CAP:
            break

    w, mx, my, mxy = fields
    ix, iy = (int(i) for i in np.unravel_index(int(np.argmax(vm)), vm.shape))
    if ix == 0 and iy == 0:
        kind = "corner"
    elif ix == grid - 1 and iy == grid - 1:
        kind = "centre"
    else:
        kind = "other"
    mix, miy = (int(i) for i in np.unravel_index(int(np.argmax(np.abs(mxy))), mxy.shape))
    result = {
        "w_max": summary["w_max"],
        "mx_centre": summary["mx_centre"],
        "my_centre": summary["my_centre"],
        "max_abs_mx": summary["max_abs_mx"],
        "max_abs_my": summary["max_abs_my"],
        "mxy_max_abs": summary["max_abs_mxy"],
        "mxy_max_location": {"x": float(x[mix]), "y": float(y[miy])},
        "vm_max": summary["vm_max"],
        "vm_max_location": {"x": float(x[ix]), "y": float(y[iy])},
        "vm_max_index": [ix, iy],
        "vm_max_kind": kind,
        "corner_force": float(2.0 * abs(mxy[0, 0])),
        "terms_used": int(terms_used),
        "last_relative_change": float(last_change),
        "converged": bool(converged),
        "grid_x": [float(v) for v in x],
        "grid_y": [float(v) for v in y],
    }
    if return_fields:
        result["fields"] = {
            "w": w.tolist(),
            "Mx": mx.tolist(),
            "My": my.tolist(),
            "Mxy": mxy.tolist(),
            "vm": vm.tolist(),
        }
    return result


# ---------------------------------------------------------------- R2: finite differences


def _axis_map(n: int, sign_ghost: float, count_before: int, count_after: int):
    """Index/sign maps along one axis for node numbers -count_before .. n+count_after.

    Nodes 0..n-1 are unknowns, node n is on the edge (w = 0), node 0 lies on a
    symmetry line (even reflection for k < 0). Nodes beyond the edge reflect
    about the edge node with `sign_ghost` (-1 simply supported, +1 clamped).
    Returns (index, sign) with index n meaning the zero node.
    """
    ks = np.arange(-count_before, n + count_after + 1)
    idx = np.empty(ks.shape, dtype=np.int64)
    sgn = np.ones(ks.shape, dtype=np.float64)
    for p, k in enumerate(ks):
        kk = abs(int(k)) if k < 0 else int(k)
        s = 1.0
        if kk > n:
            kk = 2 * n - kk
            s = sign_ghost
        idx[p] = kk
        sgn[p] = s
    return idx, sgn


def _fd_grid_solution(a, b, h, nu, sign_ghost, clamped, nx, ny):
    """Solve nabla^4 wt = 1 on the quarter plate and return the nodal scalars.

    Unknowns: nodes (i, j), i = 0..nx-1, j = 0..ny-1, at u = i hx, v = j hy from
    the plate centre. Returns scalar results in units q = 1, D = 1 for w and
    q = 1 for moments (multiply moments by q and w by q / D).
    """
    hx = (a / 2.0) / nx
    hy = (b / 2.0) / ny
    n_unknown = nx * ny

    ix_map, ix_sgn = _axis_map(nx, sign_ghost, 2, 2)
    iy_map, iy_sgn = _axis_map(ny, sign_ghost, 2, 2)

    cx4 = 1.0 / hx**4
    cy4 = 1.0 / hy**4
    cxy = 1.0 / (hx * hx * hy * hy)
    stencil = [
        (0, 0, 6.0 * cx4 + 6.0 * cy4 + 8.0 * cxy),
        (1, 0, -4.0 * cx4 - 4.0 * cxy),
        (-1, 0, -4.0 * cx4 - 4.0 * cxy),
        (0, 1, -4.0 * cy4 - 4.0 * cxy),
        (0, -1, -4.0 * cy4 - 4.0 * cxy),
        (2, 0, cx4),
        (-2, 0, cx4),
        (0, 2, cy4),
        (0, -2, cy4),
        (1, 1, 2.0 * cxy),
        (1, -1, 2.0 * cxy),
        (-1, 1, 2.0 * cxy),
        (-1, -1, 2.0 * cxy),
    ]

    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    rows = (ii * ny + jj).ravel()
    A = np.zeros((n_unknown, n_unknown), dtype=np.float64)
    for di, dj, coef in stencil:
        pi = ii.ravel() + di + 2
        pj = jj.ravel() + dj + 2
        ci = ix_map[pi]
        cj = iy_map[pj]
        sg = ix_sgn[pi] * iy_sgn[pj] * coef
        live = (ci < nx) & (cj < ny)
        np.add.at(A, (rows[live], ci[live] * ny + cj[live]), sg[live])
    rhs = np.ones(n_unknown, dtype=np.float64)
    sol = np.linalg.solve(A, rhs)
    del A

    wpad = np.zeros((nx + 1, ny + 1), dtype=np.float64)
    wpad[:nx, :ny] = sol.reshape(nx, ny)

    # Extended field for i = -1..nx+1, j = -1..ny+1.
    ex_map, ex_sgn = _axis_map(nx, sign_ghost, 1, 1)
    ey_map, ey_sgn = _axis_map(ny, sign_ghost, 1, 1)
    wext = wpad[np.ix_(ex_map, ey_map)] * ex_sgn[:, None] * ey_sgn[None, :]
    c = wext[1:-1, 1:-1]
    wxx = (wext[2:, 1:-1] - 2.0 * c + wext[:-2, 1:-1]) / hx**2
    wyy = (wext[1:-1, 2:] - 2.0 * c + wext[1:-1, :-2]) / hy**2
    wxy = (wext[2:, 2:] - wext[2:, :-2] - wext[:-2, 2:] + wext[:-2, :-2]) / (4.0 * hx * hy)
    if clamped:
        # One-sided second-order normal curvature on a clamped edge (w = w_x = 0 there).
        wxx[nx, :] = (8.0 * wpad[nx - 1, :] - wpad[nx - 2, :]) / (2.0 * hx**2)
        wyy[:, ny] = (8.0 * wpad[:, ny - 1] - wpad[:, ny - 2]) / (2.0 * hy**2)
        wxy[nx, :] = 0.0
        wxy[:, ny] = 0.0
    mx = -(wxx + nu * wyy)
    my = -(wyy + nu * wxx)
    mxy = -(1.0 - nu) * wxy
    vm = _von_mises(6.0 * mx / h**2, 6.0 * my / h**2, 6.0 * mxy / h**2)
    return {
        "w_centre": float(wpad[0, 0]),
        "mx_centre": float(mx[0, 0]),
        "my_centre": float(my[0, 0]),
        "edge_moment_long_edge": float(my[0, ny]),
        "edge_moment_short_edge": float(mx[nx, 0]),
        "mxy_max_abs": float(np.abs(mxy).max()),
        "vm_max": float(vm.max()),
    }


def _richardson(f1, f2, f3, p1=2, p2=4):
    """Eliminate error terms h^p1 then h^p2 over three grids with halving steps."""
    g1 = 2.0**p1
    g2 = 2.0**p2
    r12 = (g1 * f2 - f1) / (g1 - 1.0)
    r23 = (g1 * f3 - f2) / (g1 - 1.0)
    return (g2 * r23 - r12) / (g2 - 1.0)


# On a clamped edge the even-reflection ghost node is inconsistent at order 1/h at the
# first interior node (an even function has w_xxx = 0, the true solution does not), which
# is equivalent to a point force one cell from the wall. Deflection and interior moments
# stay O(h^2) but any quantity read at the clamped edge carries an O(h) error, so those
# are extrapolated with the orders (h, h^2) instead of (h^2, h^4).
_FD_CLAMPED_EDGE_QUANTITIES = ("edge_moment_long_edge", "edge_moment_short_edge", "vm_max")


def _fd_base_cells(n, a, b):
    return n, max(int(round(n * a / b)), 2)


def _fd_unknowns(n, a, b):
    ny0, nx0 = _fd_base_cells(n, a, b)
    m = max(FD_GRID_MULTIPLIERS)
    return (ny0 * m) * (nx0 * m)


def _fd_default_n(a, b):
    for n in range(_FD_MAX_DEFAULT_N, 1, -1):
        if _fd_unknowns(n, a, b) <= _FD_DEFAULT_TARGET_UNKNOWNS:
            return n
    raise ValueError("no finite-difference grid fits the unknown cap for this aspect ratio")


def fd_plate(a, b, h, q, E, nu, edge_condition, n=None):
    """R2: finite-difference biharmonic reference, all four edges simply supported or clamped.

    Method: the 13-point biharmonic stencil (valid for hx != hy) on the quarter
    plate, with even reflection across the two symmetry lines, w = 0 on the
    edges and ghost nodes beyond each edge by odd reflection (simply supported,
    w_-1 = -w_1) or even reflection (clamped, w_-1 = w_1). Grids use n, 2n, 4n
    cells across the quarter's short side (nx = round(n a / b) times the same
    factor, so the grids are nested and Richardson is consistent), each solved
    by dense numpy.linalg.solve. `n=None` picks the largest n <= 16 whose
    finest system is at most about 4200 unknowns; an explicit n whose finest
    system exceeds 6000 unknowns raises ValueError.

    Extrapolation: each reported scalar (centre deflection, centre Mx and My,
    My at the middle of the long edges, Mx at the middle of the short edges,
    max |Mxy| over the nodes, max von Mises over the nodes, computed per grid
    from the moment fields and then extrapolated as a scalar) is extrapolated
    with the h^2, h^4 Richardson formula over the three grids. Exception: for
    the clamped plate the quantities read at the clamped edge (both edge
    moments and the von Mises maximum, which sits on the edge) have an O(h)
    discretisation error (see _FD_CLAMPED_EDGE_QUANTITIES), so they use the
    h, h^2 formula. The orders used are returned under 'extrapolation_orders'
    and the plain h^2, h^4 result of every scalar under 'extrapolated_h2_h4'.
    The raw values on each grid are returned too, under 'raw'. Moments on a
    clamped edge use a one-sided second-order normal curvature; the simply
    supported edge moment is zero by construction. Expected accuracy
    (measured against Timoshenko, Imrak/Taylor-Govindjee and R1): deflection
    within about 1e-4 relative, centre moments within about 1e-3, edge moments
    and von Mises within a few 1e-3.

    Returns {'extrapolated', 'raw', 'extrapolation_orders',
    'extrapolated_h2_h4', 'richardson_h2' (two finest grids, h^2 only),
    'grids', 'unknowns', 'n', 'edge_condition'}.
    """
    a, b, h, q, E, nu = _validate(a, b, h, q, E, nu)
    if edge_condition not in _EDGE_CONDITIONS:
        raise ValueError(f"edge_condition must be one of {_EDGE_CONDITIONS}")
    if n is None:
        n = _fd_default_n(a, b)
    if isinstance(n, bool) or not isinstance(n, (int, np.integer)) or n < 2:
        raise ValueError("n must be an integer >= 2")
    n = int(n)
    if _fd_unknowns(n, a, b) > _FD_MAX_UNKNOWNS:
        raise ValueError("finest finite-difference system would exceed the 6000 unknown cap")

    D = _flexural_rigidity(E, h, nu)
    clamped = edge_condition == "clamped"
    sign_ghost = 1.0 if clamped else -1.0
    ny0, nx0 = _fd_base_cells(n, a, b)

    per_grid = []
    grids = []
    for mult in FD_GRID_MULTIPLIERS:
        nx, ny = nx0 * mult, ny0 * mult
        per_grid.append(_fd_grid_solution(a, b, h, nu, sign_ghost, clamped, nx, ny))
        grids.append({"multiplier": int(mult), "nx": int(nx), "ny": int(ny), "unknowns": int(nx * ny)})

    names = list(per_grid[0])
    scale = {name: (q / D if name == "w_centre" else q) for name in names}
    raw = {name: [float(g[name] * scale[name]) for g in per_grid] for name in names}
    orders = {
        name: ([1, 2] if clamped and name in _FD_CLAMPED_EDGE_QUANTITIES else [2, 4]) for name in names
    }
    extrapolated = {name: float(_richardson(*raw[name], *orders[name])) for name in names}
    extrapolated_h2_h4 = {name: float(_richardson(*raw[name], 2, 4)) for name in names}
    h2 = {name: float((4.0 * raw[name][2] - raw[name][1]) / 3.0) for name in names}
    return {
        "extrapolated": extrapolated,
        "raw": raw,
        "extrapolation_orders": orders,
        "extrapolated_h2_h4": extrapolated_h2_h4,
        "richardson_h2": h2,
        "grids": grids,
        "unknowns": [g["unknowns"] for g in grids],
        "n": n,
        "edge_condition": edge_condition,
    }


# ---------------------------------------------------------------- R4: strip limits


def strip_limits(b, h, q, E, nu):
    """R4: a/b -> infinity strip values (cylindrical bending of a strip of width b).

    Simply supported: w = 5 q b^4 / (384 D). Clamped: w = q b^4 / (384 D) and
    edge moment magnitude q b^2 / 12.
    """
    b = _positive_finite("b", b)
    h = _positive_finite("h", h)
    q = _positive_finite("q", q)
    E = _positive_finite("E", E)
    nu = _positive_finite("nu", nu)
    if not nu < 0.5:
        raise ValueError("nu must be in (0, 0.5)")
    D = _flexural_rigidity(E, h, nu)
    return {
        "D": float(D),
        "simply_supported": {"w": float(5.0 * q * b**4 / (384.0 * D))},
        "clamped": {
            "w": float(q * b**4 / (384.0 * D)),
            "edge_moment": float(q * b * b / 12.0),
        },
    }


def check_strip_limit(value, limit):
    """Relative error of `value` against `limit` and whether it is within STRIP_LIMIT_RELATIVE."""
    if isinstance(value, bool) or isinstance(limit, bool):
        raise ValueError("value and limit must be real numbers")
    v = float(value)
    lim = float(limit)
    if not (math.isfinite(v) and math.isfinite(lim)) or lim == 0.0:
        raise ValueError("value and limit must be finite and limit non-zero")
    rel = abs(v - lim) / abs(lim)
    return {"relative_error": float(rel), "passed": bool(rel <= STRIP_LIMIT_RELATIVE)}
