"""Strict schema, semantic identity and review binding; no native execution."""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

FIXTURES = Path(__file__).with_name('fixtures') / 'engineering'


def data():
    return json.loads((FIXTURES/'brief-draft.json').read_text())


def parse(model, payload):
    return model.model_validate_json(json.dumps(payload))


def draft(payload=None):
    from engineering.contracts import BriefDraft
    return parse(BriefDraft, payload if payload is not None else data())


def review_for(d):
    from engineering.contracts import REVIEW_FIELDS, ReviewAcknowledgement
    return parse(ReviewAcknowledgement, dict(
        draft_id=d.draft_id, draft_revision=d.revision, brief_sha256=d.content.brief_sha256,
        acknowledged_fields=REVIEW_FIELDS, reviewed_at='2026-11-12T12:05:00Z',
        reviewed_by='local-user'))


def finalized(d=None):
    from engineering.contracts import EngineeringBrief
    d = draft() if d is None else d
    return EngineeringBrief.from_draft(d, review_for(d), brief_id='beam-brief-1')


def test_fixture_round_trips_and_reference_candidates_keep_legacy_identity():
    from engineering.contracts import BriefDraft, CandidateSpec, EngineeringBrief
    from engineering.spec import specification_digest
    d = draft()
    assert BriefDraft.model_validate_json(d.model_dump_json()) == d
    b = finalized(d)
    assert EngineeringBrief.model_validate_json(b.model_dump_json()) == b
    for height in (5.0, 10.0, 15.0):
        c = CandidateSpec.from_brief(b, height)
        assert CandidateSpec.model_validate_json(c.model_dump_json()) == c
        assert c.candidate_sha256 == specification_digest(c.specification)
        assert len(c.candidate_sha256) == len(b.brief_sha256) == 64
        assert c.fixed_requirements_sha256 == b.fixed_requirements_sha256
    with pytest.raises(ValueError):
        CandidateSpec.from_brief(b, 12.0)
    with pytest.raises(ValidationError):
        b.content.requirements.length_mm = 300.0


@pytest.mark.parametrize('invalid', json.loads((FIXTURES/'invalid-briefs.json').read_text()))
def test_rejection_fixtures_cannot_become_executable_content(invalid):
    payload = data()
    payload['content']['requirements'][invalid['field']] = invalid['value']
    with pytest.raises(ValueError):
        draft(payload)


@pytest.mark.parametrize('field,value', [
    ('length_mm', float('nan')), ('tip_force_n', float('inf')),
    ('tip_force_n', None), ('length_mm', -1.0),
])
def test_nonfinite_missing_and_nonphysical_numbers_fail(field, value):
    payload = data()
    payload['content']['requirements'][field] = value
    with pytest.raises(ValueError):
        draft(payload)


def test_missing_load_and_conflicting_units_are_not_inferred():
    payload = data()
    del payload['content']['requirements']['tip_force_n']
    with pytest.raises(ValueError):
        draft(payload)
    payload = data()
    payload['content']['requirements']['length_cm'] = 20.0
    with pytest.raises(ValueError):
        draft(payload)


@pytest.mark.parametrize('unit,value,target,expected', [
    ('cm', 20.0, 'mm', 200.0), ('m', 0.2, 'mm', 200.0),
    ('m', 1.001, 'mm', 1001.0),
    ('in', 1.0, 'mm', 25.4), ('kN', 0.05, 'N', 50.0),
    ('GPa', 70.0, 'MPa', 70000.0), ('Pa', 120000000.0, 'MPa', 120.0),
])
def test_documented_conversions_normalize_explicit_meaning(unit, value, target, expected):
    from engineering.contracts import Quantity
    assert Quantity(value=value, unit=unit).normalized(target) == expected


@pytest.mark.parametrize('value,unit,target', [
    (5.0, 'kg', 'N'), (True, 'N', 'N'), ('50', 'N', 'N'),
    (1.0, 'mm', 'N'), (float('nan'), 'N', 'N'), (1.0, 'psi', 'MPa'),
])
def test_unsupported_or_ambiguous_quantity_is_rejected(value, unit, target):
    from engineering.contracts import Quantity
    with pytest.raises(ValueError):
        Quantity(value=value, unit=unit).normalized(target)


