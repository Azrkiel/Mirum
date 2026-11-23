"""HTTP client for the F08/F09 MEL program interface (/engineering/v1).

Pure stdlib + requests. No imports from backend/engineering (the frontend image does not hold it); the
interface constants are mirrored below as plain module constants and pinned by a backend test.

Rules (F09 D1):
* every call goes through ``requests.request`` (resolved at call time so tests can monkeypatch it);
* redirects are never followed; the token only ever comes from the ``headers`` callable's output;
* ids appear only as validated path segments, never in a query string;
* error messages are fixed per kind; only bounded envelope fields are parsed, never server prose echoed raw;
* exports verify X-Content-SHA256 (and the caller's expected digest) before any bytes are returned.
No image or mesh similarity is computed here or anywhere in the program_* frontend modules.
"""
import hashlib
import json
import re

import requests

API_PREFIX = '/engineering/v1'
API_VERSION = 'mirum_program_api_v0_1'
MEL_MEDIA_TYPE = 'application/vnd.mirum.mel'
JSON_MEDIA_TYPE = 'application/json'
HEADER_SOURCE_ID = 'X-Mirum-Source-Id'
HEADER_EXPECTED_PARENT = 'X-Mirum-Expected-Parent'
HEADER_REQUIRE_REQUIREMENTS = 'X-Mirum-Require-Requirements'
HEADER_MAX_CHARS = {HEADER_SOURCE_ID: 128, HEADER_EXPECTED_PARENT: 32, HEADER_REQUIRE_REQUIREMENTS: 64}
MAX_MEL_BODY_BYTES = 65536
MAX_JSON_RESPONSE_BYTES = 4194304
MAX_EXPORT_BYTES = 33554432
MAX_AI_REQUEST_CHARS = 2000
ID_PATTERN = r'^[0-9a-f]{32}$'
SHA_PATTERN = r'^[0-9a-f]{64}$'
EXPORT_SOURCE_FORMATS = ('program-source', 'program-ir', 'execution-lock')
BUNDLE_FORMATS = (
    'zip', 'brief', 'spec', 'source', 'step', 'glb', 'attempts', 'report', 'markdown', 'manifest',
    'generator-spec', 'generator-report', 'generator-body-step', 'generator-preview-glb',
    'generator-cleaned-stl', 'generator-spec-canonical', 'generator-bundle-zip', 'generator-result')
EXPORT_FORMATS = EXPORT_SOURCE_FORMATS + BUNDLE_FORMATS
ERROR_KINDS = ('invalid_source', 'unsupported', 'unavailable', 'refused', 'conflict', 'expired',
               'integrity', 'busy', 'not_found', 'too_large', 'programs_disabled', 'unauthorized',
               'invalid_request', 'unreachable')
KIND_STATUS = {
    'invalid_source': 422, 'unsupported': 422, 'unavailable': 503, 'refused': 409, 'conflict': 409,
    'expired': 410, 'integrity': 409, 'busy': 429, 'not_found': 404, 'too_large': 413,
    'programs_disabled': 503, 'unauthorized': 401, 'invalid_request': 422}
STATUS_KIND = {422: 'invalid_request', 503: 'unavailable', 409: 'conflict', 410: 'expired', 429: 'busy',
               404: 'not_found', 413: 'too_large', 401: 'unauthorized'}
DEFAULT_TIMEOUT = (5, 100)

KIND_MESSAGES = {
    'invalid_source': 'The program source has errors. Fix them and validate again.',
    'unsupported': 'This build does not support that program. Use a supported capability and backend.',
    'unavailable': 'A required service is unavailable. Your editor text is kept; try again later.',
    'refused': 'The server refused this action. Review the stated reason and try again.',
    'conflict': 'The program changed elsewhere. Refresh and rebase before trying again.',
    'expired': 'This revision or its files expired. Rebuild it to produce fresh files.',
    'integrity': 'Stored files failed an integrity check. No file is offered. Rebuild the revision.',
    'busy': 'The server is busy. Try again after the pause shown.',
    'not_found': 'That program or revision was not found.',
    'too_large': 'The request or response is larger than the limit.',
    'programs_disabled': 'Engineering programs are not enabled on this server.',
    'unauthorized': 'Authentication is required.',
    'invalid_request': 'The request was not valid. Check the inputs and try again.',
    'unreachable': 'The engineering service could not be reached. Your editor text is kept.',
}

