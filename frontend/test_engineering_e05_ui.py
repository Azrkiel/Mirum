"""Unit tests for the AI-entry helpers of engineering_v1 (pure; no network)."""
import pytest

import engineering_v1 as ev

GENERATORS = ('co2_dragster', 'regen_chamber', 'glider_cem')


class FakeError(ValueError):
    def __init__(self, status, body=None):
        super().__init__('boom')
        self.status = status
        self.body = body


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.error is not None:
            raise self.error
        return self.result


def entry(cid='co2_dragster', claim=None):
    e = dict(id=cid, version='1', label=cid, available=True,
             numerical=dict(version='n1'), resources=dict(version='r1'))
    if claim: e['claim'] = claim
    return e


def prepared(*, content=True, issues=(), clarifications=(), origins=(), mode='ai'):
    draft = dict(draft_id='d1', revision=1, clarifications=list(clarifications),
                 content=dict(specification=dict(a=1, b=2)) if content else None,
                 provenance=dict(input_mode=mode,
                                 field_origins=[dict(field=f, origin=o, reference=None) for f, o in origins]))
    return dict(draft=draft, issues=[dict(state=s) for s in issues], brief_sha256='x')


def test_ai_enabled_and_available():
    assert ev.ai_enabled({'ai_enabled': True}) is True
    assert ev.ai_enabled({'ai_enabled': 1}) is False
    assert ev.ai_enabled({}) is False
    assert ev.ai_enabled(None) is False
    assert ev.ai_enabled([True]) is False
    assert ev.ai_available({'ai_available': True}) is True
    assert ev.ai_available({'ai_available': 'yes'}) is False
    assert ev.ai_available({'ai_enabled': True}) is False
    assert ev.ai_available('x') is False


@pytest.mark.parametrize('cid', GENERATORS)
def test_ai_draft_body(cid):
    assert ev.ai_draft_body(entry(cid), 'a fast car') == dict(mode='ai', capability=cid, input={}, prompt='a fast car')
    assert ev.ai_draft_body(entry(cid), 'x' * 2000)['prompt'] == 'x' * 2000


def test_ai_draft_body_errors():
    with pytest.raises(ValueError, match='The beam uses its own form'):
        ev.ai_draft_body(entry(ev.BEAM_ID), 'p')
    for bad in ('', '   ', None, 5):
        with pytest.raises(ValueError, match='Describe the request'):
            ev.ai_draft_body(entry(), bad)
    with pytest.raises(ValueError, match='Request exceeds 2000 characters'):
        ev.ai_draft_body(entry(), 'x' * 2001)


def test_initial_draft_body_never_ai():
    assert ev.initial_draft_body(entry(), prompt='p')['mode'] == 'rules'
    assert ev.initial_draft_body(entry())['mode'] == 'form'


def test_ai_origins_and_tolerance():
    p = prepared(origins=[('specification.a', 'ai_proposed'), ('specification.b', 'policy')])
    assert ev.ai_origins(p) == {'specification.a': 'ai_proposed', 'specification.b': 'policy'}
    for bad in ({}, {'draft': {}}, {'draft': {'provenance': None}}, {'draft': {'provenance': {'field_origins': 'x'}}},
                {'draft': {'provenance': {'field_origins': [1, None, {'field': 3}]}}}, None, 'x'):
        assert ev.ai_origins(bad) == {}


def test_is_ai_draft():
    assert ev.is_ai_draft(prepared(mode='ai')) is True
    assert ev.is_ai_draft(prepared(mode='rules')) is False
    assert ev.is_ai_draft({}) is False


def test_unverified_fields_order():
    p = prepared(origins=[('specification.z', 'ai_proposed'), ('specification.m', 'user'),
                          ('specification.a', 'ai_proposed'), ('specification.p', 'policy')])
    assert ev.unverified_fields(p) == ('specification.z', 'specification.a')
    assert ev.unverified_fields({}) == ()