def test_mass_to_force_requires_explicit_gravity_review_and_reference():
    from engineering.contracts import ReviewedMassToForce
    fields = dict(mass_kg=5.0, gravity_m_s2=9.81, acknowledged=True,
                  reference='User approved F=m*g with local g=9.81 m/s2')
    conversion = ReviewedMassToForce(**fields)
    assert conversion.force_n == pytest.approx(49.05)
    for name in fields:
        incomplete = dict(fields)
        del incomplete[name]
        with pytest.raises(ValidationError):
            ReviewedMassToForce(**incomplete)
    for name, value in [('acknowledged', False), ('acknowledged', 1),
                        ('mass_kg', True), ('gravity_m_s2', float('inf')), ('reference', ' ')]:
        with pytest.raises(ValueError):
            ReviewedMassToForce(**dict(fields, **{name: value}))


def test_normalized_form_and_ai_content_share_semantics_but_keep_origins():
    from engineering.contracts import Quantity
    form = draft()
    payload = data()
    payload['draft_id'] = 'ai-draft-2'
    payload['revision'] = 7
    p = payload['provenance']
    p.update(record_id='ai-record-2', input_mode='ai', created_at='2026-11-12T13:00:00Z')
    for origin in p['field_origins']:
        origin.update(origin='ai_proposed', reference='Untrusted model proposal pending user review')
    req = payload['content']['requirements']
    req['length_mm'] = Quantity(value=20.0, unit='cm').normalized('mm')
    req['material']['youngs_modulus_mpa'] = Quantity(value=70.0, unit='GPa').normalized('MPa')
    req['tip_force_n'] = Quantity(value=0.05, unit='kN').normalized('N')
    payload['content']['candidate_heights_mm'] = [15.0, 5.0, 10.0, 5.0]
    ai = draft(payload)
    assert ai.content.fixed_requirements_sha256 == form.content.fixed_requirements_sha256
    assert ai.content.brief_sha256 == form.content.brief_sha256
    assert finalized(ai).brief_sha256 == finalized(form).brief_sha256
    assert ai.provenance != form.provenance


def test_material_force_and_choice_changes_mutate_only_the_right_identity():
    original = draft().content
    for mutate in ('force', 'material', 'reference', 'choices'):
        payload = data()
        c = payload['content']
        if mutate == 'force': c['requirements']['tip_force_n'] = 51.0
        if mutate == 'material': c['requirements']['material']['allowable_stress_mpa'] = 121.0
        if mutate == 'reference': c['requirements']['material']['reference'] = 'Another explicit source'
        if mutate == 'choices': c['candidate_heights_mm'] = [5.0, 10.0]
        changed = draft(payload).content
        assert changed.brief_sha256 != original.brief_sha256
        assert (changed.fixed_requirements_sha256 == original.fixed_requirements_sha256) == (mutate == 'choices')


def test_any_edit_requires_a_new_exact_revision_review():
    from engineering.contracts import EngineeringBrief
    old = draft()
    old_review = review_for(old)
    edited = old.revised(content=old.content, provenance=old.provenance)
    assert edited.revision == old.revision + 1
    with pytest.raises(ValueError, match='review'):
        EngineeringBrief.from_draft(edited, old_review, brief_id='stale')
    assert finalized(edited).draft_revision == 2
    tampered = json.loads(finalized().model_dump_json())
    tampered['content']['requirements']['tip_force_n'] = 51.0
    with pytest.raises(ValueError):
        parse(EngineeringBrief, tampered)


def test_partial_unacknowledged_or_clarification_drafts_cannot_finalize():
    from engineering.contracts import EngineeringBrief, ReviewAcknowledgement
    d = draft()
    for changes in ({'content': None, 'clarifications': ['Missing force']},
                    {'clarifications': ['Is the material source illustrative?']}):
        payload = data()
        payload.update(changes)
        incomplete = draft(payload)
        with pytest.raises(ValueError):
            EngineeringBrief.from_draft(incomplete, review_for(d), brief_id='invalid')
    payload = json.loads(review_for(d).model_dump_json())
    payload['acknowledged_fields'].remove('material.reference')
    partial_review = parse(ReviewAcknowledgement, payload)
    with pytest.raises(ValueError):
        EngineeringBrief.from_draft(d, partial_review, brief_id='unreviewed-material')


@pytest.mark.parametrize('field', ['version', 'numerical_policy_version', 'resource_policy_version'])
def test_unavailable_versions_cannot_silently_substitute(field):
    payload = data()
    payload['content']['capability'][field] = 'unavailable'
    with pytest.raises(ValueError):
        draft(payload)


