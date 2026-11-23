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


# ================================================================================ MCP server (task 5)

from engineering.program import mcp as M  # noqa: E402


def offline_server_handler(method, url, headers, body):
    """Fake API: the stateless MEL routes run the very offline functions the server runs."""
    for name, fn in (('validate', offline.validate_source), ('format', offline.format_source),
                     ('plan', offline.plan_source)):
        if url.endswith('/mel/' + name):
            sid = headers.get('X-Mirum-Source-Id')
            try:
                return 200, {'content-type': 'application/json'}, body_of(fn(body, sid))
            except api.InterfaceFailure as exc:
                return api.http_status(exc.error), {'content-type': 'application/json'}, body_of(exc.error)
    raise AssertionError('unexpected route ' + url)


def rpc(server, method, params=None, rid=1):
    msg = {'jsonrpc': '2.0', 'id': rid, 'method': method}
    if params is not None:
        msg['params'] = params
    return server.handle(msg)


def call(server, name, arguments):
    resp = rpc(server, 'tools/call', {'name': name, 'arguments': arguments})
    result = resp['result']
    assert result['content'][0]['type'] == 'text'
    assert json.loads(result['content'][0]['text']) == result['structuredContent']
    return result['structuredContent'], result['isError']


def make_server(handler, token=None):
    fake = Fake(handler)
    return M.McpServer(C.ProgramClient(URL, token=token, transport=fake)), fake


def mcp_server(handler=no_network, token=None):
    return make_server(handler, token)[0]


# ------------------------------------------------------------------------------------------ protocol

def test_initialize_echoes_supported_version():
    resp = rpc(mcp_server(), 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}})
    assert resp['id'] == 1 and resp['result']['protocolVersion'] == '2025-03-26'
    assert resp['result']['capabilities'] == {'tools': {}}
    assert resp['result']['serverInfo']['name'] == M.SERVER_NAME
    resp = rpc(mcp_server(), 'initialize', {'protocolVersion': '1999-01-01'})
    assert resp['result']['protocolVersion'] in M.SUPPORTED_PROTOCOL_VERSIONS


def test_ping_and_notifications():
    server = mcp_server()
    assert rpc(server, 'ping')['result'] == {}
    assert server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
    assert server.handle({'jsonrpc': '2.0', 'method': 'no/such'}) is None
    assert server.handle({'jsonrpc': '2.0', 'id': 4, 'result': {}}) is None


def test_unknown_method_and_malformed():
    server = mcp_server()
    assert rpc(server, 'no/such')['error']['code'] == -32601
    assert server.handle(b'{not json')['error']['code'] == -32700
    assert server.handle(b'\xff\xfe')['error']['code'] == -32700
    for bad in ([], [{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}], 5, {'id': 1},
                {'jsonrpc': '1.0', 'id': 1, 'method': 'ping'}, {'jsonrpc': '2.0', 'id': True, 'method': 'ping'},
                {'jsonrpc': '2.0', 'id': 1, 'method': 7}):
        assert server.handle(bad)['error']['code'] == -32600, bad
    assert server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'params': 3})['error']['code'] == -32602


def test_deep_json_is_refused():
    deep = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":' + b'[' * 50 + b']' * 50 + b'}'
    assert mcp_server().handle(deep)['error']['code'] == -32600
    assert mcp_server().handle(b'[' * 100000)['error']['code'] in (-32700, -32600)


def test_oversize_message_is_refused():
    big = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":"' + b'a' * api.MAX_MCP_MESSAGE_BYTES + b'"}}'
    assert mcp_server().handle(big)['error']['code'] == -32600


# ------------------------------------------------------------------------------------------ tool set

def tool_list():
    return rpc(mcp_server(), 'tools/list')['result']['tools']


def test_tool_set_is_exactly_d12():
    expected = ['validate', 'format', 'plan', 'propose', 'status', 'inspect', 'history', 'diff', 'run', 'cancel']
    assert sorted(t['name'] for t in tool_list()) == sorted(expected)
    assert set(M.tool_names()) == set(api.MCP_TOOL_NAMES) == set(expected)
    names = {t['name'] for t in tool_list()}
    assert not names & {'create', 'program_create', 'stage', 'review', 'restore', 'rebuild', 'export'}


