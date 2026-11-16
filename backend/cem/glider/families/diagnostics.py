"""Native fixed-hardware inviscid diagnostics, never family qualification.

Uses AeroSandbox VLM with actual camber/reflex and all installed lifting wings.
No profile/body drag, control authority, stall, dynamic modes or construction
evidence is supplied. L=W is an aerodynamic screening equilibrium, not a glide
trajectory. No result from this module is admissible as Evidence in admission.py.
"""
import hashlib
import inspect
from importlib.metadata import version
import math
from pathlib import Path

import aerosandbox as asb
import numpy as np
from scipy.optimize import brentq

from cem.core import StrictModel, digest
from pydantic import Field, model_validator
from . import archive, reconstruct


class Conditions(StrictModel):
    altitude_m: float = Field(0., ge=0, le=5000)
    alpha_min_deg: float = Field(-5., ge=-10, le=0)
    alpha_max_deg: float = Field(12., ge=1, le=15)
    minimum_speed_m_s: float = Field(1., ge=1, le=5)
    maximum_speed_m_s: float = Field(15., ge=6, le=15)
    coarse_resolution: int = Field(3, ge=2, le=6)
    fine_resolution: int = Field(6, ge=3, le=9)

    @model_validator(mode='after')
    def refinement(self):
        if self.fine_resolution <= self.coarse_resolution:
            raise ValueError('Fine resolution must exceed coarse resolution')
        return self


