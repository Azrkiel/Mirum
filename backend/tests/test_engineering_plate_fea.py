"""Native Kirchhoff plate FE (BFS element, quarter model): element, equilibrium, accuracy, convergence."""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

from engineering.plate_fea import (
    element_curvatures,
    element_load,
    element_stiffness,
    solve_levels,
    solve_plate,
    solve_plate_fields,
)
from engineering.plate_spec import PLATE_NUMERICAL_V1

NU = 0.3
B_MM = 100.0
H_MM = 2.0
Q_MPA = 0.01
E_MPA = 200000.0
D_NMM = E_MPA * H_MM**3 / (12 * (1 - NU**2))
EDGES = ('simply_supported', 'clamped')


def params(ab: float, edge: str, b: float = B_MM) -> dict:
    return dict(length_a_mm=b * ab, width_b_mm=b, thickness_mm=H_MM, pressure_mpa=Q_MPA,
                youngs_modulus_mpa=E_MPA, poissons_ratio=NU, edge_condition=edge)


@lru_cache(maxsize=None)
def run(ab: float, edge: str, n_b: int) -> dict:
    return solve_plate(n_b=n_b, **params(ab, edge))


def w_coeff(result: dict) -> float:
    return result['w_max'] * D_NMM / (Q_MPA * B_MM**4)


def m_coeff(value: float) -> float:
    return value / (Q_MPA * B_MM**2)


def rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b)


# ---------------------------------------------------------------- element

HX, HY = 0.37, 0.21


def test_element_stiffness_symmetric_psd_three_rigid_modes():
    ke = element_stiffness(HX, HY, NU)
    assert ke.shape == (16, 16)
    assert np.allclose(ke, ke.T, rtol=0, atol=1e-12 * np.abs(ke).max())
    eig = np.linalg.eigvalsh(0.5 * (ke + ke.T))
    scale = eig.max()
    assert eig.min() > -1e-10 * scale
    assert int(np.sum(np.abs(eig) < 1e-10 * scale)) == 3


def _nodal_dofs(func, x0: float, y0: float):
    """DOF vector (w, wx, wy, wxy per node, nodes (0,0),(1,0),(1,1),(0,1)) from derivative callbacks."""
    dofs = []
    for ex, ey in ((0, 0), (1, 0), (1, 1), (0, 1)):
        x, y = x0 + ex * HX, y0 + ey * HY
        dofs.extend(func(x, y))
    return np.array(dofs, dtype=float)


def test_rigid_modes_have_zero_energy():
    ke = element_stiffness(HX, HY, NU)
    for func in (lambda x, y: (1.0, 0.0, 0.0, 0.0),
                 lambda x, y: (x, 1.0, 0.0, 0.0),
                 lambda x, y: (y, 0.0, 1.0, 0.0)):
        u = _nodal_dofs(func, 0.4, 0.9)
        assert abs(u @ ke @ u) < 1e-12 * np.abs(ke).max()


@pytest.mark.parametrize('name, func, curv, energy', [
    ('x2', lambda x, y: (x * x, 2 * x, 0.0, 0.0), (2.0, 0.0, 0.0), 2.0 * HX * HY),
    ('y2', lambda x, y: (y * y, 0.0, 2 * y, 0.0), (0.0, 2.0, 0.0), 2.0 * HX * HY),
    ('xy', lambda x, y: (x * y, y, x, 1.0), (0.0, 0.0, 1.0), (1 - NU) * HX * HY),
])
def test_patch_constant_curvature_moments_energy(name, func, curv, energy):
    u = _nodal_dofs(func, 0.4, 0.9)
    for s, t in ((0.0, 0.0), (0.3, 0.8), (1.0, 1.0), (0.5, 0.5)):
        assert np.allclose(element_curvatures(HX, HY, u, s, t), curv, atol=1e-12)
    kx, ky, kxy = curv
    moments = (-(kx + NU * ky), -(ky + NU * kx), -(1 - NU) * kxy)
    got = element_curvatures(HX, HY, u, 0.5, 0.5)
    got_moments = (-(got[0] + NU * got[1]), -(got[1] + NU * got[0]), -(1 - NU) * got[2])
    assert np.allclose(got_moments, moments, atol=1e-12)
    ke = element_stiffness(HX, HY, NU)
    assert 0.5 * u @ ke @ u == pytest.approx(energy, rel=1e-12, abs=1e-14)


