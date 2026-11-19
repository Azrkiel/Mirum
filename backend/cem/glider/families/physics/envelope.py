"""Declared operating-envelope qualification with provenance and independent replay.

An ``EnvelopeDeclaration`` fixes, *before* any evaluation, the configurations
(each a frozen aircraft revision with its mass/CG/inertia and hardware), the
speeds (boundaries included), trim variable bounds, sideslip/bank, Reynolds
limits, atmosphere, steady load factor, trim control, stability and authority
requirements, unsteady terms and derivative/linearization settings. Its digest
is part of every report. Nothing is narrowed after the fact: every declared
point is evaluated and reported, including rejected and blocked ones.

At each point: bounded six-residual trim, full-inertia linearization, mode
requirements, step-studied derivatives and control authority on the physical
surfaces. A point is ``qualified_point`` only when all of these pass. The
envelope is ``numerically_qualified`` only when every point qualifies *and* the
evidence is admissible: aerodynamic contributions are ``numerical_model`` or
``measured`` (never fixtures), mass properties include declared or measured
hardware (never the screening inventory alone) and control hardware is declared.
Fixture envelopes can at most reach ``fixture_machinery_only``.

``physical_validation`` is always false here: it needs flight/bench evidence.
No production, physical-construction or verified-gain gate is opened.
"""
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from cem.core import StrictModel, canonical_json, digest
from . import authority, derivatives, dynamics, stability, trim

QUALIFYING_AERO = {'numerical_model', 'measured'}
QUALIFYING_MASS = {'declared_or_mixed_not_measured', 'measured_delivered_components'}
FLAGS = ('production_admission', 'physical_construction', 'verified_gain', 'physical_validation',
         'qualified_aircraft_stall')
SOURCES = ('authority.py', 'derivatives.py', 'dynamics.py', 'envelope.py', 'frames.py', 'interfaces.py',
           'mass.py', 'stability.py', 'trim.py', 'wrench.py', 'assessment.py', 'qualification.py',
           'fixtures.py', '../coupled_aero.py', '../installed.py', '../coordinate_polars.py',
           '../../../aero/numerics.py', '../../../aero/polars.py', '../../../aero/body.py')


class Configuration(StrictModel):
    name: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')
    revision_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    mass_properties_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    description: str = Field(min_length=1, max_length=300)


class EnvelopeDeclaration(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    family: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=400)
    configurations: tuple[Configuration, ...] = Field(min_length=1, max_length=8)
    speeds_m_s: tuple[float, ...] = Field(min_length=2, max_length=12)
    alpha_bounds_deg: tuple[float, float]
    pitch_bounds_deg: tuple[float, float] = (-30., 30.)
    beta_deg: Literal[0.] = 0.
    bank_deg: Literal[0.] = 0.
    reynolds_bounds: tuple[float, float]
    altitude_m: float = Field(ge=0, le=5000)
    load_factor: Literal[1.] = 1.
    trim_control: str | None = None
    fixed_controls_deg: dict[str, float]
    stability_requirement: stability.StabilityRequirement | None = None
    authority_requirements: dict[str, authority.AuthorityRequirementSet] = Field(default_factory=dict)
    unsteady: dict[str, dynamics.UnsteadyCoefficients] = Field(default_factory=dict)
    derivative_settings: derivatives.Settings = Field(default_factory=derivatives.Settings)
    linearization: dict[str, float] = Field(default_factory=lambda: dict(relative_step=1e-3, control_step_deg=.5))

    @model_validator(mode='after')
    def declared(self):
        if any(b <= a for a, b in zip(self.speeds_m_s, self.speeds_m_s[1:])) or self.speeds_m_s[0] <= 0:
            raise ValueError('Speeds must be positive and strictly increasing (boundaries included)')
        if not self.alpha_bounds_deg[0] < self.alpha_bounds_deg[1] or \
                not self.pitch_bounds_deg[0] < self.pitch_bounds_deg[1]:
            raise ValueError('Invalid trim bounds')
        if not 0 < self.reynolds_bounds[0] < self.reynolds_bounds[1]:
            raise ValueError('Invalid Reynolds bounds')
        names = [c.name for c in self.configurations]
        if len(set(names)) != len(names):
            raise ValueError('Duplicate configuration')
        for mapping in (self.authority_requirements, self.unsteady):
            if set(mapping)-set(names):
                raise ValueError('Requirement or unsteady terms for an undeclared configuration')
        for name, req in self.authority_requirements.items():
            config = next(c for c in self.configurations if c.name == name)
            if req.revision_sha256 != config.revision_sha256:
                raise ValueError('Authority requirement bound to another revision')
        for name, terms in self.unsteady.items():
            config = next(c for c in self.configurations if c.name == name)
            if terms.revision_sha256 != config.revision_sha256:
                raise ValueError('Unsteady derivatives bound to another revision')
        if self.trim_control is not None and self.trim_control in self.fixed_controls_deg:
            raise ValueError('Trim control cannot also be fixed')
        if set(self.linearization) - {'relative_step', 'control_step_deg'}:
            raise ValueError('Unknown linearization setting')
        return self


