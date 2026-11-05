"""Revision-bound force/moment contributions and complete-ledger resultants.

Every contribution is expressed in geometry axes G (see ``frames``), in N and
N m, with its moment about an explicit reference point. A force may also carry
its line-of-action application point; the moment is then derived exactly.

A required contribution that is missing or ``unsupported`` makes the whole
resultant ``unsupported`` with ``None`` totals. It is never replaced by zero.
A partial sum of supported entries is available only under an explicit
``partial_supported_only`` label and must not be used as equilibrium evidence.
"""
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from cem.core import StrictModel, canonical_json, digest
from . import frames

Vector = tuple[float, float, float]
SHA256 = str

KINDS = ('aerodynamic_lifting', 'aerodynamic_body', 'aerodynamic_junction', 'aerodynamic_control',
         'control_hardware', 'gravity', 'propulsion', 'inertial', 'other')


class Contribution(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal[KINDS]
    state: Literal['supported', 'unsupported']
    revision_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    evidence: Literal['analytic_fixture', 'development_fixture', 'numerical_model',
                      'calculated_screening', 'measured', 'none'] = 'none'
    axes: Literal['geometry'] = 'geometry'
    units: Literal['N, N m, m'] = 'N, N m, m'
    force_n: Vector | None = None
    moment_nm: Vector | None = None
    reference_point_m: Vector | None = None
    application_point_m: Vector | None = None
    scope: str = Field('', max_length=400)
    reason: str | None = Field(None, max_length=600)

    @model_validator(mode='after')
    def completeness(self):
        if self.state == 'unsupported':
            if self.reason is None or any(v is not None for v in (
                    self.force_n, self.moment_nm, self.reference_point_m, self.application_point_m)):
                raise ValueError('Unsupported contribution requires a reason and no substituted values')
            if self.evidence != 'none':
                raise ValueError('Unsupported contribution cannot claim evidence')
        else:
            if self.force_n is None or self.moment_nm is None or self.reference_point_m is None:
                raise ValueError('Supported contribution requires force, moment and reference point')
            if self.evidence == 'none':
                raise ValueError('Supported contribution requires an evidence class')
            if self.reason is not None:
                raise ValueError('Supported contribution cannot carry an unsupported reason')
            if self.application_point_m is not None:
                expected = np.cross(np.subtract(self.application_point_m, self.reference_point_m),
                                    self.force_n)
                if not np.allclose(expected, self.moment_nm, rtol=1e-12, atol=1e-12):
                    raise ValueError('Moment differs from force applied at stated point')
        return self

    def moment_about(self, point_m):
        if self.state != 'supported':
            raise ValueError('Unsupported contribution has no moment')
        return frames.transfer_moment(self.moment_nm, self.force_n, self.reference_point_m, point_m)


def supported(name, kind, revision, force_n, moment_nm, reference_point_m, *, evidence, scope=''):
    return Contribution(name=name, kind=kind, state='supported', revision_sha256=revision,
        evidence=evidence, force_n=tuple(map(float, frames.vector(force_n))),
        moment_nm=tuple(map(float, frames.vector(moment_nm))),
        reference_point_m=tuple(map(float, frames.vector(reference_point_m))), scope=scope)


def applied(name, kind, revision, force_n, point_m, *, reference_point_m, couple_nm=(0., 0., 0.),
            evidence, scope=''):
    """Force at a line-of-action point plus an optional pure couple."""
    f, p, r = (frames.vector(v) for v in (force_n, point_m, reference_point_m))
    couple = frames.vector(couple_nm)
    moment = np.cross(p-r, f)+couple
    record = dict(name=name, kind=kind, state='supported', revision_sha256=revision, evidence=evidence,
        force_n=tuple(map(float, f)), moment_nm=tuple(map(float, moment)),
        reference_point_m=tuple(map(float, r)), scope=scope)
    if not couple.any():
        record['application_point_m'] = tuple(map(float, p))
    return Contribution(**record)


def unsupported(name, kind, revision, reason):
    return Contribution(name=name, kind=kind, state='unsupported', revision_sha256=revision, reason=reason)


def gravity(revision, mass_kg, cg_m, phi_deg, theta_deg, *, evidence, g_m_s2=frames.STANDARD_GRAVITY_M_S2):
    weight = frames.gravity_force_geometry(mass_kg, phi_deg, theta_deg, g_m_s2)
    return applied('gravity', 'gravity', revision, weight, cg_m, reference_point_m=cg_m, evidence=evidence,
                   scope='uniform gravity at the frozen mass-property CG')


def resultant(contributions, *, required, revision, about_m):
    """Complete sum about ``about_m`` or an explicit unsupported outcome."""
    items = [c if isinstance(c, Contribution) else Contribution.model_validate_json(canonical_json(c))
             for c in contributions]
    names = [c.name for c in items]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate force/moment contribution')
    required = tuple(required)
    if len(set(required)) != len(required):
        raise ValueError('Duplicate required contribution')
    unexpected = sorted(set(names)-set(required))
    if unexpected:
        raise ValueError('Undeclared contribution(s): '+', '.join(unexpected))
    if any(c.revision_sha256 != revision for c in items):
        raise ValueError('Contribution belongs to another revision')
    point = frames.vector(about_m)
    by_name = {c.name: c for c in items}
    missing = [n for n in required if n not in by_name]
    blocked = [dict(name=c.name, kind=c.kind, reason=c.reason) for c in items if c.state == 'unsupported']
    record = dict(revision_sha256=revision, axes=frames.AXES['geometry'], units=frames.AXES['units'],
        about_m=point.tolist(), required=list(required), missing=missing, unsupported=blocked,
        evidence=sorted({c.evidence for c in items if c.state == 'supported'}))
    if missing or blocked:
        record.update(state='unsupported', force_n=None, moment_nm=None)
    else:
        force = sum((np.asarray(by_name[n].force_n) for n in required), np.zeros(3))
        moment = sum((by_name[n].moment_about(point) for n in required), np.zeros(3))
        if not (np.isfinite(force).all() and np.isfinite(moment).all()):
            raise ValueError('Nonfinite resultant')
        record.update(state='complete', force_n=force.tolist(), moment_nm=moment.tolist())
    record['record_sha256'] = digest(record)
    return record


def partial_supported(contributions, *, about_m):
    """Diagnostic only: never equilibrium, trim or admission evidence."""
    point = frames.vector(about_m)
    good = [c for c in contributions if c.state == 'supported']
    force = sum((np.asarray(c.force_n) for c in good), np.zeros(3))
    moment = sum((c.moment_about(point) for c in good), np.zeros(3))
    return dict(state='partial_supported_only', equilibrium_evidence=False,
        included=[c.name for c in good], excluded=[c.name for c in contributions if c.state != 'supported'],
        about_m=point.tolist(), force_n=force.tolist(), moment_nm=moment.tolist())