_ID_RE = re.compile(ID_PATTERN)
_SHA_RE = re.compile(SHA_PATTERN)
_CODE_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
_NAME_RE = re.compile(r'^[a-z_][a-z0-9_]{0,63}$')
_REQID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')
_CATEGORIES = ('lexical', 'syntax', 'semantic', 'resource', 'plan', 'revision', 'capacity', 'request',
               'service')
_NEXT_ACTIONS = ('none', 'fix_source', 'fix_request', 'refresh_and_rebase', 're_review', 'retry_later',
                 'restage', 'use_supported_capability', 'contact_operator', 'authenticate')
_MAX_DEPTH = 8


class ProgramAPIError(Exception):
    """A refusal or transport failure. ``str(error)`` is the fixed per-kind message.

    ``detail_message`` and ``detail_hint`` are the bounded printable-ASCII message/hint of the server's
    fixed diagnostic tables (never exception text); always render them through a Markdown escaper.
    """

    def __init__(self, status, kind, code='', next_action='none', *, span=None, retryable=False,
                 retry_after=None, requirement_ids=(), field=None, category=None, detail_message='',
                 detail_hint=''):
        if kind not in ERROR_KINDS:
            kind = 'unavailable'
        super().__init__(KIND_MESSAGES[kind])
        self.status = status
        self.kind = kind
        self.code = code
        self.next_action = next_action
        self.span = span
        self.retryable = retryable
        self.retry_after = retry_after
        self.requirement_ids = tuple(requirement_ids)
        self.field = field
        self.category = category
        self.detail_message = detail_message
        self.detail_hint = detail_hint


def _clean_text(value, limit=240):
    if type(value) is not str or len(value) > limit:
        return ''
    return value if all(' ' <= c <= '~' for c in value) else ''


def _int_in(value, low, high):
    return value if type(value) is int and low <= value <= high else None


def _parse_span(value):
    if not isinstance(value, dict):
        return None
    out = {}
    for key in ('line', 'column', 'end_line', 'end_column'):
        number = _int_in(value.get(key), 1, 10 ** 9)
        if number is None:
            return None
        out[key] = number
    return out


def _json_depth_ok(value):
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > _MAX_DEPTH:
            return False
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    return True


def _loads(data):
    """Bounded JSON parse; None when the bytes are not an acceptable JSON object."""
    try:
        value = json.loads(data.decode('utf-8'), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) and _json_depth_ok(value) else None


def _reject_constant(_name):
    raise ValueError('non-finite')


def error_from_response(status, data, retry_after_header=None):
    """Build a ProgramAPIError from an HTTP status and the (bounded) body bytes."""
    body = _loads(data) if isinstance(data, (bytes, bytearray)) and len(data) <= MAX_JSON_RESPONSE_BYTES else None
    envelope = body if body and body.get('api_version') == 'mirum_interface_error_v0_1' else None
    if envelope is None:
        return ProgramAPIError(status, STATUS_KIND.get(status, 'unavailable'))
    kind = envelope.get('kind')
    if kind not in KIND_STATUS:
        kind = STATUS_KIND.get(status, 'unavailable')
    code = envelope.get('code')
    code = code if type(code) is str and _CODE_RE.match(code) else ''
    category = envelope.get('category') if envelope.get('category') in _CATEGORIES else None
    action = envelope.get('next_action') if envelope.get('next_action') in _NEXT_ACTIONS else 'none'
    ids = envelope.get('requirement_ids')
    ids = tuple(i for i in ids[:16] if type(i) is str and _REQID_RE.match(i)) if isinstance(ids, list) else ()
    field = envelope.get('field')
    field = field if type(field) is str and _NAME_RE.match(field) else None
    retry_after = _int_in(envelope.get('retry_after'), 1, 86400)
    if retry_after is None and retry_after_header and str(retry_after_header).isdigit():
        retry_after = _int_in(int(retry_after_header), 1, 86400)
    return ProgramAPIError(
        status, kind, code, action, span=_parse_span(envelope.get('span')),
        retryable=envelope.get('retryable') is True, retry_after=retry_after, requirement_ids=ids,
        field=field, category=category, detail_message=_clean_text(envelope.get('message')),
        detail_hint=_clean_text(envelope.get('hint')))


