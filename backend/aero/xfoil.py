"""XFOIL subprocess wrapper for 2D aerodynamic polar computation."""
from __future__ import annotations
import subprocess
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import numpy as np


class XFOILNotFoundError(RuntimeError):
    pass


@dataclass
class PolarResult:
    alpha: np.ndarray
    cl: np.ndarray
    cd: np.ndarray
    cm: np.ndarray
    cl_max: float
    stall_alpha: float
    cl_alpha: float   # per degree


def _find_xfoil() -> str:
    for candidate in [
        "xfoil",
        "/usr/bin/xfoil",
        "/opt/conda/bin/xfoil",
        "/usr/local/bin/xfoil",
    ]:
        found = shutil.which(candidate)
        if found:
            return found
    raise XFOILNotFoundError(
        "XFOIL binary not found. "
        "Add 'xfoil' to the Dockerfile (apt-get install xfoil)."
    )


def run_polar(
    profile_pts: np.ndarray,
    Re: float,
    M: float,
    alpha_start: float = -4.0,
    alpha_end: float = 20.0,
    alpha_step: float = 0.5,
    max_iter: int = 200,
    max_fail: int = 3,
) -> PolarResult:
    """Run XFOIL polar for airfoil defined by coordinate array."""
    if M > 0.7:
        raise ValueError(
            f"XFOIL invalid for M={M:.3f} > 0.7 (panel method). "
            "Use transonic code."
        )
    xfoil_bin = _find_xfoil()

    with tempfile.TemporaryDirectory() as tmpdir:
        coord_file = Path(tmpdir) / "foil.dat"
        polar_file = Path(tmpdir) / "polar.txt"

        with open(coord_file, "w") as f:
            f.write("AIRFOIL\n")
            for x, y in profile_pts:
                f.write(f"  {x:.6f}  {y:.6f}\n")

        # Build XFOIL command script
        cmds = [
            f"LOAD {coord_file}",
            "PANE",
            "OPER",
            f"VISC {Re:.0f}",
            f"MACH {M:.4f}",
            f"ITER {max_iter}",
            f"PACC {polar_file} ",
            "",   # no dump file
        ]
        alpha = alpha_start
        while alpha <= alpha_end + 1e-9:
            cmds.append(f"ALFA {alpha:.3f}")
            alpha += alpha_step
        cmds += ["PACC", "", "QUIT"]
        stdin_text = "\n".join(cmds) + "\n"

        try:
            subprocess.run(
                [xfoil_bin],
                input=stdin_text,
                text=True,
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("XFOIL timed out")

        if not polar_file.exists():
            raise RuntimeError("XFOIL produced no polar output")

        alpha_l, cl_l, cd_l, cm_l = [], [], [], []
        consec_fail = 0
        with open(polar_file) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 6:
                    try:
                        a = float(parts[0])
                        cl = float(parts[1])
                        cd = float(parts[2])
                        cm = float(parts[4])
                        alpha_l.append(a)
                        cl_l.append(cl)
                        cd_l.append(cd)
                        cm_l.append(cm)
                        consec_fail = 0
                    except ValueError:
                        consec_fail += 1
                        if consec_fail >= max_fail:
                            break

    alpha_arr = np.array(alpha_l)
    cl_arr = np.array(cl_l)
    cd_arr = np.array(cd_l)
    cm_arr = np.array(cm_l)

    if len(cl_arr) < 3:
        raise RuntimeError("XFOIL returned insufficient converged points")

    cl_max = float(cl_arr.max())
    stall_idx = int(np.argmax(cl_arr))
    stall_alpha = float(alpha_arr[stall_idx])

    # cl_alpha from linear region (below stall)
    linear = alpha_arr < stall_alpha * 0.8
    if linear.sum() > 2:
        fit = np.polyfit(alpha_arr[linear], cl_arr[linear], 1)
        cl_alpha = float(fit[0])
    else:
        cl_alpha = float(np.gradient(cl_arr, alpha_arr).mean())

    return PolarResult(
        alpha=alpha_arr, cl=cl_arr, cd=cd_arr, cm=cm_arr,
        cl_max=cl_max, stall_alpha=stall_alpha, cl_alpha=cl_alpha,
    )