@pytest.mark.parametrize('name', ['create', 'program_create', 'stage', 'review', 'restore', 'rebuild', 'export',
                                  'nope', '', 5, None])
def test_non_exposed_tools_are_refused_without_a_request(name):
    resp = rpc(mcp_server(), 'tools/call', {'name': name, 'arguments': {}})
    assert resp['error']['code'] == -32602 and 'result' not in resp


def test_input_schemas_match_models_and_registry():
    for tool in tool_list():
        spec = api.OPERATIONS_BY_NAME[tool['name']]
        schema = tool['inputSchema']
        fields = {n for n in spec.input_model.model_fields if n != 'api_version'}
        if spec.body == 'mel':
            fields.add('source')
        assert set(schema['properties']) == fields, tool['name']
        assert schema['additionalProperties'] is False
        assert schema == api.mcp_input_schema(tool['name'])
        assert tool['annotations']['readOnlyHint'] is (not spec.mutating)


def test_schema_scan_no_path_url_dir_arguments():
    import re
    bad = re.compile(r'path|url|uri|file|dir|directory|host', re.I)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'properties':
                    for prop, sub in value.items():
                        assert not bad.search(prop), prop
                        assert not bad.search(str(sub.get('description', ''))), prop
                if key == 'description':
                    assert not bad.search(str(value)), value
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(tool_list())


# ---------------------------------------------------------------------------- argument validation

@pytest.mark.parametrize('name,args', [
    ('validate', {}), ('validate', {'source': 5}), ('validate', {'source': 'x', 'extra': 1}),
    ('validate', {'source': 'x', 'source_id': ''}), ('validate', {'source': 'x', 'path': '/etc/passwd'}),
    ('status', {}), ('status', {'revision_id': 'zz'}), ('status', {'revision_id': RID, 'url': 'http://x'}),
    ('diff', {'revision_id': RID}), ('history', {'program_id': 5}), ('propose', {'program_id': PID}),
    ('propose', {'source': 'x'}), ('run', {'revision_id': RID, 'job_id': 'x'}),
])
def test_invalid_arguments_never_reach_the_api(name, args):
    payload, is_error = call(mcp_server(), name, args)
    assert is_error and payload['kind'] in ('invalid_request', 'not_found') and payload['changed'] == 'nothing_written'


def test_lone_surrogate_source_is_invalid_field():
    payload, is_error = call(mcp_server(), 'validate', {'source': chr(0xd800)})
    assert is_error and payload['code'] == 'invalid_field' and payload['field'] == 'source'


