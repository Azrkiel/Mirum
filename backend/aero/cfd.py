"""OpenFOAM CFD interface — stub for future implementation."""
from dataclasses import dataclass


@dataclass
class CFDConfig:
    solver: str = "simpleFoam"           # incompressible; use rhoSimpleFoam for M>0.3
    turbulence: str = "kOmegaSST"        # SST k-omega recommended for aero
    y_plus_target: float = 1.0           # low-Re wall treatment
    far_field_radii: float = 20.0        # chord lengths
    mesh_refinement_levels: int = 5
    n_procs: int = 4


def generate_openfoam_case(config: CFDConfig, oml_step_path: str) -> str:
    raise NotImplementedError(
        "OpenFOAM case generation not yet implemented. "
        "Architecture: blockMesh background, snappyHexMesh for OML, "
        f"solver={config.solver}, turbulence={config.turbulence}, "
        f"y+≈{config.y_plus_target}. Export OML as STL from STEP first."
    )
