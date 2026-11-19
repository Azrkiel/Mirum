"""Two substantial lifting wings with independently declared pitch controls."""
from typing import Literal
from pydantic import Field, model_validator
from cem.core import StrictModel
from .common import Wing, Inventory, Control, surface, make_aircraft

REVISION = 'tandem-screening-v1'
STATIC_MARGIN = (.03, .25)


def admission_failures(evidence, weight):
    from .admission import lifting_pair_failures
    return lifting_pair_failures(evidence, weight, 'front_wing', 'rear_wing', (.25, .75))


class Parameters(StrictModel):
    family: Literal['tandem'] = 'tandem'
    front_wing: Wing = Field(default_factory=lambda: Wing(span_m=.9, root_chord_m=.15, incidence_deg=3.))
    rear_wing: Wing = Field(default_factory=lambda: Wing(span_m=1., root_chord_m=.16))
    front_x_m: float = Field(.05, ge=.01, le=.15)
    rear_x_m: float = Field(.43, ge=.3, le=.5)
    vertical_separation_m: float = Field(.06, ge=-.1, le=.15)
    inventory: Inventory = Field(default_factory=Inventory)

    @model_validator(mode='after')
    def two_lifting_wings(self):
        if self.front_x_m+self.front_wing.root_chord_m >= self.rear_x_m:
            raise ValueError('Tandem wings must be separated')
        ratio = (self.front_wing.span_m*self.front_wing.root_chord_m*(1+self.front_wing.taper) /
                 (self.rear_wing.span_m*self.rear_wing.root_chord_m*(1+self.rear_wing.taper)))
        if not .5 <= ratio <= 2.:
            raise ValueError('Both tandem wings must have substantial lifting area')
        return self


def build(params=None):
    p = params if isinstance(params, Parameters) else Parameters.model_validate(params or {})
    surfaces = [surface(p.front_wing, 'front_wing', p.front_x_m),
                surface(p.rear_wing, 'rear_wing', p.rear_x_m, p.vertical_separation_m)]
    controls = [Control('front_elevator', 'front_wing', 0., 1., .7, True, 20.),
                Control('rear_elevator', 'rear_wing', 0., 1., .7, True, 20.),
                Control('aileron', 'rear_wing', .5, 1., .75, False, 20.)]
    return make_aircraft(p.family, REVISION, p, surfaces, controls)
