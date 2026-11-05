"""Integrate actual family archives with the force/moment, trim and dynamics machinery.

Lifting loads come from ``coupled_aero.solve`` with a supplied CoordinateBank.
The bank's evidence class must be declared: ``development_fixture`` banks
exercise the machinery only; ``passed_and_replayed`` requires the digests of
the passed, replayed coordinate audits that produced every report. Body and
junction contributions are unsupported unless their revision-bound inputs are
supplied. Exposed control-hardware drag has no model. Any unsupported
required contribution yields an explicit unsupported outcome, never trim.
"""
import aerosandbox as asb

from cem.aero.numerics import NumericalConvergenceError
from cem.aero.polars import PolarCoverageError
from cem.core import canonical_json, digest
from .. import reconstruct
from .. import coupled_aero
from . import interfaces, mass as massprops, trim, wrench

SCOPE = 'family_aircraft_physics_assessment_not_qualification'


def _bank_class(bank, bank_evidence):
    if bank is None:
        return None
    if bank_evidence is None:
        raise ValueError('Declare the section-bank evidence class')
    state = bank_evidence.get('state')
    if state == 'development_fixture':
        return 'development_fixture'
    if state == 'passed_and_replayed':
        audits = bank_evidence.get('audit_evidence_sha256')
        reports = bank_evidence.get('report_sha256')
        if (not audits or not reports or sorted(reports) != sorted(digest(r) for r in bank.reports)
                or bank_evidence.get('replay') != 'matched'):
            raise ValueError('Passed bank requires matching report digests, audit digests and replay')
        return 'numerical_model'
    raise ValueError('Unknown section-bank evidence class')


def prepare(record, *, controls, bank=None, bank_evidence=None, body_geometry=None, junctions=None,
            hardware=None, altitude_m=0., spanwise=4):
    """Freeze aircraft, mass and evidence inputs; return the ledger model."""
    aircraft = reconstruct(record)
    if set(controls) != {c.name for c in aircraft.controls}:
        raise ValueError('Explicit state required for every declared control')
    components = massprops.family_screening_components(aircraft)
    hardware_items = interfaces.validate_hardware(aircraft, hardware) if hardware is not None else None
    if hardware_items:
        components += [m for h in hardware_items for m in h.mass]
    mass = massprops.combine(components)
    bank_class = _bank_class(bank, bank_evidence)
    if body_geometry is not None and body_geometry.family != aircraft.family:
        raise ValueError('Body geometry belongs to another family')
    air = asb.Atmosphere(altitude=altitude_m)
    air = dict(density_kg_m3=float(air.density()), viscosity_pa_s=float(air.dynamic_viscosity()))
    surfaces = sorted(s.name for s in aircraft.surfaces)
    junctions = dict(junctions or {})
    if set(junctions)-set(surfaces):
        raise ValueError('Junction evidence for an undeclared surface')
    revision = digest(dict(aircraft_revision=aircraft.revision, mass=mass['mass_properties_sha256'],
        bank=None if bank is None else digest(bank.reports), bank_class=bank_class,
        body=None if body_geometry is None else body_geometry.binding.model_dump(mode='json'),
        junctions={k: digest(v) for k, v in junctions.items()},
        hardware=None if hardware_items is None else [h.model_dump(mode='json') for h in hardware_items],
        altitude_m=altitude_m, spanwise=spanwise, required_inputs=interfaces.required_inputs_digest()))
    required = ['lifting_surfaces', 'body', *('junction:'+s for s in surfaces)]
    if hardware_items is None or any(h.exposed_hardware_drag != 'none_flush_internal' for h in hardware_items):
        required.append('control_hardware_drag')
    cg_screening = aircraft.mass_summary()['cg_m']

    def model(state):
        out = []
        if bank is None:
            out.append(wrench.unsupported('lifting_surfaces', 'aerodynamic_lifting', revision,
                'No complete passed/replayed coordinate section bank supplied'))
        elif state['beta_deg'] != 0 or any(state[k] != 0 for k in ('p_rad_s', 'q_rad_s', 'r_rad_s')):
            out.append(wrench.unsupported('lifting_surfaces', 'aerodynamic_lifting', revision,
                'Coupled lifting model represents zero sideslip and zero rates only'))
        elif state['controls'] != controls:
            raise PolarCoverageError('Control deflection outside the frozen installed coordinate state')
        else:
            try:
                report = coupled_aero.solve(record, controls, bank, speed_m_s=float(state['speed_m_s']),
                    alpha_deg=float(state['alpha_deg']), altitude_m=altitude_m, spanwise=spanwise)
            except NumericalConvergenceError as exc:
                # No covered circulation root at this state: a domain limit,
                # never a substituted lifting load.
                raise PolarCoverageError('No covered coupled lifting solution: '+str(exc)[:300]) from exc
            out.append(wrench.supported('lifting_surfaces', 'aerodynamic_lifting', revision, report['force_n'],
                report['moment_cg_nm'], cg_screening, evidence=bank_class,
                scope='coupled coordinate lifting line: attached flow, both halves, all members'))
        out.append(interfaces.body_contribution(body_geometry, state, revision=revision, air=air))
        for s in surfaces:
            out.append(interfaces.junction_contribution('junction:'+s, junctions.get(s), state,
                revision=revision, geometry_revision=aircraft.revision,
                density_kg_m3=air['density_kg_m3']))
        if 'control_hardware_drag' in required:
            out.append(wrench.unsupported('control_hardware_drag', 'control_hardware', revision,
                'Control hardware undeclared' if hardware_items is None
                else 'Exposed control-hardware drag has no supported model'))
        return out

    return dict(aircraft=aircraft, mass=mass, revision=revision, required=tuple(required), model=model,
                bank_class=bank_class, air=air, hardware=hardware_items)


