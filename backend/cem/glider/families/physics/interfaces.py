"""Body, junction, control-hardware and authority input contracts.

These define the exact revision-bound inputs that whole-aircraft equilibrium,
dynamics and authority need. They do not supply the evidence themselves.

- Body: Task A's slender-body model (``cem.aero.body``) is reused only inside
  its documented scope: slender circular sections, fineness 6..30, body Re
  1e5..3e6, |alpha| <= 8 deg, zero sideslip and zero rates. Outside that scope
  the contribution is unsupported. It is never extended to junction flow,
  shielding or separation.
- Junction: no model exists. A junction increment is supported only from
  supplied revision-bound evidence and only inside its sampled domain.
- Control hardware: hinge geometry, limits, actuator capability and hardware
  mass must be declared against the installed control; hinge moments have no
  aerodynamic model and remain unsupported.
- Authority: required moments must come from a declared mission/disturbance
  derivation; without one, authority is unassessed.
"""
import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from cem.aero.contracts import InstalledGeometry, OperatingPoint
from cem.core import StrictModel, canonical_json, digest
from . import frames, wrench
from .mass import MassComponent

Vector = tuple[float, float, float]
SHA = Field(pattern=r'^[a-f0-9]{64}$')


def body_contribution(geometry, state, *, revision, air):
    """A's slender-body increment as a ledger entry, or explicit unsupported."""
    from cem.aero.body import BodyApplicabilityError, increments
    name = 'body'
    if not isinstance(geometry, InstalledGeometry):
        return wrench.unsupported(name, 'aerodynamic_body', revision,
                                  'Missing revision-bound installed body OML (mirum-aero-1 stations)')
    if state['beta_deg'] != 0 or any(state[k] != 0 for k in ('p_rad_s', 'q_rad_s', 'r_rad_s')):
        return wrench.unsupported(name, 'aerodynamic_body', revision,
                                  'Slender-body model supports zero sideslip and zero rates only')
    try:
        op = OperatingPoint(velocity_m_s=float(state['speed_m_s']), alpha_deg=float(state['alpha_deg']),
            density_kg_m3=float(air['density_kg_m3']), viscosity_pa_s=float(air['viscosity_pa_s']))
        body = increments(geometry, op)
    except (BodyApplicabilityError, ValueError) as exc:
        return wrench.unsupported(name, 'aerodynamic_body', revision, 'Outside slender-body scope: '+str(exc)[:300])
    return wrench.supported(name, 'aerodynamic_body', revision, body.force_n, body.moment_cg_nm,
        geometry.reference.cg_m, evidence='numerical_model',
        scope='A slender-body friction/crossflow/Munk increment; no junction, shielding or separation')


class JunctionEvidence(StrictModel):
    """Required form of a revision-bound junction increment, as G-axes coefficients.

    Dimensional increments are q S_ref C_F and q S_ref c_ref C_M about the stated
    reference point. Applicability is the sampled alpha rows and speed range;
    interpolation is linear between adjacent sampled rows only and must be
    covered by the referenced interpolation audit.
    """
    junction_id: str = Field(min_length=1, max_length=80)
    surface: str = Field(min_length=1, max_length=80)
    geometry_revision_sha256: str = SHA
    method: str = Field(min_length=1, max_length=120)
    method_revision: str = Field(min_length=1, max_length=120)
    artifact_sha256: str = SHA
    evidence: Literal['numerical_model', 'measured', 'development_fixture']
    density_kg_m3: float = Field(gt=0)
    reference_area_m2: float = Field(gt=0)
    reference_chord_m: float = Field(gt=0)
    speed_range_m_s: tuple[float, float]
    alpha_deg: tuple[float, ...] = Field(min_length=2, max_length=81)
    force_coefficients: tuple[Vector, ...]
    moment_coefficients: tuple[Vector, ...]
    reference_point_m: Vector
    interpolation_audit_sha256: str = SHA

    @model_validator(mode='after')
    def table(self):
        n = len(self.alpha_deg)
        if len(self.force_coefficients) != n or len(self.moment_coefficients) != n:
            raise ValueError('Incomplete junction rows')
        if any(b <= a for a, b in zip(self.alpha_deg, self.alpha_deg[1:])):
            raise ValueError('Junction angles must strictly increase')
        if not 0 < self.speed_range_m_s[0] < self.speed_range_m_s[1]:
            raise ValueError('Invalid junction speed range')
        return self


def junction_contribution(name, evidence, state, *, revision, geometry_revision, density_kg_m3):
    if evidence is None:
        return wrench.unsupported(name, 'aerodynamic_junction', revision,
            'Missing junction evidence: JunctionEvidence table bound to this geometry revision')
    e = evidence if isinstance(evidence, JunctionEvidence) else JunctionEvidence.model_validate_json(
        canonical_json(evidence))
    if e.geometry_revision_sha256 != geometry_revision or e.surface not in name:
        raise ValueError('Junction evidence belongs to another geometry revision or junction')
    a, v = state['alpha_deg'], state['speed_m_s']
    if (state['beta_deg'] != 0 or any(state[k] != 0 for k in ('p_rad_s', 'q_rad_s', 'r_rad_s'))
            or not e.alpha_deg[0] <= a <= e.alpha_deg[-1]
            or not e.speed_range_m_s[0] <= v <= e.speed_range_m_s[1]
            or not math.isclose(density_kg_m3, e.density_kg_m3, rel_tol=1e-3)):
        return wrench.unsupported(name, 'aerodynamic_junction', revision,
                                  'State outside sampled junction evidence domain (no extrapolation)')
    q = .5*density_kg_m3*v*v
    cf = np.array([np.interp(a, e.alpha_deg, [f[i] for f in e.force_coefficients]) for i in range(3)])
    cm = np.array([np.interp(a, e.alpha_deg, [m[i] for m in e.moment_coefficients]) for i in range(3)])
    return wrench.supported(name, 'aerodynamic_junction', revision, q*e.reference_area_m2*cf,
        q*e.reference_area_m2*e.reference_chord_m*cm, e.reference_point_m, evidence=e.evidence,
        scope=f'{e.method} {e.method_revision}; sampled-domain junction increment')


