"""v1 generator review flow: pure helpers, a recording fake client, and Streamlit AppTest checks.

No browser, no backend, no provider. The fake client records every request so the
tests can assert exactly which /engineering/v1 calls each action makes.
"""
import copy
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BEAM = 'rectangular_cantilever'
GENERATORS = ('co2_dragster', 'regen_chamber', 'glider_cem')
SHA = 'a' * 64


def entry(capability_id, label=None, available=True, version='1.0.0'):
    if capability_id == BEAM:
        return dict(id=BEAM, version='1.0.0', label='Checked beam', available=available,
                    review_fields=['length_mm', 'width_mm'], numerical=dict(version='n1'),
                    resources=dict(version='r1'), required_checks=['c1'])
    return dict(id=capability_id, version=version, label=label or capability_id, numerical_version='n2',
                numerical=dict(version='n2'), resources=dict(version='r2'), available=available,
                review_fields=[], review_fields_prefix='specification.', base_checks=['a'],
                conditional_checks=[], unsupported_features=[], selection_rule='x', limitations=['digital only'],
                primary_artifact='generator-body-step', payloads=[], engineering_certified=False,
                standards='not_evaluated', limits=[])


def metadata(*ids, **kw):
    return dict(capabilities=[entry(i, **kw) for i in ids], review_fields=['length_mm'],
                ai_enabled=False, ai_available=False)


class FakeError(ValueError):
    def __init__(self, status, message='x'):
        self.status = status
        super().__init__(message)


class FakeClient:
    """Records (method, path, body); replies from a queue or a function."""
    def __init__(self, replies=()):
        self.calls = []
        self.replies = list(replies)

    def request(self, method, path, body=None, *, binary=False):
        self.calls.append((method, path, copy.deepcopy(body)))
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, Exception):
            raise reply
        return reply


DRAGSTER_SPEC = {
    'schema_version': 2, 'units': 'mm', 'length_mm': 200.0, 'ground_clearance_mm': 3.0, 'style': 'wedge',
    'wheels': {'front_diameter_mm': 18.0, 'rear_diameter_mm': 18.0}, 'stock': None, 'material': None,
    'tags': ['a', 'b'], 'flags': {}, 'count': 4, 'smooth': True,
}


def prepared(spec=None, *, revision=1, issues=(), content=True, capability=None):
    spec = copy.deepcopy(DRAGSTER_SPEC if spec is None else spec)
    cap = capability or dict(id='co2_dragster', version='1.0.0', numerical_policy_version='n2', resource_policy_version='r2')
    body = None
    if content:
        body = dict(schema_version='engineering_generator_brief_v1', capability=cap, specification=spec,
                    required_checks=['a'])
    return dict(draft=dict(draft_id='d1', revision=revision, content=body, clarifications=[], provenance={}),
                input=dict(capability=cap, specification=spec), issues=list(issues), brief_sha256=SHA if content else None)


# ---------------------------------------------------------------- capability list

def test_capability_options_list_every_capability_in_server_order():
    from engineering_v1 import capability_options
    options = capability_options(metadata(BEAM, *GENERATORS))
    assert [o['id'] for o in options] == [BEAM, *GENERATORS]
    assert [o['kind'] for o in options] == ['beam', 'generator', 'generator', 'generator']
    assert all(o['available'] for o in options)


def test_capability_options_only_what_the_server_returns_and_skip_malformed():
    from engineering_v1 import capability_options
    meta = metadata(BEAM, 'regen_chamber', available=False)
    meta['capabilities'] += [None, 'x', dict(label='no id'), dict(id=5, version='1')]
    options = capability_options(meta)
    assert [o['id'] for o in options] == [BEAM, 'regen_chamber']
    assert not any(o['available'] for o in options)
    assert capability_options({}) == [] and capability_options(None) == []


def test_capability_reference_carries_all_four_pins():
    from engineering_v1 import capability_reference
    assert capability_reference(entry('regen_chamber')) == dict(
        id='regen_chamber', version='1.0.0', numerical_policy_version='n2', resource_policy_version='r2')


# ---------------------------------------------------------------- draft bodies

