"""RegenChamberSpec: typed, validated input for the fixed PicoGK regen-chamber builder.

Geometry only. No gas dynamics, thermal, or structural analysis is implied.
The contour is a simplified C1 cosine blend, not an optimized nozzle.
Pure Python (pydantic only): no cadquery, no compiler import.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ASSUMPTIONS = 'geometry_only_no_gas_dynamics_thermal_or_structural_analysis'
MAX_SLOPE = math.tan(math.radians(35.0))
MAX_BOUNDING_VOXELS = 3e8
PORT_OVERSHOOT_MM = 3.0


class RegenChamberSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)

    family: Literal['regen_chamber']
    units: Literal['mm']
    chamber_radius_mm: float = Field(gt=0, le=500)
    chamber_length_mm: float = Field(gt=0, le=2000)
    convergent_length_mm: float = Field(gt=0, le=2000)
    throat_radius_mm: float = Field(gt=0, le=500)
    divergent_length_mm: float = Field(gt=0, le=2000)
    exit_radius_mm: float = Field(gt=0, le=500)
    wall_thickness_mm: float = Field(gt=0, le=100)
    channel_count: int = Field(ge=4, le=256)
    channel_radius_mm: float = Field(gt=0, le=50)
    channel_twist_rad_per_mm: float = Field(ge=-0.5, le=0.5)
    manifold_radius_mm: float = Field(gt=0, le=50)
    end_margin_mm: float = Field(gt=0, le=1000)
    voxel_size_mm: float = Field(ge=0.1, le=1.0)
    assumptions: Literal['geometry_only_no_gas_dynamics_thermal_or_structural_analysis']

    # derived helpers (pure functions of the fields)
    @property
    def total_length_mm(self) -> float:
        return self.chamber_length_mm + self.convergent_length_mm + self.divergent_length_mm

    @property
    def throat_z_mm(self) -> float:
        return self.chamber_length_mm + self.convergent_length_mm

    @property
    def min_feature_mm(self) -> float:
        return max(0.5, 4.0 * self.voxel_size_mm)

    @property
    def max_slope(self) -> float:
        c = math.pi / 2 * (self.chamber_radius_mm - self.throat_radius_mm) / self.convergent_length_mm
        d = math.pi / 2 * (self.exit_radius_mm - self.throat_radius_mm) / self.divergent_length_mm
        return max(c, d)

    @property
    def manifold_z_mm(self) -> tuple[float, float]:
        return (self.end_margin_mm, self.total_length_mm - self.end_margin_mm)

    @model_validator(mode='after')
    def supported_envelope(self):
        v = self.voxel_size_mm
        m = self.min_feature_mm
        if self.throat_radius_mm >= self.chamber_radius_mm or self.throat_radius_mm >= self.exit_radius_mm:
            raise ValueError('Throat radius must be smaller than both chamber and exit radius')
        if self.max_slope > MAX_SLOPE:
            raise ValueError(f'Contour max slope {self.max_slope:.3f} exceeds tan(35 deg)={MAX_SLOPE:.3f}')
        if 2 * self.channel_radius_mm < 4 * v:
            raise ValueError(f'Channel radius {self.channel_radius_mm} mm: channel diameter must be >= 4 voxels ({4 * v:.3f} mm)')
        wall_t = (self.wall_thickness_mm / 2 - self.channel_radius_mm) * math.cos(math.atan(self.max_slope))
        if wall_t < m:
            raise ValueError(f'Cold/hot wall normal thickness {wall_t:.3f} mm is below minimum {m:.3f} mm')
        rmid = self.throat_radius_mm + self.wall_thickness_mm / 2
        beta = math.atan(self.channel_twist_rad_per_mm * rmid)
        rib = 2 * math.pi * rmid / self.channel_count - 2 * self.channel_radius_mm / math.cos(beta)
        if rib < m:
            raise ValueError(f'Rib at throat {rib:.3f} mm is below minimum {m:.3f} mm')
        if self.wall_thickness_mm / 2 - self.manifold_radius_mm < m:
            raise ValueError(f'Manifold wall clearance {self.wall_thickness_mm / 2 - self.manifold_radius_mm:.3f} mm is below minimum {m:.3f} mm')
        if self.end_margin_mm < 2 * self.manifold_radius_mm + 1:
            raise ValueError('End margin must be at least 2*manifold_radius + 1 mm so the manifold ring fits')
        z1, z2 = self.manifold_z_mm
        if z1 + self.manifold_radius_mm > self.chamber_length_mm:
            raise ValueError('Forward manifold ring must lie fully on the chamber cylinder, not in the convergent blend')
        if z2 - self.manifold_radius_mm < self.throat_z_mm + self.manifold_radius_mm + self.wall_thickness_mm:
            raise ValueError('Aft manifold ring is too close to the throat / inside the throat blend region')
        rmax = max(self.chamber_radius_mm, self.exit_radius_mm)
        voxels = (2 * (rmax + self.wall_thickness_mm + 5) / v) ** 2 * (self.total_length_mm + 4) / v
        if voxels > MAX_BOUNDING_VOXELS:
            raise ValueError(f'Bounding voxel count {voxels:.3g} exceeds {MAX_BOUNDING_VOXELS:.0e}')
        return self


def inner_radius(spec: RegenChamberSpec, z: float) -> float:
    """Inner (hot-gas side) wall radius at axial position z, C1 cosine blends."""
    cl = spec.chamber_length_mm
    zt = spec.throat_z_mm
    if z <= cl:
        return spec.chamber_radius_mm
    if z <= zt:
        t = (z - cl) / spec.convergent_length_mm
        return spec.throat_radius_mm + (spec.chamber_radius_mm - spec.throat_radius_mm) * 0.5 * (1 + math.cos(math.pi * t))
    if z <= spec.total_length_mm:
        t = (z - zt) / spec.divergent_length_mm
        return spec.throat_radius_mm + (spec.exit_radius_mm - spec.throat_radius_mm) * 0.5 * (1 - math.cos(math.pi * t))
    return spec.exit_radius_mm


def canonical_spec(spec: RegenChamberSpec) -> str:
    return json.dumps(spec.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), allow_nan=False)


def specification_digest(spec: RegenChamberSpec) -> str:
    return hashlib.sha256(canonical_spec(spec).encode()).hexdigest()