def test_review_ack_required():
    assert ev.review_ack_required(prepared(origins=[('specification.a', 'ai_proposed')])) is True
    assert ev.review_ack_required(prepared(origins=[('specification.a', 'policy')])) is False
    assert ev.review_ack_required(prepared()) is False


def test_field_label():
    origins = {'specification.a': 'ai_proposed', 'specification.b': 'policy', 'specification.c': 'user'}
    assert ev.field_label('a', origins) == 'a (AI-proposed, unverified)'
    assert ev.field_label('b', origins) == 'b (policy default)'
    assert ev.field_label('c', origins) == 'c'
    assert ev.field_label('d', origins) == 'd'
    assert ev.field_label('a', {}) == 'a'


def test_ai_status():
    assert ev.ai_status(prepared()) == 'proposed'
    assert ev.ai_status(prepared(content=False, clarifications=['q?'])) == 'clarification_required'
    assert ev.ai_status(prepared(content=False)) == 'clarification_required'
    assert ev.ai_status(prepared(issues=['clarification_required'])) == 'clarification_required'
    assert ev.ai_status(prepared(content=False, issues=['unsupported', 'clarification_required'])) == 'unsupported'
    assert ev.ai_status(prepared(issues=['unsupported'])) == 'unsupported'


def test_ai_failure_message_never_echoes():
    unavailable = FakeError(503, dict(state='unavailable', reason='SECRET-PROVIDER-TEXT'))
    msg = ev.ai_failure_message(unavailable)
    assert msg == 'AI preparation unavailable; typed form remains available.'
    assert 'SECRET' not in msg
    other = FakeError(503, dict(state='other', detail='SECRET-PROVIDER-TEXT'))
    assert ev.ai_failure_message(other) == 'AI draft preparation is not available; the typed form remains available.'
    assert ev.ai_failure_message(FakeError(503)) == 'AI draft preparation is not available; the typed form remains available.'
    assert ev.ai_failure_message(ValueError('SECRET-PROVIDER-TEXT')) == \
        'AI draft preparation is not available; the typed form remains available.'


@pytest.mark.parametrize('cid', GENERATORS)
@pytest.mark.parametrize('kind', ['proposed', 'clarification_required', 'unsupported'])
def test_propose_ai_draft_ok(cid, kind):
    p = {'proposed': prepared(origins=[('specification.a', 'ai_proposed')]),
         'clarification_required': prepared(content=False, clarifications=['which?']),
         'unsupported': prepared(content=False, issues=['unsupported'])}[kind]
    client = FakeClient(result=p)
    out = ev.propose_ai_draft(client, entry(cid), 'make it')
    assert out == dict(status=kind, prepared=p, message=None)
    assert client.calls == [('POST', '/drafts', dict(mode='ai', capability=cid, input={}, prompt='make it'))]


@pytest.mark.parametrize('cid', GENERATORS)
def test_propose_ai_draft_unavailable(cid):
    client = FakeClient(error=FakeError(503, dict(state='unavailable', prepared=None, reason='SECRET-PROVIDER-TEXT')))
    out = ev.propose_ai_draft(client, entry(cid), 'make it')
    assert out == dict(status='unavailable', prepared=None, message='AI preparation unavailable; typed form remains available.')
    assert 'SECRET' not in str(out)


def test_propose_ai_draft_unavailable_with_prepared_dict():
    p = prepared()
    out = ev.propose_ai_draft(FakeClient(error=FakeError(503, dict(state='unavailable', prepared=p))), entry(), 'x')
    assert out['status'] == 'unavailable' and out['prepared'] is p


def test_propose_ai_draft_reraises():
    for status in (422, 429, 500):
        with pytest.raises(FakeError):
            ev.propose_ai_draft(FakeClient(error=FakeError(status)), entry(), 'x')


def test_propose_ai_draft_beam_refused():
    client = FakeClient(result=prepared())
    with pytest.raises(ValueError, match='The beam uses its own form'):
        ev.propose_ai_draft(client, entry(ev.BEAM_ID), 'x')
    assert client.calls == []