def test_consistent_load_vector_all_dof():
    fe = element_load(HX, HY)
    assert fe.shape == (16,)
    assert fe[0::4].sum() == pytest.approx(HX * HY, rel=1e-13)
    # work of a uniform field w = 1 equals load times area; slope DOF loads are nonzero (consistent)
    assert np.all(np.abs(fe[1::4]) > 0) and np.all(np.abs(fe[3::4]) > 0)
    # w = x*y: nodal work equals integral of x*y over the element
    u = _nodal_dofs(lambda x, y: (x * y, y, x, 1.0), 0.0, 0.0)
    assert u @ fe == pytest.approx((HX * HX / 2) * (HY * HY / 2), rel=1e-12)


# ---------------------------------------------------------------- equilibrium

@pytest.mark.parametrize('edge', EDGES)
@pytest.mark.parametrize('ab', [1.0, 2.5, 5.0])
def test_equilibrium_every_level(ab, edge):
    for n_b in PLATE_NUMERICAL_V1['mesh_levels_n_b']:
        result = run(ab, edge, n_b)
        assert result['equilibrium_relative_error'] <= 1e-9
        assert result['reaction_sum_n'] == pytest.approx(Q_MPA * B_MM * ab * B_MM, rel=1e-9)


# ---------------------------------------------------------------- symmetry

@pytest.mark.parametrize('edge', EDGES)
@pytest.mark.parametrize('ab, n_b', [(1.0, 3), (1.3, 3), (2.0, 2)])
def test_full_model_matches_quarter(ab, n_b, edge):
    quarter = solve_plate(n_b=n_b, symmetry='quarter', **params(ab, edge))
    full = solve_plate(n_b=n_b, symmetry='full', **params(ab, edge))
    assert full['dof_total'] > quarter['dof_total']
    assert rel(full['w_centre'], quarter['w_centre']) < 1e-9
    assert rel(full['vm_max_mpa'], quarter['vm_max_mpa']) < 1e-9
    assert rel(full['w_max'], quarter['w_max']) < 1e-9
    assert rel(full['mx_centre'], quarter['mx_centre']) < 1e-9
    assert rel(full['edge_moment_long_edge'], quarter['edge_moment_long_edge']) < 1e-9


@pytest.mark.parametrize('edge', EDGES)
def test_full_model_fields_symmetric(edge):
    _, f = solve_plate_fields(n_b=3, symmetry='full', **params(1.3, edge))
    w, wx, wy, wxy = f['w'], f['wx'], f['wy'], f['wxy']
    scale = np.abs(w).max()
    assert np.allclose(w, w[::-1, :], atol=1e-10 * scale)
    assert np.allclose(w, w[:, ::-1], atol=1e-10 * scale)
    sx = np.abs(wx).max()
    assert np.allclose(wx, -wx[::-1, :], atol=1e-10 * sx)
    assert np.allclose(wx, wx[:, ::-1], atol=1e-10 * sx)
    sy = np.abs(wy).max()
    assert np.allclose(wy, wy[::-1, :], atol=1e-10 * sy)
    assert np.allclose(wy, -wy[:, ::-1], atol=1e-10 * sy)
    sxy = np.abs(wxy).max()
    assert np.allclose(wxy, -wxy[::-1, :], atol=1e-10 * sxy)
    assert np.allclose(wxy, -wxy[:, ::-1], atol=1e-10 * sxy)


# ---------------------------------------------------------------- accuracy vs book

def test_ss_square_deflection_coefficient():
    assert rel(w_coeff(run(1.0, 'simply_supported', 16)), 0.00406) < 0.005


def test_cl_square_deflection_coefficient():
    c = w_coeff(run(1.0, 'clamped', 16))
    assert rel(c, 0.00126) < 0.005
    assert rel(c, 0.00126532) < 0.005


def test_ss_square_centre_moments():
    r = run(1.0, 'simply_supported', 16)
    assert rel(m_coeff(r['mx_centre']), 0.0479) < 0.015
    assert rel(m_coeff(r['my_centre']), 0.0479) < 0.015


def test_cl_square_edge_moments():
    r = run(1.0, 'clamped', 16)
    assert rel(m_coeff(r['edge_moment_long_edge']), -0.0513) < 0.015
    assert rel(m_coeff(r['edge_moment_short_edge']), -0.0513) < 0.015
    assert rel(m_coeff(r['mx_centre']), 0.0231) < 0.03


def test_ss_square_corner_force():
    r = run(1.0, 'simply_supported', 16)
    assert rel(r['corner_force_n'] / (Q_MPA * B_MM**2), 0.065) < 0.02
    assert r['corner_twist_force_n'] == pytest.approx(r['corner_force_n'], rel=0.05)
    assert run(1.0, 'clamped', 16)['corner_force_n'] is None


