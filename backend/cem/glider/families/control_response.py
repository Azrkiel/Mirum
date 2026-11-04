"""Fixed-operating-point native control responses for development only.

Each declared channel is perturbed independently about neutral. Other channels,
inventory, CG, geometry, speed and alpha stay fixed. These inviscid differences
do not establish usable control authority, trim, stall margins or dynamics.
"""
import hashlib
import math
from pathlib import Path

import numpy as np
from pydantic import Field, model_validator

from cem.core import StrictModel, canonical_json, digest
from . import reconstruct
from .diagnostics import Conditions, _run, identity
from .installed import freeze, validate

SCOPE = 'fixed_operating_point_native_control_response'


class ResponseConditions(StrictModel):
    altitude_m: float = Field(0., ge=0, le=5000)
    alpha_deg: float = Field(4., ge=-10, le=15)
    speed_m_s: float = Field(6., ge=1, le=15)
    step_deg: float = Field(2., ge=.1, le=10)
    coarse_resolution: int = Field(3, ge=2, le=6)
    fine_resolution: int = Field(6, ge=3, le=9)

    @model_validator(mode='after')
    def refinement(self):
        if self.fine_resolution <= self.coarse_resolution:
            raise ValueError('Fine resolution must exceed coarse resolution')
        return self


def _difference(positive, negative, denominator):
    result = {}
    for key in positive:
        value = (np.asarray(positive[key]) - np.asarray(negative[key])) / denominator
        result[key] = value.tolist()
    if not all(np.isfinite(np.asarray(v)).all() for v in result.values()):
        raise ValueError('Nonfinite native control difference')
    return result


def sweep(record, conditions=None):
    """Evaluate neutral and +/- step for every channel at two mesh levels.

    No simultaneous overlapping channels or trim search is implied. All states
    are validated before solving, so saturation/unsupported hinges fail rather
    than producing a partial response report.
    """
    aircraft = reconstruct(record)
    c = (conditions if isinstance(conditions, ResponseConditions)
         else ResponseConditions.model_validate(conditions or {}))
    dc = Conditions(altitude_m=c.altitude_m, coarse_resolution=c.coarse_resolution,
                    fine_resolution=c.fine_resolution)
    neutral = {control.name: 0. for control in aircraft.controls}
    states = [neutral]
    for control in aircraft.controls:
        states.extend(dict(neutral, **{control.name: sign*c.step_deg}) for sign in (-1, 1))
    installed = [freeze(record, state) for state in states]
    planes = [validate(state) for state in installed]
    revision = digest(dict(diagnostic=identity(record, dc, installed),
        conditions=c.model_dump(mode='json'),
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    levels = []
    for resolution in (c.coarse_resolution, c.fine_resolution):
        samples = []
        for state, frozen, plane in zip(states, installed, planes):
            raw = _run(aircraft, dc, c.alpha_deg, c.speed_m_s, resolution, airplane=plane)
            if not all(np.isfinite(np.asarray(v)).all() for v in raw.values()):
                raise ValueError('Nonfinite native control output')
            samples.append(dict(controls=state, installed_sha256=frozen['installed_sha256'], raw=raw))
        channels = []
        for i, control in enumerate(aircraft.controls):
            negative, positive = samples[1+2*i:3+2*i]
            channels.append(dict(name=control.name, symmetric=control.symmetric,
                negative=negative, positive=positive,
                central_derivative_per_rad=_difference(positive['raw'], negative['raw'],
                                                      2*math.radians(c.step_deg))))
        levels.append(dict(resolution=resolution, neutral=samples[0], channels=channels))
    changes = [dict(name=lo['name'], derivative_change_per_rad=_difference(
        hi['central_derivative_per_rad'], lo['central_derivative_per_rad'], 1.))
        for lo, hi in zip(levels[0]['channels'], levels[1]['channels'])]
    report = dict(scope=SCOPE, state='responses_computed', geometry=record,
        conditions=c.model_dump(mode='json'), diagnostic_revision=revision,
        operating_point_scope='fixed_alpha_and_speed_no_equilibrium', equilibrium_solved=False,
        axes='geometry: x aft, y right, z up; Cm positive nose-up',
        units='forces N, moments N m, coefficients dimensionless; control derivatives per radian',
        control_sign='positive trailing-edge down; antisymmetric channels reverse on the left',
        reference=installed[0]['payload']['reference'], mass_summary=aircraft.mass_summary(),
        sample_count=len(states)*2, mesh_levels=levels, mesh_derivative_changes=changes,
        production_admission=False, qualified_control_authority=False,
        independent_numerical_performance=False, physical_construction=False, verified_gain=False,
        missing=['qualified control authority and required moments', 'roll/yaw equilibrium',
                 'nonlinear interaction and stall qualification', 'dynamic modes',
                 'body and hardware mass/inertia', 'construction and load paths'])
    # Copy nested input/state structures before returning a portable record.
    import json
    report = json.loads(canonical_json(report))
    return dict(report, report_sha256=digest(report))


def replay(report):
    """Recompute native results and their provenance; never grant admission."""
    if report.get('scope') != SCOPE:
        raise ValueError('Unknown native control response scope')
    expected = sweep(report['geometry'], report['conditions'])
    if canonical_json(report) != canonical_json(expected):
        raise ValueError('Native control response differs from reconstruction')
    return False


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--conditions', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output must be a new file')
    report = sweep(json.loads(args.archive.read_text()),
        json.loads(args.conditions.read_text()) if args.conditions else None)
    with args.output.open('x') as output:
        output.write(canonical_json(report))


if __name__ == '__main__':
    main()