def test_glider_wording_has_no_accepted_or_checked():
    g = entry('glider_cem', claim='screening_only')
    texts = [ev.accepted_label(g), ev.result_heading(g)]
    for job in (dict(outcome='accepted'), dict(outcome='rejected'), dict(lifecycle='cancelled'), dict(outcome='error')):
        texts += [ev.outcome_message(g, job)[1], ev.state_line(g, job)]
    for t in texts:
        assert 'Accepted' not in t and 'Checked design' not in t


# ---------------------------------------------------------------- AppTest: AI entry mode in the generator view

import copy
import os


class _Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status
        self.ok = status < 400
        self.headers = {}
        self.content = b''

    def json(self):
        return self.payload


class Backend:
    """Scripted /engineering/v1. drafts: queue of payloads or (status, body) tuples answered to POST /drafts."""
    def __init__(self, meta, drafts=()):
        self.meta, self.calls, self.drafts, self.current = meta, [], list(drafts), None

    def __call__(self, method, url, headers=None, json=None, timeout=None, **_):
        path = url.split('/engineering/v1', 1)[1]
        self.calls.append((method, path, copy.deepcopy(json)))
        if (method, path) == ('GET', '/capabilities'):
            return _Response(self.meta)
        if (method, path) == ('POST', '/drafts'):
            item = self.drafts.pop(0)
            if isinstance(item, tuple):
                return _Response(item[1], item[0])
            self.current = item
            return _Response(item, 201)
        if method == 'GET' and path.startswith('/drafts/') and self.current:
            return _Response(self.current)
        return _Response({}, 404)


_SCRIPT = '''
import sys
sys.path.insert(0, {frontend!r})
from engineering_ui import render_checked
render_checked("http://backend.invalid", lambda: {{}})
'''
_SPEC = {'schema_version': 2, 'units': 'mm', 'length_mm': 200.0, 'style': 'wedge'}


def _meta(cid, ai):
    e = dict(id=cid, version='1.0.0', label=cid, available=True, numerical=dict(version='n2'),
             resources=dict(version='r2'), review_fields=[], review_fields_prefix='specification.',
             selection_rule='x', limitations=[], claim='screening_only' if cid == 'glider_cem' else None)
    beam = dict(id=ev.BEAM_ID, version='1.0.0', label='Checked beam', available=True, review_fields=['length_mm'],
                numerical=dict(version='n1'), resources=dict(version='r1'), required_checks=['c1'])
    return dict(capabilities=[beam, e], review_fields=['length_mm'], ai_enabled=ai, ai_available=ai)


def _page(monkeypatch, backend, cid, session=None):
    import requests
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(requests, 'request', backend)
    app = AppTest.from_string(_SCRIPT.format(frontend=os.path.dirname(os.path.abspath(__file__))), default_timeout=20)
    app.session_state['checked_capability'] = cid
    for k, v in (session or {}).items():
        app.session_state[k] = v
    return app.run()


def _ai_draft(cid, *, content=True, issues=(), clarifications=(), origins=()):
    cap = dict(id=cid, version='1.0.0', numerical_policy_version='n2', resource_policy_version='r2')
    body = dict(schema_version='engineering_generator_brief_v1', capability=cap, specification=copy.deepcopy(_SPEC),
                required_checks=['a']) if content else None
    return dict(draft=dict(draft_id='d1', revision=1, content=body, clarifications=list(clarifications),
                           provenance=dict(input_mode='ai',
                                           field_origins=[dict(field=f, origin=o, reference=None) for f, o in origins])),
                input=dict(capability=cap, specification=copy.deepcopy(_SPEC) if content else None),
                issues=list(issues), brief_sha256='a' * 64 if content else None)


