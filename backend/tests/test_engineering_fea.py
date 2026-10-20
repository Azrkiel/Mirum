import importlib.util
import math
import pytest


def test_cantilever_finite_elements_match_independent_closed_form_and_equilibrium():
    assert importlib.util.find_spec('engineering.beam_fea') is not None, 'Beam FE solver is missing'
    from engineering.beam_fea import solve_cantilever
    result = solve_cantilever(length_mm=200.0, width_mm=10.0, height_mm=10.0,
                              youngs_modulus_mpa=70000.0, tip_force_n=10.0, elements=8)
    inertia = 10.0 * 10.0**3 / 12.0
    assert result['tip_displacement_mm'] == pytest.approx(10.0 * 200.0**3 / (3 * 70000.0 * inertia), rel=1e-8)
    assert result['root_bending_stress_mpa'] == pytest.approx(10.0 * 200.0 * 5.0 / inertia, rel=1e-8)
    assert result['root_reaction_force_n'] == pytest.approx(-10.0, rel=1e-8)
    assert result['root_reaction_moment_n_mm'] == pytest.approx(-2000.0, rel=1e-8)
    assert result['relative_equilibrium_residual'] < 1e-8
    assert result['strain_energy_n_mm'] == pytest.approx(0.5 * 10 * result['tip_displacement_mm'], rel=1e-8)

