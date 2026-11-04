"""Bounded five-station chord search; no new airfoil or aircraft-family claim.

The root/tip chords and quarter-chord path retain the base wing controls.
Three independent interior chord factors generate genuinely different planforms.
All downstream solvers, mass strips and exported meshes use these same sections.
"""
from dataclasses import asdict, dataclass
import math

import numpy as np

from gliders.model.components import WingParams, Section, Surface

ETAS = (0., .275, .55, .775, 1.)
FACTOR_LIMITS = (.85, 1.15)


@dataclass
class ShapedWing(WingParams):
    chord_factors: tuple = (1., 1., 1., 1., 1.)

    def __post_init__(self):
        f = self.chord_factors
        if (not isinstance(f, (list, tuple)) or len(f) != len(ETAS)
                or any(type(x) not in (int, float) or not math.isfinite(x)
                       or not FACTOR_LIMITS[0] <= x <= FACTOR_LIMITS[1] for x in f)
                or f[0] != 1. or f[-1] != 1.):
            raise ValueError('Unsupported five-station wing shape')
        self.chord_factors = tuple(f)

    def chord_at(self, eta):
        # Interpolate actual station chords, so the analytical mass strips and
        # the piecewise-linear exported loft agree between stations too.
        chords = [super(ShapedWing, self).chord_at(e)*f for e, f in zip(ETAS, self.chord_factors)]
        return float(np.interp(eta, ETAS, chords))

    def surface(self):
        half = self.span/2
        sections = []
        for eta in ETAS:
            chord, y = self.chord_at(eta), eta*half
            z = half*math.tan(math.radians(self.dihedral_deg))
            z *= max(0., (eta-.55)/.45) if self.style == 'polyhedral' else eta
            sections.append(Section(.25*self.root_chord+y*math.tan(math.radians(self.sweep_qc_deg))-.25*chord,
                                    y, z, chord, self.incidence_deg-self.washout_deg*eta))
        return Surface('wing', sections, True, self.naca, 'wing')


def wing_from_parameters(value, mission=None):
    if 'chord_factors' in value and mission is not None and mission.discovery.wing_shape != 'multi_station':
        raise ValueError('Wing shape does not belong to the selected discovery mode')
    return (ShapedWing if 'chord_factors' in value else WingParams)(**value)


def sample_shape(wing, rng, parent=None, radius=.15):
    values = asdict(wing)
    values.pop('chord_factors', None)
    factors = [1.]
    for i in range(1, len(ETAS)-1):
        lo, hi = FACTOR_LIMITS
        if isinstance(parent, ShapedWing):
            lo, hi = max(lo, parent.chord_factors[i]-radius), min(hi, parent.chord_factors[i]+radius)
        factors.append(rng.uniform(lo, hi))
    return ShapedWing(**values, chord_factors=tuple(factors+[1.]))
