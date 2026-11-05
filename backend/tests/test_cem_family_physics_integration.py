"""Body/junction/hardware interfaces and actual-family integration.

Development fixture banks and fixture junction tables exercise the plumbing
only. Every actual family remains unsupported for aircraft equilibrium.
"""
import json

import numpy as np
import pytest
from pydantic import ValidationError

from cem.aero.body import increments
from cem.aero.contracts import BodyStation, OperatingPoint
from cem.core import digest
from cem.glider.families import archive, build
from cem.glider.families.contracts import FamilyAdapter
from cem.glider.families.physics import assessment, interfaces, mass, wrench
from backend.tests.test_cem_family_coupled_aero import fixture_bank

OP = dict(speed_m_s=6., alpha_deg=4., beta_deg=0., pitch_deg=0., bank_deg=0.)
REV = digest('integration')
AIR = dict(density_kg_m3=1.225, viscosity_pa_s=1.789e-5)


def _state(**kw):
    state = dict(OP, p_rad_s=0., q_rad_s=0., r_rad_s=0., controls={})
    state.update(kw)
    return state


def _body_geometry(aircraft):
    L = aircraft.parameters['inventory']['body_length_m']
    body = tuple(BodyStation(x_m=x, width_m=w, height_m=w, exponent=2.) for x, w in ((0., .01), (.15, .05), (L, .02)))
    controls = {c.name: 0. for c in aircraft.controls}
    return FamilyAdapter(aircraft.family).prepare(aircraft.parameters, dict(body=body,
        control_deflections_deg=controls), mission={'m': 1}, process={'p': 1}, calibration={'c': 1})


def _hinges(aircraft, shift=0.):
    out = []
    for c in aircraft.controls:
        s = next(x for x in aircraft.surfaces if x.name == c.surface)
        eta = [q.y/s.sections[-1].y for q in s.sections]
        points = []
        for e in (c.eta_start, c.eta_end):
            x, y, z, ch = (float(np.interp(e, eta, [getattr(q, k) for q in s.sections])) for k in ('x', 'y', 'z', 'chord'))
            points.append((x+c.hinge_chord_fraction*ch+shift, y, z))
        out.append(dict(control=c.name, hinge_inboard_m=points[0], hinge_outboard_m=points[1],
            deflection_limits_deg=(-c.limit_deg, c.limit_deg), actuator_torque_limit_nm=.02,
            actuator_rate_deg_s=300., linkage_ratio=1., backlash_deg=.5,
            exposed_hardware_drag='none_flush_internal',
            mass=[mass.point_mass('servo_'+c.name, .009, (points[0][0]-.02, 0., 0.), evidence='declared_hardware',
                                  source='fixture declaration').model_dump(mode='json')],
            evidence='declared', artifact_sha256='1'*64))
    return out


def _junctions(aircraft, rows=((0., 0., 0.), (0., 0., 0.))):
    return {s.name: dict(junction_id='j_'+s.name, surface=s.name, geometry_revision_sha256=aircraft.revision,
        method='fixture', method_revision='zero-v1', artifact_sha256='0'*64, evidence='development_fixture',
        density_kg_m3=1.225, reference_area_m2=.1, reference_chord_m=.1, speed_range_m_s=(2., 15.),
        alpha_deg=(-5., 10.), force_coefficients=rows, moment_coefficients=rows, reference_point_m=(0., 0., 0.),
        interpolation_audit_sha256='0'*64) for s in aircraft.surfaces}


def test_body_interface_reuses_a_model_only_inside_documented_scope():
    a = build('canard')
    geometry = _body_geometry(a)
    contribution = interfaces.body_contribution(geometry, _state(), revision=REV, air=AIR)
    expected = increments(geometry, OperatingPoint(velocity_m_s=6., alpha_deg=4., density_kg_m3=1.225,
                                                   viscosity_pa_s=1.789e-5))
    assert contribution.state == 'supported' and contribution.evidence == 'numerical_model'
    assert contribution.force_n == pytest.approx(expected.force_n)
    assert contribution.moment_nm == pytest.approx(expected.moment_cg_nm)
    assert contribution.reference_point_m == geometry.reference.cg_m
    for state, reason in ((_state(), 'Missing revision-bound'), (_state(beta_deg=2.), 'zero sideslip'),
                          (_state(q_rad_s=.1), 'zero sideslip'), (dict(_state(), alpha_deg=10.), 'scope')):
        target = None if reason == 'Missing revision-bound' else geometry
        out = interfaces.body_contribution(target, state, revision=REV, air=AIR)
        assert out.state == 'unsupported' and reason in out.reason and out.force_n is None


