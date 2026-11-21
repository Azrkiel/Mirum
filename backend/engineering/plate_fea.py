"""Kirchhoff plate finite elements: conforming Bogner-Fox-Schmit element, uniform pressure.

Four-node rectangular element, DOF (w, w_x, w_y, w_xy) per node, bicubic Hermite shape functions,
consistent load vector, dense NumPy solve. Default model is one quarter of the plate with symmetry
lines; ``symmetry='full'`` models the whole plate and exists for test fixtures.

Units mm, N, MPa. Lengths are divided by the short side b and the solve is made with D = 1 and
q = 1, so the conditioning does not depend on a, h or E. ``w`` is reported as a positive magnitude in
the load direction; moments follow Mx = -D (w_xx + nu w_yy) with that w (SS centre moments positive).
"""
from __future__ import annotations

import math

import numpy as np

from .plate_spec import PLATE_NUMERICAL_V1

METHOD = 'bfs_kirchhoff_quarter_v1'
_EDGE_CONDITIONS = ('simply_supported', 'clamped')
_SYMMETRIES = ('quarter', 'full')
_CORNERS = ((0, 0), (1, 0), (1, 1), (0, 1))  # local node order
_CORNER_INDEX = {corner: index for index, corner in enumerate(_CORNERS)}
# Per local DOF d = 4*node + k: index into the 1-D Hermite basis (value0, slope0, value1, slope1).
_XI = np.array([2 * _CORNERS[d // 4][0] + (1 if d % 4 in (1, 3) else 0) for d in range(16)])
_YI = np.array([2 * _CORNERS[d // 4][1] + (1 if d % 4 in (2, 3) else 0) for d in range(16)])


# ------------------------------------------------------------------ element

def _hermite(s: np.ndarray, h: float, order: int) -> np.ndarray:
    s = np.asarray(s, dtype=float)
    if order == 0:
        cols = (1 - 3 * s**2 + 2 * s**3, h * (s - 2 * s**2 + s**3), 3 * s**2 - 2 * s**3, h * (-s**2 + s**3))
    elif order == 1:
        cols = ((-6 * s + 6 * s**2) / h, 1 - 4 * s + 3 * s**2, (6 * s - 6 * s**2) / h, -2 * s + 3 * s**2)
    else:
        cols = ((-6 + 12 * s) / h**2, (-4 + 6 * s) / h, (6 - 12 * s) / h**2, (-2 + 6 * s) / h)
    return np.stack(cols, axis=-1)


def _shape(hx: float, hy: float, s, t, px: int, py: int) -> np.ndarray:
    return _hermite(s, hx, px)[:, _XI] * _hermite(t, hy, py)[:, _YI]


def _curvature_matrix(hx: float, hy: float, s, t) -> np.ndarray:
    """(n, 3, 16): rows w_xx, w_yy, w_xy at local fractional coordinates (s, t) in [0, 1]."""
    return np.stack([_shape(hx, hy, s, t, 2, 0), _shape(hx, hy, s, t, 0, 2), _shape(hx, hy, s, t, 1, 1)], axis=1)


def _gauss(points: int):
    g, w = np.polynomial.legendre.leggauss(points)
    return 0.5 * (g + 1.0), 0.5 * w


def _quadrature(hx: float, hy: float):
    gp = PLATE_NUMERICAL_V1['stress_gauss_points_per_direction']
    g, w = _gauss(gp)
    s, t = np.meshgrid(g, g, indexing='ij')
    return s.ravel(), t.ravel(), (w[:, None] * w[None, :]).ravel() * hx * hy


def _check_element_args(hx: float, hy: float, nu: float) -> None:
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (hx, hy, nu)):
        raise ValueError('Finite element sizes and Poisson ratio required')
    if hx <= 0 or hy <= 0 or not 0 < nu < 0.5:
        raise ValueError('Positive element sizes and 0 < nu < 0.5 required')


def element_stiffness(hx: float, hy: float, nu: float) -> np.ndarray:
    """16x16 BFS stiffness for D = 1 (multiply by D); integrand degree 7 is exact with 4x4 Gauss."""
    _check_element_args(hx, hy, nu)
    s, t, weights = _quadrature(hx, hy)
    b = _curvature_matrix(hx, hy, s, t)
    e = np.array([[1.0, nu, 0.0], [nu, 1.0, 0.0], [0.0, 0.0, 2.0 * (1.0 - nu)]])
    return np.einsum('p,pci,cd,pdj->ij', weights, b, e, b, optimize=True)


def element_load(hx: float, hy: float) -> np.ndarray:
    """Consistent load vector (all 16 DOF) for unit pressure."""
    _check_element_args(hx, hy, 0.3)
    s, t, weights = _quadrature(hx, hy)
    return weights @ _shape(hx, hy, s, t, 0, 0)


def element_curvatures(hx: float, hy: float, dofs, s: float, t: float) -> np.ndarray:
    """(w_xx, w_yy, w_xy) at fractional local coordinates (s, t) from the 16 element DOF."""
    _check_element_args(hx, hy, 0.3)
    return _curvature_matrix(hx, hy, np.array([s]), np.array([t]))[0] @ np.asarray(dofs, dtype=float)


# ------------------------------------------------------------------ validation

def _number(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be a finite positive number')
    return float(value)


def _level(n_b) -> int:
    if isinstance(n_b, bool) or not isinstance(n_b, int) or n_b < 1:
        raise ValueError('n_b must be an integer >= 1')
    return int(n_b)


def _node_counts(n_a: int, n_b: int, quarter: bool) -> tuple[int, int, int, int]:
    nx, ny = (n_a, n_b) if quarter else (2 * n_a, 2 * n_b)
    return nx, ny, nx + 1, ny + 1


# ------------------------------------------------------------------ solve

def _solve(*, length_a_mm, width_b_mm, thickness_mm, pressure_mpa, youngs_modulus_mpa, poissons_ratio,
           edge_condition, n_b, max_dof, symmetry) -> tuple[dict, dict]:
    a = _number('length_a_mm', length_a_mm)
    b = _number('width_b_mm', width_b_mm)
    h = _number('thickness_mm', thickness_mm)
    q = _number('pressure_mpa', pressure_mpa)
    young = _number('youngs_modulus_mpa', youngs_modulus_mpa)
    nu = _number('poissons_ratio', poissons_ratio)
    if a < b:
        raise ValueError('length_a_mm must be >= width_b_mm')
    if a / b > PLATE_NUMERICAL_V1['aspect_ratio_max']:
        raise ValueError('aspect ratio above the supported limit')
    if not 0 < nu < 0.5:
        raise ValueError('poissons_ratio must be in (0, 0.5)')
    if edge_condition not in _EDGE_CONDITIONS:
        raise ValueError('edge_condition must be simply_supported or clamped')
    if symmetry not in _SYMMETRIES:
        raise ValueError("symmetry must be 'quarter' or 'full'")
    n_b = _level(n_b)
    if max_dof is None:
        max_dof = PLATE_NUMERICAL_V1['max_dof']
    elif isinstance(max_dof, bool) or not isinstance(max_dof, int) or max_dof < 1:
        raise ValueError('max_dof must be a positive integer')
    quarter = symmetry == 'quarter'
    clamped = edge_condition == 'clamped'

    n_a_uncapped = math.ceil(n_b * a / b - 1e-9)
    n_a = n_a_uncapped
    while 4 * _node_counts(n_a, n_b, quarter)[2] * _node_counts(n_a, n_b, quarter)[3] > max_dof and n_a > 1:
        n_a -= 1
    nx, ny, nxn, nyn = _node_counts(n_a, n_b, quarter)
    n_dof = 4 * nxn * nyn
    if n_dof > max_dof:
        raise ValueError('max_dof too small for even a one-element-wide model')

    lx, ly = (a / 2, b / 2) if quarter else (a, b)
    hx, hy = lx / (b * nx), ly / (b * ny)  # nondimensional by b
    ke = element_stiffness(hx, hy, nu)
    fe = element_load(hx, hy)

    ei, ej = np.meshgrid(np.arange(nx), np.arange(ny), indexing='ij')
    ei, ej = ei.ravel(), ej.ravel()
    nel = ei.size
    nodes = np.stack([(ei + cx) * nyn + (ej + cy) for cx, cy in _CORNERS], axis=1)
    edofs = (4 * nodes[:, :, None] + np.arange(4)).reshape(nel, 16)

    flat = (edofs[:, :, None] * n_dof + edofs[:, None, :]).ravel()
    stiffness = np.bincount(flat, weights=np.broadcast_to(ke, (nel, 16, 16)).ravel(),
                            minlength=n_dof * n_dof).reshape(n_dof, n_dof)
    force = np.bincount(edofs.ravel(), weights=np.tile(fe, nel), minlength=n_dof)

    fixed = np.zeros((nxn, nyn, 4), dtype=bool)
    if quarter:
        fixed[0, :, 1] = fixed[0, :, 3] = True   # x = 0 symmetry: normal slope, w_xy
        fixed[:, 0, 2] = fixed[:, 0, 3] = True   # y = 0 symmetry
    for i in ((nx,) if quarter else (0, nx)):    # edges parallel to Y
        fixed[i, :, 0] = True
        if clamped:
            fixed[i, :, :] = True
        else:
            fixed[i, :, 2] = True
    for j in ((ny,) if quarter else (0, ny)):    # edges parallel to X
        fixed[:, j, 0] = True
        if clamped:
            fixed[:, j, :] = True
        else:
            fixed[:, j, 1] = True
    fixed = fixed.reshape(-1)
    free_idx = np.flatnonzero(~fixed)
    fixed_idx = np.flatnonzero(fixed)

    kff = stiffness[np.ix_(free_idx, free_idx)]
    kcf = stiffness[np.ix_(fixed_idx, free_idx)]
    del stiffness
    u = np.zeros(n_dof)
    u[free_idx] = np.linalg.solve(kff, force[free_idx])
    del kff
    support = np.zeros(n_dof)  # support force in the direction opposing the load
    support[fixed_idx] = force[fixed_idx] - kcf @ u[free_idx]
    del kcf

    mult = 4.0 if quarter else 1.0
    force_scale = q * b * b
    w_scale = q * b**4 / (young * h**3 / (12 * (1 - nu**2)))
    w_reactions = support[fixed_idx[fixed_idx % 4 == 0]]
    reaction_sum = float(mult * w_reactions.sum() * force_scale)
    total_load = q * a * b
    equilibrium = abs(reaction_sum - total_load) / total_load

    # --- recovery at 4x4 Gauss points and the 4 element nodes
    gs, gt, _ = _quadrature(hx, hy)
    ns = np.array([c[0] for c in _CORNERS], dtype=float)
    nt = np.array([c[1] for c in _CORNERS], dtype=float)
    ps, pt = np.concatenate([gs, ns]), np.concatenate([gt, nt])
    curv = np.einsum('pcd,ed->epc', _curvature_matrix(hx, hy, ps, pt), u[edofs])
    dm = np.array([[1.0, nu, 0.0], [nu, 1.0, 0.0], [0.0, 0.0, 1.0 - nu]])
    moments = -curv @ dm.T * force_scale  # (nel, P, 3): Mx, My, Mxy in N
    sig = 6.0 * moments / h**2
    sx, sy, txy = sig[..., 0], sig[..., 1], sig[..., 2]
    vm = np.sqrt(np.maximum(sx**2 - sx * sy + sy**2 + 3 * txy**2, 0.0))
    e_star, p_star = np.unravel_index(int(np.argmax(vm)), vm.shape)
    xq = (ei[e_star] + ps[p_star]) * hx * b
    yq = (ej[e_star] + pt[p_star]) * hy * b
    px, py = (a / 2 - xq, b / 2 - yq) if quarter else (xq, yq)
    px, py = float(min(max(px, 0.0), a)), float(min(max(py, 0.0), b))
    tol = 1e-9 * a
    on_x = px <= tol or px >= a - tol
    on_y = py <= tol or py >= b - tol
    if on_x and on_y:
        region = 'corner'
    elif on_x or on_y:
        region = 'edge'
    elif abs(px - a / 2) <= tol and abs(py - b / 2) <= tol:
        region = 'centre'
    else:
        region = 'interior'

    def nodal(i: int, j: int) -> np.ndarray:
        e = min(i, nx - 1) * ny + min(j, ny - 1)
        c = _CORNER_INDEX[(i - min(i, nx - 1), j - min(j, ny - 1))]
        return moments[e, gs.size + c]

    ci, cj = (0, 0) if quarter else (nx // 2, ny // 2)
    centre = nodal(ci, cj)
    long_edge = nodal(*((0, ny) if quarter else (nx // 2, 0)))     # x = a/2, y = 0
    short_edge = nodal(*((nx, 0) if quarter else (0, ny // 2)))    # x = 0, y = b/2
    nodal_w = u.reshape(nxn, nyn, 4)
    corner_id = (nx, ny) if quarter else (0, 0)
    corner_twist = 2.0 * abs(float(nodal(*corner_id)[2]))
    corner_force = None if clamped else abs(float(support[4 * (corner_id[0] * nyn + corner_id[1])]) * force_scale)

    result = {
        'n_a': int(n_a), 'n_b': int(n_b), 'n_a_uncapped': int(n_a_uncapped), 'n_a_capped': bool(n_a != n_a_uncapped),
        'dof_total': int(n_dof), 'dof_free': int(free_idx.size),
        'w_max': float(nodal_w[:, :, 0].max() * w_scale),
        'w_centre': float(nodal_w[ci, cj, 0] * w_scale),
        'mx_centre': float(centre[0]), 'my_centre': float(centre[1]),
        'edge_moment_long_edge': float(long_edge[1]), 'edge_moment_short_edge': float(short_edge[0]),
        'mxy_max_abs': float(np.abs(moments[..., 2]).max()),
        'vm_max_mpa': float(vm[e_star, p_star]),
        'vm_max_location': {'x': px, 'y': py, 'region': region},
        'reaction_sum_n': reaction_sum, 'equilibrium_relative_error': float(equilibrium),
        'corner_force_n': corner_force,
        'corner_twist_force_n': None if clamped else float(corner_twist),
        'strain_energy_n_mm': float(mult * 0.5 * (u @ force) * q * q * b**6
                                    / (young * h**3 / (12 * (1 - nu**2)))),
        'method': METHOD,
        'symmetry': symmetry,
    }
    numbers = [v for v in result.values() if isinstance(v, float)] + [result['vm_max_location']['x'],
                                                                       result['vm_max_location']['y']]
    if any(not math.isfinite(v) for v in numbers):
        raise ValueError('Nonfinite plate finite-element solution')
    fields = {
        'w': nodal_w[:, :, 0] * w_scale,
        'wx': nodal_w[:, :, 1] * w_scale / b,
        'wy': nodal_w[:, :, 2] * w_scale / b,
        'wxy': nodal_w[:, :, 3] * w_scale / b**2,
    }
    return result, fields


def solve_plate(*, length_a_mm: float, width_b_mm: float, thickness_mm: float, pressure_mpa: float,
                youngs_modulus_mpa: float, poissons_ratio: float, edge_condition: str, n_b: int,
                max_dof: int | None = None, symmetry: str = 'quarter') -> dict:
    return _solve(length_a_mm=length_a_mm, width_b_mm=width_b_mm, thickness_mm=thickness_mm,
                  pressure_mpa=pressure_mpa, youngs_modulus_mpa=youngs_modulus_mpa,
                  poissons_ratio=poissons_ratio, edge_condition=edge_condition, n_b=n_b,
                  max_dof=max_dof, symmetry=symmetry)[0]


def solve_plate_fields(*, length_a_mm: float, width_b_mm: float, thickness_mm: float, pressure_mpa: float,
                       youngs_modulus_mpa: float, poissons_ratio: float, edge_condition: str, n_b: int,
                       max_dof: int | None = None, symmetry: str = 'quarter') -> tuple[dict, dict]:
    """Same as solve_plate, plus nodal grids (index [i, j], i along x) of w, w_x, w_y, w_xy."""
    return _solve(length_a_mm=length_a_mm, width_b_mm=width_b_mm, thickness_mm=thickness_mm,
                  pressure_mpa=pressure_mpa, youngs_modulus_mpa=youngs_modulus_mpa,
                  poissons_ratio=poissons_ratio, edge_condition=edge_condition, n_b=n_b,
                  max_dof=max_dof, symmetry=symmetry)


def solve_levels(*, length_a_mm: float, width_b_mm: float, thickness_mm: float, pressure_mpa: float,
                 youngs_modulus_mpa: float, poissons_ratio: float, edge_condition: str,
                 max_dof: int | None = None, symmetry: str = 'quarter', levels=None) -> list[dict]:
    levels = PLATE_NUMERICAL_V1['mesh_levels_n_b'] if levels is None else tuple(levels)
    if not levels:
        raise ValueError('At least one mesh level is required')
    levels = tuple(_level(n) for n in levels)
    return [solve_plate(length_a_mm=length_a_mm, width_b_mm=width_b_mm, thickness_mm=thickness_mm,
                        pressure_mpa=pressure_mpa, youngs_modulus_mpa=youngs_modulus_mpa,
                        poissons_ratio=poissons_ratio, edge_condition=edge_condition, n_b=n,
                        max_dof=max_dof, symmetry=symmetry) for n in levels]
