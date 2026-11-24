"""Euler–Bernoulli beam finite elements, not 3D solid/stress certification.

Two DOFs per node give transverse displacement in +Z and bending rotation.
The selected node fixes both DOFs. Positive tip force at X=L. Units are N, mm and MPa.
An independent closed-form
benchmark is reported by the verifier, not used in the stiffness solve.
"""
# Adapted from Azrkiel/Mirum backend/engineering/beam_fea.py.
# MIT License
#
# Copyright (c) 2026 Azrkiel
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import annotations

import math
import numpy as np


def solve_cantilever(*, length_mm: float, width_mm: float, height_mm: float,
                     youngs_modulus_mpa: float, tip_force_n: float,
                     elements: int, second_moment_mm4: float | None = None,
                     clamp_node: int = 0) -> dict:
    values = (length_mm, width_mm, height_mm, youngs_modulus_mpa, tip_force_n)
    if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('Finite positive beam inputs required')
    if type(elements) is not int or not 2 <= elements <= 64:
        raise ValueError('Beam mesh requires 2 to 64 elements')
    if type(clamp_node) is not int or not 0 <= clamp_node < elements:
        raise ValueError('The clamp must be on a mesh node before the tip')
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
    fixed = 2 * clamp_node
    free = np.r_[0:fixed, fixed + 2:count]
    displacement[free] = np.linalg.solve(stiffness[np.ix_(free, free)], force[free])
    residual = stiffness @ displacement - force
    scale = rigidity / length_mm**3
    root_force = float(residual[fixed] * scale)
    root_moment = float(residual[fixed + 1] * scale * length_mm)
    free_span = length_mm * (elements - clamp_node) / elements
    equilibrium = max(abs(root_force + tip_force_n) / tip_force_n,
                      abs(root_moment + tip_force_n * free_span) / (tip_force_n * free_span),
                      float(np.max(np.abs(residual[free]))) / abs(force[-2]))
    result = {
        'elements': elements, 'degrees_of_freedom': count-2,
        'tip_displacement_mm': float(displacement[-2]),
        'tip_rotation_rad': float(displacement[-1] / length_mm),
        'root_bending_stress_mpa': abs(root_moment) * height_mm / (2 * inertia),
        'root_reaction_force_n': root_force,
        'root_reaction_moment_n_mm': root_moment,
        'relative_equilibrium_residual': float(equilibrium),
        'strain_energy_n_mm': float(0.5 * displacement @ stiffness @ displacement * scale),
        'second_moment_mm4': inertia,
    }
    if any(not math.isfinite(v) for v in result.values()):
        raise ValueError('Nonfinite finite-element solution')
    return result