def test_source_cap_is_in_utf8_bytes():
    ok = 'a' * api.MAX_MEL_BODY_BYTES
    server, fake = make_server(const(api.FormatResult(formatted_source='x', formatted_sha256=SHA, unchanged=True)))
    payload, is_error = call(server, 'format', {'source': ok})
    assert not is_error and len(fake.calls[0][3]) == api.MAX_MEL_BODY_BYTES
    two_byte = chr(0xe9) * (api.MAX_MEL_BODY_BYTES // 2 + 1)
    assert len(two_byte) <= api.MAX_MEL_BODY_BYTES
    payload, is_error = call(mcp_server(), 'format', {'source': two_byte})
    assert is_error and payload['kind'] == 'too_large' and payload['code'] == 'body_too_large'
    payload, is_error = call(mcp_server(), 'validate', {'source': ok + 'a'})
    assert is_error and payload['kind'] == 'too_large'


# ------------------------------------------------------------------- offline parity (same functions)

def test_validate_format_plan_equal_offline_outputs():
    server = mcp_server(offline_server_handler)
    source = BEAM.read_bytes()
    text = source.decode('ascii')
    for name, fn in (('validate', offline.validate_source), ('format', offline.format_source),
                     ('plan', offline.plan_source)):
        payload, is_error = call(server, name, {'source': text, 'source_id': 'beam.mir'})
        assert not is_error
        assert payload == json.loads(fn(source, 'beam.mir').model_dump_json()), name
        code, out, _err = run_cli(['--offline', name, str(BEAM), '--source-id', 'beam.mir'])
        assert code == 0 and json.loads(out) == payload, name


@pytest.mark.parametrize('case,source', mel_cases(), ids=[c['id'] for c, _ in mel_cases()])
def test_mel_codes_through_mcp_match_fixture(case, source):
    payload, is_error = call(mcp_server(offline_server_handler), 'validate',
                             {'source': source, 'source_id': case['source_id']})
    assert is_error
    assert payload['code'] == case['code'] and payload['kind'] == case['kind'] and payload['field'] == case['field']
    if case['kind'] == 'unsupported':
        assert payload['next_action'] == 'use_supported_capability'
    if payload.get('span'):
        line, column, end_line, end_column = case['span']
        assert (payload['span']['line'], payload['span']['column'], payload['span']['end_line'],
                payload['span']['end_column']) == (line, column, end_line, end_column)


def test_python_like_source_never_executes(tmp_path):
    marker = tmp_path / 'ran'
    src = f"import os\nopen({str(marker)!r}, 'w')\n"
    payload, is_error = call(mcp_server(offline_server_handler), 'validate', {'source': src})
    assert is_error and payload['kind'] == 'invalid_source' and not marker.exists()


# ---------------------------------------------------------------------------------------------- propose

def head_view(head=RID, fixed='b' * 64):
    revs = (rev(revision_id=head, fixed_requirements_sha256=fixed),) if head else ()
    return api.HistoryView(program_id=PID, head_revision_id=head, revisions=revs)


def propose_handler(history, stage_response=None):
    def handler(method, url, headers, body):
        if url.endswith('/history'):
            return 200, {}, body_of(history)
        if url.endswith('/revisions') and method == 'POST':
            return 201, {}, body_of(stage_response or api.StageResponse(revision=rev()))
        raise AssertionError(url)
    return handler


def test_propose_sends_guard_header_from_head():
    server, fake = make_server(propose_handler(head_view(fixed='c' * 64)), token=TOKEN)
    payload, is_error = call(server, 'propose', {'program_id': PID, 'source': 'x\n', 'source_id': 'a.mir',
                                                 'expected_parent': RID})
    assert not is_error and payload['revision']['state'] == 'staged'
    stage = [c for c in fake.calls if c[0] == 'POST'][0]
    assert stage[1].endswith(f'/programs/{PID}/revisions')
    assert stage[2]['X-Mirum-Require-Requirements'] == 'c' * 64
    assert stage[2]['X-Mirum-Expected-Parent'] == RID and stage[3] == b'x\n'
    assert not any('/review' in c[1] or '/run' in c[1] for c in fake.calls)


def test_propose_without_head_never_stages():
    server, fake = make_server(propose_handler(head_view(head=None)))
    payload, is_error = call(server, 'propose', {'program_id': PID, 'source': 'x\n'})
    assert is_error and payload['kind'] == 'refused' and payload['code'] == 'requirements_changed'
    assert payload['changed'] == 'nothing_written'
    assert [c[0] for c in fake.calls] == ['GET']


def test_propose_server_refusal_passes_through():
    err = api.error('requirements_changed')
    server = mcp_server(lambda m, u, h, b: (200, {}, body_of(head_view())) if u.endswith('/history')
                        else (api.http_status(err), {}, body_of(err)))
    payload, is_error = call(server, 'propose', {'program_id': PID, 'source': 'x'})
    assert is_error and payload == json.loads(body_of(err))


# --------------------------------------------------------------------------------- error passthrough

@pytest.mark.parametrize('err', [
    api.error('stale_parent'), api.error('capacity'), api.unavailable(), api.error('mock_backend'),
    api.error('lock_mismatch'), api.not_found(), api.too_large(),
], ids=lambda e: e.code)
def test_envelopes_pass_through_unchanged(err):
    payload, is_error = call(mcp_server(err_handler(err)), 'status', {'revision_id': RID})
    assert is_error is True
    assert payload == json.loads(body_of(err))
    for key in ('kind', 'code', 'retryable', 'next_action'):
        assert payload[key] == getattr(err, key)


def test_busy_keeps_retry_after():
    err = api.busy('admission_busy', retry_after=17)
    payload, is_error = call(mcp_server(err_handler(err)), 'run', {'revision_id': RID})
    assert is_error and payload['kind'] == 'busy' and payload['retry_after'] == 17 and payload['retryable'] is True


def test_invalid_source_and_unsupported_stay_distinct():
    server = mcp_server(offline_server_handler)
    inv, _ = call(server, 'validate', {'source': 'import os\n'})
    uns_src = BEAM.read_text('ascii').replace('"rectangular_cantilever"', '"other_thing"')
    uns, _ = call(server, 'validate', {'source': uns_src})
    assert inv['kind'] == 'invalid_source' and uns['kind'] == 'unsupported' and uns['code'] == 'MEL-S011'
    assert uns['next_action'] == 'use_supported_capability'


def test_rejected_run_is_a_result_not_an_error():
    resp = api.RevisionResponse(revision=rev(state='rejected', result_kind='rejected', reason='policy_rejected'))
    payload, is_error = call(mcp_server(const(resp)), 'run', {'revision_id': RID})
    assert is_error is False and payload['revision']['result_kind'] == 'rejected'


def test_transport_failures_use_fixed_envelope():
    def boom(*_a):
        raise RuntimeError('secret detail ' + TOKEN)
    payload, is_error = call(mcp_server(boom, token=TOKEN), 'status', {'revision_id': RID})
    assert is_error and payload == {'kind': 'transport_error', 'code': 'internal_error'}

    def refuse(*_a):
        raise C.TransportError('connection_failed')
    payload, is_error = call(mcp_server(refuse), 'status', {'revision_id': RID})
    assert is_error and payload == {'kind': 'transport_error', 'code': 'connection_failed'}
    payload, is_error = call(mcp_server(lambda *a: (500, {}, b'junk')), 'status', {'revision_id': RID})
    assert is_error and payload['kind'] == 'transport_error'


def test_token_never_in_output():
    server, fake = make_server(lambda *a: (500, {}, TOKEN.encode()), token=TOKEN)
    outputs = [rpc(server, 'tools/list'), rpc(server, 'initialize', {})]
    outputs.append(call(server, 'status', {'revision_id': RID}))
    outputs.append(call(server, 'status', {'revision_id': RID, 'token': TOKEN}))
    outputs.append(rpc(server, 'tools/call', {'name': TOKEN, 'arguments': {}}))
    assert TOKEN not in json.dumps(outputs)
    assert fake.calls[0][2]['X-Mirum-Access-Token'] == TOKEN
    assert 'token' not in json.dumps(tool_list()).lower()


def test_each_tool_reaches_its_route():
    expected = {'status': ('GET', f'/revisions/{RID}'), 'run': ('POST', f'/revisions/{RID}/run'),
                'cancel': ('POST', f'/revisions/{RID}/cancel'), 'inspect': ('GET', f'/revisions/{RID}/evidence'),
                'history': ('GET', f'/programs/{PID}/history'), 'diff': ('POST', f'/revisions/{RID}/compare')}
    args = {'status': {'revision_id': RID}, 'run': {'revision_id': RID}, 'cancel': {'revision_id': RID},
            'inspect': {'revision_id': RID}, 'history': {'program_id': PID},
            'diff': {'revision_id': RID, 'other_revision_id': RID2}}
    models = {s[0]: s[5] for s in SHAPES}
    for name, (method, path) in expected.items():
        server, fake = make_server(const(models[name]))
        payload, is_error = call(server, name, args[name])
        assert not is_error, name
        assert (fake.calls[0][0], fake.calls[0][1]) == (method, URL + api.API_PREFIX + path)


# ------------------------------------------------------------------------------------------ stdio loop

def run_loop(lines: bytes, server=None):
    stdin, stdout, stderr = io.BytesIO(lines), io.BytesIO(), io.StringIO()
    M.serve(server or mcp_server(), stdin, stdout, stderr)
    return [json.loads(x) for x in stdout.getvalue().splitlines()], stderr.getvalue()


def test_stdio_loop_roundtrip():
    lines = (b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n'
             b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
             b'\n'
             b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\r\n'
             b'garbage\n'
             b'{"jsonrpc":"2.0","id":3,"method":"ping"}')
    out, err = run_loop(lines)
    assert [o.get('id') for o in out] == [1, 2, None, 3]
    assert len(out[1]['result']['tools']) == 10 and out[2]['error']['code'] == -32700
    assert err == 'mirum-mcp: started\nmirum-mcp: stopped\n'


def test_stdio_oversize_line_is_discarded_and_loop_continues():
    cap = api.MAX_MCP_MESSAGE_BYTES
    big = b'x' * (cap * 2 + 7)
    out, err = run_loop(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n' + big + b'\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
    assert [o.get('id') for o in out] == [1, None, 2]
    assert out[1]['error']['code'] == -32600 and 'oversize' in err
    assert 'x' * 10 not in err


def test_stdio_line_exactly_at_cap_is_accepted():
    cap = api.MAX_MCP_MESSAGE_BYTES
    head = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"p":"'
    tail = b'"}}'
    line = head + b'a' * (cap - len(head) - len(tail)) + tail
    assert len(line) == cap
    out, _err = run_loop(line + b'\n')
    assert out[0]['result'] == {}
    out, _err = run_loop(line + b'a\n')
    assert out[0]['error']['code'] == -32600


def test_stdio_reader_never_buffers_more_than_cap():
    sizes = []

    class Spy(io.BytesIO):
        def readline(self, size=-1):
            sizes.append(size)
            return super().readline(size)
    M.serve(mcp_server(), Spy(b'y' * (api.MAX_MCP_MESSAGE_BYTES * 3) + b'\n'), io.BytesIO(), None)
    assert sizes and all(s == api.MAX_MCP_MESSAGE_BYTES + 1 for s in sizes)


def test_main_requires_url_and_hides_token():
    err = io.StringIO()
    assert M.main(environ={}, stdin=io.BytesIO(b''), stdout=io.BytesIO(), stderr=err) == 1
    err = io.StringIO()
    assert M.main(environ={'MIRUM_API_URL': 'ftp://x', 'MIRUM_ACCESS_TOKEN': TOKEN}, stdin=io.BytesIO(b''),
                  stdout=io.BytesIO(), stderr=err) == 1
    assert TOKEN not in err.getvalue() and 'ftp' not in err.getvalue()
    out = io.BytesIO()
    err = io.StringIO()
    code = M.main(environ={'MIRUM_API_URL': URL, 'MIRUM_ACCESS_TOKEN': TOKEN},
                  stdin=io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'), stdout=out, stderr=err,
                  transport=no_network)
    assert code == 0 and TOKEN not in out.getvalue().decode() + err.getvalue()


MCP_PIN = '''
import sys
sys.path[:0] = [{backend!r}, {root!r}]
import engineering.program.mcp as mcp
import io
out = io.BytesIO()
mcp.main(environ={{"MIRUM_API_URL": "http://api.test"}}, stdin=io.BytesIO(
    b'{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}\\n'), stdout=out, stderr=io.StringIO())
assert b'"validate"' in out.getvalue()
bad = sorted(m for m in sys.modules if m.split(".")[0] in {banned!r} or m in {banned_modules!r})
print("BAD", bad)
'''


def test_mcp_import_pin_subprocess():
    code = MCP_PIN.format(backend=str(BACKEND), root=str(ROOT), banned=BANNED_TOP,
                          banned_modules=BANNED_MODULES + ('engineering.program.offline',))
    proc = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, timeout=120,
                          cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == 'BAD []', proc.stdout


def test_mcp_runs_as_module_over_pipes():
    env = {k: v for k, v in os.environ.items() if not k.startswith('MIRUM_')}
    env['PYTHONPATH'] = os.pathsep.join([str(BACKEND), str(ROOT)])
    env['MIRUM_API_URL'] = URL
    env['MIRUM_ACCESS_TOKEN'] = TOKEN
    stdin = (b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n'
             b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
             b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n')
    proc = subprocess.run([sys.executable, '-m', 'engineering.program.mcp'], input=stdin, capture_output=True,
                          timeout=120, env=env, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    lines = [json.loads(x) for x in proc.stdout.splitlines()]
    assert [x['id'] for x in lines] == [1, 2]
    assert {t['name'] for t in lines[1]['result']['tools']} == set(api.MCP_TOOL_NAMES)
    assert TOKEN.encode() not in proc.stdout + proc.stderr


# ------------------------------------------------------------------------------------------ no AI refs

def test_program_modules_never_mention_ai_or_provider_names():
    import re
    names = ['mcp.py', 'client.py', 'cli.py', 'facade.py', 'offline.py', 'interface.py']
    paths = [BACKEND / 'engineering' / 'program' / n for n in names] + [BACKEND / 'engineering' / 'api_programs.py']
    pattern = re.compile(r'\bproposer\b|proposal_contract|AiCallBudget|genai|GEMINI')
    for path in paths:
        if path.exists():
            assert not pattern.search(path.read_text('utf-8')), path.name