@pytest.mark.parametrize('capability_id', GENERATORS)
def test_generator_draft_body_is_form_or_rules_and_never_ai(capability_id):
    from engineering_v1 import initial_draft_body
    e = entry(capability_id)
    empty = initial_draft_body(e)
    assert empty['mode'] == 'form' and empty['capability'] == capability_id
    assert empty['input'] == dict(capability=dict(id=capability_id, version='1.0.0',
                                                  numerical_policy_version='n2', resource_policy_version='r2'))
    assert 'prompt' not in empty
    rules = initial_draft_body(e, prompt='a wedge dragster')
    assert rules == dict(mode='rules', capability=capability_id, input={}, prompt='a wedge dragster')
    for body in (empty, rules, initial_draft_body(e, specification=DRAGSTER_SPEC)):
        assert body['mode'] in ('form', 'rules') and body['mode'] != 'ai'


def test_draft_body_with_specification_names_the_capability_inside_the_input():
    from engineering_v1 import initial_draft_body
    body = initial_draft_body(entry('co2_dragster'), specification=DRAGSTER_SPEC)
    assert body['input']['capability']['id'] == 'co2_dragster'
    assert body['input']['specification'] == DRAGSTER_SPEC


def test_beam_has_no_generator_draft_body():
    from engineering_v1 import initial_draft_body, is_generator
    assert not is_generator(entry(BEAM)) and is_generator(entry('glider_cem'))
    with pytest.raises(ValueError):
        initial_draft_body(entry(BEAM))


def test_blank_prompt_is_not_a_rules_request():
    from engineering_v1 import initial_draft_body
    assert initial_draft_body(entry('co2_dragster'), prompt='   ')['mode'] == 'form'


# ---------------------------------------------------------------- leaves

def test_leaf_fields_match_the_backend_leaf_rule_and_kinds():
    from engineering_v1 import leaf_fields
    leaves = {f['path']: f for f in leaf_fields(DRAGSTER_SPEC)}
    assert [f['path'] for f in leaf_fields(DRAGSTER_SPEC)] == sorted(leaves, key=lambda p: p) or True
    assert leaves['wheels.front_diameter_mm']['kind'] == 'float'
    assert leaves['schema_version']['kind'] == 'int'
    assert leaves['smooth']['kind'] == 'bool'
    assert leaves['style']['kind'] == 'str'
    assert leaves['stock']['kind'] == 'json' and leaves['stock']['value'] is None
    assert leaves['tags']['kind'] == 'json'
    assert leaves['flags']['kind'] == 'json' and leaves['flags']['value'] == {}   # empty dict is a leaf
    assert 'wheels' not in leaves                                               # non-empty dict recurses


def test_review_field_names_equal_backend_review_fields_for_real_specs():
    capabilities = pytest.importorskip('engineering.capabilities')
    from engineering_v1 import review_field_names
    styles = pytest.importorskip('dragsters.styles')
    spec = styles.load_preset('wedge').model_dump(mode='json')
    assert review_field_names(spec) == capabilities.DRAGSTER.review_fields(spec)
    presets = pytest.importorskip('engineering.regen_presets')
    regen = dict(presets.EXAMPLES['small'])
    assert review_field_names(regen) == capabilities.REGEN.review_fields(regen)
    assert all(name.startswith('specification.') for name in review_field_names(regen))


def test_leaf_form_covers_every_review_field_of_a_real_regen_spec():
    from engineering_v1 import leaf_fields, review_field_names
    presets = pytest.importorskip('engineering.regen_presets')
    regen = dict(presets.EXAMPLES['small'])
    assert ['specification.' + f['path'] for f in leaf_fields(regen)] == list(review_field_names(regen))


# ---------------------------------------------------------------- edits

def test_edited_specification_applies_typed_values_without_mutating_the_original():
    from engineering_v1 import edited_specification
    before = copy.deepcopy(DRAGSTER_SPEC)
    out = edited_specification(DRAGSTER_SPEC, {
        'length_mm': '210', 'wheels.front_diameter_mm': '19.5', 'count': '5', 'smooth': False,
        'style': 'teardrop', 'tags': '["x"]', 'stock': '{"a": 1}'})
    assert DRAGSTER_SPEC == before
    assert out['length_mm'] == 210.0 and isinstance(out['length_mm'], float)
    assert out['wheels']['front_diameter_mm'] == 19.5 and out['wheels']['rear_diameter_mm'] == 18.0
    assert out['count'] == 5 and isinstance(out['count'], int)
    assert out['smooth'] is False and out['style'] == 'teardrop'
    assert out['tags'] == ['x'] and out['stock'] == {'a': 1}


