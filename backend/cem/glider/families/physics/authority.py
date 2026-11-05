"""Control effectiveness and authority on the declared physical control surfaces.

Channels (``elevon_pitch``, ``elevon_roll``, ``aileron`` ...) are not physical
surfaces. ``ControlTopology`` maps every installed channel onto the physical
surface segments it moves, exactly as ``installed._states`` mixes them: a
symmetric channel moves both halves alike, an antisymmetric channel reverses on
the left (side -1). A segment's deflection is the sum of its active channels
and is limited by the segment's physical limit (and, when declared, by its
hardware's asymmetric travel). A channel's available travel therefore depends
on the other channels' current deflections.

Roles come from the declared topology only: symmetric channels are pitch
controls, antisymmetric channels roll controls. No family declares a
directional (yaw) channel, so a yaw requirement is *blocked by missing
directional hardware*; yaw produced by roll or pitch channels is reported as
cross-coupling and is never counted as directional authority.

``assess`` compares evaluated (never extrapolated) moment increments with an
explicit ``AuthorityRequirementSet``. Every body axis must be required by a
case or explicitly exempted by an artifact; otherwise that axis is blocked.
Rate and hinge-moment/actuator-torque checks need declared hardware and hinge
moment evidence; without them those checks block qualification.
"""
import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from cem.aero.polars import PolarCoverageError
from cem.core import StrictModel, canonical_json, digest
from . import frames
from .derivatives import _aero
from .interfaces import ControlHardware, HingeMomentEvidence
from .trim import UnsupportedContribution

AXES = ('L', 'M', 'N')
ROLE_AXIS = dict(roll='L', pitch='M', directional='N')
SHA = Field(pattern=r'^[a-f0-9]{64}$')


