"""F08 parity suite, CLI and HTTP SDK section (task 4). Host-runnable and pure: an injected fake transport
records requests and returns canned server responses built from interface models; no sockets.

Task 6 extends this file with the cross-transport (facade / HTTP / MCP) parity, real-socket and native parts.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from engineering.program import client as C
from engineering.program import cli as K
from engineering.program import interface as api
from engineering.program import offline
from engineering.program.revisions import HashEquality, RevisionDiff

FIXTURES = Path(__file__).parent / 'fixtures' / 'program' / 'f08'
BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
BEAM = FIXTURES / 'beam.mir'
SHA = 'a' * 64
RID = '1' * 32
RID2 = '3' * 32
PID = '2' * 32
TOKEN = 'tok-SECRETVALUE-123'
URL = 'http://api.test'
STAMP = '2026-01-01T00:00:00Z'


def rev(state='staged', result_kind='pending', **kw):
    base = dict(revision_id=RID, program_id=PID, kind='edit', state=state, result_kind=result_kind,
                source_sha256=SHA, semantic_sha256=SHA, brief_sha256=SHA, fixed_requirements_sha256=SHA,
                lock_sha256=SHA, created_at=STAMP, updated_at=STAMP, artifacts='none', is_current=False)
    base.update(kw)
    return api.RevisionStatus(**base)


def body_of(model) -> bytes:
    return model.model_dump_json().encode('ascii')


class Fake:
    """Records requests; the handler returns (status, headers, body)."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        return self.handler(method, url, headers, body)


def const(model, status=200):
    return lambda *a: (status, {'content-type': 'application/json'}, body_of(model))


def err_handler(err):
    return lambda *a: (api.http_status(err), {'content-type': 'application/json'}, body_of(err))