def identity(record, conditions, installed=None):
    sources = [Path(__file__), Path(inspect.getfile(asb.VortexLatticeMethod))]
    return digest(dict(geometry=record, installed=installed, conditions=conditions.model_dump(mode='json'),
        sources={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        runtime={n: version(n) for n in ('aerosandbox', 'numpy', 'scipy')}))


def _run(aircraft, conditions, alpha, speed, resolution, derivatives=False, *, airplane=None):
    solver = asb.VortexLatticeMethod(aircraft.airplane() if airplane is None else airplane,
        asb.OperatingPoint(atmosphere=asb.Atmosphere(altitude=conditions.altitude_m),
                           velocity=speed, alpha=alpha),
        spanwise_resolution=resolution, chordwise_resolution=resolution)
    raw = (solver.run_with_stability_derivatives(alpha=True, beta=False, p=False, q=False, r=False)
           if derivatives else solver.run())
    keys = ('L', 'D', 'CL', 'CD', 'Cm', 'CLa', 'Cma') if derivatives else ('L', 'D', 'CL', 'CD', 'Cm')
    result = {k: float(raw[k]) for k in keys}
    result.update(force_geometry_n=np.asarray(raw['F_g']).tolist(),
                  moment_geometry_nm=np.asarray(raw['M_g']).tolist())
    if not np.isfinite(np.concatenate([np.asarray(v).ravel() for v in result.values()])).all():
        raise ValueError('Nonfinite native VLM output')
    return result


def diagnose(record, conditions=None, *, control_deflections_deg=None):
    aircraft = reconstruct(record)
    c = conditions if isinstance(conditions, Conditions) else Conditions.model_validate(conditions or {})
    installed = None
    ap = aircraft.airplane()
    if control_deflections_deg is not None:
        from .installed import freeze, validate
        installed = freeze(record, control_deflections_deg)
        ap = validate(installed)
    def run(alpha, speed, resolution, derivatives=False):
        return _run(aircraft, c, alpha, speed, resolution, derivatives, airplane=ap)
    report = dict(aircraft_revision=aircraft.revision, diagnostic_revision=identity(record, c, installed),
        installed=installed,
        geometry=archive(aircraft), conditions=c.model_dump(mode='json'),
        solver='AeroSandbox VLM', scope='fixed_hardware_inviscid_diagnostic',
        equilibrium_scope='lift_weight_and_pitch_only',
        reference=dict(area_m2=float(ap.s_ref), chord_m=float(ap.c_ref), span_m=float(ap.b_ref),
                       cg_m=aircraft.mass_summary()['cg_m']),
        axes='geometry: x aft, y right, z up; Cm positive nose-up; alpha derivatives per radian',
        independent_numerical_performance=False, physical_construction=False, verified_gain=False,
        missing=['profile and body drag', 'nonlinear interaction and stall qualification',
                 'qualified control authority', 'roll/yaw equilibrium and directional control',
                 'dynamic modes', 'construction and load paths'],
        mesh_levels=[])
    for resolution in (c.coarse_resolution, c.fine_resolution):
        level = dict(resolution=resolution)
        report['mesh_levels'].append(level)
        samples = [(a, run(a, 6., resolution))
                   for a in np.linspace(c.alpha_min_deg, c.alpha_max_deg, 9)]
        level['moment_samples'] = [dict(alpha_deg=float(a), **raw) for a, raw in samples]
        brackets = [(lo[0], hi[0]) for lo, hi in zip(samples, samples[1:])
                    if lo[1]['Cm']*hi[1]['Cm'] <= 0]
        if len(brackets) != 1:
            level['state'] = 'untrimmable' if not brackets else 'ambiguous_equilibrium'
            continue
        alpha = brentq(lambda a: run(a, 6., resolution)['Cm'], *brackets[0], xtol=1e-7)
        raw = run(alpha, 6., resolution)
        if raw['L'] <= 0:
            level['state'] = 'nonpositive_lift'
            continue
        weight = aircraft.mass_summary()['mass_kg']*9.80665
        speed = 6.*math.sqrt(weight/raw['L'])
        if not c.minimum_speed_m_s <= speed <= c.maximum_speed_m_s:
            level.update(state='speed_outside_envelope', alpha_deg=alpha, speed_m_s=speed)
            continue
        raw = run(alpha, speed, resolution, derivatives=True)
        margin = -raw['Cma']/raw['CLa'] if raw['CLa'] > 0 else None
        from . import BUILDERS
        lo, hi = BUILDERS[aircraft.family].STATIC_MARGIN
        state = ('inviscid_equilibrium' if margin is not None and lo <= margin <= hi
                 else 'static_stability_rejected')
        if abs(raw['Cm']) > .00001 or abs(raw['L']/weight-1) > .0001:
            state = 'equilibrium_residual_rejected'
        level.update(state=state, alpha_deg=alpha, speed_m_s=speed, static_margin=margin, raw=raw)
    levels = report['mesh_levels']
    report['state'] = 'inconclusive'
    if all(l['state'] == 'inviscid_equilibrium' for l in levels):
        # An engineering qualification would need a larger convergence study and
        # calibrated errors. These are explicit diagnostic sensitivity limits.
        changes = dict(alpha_deg=abs(levels[1]['alpha_deg']-levels[0]['alpha_deg']),
            relative_speed=abs(levels[1]['speed_m_s']/levels[0]['speed_m_s']-1),
            static_margin=abs(levels[1]['static_margin']-levels[0]['static_margin']))
        report['mesh_changes'] = changes
        report['state'] = ('diagnostic_equilibrium' if changes['alpha_deg'] <= .5 and
            changes['relative_speed'] <= .05 and changes['static_margin'] <= .02
            else 'mesh_sensitive')
    return report


if __name__ == '__main__':
    import argparse
    import json
    from cem.core import canonical_json
    from . import build
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('family', choices=('tailless_swept', 'canard', 'tandem'))
    parser.add_argument('--archive', type=Path, help='Exact geometry archive; family must match')
    parser.add_argument('--conditions', type=Path, help='Validated Conditions JSON, including mesh resolutions')
    parser.add_argument('--controls', type=Path, help='Explicit installed deflection for every control, JSON degrees')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    record = json.loads(args.archive.read_text()) if args.archive else archive(build(args.family))
    if record['family'] != args.family:
        parser.error('Archive belongs to another family')
    conditions = json.loads(args.conditions.read_text()) if args.conditions else None
    controls = json.loads(args.controls.read_text()) if args.controls else None
    result = diagnose(record, conditions, control_deflections_deg=controls)
    with args.output.open('x') as f:
        f.write(canonical_json(result))
    print(result['state'])
