"""Distinct foreplane and main lifting wing; no conventional tail surrogate."""
from typing import Literal
from pydantic import Field, model_validator
from cem.core import StrictModel
from .common import Wing, Inventory, Control, surface, make_aircraft

REVISION = 'canard-screening-v1'
STATIC_MARGIN = (.03, .25)


def admission_failures(evidence, weight):
    from .admission import lifting_pair_failures
    return lifting_pair_failures(evidence, weight, 'foreplane', 'main_wing', (.05, .4))


class Parameters(StrictModel):
    family: Literal['canard'] = 'canard'
    foreplane: Wing = Field(default_factory=lambda: Wing(span_m=.45, root_chord_m=.10,
        taper=.8, incidence_deg=4., washout_deg=0.))
    main_wing: Wing = Field(default_factory=Wing)
    foreplane_x_m: float = Field(.04, ge=.01, le=.15)
    main_x_m: float = Field(.4, ge=.25, le=.5)
    vertical_separation_m: float = Field(.04, ge=-.1, le=.1)
    inventory: Inventory = Field(default_factory=lambda: Inventory(equipment_x_m=.35))

    @model_validator(mode='after')
    def foreplane_envelope(self):
        if self.foreplane_x_m+self.foreplane.root_chord_m >= self.main_x_m:
            raise ValueError('Foreplane must precede the main wing without overlap')
        if not .1 <= (self.foreplane.span_m*self.foreplane.root_chord_m*(1+self.foreplane.taper) /
                       (self.main_wing.span_m*self.main_wing.root_chord_m*(1+self.main_wing.taper))) <= .4:
            raise ValueError('Foreplane/main area ratio outside prototype envelope')
        return self


def build(params=None):
    p = params if isinstance(params, Parameters) else Parameters.model_validate(params or {})
    surfaces = [surface(p.foreplane, 'foreplane', p.foreplane_x_m, p.vertical_separation_m),
                surface(p.main_wing, 'main_wing', p.main_x_m)]
    controls = [Control('foreplane_elevator', 'foreplane', 0., 1., .7, True, 20.),
                Control('aileron', 'main_wing', .5, 1., .75, False, 20.)]
    return make_aircraft(p.family, REVISION, p, surfaces, controls)