def implementation_identity():
    root = Path(__file__).resolve().parent
    files = {name: hashlib.sha256((root/name).resolve().read_bytes()).hexdigest() for name in SOURCES}
    return dict(files=files, implementation_sha256=digest(files))


def runtime_identity():
    return dict(python=platform.python_version(),
                packages={name: version(name) for name in ('numpy', 'scipy', 'pydantic', 'aerosandbox')})


def _declaration(value):
    return value if isinstance(value, EnvelopeDeclaration) else EnvelopeDeclaration.model_validate_json(
        canonical_json(value))


def _point(decl, config, prepared, speed):
    topo = prepared['topology']
    channels = [c.name for c in topo.channels]
    variables = [dict(name='alpha_deg', lower=decl.alpha_bounds_deg[0], upper=decl.alpha_bounds_deg[1],
                      initial=.5*sum(decl.alpha_bounds_deg)),
                 dict(name='pitch_deg', lower=decl.pitch_bounds_deg[0], upper=decl.pitch_bounds_deg[1],
                      initial=.5*sum(decl.pitch_bounds_deg)+1e-3)]
    equations = ['Fz', 'Fx']
    fixed = dict(speed_m_s=speed, beta_deg=decl.beta_deg, bank_deg=decl.bank_deg)
    if decl.trim_control is not None:
        limit = topo.channel(decl.trim_control).limit_deg
        variables.append(dict(name='control:'+decl.trim_control, lower=-limit, upper=limit, initial=1e-3))
        equations.append('My')
    if set(decl.fixed_controls_deg) | ({decl.trim_control} - {None}) != set(channels):
        raise ValueError('Every installed control must be fixed or be the declared trim control')
    fixed.update({'control:'+k: float(v) for k, v in decl.fixed_controls_deg.items()})
    reserves = decl.authority_requirements.get(config.name)
    problem = dict(revision_sha256=config.revision_sha256, required=list(prepared['required']),
        variables=variables, equations=equations, fixed=fixed, gravity_m_s2=prepared['gravity_m_s2'],
        controls=[dict(name=c.name, limit_deg=c.limit_deg, reserve_deg=None if reserves is None
                       else reserves.reserve_deg.get(c.name)) for c in topo.channels],
        reference_chord_m=prepared['reference']['chord_m'], reference_span_m=prepared['reference']['span_m'])
    model, mass, required = prepared['model'], prepared['mass'], prepared['required']
    result = dict(configuration=config.name, speed_m_s=speed)
    equilibrium = trim.solve(problem, model, mass)
    result['trim'] = {k: equilibrium.get(k) for k in ('state', 'solution', 'residual', 'unsolved_residuals',
        'flight_path_angle_deg', 'controls', 'saturated_controls', 'diagnosis', 'reason', 'sign_definite_equations')
        if equilibrium.get(k) is not None}
    if equilibrium.get('resultant'):
        result['trim']['unsupported'] = equilibrium['resultant'].get('unsupported')
        result['trim']['missing'] = equilibrium['resultant'].get('missing')
    if equilibrium['state'] != 'trimmed':
        blocked = equilibrium['state'] in ('unsupported', 'out_of_coverage', 'no_equilibrium_established')
        return dict(result, verdict='blocked' if blocked else 'rejected', reasons=['trim: '+equilibrium['state']])
    strips = prepared.get('reynolds')
    if strips is not None:
        lo, hi = strips(equilibrium)
        result['reynolds_range'] = [lo, hi]
        if lo < decl.reynolds_bounds[0] or hi > decl.reynolds_bounds[1]:
            return dict(result, verdict='rejected', reasons=['Reynolds outside declared envelope'])
    limits = [dict(name=c.name, limit_deg=c.limit_deg) for c in topo.channels]
    coefficients = decl.unsteady.get(config.name)
    unsteady = None if coefficients is None else coefficients.at(equilibrium, prepared['reference'])
    lin = dynamics.linearize(equilibrium, model=model, mass=mass, required=required, control_limits=limits,
        reference_span_m=prepared['reference']['span_m'], reference_chord_m=prepared['reference']['chord_m'],
        unsteady=unsteady, **decl.linearization)
    if coefficients is not None and unsteady is None and lin['state'] == 'computed':
        lin['unsteady_aerodynamics'] = 'unsupported_outside_unsteady_domain'
    result['linearization'] = {k: lin.get(k) for k in ('state', 'A', 'B', 'control_order', 'modes',
        'unsteady_aerodynamics', 'decoupled', 'longitudinal_lateral_coupling', 'step_convergence_excess',
        'reason', 'excess') if lin.get(k) is not None}
    if lin['state'] == 'unsupported' and lin.get('resultant'):
        result['linearization']['unsupported'] = lin['resultant'].get('unsupported')
    modes = stability.assess(lin, decl.stability_requirement)
    result['stability'] = {k: modes[k] for k in ('state', 'modes', 'failed', 'unassessed',
                                                 'family_policy_failures', 'unsteady_aerodynamics')}
    deriv = derivatives.compute(equilibrium, model=model, mass=mass, required=required,
        reference=prepared['reference'], settings=decl.derivative_settings,
        control_limits={c.name: c.limit_deg for c in topo.channels},
        feasible=lambda controls: not authority.violations(topo, controls))
    result['derivatives'] = dict(state=deriv['state'], missing_or_failed=deriv['missing_or_failed'],
        values={k: v['value'] for k, v in deriv['derivatives'].items()},
        states={k: v['state'] for k, v in deriv['derivatives'].items()})
    auth = authority.assess(equilibrium, model=model, mass=mass, required=required, topology=topo,
        requirement=decl.authority_requirements.get(config.name), hardware=prepared.get('hardware'),
        hinge_moments=prepared.get('hinge_moments'), density_kg_m3=prepared['reference']['density_kg_m3'])
    result['authority'] = {k: auth.get(k) for k in ('state', 'axes', 'reserves', 'cases', 'blockers',
        'channels', 'directional_control', 'reason', 'violations') if auth.get(k) is not None}
    reasons, blocked = [], []
    if lin['state'] != 'computed':
        (blocked if lin['state'] in ('unsupported', 'out_of_coverage') else reasons).append(
            'linearization: '+lin['state'])
    if modes['state'] == 'rejected':
        reasons.append('stability: '+', '.join(modes['failed']+modes['family_policy_failures']))
    elif modes['state'] != 'requirements_met':
        blocked.append('stability: '+modes['state'])
    if deriv['state'] != 'complete':
        bad = [k for k in deriv['missing_or_failed'] if deriv['derivatives'][k]['state'] == 'not_converged']
        (reasons if bad else blocked).append('derivatives: '+', '.join(deriv['missing_or_failed']))
    if auth['state'] in ('insufficient_authority', 'trim_saturated'):
        reasons.append('authority: '+auth['state'])
    elif auth['state'] != 'requirements_met':
        blocked.append('authority: '+auth['state'])
    evidence = sorted({c['evidence'] for c in equilibrium['contributions'] if c['state'] == 'supported'
                       and c['kind'] != 'gravity'})
    result['evidence'] = evidence
    verdict = 'rejected' if reasons else 'blocked' if blocked else 'qualified_point'
    return dict(result, verdict=verdict, reasons=reasons+blocked)


