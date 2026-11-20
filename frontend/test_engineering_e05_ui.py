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