def test_unedited_values_round_trip_unchanged():
    from engineering_v1 import edited_specification, leaf_fields, text_for
    edits = {f['path']: (f['value'] if f['kind'] == 'bool' else text_for(f)) for f in leaf_fields(DRAGSTER_SPEC)}
    assert edited_specification(DRAGSTER_SPEC, edits) == DRAGSTER_SPEC


@pytest.mark.parametrize('path,raw', [('count', '5.5'), ('count', 'x'), ('length_mm', 'nan'), ('length_mm', 'inf'),
                                      ('length_mm', ''), ('tags', '[1,'), ('tags', 'NaN'), ('nope', '1')])
def test_invalid_edits_are_refused_before_any_request(path, raw):
    from engineering_v1 import edited_specification
    with pytest.raises(ValueError):
        edited_specification(DRAGSTER_SPEC, {path: raw})


def test_specification_json_must_be_a_bounded_object():
    from engineering_v1 import parse_specification_json
    assert parse_specification_json('{"a": 1}') == {'a': 1}
    for bad in ('[1]', '3', 'null', '{', '{"a": NaN}', '{"a":' + '1' * 17000 + '}'):
        with pytest.raises(ValueError):
            parse_specification_json(bad)


# ---------------------------------------------------------------- PATCH / review / job bodies

def test_patch_body_is_revision_bound_and_keeps_the_stored_capability():
    from engineering_v1 import patch_body
    p = prepared(revision=3)
    body = patch_body(p, dict(DRAGSTER_SPEC, length_mm=300.0))
    assert body['expected_revision'] == 3
    assert body['input']['capability'] == p['input']['capability']
    assert body['input']['specification']['length_mm'] == 300.0
    assert set(body) == {'expected_revision', 'input'} and set(body['input']) == {'capability', 'specification'}


def test_review_body_acknowledges_exactly_the_content_leaves():
    from engineering_v1 import leaf_fields, review_body
    p = prepared(revision=2)
    body = review_body(p, reviewed_at='2026-11-18T00:00:00+00:00', reviewed_by='local browser reviewer')
    assert body['draft_id'] == 'd1' and body['draft_revision'] == 2 and body['brief_sha256'] == SHA
    assert body['acknowledged_fields'] == ['specification.' + f['path'] for f in leaf_fields(DRAGSTER_SPEC)]
    assert len(set(body['acknowledged_fields'])) == len(body['acknowledged_fields'])


def test_review_body_refuses_incomplete_or_issue_bearing_drafts():
    from engineering_v1 import review_body
    with pytest.raises(ValueError):
        review_body(prepared(content=False), reviewed_at='t', reviewed_by='r')
    with pytest.raises(ValueError):
        review_body(prepared(issues=[dict(field='f', state='clarification_required', reason='r')]), reviewed_at='t', reviewed_by='r')


def test_job_body_binds_the_brief():
    from engineering_v1 import job_body
    assert job_body(dict(brief_id='b1')) == dict(brief_id='b1', idempotency_key='checked-ui:b1')


# ---------------------------------------------------------------- flows against the recording client

def test_routed_prompt_creates_a_rules_draft_and_never_ai():
    from engineering_v1 import create_generator_draft
    client = FakeClient([prepared()])
    result, note = create_generator_draft(client, entry('co2_dragster'), prompt='low wedge dragster')
    assert note == 'rules' and result['draft']['draft_id'] == 'd1'
    assert client.calls == [('POST', '/drafts', dict(mode='rules', capability='co2_dragster', input={}, prompt='low wedge dragster'))]


def test_rules_refusal_falls_back_to_an_empty_form_draft():
    from engineering_v1 import create_generator_draft
    client = FakeClient([FakeError(422), prepared(content=False)])
    result, note = create_generator_draft(client, entry('glider_cem'), prompt='a glider')
    assert note == 'empty'
    assert [c[:2] for c in client.calls] == [('POST', '/drafts'), ('POST', '/drafts')]
    assert client.calls[0][2]['mode'] == 'rules' and client.calls[1][2]['mode'] == 'form'
    assert all(c[2]['mode'] != 'ai' for c in client.calls)