class ControlHardware(StrictModel):
    """Required form for one installed control's physical hardware."""
    control: str = Field(min_length=1, max_length=80)
    hinge_inboard_m: Vector
    hinge_outboard_m: Vector
    deflection_limits_deg: tuple[float, float]
    actuator_torque_limit_nm: float = Field(gt=0)
    actuator_rate_deg_s: float = Field(gt=0)
    linkage_ratio: float = Field(gt=0)
    backlash_deg: float = Field(ge=0)
    exposed_hardware_drag: Literal['none_flush_internal', 'present_requires_evidence']
    mass: tuple[MassComponent, ...] = Field(min_length=1)
    evidence: Literal['declared', 'measured']
    artifact_sha256: str = SHA

    @model_validator(mode='after')
    def limits(self):
        lo, hi = self.deflection_limits_deg
        if not lo < 0 < hi:
            raise ValueError('Control limits must bracket neutral')
        return self


def validate_hardware(aircraft, hardware, *, tolerance_m=1e-3):
    """Check every declared control has hardware on its actual hinge line."""
    items = [h if isinstance(h, ControlHardware) else ControlHardware.model_validate_json(canonical_json(h))
             for h in hardware]
    controls = {c.name: c for c in aircraft.controls}
    if sorted(h.control for h in items) != sorted(controls):
        raise ValueError('Hardware required exactly once for every declared control')
    surfaces = {s.name: s for s in aircraft.surfaces}
    for h in items:
        c = controls[h.control]
        if max(abs(v) for v in h.deflection_limits_deg) > c.limit_deg:
            raise ValueError('Hardware limit exceeds the declared control limit')
        surf = surfaces[c.surface]
        span = surf.sections[-1].y
        eta = [s.y/span for s in surf.sections]
        for e, point in ((c.eta_start, h.hinge_inboard_m), (c.eta_end, h.hinge_outboard_m)):
            x, y, z, chord = (float(np.interp(e, eta, [getattr(s, k) for s in surf.sections]))
                              for k in ('x', 'y', 'z', 'chord'))
            expected = (x+c.hinge_chord_fraction*chord, y, z)
            if max(abs(a-b) for a, b in zip(point, expected)) > tolerance_m:
                raise ValueError('Hinge line differs from the installed control geometry')
    return items


class AuthorityRequirement(StrictModel):
    """Required body-axis control moments (N m) and their derivation."""
    derivation: Literal['mission_manoeuvre', 'disturbance_rejection', 'mission_and_disturbance']
    artifact_sha256: str = SHA
    moments_nm: dict[Literal['L', 'M', 'N'], float]
    reserve_deg: dict[str, float]

    @model_validator(mode='after')
    def positive(self):
        if not self.moments_nm or any(not (v > 0 and math.isfinite(v)) for v in self.moments_nm.values()):
            raise ValueError('Positive finite required moments')
        if any(v < 0 for v in self.reserve_deg.values()):
            raise ValueError('Nonnegative control reserves required')
        return self


REQUIRED_INPUTS = {
    'section_bank': dict(form='CoordinateBank of complete coordinate-audit reports that passed and independently '
        'replayed, covering every installed (including deflected) foil over the converged local Re/alpha domain',
        unblocks=['lifting-surface force/moment contribution', 'trim', 'derivatives']),
    'body_oml': dict(form='mirum-aero-1 InstalledGeometry body stations bound to the family geometry revision, '
        'circular slender sections inside A body-model scope, or a separately qualified body model',
        unblocks=['body force/moment contribution', 'whole-aircraft drag']),
    'junctions': dict(form='JunctionEvidence per lifting surface/body junction, revision-bound, with sampled '
        'alpha/speed domain and interpolation audit', unblocks=['junction increments', 'complete ledger']),
    'control_hardware': dict(form='ControlHardware per declared control: hinge line on installed geometry, limits, '
        'actuator torque/rate, linkage, backlash, hardware MassComponents, exposed-drag declaration',
        unblocks=['hardware mass/CG/inertia', 'control limits', 'hinge-moment requirement']),
    'hinge_moments': dict(form='Revision-bound hinge-moment coefficients or measurements per control',
        unblocks=['actuator torque adequacy']),
    'rate_and_sideslip_aerodynamics': dict(form='Supported lifting/body/junction contributions at nonzero '
        'beta, p, q, r within coverage', unblocks=['lateral equilibrium', 'damping derivatives', 'modes']),
    'measured_mass_properties': dict(form='MeasuredAircraftProperties for the delivered article revision',
        unblocks=['measured mass/CG/full inertia replacing calculated screening inventory']),
    'authority_requirement': dict(form='AuthorityRequirement (required L/M/N moments and reserves) with derivation',
        unblocks=['control saturation/reserve and authority assessment']),
    'stall_envelope': dict(form='Qualified nonlinear post-peak section/interaction evidence per family',
        unblocks=['stall sequencing (root-first, foreplane-first, front-first)']),
}


def required_inputs_digest():
    return digest(REQUIRED_INPUTS)