def run_cli(argv, transport=None, env=None):
    out, err = io.StringIO(), io.StringIO()
    environ = {'MIRUM_API_URL': URL}
    environ.update(env or {})
    code = K.main(argv, transport=transport, environ=environ, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def beam(tmp_path):
    path = tmp_path / 'beam.mir'
    path.write_bytes(BEAM.read_bytes())
    return str(path)


def no_network(*_a):
    raise AssertionError('the transport must not be called')


# ------------------------------------------------------------------------------------ request shapes

DIFF = api.DiffResponse(diff=RevisionDiff(
    a=RID, b=RID2, hashes=HashEquality(source=True, semantic=True, brief=True, fixed_requirements=True, lock=True),
    requirements_changed=False))
SRC = b'program source bytes\n'

# name, call, method, path, status, response, extra headers, body check
SHAPES = [
    ('validate', lambda c: c.validate(SRC, 'a.mir'), 'POST', '/mel/validate', 200,
     api.ValidateResult(source_sha256=SHA, semantic_sha256=SHA, language_version='1', compiler_version='c'),
     {'X-Mirum-Source-Id': 'a.mir', 'Content-Type': api.MEL_MEDIA_TYPE}, SRC),
    ('format', lambda c: c.format(SRC), 'POST', '/mel/format', 200,
     api.FormatResult(formatted_source='x', formatted_sha256=SHA, unchanged=True),
     {'Content-Type': api.MEL_MEDIA_TYPE}, SRC),
    ('plan', lambda c: c.plan(SRC, 'p.mir'), 'POST', '/mel/plan', 200,
     api.PlanResult(semantic_sha256=SHA, mode='native', backend='occt', candidate_count=1),
     {'X-Mirum-Source-Id': 'p.mir', 'Content-Type': api.MEL_MEDIA_TYPE}, SRC),
    ('program_create', lambda c: c.program_create(), 'POST', '/programs', 201,
     api.ProgramCreated(program_id=PID), {}, b''),
    ('stage', lambda c: c.stage(PID, SRC, 's.mir', RID, SHA), 'POST', f'/programs/{PID}/revisions', 201,
     api.StageResponse(revision=rev()),
     {'X-Mirum-Source-Id': 's.mir', 'X-Mirum-Expected-Parent': RID, 'X-Mirum-Require-Requirements': SHA,
      'Content-Type': api.MEL_MEDIA_TYPE}, SRC),
    ('propose', lambda c: c.propose(PID, SRC, require_requirements_sha256=SHA), 'POST',
     f'/programs/{PID}/revisions', 201, api.StageResponse(revision=rev()),
     {'X-Mirum-Require-Requirements': SHA, 'Content-Type': api.MEL_MEDIA_TYPE}, SRC),
    ('review', lambda c: c.review(RID, SHA, ['geometry.length_mm'], 'me'), 'POST', f'/revisions/{RID}/review',
     200, api.RevisionResponse(revision=rev()), {'Content-Type': 'application/json'},
     {'api_version': api.PROGRAM_API_VERSION, 'brief_sha256': SHA, 'acknowledged_fields': ['geometry.length_mm'],
      'reviewed_by': 'me'}),
    ('run', lambda c: c.run(RID), 'POST', f'/revisions/{RID}/run', 200, api.RevisionResponse(revision=rev()), {}, b''),
    ('status', lambda c: c.status(RID), 'GET', f'/revisions/{RID}', 200, api.RevisionResponse(revision=rev()), {}, None),
    ('cancel', lambda c: c.cancel(RID), 'POST', f'/revisions/{RID}/cancel', 200,
     api.RevisionResponse(revision=rev()), {}, b''),
    ('inspect', lambda c: c.inspect(RID), 'GET', f'/revisions/{RID}/evidence', 200,
     api.InspectBundle(revision=rev(), evidence=api.EvidenceView(status='none')), {}, None),
    ('history', lambda c: c.history(PID), 'GET', f'/programs/{PID}/history', 200,
     api.HistoryView(program_id=PID, revisions=()), {}, None),
    ('diff', lambda c: c.diff(RID, RID2), 'POST', f'/revisions/{RID}/compare', 200, DIFF,
     {'Content-Type': 'application/json'}, {'api_version': api.PROGRAM_API_VERSION, 'other_revision_id': RID2}),
    ('restore', lambda c: c.restore(RID, PID), 'POST', f'/revisions/{RID}/restore', 200,
     api.RevisionResponse(revision=rev()), {'X-Mirum-Expected-Parent': PID}, b''),
    ('rebuild', lambda c: c.rebuild(RID, PID), 'POST', f'/revisions/{RID}/rebuild', 200,
     api.RevisionResponse(revision=rev()), {'X-Mirum-Expected-Parent': PID}, b''),
]


@pytest.mark.parametrize('shape', SHAPES, ids=[s[0] for s in SHAPES])
@pytest.mark.parametrize('token', [None, TOKEN])
def test_request_shape(shape, token):
    name, call, method, path, status, response, extra, body = shape
    fake = Fake(const(response, status))
    result = call(C.ProgramClient(URL, token=token, transport=fake))
    assert type(result) is type(response)
    (m, url, headers, sent), = fake.calls
    assert m == method
    assert url == URL + api.API_PREFIX + path and '?' not in url and '#' not in url
    for key, value in extra.items():
        assert headers[key] == value
    allowed = {'Accept', 'X-Mirum-Access-Token', 'X-Mirum-Source-Id', 'X-Mirum-Expected-Parent',
               'X-Mirum-Require-Requirements', 'Content-Type'}
    assert set(headers) <= allowed
    assert not any(k.lower() == 'x-mirum-client-ip' for k in headers)
    assert (headers.get('X-Mirum-Access-Token')) == token
    if isinstance(body, dict):
        assert json.loads(sent) == body
    else:
        assert sent == body


def test_every_registry_operation_has_a_client_method():
    for spec in api.OPERATIONS:
        assert callable(getattr(C.ProgramClient, spec.name)), spec.name
    assert callable(C.ProgramClient.meta)


def test_meta_route():
    fake = Fake(const(api.MelMeta(language_version='1', compiler_version='c')))
    C.ProgramClient(URL, transport=fake).meta()
    assert fake.calls[0][0] == 'GET' and fake.calls[0][1].endswith('/engineering/v1/mel/meta')


def test_paths_only_from_validated_ids_and_formats():
    fake = Fake(no_network)
    client = C.ProgramClient(URL, transport=fake)
    for bad in ('../x', RID + '/x', 'A' * 32, '', RID[:-1]):
        with pytest.raises(C.ClientError) as exc:
            client.status(bad)
        assert exc.value.error.kind == 'not_found'
    with pytest.raises(C.ClientError):
        client.export(RID, '../x')
    with pytest.raises(C.ClientError):
        client.stage(PID, SRC, source_id='bad id with spaces')
    assert fake.calls == []


@pytest.mark.parametrize('url', ['ftp://x', 'file:///etc/passwd', 'http://u:p@host', 'http://h/?q=1',
                                 'http://h/prefix', 'http://', 'javascript:1', '', 'http://h\n'])
def test_only_plain_http_urls(url):
    with pytest.raises(C.TransportError) as exc:
        C.ProgramClient(url)
    assert exc.value.code == 'invalid_url'


def test_https_accepted_and_bad_token_refused():
    C.ProgramClient('https://api.test:8443/')
    for token in ('has space', 'new\nline', '', 'x' * 513):
        with pytest.raises(C.TransportError) as exc:
            C.ProgramClient(URL, token=token)
        assert exc.value.code == 'invalid_token' and str(exc.value) == 'invalid_token'


def test_repr_hides_token():
    assert TOKEN not in repr(C.ProgramClient(URL, token=TOKEN))


# ------------------------------------------------------------------------------------ response bounds

def test_redirect_refused():
    for status in (301, 302, 303, 307, 308):
        client = C.ProgramClient(URL, transport=Fake(lambda *a, s=status: (s, {'location': 'http://evil/'}, b'')))
        with pytest.raises(C.TransportError) as exc:
            client.status(RID)
        assert exc.value.code == 'redirect_refused'


def test_default_transport_refuses_redirects():
    handler = C._RefuseRedirects()
    req = urllib.request.Request(URL + '/a')
    assert handler.redirect_request(req, None, 302, 'Found', {}, 'http://evil/') is None
    transport = C.UrllibTransport()
    assert any(isinstance(h, C._RefuseRedirects) for h in transport._opener.handlers)
    assert isinstance(handler, urllib.request.HTTPRedirectHandler)


def test_read_cap_is_cap_plus_one():
    class Resp:
        def __init__(self):
            self.asked = None

        def read(self, n):
            self.asked = n
            return b'x' * n
    resp = Resp()
    with pytest.raises(C.TransportError) as exc:
        C._read_capped(resp, 10)
    assert resp.asked == 11 and exc.value.code == 'response_too_large'


def test_oversized_json_response_refused():
    cap = api.MAX_JSON_RESPONSE_BYTES
    client = C.ProgramClient(URL, transport=Fake(lambda *a: (200, {}, b' ' * (cap + 1))))
    with pytest.raises(C.TransportError) as exc:
        client.status(RID)
    assert exc.value.code == 'response_too_large'


def test_deep_json_response_refused():
    deep = b'[' * 20 + b']' * 20
    client = C.ProgramClient(URL, transport=Fake(lambda *a: (200, {}, deep)))
    with pytest.raises(C.TransportError) as exc:
        client.status(RID)
    assert exc.value.code == 'malformed_response'
    client = C.ProgramClient(URL, transport=Fake(lambda *a: (200, {}, b'[' * 200000)))
    with pytest.raises(C.TransportError):
        client.status(RID)


def test_response_must_validate_into_the_model():
    for body in (b'{"api_version": "mirum_program_api_v0_1", "extra": 1}', b'not json', b'{}'):
        client = C.ProgramClient(URL, transport=Fake(lambda *a, b=body: (200, {}, b)))
        with pytest.raises(C.TransportError) as exc:
            client.status(RID)
        assert exc.value.code == 'malformed_response'


def test_unexpected_success_status_is_a_transport_error():
    client = C.ProgramClient(URL, transport=Fake(lambda *a: (204, {}, b'')))
    with pytest.raises(C.TransportError) as exc:
        client.status(RID)
    assert exc.value.code == 'bad_status'


def test_non_envelope_error_body_is_a_transport_error():
    for status, body in ((500, b'<html>oops</html>'), (404, b'{"detail": "Not Found"}'), (401, b'')):
        client = C.ProgramClient(URL, transport=Fake(lambda *a, s=status, b=body: (s, {}, b)))
        with pytest.raises(C.TransportError) as exc:
            client.status(RID)
        assert exc.value.code == 'bad_error_body'


def test_envelope_error_raises_client_error():
    err = api.not_found()
    client = C.ProgramClient(URL, transport=Fake(err_handler(err)))
    with pytest.raises(C.ClientError) as exc:
        client.status(RID)
    assert exc.value.error == err


# --------------------------------------------------------------------------------------- exit codes

def envelope(kind):
    for code, entry in api.ERROR_TABLE.items():
        if entry.kind == kind:
            return api.error(code)
    return api.InterfaceError(kind=kind, code='MEL-P002', category='syntax', message='Invalid program.',
                              retryable=False, next_action='fix_source')


@pytest.mark.parametrize('kind', api.ERROR_KINDS)
def test_exit_code_per_error_kind(kind):
    err = envelope(kind)
    code, out, text = run_cli(['status', RID], transport=Fake(err_handler(err)))
    assert code == api.KIND_EXIT_CODE[kind] == api.exit_code(kind)
    assert out == '' and json.loads(text) == err.model_dump(mode='json')


def test_documented_exit_classes():
    assert {api.exit_code(k) for k in ('invalid_source', 'unsupported')} == {2}
    assert {api.exit_code(k) for k in ('unavailable', 'busy')} == {3}
    assert {api.exit_code(k) for k in ('conflict', 'refused')} == {4}


@pytest.mark.parametrize('state,kind', [('published', 'published'), ('rejected', 'rejected'),
                                        ('staged', 'pending'), ('cancelled', 'cancelled'),
                                        ('failed', 'failed'), ('conflict', 'conflict')])
@pytest.mark.parametrize('command', ['run', 'status'])
def test_exit_code_per_result_kind(command, state, kind):
    response = api.RevisionResponse(revision=rev(state=state, result_kind=kind))
    code, out, text = run_cli([command, RID], transport=Fake(const(response)))
    assert code == api.result_exit_code(kind) and text == ''
    assert json.loads(out)['revision']['result_kind'] == kind
    assert api.result_exit_code('rejected') == 2


def test_result_kind_unavailable_and_verification_failed():
    for kind, expected in (('unavailable', 3), ('verification_failed', 2)):
        response = api.RevisionResponse(revision=rev(state='failed', result_kind=kind, reason='x'))
        assert run_cli(['run', RID], transport=Fake(const(response)))[0] == expected


def test_transport_failures_exit_1():
    def boom(*a):
        raise C.TransportError('connection_failed')
    code, out, text = run_cli(['status', RID], transport=Fake(boom))
    assert code == 1 and out == '' and json.loads(text) == {'kind': 'transport_error', 'code': 'connection_failed'}
    code, _o, text = run_cli(['status', RID], transport=Fake(lambda *a: (500, {}, b'garbage')))
    assert code == 1 and json.loads(text)['code'] == 'bad_error_body'
    assert run_cli(['status', RID], transport=Fake(lambda *a: (302, {}, b'')))[0] == 1


def test_missing_url_is_usage_error():
    out, err = io.StringIO(), io.StringIO()
    assert K.main(['status', RID], transport=no_network, environ={}, stdout=out, stderr=err) == 2
    assert json.loads(err.getvalue())['kind'] == 'invalid_request'


# --------------------------------------------------------------------------------- token and argv

def test_token_only_from_environment_and_never_printed(beam):
    fake = Fake(const(rev_resp := api.RevisionResponse(revision=rev())))
    code, out, text = run_cli(['status', RID], transport=fake, env={'MIRUM_ACCESS_TOKEN': TOKEN})
    assert code == 0 and fake.calls[0][2]['X-Mirum-Access-Token'] == TOKEN
    assert TOKEN not in out and TOKEN not in text
    fake = Fake(err_handler(api.not_found()))
    code, out, text = run_cli(['status', RID], transport=fake, env={'MIRUM_ACCESS_TOKEN': TOKEN})
    assert TOKEN not in out and TOKEN not in text
    fake = Fake(const(rev_resp))
    run_cli(['status', RID], transport=fake)
    assert 'X-Mirum-Access-Token' not in fake.calls[0][2]
    # a malformed token is refused without echoing it
    code, out, text = run_cli(['status', RID], transport=no_network, env={'MIRUM_ACCESS_TOKEN': 'bad token'})
    assert code == 1 and 'bad token' not in out + text and json.loads(text)['code'] == 'invalid_token'


@pytest.mark.parametrize('argv', [['--token', 'x', 'status', RID], ['status', RID, '--token', 'x'],
                                  ['--token=SECRET', 'status', RID], ['status', RID, '--access-token', 'x']])
def test_token_flag_rejected(argv):
    code, out, text = run_cli(argv, transport=no_network)
    assert code == 2 and 'SECRET' not in text and 'x' not in json.loads(text)['message'].split()
    assert json.loads(text)['kind'] == 'invalid_request'


def test_no_token_option_anywhere():
    parser = K.build_parser()
    stack = [parser]
    while stack:
        p = stack.pop()
        for action in p._actions:
            assert not any('token' in s for s in action.option_strings), action.option_strings
            if hasattr(action, 'choices') and isinstance(action.choices, dict):
                stack.extend(action.choices.values())


def test_help_goes_to_stdout_exit_0():
    code, out, text = run_cli(['--help'])
    assert code == 0 and 'usage' in out.lower() and text == ''


# --------------------------------------------------------------------------------------- offline

@pytest.mark.parametrize('command', ['validate', 'format', 'plan'])
def test_offline_matches_offline_functions_and_runs_no_transport(command, beam):
    func = {'validate': offline.validate_source, 'format': offline.format_source, 'plan': offline.plan_source}[command]
    source = Path(beam).read_bytes()
    code, out, text = run_cli(['--offline', command, beam, '--source-id', 'beam.mir'], transport=no_network,
                              env={'MIRUM_API_URL': ''})
    try:
        expected = func(source, 'beam.mir')
    except api.InterfaceFailure as failure:
        assert code == api.exit_code(failure.error) and out == ''
        assert json.loads(text) == failure.error.model_dump(mode='json')
    else:
        assert code == 0 and text == ''
        assert json.loads(out) == expected.model_dump(mode='json')


def test_offline_validate_beam_ok(beam):
    code, out, text = run_cli(['--offline', 'validate', beam])
    assert code == 0, text
    assert json.loads(out)['ok'] is True


@pytest.mark.parametrize('command', ['run', 'status', 'program-create', 'stage', 'review', 'export', 'history',
                                     'diff', 'cancel', 'inspect', 'restore', 'rebuild'])
def test_offline_with_other_subcommands_is_a_usage_error(command):
    code, out, text = run_cli(['--offline', command, RID, RID2], transport=no_network)
    assert code == 2 and out == '' and json.loads(text)['kind'] == 'invalid_request'


def mel_cases():
    data = json.loads((FIXTURES / 'mel_cases.json').read_text('ascii'))
    base = (FIXTURES / data['base']).read_text('ascii')
    cases = []
    for case in data['cases']:
        source = case.get('source')
        if source is None:
            source = base
            for old, new in case.get('edits', []):
                assert old in source
                source = source.replace(old, new)
        cases.append((case, source))
    return cases


@pytest.mark.parametrize('case,source', mel_cases(), ids=[c['id'] for c, _ in mel_cases()])
def test_offline_mel_codes_match_fixture(case, source, tmp_path):
    path = tmp_path / 'case.mir'
    path.write_bytes(source.encode('ascii'))
    code, out, text = run_cli(['--offline', 'validate', str(path), '--source-id', case['source_id']],
                              transport=no_network)
    err = json.loads(text)
    assert out == ''
    assert err['code'] == case['code'] and err['kind'] == case['kind'] and err['field'] == case['field']
    assert code == 2
    line, column, end_line, end_column = case['span']
    assert (err['span']['line'], err['span']['column'], err['span']['end_line'],
            err['span']['end_column']) == (line, column, end_line, end_column)


def test_offline_python_like_source_never_executes(tmp_path):
    path = tmp_path / 'x.mir'
    marker = tmp_path / 'ran'
    path.write_bytes(f"import os\nopen({str(marker)!r}, 'w')\n".encode('ascii'))
    code, _o, text = run_cli(['--offline', 'validate', str(path)])
    assert code == 2 and not marker.exists() and json.loads(text)['kind'] == 'invalid_source'


def test_source_file_read_is_capped(tmp_path, monkeypatch):
    big = tmp_path / 'big.mir'
    big.write_bytes(b'x' * (api.MAX_MEL_BODY_BYTES + 500))
    assert len(K._read_source(str(big))) == api.MAX_MEL_BODY_BYTES + 1
    fake = Fake(const(api.ValidateResult(source_sha256=SHA, semantic_sha256=SHA, language_version='1',
                                         compiler_version='c')))
    run_cli(['validate', str(big)], transport=fake)
    assert len(fake.calls[0][3]) == api.MAX_MEL_BODY_BYTES + 1
    code, _o, text = run_cli(['validate', str(tmp_path)], transport=no_network)
    assert code == 2 and json.loads(text)['field'] == 'file'
    code, _o, text = run_cli(['validate', str(tmp_path / 'missing.mir')], transport=no_network)
    assert code == 2 and str(tmp_path) not in text


def test_online_validate_sends_raw_file_bytes(beam):
    fake = Fake(const(api.ValidateResult(source_sha256=SHA, semantic_sha256=SHA, language_version='1',
                                         compiler_version='c')))
    code, out, _t = run_cli(['validate', beam, '--source-id', 'beam.mir'], transport=fake)
    assert code == 0 and fake.calls[0][3] == BEAM.read_bytes()
    assert fake.calls[0][2]['Content-Type'] == api.MEL_MEDIA_TYPE


# ------------------------------------------------------------------------------------------ export

def export_handler(data, fmt='step', *, sha=None, filename=None, ctype='application/step'):
    name = filename if filename is not None else C.EXPORT_FILENAMES[fmt]
    digest = sha if sha is not None else hashlib.sha256(data).hexdigest()
    return lambda *a: (200, {'content-type': ctype, 'content-disposition': f'attachment; filename="{name}"',
                             'x-content-sha256': digest}, data)


def export_argv(out, fmt='step'):
    return ['export', RID, fmt, '--out', str(out)]


def test_export_writes_atomically_and_prints_metadata(tmp_path):
    data = b'step-bytes' * 100
    fake = Fake(export_handler(data))
    code, out, text = run_cli(export_argv(tmp_path), transport=fake, env={'MIRUM_ACCESS_TOKEN': TOKEN})
    assert code == 0, text
    assert (tmp_path / 'model.step').read_bytes() == data
    assert sorted(p.name for p in tmp_path.iterdir()) == ['model.step']
    meta = json.loads(out)
    assert meta['sha256'] == hashlib.sha256(data).hexdigest() and meta['size_bytes'] == len(data)
    assert meta['filename'] == 'model.step' and meta['fmt'] == 'step'
    assert fake.calls[0][0] == 'GET' and fake.calls[0][1].endswith(f'/revisions/{RID}/artifacts/step')


def test_export_uses_os_replace_in_target_dir(tmp_path, monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(os, 'replace', lambda a, b: (seen.append((a, b)), real(a, b))[1])
    assert run_cli(export_argv(tmp_path), transport=Fake(export_handler(b'abc')))[0] == 0
    (src, dst), = seen
    assert os.path.dirname(src) == str(tmp_path) and dst == str(tmp_path / 'model.step') and src != dst


def test_export_refuses_existing_file(tmp_path):
    target = tmp_path / 'model.step'
    target.write_bytes(b'old')
    fake = Fake(export_handler(b'new'))
    code, _o, text = run_cli(export_argv(tmp_path), transport=fake)
    assert code == 2 and target.read_bytes() == b'old' and json.loads(text)['field'] == 'out'
    assert fake.calls == []


def test_export_refuses_symlinks(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    link_dir = tmp_path / 'linkdir'
    link_file_dir = tmp_path / 'out'
    link_file_dir.mkdir()
    try:
        os.symlink(real, link_dir, target_is_directory=True)
        os.symlink(tmp_path / 'elsewhere', link_file_dir / 'model.step')
    except (OSError, NotImplementedError):
        pytest.skip('symlinks not permitted on this host (runs on Linux CI)')
    fake = Fake(export_handler(b'new'))
    assert run_cli(export_argv(link_dir), transport=fake)[0] == 2
    assert list(real.iterdir()) == []
    assert run_cli(export_argv(link_file_dir), transport=fake)[0] == 2
    assert not (tmp_path / 'elsewhere').exists()
    assert fake.calls == []


def test_export_refuses_non_directory_out(tmp_path):
    not_dir = tmp_path / 'file'
    not_dir.write_bytes(b'x')
    fake = Fake(export_handler(b'x'))
    assert run_cli(export_argv(not_dir), transport=fake)[0] == 2
    assert run_cli(export_argv(tmp_path / 'missing'), transport=fake)[0] == 2
    assert fake.calls == []


@pytest.mark.parametrize('fmt', ['../x', 'nope', '..', 'step/../../x', '', 'STEP', 'step\x00'])
def test_export_refuses_non_allowlisted_format(tmp_path, fmt):
    fake = Fake(export_handler(b'x'))
    code, _o, text = run_cli(['export', RID, fmt, '--out', str(tmp_path)], transport=fake)
    assert code == 2 and json.loads(text)['kind'] == 'not_found'
    assert fake.calls == [] and list(tmp_path.iterdir()) == []


def test_export_sha_mismatch_leaves_no_file(tmp_path):
    code, out, text = run_cli(export_argv(tmp_path), transport=Fake(export_handler(b'abc', sha='b' * 64)))
    assert code == 1 and out == '' and json.loads(text)['code'] == 'integrity_mismatch'
    assert list(tmp_path.iterdir()) == []
    fake = Fake(lambda *a: (200, {'content-disposition': 'attachment; filename="model.step"',
                                  'content-type': 'application/step'}, b'abc'))
    assert run_cli(export_argv(tmp_path), transport=fake)[0] == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('name', ['../model.step', 'evil.step', 'MODEL.STEP', 'model.step.exe', '..\\model.step'])
def test_export_never_follows_server_filename(tmp_path, name):
    code, _o, text = run_cli(export_argv(tmp_path), transport=Fake(export_handler(b'abc', filename=name)))
    assert code == 1 and list(tmp_path.iterdir()) == []
    assert json.loads(text)['code'] == 'filename_mismatch'


def test_export_error_envelope(tmp_path):
    code, _o, text = run_cli(export_argv(tmp_path), transport=Fake(err_handler(envelope('expired'))))
    assert code == 4 and json.loads(text)['kind'] == 'expired' and list(tmp_path.iterdir()) == []


def test_export_byte_cap():
    client = C.ProgramClient(URL, transport=Fake(lambda *a: (200, {}, b'x' * (C.MAX_EXPORT_BYTES + 1))))
    with pytest.raises(C.TransportError) as exc:
        client.export(RID, 'step')
    assert exc.value.code == 'response_too_large'


def test_export_filenames_match_server_tables():
    assert set(C.EXPORT_FILENAMES) == set(api.EXPORT_FORMATS)
    bundles = pytest.importorskip('engineering.bundles')
    for fmt, (name, _mime) in bundles.ALL_FORMATS.items():
        assert C.EXPORT_FILENAMES[fmt] == name
    for fmt, expected in (('program-source', 'program.mir'), ('program-ir', 'program-ir.json'),
                          ('execution-lock', 'execution-lock.json')):
        assert C.EXPORT_FILENAMES[fmt] == expected


# --------------------------------------------------------------------------------------- parity

def _subcommands():
    parser = K.build_parser()
    action = next(a for a in parser._actions if getattr(a, 'choices', None) and isinstance(a.choices, dict))
    return action.choices


def test_cli_subcommand_set_equals_registry():
    names = set(_subcommands())
    assert names == {op.name.replace('_', '-') for op in api.OPERATIONS if 'cli' in op.transports}
    assert 'propose' not in names
    assert {op.name for op in api.OPERATIONS if 'cli' not in op.transports} == {'propose'}
    assert 'token' not in ' '.join(names)


def test_cli_options_equal_request_model_fields():
    subs = _subcommands()
    for spec in K.cli_operations():
        parser = subs[K.cli_name(spec)]
        actions = [a for a in parser._actions if a.dest != 'help']
        dests = {a.dest for a in actions}
        expected = {n for n in spec.input_model.model_fields if n != 'api_version'}
        if spec.body == 'mel':
            expected.add('file')
        if spec.name == 'export':
            expected.add('out')
        assert dests == expected, spec.name
        assert len(dests) == len(actions)
        for name, info in spec.input_model.model_fields.items():
            if name == 'api_version':
                continue
            action = next(a for a in actions if a.dest == name)
            assert action.required == info.is_required(), (spec.name, name)
            if not action.option_strings:
                assert info.is_required()
            else:
                assert action.option_strings == ['--' + name.replace('_', '-')]


def test_client_methods_take_the_request_model_fields():
    import inspect
    for spec in api.OPERATIONS:
        params = set(inspect.signature(getattr(C.ProgramClient, spec.name)).parameters) - {'self'}
        fields = {n for n in spec.input_model.model_fields if n != 'api_version'}
        if spec.body == 'mel':
            fields.add('source')
        if spec.name == 'propose':
            fields.add('require_requirements_sha256')
        assert params == fields, spec.name


# ---------------------------------------------------------------------------------------- import pin

PIN = '''
import sys
sys.path[:0] = [{backend!r}, {root!r}]
import engineering.program.cli as cli
import engineering.program.client as client
before = set(sys.modules)
import io
out, err = io.StringIO(), io.StringIO()
code = cli.main(["--offline", "validate", {beam!r}], stdout=out, stderr=err, environ={{}})
assert code == 0, (code, err.getvalue())
bad = sorted(m for m in sys.modules if m.split(".")[0] in {banned!r}
             or m in {banned_modules!r})
print("BAD", bad)
'''

BANNED_TOP = ('sqlite3', 'fastapi', 'starlette', 'httpx', 'uvicorn', 'cadquery', 'OCP')
BANNED_MODULES = ('engineering.store', 'engineering.program.transactions', 'engineering.program.facade',
                  'engineering.api_programs', 'engineering.jobs', 'engineering.program.revision_store')


def test_import_pin_subprocess():
    code = PIN.format(backend=str(BACKEND), root=str(ROOT), beam=str(BEAM), banned=BANNED_TOP,
                      banned_modules=BANNED_MODULES)
    proc = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, timeout=120,
                          cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == 'BAD []', proc.stdout


def test_cli_runs_as_module_offline():
    env = {k: v for k, v in os.environ.items() if not k.startswith('MIRUM_')}
    env['PYTHONPATH'] = os.pathsep.join([str(BACKEND), str(ROOT)])
    proc = subprocess.run([sys.executable, '-m', 'engineering.program.cli', '--offline', 'validate', str(BEAM)],
                          capture_output=True, text=True, timeout=120, env=env, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout)['ok'] is True