def run(declaration, providers, *, declared_file=None):
    """Evaluate every declared point. ``providers`` maps configuration name to a
    callable returning the frozen inputs (model, mass, required, topology,
    reference, gravity, bindings) for exactly that revision."""
    decl = _declaration(declaration)
    record = decl.model_dump(mode='json')
    declaration_sha256 = digest(record)
    file_binding = None
    if declared_file is not None:
        text = Path(declared_file).read_text()
        if digest(json.loads(text)) != declaration_sha256:
            raise ValueError('Declaration file differs from the evaluated declaration')
        file_binding = dict(path=str(declared_file), sha256=hashlib.sha256(text.encode()).hexdigest())
    if set(providers) != {c.name for c in decl.configurations}:
        raise ValueError('A provider is required for exactly every declared configuration')
    bindings, points = {}, []
    for config in decl.configurations:
        prepared = providers[config.name]()
        if prepared['revision'] != config.revision_sha256:
            raise ValueError(f'Configuration {config.name} revision differs from the declaration (stale or altered)')
        if prepared['mass']['mass_properties_sha256'] != config.mass_properties_sha256 or \
                digest({k: v for k, v in prepared['mass'].items() if k != 'mass_properties_sha256'}) != \
                prepared['mass']['mass_properties_sha256']:
            raise ValueError(f'Configuration {config.name} mass properties differ from the declaration')
        dynamics_mass = prepared['mass']
        bindings[config.name] = dict(prepared['bindings'], revision_sha256=config.revision_sha256,
            mass_properties_sha256=dynamics_mass['mass_properties_sha256'], mass_state=dynamics_mass['state'],
            inertia_body_axes_kg_m2=dynamics_mass['inertia_body_axes_kg_m2'],
            topology_sha256=digest(prepared['topology'].model_dump(mode='json')),
            hardware=prepared['topology'].hardware, reference=prepared['reference'],
            gravity_m_s2=prepared['gravity_m_s2'], required_contributions=list(prepared['required']))
        for speed in decl.speeds_m_s:
            points.append(_point(decl, config, prepared, float(speed)))
    verdicts = [p['verdict'] for p in points]
    aero = sorted({e for p in points for e in p.get('evidence', [])})
    admissible = (bool(aero) and set(aero) <= QUALIFYING_AERO
                  and all(b['mass_state'] in QUALIFYING_MASS and b['hardware'] != 'undeclared'
                          for b in bindings.values()))
    if all(v == 'qualified_point' for v in verdicts):
        state = 'numerically_qualified' if admissible else 'fixture_machinery_only'
    elif any(v == 'rejected' for v in verdicts):
        state = 'rejected_points_present'
    else:
        state = 'blocked'
    blockers = sorted({r for p in points for r in p['reasons']})
    report = dict(scope='declared_envelope_trim_stability_authority', declaration=record,
        declaration_sha256=declaration_sha256, declared_file=file_binding, bindings=bindings, points=points,
        summary=dict(points=len(points), qualified=verdicts.count('qualified_point'),
                     rejected=verdicts.count('rejected'), blocked=verdicts.count('blocked')),
        aerodynamic_evidence=aero, evidence_admissible=admissible, state=state, blockers=blockers,
        numerically_qualified=state == 'numerically_qualified',
        implementation=implementation_identity(), runtime=runtime_identity(),
        limitation='Linear dynamics about steady 1 g trims inside the declared attached-flow domain; no stall, '
                   'nonlinear manoeuvre, aeroelastic, gust or actuator-dynamics qualification.',
        **{flag: False for flag in FLAGS})
    report = json.loads(canonical_json(report))
    report['report_sha256'] = digest(report)
    return report


