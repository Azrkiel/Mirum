"""Approved, explicit requirements for the v1 beam capability envelope."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CantileverSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)

    family: Literal['rectangular_cantilever']
    units: Literal['mm_N_MPa']
    length_mm: float = Field(gt=0, le=10000)
    width_mm: float = Field(gt=0, le=1000)
    height_mm: float = Field(gt=0, le=1000)
    dimensional_tolerance_mm: float = Field(ge=0.00001, le=0.1)
    youngs_modulus_mpa: float = Field(gt=0, le=10000000)
    allowable_stress_mpa: float = Field(gt=0, le=100000)
    tip_force_n: float = Field(gt=0, le=100000000)
    max_tip_deflection_mm: float = Field(gt=0, le=100)
    material_reference: str = Field(min_length=1, max_length=240)
    assumptions: Literal['ideal_clamp_end_force_linear_elastic']

    @model_validator(mode='after')
    def supported_envelope(self):
        if max(self.width_mm, self.height_mm) > self.length_mm / 10:
            raise ValueError('Beam requires length >= 10 times both section dimensions')
        if self.dimensional_tolerance_mm > min(self.width_mm, self.height_mm) / 100:
            raise ValueError('Dimensional tolerance exceeds 1 percent of the section')
        if self.max_tip_deflection_mm > self.length_mm * 0.02:
            raise ValueError('Deflection requirement must remain within 2 percent of span')
        if not self.material_reference.strip():
            raise ValueError('A user-approved material/allowable reference is required')
        return self


def canonical_spec(spec: CantileverSpec) -> str:
    return json.dumps(spec.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), allow_nan=False)


def specification_digest(spec: CantileverSpec) -> str:
    return hashlib.sha256(canonical_spec(spec).encode()).hexdigest()


def build_source(spec: CantileverSpec) -> str:
    """No model call or interpolation of user strings into executable source."""
    return ('import cadquery as cq\n'
            f'result = cq.Workplane("XY").box({spec.length_mm!r}, '
            f'{spec.width_mm!r}, {spec.height_mm!r}, centered=(False, False, False))\n')