def test_check_and_run_schema_require_evidence_identity_and_consistent_outcomes():
    from engineering.contracts import CheckResult, EngineeringRun
    b = finalized()
    base = dict(check_id='stress_allowable', state='passed', method='beam_fem', method_version='1',
                brief_sha256=b.brief_sha256, candidate_sha256='a'*64,
                measurements=[{'name': 'stress', 'value': 26.6667, 'unit': 'MPa'}],
                limits=[{'name': 'stress', 'value': 120.0, 'unit': 'MPa', 'comparison': 'le'}],
                artifacts=[{'path': 'model.step', 'sha256': 'b'*64, 'bytes': 100}])
    check = parse(CheckResult, base)
    assert CheckResult.model_validate_json(check.model_dump_json()) == check
    for change in ({'measurements': []}, {'limits': []}, {'candidate_sha256': 'short'},
                   {'artifacts': []}, {'state': 'failed'}, {'state': 'skipped'}):
        with pytest.raises(ValueError):
            parse(CheckResult, dict(base, **change))
    unavailable = dict(base, state='unavailable', reason='Solver unavailable', measurements=[], limits=[], artifacts=[])
    assert parse(CheckResult, unavailable).state == 'unavailable'
    run = dict(schema_version='engineering_run_v1', job_id='run-1', capability=b.content.capability.model_dump(),
               brief_sha256=b.brief_sha256, fixed_requirements_sha256=b.fixed_requirements_sha256,
               lifecycle='admitted', outcome='not_evaluated', attempts=[],
               created_at='2026-11-12T12:06:00Z', updated_at='2026-11-12T12:06:00Z')
    r = parse(EngineeringRun, run)
    assert EngineeringRun.model_validate_json(r.model_dump_json()) == r
    for change in ({'outcome': 'accepted'}, {'lifecycle': 'completed', 'outcome': 'accepted'},
                   {'lifecycle': 'unavailable', 'outcome': 'accepted'}, {'brief_sha256': 'bad'},
                   {'updated_at': '2026-11-11T00:00:00Z'}):
        with pytest.raises(ValueError):
            parse(EngineeringRun, dict(run, **change))


def test_candidate_tampering_breaks_association_with_reviewed_meaning():
    from engineering.contracts import CandidateSpec
    c = CandidateSpec.from_brief(finalized(), 15.0)
    original = json.loads(c.model_dump_json())
    for key, value in [('brief_sha256', 'f'*64), ('fixed_requirements_sha256', 'e'*64),
                       ('candidate_sha256', 'd'*64)]:
        with pytest.raises(ValueError):
            parse(CandidateSpec, dict(original, **{key: value}))
    changed = copy.deepcopy(original)
    changed['specification']['tip_force_n'] = 51.0
    with pytest.raises(ValueError):
        parse(CandidateSpec, changed)


def accepted_run_data():
    """Synthetic schema fixture, expressly not native acceptance evidence."""
    from engineering.capabilities import CAPABILITY
    from engineering.contracts import CandidateSpec
    payload = data()
    payload['content']['mode'] = 'fixed_design'
    payload['content']['candidate_heights_mm'] = [15.0]
    b = finalized(draft(payload))
    candidate = CandidateSpec.from_brief(b, 15.0)
    step = dict(path='model.step', sha256='b'*64, bytes=100)
    checks = [dict(check_id=name, state='passed', method='synthetic_fixture', method_version='1',
                   brief_sha256=b.brief_sha256, candidate_sha256=candidate.candidate_sha256,
                   measurements=[dict(name='fixture', value=1.0, unit='count')],
                   limits=[dict(name='fixture', value=1.0, unit='count', comparison='eq')],
                   artifacts=[step]) for name in CAPABILITY.required_checks]
    paths = ['brief.json', 'selected_spec.json', 'model.py', 'model.step',
             'preview.glb', 'attempts.json', 'report.json', 'report.md']
    return dict(schema_version='engineering_run_v1', job_id='synthetic-run-1',
                capability=b.content.capability.model_dump(mode='json'),
                brief_sha256=b.brief_sha256, fixed_requirements_sha256=b.fixed_requirements_sha256,
                lifecycle='completed', outcome='accepted',
                created_at='2026-11-12T12:06:00Z', updated_at='2026-11-12T12:07:00Z',
                attempts=[dict(ordinal=0, candidate=candidate.model_dump(mode='json'),
                               outcome='accepted', checks=checks)],
                selected_candidate_sha256=candidate.candidate_sha256,
                bundle=dict(payloads=[dict(path=p, sha256='b'*64, bytes=100) for p in paths],
                            manifest_sha256='c'*64, manifest_bytes=100, zip_sha256='d'*64,
                            zip_bytes=200, stored_bytes=1100))


