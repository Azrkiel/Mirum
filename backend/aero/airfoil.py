"""Airfoil geometry and section properties — Module 2."""
from __future__ import annotations
from dataclasses import dataclass
import math
import re
from pathlib import Path
from typing import Sequence


@dataclass
class SectionProps:
    area: float
    cx: float
    cz: float
    Ixx: float
    Izz: float
    Ixz: float
    t_max: float        # max thickness / chord
    t_max_x: float      # x/c location of max thickness
    camber_max: float   # max camber / chord
    camber_x: float     # x/c location of max camber


def _shoelace_props(pts: list[tuple[float, float]]) -> SectionProps:
    n = len(pts)
    A = cx = cz = Ixx = Izz = Ixz = 0.0
    for i in range(n):
        x0, z0 = pts[i]
        x1, z1 = pts[(i + 1) % n]
        cross = x0 * z1 - x1 * z0
        A += cross
        cx += (x0 + x1) * cross
        cz += (z0 + z1) * cross
        Ixx += (z0**2 + z0 * z1 + z1**2) * cross
        Izz += (x0**2 + x0 * x1 + x1**2) * cross
        Ixz += (x0 * z1 + 2 * x0 * z0 + 2 * x1 * z1 + x1 * z0) * cross
    A /= 2.0
    if abs(A) < 1e-14:
        raise ValueError("Degenerate airfoil polygon")
    cx /= 6 * A
    cz /= 6 * A
    Ixx = abs(Ixx) / 12.0
    Izz = abs(Izz) / 12.0
    Ixz = abs(Ixz) / 24.0
    return SectionProps(area=abs(A), cx=cx, cz=cz,
                        Ixx=Ixx, Izz=Izz, Ixz=Ixz,
                        t_max=0.0, t_max_x=0.5,
                        camber_max=0.0, camber_x=0.5)


class NACAProfile:
    """NACA 4-digit symmetric or cambered profile (cosine-spaced)."""

    def __init__(self, designation: str, n_pts: int = 100):
        self.designation = designation.upper().replace("NACA", "").strip()
        if len(self.designation) != 4:
            raise ValueError(f"Expected 4-digit NACA, got '{designation}'")
        self.m = int(self.designation[0]) / 100.0
        self.p = int(self.designation[1]) / 10.0
        self.t = int(self.designation[2:]) / 100.0
        self.n_pts = n_pts
        self._pts: list[tuple[float, float]] | None = None

    def _thickness(self, x: float) -> float:
        t = self.t
        return 5 * t * (0.2969 * x**0.5 - 0.1260 * x
                        - 0.3516 * x**2 + 0.2843 * x**3 - 0.1015 * x**4)

    def _camber(self, x: float) -> tuple[float, float]:
        m, p = self.m, self.p
        if m == 0 or p == 0:
            return 0.0, 0.0
        if x <= p:
            yc = m / p**2 * (2 * p * x - x**2)
            dyc = 2 * m / p**2 * (p - x)
        else:
            yc = m / (1 - p)**2 * (1 - 2 * p + 2 * p * x - x**2)
            dyc = 2 * m / (1 - p)**2 * (p - x)
        return yc, dyc

    @property
    def points(self) -> list[tuple[float, float]]:
        if self._pts is not None:
            return self._pts
        beta = [math.pi * i / self.n_pts for i in range(self.n_pts + 1)]
        xs = [(1 - math.cos(b)) / 2 for b in beta]
        upper, lower = [], []
        camber_vals = []
        t_vals = []
        for x in xs:
            yt = self._thickness(x)
            yc, dyc = self._camber(x)
            theta = math.atan(dyc)
            camber_vals.append((x, yc))
            t_vals.append((x, yt))
            upper.append((x - yt * math.sin(theta), yc + yt * math.cos(theta)))
            lower.append((x + yt * math.sin(theta), yc - yt * math.cos(theta)))
        # compute max thickness and camber
        max_t = max(t_vals, key=lambda v: v[1])
        max_c = max(camber_vals, key=lambda v: v[1])
        pts = upper + list(reversed(lower[1:]))
        self._pts = pts
        self._t_max = max_t[1] * 2  # half-thickness → full
        self._t_max_x = max_t[0]
        self._camber_max = max_c[1]
        self._camber_x = max_c[0]
        return self._pts

    @property
    def section_props(self) -> SectionProps:
        props = _shoelace_props(self.points)
        props.t_max = self._t_max if hasattr(self, "_t_max") else self.t
        props.t_max_x = self._t_max_x if hasattr(self, "_t_max_x") else 0.3
        props.camber_max = self._camber_max if hasattr(self, "_camber_max") else self.m
        props.camber_x = self._camber_x if hasattr(self, "_camber_x") else self.p
        return props


class DatProfile:
    """Import airfoil from .dat file (auto-detect Selig vs Lednicer)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._pts = self._load()

    def _load(self) -> list[tuple[float, float]]:
        lines = self.path.read_text().splitlines()
        coords: list[tuple[float, float]] = []
        header_skipped = False
        lednicer = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) == 2:
                try:
                    x, z = float(parts[0]), float(parts[1])
                except ValueError:
                    continue
                if not header_skipped and x > 1.5:
                    # Lednicer header: first float is npts_upper
                    lednicer = True
                    header_skipped = True
                    continue
                if not header_skipped:
                    header_skipped = True
                coords.append((x, z))
            else:
                header_skipped = True  # skip name line
        if lednicer:
            # Lednicer: upper surface TE→LE then lower LE→TE
            # already in correct winding if we just use as-is
            pass
        return coords

    @property
    def points(self) -> list[tuple[float, float]]:
        return self._pts

    @property
    def section_props(self) -> SectionProps:
        props = _shoelace_props(self._pts)
        # estimate t_max from upper/lower split
        upper = [(x, z) for x, z in self._pts if z >= 0]
        lower = [(x, z) for x, z in self._pts if z < 0]
        if upper and lower:
            max_t = 0.0
            for xu, zu in upper:
                zl = min((abs(z) for x, z in lower if abs(x - xu) < 0.05), default=0.0)
                max_t = max(max_t, zu + zl)
            props.t_max = max_t
        return props


def get_profile(spec_airfoil: str, n_pts: int = 100):
    """Return NACAProfile or DatProfile from a WingSpec.airfoil string."""
    s = spec_airfoil.strip()
    if re.match(r"(?i)naca\s*\d{4}$", s):
        return NACAProfile(s, n_pts)
    p = Path(s)
    if p.exists():
        return DatProfile(p)
    raise ValueError(f"Cannot resolve airfoil '{s}': not a NACA 4-digit and file not found.")