def test_ss_square_vm_max_location_reported():
    r = run(1.0, 'simply_supported', 16)
    loc = r['vm_max_location']
    assert loc['region'] in ('corner', 'centre')
    sigma_c = 6 * r['mx_centre'] / H_MM**2
    sigma_corner = math.sqrt(3) * 6 * r['corner_twist_force_n'] / 2 / H_MM**2
    print('SS square vm max', r['vm_max_mpa'], loc, 'centre', sigma_c, 'corner', sigma_corner)
    assert r['vm_max_mpa'] >= 0.99 * max(sigma_c, sigma_corner)
    # corner twisting stress (0.0325 q b^2 sqrt(3)) exceeds the centre bending stress (0.0479 q b^2)
    assert loc['region'] == 'corner'


def test_cl_vm_max_at_edge_midpoint():
    r = run(1.0, 'clamped', 16)
    assert r['vm_max_location']['region'] == 'edge'
    # clamped edge: tangential moment is nu times the normal one, so vm = sigma_n sqrt(1 - nu + nu^2)
    expected = 6 * abs(r['edge_moment_long_edge']) / H_MM**2 * math.sqrt(1 - NU + NU**2)
    assert r['vm_max_mpa'] == pytest.approx(expected, rel=0.02)


def test_strip_limit_aspect_five():
    ss = run(5.0, 'simply_supported', 16)
    cl = run(5.0, 'clamped', 16)
    assert rel(w_coeff(ss), 5 / 384) < 0.03
    assert rel(w_coeff(cl), 1 / 384) < 0.03
    assert rel(m_coeff(cl['edge_moment_long_edge']), -1 / 12) < 0.02


# ---------------------------------------------------------------- convergence

@pytest.mark.parametrize('edge', EDGES)
@pytest.mark.parametrize('ab', [1.0, 5.0])
def test_mesh_convergence(ab, edge):
    r4, r8, r16 = (run(ab, edge, n) for n in (4, 8, 16))
    assert rel(r16['w_max'], r8['w_max']) <= PLATE_NUMERICAL_V1['mesh_convergence_deflection']
    assert rel(r16['vm_max_mpa'], r8['vm_max_mpa']) <= PLATE_NUMERICAL_V1['mesh_convergence_stress']
    # Observed order on the centre deflection: w_max is a maximum over nodes, and for the clamped
    # a/b = 5 plate its location shifts with the node spacing (the end-effect overshoot sits off
    # centre), so w_max itself is only checked against the contract threshold above.
    c1 = abs(r8['w_centre'] - r4['w_centre'])
    c2 = abs(r16['w_centre'] - r8['w_centre'])
    assert c2 < c1
    assert math.log2(c1 / c2) >= 1.5
    v1 = abs(r8['vm_max_mpa'] - r4['vm_max_mpa'])
    v2 = abs(r16['vm_max_mpa'] - r8['vm_max_mpa'])
    assert v2 < v1


def test_solve_levels_default_and_explicit():
    got = solve_levels(**params(1.0, 'clamped'))
    assert [r['n_b'] for r in got] == list(PLATE_NUMERICAL_V1['mesh_levels_n_b'])
    got2 = solve_levels(levels=(2, 3), **params(1.0, 'clamped'))
    assert [r['n_b'] for r in got2] == [2, 3]
    assert got[0]['w_max'] == run(1.0, 'clamped', 4)['w_max']


# ---------------------------------------------------------------- DOF cap

def test_dof_cap_lowers_n_a_and_records_it():
    r = solve_plate(n_b=4, max_dof=200, **params(5.0, 'simply_supported'))
    assert r['n_a_uncapped'] == 20
    assert r['n_a_capped'] is True
    assert r['n_a'] < 20
    assert r['dof_total'] <= 200
    assert 4 * (r['n_a'] + 2) * (r['n_b'] + 1) > 200  # largest n_a within the cap
    assert r['equilibrium_relative_error'] <= 1e-9


def test_uncapped_levels_report_flags():
    r = run(2.5, 'clamped', 8)
    assert r['n_a'] == r['n_a_uncapped'] == 20
    assert r['n_a_capped'] is False
    assert r['dof_total'] == 4 * 21 * 9
    assert r['dof_free'] < r['dof_total']


def test_finest_level_aspect_five_dof():
    r = run(5.0, 'simply_supported', 16)
    assert (r['n_a'], r['n_b'], r['dof_total']) == (80, 16, 5508)
    assert r['n_a_capped'] is False
    assert r['method'] == 'bfs_kirchhoff_quarter_v1'