def _propose(app, cid, text='a fast car'):
    app.radio(key='v1gen_entry_' + cid).set_value('AI-assisted draft').run()
    app.text_area(key='v1gen_ai_prompt_' + cid).set_value(text).run()
    next(b for b in app.button if b.key == 'v1gen_ai_' + cid).click().run()
    return app


def _texts(app):
    return [e.value for e in list(app.warning) + list(app.error) + list(app.caption) + list(app.text) + list(app.info)]


def _spec_fields(app):
    return [w for w in list(app.text_input) + list(app.checkbox) if w.key and w.key.startswith('v1gen_') and '_d1_' in w.key]


def test_flag_off_shows_no_entry_radio_and_no_ai_button(monkeypatch):
    app = _page(monkeypatch, Backend(_meta('co2_dragster', False)), 'co2_dragster')
    assert not app.exception
    assert not any(r.label == 'Entry method' for r in app.radio)
    assert not any('AI' in b.label for b in app.button)
    assert any(b.label == 'Start an empty draft' for b in app.button)


@pytest.mark.parametrize('cid', GENERATORS)
def test_flag_on_shows_entry_radio(monkeypatch, cid):
    app = _page(monkeypatch, Backend(_meta(cid, True)), cid)
    assert not app.exception
    radio = next(r for r in app.radio if r.label == 'Entry method')
    assert radio.options == ['Typed form', 'AI-assisted draft'] and radio.value == 'Typed form'
    assert any(b.label == 'Start an empty draft' for b in app.button)


def test_unavailable_ai_caption(monkeypatch):
    meta = _meta('co2_dragster', True)
    meta['ai_available'] = False
    app = _page(monkeypatch, Backend(meta), 'co2_dragster')
    assert any('AI is unavailable. The typed form works without provider credentials.' in c.value for c in app.caption)


def test_ai_503_unavailable_keeps_typed_form_and_adopts_nothing(monkeypatch):
    backend = Backend(_meta('co2_dragster', True),
                      drafts=[(503, dict(state='unavailable', reasons=['SECRET'], prepared=None))])
    app = _page(monkeypatch, backend, 'co2_dragster')
    _propose(app, 'co2_dragster')
    assert not app.exception
    assert any('typed form remains available' in e.value for e in app.error)
    assert not any('SECRET' in t for t in _texts(app))
    assert not _spec_fields(app)
    assert not any(b.label == 'Finalize reviewed brief' for b in app.button)
    assert backend.calls[-1] == ('POST', '/drafts', dict(mode='ai', capability='co2_dragster', input={}, prompt='a fast car'))


def test_proposed_draft_needs_both_acknowledgements(monkeypatch):
    cid = 'co2_dragster'
    backend = Backend(_meta(cid, True), drafts=[_ai_draft(cid, origins=[('specification.length_mm', 'ai_proposed'),
                                                                        ('specification.style', 'policy')])])
    app = _page(monkeypatch, backend, cid)
    _propose(app, cid)
    assert not app.exception
    assert any('AI-proposed values are unverified. Review and edit every marked field before finalizing.' in w.value
               for w in app.warning)
    assert any('AI proposed a draft.' in c.value for c in app.caption)
    labels = [w.label.replace(chr(92), '') for w in app.text_input]
    assert 'length_mm (AI-proposed, unverified)' in labels and 'style (policy default)' in labels
    assert any('specification.length_mm' in t.replace(chr(92), '') for t in _texts(app))

    def final():
        return next(b for b in app.button if b.key == 'v1gen_final_' + cid)
    assert final().disabled
    next(c for c in app.checkbox if c.key.startswith('gen_ack_')).set_value(True).run()
    assert final().disabled
    next(c for c in app.checkbox if c.key.startswith('gen_aiack_')).set_value(True).run()
    assert not final().disabled


def test_ai_draft_without_ai_fields_has_no_extra_ack(monkeypatch):
    cid = 'co2_dragster'
    app = _page(monkeypatch, Backend(_meta(cid, True), drafts=[_ai_draft(cid, origins=[('specification.style', 'policy')])]), cid)
    _propose(app, cid)
    assert not any(c.key.startswith('gen_aiack_') for c in app.checkbox)


