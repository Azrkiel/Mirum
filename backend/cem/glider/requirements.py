"""Explicit maximization missions; SI dimensions and bounded computation."""
from typing import Literal

from pydantic import Field, model_validator
from cem.core import StrictModel

Process = Literal['sheet_foam', 'balsa', 'fdm_lwpla', 'fdm_pla', 'hybrid', 'auto']
Family = Literal['conventional', 't_tail', 'v_tail', 'tailless_swept', 'flying_plank',
                 'canard', 'tandem', 'box', 'blended_body']


class Launch(StrictModel):
    height_m: float = Field(10., ge=0, le=100)
    method: Literal['drop', 'hand_toss', 'discus', 'catapult', 'hi_start']
    launch_speed_m_s: float | None = Field(None, ge=0, le=60)

    @model_validator(mode='after')
    def supported(self):
        if self.method == 'drop' and self.launch_speed_m_s not in (None, 0):
            raise ValueError('A drop has no additional launch speed')
        if self.method == 'hi_start' and self.launch_speed_m_s is None:
            raise ValueError('hi_start requires an explicit release speed and release height')
        return self


class Thermal(StrictModel):
    core_uplift_m_s: float = Field(ge=0.01, le=10)
    radius_m: float = Field(ge=1, le=1000)
    distance_m: float = Field(ge=0, le=10000)


class Ridge(StrictModel):
    slope_deg: float = Field(ge=1, le=60)
    wind_m_s: float = Field(ge=0.1, le=20)


class Environment(StrictModel):
    kind: Literal['still_air', 'wind', 'uniform_uplift', 'thermal', 'ridge'] = 'still_air'
    wind_m_s: float = Field(0., ge=-15, le=15)
    uplift_m_s: float = Field(0., ge=0, le=5)
    thermal: Thermal | None = None
    ridge: Ridge | None = None
    altitude_m: float = Field(0., ge=0, le=5000)
    temperature_c: float = Field(15., ge=-30, le=45)

    @model_validator(mode='after')
    def explicit_toggles(self):
        if self.kind != 'wind' and self.wind_m_s != 0:
            raise ValueError('Enable wind explicitly before supplying wind')
        if self.kind != 'uniform_uplift' and self.uplift_m_s != 0:
            raise ValueError('Enable uniform uplift explicitly')
        if (self.thermal is not None) != (self.kind == 'thermal'):
            raise ValueError('Thermal inputs require the thermal environment')
        if (self.ridge is not None) != (self.kind == 'ridge'):
            raise ValueError('Ridge inputs require the ridge environment')
        return self


class Payload(StrictModel):
    mass_kg: float = Field(ge=0, le=0.3)
    diameter_m: float = Field(ge=0.001, le=0.1)
    length_m: float = Field(ge=0.001, le=0.3)


class Constraints(StrictModel):
    # Existing solver's qualified range; broader dimensions require qualification.
    max_span_m: float = Field(1.4, ge=0.6, le=1.4)
    max_length_m: float = Field(0.95, ge=0.45, le=0.95)
    max_mass_kg: float = Field(0.5, ge=0.001, le=0.5)
    max_speed_m_s: float = Field(9., ge=1, le=15)
    payload: Payload | None = None


class Discovery(StrictModel):
    families: list[Family] | Literal['all'] = 'all'
    budget_evals: int = Field(40, ge=8, le=120)
    seed: int = Field(42, ge=0, le=2147483647)
    top_k: int = Field(3, ge=1, le=5)

    @model_validator(mode='after')
    def unique_families(self):
        if isinstance(self.families, list) and (not self.families or len(set(self.families)) != len(self.families)):
            raise ValueError('Select at least one unique family')
        return self


class GliderMissionRequirements(StrictModel):
    objective: Literal['distance_m', 'duration_s'] = 'distance_m'
    mode: Literal['maximize'] = 'maximize'
    reference_target: float | None = Field(None, ge=0.001, le=1000000)
    minimum_performance: float | None = Field(None, ge=0.001, le=1000000,
        description='Optional hard acceptance requirement, evaluated at P10. Reference target is advisory.')
    launch: Launch
    environment: Environment = Field(default_factory=Environment)
    constraints: Constraints = Field(default_factory=Constraints)
    process: Process = 'auto'
    discovery: Discovery = Field(default_factory=Discovery)
