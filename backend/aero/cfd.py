"""OpenFOAM CFD stub — Module 10."""
from __future__ import annotations


class OpenFOAMNotFoundError(RuntimeError):
    pass


def run_cfd(wing_geometry, spec, sizing) -> dict:
    """
    OpenFOAM CFD stub.
    Full implementation requires OpenFOAM installation, mesh generation (snappyHexMesh),
    and case setup. This stub raises NotImplementedError.
    """
    raise NotImplementedError(
        "OpenFOAM CFD integration is not yet implemented. "
        "Use XFOIL (xfoil.run_polar) for 2D aerodynamics or "
        "AVL (avl.compute_spanwise) for 3D vortex-lattice analysis."
    )