def _local_error(code, kind='invalid_request', field=None):
    return ProgramAPIError(KIND_STATUS.get(kind, 0), kind, code, 'fix_request', field=field)


def _check_id(value, field):
    if type(value) is not str or not _ID_RE.match(value):
        raise _local_error('invalid_field', field=field)
    return value


def _check_sha(value, field):
    if type(value) is not str or not _SHA_RE.match(value):
        raise _local_error('invalid_field', field=field)
    return value


def _check_header(name, value):
    """Validate a header value like the backend's check_header_value; raise a local invalid_request."""
    limit = HEADER_MAX_CHARS[name]
    if type(value) is not str or not value or len(value) > limit:
        raise _local_error('invalid_header')
    if not value.isascii() or any(ord(c) < 0x21 or ord(c) == 0x7f for c in value):
        raise _local_error('invalid_header')
    if name == HEADER_EXPECTED_PARENT and not _ID_RE.match(value):
        raise _local_error('invalid_header')
    if name == HEADER_REQUIRE_REQUIREMENTS and not _SHA_RE.match(value):
        raise _local_error('invalid_header')
    return value


def _source_bytes(source):
    data = source.encode('utf-8') if isinstance(source, str) else source
    if type(data) is not bytes:
        raise _local_error('invalid_field')
    if len(data) > MAX_MEL_BODY_BYTES:
        raise _local_error('body_too_large', 'too_large')
    return data


