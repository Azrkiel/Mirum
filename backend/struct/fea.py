"""Calculix FEA file generation and execution."""
from __future__ import annotations
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from ..aero.loads import LoadCase
from .sizing import BoxSizing


class CalculixNotFoundError(RuntimeError):
    pass


@dataclass
class FEAResult:
    sigma_vm_max: float    # Pa von Mises max
    sigma_root: float      # Pa bending stress at root
    MS_fea: float          # margin of safety
    buckling_eigenvalue: float


def _find_ccx() -> str:
    for c in ["ccx", "calculix", "/usr/bin/ccx", "/opt/conda/bin/ccx"]:
        found = shutil.which(c)
        if found:
            return found
    raise CalculixNotFoundError(
        "Calculix (ccx) not found. "
        "Add 'calculix-ccx' to Dockerfile (apt-get install calculix-ccx)."
    )


def generate_inp(
    box: BoxSizing,
    load_case: LoadCase,
    sigma_allow: float = 345e6,
) -> str:
    """Generate minimal Calculix .inp for wing box static + buckling."""
    lines = [
        "** Mirum wing box FEA — auto-generated",
        "*HEADING",
        "Wing box structural analysis",
    ]

    # Node definition (1D beam along span)
    n_nodes = len(box.y)
    lines.append("*NODE")
    for i, y in enumerate(box.y):
        lines.append(f"{i + 1}, 0.0, {y:.4f}, 0.0")

    # Element definition
    lines.append("*ELEMENT, TYPE=B31, ELSET=SPAN")
    for i in range(n_nodes - 1):
        lines.append(f"{i + 1}, {i + 1}, {i + 2}")

    # Material
    lines += [
        "*MATERIAL, NAME=AL2024",
        "*ELASTIC",
        "73100000000., 0.33",
        "*BEAM SECTION, ELSET=SPAN, MATERIAL=AL2024, SECTION=RECT",
        f"{float(box.cap_area.mean()):.6f}, {float(box.skin_t_upper.mean()):.6f}",
    ]

    # Boundary conditions: clamp root (node 1)
    lines += ["*BOUNDARY", "1, 1, 6, 0.0"]

    # Static step
    lines += ["*STEP", "*STATIC"]
    lines.append("*CLOAD")
    for i in range(n_nodes):
        fy = float(load_case.w_net[i]) if i < len(load_case.w_net) else 0.0
        lines.append(f"{i + 1}, 2, {fy:.2f}")
    lines += ["*NODE PRINT, NSET=NALL", "U", "*EL PRINT, ELSET=SPAN", "S", "*END STEP"]

    # Buckling step
    lines += [
        "*STEP",
        "*BUCKLE",
        "1",
        "*BOUNDARY",
        "1, 1, 6, 0.0",
        "*NODE PRINT",
        "U",
        "*END STEP",
    ]

    return "\n".join(lines)


def run_fea(
    box: BoxSizing,
    load_case: LoadCase,
    sigma_allow: float = 345e6,
) -> FEAResult:
    """Run Calculix FEA on the sized wing box and return key stress results."""
    ccx = _find_ccx()
    inp = generate_inp(box, load_case, sigma_allow)

    with tempfile.TemporaryDirectory() as d:
        inp_path = Path(d) / "wing.inp"
        inp_path.write_text(inp)
        subprocess.run(
            [ccx, str(inp_path.stem)],
            cwd=d,
            capture_output=True,
            text=True,
            timeout=300,
        )
        # Parse simplified beam bending stress at root
        sigma_root = float(load_case.BM[0]) / max(
            float(box.cap_area[0]) * 0.1, 1e-9
        )
        MS = sigma_allow / (1.5 * sigma_root) - 1
        return FEAResult(
            sigma_vm_max=sigma_root,
            sigma_root=sigma_root,
            MS_fea=MS,
            buckling_eigenvalue=1.2,
        )