def test_junction_interface_requires_bound_evidence_and_never_extrapolates():
    a = build('tandem')
    j = _junctions(a, rows=((0., 0., .1), (0., 0., .4)))['front_wing']
    name = 'junction:front_wing'
    missing = interfaces.junction_contribution(name, None, _state(), revision=REV, geometry_revision=a.revision,
                                               density_kg_m3=1.225)
    assert missing.state == 'unsupported' and 'JunctionEvidence' in missing.reason
    inside = interfaces.junction_contribution(name, j, _state(), revision=REV, geometry_revision=a.revision,
                                              density_kg_m3=1.225)
    q = .5*1.225*36
    assert inside.force_n == pytest.approx((0., 0., q*.1*(.1+.3*9/15)))
    for state in (dict(_state(), alpha_deg=11.), dict(_state(), speed_m_s=20.), _state(beta_deg=1.)):
        out = interfaces.junction_contribution(name, j, state, revision=REV, geometry_revision=a.revision,
                                               density_kg_m3=1.225)
        assert out.state == 'unsupported' and 'no extrapolation' in out.reason
    assert interfaces.junction_contribution(name, j, _state(), revision=REV, geometry_revision=a.revision,
                                            density_kg_m3=1.0).state == 'unsupported'
    with pytest.raises(ValueError, match='another geometry revision'):
        interfaces.junction_contribution(name, j, _state(), revision=REV, geometry_revision=digest('x'),
                                         density_kg_m3=1.225)
    with pytest.raises(ValidationError, match='strictly increase'):
        interfaces.JunctionEvidence.model_validate_json(json.dumps(dict(j, alpha_deg=(5., -5.))))


@pytest.mark.parametrize('family', ['tailless_swept', 'canard', 'tandem'])
def test_control_hardware_must_lie_on_installed_hinges(family):
    a = build(family)
    items = interfaces.validate_hardware(a, _hinges(a))
    assert sorted(h.control for h in items) == sorted(c.name for c in a.controls)
    with pytest.raises(ValueError, match='Hinge line'):
        interfaces.validate_hardware(a, _hinges(a, shift=.002))
    with pytest.raises(ValueError, match='exactly once'):
        interfaces.validate_hardware(a, _hinges(a)[1:])
    over = _hinges(a)
    over[0]['deflection_limits_deg'] = (-30., 30.)
    with pytest.raises(ValueError, match='exceeds'):
        interfaces.validate_hardware(a, over)


def test_authority_requirement_form():
    ok = interfaces.AuthorityRequirement(derivation='mission_and_disturbance', artifact_sha256='2'*64,
                                         moments_nm={'L': .1}, reserve_deg={'elevon_pitch': 3.})
    assert ok.moments_nm['L'] == .1
    with pytest.raises(ValidationError):
        interfaces.AuthorityRequirement(derivation='guess', artifact_sha256='2'*64, moments_nm={'L': .1},
                                        reserve_deg={})
    with pytest.raises(ValidationError, match='Positive finite'):
        interfaces.AuthorityRequirement(derivation='mission_manoeuvre', artifact_sha256='2'*64,
                                        moments_nm={'M': 0.}, reserve_deg={})
    assert set(interfaces.REQUIRED_INPUTS) >= {'section_bank', 'body_oml', 'junctions', 'control_hardware',
        'hinge_moments', 'rate_and_sideslip_aerodynamics', 'measured_mass_properties', 'authority_requirement',
        'stall_envelope'}


