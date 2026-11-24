"""F08 HTTP SDK for the program routes (stdlib urllib only).

Speaks the exact protocol of engineering.api_programs: raw-byte MEL bodies (application/vnd.mirum.mel),
small fields in the interface headers, small JSON bodies for review and compare, JSON responses read
with a hard cap and a depth cap and validated into the interface response models.

PURE CLIENT (D3): imports only the stdlib, pydantic and engineering.program.interface. Never the store,
sqlite3, a web framework, an HTTP client library, transactions, the facade, the routes module or a
native module (a subprocess test pins this).

Safety (D13): the access token is sent only as X-Mirum-Access-Token and is never part of any error or
repr; X-Mirum-Client-IP is never sent; redirects are refused (a 3xx is a transport error); only http
and https URLs; request paths are built only from validated ids and allowlisted export formats.

Errors: ClientError carries the server's InterfaceError envelope; TransportError carries a fixed code
(never exception text, URLs or response bytes).
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from pydantic import ValidationError

from . import interface as api

__all__ = ['ProgramClient', 'ClientError', 'TransportError', 'UrllibTransport', 'ExportDownload',
           'TRANSPORT_CODES', 'EXPORT_FILENAMES', 'MAX_EXPORT_BYTES', 'DEFAULT_TIMEOUT', 'TOKEN_HEADER']

TOKEN_HEADER = 'X-Mirum-Access-Token'
MAX_EXPORT_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT = 60.0
_JSON_MEDIA = 'application/json'
_TOKEN_RE = re.compile(r'^[\x21-\x7e]{1,512}$')
_CONTENT_TYPE_RE = re.compile(r'^[a-z]+/[a-z0-9.+-]+$')
_SHA_RE = re.compile(r'^[0-9a-f]{64}$')
_DISPOSITION_RE = re.compile(r'^attachment; filename="([A-Za-z0-9._-]{1,64})"$')

TRANSPORT_CODES = frozenset({
    'invalid_url', 'invalid_token', 'connection_failed', 'timeout', 'redirect_refused', 'response_too_large',
    'malformed_response', 'bad_status', 'bad_error_body', 'filename_mismatch', 'integrity_mismatch'})

# The only file name each export format may be written under (mirrors facade._SOURCE_EXPORTS and
# bundles.ALL_FORMATS; a test pins equality). A server-supplied name is never followed.
EXPORT_FILENAMES = {
    'program-source': 'program.mir', 'program-ir': 'program-ir.json', 'execution-lock': 'execution-lock.json',
    'zip': 'package.zip', 'brief': 'brief.json', 'spec': 'selected_spec.json', 'source': 'model.py',
    'step': 'model.step', 'glb': 'preview.glb', 'attempts': 'attempts.json', 'report': 'report.json',
    'markdown': 'report.md', 'manifest': 'manifest.json',
    'generator-spec': 'generator-spec.json', 'generator-report': 'generator-report.json',
    'generator-bom': 'generator-bom.json',
    'generator-body-step': 'generator-body.step', 'generator-preview-glb': 'generator-preview.glb',
    'generator-cleaned-stl': 'generator-cleaned.stl', 'generator-spec-canonical': 'generator-spec.canonical.json',
    'generator-bundle-zip': 'generator-bundle.zip', 'generator-result': 'generator-result.json',
}


class TransportError(Exception):
    """Connection, protocol or size failure. ``code`` is one of TRANSPORT_CODES; the text is the code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ClientError(Exception):
    """The server (or the client's own request validation) returned an interface error envelope."""

    def __init__(self, err: api.InterfaceError):
        super().__init__(err.code)
        self.error = err


@dataclass(frozen=True)
class ExportDownload:
    revision_id: str
    fmt: str
    filename: str
    content_type: str
    data: bytes
    sha256: str


# ------------------------------------------------------------------------------------------ transport

class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: returning None makes urllib raise the 3xx as an HTTPError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _read_capped(resp, max_bytes: int) -> bytes:
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise TransportError('response_too_large')
    return data


