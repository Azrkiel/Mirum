"""Coordinate-based family development geometry, separate from mirum-aero-1.

Controls rotate the actual section coordinates, including reflex. Spanwise
control boundaries split wings rather than smearing a deflection across a bay.
This is rigid inviscid diagnostic geometry, not a hinge or construction model.
"""
from dataclasses import asdict
import hashlib
from importlib.metadata import version
from pathlib import Path

import aerosandbox as asb
import numpy as np

from cem.core import digest
from . import archive, reconstruct


def _states(aircraft, states):
    controls = {c.name: c for c in aircraft.controls}
    if set(states) != set(controls):
        raise ValueError('Explicit state required for every installed control')
    for name, value in states.items():
        if (type(value) not in (int, float) or not np.isfinite(value)
                or abs(value) > controls[name].limit_deg):
            raise ValueError('Nonfinite or out-of-range installed control')
    # Pitch and roll channels can act on the same physical elevon/aileron.
    for surface in aircraft.surfaces:
        cs = [c for c in controls.values() if c.surface == surface.name]
        cuts = sorted({0., 1., *(v for c in cs for v in (c.eta_start, c.eta_end))})
        for lo, hi in zip(cuts, cuts[1:]):
            active = [c for c in cs if c.eta_start < (lo+hi)/2 < c.eta_end]
            for side in (-1, 1):
                if active and abs(sum(states[c.name]*(1 if c.symmetric else side)
                                      for c in active)) > min(c.limit_deg for c in active):
                    raise ValueError('Combined physical control is saturated')
    return dict(states)


def airplane(record, control_deflections_deg):
    aircraft = reconstruct(record)
    states = _states(aircraft, control_deflections_deg)
    base = aircraft.airplane()
    wings = []
    for surface, wing in zip(aircraft.surfaces, base.wings):
        cs = [c for c in aircraft.controls if c.surface == surface.name]
        span = surface.sections[-1].y
        eta = np.array([s.y/span for s in surface.sections])
        cuts = sorted({*eta.tolist(), *(v for c in cs for v in (c.eta_start, c.eta_end))})
        for side in (-1, 1):
            for i, (lo, hi) in enumerate(zip(cuts, cuts[1:])):
                active = [c for c in cs if c.eta_start < (lo+hi)/2 < c.eta_end]
                hinges = {c.hinge_chord_fraction for c in active if states[c.name] != 0.}
                if len(hinges) > 1:
                    raise ValueError('Overlapping controls with different hinges unsupported')
                delta = sum(states[c.name]*(1 if c.symmetric else side) for c in active)
                foil = wing.xsecs[0].airfoil
                if delta:
                    foil = foil.add_control_surface(deflection=delta,
                        hinge_point_x=next(iter(hinges)), modify_polars=False)
                xsecs = []
                # Always orient sections from smaller to larger y, including
                # the physical left half. No implicit mirror of roll controls.
                for e in ((hi, lo) if side < 0 else (lo, hi)):
                    vals = {key: float(np.interp(e, eta, [getattr(s, key) for s in surface.sections]))
                            for key in ('x', 'y', 'z', 'chord', 'twist')}
                    xsecs.append(asb.WingXSec(xyz_le=[vals['x'], side*vals['y'], vals['z']],
                        chord=vals['chord'], twist=vals['twist'], airfoil=foil))
                wings.append(asb.Wing(name=f'{surface.name}_{side}_{i}', symmetric=False, xsecs=xsecs))
    return asb.Airplane(name=base.name, wings=wings, xyz_ref=base.xyz_ref,
                        s_ref=base.s_ref, b_ref=base.b_ref, c_ref=base.c_ref)


def freeze(record, control_deflections_deg):
    """Store exact coordinates and all controls; reconstruction is authoritative."""
    aircraft = reconstruct(record)
    states = _states(aircraft, control_deflections_deg)
    plane = airplane(record, states)
    payload = dict(contract='mirum-family-coordinates-1', geometry=archive(aircraft),
        control_deflections_deg=states, controls=[asdict(c) for c in aircraft.controls],
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        aerosandbox_version=version('aerosandbox'),
        reference=dict(area_m2=float(plane.s_ref), span_m=float(plane.b_ref),
                       chord_m=float(plane.c_ref), cg_m=aircraft.mass_summary()['cg_m']),
        wings=[dict(name=w.name, sections=[dict(leading_edge_m=np.asarray(s.xyz_le).tolist(),
            chord_m=float(s.chord), incidence_deg=float(s.twist),
            coordinates=np.asarray(s.airfoil.coordinates).tolist()) for s in w.xsecs]) for w in plane.wings],
        scope='rigid_lifting_oml_diagnostic_only', independent_numerical_performance=False,
        physical_construction=False, verified_gain=False,
        missing=['installed body OML', 'hinge/hardware mass and full inertia',
                 'directional control', 'stall and dynamics qualification', 'construction'])
    return dict(payload=payload, installed_sha256=digest(payload))


def validate(state):
    """Reject altered coordinates, controls, sources, flags or stale archives."""
    from cem.core import canonical_json
    if set(state) != {'payload', 'installed_sha256'}:
        raise ValueError('Malformed installed family state')
    p = state['payload']
    expected = freeze(p['geometry'], p['control_deflections_deg'])
    if canonical_json(state) != canonical_json(expected):
        raise ValueError('Installed family state differs from reconstruction')
    return airplane(p['geometry'], p['control_deflections_deg'])