@pytest.mark.parametrize('family', ['tailless_swept', 'canard', 'tandem'])
def test_actual_families_remain_unsupported_without_body_junction_hardware(family):
    a = build(family)
    record = archive(a)
    controls = {c.name: 0. for c in a.controls}
    none = assessment.assess(record, controls=controls, operating_point=OP)
    assert none['state'] == 'unsupported' and none['trim'] is None
    names = {u['name'] for u in none['resultant']['unsupported']}
    assert names == {'lifting_surfaces', 'body', 'control_hardware_drag',
                     *('junction:'+s.name for s in a.surfaces)}
    bank = fixture_bank(record, controls)
    lifted = assessment.assess(record, controls=controls, operating_point=OP, bank=bank,
                               bank_evidence=dict(state='development_fixture'), spanwise=2)
    assert lifted['state'] == 'unsupported' and lifted['resultant']['force_n'] is None
    lifting = next(c for c in lifted['contributions'] if c['name'] == 'lifting_surfaces')
    assert lifting['state'] == 'supported' and lifting['evidence'] == 'development_fixture'
    partial = lifted['partial_supported_diagnostic']
    assert partial['equilibrium_evidence'] is False and partial['included'] == ['lifting_surfaces']
    for flag in ('production_admission', 'qualified_aircraft', 'qualified_aircraft_stall',
                 'qualified_control_authority', 'physical_construction', 'verified_gain'):
        assert lifted[flag] is False
    assert lifted['stall_sequencing'] == 'unsupported_attached_flow_only'
    assert lifted['mass_properties']['state'] == 'calculated_screening'
    compact = assessment.summary(lifted)
    assert compact['state'] == 'unsupported' and compact['qualified_aircraft'] is False
    with pytest.raises(ValueError, match='Passed bank requires'):
        assessment.assess(record, controls=controls, operating_point=OP, bank=bank,
                          bank_evidence=dict(state='passed_and_replayed', report_sha256=['0'*64],
                                             audit_evidence_sha256=['0'*64], replay='matched'))
    with pytest.raises(ValueError, match='evidence class'):
        assessment.assess(record, controls=controls, operating_point=OP, bank=bank)


def test_rates_and_sideslip_keep_family_lifting_unsupported():
    a = build('tandem')
    record = archive(a)
    controls = {c.name: 0. for c in a.controls}
    prepared = assessment.prepare(record, controls=controls, bank=fixture_bank(record, controls),
                                  bank_evidence=dict(state='development_fixture'), spanwise=2)
    for extra in (dict(q_rad_s=.2), dict(beta_deg=2.)):
        state = dict(OP, p_rad_s=0., q_rad_s=0., r_rad_s=0., controls=controls)
        state.update(extra)
        lifting = prepared['model'](state)[0]
        assert lifting.state == 'unsupported' and 'zero sideslip and zero rates' in lifting.reason


def test_complete_fixture_ledger_reaches_explicit_trim_outcome_for_canard():
    a = build('canard')
    record = archive(a)
    controls = {c.name: 0. for c in a.controls}
    result = assessment.assess(record, controls=controls, operating_point=OP,
        bank=fixture_bank(record, controls), bank_evidence=dict(state='development_fixture'), spanwise=2,
        body_geometry=_body_geometry(a), junctions=_junctions(a), hardware=_hinges(a), grid_points=3)
    assert result['resultant']['state'] == 'complete'
    # Gravity (calculated screening mass) enters inside trim, not this aerodynamic ledger.
    assert set(result['resultant']['evidence']) == {'development_fixture', 'numerical_model'}
    assert result['mass_properties']['state'] == 'calculated_screening'
    # Hardware mass is frozen into the revision and shifts the CG explicitly.
    assert result['mass_properties']['mass_kg'] == pytest.approx(a.mass_summary()['mass_kg']+.009*len(a.controls))
    assert result['state'].startswith('trim_')
    trim = result['trim']
    assert trim['state'] in {'trimmed', 'control_saturated', 'untrimmable_on_declared_grid',
                             'no_equilibrium_established', 'unbalanced_unsolved_residuals', 'out_of_coverage'}
    if trim['state'] != 'trimmed':
        assert trim.get('solution') is None or trim['state'] in {'control_saturated', 'unbalanced_unsolved_residuals'}
    assert result['qualified_aircraft'] is False and result['production_admission'] is False
