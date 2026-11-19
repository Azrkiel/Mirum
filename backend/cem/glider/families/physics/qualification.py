"""Qualification inputs for an actual family revision, recorded explicitly.

For one exact archive revision this inventories everything dynamics and
control qualification depends on: full mass/CG/inertia tensor and its evidence
class, the physical control topology, the qualified aerodynamic domain (from a
passed/replayed coordinate bank, if supplied), the force/moment ledger state at
a declared operating point, and each missing input in its required form. A
circulation root or lifting-only pitch closure is reported as such and is never
counted as a qualified equilibrium.
"""
from cem.core import digest
from . import assessment, authority, interfaces


def bank_domain(bank):
    if bank is None:
        return None
    out = {}
    for foil, group in bank.groups.items():
        rows = [row['alpha_deg'] for row in group[0]['rows']]
        out[foil] = dict(reynolds=[r['reynolds'] for r in group], native_alpha_deg=[rows[0], rows[-1]],
                         rows=len(rows))
    return dict(foils=out, beta_deg=[0., 0.], body_rates_rad_s='zero_only', controls='frozen_installed_state',
                interpolation='inside sampled native rows and Reynolds bracket only; no extrapolation')


def inventory(record, *, controls, operating_point, bank=None, bank_evidence=None, body_geometry=None,
              junctions=None, hardware=None, hinge_moments=None, measured_properties=None,
              authority_requirement=None, stability_requirement=None, unsteady=None, altitude_m=0., spanwise=4):
    """Return the explicit qualification-input record for one family revision."""
    report = assessment.assess(record, controls=controls, operating_point=operating_point, bank=bank,
        bank_evidence=bank_evidence, body_geometry=body_geometry, junctions=junctions, hardware=hardware,
        altitude_m=altitude_m, spanwise=spanwise)
    prepared = assessment.prepare(record, controls=controls, bank=bank, bank_evidence=bank_evidence,
        body_geometry=body_geometry, junctions=junctions, hardware=hardware, altitude_m=altitude_m,
        spanwise=spanwise)
    aircraft = prepared['aircraft']
    topo = authority.topology(aircraft, hardware)
    mass = prepared['mass']
    supplied = dict(section_bank=bank is not None and prepared['bank_class'] == 'numerical_model',
        body_oml=body_geometry is not None, junctions=bool(junctions) and set(junctions) == {
            s.name for s in aircraft.surfaces},
        control_hardware=hardware is not None, hinge_moments=hinge_moments is not None,
        rate_and_sideslip_aerodynamics=False,  # no supported family model at nonzero beta/p/q/r
        measured_mass_properties=measured_properties is not None,
        authority_requirement=authority_requirement is not None,
        stall_envelope=False, unsteady_derivatives=unsteady is not None,
        stability_requirement=stability_requirement is not None,
        directional_control_hardware=any(c.role == 'directional' for c in topo.channels))
    forms = dict(interfaces.REQUIRED_INPUTS,
        unsteady_derivatives=dict(form='dynamics.UnsteadyDerivatives bound to the revision (alpha-dot/beta-dot '
            'terms from a qualified unsteady method or measurement) with speed/alpha domain',
            unblocks=['short-period and Dutch-roll damping requirements']),
        stability_requirement=dict(form='stability.StabilityRequirement with short-period, phugoid, Dutch-roll, '
            'roll and spiral criteria and their derivation artifact', unblocks=['mode requirement assessment']),
        directional_control_hardware=dict(form='Declared yaw-control surface (geometry, hinge, limits, hardware) '
            'in the family builder and topology; none is declared or invented here',
            unblocks=['directional (N) authority', 'yaw disturbance rejection']))
    missing = {k: forms[k] for k, ok in supplied.items() if not ok}
    partial = report.get('partial_supported_diagnostic')
    lifting = next((c for c in report.get('contributions') or [] if c['name'] == 'lifting_surfaces'), None)
    out = dict(scope='dynamics_and_control_qualification_inputs', family=aircraft.family,
        aircraft_revision=aircraft.revision, builder_revision=aircraft.builder_revision,
        ledger_revision_sha256=prepared['revision'], controls_deg=controls,
        mass_properties=dict(state=mass['state'], mass_kg=mass['mass_kg'], cg_m=mass['cg_m'],
            inertia_cg_kg_m2=mass['inertia_cg_kg_m2'], inertia_body_axes_kg_m2=mass['inertia_body_axes_kg_m2'],
            products_of_inertia_kg_m2=mass['products_of_inertia_kg_m2'],
            omitted_self_inertia=mass['omitted_self_inertia'], measured_aircraft=False,
            mass_properties_sha256=mass['mass_properties_sha256']),
        control_topology=topo.model_dump(mode='json'),
        aerodynamic_domain=dict(section_bank_class=prepared['bank_class'], bank=bank_domain(bank),
            body=('A slender body inside documented scope' if body_geometry is not None else 'absent'),
            junctions=sorted(junctions or {}), stall='unsupported_attached_flow_only'),
        equilibrium=dict(state=report['state'], required_contributions=report['required_contributions'],
            unsupported=(report.get('resultant') or {}).get('unsupported'),
            lifting_only=None if lifting is None or lifting['state'] != 'supported' else dict(
                force_n=lifting['force_n'], moment_nm=lifting['moment_nm'],
                label='circulation_root_lifting_only_not_trim'),
            partial_supported=None if partial is None else dict(partial, label='diagnostic_not_equilibrium')),
        supplied=supplied, missing=missing, qualified_trim=False, qualified_dynamics=False,
        qualified_control_authority=False, production_admission=False, physical_construction=False,
        verified_gain=False)
    out['record_sha256'] = digest(out)
    return out