class ProgramClient:
    """Thin client. ``headers`` is called for every request and is the only source of credentials."""

    def __init__(self, backend_url, headers, *, timeout=DEFAULT_TIMEOUT, session_request=None):
        self.base = str(backend_url).rstrip('/') + API_PREFIX
        self._headers = headers
        self.timeout = timeout
        self._request = session_request

    # ---------------------------------------------------------------------------------------- plumbing

    def _send(self, method, path, *, extra=None, data=None, json_body=None, limit=MAX_JSON_RESPONSE_BYTES):
        headers = dict(self._headers() or {})
        headers.update(extra or {})
        kwargs = dict(headers=headers, timeout=self.timeout, allow_redirects=False)
        if json_body is not None:
            kwargs['data'] = json.dumps(json_body, separators=(',', ':')).encode('utf-8')
            headers['Content-Type'] = JSON_MEDIA_TYPE
        elif data is not None:
            kwargs['data'] = data
        send = self._request or requests.request
        try:
            response = send(method, self.base + path, **kwargs)
        except requests.RequestException:
            raise ProgramAPIError(0, 'unreachable') from None
        status = response.status_code
        content = response.content
        if 300 <= status < 400:
            raise ProgramAPIError(status, 'unavailable', 'unexpected_redirect')
        if not 200 <= status < 300:
            raise error_from_response(status, content[:MAX_JSON_RESPONSE_BYTES + 1],
                                      (response.headers or {}).get('Retry-After'))
        if len(content) > limit:
            raise ProgramAPIError(status, 'too_large', 'response_too_large')
        return response

    def _json(self, method, path, **kwargs):
        response = self._send(method, path, **kwargs)
        value = _loads(response.content)
        if value is None:
            raise ProgramAPIError(response.status_code, 'unavailable', 'invalid_response')
        return value

    def _mel(self, method, path, source, source_id=None, headers=None):
        extra = {'Content-Type': MEL_MEDIA_TYPE}
        if source_id is not None:
            extra[HEADER_SOURCE_ID] = _check_header(HEADER_SOURCE_ID, source_id)
        extra.update(headers or {})
        return self._json(method, path, extra=extra, data=_source_bytes(source))

    # ---------------------------------------------------------------------------------------------- MEL

    def meta(self):
        return self._json('GET', '/mel/meta')

    def validate(self, source, *, source_id=None):
        return self._mel('POST', '/mel/validate', source, source_id)

    def format(self, source, *, source_id=None):
        return self._mel('POST', '/mel/format', source, source_id)

    def plan(self, source, *, source_id=None):
        return self._mel('POST', '/mel/plan', source, source_id)

    # ------------------------------------------------------------------------------------------ programs

    def create_program(self):
        return self._json('POST', '/programs')

    def list_programs(self):
        return self._json('GET', '/programs')

    def program(self, pid):
        return self._json('GET', '/programs/%s' % _check_id(pid, 'program_id'))

    def history(self, pid):
        return self._json('GET', '/programs/%s/history' % _check_id(pid, 'program_id'))

    def journal(self, pid):
        return self._json('GET', '/programs/%s/journal' % _check_id(pid, 'program_id'))

    def stage(self, pid, source, *, expected_parent, require_requirements_sha256=None, source_id=None):
        path = '/programs/%s/revisions' % _check_id(pid, 'program_id')
        extra = {}
        if expected_parent is not None:
            extra[HEADER_EXPECTED_PARENT] = _check_header(HEADER_EXPECTED_PARENT, expected_parent)
        if require_requirements_sha256 is not None:
            extra[HEADER_REQUIRE_REQUIREMENTS] = _check_header(
                HEADER_REQUIRE_REQUIREMENTS, require_requirements_sha256)
        return self._mel('POST', path, source, source_id, extra)

    def ai_edit(self, pid, request, *, expected_parent=None):
        path = '/programs/%s/ai-edits' % _check_id(pid, 'program_id')
        if type(request) is not str or not request.strip() or len(request) > MAX_AI_REQUEST_CHARS:
            raise _local_error('invalid_field', field='request')
        _check_id(expected_parent, 'expected_parent')
        return self._json('POST', path, json_body={
            'api_version': API_VERSION, 'request': request, 'expected_parent': expected_parent})

    # ----------------------------------------------------------------------------------------- revisions

    def status(self, rid):
        return self._json('GET', '/revisions/%s' % _check_id(rid, 'revision_id'))

    def inspect(self, rid):
        return self._json('GET', '/revisions/%s/evidence' % _check_id(rid, 'revision_id'))

    def review(self, rid, brief_sha256, acknowledged_fields, reviewed_by):
        path = '/revisions/%s/review' % _check_id(rid, 'revision_id')
        fields = list(acknowledged_fields)
        if type(reviewed_by) is not str or not 1 <= len(reviewed_by) <= 120 or not _clean_text(reviewed_by, 120):
            raise _local_error('invalid_field', field='reviewed_by')
        return self._json('POST', path, json_body={
            'api_version': API_VERSION, 'brief_sha256': _check_sha(brief_sha256, 'brief_sha256'),
            'acknowledged_fields': fields, 'reviewed_by': reviewed_by})

    def run(self, rid):
        return self._json('POST', '/revisions/%s/run' % _check_id(rid, 'revision_id'))

    def cancel(self, rid):
        return self._json('POST', '/revisions/%s/cancel' % _check_id(rid, 'revision_id'))

    def compare(self, rid, other_rid):
        path = '/revisions/%s/compare' % _check_id(rid, 'revision_id')
        return self._json('POST', path, json_body={
            'api_version': API_VERSION, 'other_revision_id': _check_id(other_rid, 'other_revision_id')})

    def _with_parent(self, rid, action, expected_parent):
        path = '/revisions/%s/%s' % (_check_id(rid, 'revision_id'), action)
        extra = {}
        if expected_parent is not None:
            extra[HEADER_EXPECTED_PARENT] = _check_header(HEADER_EXPECTED_PARENT, expected_parent)
        return self._json('POST', path, extra=extra)

    def restore(self, rid, expected_parent):
        return self._with_parent(rid, 'restore', expected_parent)

    def rebuild(self, rid, expected_parent):
        return self._with_parent(rid, 'rebuild', expected_parent)

    def export(self, rid, fmt, *, expected_sha256=None):
        """Bytes of one export, only when X-Content-SHA256 equals sha256(body) and, when given, the
        caller's expected digest (from that revision's own inspect bundle)."""
        if type(fmt) is not str or fmt not in EXPORT_FORMATS:
            raise _local_error('invalid_field', field='fmt')
        if expected_sha256 is not None:
            _check_sha(expected_sha256, 'expected_sha256')
        path = '/revisions/%s/artifacts/%s' % (_check_id(rid, 'revision_id'), fmt)
        response = self._send('GET', path, limit=MAX_EXPORT_BYTES)
        body = response.content
        digest = hashlib.sha256(body).hexdigest()
        header = (response.headers or {}).get('X-Content-SHA256')
        if header != digest or (expected_sha256 is not None and expected_sha256 != digest):
            raise ProgramAPIError(response.status_code, 'integrity', 'download_digest_mismatch',
                                  'restage')
        return bytes(body)
