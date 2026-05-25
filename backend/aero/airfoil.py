"""Airfoil geometry: NACA 4/5-digit generation, .dat import, section properties."""
from __future__ import annotations
import math
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SectionProps:
    area: float
    x_centroid: float
    y_centroid: float
    Ixx: float
    Iyy: float
    Ixy: float
    le_radius: float
    t_c: float


def _cosine_resample(pts: np.ndarray, n: int = 200) -> np.ndarray:
    """Resample closed airfoil polygon to n cosine-spaced points."""
    diffs = np.diff(pts, axis=0)
    seg_len = np.hypot(diffs[:, 0], diffs[:, 1])
    s = np.concatenate([[0], np.cumsum(seg_len)])
    s /= s[-1]
    # Cosine spacing: clustered at leading and trailing edges
    t_cos = 0.5 * (1 - np.cos(np.linspace(0, np.pi, n)))
    from scipy.interpolate import interp1d
    fx = interp1d(s, pts[:, 0], kind='cubic')
    fy = interp1d(s, pts[:, 1], kind='cubic')
    return np.column_stack([fx(t_cos), fy(t_cos)])


def _section_props(pts: np.ndarray) -> tuple:
    """Green's theorem on closed polygon. Returns (A, cx, cy, Ixx, Iyy, Ixy)."""
    x, y = pts[:, 0], pts[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    A = 0.5 * abs(np.sum(cross))
    cx = np.sum((x + xn) * cross) / (6 * A)
    cy = np.sum((y + yn) * cross) / (6 * A)
    Ixx = np.sum((y**2 + y * yn + yn**2) * cross) / 12
    Iyy = np.sum((x**2 + x * xn + xn**2) * cross) / 12
    Ixy = np.sum((x * yn + 2 * x * y + 2 * xn * yn + xn * y) * cross) / 24
    return A, cx, cy, abs(Ixx), abs(Iyy), abs(Ixy)


class NACAProfile:
    def __init__(self, code: str, chord: float = 1.0, n_pts: int = 200):
        self.code = code.upper().replace("NACA", "").strip()
        self.chord = chord
        self._pts = self._generate(n_pts)

    def _generate(self, n: int) -> np.ndarray:
        c = self.code
        if len(c) == 4:
            m = int(c[0]) / 100
            p = int(c[1]) / 10
            t = int(c[2:]) / 100
        else:
            raise ValueError(f"Only NACA 4-digit implemented, got {c}")
        beta = np.linspace(0, np.pi, n // 2 + 1)
        x = 0.5 * (1 - np.cos(beta))
        # Thickness distribution
        yt = 5 * t * (
            0.2969 * np.sqrt(x)
            - 0.1260 * x
            - 0.3516 * x**2
            + 0.2843 * x**3
            - 0.1015 * x**4
        )
        # Camber line and slope
        if m == 0 and p == 0:
            yc = np.zeros_like(x)
            dyc = np.zeros_like(x)
        else:
            yc = np.where(
                x < p,
                m / p**2 * (2 * p * x - x**2),
                m / (1 - p)**2 * ((1 - 2 * p) + 2 * p * x - x**2),
            )
            dyc = np.where(
                x < p,
                2 * m / p**2 * (p - x),
                2 * m / (1 - p)**2 * (p - x),
            )
        theta = np.arctan(dyc)
        xu = (x - yt * np.sin(theta)) * self.chord
        yu = (yc + yt * np.cos(theta)) * self.chord
        xl = (x + yt * np.sin(theta)) * self.chord
        yl = (yc - yt * np.cos(theta)) * self.chord
        upper = np.column_stack([xu, yu])
        lower = np.column_stack([xl[::-1], yl[::-1]])
        return np.vstack([upper, lower[1:]])

    @property
    def coords(self) -> np.ndarray:
        return self._pts

    @property
    def t_c(self) -> float:
        return int(self.code[2:]) / 100

    @property
    def le_radius(self) -> float:
        return 1.1019 * self.t_c**2 * self.chord

    @property
    def section_props(self) -> SectionProps:
        A, cx, cy, Ixx, Iyy, Ixy = _section_props(self._pts)
        return SectionProps(
            area=A, x_centroid=cx, y_centroid=cy,
            Ixx=Ixx, Iyy=Iyy, Ixy=Ixy,
            le_radius=self.le_radius, t_c=self.t_c,
        )


class DatProfile:
    """Import airfoil from Selig (.dat) or Lednicer format."""

    def __init__(self, path: str | Path, chord: float = 1.0, n_pts: int = 200):
        self.path = Path(path)
        self.chord = chord
        raw = self._load()
        self._pts = _cosine_resample(raw * chord, n_pts)

    def _load(self) -> np.ndarray:
        lines = self.path.read_text().splitlines()
        data = []
        for line in lines:
            parts = line.split()
            if len(parts) == 2:
                try:
                    data.append([float(parts[0]), float(parts[1])])
                except ValueError:
                    continue
        pts = np.array(data)
        # Detect Lednicer: first non-header row has x > 1 (count row)
        if len(pts) > 0 and pts[0, 0] > 1.1:
            pts = pts[1:]
        return pts

    @property
    def coords(self) -> np.ndarray:
        return self._pts

    @property
    def section_props(self) -> SectionProps:
        A, cx, cy, Ixx, Iyy, Ixy = _section_props(self._pts)
        t_c = (self._pts[:, 1].max() - self._pts[:, 1].min()) / self.chord
        le_r = 1.1019 * t_c**2 * self.chord
        return SectionProps(
            area=A, x_centroid=cx, y_centroid=cy,
            Ixx=Ixx, Iyy=Iyy, Ixy=Ixy,
            le_radius=le_r, t_c=t_c,
        )
