"""Bounded inputs for small passive gliders, SI units throughout."""
import copy
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .model.config import CONFIG


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Materials(StrictModel):
    wing_areal_density_kg_m2: float = Field(0.30, ge=0.05, le=3)
    tail_areal_density_kg_m2: float = Field(0.15, ge=0.03, le=3)
    fuselage_shell_areal_density_kg_m2: float = Field(0.50, ge=0.05, le=5)
    fixed_hardware_mass_kg: float = Field(0.010, ge=0, le=0.2)
    wall_thickness_m: float = Field(0.003, gt=0, le=0.02)
    wing_cg_chord_fraction: float = Field(0.42, ge=0.1, le=0.8)


class GliderRequest(StrictModel):
    objective: Literal['distance', 'duration'] = 'distance'
    launch_height_m: float = Field(10, gt=0, le=100)
    payload_mass_kg: float = Field(0.060, ge=0, le=0.3)
    payload_diameter_m: float = Field(0.045, gt=0, le=0.1)
    payload_length_m: float = Field(0.060, gt=0, le=0.3)
    max_wingspan_m: float = Field(1.4, ge=0.6, le=1.4)
    max_length_m: float = Field(0.95, ge=0.45, le=0.95)
    max_total_mass_kg: float = Field(0.5, gt=0, le=0.5)
    max_flight_speed_m_s: float = Field(9, ge=1, le=15)
    static_margin_min: float = Field(0.05, ge=0.02, le=0.19)
    static_margin_target: float = Field(0.12, ge=0.02, le=0.2)
    stall_margin: float = Field(0.85, ge=0.5, le=0.9,
                                description='Maximum trimmed CL / estimated CLmax')
    materials: Materials = Field(default_factory=Materials)
    # Overrides can narrow or lock the model's supported design space.
    ranges: dict[str, dict[str, list]] = Field(default_factory=dict)
    evaluations: int = Field(40, ge=8, le=120, strict=True)
    top_k: int = Field(5, ge=1, le=10, strict=True)
    seed: int = Field(42, ge=0, le=2147483647, strict=True)

    @model_validator(mode='after')
    def validate_space(self):
        if self.static_margin_target < self.static_margin_min:
            raise ValueError('Target static margin must meet the minimum')
        for family, overrides in self.ranges.items():
            if family not in CONFIG['ranges']:
                raise ValueError(f'Unknown part family: {family}')
            for key, values in overrides.items():
                base = CONFIG['ranges'][family].get(key)
                if base is None or not values:
                    raise ValueError(f'Unknown or empty range: {family}.{key}')
                if isinstance(base, tuple):
                    if (len(values) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) for x in values)
                            or not base[0] <= values[0] <= values[1] <= base[1]):
                        raise ValueError(f'{family}.{key} must be an ordered interval within {base}')
                elif any(type(v) is not type(base[0]) or v not in base for v in values):
                    raise ValueError(f'Unsupported choice for {family}.{key}')
        for family, key, cap in [('wing', 'span_m', self.max_wingspan_m),
                                  ('fuselage', 'length_m', self.max_length_m)]:
            lower = self.ranges.get(family, {}).get(key, CONFIG['ranges'][family][key])[0]
            if lower > cap:
                raise ValueError(f'{family}.{key} lower bound exceeds maximum dimension')
        return self

    def configuration(self):
        cfg = copy.deepcopy(CONFIG)
        cfg['seed'] = self.seed
        cfg['mission'].update(objective=self.objective, launch_height_m=self.launch_height_m,
                              payload_mass_kg=self.payload_mass_kg,
                              payload_diameter_m=self.payload_diameter_m,
                              payload_length_m=self.payload_length_m)
        cfg['materials'] = self.materials.model_dump()
        cfg['limits'].update(max_total_mass_kg=self.max_total_mass_kg,
                             max_glide_speed_m_s=self.max_flight_speed_m_s,
                             static_margin_min=self.static_margin_min,
                             static_margin_target=self.static_margin_target,
                             stall_margin=self.stall_margin)
        for family, overrides in self.ranges.items():
            cfg['ranges'][family].update(overrides)
        for family, key, cap in [('wing', 'span_m', self.max_wingspan_m),
                                  ('fuselage', 'length_m', self.max_length_m)]:
            lo, hi = cfg['ranges'][family][key]
            cfg['ranges'][family][key] = (lo, min(hi, cap))
        return cfg