def test_complete_run_round_trips_without_claiming_native_verification():
    from engineering.contracts import EngineeringRun
    run = parse(EngineeringRun, accepted_run_data())
    assert EngineeringRun.model_validate_json(run.model_dump_json()) == run


@pytest.mark.parametrize('mutation', [
    'missing_check', 'unavailable_check', 'forged_artifact', 'wrong_selected',
    'missing_payload', 'manifest_self_hash', 'missing_manifest_bytes', 'understated_storage',
    'over_budget', 'wrong_check_candidate', 'duplicate_check', 'non_step_evidence',
])
def test_synthetic_accepted_records_reject_incomplete_or_mismatched_evidence(mutation):
    from engineering.contracts import EngineeringRun
    payload = accepted_run_data()
    checks = payload['attempts'][0]['checks']
    bundle = payload['bundle']
    if mutation == 'missing_check': checks.pop()
    if mutation == 'unavailable_check': checks[0].update(state='unavailable', reason='Missing solver')
    if mutation == 'forged_artifact': bundle['payloads'][3]['sha256'] = 'f'*64
    if mutation == 'wrong_selected': payload['selected_candidate_sha256'] = 'e'*64
    if mutation == 'missing_payload': bundle['payloads'].pop()
    if mutation == 'manifest_self_hash': bundle['payloads'].append(dict(path='manifest.json', sha256='c'*64, bytes=100))
    if mutation == 'missing_manifest_bytes': del bundle['manifest_bytes']
    if mutation == 'understated_storage': bundle['stored_bytes'] = 1000
    if mutation == 'over_budget': bundle['stored_bytes'] = 32 * 1024**2 + 1
    if mutation == 'wrong_check_candidate': checks[0]['candidate_sha256'] = 'e'*64
    if mutation == 'duplicate_check': checks.append(copy.deepcopy(checks[0]))
    if mutation == 'non_step_evidence': checks[0]['artifacts'][0]['path'] = 'report.md'
    with pytest.raises(ValueError):
        parse(EngineeringRun, payload)


def test_unavailable_mandatory_stage_cannot_be_reported_as_ordinary_rejection():
    from engineering.contracts import AttemptRecord
    payload = accepted_run_data()['attempts'][0]
    payload.update(outcome='rejected', failures=['Stress requirement failed'])
    payload['checks'][0].update(state='unavailable', reason='Native read unavailable')
    payload['checks'][-1].update(state='failed', reason='Displacement failed')
    with pytest.raises(ValueError):
        parse(AttemptRecord, payload)


@pytest.mark.parametrize('mutation', ['skip_first', 'out_of_order', 'duplicate_candidate'])
def test_recorded_attempts_cannot_skip_or_reorder_the_finalized_set(mutation):
    from engineering.contracts import CandidateSpec, EngineeringRun
    b = finalized()
    payload = accepted_run_data()
    payload.update(brief_sha256=b.brief_sha256,
                   fixed_requirements_sha256=b.fixed_requirements_sha256,
                   lifecycle='running', outcome='not_evaluated', selected_candidate_sha256=None, bundle=None)
    heights = {'skip_first': [10.0], 'out_of_order': [10.0, 5.0], 'duplicate_candidate': [5.0, 5.0]}[mutation]
    payload['attempts'] = []
    for index, height in enumerate(heights):
        c = CandidateSpec.from_brief(b, height)
        check = dict(check_id='displacement_requirement', state='failed', method='synthetic_fixture',
                     method_version='1', brief_sha256=b.brief_sha256, candidate_sha256=c.candidate_sha256,
                     measurements=[], limits=[], artifacts=[], reason='Synthetic failed check')
        payload['attempts'].append(dict(ordinal=index, candidate=c.model_dump(mode='json'),
                                        outcome='rejected', failures=['Rejected'], checks=[check]))
    with pytest.raises(ValueError):
        parse(EngineeringRun, payload)


