"""Mode requirements: explicit, sourced criteria; nothing passes by default.

``StabilityRequirement`` carries the criterion for each of the five classical
modes. A criterion left ``None`` is *unassessed* and blocks qualification.
``mission_limits_requirement`` reproduces the existing mission stability limits
(Dutch-roll minimum damping and spiral minimum time to double from
``gliders.model.config``); it supplies no short-period, phugoid or roll
criterion because none is declared there, so those remain unassessed.

The existing family admission rule (all five modes identified with real part
< -1e-4 1/s, ``admission.MODES``) is evaluated separately and unchanged.
Modes from a linearization without supported unsteady terms cannot meet a
requirement. Degenerate, coupled or ambiguous eigenstructures are reported as
not identified rather than assigned.
"""
import hashlib
import math
from pathlib import Path
from typing import Literal

from pydantic import Field

from cem.core import StrictModel, canonical_json, digest

MODES = ('short_period', 'phugoid', 'dutch_roll', 'roll', 'spiral')
FAMILY_POLICY_REAL_LIMIT = -1e-4  # admission.assess: any mode real part >= -1e-4 rejects


class StabilityRequirement(StrictModel):
    derivation: Literal['mission_specification', 'handling_qualities_specification', 'analytic_test']
    artifact_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source: str = Field(min_length=1, max_length=300)
    short_period_min_damping: float | None = Field(None, gt=0, lt=2)
    phugoid_min_damping: float | None = Field(None, ge=-1, lt=2)
    dutch_roll_min_damping: float | None = Field(None, gt=0, lt=2)
    roll_max_time_constant_s: float | None = Field(None, gt=0)
    spiral_min_time_to_double_s: float | None = Field(None, gt=0)


def mission_limits_requirement():
    """The existing mission lateral limits, bound to their source bytes."""
    from gliders.model import config
    path = Path(config.__file__)
    limits = config.CONFIG['limits']
    return StabilityRequirement(derivation='mission_specification',
        artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source='gliders.model.config limits: dutch_roll_min_damping, spiral_min_time_to_double_s',
        dutch_roll_min_damping=float(limits['dutch_roll_min_damping']),
        spiral_min_time_to_double_s=float(limits['spiral_min_time_to_double_s']))


def family_policy_failures(modes):
    """The unchanged admission-policy mode rule: identified and real < -1e-4."""
    by = {}
    for m in modes:
        by.setdefault(m['mode'], []).append(m)
    failures = []
    for name in MODES:
        found = by.get(name, [])
        if len(found) != 1:
            failures.append(f'{name}: not identified')
        elif found[0]['real_per_s'] >= FAMILY_POLICY_REAL_LIMIT:
            failures.append(f'{name}: real part {found[0]["real_per_s"]:.6g} >= {FAMILY_POLICY_REAL_LIMIT}')
    return failures


def assess(linearization, requirement):
    """Per-mode outcome and an overall state; never a favourable default."""
    if linearization.get('state') != 'computed':
        return dict(state='unsupported', reason='No computed linearization', modes={},
                    linearization_state=linearization.get('state'))
    req = None if requirement is None else (requirement if isinstance(requirement, StabilityRequirement)
        else StabilityRequirement.model_validate_json(canonical_json(requirement)))
    by = {}
    for m in linearization['modes']:
        by.setdefault(m['mode'], []).append(m)
    out, failures, unassessed = {}, [], []
    for name in MODES:
        found = by.get(name, [])
        if len(found) != 1:
            out[name] = dict(state='not_identified')
            failures.append(name)
            continue
        m = found[0]
        record = dict(real_per_s=m['real_per_s'], imag_rad_s=m['imag_rad_s'], damping_ratio=m['damping_ratio'],
                      natural_frequency_rad_s=m['natural_frequency_rad_s'], neutral=m.get('neutral', False))
        if name == 'spiral':
            criterion = None if req is None else req.spiral_min_time_to_double_s
            if m['real_per_s'] > 0:
                record['time_to_double_s'] = math.log(2)/m['real_per_s']
            if criterion is None:
                state = 'unassessed_missing_requirement'
            elif m['real_per_s'] <= 0:
                state = 'met'
            else:
                state = 'met' if record['time_to_double_s'] >= criterion else 'divergence_too_fast'
        elif name == 'roll':
            criterion = None if req is None else req.roll_max_time_constant_s
            if m['real_per_s'] >= 0:
                state = 'unstable'
            else:
                record['time_constant_s'] = -1/m['real_per_s']
                state = ('unassessed_missing_requirement' if criterion is None else
                         'met' if record['time_constant_s'] <= criterion else 'too_slow')
        else:
            criterion = None if req is None else getattr(req, name+'_min_damping')
            if m['real_per_s'] >= 0 and name != 'phugoid':
                state = 'unstable'
            elif criterion is None:
                state = 'unassessed_missing_requirement'
            else:
                state = 'met' if m['damping_ratio'] >= criterion else 'insufficient_damping'
        record.update(state=state, criterion=criterion)
        out[name] = record
        if state == 'unassessed_missing_requirement':
            unassessed.append(name)
        elif state != 'met':
            failures.append(name)
    policy = family_policy_failures(linearization['modes'])
    unsteady = linearization.get('unsteady_aerodynamics')
    if failures or policy:
        state = 'rejected'
    elif unsteady != 'included':
        state = 'blocked_missing_unsteady_terms'
    elif req is None or unassessed:
        state = 'blocked_missing_requirement'
    else:
        state = 'requirements_met'
    record = dict(scope='linear_mode_requirements_about_one_equilibrium', state=state, modes=out,
        failed=failures, unassessed=unassessed, family_policy_failures=policy, unsteady_aerodynamics=unsteady,
        requirement=None if req is None else req.model_dump(mode='json'),
        linearization_sha256=digest(linearization), qualified_aircraft=False)
    record['record_sha256'] = digest(record)
    return record