class UrllibTransport:
    """Default transport: (method, url, headers, body) -> (status, headers, body_bytes)."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_RefuseRedirects)

    def __call__(self, method, url, headers, body, max_bytes: int = api.MAX_JSON_RESPONSE_BYTES):
        req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, _read_capped(resp, max_bytes)
        except urllib.error.HTTPError as exc:  # any non-2xx, including a refused 3xx
            try:
                data = b'' if 300 <= exc.code < 400 else _read_capped(exc, max_bytes)
                return exc.code, {k.lower(): v for k, v in exc.headers.items()}, data
            finally:
                exc.close()
        except TransportError:
            raise
        except TimeoutError:
            raise TransportError('timeout') from None
        except Exception:  # noqa: BLE001 - URLError, OSError, http.client.HTTPException, ValueError
            raise TransportError('connection_failed') from None


def _check_base_url(url) -> str:
    if type(url) is not str or not url or len(url) > 2048:
        raise TransportError('invalid_url')
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        raise TransportError('invalid_url') from None
    if (parts.scheme not in ('http', 'https') or not host or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in ('', '/')
            or any(ord(c) <= 0x20 or ord(c) >= 0x7f for c in url)):
        raise TransportError('invalid_url')
    return f'{parts.scheme}://{parts.netloc}'


def _decode_json(body: bytes):
    try:
        return api.bounded_json_loads(body, max_bytes=api.MAX_JSON_RESPONSE_BYTES, max_depth=api.MAX_JSON_DEPTH)
    except api.BoundsError as exc:
        raise TransportError('response_too_large' if exc.code == 'body_too_large'
                             else 'malformed_response') from None


class ProgramClient:
    """One method per registry operation. Raises ClientError or TransportError only."""

    def __init__(self, base_url: str, *, token: str | None = None, transport=None,
                 timeout: float = DEFAULT_TIMEOUT):
        self._base = _check_base_url(base_url)
        if token is not None and (type(token) is not str or not _TOKEN_RE.match(token)):
            raise TransportError('invalid_token')
        self._token = token
        self._transport = transport if transport is not None else UrllibTransport(timeout)

    def __repr__(self) -> str:  # never shows the token
        return f'ProgramClient({self._base!r})'

    # ------------------------------------------------------------------------------------------ core

    def _request(self, spec, path_values: dict, *, body: bytes | None = None, content_type: str | None = None,
                 headers: dict | None = None, max_bytes: int = api.MAX_JSON_RESPONSE_BYTES, route=None):
        method, template = (spec.http_method, spec.http_path) if route is None else (route.method, route.path)
        path = template
        for key, value in path_values.items():
            path = path.replace('{' + key + '}', value)
        url = self._base + api.API_PREFIX + path
        send = {'Accept': _JSON_MEDIA if max_bytes == api.MAX_JSON_RESPONSE_BYTES else '*/*'}
        if self._token is not None:
            send[TOKEN_HEADER] = self._token
        if content_type is not None:
            send['Content-Type'] = content_type
        for name, value in (headers or {}).items():
            if value is None:
                continue
            code = api.check_header_value(name, value)
            if code is not None:
                raise ClientError(api.error(code))
            send[name] = value
        if body is None and method == 'POST':
            body = b''
        if isinstance(self._transport, UrllibTransport):
            result = self._transport(method, url, send, body, max_bytes=max_bytes)
        else:
            result = self._transport(method, url, send, body)
        try:
            status, resp_headers, data = result
            resp_headers = {str(k).lower(): v for k, v in dict(resp_headers).items()}
        except (TypeError, ValueError):
            raise TransportError('malformed_response') from None
        if type(data) is not bytes:
            raise TransportError('malformed_response')
        if len(data) > max_bytes:
            raise TransportError('response_too_large')
        return status, resp_headers, data

    @staticmethod
    def _raise_for_status(status, data):
        if type(status) is not int:
            raise TransportError('malformed_response')
        if 300 <= status < 400:
            raise TransportError('redirect_refused')
        if status >= 400:
            try:
                err = api.parse_model(api.InterfaceError, _decode_json(data))
            except (TransportError, ValidationError):
                raise TransportError('bad_error_body') from None
            raise ClientError(err)
        raise TransportError('bad_status')

    def _json(self, spec, path_values, **kw):
        status, _headers, data = self._request(spec, path_values, **kw)
        if status not in (200, 201):
            self._raise_for_status(status, data)
        try:
            return api.parse_model(spec.response_model, _decode_json(data))
        except ValidationError:
            raise TransportError('malformed_response') from None

    @staticmethod
    def _model(model, **fields):
        try:
            return api.parse_model(model, {k: v for k, v in fields.items()})
        except ValidationError as exc:
            raise ClientError(api.from_validation_error(exc)) from None

    @staticmethod
    def _mel(source) -> bytes:
        if type(source) is not bytes:
            raise ClientError(api.invalid_request('invalid_field', field='source'))
        return source

    # ------------------------------------------------------------------------------------------ MEL

    def meta(self) -> api.MelMeta:
        spec = api.OPERATIONS_BY_NAME['validate']
        route = next(r for r in spec.routes if r.method == 'GET')
        status, _h, data = self._request(spec, {}, route=route)
        if status != 200:
            self._raise_for_status(status, data)
        try:
            return api.parse_model(api.MelMeta, _decode_json(data))
        except ValidationError:
            raise TransportError('malformed_response') from None

    def _stateless(self, name, source, source_id):
        spec = api.OPERATIONS_BY_NAME[name]
        req = self._model(spec.input_model, source_id=source_id)
        return self._json(spec, {}, body=self._mel(source), content_type=api.MEL_MEDIA_TYPE,
                          headers={api.HEADER_SOURCE_ID: req.source_id})

    def validate(self, source: bytes, source_id: str | None = None) -> api.ValidateResult:
        return self._stateless('validate', source, source_id)

    def format(self, source: bytes, source_id: str | None = None) -> api.FormatResult:
        return self._stateless('format', source, source_id)

    def plan(self, source: bytes, source_id: str | None = None) -> api.PlanResult:
        return self._stateless('plan', source, source_id)

    # --------------------------------------------------------------------------------------- programs

    def program_create(self) -> api.ProgramCreated:
        return self._json(api.OPERATIONS_BY_NAME['program_create'], {})

    def _stage(self, name, program_id, source, source_id, expected_parent, require):
        spec = api.OPERATIONS_BY_NAME[name]
        fields = dict(program_id=program_id, source_id=source_id, expected_parent=expected_parent)
        if name == 'stage':
            fields['require_requirements_sha256'] = require
        req = self._model(spec.input_model, **fields)
        return self._json(
            spec, {'pid': req.program_id}, body=self._mel(source), content_type=api.MEL_MEDIA_TYPE,
            headers={api.HEADER_SOURCE_ID: req.source_id, api.HEADER_EXPECTED_PARENT: req.expected_parent,
                     api.HEADER_REQUIRE_REQUIREMENTS: require})

    def stage(self, program_id: str, source: bytes, source_id: str | None = None,
              expected_parent: str | None = None,
              require_requirements_sha256: str | None = None) -> api.StageResponse:
        return self._stage('stage', program_id, source, source_id, expected_parent, require_requirements_sha256)

    def propose(self, program_id: str, source: bytes, source_id: str | None = None,
                expected_parent: str | None = None,
                require_requirements_sha256: str | None = None) -> api.StageResponse:
        """MCP-only stage: same route as stage. The MCP server supplies the guard hash (D12)."""
        return self._stage('propose', program_id, source, source_id, expected_parent, require_requirements_sha256)

    # -------------------------------------------------------------------------------------- revisions

    def review(self, revision_id: str, brief_sha256: str, acknowledged_fields, reviewed_by: str) -> api.RevisionResponse:
        spec = api.OPERATIONS_BY_NAME['review']
        req = self._model(api.ReviewRequest, revision_id=revision_id, brief_sha256=brief_sha256,
                          acknowledged_fields=list(acknowledged_fields), reviewed_by=reviewed_by)
        body = json.dumps(req.model_dump(mode='json', exclude={'revision_id'}), sort_keys=True,
                          separators=(',', ':')).encode('ascii')
        return self._json(spec, {'rid': req.revision_id}, body=body, content_type=_JSON_MEDIA)

    def _simple(self, name, model, **fields):
        spec = api.OPERATIONS_BY_NAME[name]
        req = self._model(model, **fields)
        return spec, req

    def run(self, revision_id: str) -> api.RevisionResponse:
        spec, req = self._simple('run', api.RunRequest, revision_id=revision_id)
        return self._json(spec, {'rid': req.revision_id})

    def status(self, revision_id: str) -> api.RevisionResponse:
        spec, req = self._simple('status', api.StatusRequest, revision_id=revision_id)
        return self._json(spec, {'rid': req.revision_id})

    def cancel(self, revision_id: str) -> api.RevisionResponse:
        spec, req = self._simple('cancel', api.CancelRequest, revision_id=revision_id)
        return self._json(spec, {'rid': req.revision_id})

    def inspect(self, revision_id: str) -> api.InspectBundle:
        spec, req = self._simple('inspect', api.InspectRequest, revision_id=revision_id)
        return self._json(spec, {'rid': req.revision_id})

    def history(self, program_id: str) -> api.HistoryView:
        spec, req = self._simple('history', api.HistoryRequest, program_id=program_id)
        return self._json(spec, {'pid': req.program_id})

    def diff(self, revision_id: str, other_revision_id: str) -> api.DiffResponse:
        spec = api.OPERATIONS_BY_NAME['diff']
        req = self._model(api.DiffRequest, revision_id=revision_id, other_revision_id=other_revision_id)
        body = json.dumps(req.model_dump(mode='json', exclude={'revision_id'}), sort_keys=True,
                          separators=(',', ':')).encode('ascii')
        return self._json(spec, {'rid': req.revision_id}, body=body, content_type=_JSON_MEDIA)

    def restore(self, revision_id: str, expected_head: str | None = None) -> api.RevisionResponse:
        spec, req = self._simple('restore', api.RestoreRequest, revision_id=revision_id,
                                 expected_head=expected_head)
        return self._json(spec, {'rid': req.revision_id}, headers={api.HEADER_EXPECTED_PARENT: req.expected_head})

    def rebuild(self, revision_id: str, expected_parent: str | None = None) -> api.RevisionResponse:
        spec, req = self._simple('rebuild', api.RebuildRequest, revision_id=revision_id,
                                 expected_parent=expected_parent)
        return self._json(spec, {'rid': req.revision_id},
                          headers={api.HEADER_EXPECTED_PARENT: req.expected_parent})

    def export(self, revision_id: str, fmt: str) -> ExportDownload:
        """Download one artifact. The bytes are verified against X-Content-SHA256 and the server file name
        must equal the allowlisted name for ``fmt`` (anything else is a TransportError)."""
        spec = api.OPERATIONS_BY_NAME['export']
        if not api.is_export_format(fmt):
            raise ClientError(api.not_found())
        req = self._model(api.ExportRequest, revision_id=revision_id, fmt=fmt)
        status, headers, data = self._request(spec, {'rid': req.revision_id, 'fmt': fmt},
                                              max_bytes=MAX_EXPORT_BYTES)
        if status != 200:
            if status >= 400 and len(data) > api.MAX_JSON_RESPONSE_BYTES:
                raise TransportError('bad_error_body')
            self._raise_for_status(status, data)
        expected = EXPORT_FILENAMES[fmt]
        match = _DISPOSITION_RE.match(str(headers.get('content-disposition', '')))
        if match is None or match.group(1) != expected:
            raise TransportError('filename_mismatch')
        digest = hashlib.sha256(data).hexdigest()
        claimed = headers.get('x-content-sha256')
        if type(claimed) is not str or not _SHA_RE.match(claimed) or claimed != digest:
            raise TransportError('integrity_mismatch')
        content_type = str(headers.get('content-type', '')).split(';', 1)[0].strip().lower()
        if not _CONTENT_TYPE_RE.match(content_type):
            raise TransportError('malformed_response')
        return ExportDownload(req.revision_id, fmt, expected, content_type, data, digest)