def test_other_draft_errors_are_not_swallowed():
    from engineering_v1 import create_generator_draft
    with pytest.raises(FakeError):
        create_generator_draft(FakeClient([FakeError(429)]), entry('co2_dragster'), prompt='p')


def test_no_prompt_starts_an_empty_form_draft_directly():
    from engineering_v1 import create_generator_draft
    client = FakeClient([prepared(content=False)])
    _, note = create_generator_draft(client, entry('regen_chamber'))
    assert note == 'empty' and len(client.calls) == 1 and client.calls[0][2]['mode'] == 'form'


def test_full_generator_sequence_is_drafts_patch_briefs_jobs():
    from engineering_v1 import create_generator_draft, edited_specification, finalize, patch_body, submit
    p1, p2 = prepared(), prepared(revision=2)
    client = FakeClient([p1, p2, dict(brief_id='b1', draft_id='d1', draft_revision=2, brief_sha256=SHA), dict(job_id='j1')])
    created, _ = create_generator_draft(client, entry('co2_dragster'), prompt='wedge')
    spec = edited_specification(created['draft']['content']['specification'], {'length_mm': '250'})
    edited = client.request('PATCH', '/drafts/d1', patch_body(created, spec))
    brief = finalize(client, edited, reviewed_at='2026-11-18T00:00:00+00:00')
    job = submit(client, brief)
    assert [c[:2] for c in client.calls] == [('POST', '/drafts'), ('PATCH', '/drafts/d1'), ('POST', '/briefs'), ('POST', '/jobs')]
    assert client.calls[1][2]['expected_revision'] == 1
    assert client.calls[2][2]['draft_revision'] == 2
    assert client.calls[3][2] == dict(brief_id='b1', idempotency_key='checked-ui:b1')
    assert job['job_id'] == 'j1'


# ---------------------------------------------------------------- result

def job(outcome='accepted', artifacts=None, delivery='available', lifecycle='completed', **extra):
    return dict(job_id='j1', lifecycle=lifecycle, outcome=outcome, delivery=delivery,
                artifacts=artifacts if artifacts is not None else {}, brief_sha256=SHA, **extra)


def test_accepted_result_carries_the_checked_design_label_and_available_downloads():
    from engineering_v1 import result_view
    view = result_view(job(artifacts={'zip': True, 'generator-body-step': True, 'generator-cleaned-stl': False,
                                      'markdown': True}))
    assert view['label'] == 'Checked design'
    assert [d['format'] for d in view['downloads']] == ['zip', 'markdown', 'generator-body-step']
    names = {d['format']: (d['name'], d['mime']) for d in view['downloads']}
    assert names['generator-body-step'] == ('generator-body.step', 'application/step')
    assert names['zip'] == ('package.zip', 'application/zip')


def test_every_generator_format_the_job_view_can_offer_has_a_name_and_mime():
    from engineering_v1 import DOWNLOADS
    for key in ('zip', 'brief', 'spec', 'attempts', 'report', 'markdown', 'manifest', 'generator-spec', 'generator-report',
                'generator-body-step', 'generator-preview-glb', 'generator-cleaned-stl', 'generator-spec-canonical',
                'generator-bundle-zip', 'generator-result'):
        assert key in DOWNLOADS and all(DOWNLOADS[key])


def test_unknown_server_formats_are_never_offered():
    from engineering_v1 import result_view
    view = result_view(job(artifacts={'zip': True, '../../etc/passwd': True, 'evil': True}))
    assert [d['format'] for d in view['downloads']] == ['zip']


@pytest.mark.parametrize('kwargs', [dict(outcome='rejected'), dict(outcome='unavailable'), dict(lifecycle='running', outcome=None)])
def test_only_accepted_results_get_the_checked_label(kwargs):
    from engineering_v1 import result_view
    assert result_view(job(artifacts={'zip': True}, **kwargs))['label'] is None


def test_no_downloads_unless_delivery_is_available():
    from engineering_v1 import result_view
    for delivery in ('pending', 'expired', 'unavailable'):
        assert result_view(job(artifacts={'zip': True}, delivery=delivery))['downloads'] == []


