"""XFOIL subprocess wrapper — Module 3."""
from __future__ import annotations
import subprocess
import tempfile
import os
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


class XFOILNotFoundError(RuntimeError):
    pass


@dataclass
class PolarResult:
    alphas: list[float] = field(default_factory=list)
    CLs: list[float] = field(default_factory=list)
    CDs: list[float] = field(default_factory=list)
    CMs: list[float] = field(default_factory=list)
    converged: list[bool] = field(default_factory=list)

    @property
    def CL_max(self) -> float:
        cls = [cl for cl, ok in zip(self.CLs, self.converged) if ok]
        return max(cls) if cls else float("nan")

    @property
    def alpha_stall(self) -> float:
        pairs = [(a, cl) for a, cl, ok in zip(self.alphas, self.CLs, self.converged) if ok]
        if not pairs:
            return float("nan")
        max_cl = max(pairs, key=lambda p: p[1])
        return max_cl[0]

    def at_alpha(self, alpha: float) -> tuple[float, float, float]:
        """Interpolate (CL, CD, CM) at given alpha. Returns NaN if out of range."""
        ok_pairs = [(a, cl, cd, cm) for a, cl, cd, cm, ok
                    in zip(self.alphas, self.CLs, self.CDs, self.CMs, self.converged) if ok]
        if not ok_pairs:
            return float("nan"), float("nan"), float("nan")
        ok_pairs.sort(key=lambda p: p[0])
        alphas = [p[0] for p in ok_pairs]
        if alpha <= alphas[0]:
            return ok_pairs[0][1], ok_pairs[0][2], ok_pairs[0][3]
        if alpha >= alphas[-1]:
            return ok_pairs[-1][1], ok_pairs[-1][2], ok_pairs[-1][3]
        for i in range(len(alphas) - 1):
            if alphas[i] <= alpha <= alphas[i + 1]:
                t = (alpha - alphas[i]) / (alphas[i + 1] - alphas[i])
                return (
                    ok_pairs[i][1] + t * (ok_pairs[i + 1][1] - ok_pairs[i][1]),
                    ok_pairs[i][2] + t * (ok_pairs[i + 1][2] - ok_pairs[i][2]),
                    ok_pairs[i][3] + t * (ok_pairs[i + 1][3] - ok_pairs[i][3]),
                )
        return float("nan"), float("nan"), float("nan")


def _find_xfoil() -> str:
    for candidate in ("xfoil", "xfoil.exe", "/usr/local/bin/xfoil", "/opt/xfoil/xfoil"):
        if os.path.isfile(candidate) or _which(candidate):
            return candidate
    raise XFOILNotFoundError(
        "xfoil binary not found. Install XFOIL and ensure it is on PATH."
    )


def _which(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def run_polar(
    profile_pts: list[tuple[float, float]],
    Re: float,
    M: float,
    alpha_start: float = -4.0,
    alpha_end: float = 20.0,
    alpha_step: float = 0.5,
    max_iter: int = 200,
    max_fail: int = 3,
) -> PolarResult:
    """
    Drive XFOIL via stdin for a polar sweep.
    Raises XFOILNotFoundError if binary missing.
    Returns PolarResult with NaN entries where XFOIL did not converge.
    """
    xfoil_bin = _find_xfoil()

    with tempfile.TemporaryDirectory() as tmp:
        coord_file = Path(tmp) / "profile.dat"
        polar_file = Path(tmp) / "polar.txt"

        # Write coordinate file (Selig format)
        lines = ["AirfoilProfile"]
        for x, z in profile_pts:
            lines.append(f"  {x:.6f}  {z:.6f}")
        coord_file.write_text("\n".join(lines))

        # Build XFOIL stdin script
        cmds = [
            f"LOAD {coord_file}",
            "PANE",
            "OPER",
            f"VISC {Re:.2f}",
            f"MACH {M:.4f}",
            f"ITER {max_iter}",
            "PACC",
            str(polar_file),
            "",   # no dump file
            f"ASEQ {alpha_start} {alpha_end} {alpha_step}",
            "PACC",
            "",
            "QUIT",
        ]
        stdin_str = "\n".join(cmds) + "\n"

        try:
            result = subprocess.run(
                [xfoil_bin],
                input=stdin_str,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            raise XFOILNotFoundError(f"xfoil binary '{xfoil_bin}' not executable.")

        return _parse_polar(polar_file, alpha_start, alpha_end, alpha_step, max_fail)


def _parse_polar(
    polar_file: Path,
    alpha_start: float,
    alpha_end: float,
    alpha_step: float,
    max_fail: int,
) -> PolarResult:
    result = PolarResult()
    expected_alphas = []
    a = alpha_start
    while a <= alpha_end + 1e-9:
        expected_alphas.append(round(a, 6))
        a += alpha_step

    parsed: dict[float, tuple[float, float, float]] = {}
    if polar_file.exists():
        for line in polar_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!") or line.startswith("---"):
                continue
            parts = line.split()
            if len(parts) >= 4:
                try:
                    alpha = float(parts[0])
                    CL = float(parts[1])
                    CD = float(parts[2])
                    CM = float(parts[4]) if len(parts) > 4 else 0.0
                    parsed[round(alpha, 6)] = (CL, CD, CM)
                except (ValueError, IndexError):
                    continue

    fail_count = 0
    for a in expected_alphas:
        if a in parsed:
            cl, cd, cm = parsed[a]
            result.alphas.append(a)
            result.CLs.append(cl)
            result.CDs.append(cd)
            result.CMs.append(cm)
            result.converged.append(True)
            fail_count = 0
        else:
            result.alphas.append(a)
            result.CLs.append(float("nan"))
            result.CDs.append(float("nan"))
            result.CMs.append(float("nan"))
            result.converged.append(False)
            fail_count += 1
            if fail_count >= max_fail:
                break  # abort — polar has diverged

    return result
