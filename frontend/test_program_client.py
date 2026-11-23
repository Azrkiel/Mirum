"""Pure tests for frontend/program_client.py with a monkeypatched requests.request."""
import hashlib
import json

import pytest
import requests

from frontend import program_client as pc

PID = 'a' * 32
RID = 'b' * 32
OTHER = 'c' * 32
SHA = 'd' * 64
TOKEN = 'secret-token-value'


class Resp:
    def __init__(self, status=200, body=b'{}', headers=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = headers or {}


class Recorder:
    def __init__(self, *responses):
        self.calls = []
        self.responses = list(responses) or [Resp()]

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses[0] if len(self.responses) == 1 else self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(monkeypatch, *responses):
    rec = Recorder(*responses)
    monkeypatch.setattr(requests, 'request', rec)
    return pc.ProgramClient('http://backend:8000/', lambda: {'Authorization': 'Bearer ' + TOKEN}), rec


def envelope(**over):
    base = dict(api_version='mirum_interface_error_v0_1', kind='refused', code='requirements_changed',
                category='revision', message='The fixed requirements differ.', hint='Review them.',
                requirement_ids=['req_a'], retryable=False, next_action='re_review')
    base.update(over)
    return base


def test_constants_mirror_interface():
    assert pc.API_PREFIX == '/engineering/v1'
    assert pc.MEL_MEDIA_TYPE == 'application/vnd.mirum.mel'
    assert pc.HEADER_MAX_CHARS == {'X-Mirum-Source-Id': 128, 'X-Mirum-Expected-Parent': 32,
                                   'X-Mirum-Require-Requirements': 64}
    assert pc.MAX_JSON_RESPONSE_BYTES == 4 * 1024 * 1024
    assert len(pc.EXPORT_FORMATS) == 21


def test_no_ids_in_query_strings_and_no_redirects(monkeypatch):
    c, rec = client(monkeypatch)
    c.meta(); c.validate('x'); c.format('x'); c.plan('x'); c.create_program(); c.list_programs()
    c.program(PID); c.history(PID); c.journal(PID)
    c.stage(PID, 'x', expected_parent=RID, require_requirements_sha256=SHA)
    c.review(RID, SHA, ['length'], 'me'); c.run(RID); c.status(RID); c.cancel(RID); c.inspect(RID)
    c.compare(RID, OTHER); c.restore(RID, PID); c.rebuild(RID, PID); c.ai_edit(PID, 'use 5 mm', expected_parent=RID)
    assert len(rec.calls) == 19
    for method, url, kwargs in rec.calls:
        assert '?' not in url and '#' not in url
        assert kwargs['allow_redirects'] is False
        assert kwargs['timeout'] == (5, 100)
        assert url.startswith('http://backend:8000/engineering/v1/')
        assert TOKEN not in url and TOKEN not in str(kwargs.get('data', b''))


def test_token_only_from_headers_callable(monkeypatch):
    c, rec = client(monkeypatch)
    c.validate('x')
    headers = rec.calls[0][2]['headers']
    assert headers['Authorization'] == 'Bearer ' + TOKEN
    c2 = pc.ProgramClient('http://b', lambda: {}, session_request=rec)
    c2.meta()
    assert 'Authorization' not in rec.calls[-1][2]['headers']


def test_raw_mel_body_and_headers(monkeypatch):
    c, rec = client(monkeypatch)
    c.stage(PID, 'design x {}', expected_parent=RID, require_requirements_sha256=SHA, source_id='main.mir')
    method, url, kw = rec.calls[0]
    assert (method, url) == ('POST', 'http://backend:8000/engineering/v1/programs/%s/revisions' % PID)
    assert kw['data'] == b'design x {}'
    h = kw['headers']
    assert h['Content-Type'] == 'application/vnd.mirum.mel'
    assert h['X-Mirum-Source-Id'] == 'main.mir'
    assert h['X-Mirum-Expected-Parent'] == RID
    assert h['X-Mirum-Require-Requirements'] == SHA
    c.stage(PID, 'x', expected_parent=None)
    h = rec.calls[1][2]['headers']
    assert 'X-Mirum-Expected-Parent' not in h and 'X-Mirum-Require-Requirements' not in h


def test_header_and_id_validation_is_local(monkeypatch):
    c, rec = client(monkeypatch)
    for bad in (lambda: c.stage(PID, 'x', expected_parent='nothex'),
                lambda: c.stage(PID, 'x', expected_parent=RID, require_requirements_sha256='abc'),
                lambda: c.stage(PID, 'x', expected_parent=RID, source_id='a b'),
                lambda: c.stage(PID, 'x', expected_parent=RID, source_id='s' * 129),
                lambda: c.status('../x'), lambda: c.program(PID + '?a=1'), lambda: c.export(RID, 'nope')):
        with pytest.raises(pc.ProgramAPIError) as info:
            bad()
        assert info.value.kind in ('invalid_request',)
    with pytest.raises(pc.ProgramAPIError) as info:
        c.validate('a' * 65537)
    assert info.value.kind == 'too_large'
    assert rec.calls == []


def test_json_request_shapes(monkeypatch):
    c, rec = client(monkeypatch)
    c.review(RID, SHA, ['length', 'material.youngs_modulus_mpa'], 'Kevin')
    c.compare(RID, OTHER)
    c.ai_edit(PID, 'try 5 and 10 mm', expected_parent=RID)
    review, compare, ai = (json.loads(call[2]['data']) for call in rec.calls)
    assert review == {'api_version': 'mirum_program_api_v0_1', 'brief_sha256': SHA,
                      'acknowledged_fields': ['length', 'material.youngs_modulus_mpa'], 'reviewed_by': 'Kevin'}
    assert compare == {'api_version': 'mirum_program_api_v0_1', 'other_revision_id': OTHER}
    assert ai == {'api_version': 'mirum_program_api_v0_1', 'request': 'try 5 and 10 mm', 'expected_parent': RID}
    assert all(call[2]['headers']['Content-Type'] == 'application/json' for call in rec.calls)
    assert rec.calls[2][1].endswith('/programs/%s/ai-edits' % PID)
    with pytest.raises(pc.ProgramAPIError):
        c.ai_edit(PID, 'x' * 2001, expected_parent=RID)
    with pytest.raises(pc.ProgramAPIError):
        c.ai_edit(PID, '   ', expected_parent=RID)


def test_restore_rebuild_send_expected_parent_header(monkeypatch):
    c, rec = client(monkeypatch)
    c.restore(RID, OTHER)
    c.rebuild(RID, OTHER)
    for method, url, kw in rec.calls:
        assert method == 'POST'
        assert kw['headers']['X-Mirum-Expected-Parent'] == OTHER
    assert rec.calls[0][1].endswith('/revisions/%s/restore' % RID)
    assert rec.calls[1][1].endswith('/revisions/%s/rebuild' % RID)


def test_error_envelope_parsing(monkeypatch):
    span = dict(line=3, column=4, end_line=3, end_column=9)
    c, rec = client(monkeypatch, Resp(409, envelope(span=span, field='mode')))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.stage(PID, 'x', expected_parent=RID)
    e = info.value
    assert (e.status, e.kind, e.code, e.next_action) == (409, 'refused', 'requirements_changed', 're_review')
    assert e.span == span and e.field == 'mode' and e.requirement_ids == ('req_a',)
    assert e.retryable is False and e.retry_after is None
    assert str(e) == pc.KIND_MESSAGES['refused']
    assert 'fixed requirements differ' not in str(e)
    assert e.detail_message == 'The fixed requirements differ.'


def test_busy_retry_after_and_kinds(monkeypatch):
    c, rec = client(monkeypatch, Resp(429, envelope(kind='busy', code='capacity', category='capacity',
                                                    retryable=True, retry_after=7, requirement_ids=[])))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.run(RID)
    assert info.value.kind == 'busy' and info.value.retry_after == 7 and info.value.retryable
    for status, kind in ((401, 'unauthorized'), (404, 'not_found'), (410, 'expired'), (503, 'unavailable'),
                         (409, 'conflict'), (422, 'invalid_request'), (413, 'too_large'), (500, 'unavailable')):
        c, rec = client(monkeypatch, Resp(status, b'<html>server prose</html>'))
        with pytest.raises(pc.ProgramAPIError) as info:
            c.status(RID)
        assert info.value.kind == kind and 'server prose' not in str(info.value)
    c, rec = client(monkeypatch, Resp(503, envelope(kind='programs_disabled', code='programs_disabled',
                                                    category='service', next_action='contact_operator')))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.meta()
    assert info.value.kind == 'programs_disabled'


def test_hostile_envelope_fields_are_dropped(monkeypatch):
    bad = envelope(kind='bogus', code='x y!', category='nope', next_action='rm', span={'line': 'a'},
                   field='Bad Field', requirement_ids=['ok', 'not ok!', 5], message='caf' + chr(233), hint='h' * 241)
    c, rec = client(monkeypatch, Resp(409, bad))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.status(RID)
    e = info.value
    assert (e.kind, e.code, e.category, e.next_action, e.span, e.field) == ('conflict', '', None, 'none', None, None)
    assert e.requirement_ids == ('ok',) and e.detail_message == '' and e.detail_hint == ''


def test_response_cap_4_mib(monkeypatch):
    big = b'{"x":"' + b'a' * pc.MAX_JSON_RESPONSE_BYTES + b'"}'
    c, rec = client(monkeypatch, Resp(200, big))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.meta()
    assert info.value.kind == 'too_large'
    c, rec = client(monkeypatch, Resp(200, b'[1,2]'))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.meta()
    assert info.value.kind == 'unavailable'
    c, rec = client(monkeypatch, Resp(200, {'ok': True}))
    assert c.meta() == {'ok': True}


def test_redirect_not_followed(monkeypatch):
    c, rec = client(monkeypatch, Resp(302, b'', {'Location': 'http://evil/'}))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.status(RID)
    assert info.value.kind == 'unavailable' and info.value.code == 'unexpected_redirect'
    assert len(rec.calls) == 1


def test_unreachable(monkeypatch):
    c, rec = client(monkeypatch, requests.ConnectionError('boom http://secret-host'))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.validate('x')
    assert info.value.kind == 'unreachable' and 'secret-host' not in str(info.value)
    c, rec = client(monkeypatch, requests.Timeout('slow'))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.run(RID)
    assert info.value.kind == 'unreachable'


def test_export_verifies_digests(monkeypatch):
    body = b'glTF-bytes'
    digest = hashlib.sha256(body).hexdigest()
    c, rec = client(monkeypatch, Resp(200, body, {'X-Content-SHA256': digest}))
    assert c.export(RID, 'glb') == body
    assert c.export(RID, 'glb', expected_sha256=digest) == body
    assert rec.calls[0][1].endswith('/revisions/%s/artifacts/glb' % RID)
    for header, expected in ((digest, 'e' * 64), ('e' * 64, None), (None, None)):
        headers = {} if header is None else {'X-Content-SHA256': header}
        c, rec = client(monkeypatch, Resp(200, body, headers))
        with pytest.raises(pc.ProgramAPIError) as info:
            c.export(RID, 'glb', expected_sha256=expected)
        assert info.value.kind == 'integrity'


def test_export_error_envelope(monkeypatch):
    c, rec = client(monkeypatch, Resp(410, envelope(kind='expired', code='artifacts_expired',
                                                    category='revision', requirement_ids=[], next_action='restage')))
    with pytest.raises(pc.ProgramAPIError) as info:
        c.export(RID, 'zip')
    assert info.value.kind == 'expired' and info.value.code == 'artifacts_expired'
