"""Family-specific rejection gates for injected development solver evidence.

No fixture or user-supplied report grants numerical/physical qualification.
Task A must supply independently qualified station/interference/mode evidence;
Task B must supply family-applicable construction/load-path evidence.
"""
from typing import Literal
from pydantic import Field
from cem.core import StrictModel


class SurfaceEvidence(StrictModel):
    lift_n: float = Field(gt=0, le=100)
    root_stall_alpha_deg: float = Field(ge=-5, le=30)
    tip_stall_alpha_deg: float = Field(ge=-5, le=30)
    effective_alpha_deg: float = Field(ge=-10, le=25)
    reynolds: float = Field(ge=10000, le=1000000)


class Interaction(StrictModel):
    # Signed induced-angle increments in geometry axes, degrees. Coverage must
    # include both ordered directions; a zero increment still needs evidence.
    induced_angle_deg: float = Field(ge=-20, le=20)
    method: str = Field(min_length=1, max_length=120)


class Evidence(StrictModel):
    aircraft_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    source: Literal['development_fixture']
    method_revision: str = Field(min_length=1, max_length=120)
    speed_m_s: float = Field(ge=1, le=15)
    alpha_deg: float = Field(ge=-10, le=20)
    cm: float = Field(ge=-2, le=2)
    cl_alpha_per_rad: float = Field(gt=0, le=20)
    cm_alpha_per_rad: float = Field(ge=-20, le=20)
    # Real parts in 1/s, not dimensional derivatives relabelled as eigenvalues.
    mode_real_parts_per_s: dict[str, float]
    control_deflections_deg: dict[str, float]
    surfaces: dict[str, SurfaceEvidence]
    interactions: dict[str, Interaction]


MODES = {'short_period', 'phugoid', 'dutch_roll', 'roll', 'spiral'}


def lifting_pair_failures(e, weight, front, rear, share_bounds):
    # Compare aircraft-alpha increments to stall, including the solver's local
    # effective angle, rather than comparing uncorrected section stall angles.
    stall_at = lambda s: min(s.root_stall_alpha_deg, s.tip_stall_alpha_deg)-s.effective_alpha_deg
    failures = []
    if stall_at(e.surfaces[rear])-stall_at(e.surfaces[front]) < 2.:
        failures.append('unsupported front-first stall sequence')
    fraction = e.surfaces[front].lift_n/weight
    if not share_bounds[0] <= fraction <= share_bounds[1]:
        failures.append('lift sharing outside family envelope')
    return failures


def assess(aircraft, evidence):
    """A passing outcome means fixture checks passed, never native admission."""
    e = evidence if isinstance(evidence, Evidence) else Evidence.model_validate(evidence)
    failures = []
    if e.aircraft_revision != aircraft.revision:
        raise ValueError('Stale aircraft/solver revision')
    names = {s.name for s in aircraft.surfaces}
    if set(e.surfaces) != names:
        failures.append('missing or unexpected lifting-surface evidence')
    if abs(e.cm) > .005:
        failures.append('untrimmable pitching moment')
    margin = -e.cm_alpha_per_rad/e.cl_alpha_per_rad
    # Whole-aircraft static margin, not a conventional tail-volume screen.
    from . import BUILDERS
    policy = BUILDERS[aircraft.family]
    lower, upper = policy.STATIC_MARGIN
    if not lower <= margin <= upper:
        failures.append('static stability outside family screening envelope')
    if set(e.mode_real_parts_per_s) != MODES or any(v >= -.0001 for v in e.mode_real_parts_per_s.values()):
        failures.append('missing or unstable dynamic mode')
    controls = {c.name: c for c in aircraft.controls}
    if set(e.control_deflections_deg) != set(controls) or any(
        abs(v) > controls[k].limit_deg for k, v in e.control_deflections_deg.items() if k in controls):
        failures.append('missing control solution or saturated control')
    weight = aircraft.mass_summary()['mass_kg']*9.80665
    if abs(sum(s.lift_n for s in e.surfaces.values())-weight) > .02*weight:
        failures.append('lift/weight closure failed')
    expected_pairs = {a+'->'+b for a in names for b in names if a != b}
    if set(e.interactions) != expected_pairs:
        failures.append('missing bidirectional interference evidence')
    for name, s in e.surfaces.items():
        if min(s.root_stall_alpha_deg, s.tip_stall_alpha_deg)-s.effective_alpha_deg < 2.:
            failures.append(name+': insufficient operating stall margin')
    if names == set(e.surfaces):
        failures.extend(policy.admission_failures(e, weight))
    import hashlib
    from pathlib import Path
    from cem.core import digest
    policy_revision = digest(dict(builder=aircraft.builder_revision,
        shared=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    return dict(state='rejected' if failures else 'fixture_passed', failures=failures,
                policy_revision=policy_revision,
                aircraft_revision=aircraft.revision, static_margin=margin,
                independent_numerical_performance=False, physical_construction=False,
                manufacturing_ready=False)
