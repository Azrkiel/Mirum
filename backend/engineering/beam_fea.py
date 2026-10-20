"""Euler–Bernoulli beam finite elements, not 3D solid/stress certification.

Two DOFs/node: transverse displacement in +Z and bending rotation. Fixed X=0;
positive tip force at X=L. N, mm, MPa throughout. An independent closed-form
benchmark is reported by the verifier, not used in the stiffness solve.
"""
from __future__ import annotations

import math
import numpy as np


def solve_cantilever(*, length_mm: float, width_mm: float, height_mm: float,
                     youngs_modulus_mpa: float, tip_force_n: float,
                     elements: int, second_moment_mm4: float | None = None) -> dict:
    values = (length_mm, width_mm, height_mm, youngs_modulus_mpa, tip_force_n)
    if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('Finite positive beam inputs required')
    if type(elements) is not int or not 2 <= elements <= 64:
        raise ValueError('Beam mesh requires 2 to 64 elements')
    if max(width_mm, height_mm) > length_mm / 10:
        raise ValueError('Unsupported non-slender beam')
    inertia = second_moment_mm4 if second_moment_mm4 is not None else width_mm * height_mm**3 / 12
    if isinstance(inertia, bool) or not math.isfinite(inertia) or inertia <= 0:
        raise ValueError('Finite positive measured second moment required')
    rigidity = youngs_modulus_mpa * inertia
    # Rotation DOF is scaled by L to reduce dimension-driven conditioning.
    ell = 1.0 / elements
    local = np.array([[12, 6*ell, -12, 6*ell],
                      [6*ell, 4*ell**2, -6*ell, 2*ell**2],
                      [-12, -6*ell, 12, -6*ell],
                      [6*ell, 2*ell**2, -6*ell, 4*ell**2]], dtype=float) / ell**3
    count = 2 * (elements + 1)
    stiffness = np.zeros((count, count))
    for element in range(elements):
        indices = np.arange(2*element, 2*element+4)
        stiffness[np.ix_(indices, indices)] += local
    force = np.zeros(count)
    force[-2] = tip_force_n * length_mm**3 / rigidity
    displacement = np.zeros(count)
    displacement[2:] = np.linalg.solve(stiffness[2:, 2:], force[2:])
    residual = stiffness @ displacement - force
    scale = rigidity / length_mm**3
    root_force = float(residual[0] * scale)
    root_moment = float(residual[1] * scale * length_mm)
    equilibrium = max(abs(root_force + tip_force_n) / tip_force_n,
                      abs(root_moment + tip_force_n * length_mm) / (tip_force_n * length_mm),
                      float(np.max(np.abs(residual[2:]))) / abs(force[-2]))
    result = {
        'elements': elements, 'degrees_of_freedom': count-2,
        'tip_displacement_mm': float(displacement[-2]),
        'tip_rotation_rad': float(displacement[-1] / length_mm),
        'root_bending_stress_mpa': abs(root_moment) * height_mm / (2 * inertia),
        'root_reaction_force_n': root_force,
        'root_reaction_moment_n_mm': root_moment,
        'relative_equilibrium_residual': equilibrium,
        'strain_energy_n_mm': float(0.5 * displacement @ stiffness @ displacement * scale),
        'second_moment_mm4': inertia,
    }
    if any(not math.isfinite(v) for v in result.values()):
        raise ValueError('Nonfinite finite-element solution')
    return result

