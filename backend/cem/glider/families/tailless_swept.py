"""Single swept lifting wing with reflex and declared differential elevons."""
from typing import Literal
from pydantic import Field, model_validator
from cem.core import StrictModel
from .common import Wing, Inventory, Control, surface, make_aircraft

REVISION = 'tailless-swept-screening-v1'
STATIC_MARGIN = (.02, .15)


def admission_failures(evidence, weight):
    s = evidence.surfaces['main_wing']
    return (['elevon tip region stalls before required root-first margin']
            if s.tip_stall_alpha_deg-s.root_stall_alpha_deg < 2. else [])


class Parameters(StrictModel):
    family: Literal['tailless_swept'] = 'tailless_swept'
    wing: Wing = Field(default_factory=lambda: Wing(sweep_deg=25., airfoil='0012'))
    wing_x_m: float = Field(.12, ge=.02, le=.3)
    reflex_fraction: float = Field(.015, ge=0., le=.04)
    elevon_hinge: float = Field(.75, ge=.65, le=.85)
    inventory: Inventory = Field(default_factory=lambda: Inventory(equipment_x_m=.22))

    @model_validator(mode='after')
    def swept_envelope(self):
        if self.wing.sweep_deg < 15 or self.wing.airfoil[:2] != '00':
            raise ValueError('Swept prototype requires >=15 degree sweep and explicit symmetric-base reflex')
        return self


def build(params=None):
    p = params if isinstance(params, Parameters) else Parameters.model_validate(params or {})
    surfaces = [surface(p.wing, 'main_wing', p.wing_x_m)]
    controls = [Control('elevon_pitch', 'main_wing', .35, 1., p.elevon_hinge, True, 20.),
                Control('elevon_roll', 'main_wing', .35, 1., p.elevon_hinge, False, 20.)]
    return make_aircraft(p.family, REVISION, p, surfaces, controls, p.reflex_fraction)