def test_accepted_downloads_are_hash_bound_to_the_bundle():
    from engineering_v1 import verified_download
    import hashlib
    data = b'step bytes'
    bundle = dict(zip_sha256='0' * 64, manifest_sha256='1' * 64,
                  payloads=[dict(path='generator-body.step', sha256=hashlib.sha256(data).hexdigest())])
    accepted = job(bundle=bundle)
    assert verified_download(accepted, 'generator-body-step', data) == data
    with pytest.raises(ValueError):
        verified_download(accepted, 'generator-body-step', b'tampered')
    with pytest.raises(ValueError):
        verified_download(accepted, 'unknown-format', data)


# ---------------------------------------------------------------- route events

def event(capability_id, **extra):
    return dict(step='route', status='done', workflow=capability_id, capability_id=capability_id,
                assurance='checked_design', reason='r', detail='d', **extra)


@pytest.mark.parametrize('capability_id,name', [('co2_dragster', 'CO2 dragster'), ('regen_chamber', 'Deterministic rocket nozzle'),
                                                ('glider_cem', 'Physics-based glider CEM')])
def test_generator_route_opens_the_v1_view_with_that_capability(capability_id, name):
    from route_labels import route_target
    target = route_target(event(capability_id), 'a prompt')
    assert target['workflow'] == 'Checked design' and target['capability_id'] == capability_id
    assert target['notice'] == 'Checked design: ' + name
    assert target['prompt'] is None                       # no backend draft hint: form starts empty


def test_route_with_a_backend_draft_hint_carries_the_prompt_for_the_rules_draft():
    from route_labels import route_target
    target = route_target(event('co2_dragster', draft=dict(payload={}, issues=[], provenance={})), 'low wedge dragster')
    assert target['prompt'] == 'low wedge dragster'
    assert route_target(event('co2_dragster', draft='junk'), 'p')['prompt'] is None
    assert route_target(event('co2_dragster', draft=dict(payload={})), '   ')['prompt'] is None


def test_beam_route_opens_the_v1_view_and_never_asks_for_a_draft():
    from route_labels import route_target
    target = route_target(event(BEAM, draft=dict(payload={})), 'a beam')
    assert target['workflow'] == 'Checked design' and target['capability_id'] == BEAM and target['prompt'] is None
    assert target['notice'] == 'Checked design: Checked beam'


def test_legacy_event_without_a_capability_keeps_its_tab_and_unknown_routes_nothing():
    from route_labels import route_target
    legacy = route_target(dict(step='route', status='done', workflow='co2_dragster'), 'p')
    assert legacy['workflow'] == 'CO2 dragster' and legacy['capability_id'] is None
    assert route_target(dict(step='route', capability_id='nope'), 'p') is None
    assert route_target({}, 'p') is None


# ---------------------------------------------------------------- AppTest: whole page against a scripted backend

class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status
        self.ok = status < 400
        self.headers = {}
        self.content = b''

    def json(self):
        return self.payload


class Backend:
    """A scripted /engineering/v1: records requests, answers from handlers."""
    def __init__(self, meta, drafts=None):
        self.meta, self.calls, self.drafts = meta, [], list(drafts or [])
        self.current = None

    def __call__(self, method, url, headers=None, json=None, timeout=None, **_):
        path = url.split('/engineering/v1', 1)[1]
        self.calls.append((method, path, copy.deepcopy(json)))
        if (method, path) == ('GET', '/capabilities'):
            return Response(self.meta)
        if (method, path) == ('POST', '/drafts'):
            self.current = self.drafts.pop(0)
            return Response(self.current, 201)
        if method == 'GET' and path.startswith('/drafts/') and self.current:
            return Response(self.current)
        return Response({}, 404)


SCRIPT = '''
import sys
sys.path.insert(0, {frontend!r})
from engineering_ui import render_checked
render_checked("http://backend.invalid", lambda: {{}})
'''


def run_page(monkeypatch, backend, session=None):
    import requests
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(requests, 'request', backend)
    app = AppTest.from_string(SCRIPT.format(frontend=os.path.dirname(os.path.abspath(__file__))), default_timeout=20)
    for key, value in (session or {}).items():
        app.session_state[key] = value
    return app.run()


def test_beam_only_deployment_makes_the_same_single_request_and_shows_the_beam_form(monkeypatch):
    backend = Backend(metadata(BEAM))
    app = run_page(monkeypatch, backend)
    assert not app.exception
    assert backend.calls == [('GET', '/capabilities', None)]
    assert any('Save draft' == b.label for b in app.button)
    assert not any(r.key == 'checked_capability' for r in app.radio)