def test_converted_force_provenance_and_all_origins_remain_required_at_review():
    from engineering.contracts import EngineeringBrief
    payload = data()
    payload['content']['requirements']['tip_force_n'] = 49.05
    payload['provenance']['force_conversion'] = dict(
        mass_kg=5.0, gravity_m_s2=9.81, acknowledged=True, reference='Reviewed explicit conversion')
    origin = next(o for o in payload['provenance']['field_origins'] if o['field'] == 'tip_force_n')
    origin['origin'] = 'converted'
    assert finalized(draft(payload)).content.requirements.tip_force_n == 49.05
    payload['content']['requirements']['tip_force_n'] = 50.0
    d = draft(payload)
    with pytest.raises(ValueError):
        EngineeringBrief.from_draft(d, review_for(d), brief_id='mismatched-conversion')
    payload = data()
    payload['provenance']['field_origins'].pop()
    with pytest.raises(ValueError):
        finalized(draft(payload))


def test_exported_json_schemas_and_imports_are_provider_free():
    import subprocess
    import sys
    code = '''import sys
from engineering.contracts import EngineeringBrief, CandidateSpec, CheckResult, EngineeringRun
for model in (EngineeringBrief, CandidateSpec, CheckResult, EngineeringRun):
    schema = model.model_json_schema()
    assert schema['additionalProperties'] is False
assert not any(m in sys.modules for m in ['agents', 'google.generativeai', 'cadquery', 'OCP', 'numpy'])
'''
    subprocess.run([sys.executable, '-c', code], check=True)


def test_shared_serialized_fixtures_round_trip_for_downstream_consumers():
    from engineering.contracts import CandidateSpec, EngineeringBrief, EngineeringRun
    for name, model in [('finalized-brief.json', EngineeringBrief),
                        ('candidate-15mm.json', CandidateSpec), ('admitted-run.json', EngineeringRun)]:
        value = model.model_validate_json((FIXTURES/name).read_text())
        assert model.model_validate_json(value.model_dump_json()) == value


def test_each_top_level_record_pins_its_schema_version():
    from engineering.contracts import BriefDraft, CandidateSpec, CheckResult, EngineeringBrief
    records = [draft(), finalized(), CandidateSpec.from_brief(finalized(), 15.0),
               parse(CheckResult, accepted_run_data()['attempts'][0]['checks'][0])]
    for record in records:
        payload = json.loads(record.model_dump_json())
        assert payload['schema_version'].endswith('_v1')
        payload['schema_version'] = 'unsupported_v2'
        with pytest.raises(ValueError):
            parse(type(record), payload)


@pytest.mark.parametrize('mutation', [
    'missing_origins', 'duplicate_origins', 'unknown_origin_field',
    'missing_material_reference', 'unknown_material_source', 'empty_choices',
    'bad_choice', 'boolean_choice', 'too_many_choices', 'fixed_multi_choice',
    'missing_required_check', 'unknown_schema', 'naive_timestamp',
])
def test_incomplete_or_conflicting_drafts_cannot_become_finalized(mutation):
    payload = data()
    content = payload['content']
    if mutation == 'missing_origins': payload['provenance']['field_origins'].pop()
    if mutation == 'duplicate_origins': payload['provenance']['field_origins'].append(payload['provenance']['field_origins'][0])
    if mutation == 'unknown_origin_field': payload['provenance']['field_origins'][0]['field'] = 'invented'
    if mutation == 'missing_material_reference': del content['requirements']['material']['reference']
    if mutation == 'unknown_material_source': content['requirements']['material']['source_kind'] = 'certified_by_ai'
    if mutation == 'empty_choices': content['candidate_heights_mm'] = []
    if mutation == 'bad_choice': content['candidate_heights_mm'] = [-1.0]
    if mutation == 'boolean_choice': content['candidate_heights_mm'] = [True]
    if mutation == 'too_many_choices': content['candidate_heights_mm'] = [1.0, 2.0, 3.0, 4.0, 5.0]
    if mutation == 'fixed_multi_choice': content['mode'] = 'fixed_design'
    if mutation == 'missing_required_check': content['required_checks'] = ['native_geometry']
    if mutation == 'unknown_schema': content['schema_version'] = 'future_schema'
    if mutation == 'naive_timestamp': payload['provenance']['created_at'] = '2026-11-12T12:00:00'
    with pytest.raises(ValueError):
        finalized(draft(payload))