def assess(record, *, controls, operating_point, trim_variables=None, authority=None, grid_points=5, **inputs):
    """Explicit evidence-state report; trim only when every contribution is supported."""
    prepared = prepare(record, controls=controls, **inputs)
    aircraft, mass, revision, model = (prepared[k] for k in ('aircraft', 'mass', 'revision', 'model'))
    state = dict(operating_point, p_rad_s=0., q_rad_s=0., r_rad_s=0., controls=dict(controls))
    ref_wing = aircraft.airplane()
    try:
        contributions = model(state)
    except PolarCoverageError as exc:
        contributions, coverage = None, str(exc)
    else:
        coverage = None
    report = dict(scope=SCOPE, family=aircraft.family, aircraft_revision=aircraft.revision,
        revision_sha256=revision, mass_properties=mass, section_bank_class=prepared['bank_class'],
        operating_point=operating_point, required_contributions=list(prepared['required']),
        required_inputs=interfaces.REQUIRED_INPUTS, production_admission=False, qualified_aircraft=False,
        qualified_aircraft_stall=False, qualified_control_authority=False, physical_construction=False,
        verified_gain=False, stall_sequencing='unsupported_attached_flow_only')
    if contributions is None:
        return dict(report, state='out_of_coverage', reason=coverage)
    partial = wrench.partial_supported(contributions, about_m=mass['cg_m'])
    ledger = wrench.resultant(contributions, required=prepared['required'], revision=revision, about_m=mass['cg_m'])
    report.update(contributions=[c.model_dump(mode='json') for c in contributions], resultant=ledger,
                  partial_supported_diagnostic=partial)
    if ledger['state'] != 'complete':
        return dict(report, state='unsupported', trim=None, dynamics=None,
            reason='Required force/moment contributions unsupported; no equilibrium attempted')
    variables = trim_variables or [
        dict(name='speed_m_s', lower=2., upper=15., initial=float(operating_point['speed_m_s'])),
        dict(name='alpha_deg', lower=-4., upper=10., initial=float(operating_point['alpha_deg'])),
        dict(name='pitch_deg', lower=-30., upper=30., initial=float(operating_point['pitch_deg']))]
    fixed = {k: float(v) for k, v in operating_point.items() if k not in {v['name'] for v in variables}}
    fixed.update({'control:'+k: float(v) for k, v in controls.items()})
    reserves = {} if authority is None else dict(authority.reserve_deg)
    problem = dict(revision_sha256=revision, required=list(prepared['required']), variables=variables,
        equations=['Fz', 'Fx', 'My'][:len(variables)], fixed=fixed,
        controls=[dict(name=c.name, limit_deg=c.limit_deg, reserve_deg=reserves.get(c.name))
                  for c in aircraft.controls],
        reference_chord_m=float(ref_wing.c_ref), reference_span_m=float(ref_wing.b_ref), grid_points=grid_points)
    result = trim.solve(problem, model, mass)
    return dict(report, state='trim_'+result['state'], trim=result)


def summary(report):
    """Compact portable view; never upgrades evidence state."""
    keep = ('scope', 'family', 'aircraft_revision', 'revision_sha256', 'state', 'section_bank_class',
            'required_contributions', 'production_admission', 'qualified_aircraft', 'stall_sequencing')
    out = {k: report[k] for k in keep if k in report}
    out['unsupported'] = (report.get('resultant') or {}).get('unsupported')
    out['mass_state'] = report['mass_properties']['state']
    out['mass_kg'] = report['mass_properties']['mass_kg']
    out['products_of_inertia_kg_m2'] = report['mass_properties']['products_of_inertia_kg_m2']
    out['partial_lifting_only'] = report.get('partial_supported_diagnostic')
    return __import__('json').loads(canonical_json(out))