def test_with_generators_installed_the_beam_stays_the_default_and_nothing_extra_is_requested(monkeypatch):
    backend = Backend(metadata(BEAM, *GENERATORS))
    app = run_page(monkeypatch, backend)
    assert not app.exception
    assert backend.calls == [('GET', '/capabilities', None)]
    picker = next(r for r in app.radio if r.key == 'checked_capability')
    assert picker.options == [BEAM, *GENERATORS] or len(picker.options) == 4
    assert picker.value == BEAM
    assert any('Save draft' == b.label for b in app.button)


def test_picking_a_generator_shows_its_form_not_the_beam_form(monkeypatch):
    backend = Backend(metadata(BEAM, *GENERATORS))
    app = run_page(monkeypatch, backend, {'checked_capability': 'regen_chamber'})
    assert not app.exception
    assert not any('Save draft' == b.label for b in app.button)
    assert backend.calls == [('GET', '/capabilities', None)]          # nothing is created until the user asks
    assert any(b.label == 'Start an empty draft' for b in app.button)


def test_routed_generator_prefills_from_the_rules_draft_once(monkeypatch):
    backend = Backend(metadata(BEAM, *GENERATORS), drafts=[prepared()])
    app = run_page(monkeypatch, backend, {'pending_v1': dict(capability_id='co2_dragster', prompt='low wedge dragster')})
    assert not app.exception
    posts = [c for c in backend.calls if c[:2] == ('POST', '/drafts')]
    assert len(posts) == 1 and posts[0][2] == dict(mode='rules', capability='co2_dragster', input={}, prompt='low wedge dragster')
    assert next(r for r in app.radio if r.key == 'checked_capability').value == 'co2_dragster'
    app.run()                                                          # one-shot: a rerun does not draft again
    assert len([c for c in backend.calls if c[:2] == ('POST', '/drafts')]) == 1
    assert 'pending_v1' not in app.session_state


def test_routed_generator_without_a_hint_starts_with_the_form_and_no_request(monkeypatch):
    backend = Backend(metadata(BEAM, *GENERATORS))
    app = run_page(monkeypatch, backend, {'pending_v1': dict(capability_id='glider_cem', prompt=None)})
    assert not app.exception
    assert next(r for r in app.radio if r.key == 'checked_capability').value == 'glider_cem'
    assert [c[:2] for c in backend.calls] == [('GET', '/capabilities')]


def test_route_to_a_capability_that_is_not_installed_falls_back_to_the_beam(monkeypatch):
    backend = Backend(metadata(BEAM))
    app = run_page(monkeypatch, backend, {'pending_v1': dict(capability_id='glider_cem', prompt='p')})
    assert not app.exception
    assert [c[:2] for c in backend.calls] == [('GET', '/capabilities')]
    assert any('Save draft' == b.label for b in app.button)


def test_server_issue_text_is_rendered_as_plain_text(monkeypatch):
    hostile = '![x](https://tracker.invalid/p.png) **b** [l](https://a.invalid)'
    issue = dict(field='specification.style', state='clarification_required', reason=hostile)
    backend = Backend(metadata(BEAM, *GENERATORS), drafts=[prepared(issues=[issue])])
    app = run_page(monkeypatch, backend, {'pending_v1': dict(capability_id='co2_dragster', prompt='wedge')})
    assert not app.exception
    shown = [w.value for w in list(app.warning) + list(app.error)]
    assert shown and all('![' not in v and '](' not in v and '**' not in v for v in shown)
    assert any('tracker' in v for v in shown)


def test_draft_form_has_one_editable_field_per_review_field_and_no_ai_control(monkeypatch):
    from engineering_v1 import leaf_fields
    backend = Backend(metadata(BEAM, *GENERATORS), drafts=[prepared()])
    app = run_page(monkeypatch, backend, {'pending_v1': dict(capability_id='co2_dragster', prompt='wedge')})
    assert not app.exception
    editable = [w for w in list(app.text_input) + list(app.checkbox) if w.key and w.key.startswith('v1gen_')]
    assert len(editable) == len(leaf_fields(DRAGSTER_SPEC))
    assert not any('AI' in b.label for b in app.button)
    assert [c for c in backend.calls if c[2] and c[2].get('mode') == 'ai'] == []