class Channel(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    role: Literal['pitch', 'roll', 'directional']
    limit_deg: float = Field(gt=0, le=45)


class Segment(StrictModel):
    surface: str = Field(min_length=1, max_length=80)
    side: Literal[-1, 1]
    eta: tuple[float, float]
    mixing: dict[str, float]
    limit_deg: tuple[float, float]
    hinge_chord_fractions: dict[str, float]

    @model_validator(mode='after')
    def physical(self):
        if not 0 <= self.eta[0] < self.eta[1] <= 1 or not self.limit_deg[0] < 0 < self.limit_deg[1]:
            raise ValueError('Invalid physical segment')
        if not self.mixing or any(v not in (-1., 1.) for v in self.mixing.values()):
            raise ValueError('Segment mixing coefficients must be +/-1 per active channel')
        if set(self.hinge_chord_fractions) != set(self.mixing) or not all(
                0 < v < 1 for v in self.hinge_chord_fractions.values()):
            raise ValueError('A hinge chord fraction is required for every active channel')
        return self

    @property
    def exclusive(self):
        """Overlapping channels on different hinge lines are separate flaps that
        the installed geometry cannot deflect together (installed._states)."""
        return len(set(self.hinge_chord_fractions.values())) > 1


class ControlTopology(StrictModel):
    revision_sha256: str = SHA
    channels: tuple[Channel, ...] = Field(min_length=1)
    segments: tuple[Segment, ...] = Field(min_length=1)
    hardware: Literal['undeclared', 'declared', 'measured'] = 'undeclared'
    source: str = Field(min_length=1, max_length=300)

    @model_validator(mode='after')
    def consistent(self):
        names = [c.name for c in self.channels]
        if len(set(names)) != len(names):
            raise ValueError('Duplicate control channel')
        used = {n for s in self.segments for n in s.mixing}
        if used != set(names):
            raise ValueError('Every channel must move a physical segment and only declared channels may')
        return self

    def channel(self, name):
        return next(c for c in self.channels if c.name == name)


def topology(aircraft, hardware=None):
    """Physical segments of an actual family aircraft (and optional hardware)."""
    channels = [Channel(name=c.name, role='pitch' if c.symmetric else 'roll', limit_deg=c.limit_deg)
                for c in aircraft.controls]
    items = None
    if hardware is not None:
        from .interfaces import validate_hardware
        items = {h.control: h for h in validate_hardware(aircraft, hardware)}
    segments = []
    for surface in aircraft.surfaces:
        cs = [c for c in aircraft.controls if c.surface == surface.name]
        cuts = sorted({0., 1., *(v for c in cs for v in (c.eta_start, c.eta_end))})
        for lo, hi in zip(cuts, cuts[1:]):
            active = [c for c in cs if c.eta_start < (lo+hi)/2 < c.eta_end]
            if not active:
                continue
            exclusive = len({c.hinge_chord_fraction for c in active}) > 1
            limit = min(c.limit_deg for c in active)
            travel = (-limit, limit)
            if items is not None:
                lims = {items[c.name].deflection_limits_deg for c in active}
                rates = {(items[c.name].actuator_rate_deg_s, items[c.name].linkage_ratio,
                          items[c.name].actuator_torque_limit_nm) for c in active}
                if not exclusive and (len(lims) != 1 or len(rates) != 1):
                    raise ValueError('Conflicting physical controls: channels sharing a surface declare '
                                     'different hardware travel or actuator')
                # Exclusive overlapping flaps: the intersection of their travels (conservative).
                travel = (max(travel[0], *(l[0] for l in lims)), min(travel[1], *(l[1] for l in lims)))
            for side in (-1, 1):
                segments.append(Segment(surface=surface.name, side=side, eta=(lo, hi),
                    mixing={c.name: 1. if c.symmetric else float(side) for c in active},
                    limit_deg=travel, hinge_chord_fractions={c.name: c.hinge_chord_fraction for c in active}))
    state = 'undeclared' if items is None else (
        'measured' if all(h.evidence == 'measured' for h in items.values()) else 'declared')
    return ControlTopology(revision_sha256=aircraft.revision, channels=tuple(channels), segments=tuple(segments),
                           hardware=state, source='family controls '+aircraft.builder_revision)


def violations(topo, controls, tolerance=1e-9):
    out = []
    for c in topo.channels:
        if abs(controls[c.name]) > c.limit_deg+tolerance:
            out.append(f'{c.name} beyond channel limit')
    for s in topo.segments:
        if s.exclusive and sum(abs(controls[n]) > tolerance for n in s.mixing) > 1:
            out.append(f'{s.surface} side {s.side}: overlapping flaps on different hinges deflected together')
        value = sum(k*controls[n] for n, k in s.mixing.items())
        if not s.limit_deg[0]-tolerance <= value <= s.limit_deg[1]+tolerance:
            out.append(f'{s.surface} side {s.side} eta {s.eta[0]:.3g}-{s.eta[1]:.3g} saturated ({value:.6g} deg)')
    return out


def channel_range(topo, controls, name):
    """Feasible travel of one channel with every other channel held."""
    limit = topo.channel(name).limit_deg
    lo, hi = -limit, limit
    for s in topo.segments:
        k = s.mixing.get(name)
        if k is None:
            continue
        if s.exclusive and any(controls[n] != 0 for n in s.mixing if n != name):
            lo, hi = max(lo, 0.), min(hi, 0.)  # locked while the other flap is deflected
            continue
        base = sum(c*controls[n] for n, c in s.mixing.items() if n != name)
        a, b = (s.limit_deg[0]-base)/k, (s.limit_deg[1]-base)/k
        lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
    return lo, hi


def segment_deflections(topo, controls):
    return [sum(k*controls[n] for n, k in s.mixing.items()) for s in topo.segments]


class AuthorityCase(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal['manoeuvre', 'disturbance']
    moments_nm: dict[Literal['L', 'M', 'N'], float] = Field(min_length=1)
    both_directions: bool = True
    onset_time_s: float | None = Field(None, gt=0)

    @model_validator(mode='after')
    def finite(self):
        if any(not (math.isfinite(v) and v != 0) for v in self.moments_nm.values()):
            raise ValueError('Required moments must be finite and nonzero')
        return self


class AuthorityRequirementSet(StrictModel):
    """Explicit manoeuvre/disturbance requirements and their derivation."""
    revision_sha256: str = SHA
    derivation: Literal['mission_manoeuvre', 'disturbance_rejection', 'mission_and_disturbance', 'analytic_test']
    artifact_sha256: str = SHA
    source: str = Field(min_length=1, max_length=300)
    cases: tuple[AuthorityCase, ...] = Field(min_length=1)
    reserve_deg: dict[str, float]
    exemptions: dict[Literal['L', 'M', 'N'], str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def declared(self):
        if any(v < 0 or not math.isfinite(v) for v in self.reserve_deg.values()):
            raise ValueError('Nonnegative finite reserves required')
        for axis, artifact in self.exemptions.items():
            if len(artifact) != 64 or any(ch not in '0123456789abcdef' for ch in artifact):
                raise ValueError('An axis exemption must cite an artifact digest')
            if any(axis in c.moments_nm for c in self.cases):
                raise ValueError('An axis cannot be both required and exempted')
        if len({c.name for c in self.cases}) != len(self.cases):
            raise ValueError('Duplicate authority case')
        return self


def _moment(model, state, *, required, revision, about):
    return frames.geometry_to_body(_aero(model, state, required=required, revision=revision, about=about)[1])


def assess(equilibrium, *, model, mass, required, topology, requirement=None, hardware=None, hinge_moments=None,
           density_kg_m3=None, jacobian_step_deg=.25, iterations=6, tolerance=1e-9):
    """Authority report for one established equilibrium of one revision."""
    topo = topology if isinstance(topology, ControlTopology) else ControlTopology.model_validate_json(
        canonical_json(topology))
    req = None if requirement is None else (requirement if isinstance(requirement, AuthorityRequirementSet)
        else AuthorityRequirementSet.model_validate_json(canonical_json(requirement)))
    base = dict(scope='control_authority_on_declared_physical_surfaces_at_one_equilibrium',
        topology=topo.model_dump(mode='json'), axes=frames.AXES['body'], qualified_control_authority=False,
        production_admission=False)
    if equilibrium.get('state') != 'trimmed':
        return dict(base, state='unsupported', reason='Authority requires an established equilibrium')
    if equilibrium['mass_properties_sha256'] != digest(mass):
        raise ValueError('Mass properties differ from the equilibrium revision')
    revision = equilibrium['revision_sha256']
    if req is not None and req.revision_sha256 != revision:
        raise ValueError('Authority requirement belongs to another revision')
    s0 = equilibrium['flight_state']
    trim_controls = {k: float(v) for k, v in s0['controls'].items()}
    if set(trim_controls) != {c.name for c in topo.channels}:
        raise ValueError('Topology channels differ from the equilibrium controls')
    trim_violations = violations(topo, trim_controls)
    if trim_violations:
        return dict(base, state='trim_saturated', violations=trim_violations)
    kw = dict(required=required, revision=revision, about=mass['cg_m'])
    evaluate = lambda controls: _moment(model, dict(s0, controls=controls), **kw)
    blockers, report = [], dict(base, trim_controls_deg=trim_controls)
    try:
        neutral = evaluate(trim_controls)
        channels = {}
        for c in topo.channels:
            lo, hi = channel_range(topo, trim_controls, c.name)
            at = {}
            for label, value in (('-', lo), ('+', hi)):
                state = dict(trim_controls, **{c.name: value})
                at[label] = dict(deflection_deg=value,
                                 moment_increment_body_nm=(evaluate(state)-neutral).tolist())
            channels[c.name] = dict(role=c.role, primary_axis=ROLE_AXIS[c.role], available_range_deg=[lo, hi],
                                    remaining_from_trim_deg=[lo-trim_controls[c.name], hi-trim_controls[c.name]],
                                    at_feasible_limit=at)
        # Effectiveness about trim (evaluated central differences, per degree).
        G = np.zeros((3, len(topo.channels)))
        for j, c in enumerate(topo.channels):
            lo, hi = channel_range(topo, trim_controls, c.name)
            h = min(jacobian_step_deg, hi-trim_controls[c.name], trim_controls[c.name]-lo)
            if h <= 1e-6:
                return dict(report, state='control_at_limit', control=c.name, channels=channels)
            plus, minus = (dict(trim_controls, **{c.name: trim_controls[c.name]+s*h}) for s in (1, -1))
            G[:, j] = (evaluate(plus)-evaluate(minus))/(2*h)
    except UnsupportedContribution as exc:
        return dict(report, state='unsupported', resultant=exc.record)
    except PolarCoverageError as exc:
        return dict(report, state='unsupported_out_of_coverage_at_limit', reason=str(exc)[:300])
    report.update(channels=channels, effectiveness_body_nm_per_deg=dict(
        order=[c.name for c in topo.channels], matrix=G.tolist()))
    roles = {c.role for c in topo.channels}
    if 'directional' not in roles:
        report['directional_control'] = 'absent_no_declared_yaw_channel'
    if req is None:
        return dict(report, state='unassessed_missing_requirement',
                    blockers=['authority requirement (AuthorityRequirementSet) absent'])
    # Trim reserve per channel: travel remaining in both directions.
    reserves = {}
    for c in topo.channels:
        need = req.reserve_deg.get(c.name)
        lo, hi = channels[c.name]['remaining_from_trim_deg']
        if need is None:
            reserves[c.name] = dict(state='unassessed_missing_requirement', remaining_deg=[lo, hi])
            blockers.append(f'reserve requirement for {c.name}')
        else:
            ok = -lo >= need and hi >= need
            reserves[c.name] = dict(state='met' if ok else 'insufficient_reserve', required_deg=need,
                                    remaining_deg=[lo, hi])
            if not ok:
                blockers.append(f'insufficient trim reserve: {c.name}')
    axes = {}
    for axis in AXES:
        if axis in req.exemptions:
            axes[axis] = dict(state='exempted_by_declared_requirement', artifact_sha256=req.exemptions[axis])
        elif not any(axis in case.moments_nm for case in req.cases):
            axes[axis] = dict(state='blocked_missing_requirement')
            blockers.append(f'{axis}: no requirement or exemption declared')
        else:
            role = next(r for r, a in ROLE_AXIS.items() if a == axis)
            if role not in roles:
                axes[axis] = dict(state='blocked_missing_directional_hardware' if axis == 'N'
                                  else f'blocked_missing_{role}_control')
                blockers.append(f'{axis}: no declared {role} control channel')
            else:
                axes[axis] = dict(state='assessed_in_cases')
    hardware_items = None if hardware is None else {
        h.control: h for h in (x if isinstance(x, ControlHardware) else
                               ControlHardware.model_validate_json(canonical_json(x)) for x in hardware)}
    hinges = None if hinge_moments is None else {
        h.control: h for h in (x if isinstance(x, HingeMomentEvidence) else
                               HingeMomentEvidence.model_validate_json(canonical_json(x)) for x in hinge_moments)}
    cases = []
    try:
        for case in req.cases:
            for sign in ((1, -1) if case.both_directions else (1,)):
                cases.append(_case(case, sign, topo, trim_controls, neutral, G, evaluate, roles, s0, axes,
                                   hardware_items, hinges, density_kg_m3, iterations, tolerance))
    except UnsupportedContribution as exc:
        return dict(report, state='unsupported', resultant=exc.record)
    except PolarCoverageError as exc:
        return dict(report, state='unsupported_out_of_coverage_at_limit', reason=str(exc)[:300])
    for c in cases:
        if c['state'] != 'met':
            blockers.append(f"{c['case']} ({c['direction']}): {c['state']}")
        for check in ('rate', 'hinge_torque'):
            if c[check]['state'] != 'met':
                blockers.append(f"{c['case']} ({c['direction']}) {check}: {c[check]['state']}")
    for axis, record in axes.items():
        if record['state'] == 'assessed_in_cases':
            relevant = [c for c in cases if axis in c['required_nm']]
            record['state'] = 'met' if all(c['state'] == 'met' for c in relevant) else 'insufficient_authority'
    failed = any(c['state'] in ('insufficient_authority', 'combined_surface_saturated') for c in cases) or \
        any(r['state'] == 'insufficient_reserve' for r in reserves.values())
    if failed:
        state = 'insufficient_authority'
    elif blockers:
        state = 'blocked'
    else:
        state = 'requirements_met'
    out = dict(report, state=state, requirement=req.model_dump(mode='json'), reserves=reserves, axes=axes,
               cases=cases, blockers=blockers)
    out['record_sha256'] = digest(out)
    return out


def _case(case, sign, topo, trim, neutral, G, evaluate, roles, s0, axes, hardware, hinges, rho, iterations,
          tolerance):
    target = {a: sign*v for a, v in case.moments_nm.items()}
    record = dict(case=case.name, kind=case.kind, direction='+' if sign > 0 else '-', required_nm=target)
    blocked = [a for a in target if axes[a]['state'].startswith('blocked')]
    if blocked:
        return dict(record, state='blocked', blocked_axes=blocked, rate=dict(state='not_evaluated'),
                    hinge_torque=dict(state='not_evaluated'))
    rows = [AXES.index(a) for a in target]
    primary = [j for j, c in enumerate(topo.channels) if ROLE_AXIS[c.role] in target]
    want = np.array([target[a] for a in target])
    x = np.zeros(len(topo.channels))
    achieved, feasible = np.zeros(3), True
    sub = G[np.ix_(rows, primary)]
    for _ in range(iterations):
        delta_needed = want-achieved[rows]
        step, *_ = np.linalg.lstsq(sub, delta_needed, rcond=None)
        x[primary] += step
        controls = {c.name: float(trim[c.name]+x[j]) for j, c in enumerate(topo.channels)}
        if violations(topo, controls):
            feasible = False
            break
        achieved = evaluate(controls)-neutral
        if all(sign_ok(achieved[r], w, tolerance) for r, w in zip(rows, want)):
            break
    controls = {c.name: float(trim[c.name]+x[j]) for j, c in enumerate(topo.channels)}
    record.update(deflections_deg=controls, segment_deflections_deg=segment_deflections(topo, controls))
    if not feasible:
        # Largest evaluated fraction of the demand inside every physical limit.
        lo, hi = 0., 1.
        for _ in range(40):
            mid = .5*(lo+hi)
            if violations(topo, {c.name: trim[c.name]+mid*x[j] for j, c in enumerate(topo.channels)}):
                hi = mid
            else:
                lo = mid
        partial = {c.name: float(trim[c.name]+lo*x[j]) for j, c in enumerate(topo.channels)}
        got = evaluate(partial)-neutral
        return dict(record, state='combined_surface_saturated', violations=violations(topo, controls),
            largest_feasible_fraction=lo, achieved_at_largest_feasible_nm=got.tolist(),
            rate=dict(state='not_evaluated'), hinge_torque=dict(state='not_evaluated'))
    ok = all(sign_ok(achieved[r], w, tolerance) for r, w in zip(rows, want))
    record.update(achieved_nm=achieved.tolist(),
        cross_coupling_nm={a: float(achieved[i]) for i, a in enumerate(AXES) if a not in target},
        state='met' if ok else 'insufficient_authority')
    # Actuator rate (surface deg/s) and hinge torque at the solved deflection.
    if hardware is None:
        record['rate'] = dict(state='blocked_missing_control_hardware')
        record['hinge_torque'] = dict(state='blocked_missing_control_hardware')
        return record
    if case.onset_time_s is None:
        record['rate'] = dict(state='unassessed_missing_onset_requirement')
    else:
        demand = []
        base = segment_deflections(topo, trim)
        for s, after, before in zip(topo.segments, record['segment_deflections_deg'], base):
            h = hardware[_segment_channel(s, record['deflections_deg'])]
            available = h.actuator_rate_deg_s/h.linkage_ratio
            demand.append(dict(surface=s.surface, side=s.side, required_deg_s=abs(after-before)/case.onset_time_s,
                               available_deg_s=available))
        ok = all(d['required_deg_s'] <= d['available_deg_s'] for d in demand)
        record['rate'] = dict(state='met' if ok else 'actuator_rate_insufficient', segments=demand)
    if hinges is None or rho is None or any(n not in hinges for n in (c.name for c in topo.channels)):
        record['hinge_torque'] = dict(state='blocked_missing_hinge_moment_evidence')
        return record
    torques = []
    for s, value in zip(topo.segments, record['segment_deflections_deg']):
        name = _segment_channel(s, record['deflections_deg'])
        moment = hinges[name].hinge_moment_nm(alpha_deg=s0['alpha_deg'], deflection_deg=value,
                                             speed_m_s=s0['speed_m_s'], density_kg_m3=rho)
        h = hardware[name]
        if moment is None:
            record['hinge_torque'] = dict(state='hinge_moment_outside_evidence_domain', surface=s.surface)
            return record
        torques.append(dict(surface=s.surface, side=s.side, hinge_moment_nm=moment,
                            actuator_torque_nm=abs(moment)/h.linkage_ratio, limit_nm=h.actuator_torque_limit_nm))
    ok = all(t['actuator_torque_nm'] <= t['limit_nm'] for t in torques)
    record['hinge_torque'] = dict(state='met' if ok else 'actuator_torque_insufficient', segments=torques)
    return record


def _segment_channel(segment, controls):
    """Channel whose hardware drives this segment (the deflected one if exclusive)."""
    moving = [n for n in segment.mixing if controls.get(n, 0.) != 0]
    return moving[0] if moving else next(iter(segment.mixing))


def sign_ok(achieved, want, tolerance):
    return achieved*math.copysign(1., want) >= abs(want)*(1-tolerance)