@pytest.mark.parametrize('cid', GENERATORS)
def test_clarification_required_renders_message_and_no_fields(monkeypatch, cid):
    draft = _ai_draft(cid, content=False, clarifications=['Which wheel size?'])
    app = _page(monkeypatch, Backend(_meta(cid, True), drafts=[draft]), cid)
    _propose(app, cid)
    assert not app.exception
    assert any('AI needs clarification; no values were proposed.' in c.value for c in app.caption)
    assert any('Which wheel size?' in t for t in _texts(app))
    assert not _spec_fields(app)
    assert not any('Accepted' in t or 'Checked design' in t for t in _texts(app))


@pytest.mark.parametrize('cid', GENERATORS)
def test_unsupported_renders_message_and_no_fields(monkeypatch, cid):
    draft = _ai_draft(cid, content=False, issues=[dict(field='specification', state='unsupported', reason='nope')])
    app = _page(monkeypatch, Backend(_meta(cid, True), drafts=[draft]), cid)
    _propose(app, cid)
    assert not app.exception
    assert any('This request is not supported; no values were proposed.' in c.value for c in app.caption)
    assert not _spec_fields(app)
    assert not any('Accepted' in t or 'Checked design' in t for t in _texts(app))


def test_glider_ai_screens_never_say_accepted(monkeypatch):
    cid = 'glider_cem'
    app = _page(monkeypatch, Backend(_meta(cid, True), drafts=[_ai_draft(cid, origins=[('specification.style', 'ai_proposed')])]), cid)
    _propose(app, cid)
    assert not app.exception
    assert not any('Accepted' in t or 'Checked design' in t for t in _texts(app))


# ------------------------------------------------- the job's own claim wins over a capability entry without one

def _bare_entry(capability_id):
    return dict(id=capability_id, version='1.0.0')


@pytest.mark.parametrize('capability_id,claim,label,heading,line', [
    ('co2_dragster', 'geometry_checks_only', 'Geometry checks passed', 'Geometry check result', 'geometry checks passed'),
    ('regen_chamber', 'geometry_checks_only', 'Geometry checks passed', 'Geometry check result', 'geometry checks passed'),
    ('glider_cem', 'screening_only', 'Screened', 'Screening result', 'screened'),
])
def test_job_claim_labels_a_capability_entry_that_carries_none(capability_id, claim, label, heading, line):
    from engineering_v1 import accepted_label, result_heading, result_view, state_line
    entry = _bare_entry(capability_id)
    job = dict(outcome='accepted', delivery='available', lifecycle='completed', claim=claim, artifacts={})
    assert accepted_label(entry, job) == label
    assert result_heading(entry, job) == heading
    assert result_view(job, entry)['label'] == label
    assert state_line(entry, job).endswith('engineering outcome: ' + line)


def test_unknown_job_claim_falls_back_to_the_entry_then_the_weakest():
    from engineering_v1 import claim_of
    assert claim_of(dict(id='co2_dragster', claim='geometry_checks_only'), dict(claim='bogus')) == 'geometry_checks_only'
    assert claim_of(_bare_entry('co2_dragster'), dict(claim='bogus')) == 'screening_only'


def test_attempt_rows_follow_the_claim_and_never_read_accepted_for_generators():
    """#111 review LOW-2: the attempts table relabels 'accepted' like the state line."""
    from engineering_v1 import attempt_outcome
    passing = {'outcome': 'accepted'}
    assert attempt_outcome({'claim': 'screening_only'}, {}, passing) == 'screened'
    assert attempt_outcome({'claim': 'geometry_checks_only'}, {}, passing) == 'geometry checks passed'
    assert attempt_outcome({'claim': 'geometry_checks_only'}, {}, {'outcome': 'rejected'}) == 'rejected'