def replay(report, providers):
    """Recompute the whole envelope; reject altered, stale, incomplete or promoted evidence."""
    required = ('scope', 'declaration', 'declaration_sha256', 'bindings', 'points', 'summary', 'state',
                'implementation', 'runtime', 'report_sha256', *FLAGS)
    missing = [k for k in required if k not in report]
    if missing:
        raise ValueError('Incomplete envelope report: '+', '.join(missing))
    if any(report[f] is not False for f in FLAGS):
        raise ValueError('Promoted envelope report: production/physical/verified flags must be false')
    body = {k: v for k, v in report.items() if k != 'report_sha256'}
    if digest(body) != report['report_sha256']:
        raise ValueError('Envelope report digest mismatch (altered evidence)')
    if digest(report['declaration']) != report['declaration_sha256']:
        raise ValueError('Envelope declaration digest mismatch')
    if report['implementation'] != implementation_identity():
        raise ValueError('Stale envelope evidence: implementation identity differs')
    expected = run(report['declaration'], providers)
    expected['declared_file'] = report.get('declared_file')
    expected = json.loads(canonical_json(expected))
    expected.pop('report_sha256')
    expected['report_sha256'] = digest(expected)
    if canonical_json(expected) != canonical_json(report):
        raise ValueError('Envelope evidence differs from independent recomputation')
    return False
