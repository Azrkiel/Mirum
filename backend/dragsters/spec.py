"""Explicit millimeter inputs for the fixed wedge topology."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DragsterSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)

    schema_version: Literal[1]
    style: Literal['wedge']
    units: Literal['mm']
    length_mm: float = Field(ge=160, le=260)
    width_mm: float = Field(ge=32, le=60)
    height_mm: float = Field(ge=28, le=45)
    nose_height_mm: float = Field(ge=8, le=18)
    ground_clearance_mm: float = Field(ge=0, le=10)
    front_axle_x_mm: float = Field(ge=10, le=250)
    rear_axle_x_mm: float = Field(ge=10, le=250)
    axle_z_mm: float = Field(ge=4, le=24)
    axle_bore_diameter_mm: float = Field(ge=2, le=5)
    pocket_diameter_mm: float = Field(ge=12, le=24)
    pocket_depth_mm: float = Field(ge=30, le=80)
    pocket_center_z_mm: float = Field(ge=15, le=45)
    min_wall_mm: float = Field(ge=2, le=5)

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('schema_version must be the integer 1')
        return value

    @model_validator(mode='after')
    def supported_envelope(self):
        L, W, H, N, g = (self.length_mm, self.width_mm, self.height_mm,
                          self.nose_height_mm, self.ground_clearance_mm)
        xf, xr, za = self.front_axle_x_mm, self.rear_axle_x_mm, self.axle_z_mm
        ra, rp = self.axle_bore_diameter_mm / 2, self.pocket_diameter_mm / 2
        D, zp, t = self.pocket_depth_mm, self.pocket_center_z_mm, self.min_wall_mm
        constraints = [
            (N < H, 'nose_height_mm must be below height_mm'),
            (xf < xr, 'front_axle_x_mm must precede rear_axle_x_mm'),
            (xr - xf >= 2 * ra + t, 'front_axle_x_mm/rear_axle_x_mm separation requires axle_bore_diameter_mm + min_wall_mm'),
            (L - D >= L / 2 + t, 'pocket_depth_mm must remain in the flat rear housing of length_mm with min_wall_mm'),
            (W / 2 >= rp + t, 'width_mm must contain pocket_diameter_mm and min_wall_mm'),
            (zp - rp >= g + t, 'pocket_center_z_mm/pocket_diameter_mm must clear ground_clearance_mm + min_wall_mm'),
            (zp + rp <= g + H - t, 'pocket_center_z_mm/pocket_diameter_mm exceeds height_mm above ground_clearance_mm minus min_wall_mm'),
            (za - ra >= g + t, 'axle_z_mm/axle_bore_diameter_mm must clear ground_clearance_mm + min_wall_mm'),
            (za + ra + t <= zp - rp, 'axle_z_mm/axle_bore_diameter_mm must separate from pocket_center_z_mm/pocket_diameter_mm by min_wall_mm'),
        ]
        for name, x in [('front_axle_x_mm', xf), ('rear_axle_x_mm', xr)]:
            constraints.extend([
                (x - ra >= t, f'{name}/axle_bore_diameter_mm must clear nose by min_wall_mm'),
                (x + ra <= L - t, f'{name}/axle_bore_diameter_mm must clear length_mm by min_wall_mm'),
                (za + ra + t <= g + N + (H - N) * min(2 * (x - ra) / L, 1),
                 f'{name}/axle_z_mm/axle_bore_diameter_mm must clear wedge top (ground_clearance_mm, nose_height_mm, height_mm, length_mm, min_wall_mm)'),
            ])
        for passed, reason in constraints:
            if not passed:
                raise ValueError(reason)
        return self


def canonical_spec(spec: DragsterSpec) -> str:
    return json.dumps(spec.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), allow_nan=False)


def specification_digest(spec: DragsterSpec) -> str:
    return hashlib.sha256(canonical_spec(spec).encode('utf-8')).hexdigest()
