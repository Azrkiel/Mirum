"""rectangular_plate in the v1 UI: typed form, claim labels and result view (no browser, no backend)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engineering_v1 as ev
from test_engineering_v1 import Backend, SCRIPT, entry as v1_entry, metadata

PLATE = 'rectangular_plate'
BEAM = ev.BEAM_ID
OTHERS = ('co2_dragster', 'regen_chamber', 'glider_cem')

RAW = dict(length_a_mm='1500', width_b_mm='1000', edge_condition='simply_supported', pressure_mpa='0.01',
           youngs_modulus_mpa='200000', poissons_ratio='0.3', allowable_stress_mpa='250', max_deflection_mm='20',
           dimensional_tolerance_mm='0.01', material_reference='Illustrative steel', source_kind='illustrative',
           mode='bounded_thickness', candidate_thicknesses_mm='8, 10, 12')

SS_NOTE = ('corners held down: the simply supported edges and corners are held against lift; '
           'corner hold-down forces are reported')
ALLOWABLE_NOTE = 'The allowable stress is your design allowable; no hidden safety factor is applied'
SHEAR_NOTE = 'w_check = w_K (1 + 20 (h/b)^2)'


def plate_entry(claim='checked_design', available=True):
    e = v1_entry(PLATE, label='Rectangular plate in bending', available=available)
    e['claim'] = claim
    return e


def with_claim(cid, claim):
    e = v1_entry(cid)
    e['claim'] = claim
    return e


def report(edge='simply_supported', outcome='accepted'):
    ok = outcome == 'accepted'
    verify = dict(
        scope='s', passed=ok, outcome=outcome, vm_max_location=dict(x=0.0, y=0.0, region='corner'),
        mesh_history=[dict(w_max=10.1, vm_max_mpa=120.0, corner_force_n=5.0),
                      dict(w_max=10.2, vm_max_mpa=121.0, corner_force_n=5.1)],
        references=dict(method='navier_ss_r1', w_max=10.25, vm_max=121.5),
        guards=dict(thin_ratio=dict(state='passed', quantity=100.0, limit=20.0, comparison='ge'),
                    small_deflection=dict(state='passed', quantity=0.9, limit=0.2, comparison='le')),
        checks=dict(deflection_limit=dict(state='passed', quantity=10.4, limit=20.0, comparison='le'),
                    stress_limit=dict(state='passed', quantity=121.0, limit=250.0, comparison='le')))
    corner = dict(finest_n=5.1, per_level_n=[5.0, 5.1], twist_force_n=2.5) if edge == 'simply_supported' else None
    return dict(passed=ok, outcome=outcome, engineering_certified=False,
                standards=dict(state='not_evaluated', reason='none'), mode='bounded_thickness',
                selected_thickness_mm=10.0 if ok else None, corner_forces=corner,
                attempts=[dict(ordinal=0, thickness_mm=8.0, passed=False, verify=dict(verify, passed=False)),
                          dict(ordinal=1, thickness_mm=10.0, passed=ok, verify=verify)])


# ---------------------------------------------------------------- typed payload

def test_plate_brief_matches_the_plate_brief_shape():
    brief = ev.plate_brief(RAW)
    assert brief == dict(
        mode='bounded_thickness',
        requirements=dict(family='rectangular_plate', units='mm_N_MPa', length_a_mm=1500.0, width_b_mm=1000.0,
                          edge_condition='simply_supported', pressure_mpa=0.01, youngs_modulus_mpa=200000.0,
                          poissons_ratio=0.3, allowable_stress_mpa=250.0, max_deflection_mm=20.0,
                          dimensional_tolerance_mm=0.01,
                          material_reference=dict(source_kind='illustrative', reference='Illustrative steel'),
                          assumptions='kirchhoff_uniform_pressure_uniform_edges_linear_elastic'),
        candidate_thicknesses_mm=[8.0, 10.0, 12.0])
    assert 'thickness_mm' not in brief['requirements']


def test_plate_brief_validates_against_the_backend_model_when_importable():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    sys.path.insert(0, root)
    try:
        from backend.engineering.plate_spec import PlateBrief
    except Exception:
        pytest.skip('backend package not importable here')
    import json
    PlateBrief.model_validate_json(json.dumps(ev.plate_brief(RAW)))


def test_plate_brief_never_swaps_or_clips():
    swapped = ev.plate_brief(dict(RAW, length_a_mm='500', width_b_mm='1000'))
    assert swapped['requirements']['length_a_mm'] == 500.0 and swapped['requirements']['width_b_mm'] == 1000.0
    many = ev.plate_brief(dict(RAW, candidate_thicknesses_mm='1,2,3,4,5,6'))
    assert many['candidate_thicknesses_mm'] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    out = ev.plate_brief(dict(RAW, pressure_mpa='99999'))
    assert out['requirements']['pressure_mpa'] == 99999.0


@pytest.mark.parametrize('field,value,message', [
    ('length_a_mm', 'abc', 'Length a'), ('pressure_mpa', 'nan', 'Pressure'), ('youngs_modulus_mpa', 'inf', 'Young'),
    ('width_b_mm', '', 'Width b'), ('candidate_thicknesses_mm', '8, x', 'thickness')])
def test_plate_brief_rejects_non_numbers_with_a_named_error(field, value, message):
    with pytest.raises(ValueError, match=message):
        ev.plate_brief(dict(RAW, **{field: value}))


def test_plate_form_values_round_trip_a_draft_specification():
    brief = ev.plate_brief(RAW)
    values = ev.plate_form_values(brief)
    assert ev.plate_brief(values) == brief
    blank = ev.plate_form_values(None)
    assert blank['edge_condition'] == 'simply_supported' and blank['mode'] == 'fixed_design'
    assert blank['length_a_mm'] == ''


def test_plate_draft_goes_through_the_generator_form_path():
    body = ev.initial_draft_body(plate_entry(), specification=ev.plate_brief(RAW))
    assert body['mode'] == 'form' and body['capability'] == PLATE
    assert body['input']['capability']['id'] == PLATE
    assert body['input']['specification']['mode'] == 'bounded_thickness'


def test_plate_is_a_generator_option():
    opts = ev.capability_options(metadata(BEAM, PLATE))
    assert [(o['id'], o['kind']) for o in opts] == [(BEAM, 'beam'), (PLATE, 'generator')]


# ---------------------------------------------------------------- claim labels

def test_plate_checked_design_reads_as_the_beam_does():
    e = plate_entry()
    assert ev.claim_of(e) is None
    assert ev.claim_of(e, dict(claim='checked_design')) is None
    assert ev.accepted_label(e) == 'Checked design'
    assert ev.result_heading(e) == 'Checked result'
    assert ev.state_line(e, dict(lifecycle='completed', outcome='accepted')) == \
        'State: completed | engineering outcome: accepted'
    kind, text = ev.outcome_message(e, dict(outcome='accepted'))
    assert kind == 'success' and text.startswith('Accepted under the declared digital model')
    assert ev.attempt_outcome(e, {}, dict(outcome='accepted')) == 'accepted'
    done = dict(outcome='accepted', delivery='available', lifecycle='completed')
    assert ev.result_view(done, e)['label'] == 'Checked design'


def test_checked_design_claim_is_honoured_only_for_the_plate():
    assert ev.claim_of(v1_entry('co2_dragster'), dict(claim='checked_design')) == 'screening_only'
    assert ev.claim_of(with_claim('glider_cem', 'checked_design')) == 'screening_only'


def test_other_generators_labels_are_unchanged():
    expect = {('co2_dragster', 'geometry_checks_only'): ('Geometry checks passed', 'Geometry check result'),
              ('regen_chamber', 'geometry_checks_only'): ('Geometry checks passed', 'Geometry check result'),
              ('glider_cem', 'screening_only'): ('Screened', 'Screening result')}
    for (cid, claim), (label, heading) in expect.items():
        e = with_claim(cid, claim)
        assert (ev.accepted_label(e), ev.result_heading(e)) == (label, heading)
    assert ev.accepted_label(v1_entry(BEAM)) == 'Checked design'


def test_route_labels_know_the_plate_and_keep_the_others():
    import route_labels as rl
    assert rl.workflow_for(dict(capability_id=PLATE)) == 'Checked design'
    target = rl.route_target(dict(capability_id=PLATE))
    assert target['workflow'] == 'Checked design' and target['capability_id'] == PLATE and target['prompt'] is None
    assert target['notice'] == 'Checked design: Checked plate'
    assert rl.WORKFLOW_BY_CAPABILITY['co2_dragster'] == 'CO2 dragster'
    assert rl.route_target(dict(capability_id='glider_cem'))['notice'] == 'Checked design: Physics-based glider CEM'
    assert rl.assurance_label('checked_design') == 'Checked design'


# ---------------------------------------------------------------- result view

def _text(view):
    return '\n'.join(view['notes'] + view['lines'])


def test_ss_result_shows_every_required_fact():
    view = ev.plate_result_view(report('simply_supported'))
    assert view['selected_thickness_mm'] == 10.0
    assert view['attempts'] == [dict(thickness_mm=8.0, passed=False), dict(thickness_mm=10.0, passed=True)]
    text = _text(view)
    assert SS_NOTE in text and ALLOWABLE_NOTE in text and SHEAR_NOTE in text
    assert 'engineering_certified: false' in text and 'standards: not evaluated' in text
    assert view['deflection'] == dict(checked_mm=10.4, limit_mm=20.0, kirchhoff_mm=10.2)
    assert view['stress'] == dict(von_mises_mpa=121.0, allowable_mpa=250.0,
                                  location=dict(x=0.0, y=0.0, region='corner'))
    assert len(view['mesh_history']) == 2
    assert view['references'] == dict(method='navier_ss_r1', w_max=10.25, vm_max=121.5)
    assert set(view['guards']) == {'thin_ratio', 'small_deflection'}
    assert view['corner_forces']['finest_n'] == 5.1


def test_clamped_result_has_no_corner_forces_or_hold_down_note():
    view = ev.plate_result_view(report('clamped'))
    assert view['corner_forces'] is None
    assert 'corners held down' not in _text(view)
    assert ALLOWABLE_NOTE in _text(view) and SHEAR_NOTE in _text(view)


def test_rejected_result_has_no_selected_thickness():
    view = ev.plate_result_view(report('simply_supported', outcome='rejected'))
    assert view['selected_thickness_mm'] is None


def test_result_view_tolerates_a_sparse_report():
    view = ev.plate_result_view(dict(attempts=[]))
    assert view['selected_thickness_mm'] is None and view['corner_forces'] is None and view['mesh_history'] == []
    assert ALLOWABLE_NOTE in _text(view)


# ---------------------------------------------------------------- AppTest: the form

def _plate_meta(ai=False):
    meta = metadata(BEAM, PLATE)
    meta['capabilities'][1] = plate_entry()
    meta['ai_enabled'] = meta['ai_available'] = ai
    return meta


def _page(monkeypatch, backend, session=None):
    import requests
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(requests, 'request', backend)
    app = AppTest.from_string(SCRIPT.format(frontend=os.path.dirname(os.path.abspath(__file__))), default_timeout=30)
    app.session_state['checked_capability'] = PLATE
    for k, v in (session or {}).items():
        app.session_state[k] = v
    return app.run()


def _plate_draft(spec, revision=1, issues=()):
    cap = dict(id=PLATE, version='1.0.0', numerical_policy_version='n2', resource_policy_version='r2')
    return dict(draft=dict(draft_id='d1', revision=revision, content=None, clarifications=[], provenance={}),
                input=dict(capability=cap, specification=spec), issues=list(issues), brief_sha256=None)


def _by_key(app, name):
    for group in (app.text_input, app.selectbox):
        for w in group:
            if w.key == 'v1plate_new_' + name:
                return w
    raise AssertionError('missing widget ' + name)


FORM_FIELDS = ('length_a_mm', 'width_b_mm', 'edge_condition', 'pressure_mpa', 'youngs_modulus_mpa', 'poissons_ratio',
               'allowable_stress_mpa', 'max_deflection_mm', 'dimensional_tolerance_mm', 'material_reference',
               'source_kind', 'mode', 'candidate_thicknesses_mm')


def _fill(app, raw):
    for name, value in raw.items():
        _by_key(app, name).set_value(value)


def test_form_fields_present_and_no_json_editor_or_ai_control(monkeypatch):
    app = _page(monkeypatch, Backend(_plate_meta(ai=True)))
    assert not app.exception
    for name in FORM_FIELDS:
        _by_key(app, name)
    assert any(b.label == 'Prepare brief' for b in app.button)
    assert not any(t.label == 'Specification JSON' for t in app.text_area)
    assert not any(r.label == 'Entry method' for r in app.radio)
    assert not any('AI' in b.label or 'Propose' in b.label for b in app.button)
    assert not any(b.label == 'Draft from description' for b in app.button)


def test_form_posts_a_typed_form_draft_and_shows_server_issues(monkeypatch):
    issue = dict(state='invalid', field='requirements.width_b_mm', reason='Plate requires the length to be at least the width')
    swapped = dict(RAW, length_a_mm='500')
    backend = Backend(_plate_meta(), drafts=[_plate_draft(ev.plate_brief(swapped), issues=[issue])])
    app = _page(monkeypatch, backend)
    _fill(app, swapped)
    next(b for b in app.button if b.label == 'Prepare brief').click().run()
    assert not app.exception
    posts = [c for c in backend.calls if c[:2] == ('POST', '/drafts')]
    assert len(posts) == 1
    body = posts[0][2]
    assert body['mode'] == 'form' and body['capability'] == PLATE
    spec = body['input']['specification']
    assert spec['requirements']['length_a_mm'] == 500.0 and spec['requirements']['width_b_mm'] == 1000.0
    assert spec['candidate_thicknesses_mm'] == [8.0, 10.0, 12.0]
    shown = [e.value for e in list(app.error) + list(app.warning)]
    assert any('Plate requires the length to be at least the width' in s for s in shown)
    assert not any(t.label == 'Specification JSON' for t in app.text_area)


def test_bad_number_is_an_error_and_makes_no_request(monkeypatch):
    backend = Backend(_plate_meta())
    app = _page(monkeypatch, backend)
    _fill(app, dict(RAW, pressure_mpa='lots'))
    next(b for b in app.button if b.label == 'Prepare brief').click().run()
    assert any('Pressure' in e.value for e in app.error)
    assert not any(c[:2] == ('POST', '/drafts') for c in backend.calls)


def test_other_generators_still_show_their_json_editor_and_ai_entry(monkeypatch):
    meta = metadata(BEAM, *OTHERS)
    meta['ai_enabled'] = meta['ai_available'] = True
    app = _page(monkeypatch, Backend(meta), dict(checked_capability='co2_dragster'))
    assert any(r.label == 'Entry method' for r in app.radio)
    assert any(b.label == 'Start an empty draft' for b in app.button)