def test_non_integer_ratio_ceil():
    r = solve_plate(n_b=4, **params(1.3, 'clamped'))
    assert r['n_a'] == 6  # ceil(5.2)


# ---------------------------------------------------------------- validation, output, determinism

@pytest.mark.parametrize('override', [
    dict(length_a_mm=float('nan')), dict(length_a_mm=float('inf')), dict(width_b_mm=-1.0),
    dict(thickness_mm=0.0), dict(pressure_mpa=0.0), dict(youngs_modulus_mpa=-5.0),
    dict(poissons_ratio=0.0), dict(poissons_ratio=0.5), dict(poissons_ratio=float('nan')),
    dict(length_a_mm=50.0), dict(length_a_mm=501.0),
    dict(edge_condition='free'), dict(edge_condition='Clamped'),
    dict(n_b=0), dict(n_b=-2), dict(n_b=2.0), dict(n_b=True), dict(n_b='4'),
    dict(thickness_mm=True), dict(symmetry='half'), dict(max_dof=0), dict(max_dof=True),
    dict(max_dof=10),
])
def test_input_validation(override):
    kwargs = dict(n_b=4, **params(2.0, 'simply_supported'))
    kwargs.update(override)
    with pytest.raises(ValueError):
        solve_plate(**kwargs)


def test_levels_validation():
    with pytest.raises(ValueError):
        solve_levels(levels=(4, 0), **params(1.0, 'clamped'))
    with pytest.raises(ValueError):
        solve_levels(levels=(), **params(1.0, 'clamped'))


def test_output_is_plain_and_json_serialisable():
    r = run(2.0, 'simply_supported', 8)
    json.dumps(r, allow_nan=False)
    expected = {
        'n_a', 'n_b', 'n_a_uncapped', 'n_a_capped', 'dof_total', 'dof_free', 'w_max', 'w_centre',
        'mx_centre', 'my_centre', 'edge_moment_long_edge', 'edge_moment_short_edge', 'mxy_max_abs',
        'vm_max_mpa', 'vm_max_location', 'reaction_sum_n', 'equilibrium_relative_error',
        'corner_force_n', 'corner_twist_force_n', 'strain_energy_n_mm', 'method',
    }
    assert expected <= set(r)
    for key, value in r.items():
        if key == 'vm_max_location':
            assert set(value) >= {'x', 'y', 'region'}
            assert type(value['x']) is float and type(value['y']) is float
            assert 0 <= value['x'] <= 2 * B_MM and 0 <= value['y'] <= B_MM
        else:
            assert type(value) in (int, float, bool, str, type(None)), key
    assert r['strain_energy_n_mm'] > 0
    # strain energy equals half the work of the load: U = 1/2 q * integral(w) (positive, sane scale)
    assert r['w_max'] >= r['w_centre'] > 0


def test_deterministic():
    a = solve_plate(n_b=6, **params(2.0, 'clamped'))
    b = solve_plate(n_b=6, **params(2.0, 'clamped'))
    assert a == b


def test_scaling_invariance_of_coefficients():
    base = solve_plate(n_b=6, **params(2.0, 'simply_supported'))
    other = solve_plate(n_b=6, length_a_mm=2000.0, width_b_mm=1000.0, thickness_mm=30.0,
                        pressure_mpa=1.7, youngs_modulus_mpa=70000.0, poissons_ratio=NU,
                        edge_condition='simply_supported')
    d2 = 70000.0 * 30.0**3 / (12 * (1 - NU**2))
    assert (other['w_max'] * d2 / (1.7 * 1000.0**4) ==
            pytest.approx(base['w_max'] * D_NMM / (Q_MPA * B_MM**4), rel=1e-9))
    assert (other['mx_centre'] / (1.7 * 1000.0**2) ==
            pytest.approx(base['mx_centre'] / (Q_MPA * B_MM**2), rel=1e-9))


# ---------------------------------------------------------------- performance and import hygiene

def test_finest_level_aspect_five_time():
    start = time.perf_counter()
    solve_plate(n_b=16, **params(5.0, 'clamped'))
    assert time.perf_counter() - start < 10.0


def test_import_loads_no_forbidden_modules():
    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys, json\n"
        "sys.path.insert(0, 'backend')\n"
        "import engineering.plate_fea\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('cadquery', 'OCP', 'scipy')"
        " or m == 'engineering.plate_reference')\n"
        "print(json.dumps(bad))\n"
    )
    out = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []
