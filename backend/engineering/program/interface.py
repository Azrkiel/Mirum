"""F08 program interface contract: operation registry, request/response models, error envelope.

PURE. Imports only the stdlib, pydantic, engineering.program.versions and the pure record modules
engineering.program.revisions / engineering.program.evidence / engineering.language.diagnostics.
Never the store, sqlite3, a web framework, an HTTP client, a native module, transactions, the
revision store or jobs (a subprocess test pins this).

Every transport (Python facade, HTTP routes, CLI, MCP) is generated from, or checked against, this
module, so equivalent requests reach identical checks and identical envelopes.

Decisions recorded here (F08 brief D1, D2, D5, D7, D8, D12, D13):

* The registry has 16 entries: the 15 operations of D2 plus the MCP-only tool ``propose``. ``propose``
  stages through the same HTTP route as ``stage`` but its tool inputs never carry the requirements guard:
  the MCP server reads the program head and sends ``X-Mirum-Require-Requirements`` itself (D12).
* ``GET /mel/meta`` is a secondary route of ``validate``; ``GET /programs/{pid}`` is a secondary route of
  ``history``. ``inspect`` = view + evidence + journal (three routes).
* MEL bodies (validate, format, plan, stage) are raw bytes (application/vnd.mirum.mel). The request models
  carry only the non-body fields; ``mcp_input_schema`` adds the ``source`` text argument for MCP tools.
* ``invalid_request`` (422) is an additive kind for malformed small JSON bodies and headers.
* Error messages come only from the fixed tables below (keyed by code) and from the MEL diagnostics
  tables; builders take codes, never exception text.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from engineering.language.diagnostics import HINTS as _MEL_HINTS
from engineering.language.diagnostics import LEXICAL as _MEL_LEXICAL
from engineering.language.diagnostics import SEMANTIC as _MEL_SEMANTIC
from engineering.language.diagnostics import SYNTAX as _MEL_SYNTAX
from .revisions import BundleDigests, EvidenceSummary, RevisionDiff
from .versions import INTERFACE_ERROR_VERSION, PROGRAM_API_VERSION

__all__ = [
    'API_PREFIX', 'MEL_MEDIA_TYPE', 'HEADER_SOURCE_ID', 'HEADER_EXPECTED_PARENT',
    'HEADER_REQUIRE_REQUIREMENTS', 'HEADER_MAX_CHARS', 'MAX_MEL_BODY_BYTES', 'MAX_JSON_BODY_BYTES',
    'MAX_JSON_RESPONSE_BYTES', 'MAX_MCP_MESSAGE_BYTES', 'MAX_JSON_DEPTH', 'ID_PATTERN',
    'EXPORT_SOURCE_FORMATS', 'BUNDLE_FORMATS', 'EXPORT_FORMATS', 'is_export_format', 'check_header_value',
    'json_depth', 'bounded_json_loads', 'BoundsError', 'iso_utc', 'parse_model',
    'TRANSPORTS', 'Route', 'OperationSpec', 'OPERATIONS', 'OPERATIONS_BY_NAME', 'MCP_TOOL_NAMES',
    'get_operation', 'full_path', 'mcp_input_schema', 'mcp_tool_descriptors',
    'ValidateRequest', 'FormatRequest', 'PlanRequest', 'ProgramCreateRequest', 'StageRequest',
    'ProposeRequest', 'ReviewRequest', 'RunRequest', 'StatusRequest', 'CancelRequest', 'InspectRequest',
    'HistoryRequest', 'DiffRequest', 'RestoreRequest', 'RebuildRequest', 'ExportRequest',
    'RESULT_KINDS', 'result_kind', 'ErrorSpan', 'MelMeta', 'ValidateResult', 'FormatResult',
    'PlanResult', 'RevisionStatus', 'ProgramView', 'ProgramCreated', 'RevisionResponse',
    'StageResponse', 'JournalLine', 'EvidenceCheck', 'EvidenceView', 'InspectBundle', 'HistoryView',
    'DiffResponse', 'ExportMetadata', 'RESPONSE_MODELS', 'API_SCHEMA_MODELS',
    'ERROR_KINDS', 'ERROR_CATEGORIES', 'CHANGED_VALUES', 'NEXT_ACTIONS', 'KIND_STATUS', 'KIND_EXIT_CODE',
    'RESULT_KIND_EXIT_CODE', 'EXIT_OK', 'EXIT_TRANSPORT', 'EXIT_INVALID', 'EXIT_UNAVAILABLE',
    'EXIT_CONFLICT', 'ErrorEntry', 'ERROR_TABLE', 'InterfaceError', 'http_status', 'exit_code',
    'result_exit_code', 'error', 'from_mel', 'from_plan_refusal', 'from_revision_refusal',
    'from_validation_error', 'InterfaceFailure', 'busy', 'not_found', 'too_large', 'unavailable', 'programs_disabled',
    'unauthorized', 'invalid_request',
]

# ------------------------------------------------------------------------------------------ constants

API_PREFIX = '/engineering/v1'
MEL_MEDIA_TYPE = 'application/vnd.mirum.mel'
HEADER_SOURCE_ID = 'X-Mirum-Source-Id'
HEADER_EXPECTED_PARENT = 'X-Mirum-Expected-Parent'
HEADER_REQUIRE_REQUIREMENTS = 'X-Mirum-Require-Requirements'
# Maximum characters of each header value (source id: MEL-S016 limit; ids: 32 hex; hash: 64 hex).
HEADER_MAX_CHARS = {HEADER_SOURCE_ID: 128, HEADER_EXPECTED_PARENT: 32, HEADER_REQUIRE_REQUIREMENTS: 64}
# Equal to ENGINEERING_MAX_BODY_BYTES (backend/config.py default) and revisions.MAX_SOURCE_BYTES.
MAX_MEL_BODY_BYTES = 65536
MAX_JSON_BODY_BYTES = 65536          # small JSON request bodies (review, compare)
MAX_JSON_RESPONSE_BYTES = 4194304    # 4 MiB, the CLI and HTTP SDK response cap
MAX_MCP_MESSAGE_BYTES = 1048576      # 1 MiB per JSON-RPC line (a 64 KiB source fits with slack)
MAX_JSON_DEPTH = 8                   # nesting depth of any accepted small JSON body
MAX_EXPORT_BYTES = 64 * 1024**2
MAX_GENERATOR_STL_EXPORT_BYTES = 128 * 1024**2

# Program and revision ids are uuid4().hex (revision_store.create_program, revisions.Id32).
ID_PATTERN = r'^[0-9a-f]{32}$'
_ID_RE = re.compile(ID_PATTERN)
EXPORT_SOURCE_FORMATS = ('program-source', 'program-ir', 'execution-lock')   # D11
# Keys of engineering.bundles.ALL_FORMATS (FORMATS + GENERATOR_FORMATS); a test pins equality.
BUNDLE_FORMATS = (
    'zip', 'brief', 'spec', 'source', 'step', 'glb', 'attempts', 'report', 'markdown', 'manifest',
    'generator-spec', 'generator-report', 'generator-body-step', 'generator-preview-glb',
    'generator-cleaned-stl', 'generator-spec-canonical', 'generator-bundle-zip', 'generator-result',
    'generator-bom')
EXPORT_FORMATS = EXPORT_SOURCE_FORMATS + BUNDLE_FORMATS


def is_export_format(fmt) -> bool:
    return type(fmt) is str and fmt in EXPORT_FORMATS


def export_byte_limit(fmt: str) -> int:
    return MAX_GENERATOR_STL_EXPORT_BYTES if fmt == 'generator-cleaned-stl' else MAX_EXPORT_BYTES


class BoundsError(ValueError):
    """A bounded reader refused its input. ``code`` is an ERROR_TABLE code; the text is the code only."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def check_header_value(name: str, value) -> str | None:
    """None when fine, else an ERROR_TABLE code (header_too_large or invalid_header). Never raises."""
    limit = HEADER_MAX_CHARS.get(name)
    if limit is None or type(value) is not str:
        return 'invalid_header'
    if len(value) > limit:
        return 'header_too_large'
    if not value or not value.isascii() or any(ord(c) < 0x21 or ord(c) == 0x7f for c in value):
        return 'invalid_header'
    if name in (HEADER_EXPECTED_PARENT,) and not _ID_RE.match(value):
        return 'invalid_header'
    if name == HEADER_REQUIRE_REQUIREMENTS and not re.fullmatch(r'[0-9a-f]{64}', value):
        return 'invalid_header'
    return None


def json_depth(value) -> int:
    """Nesting depth (a scalar is 0, [] is 1). Iterative, so a deep value cannot overflow the stack."""
    deepest = 0
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            deepest = max(deepest, depth + 1)
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            deepest = max(deepest, depth + 1)
            stack.extend((v, depth + 1) for v in item)
    return deepest


def _reject_constant(_name):
    raise ValueError('non-finite')


def bounded_json_loads(data: bytes, *, max_bytes: int = MAX_JSON_BODY_BYTES, max_depth: int = MAX_JSON_DEPTH):
    """Parse a JSON body with a size cap, a depth cap, no NaN/Infinity. Raises BoundsError(code) only."""
    if type(data) is not bytes:
        raise BoundsError('malformed_json')
    if len(data) > max_bytes:
        raise BoundsError('body_too_large')
    try:
        text = data.decode('utf-8')
        value = json.loads(text, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        # A pathological nesting depth can overflow the parser itself: the same fixed code.
        raise BoundsError('malformed_json') from None
    if json_depth(value) > max_depth:
        raise BoundsError('json_too_deep')
    return value


_ISO_RE = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$'


def iso_utc(moment) -> str:
    """UTC ISO-8601 with a Z suffix from an aware datetime (timestamps are strings in API bodies)."""
    from datetime import timezone
    text = moment.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec='microseconds')
    return text + 'Z'


# ----------------------------------------------------------------------------------------- base models

class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


def parse_model(model: type[BaseModel], data):
    """Validate decoded JSON (dict) or JSON text/bytes into ``model``, accepting JSON arrays for tuples."""
    if isinstance(data, (bytes, str)):
        return model.model_validate_json(data)
    return model.model_validate(data, strict=False)


Id32 = Annotated[str, StringConstraints(min_length=32, max_length=32, pattern=ID_PATTERN)]
Sha256 = Annotated[str, StringConstraints(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')]
SourceId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Code = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
Name64 = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r'^[a-z_][a-z0-9_]{0,63}$')]
# Review field names include the dotted material leaves of REVIEW_FIELDS (material.youngs_modulus_mpa).
ReviewField = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r'^[a-z_][a-z0-9_.]{0,63}$')]
Text240 = Annotated[str, StringConstraints(max_length=240, pattern=r'^[ -~]*$')]
Stamp = Annotated[str, StringConstraints(pattern=_ISO_RE, max_length=32)]
_API = Literal['mirum_program_api_v0_1']
_Count = Annotated[int, Field(ge=0, le=2 ** 53)]


class _Request(_Model):
    api_version: _API = PROGRAM_API_VERSION


# -------------------------------------------------------------------------------------- request models
# MEL source text is NOT a field of any of these (D5: bodies are raw bytes).

class ValidateRequest(_Request):
    source_id: SourceId | None = Field(default=None, description='Label for the source in diagnostics.')


class FormatRequest(_Request):
    source_id: SourceId | None = Field(default=None, description='Label for the source in diagnostics.')


class PlanRequest(_Request):
    source_id: SourceId | None = Field(default=None, description='Label for the source in diagnostics.')


class ProgramCreateRequest(_Request):
    pass


class StageRequest(_Request):
    program_id: Id32
    source_id: SourceId | None = Field(default=None, description='Label for the source in diagnostics.')
    expected_parent: Id32 | None = Field(
        default=None, description='Head revision the edit is based on; null for a program with no head.')
    require_requirements_sha256: Sha256 | None = Field(
        default=None, description='Refuse when the head fixed requirements hash differs.')


class ProposeRequest(_Request):
    """MCP-only stage. The guard hash is added by the MCP server from the program head (D12)."""
    program_id: Id32
    source_id: SourceId | None = Field(default=None, description='Label for the source in diagnostics.')
    expected_parent: Id32 | None = Field(
        default=None, description='Head revision the edit is based on; the program must have a head.')


class ReviewRequest(_Request):
    revision_id: Id32
    brief_sha256: Sha256
    acknowledged_fields: Annotated[tuple[ReviewField, ...], Field(max_length=32)]
    reviewed_by: Annotated[str, StringConstraints(min_length=1, max_length=120, pattern=r'^[ -~]{1,120}$')]


class RunRequest(_Request):
    revision_id: Id32


class StatusRequest(_Request):
    revision_id: Id32


class CancelRequest(_Request):
    revision_id: Id32


class InspectRequest(_Request):
    revision_id: Id32


class HistoryRequest(_Request):
    program_id: Id32


class DiffRequest(_Request):
    revision_id: Id32
    other_revision_id: Id32


class RestoreRequest(_Request):
    revision_id: Id32
    expected_head: Id32 | None = None


class RebuildRequest(_Request):
    revision_id: Id32
    expected_parent: Id32 | None = None


class ExportRequest(_Request):
    revision_id: Id32
    fmt: Annotated[str, StringConstraints(min_length=1, max_length=64)]


# ------------------------------------------------------------------------------------- response models
# No response model may carry draft_id, brief_id or job_id (D8: bearer ids of the unchecked v1 routes).

RESULT_KINDS = ('published', 'rejected', 'unavailable', 'verification_failed', 'pending', 'failed',
                'cancelled', 'conflict')
ResultKind = Literal['published', 'rejected', 'unavailable', 'verification_failed', 'pending', 'failed',
                     'cancelled', 'conflict']
_UNAVAILABLE_REASONS = frozenset({'archive_unavailable', 'delivered_evidence_unavailable', 'job_unavailable'})
_VERIFICATION_REASONS = frozenset({'delivered_check_failed', 'delivered_unverified',
                                   'delivered_evidence_stale', 'delivered_binding_ambiguous'})


def result_kind(state: str, reason: str | None = None) -> str:
    """D7: policy rejection is a result (rejected), not an error; verifier outcomes keep their own kind."""
    if state == 'published':
        return 'published'
    if state == 'rejected':
        return 'rejected'
    if state in ('staged', 'running'):
        return 'pending'
    if state == 'cancelled':
        return 'cancelled'
    if state == 'conflict':
        return 'conflict'
    if reason in _UNAVAILABLE_REASONS:
        return 'unavailable'
    if reason in _VERIFICATION_REASONS:
        return 'verification_failed'
    return 'failed'


class ErrorSpan(_Model):
    line: Annotated[int, Field(ge=1, le=10 ** 9)]
    column: Annotated[int, Field(ge=1, le=10 ** 9)]
    end_line: Annotated[int, Field(ge=1, le=10 ** 9)]
    end_column: Annotated[int, Field(ge=1, le=10 ** 9)]
    source_id: SourceId | None = None


class _Response(_Model):
    api_version: _API = PROGRAM_API_VERSION


class MelMeta(_Response):
    language_version: Annotated[str, StringConstraints(max_length=16)]
    compiler_version: Annotated[str, StringConstraints(max_length=64)]
    media_type: Literal['application/vnd.mirum.mel'] = MEL_MEDIA_TYPE
    max_source_bytes: Literal[65536] = MAX_MEL_BODY_BYTES


class ValidateResult(_Response):
    ok: Literal[True] = True
    source_id: SourceId | None = None
    source_sha256: Sha256
    semantic_sha256: Sha256
    language_version: Annotated[str, StringConstraints(max_length=16)]
    compiler_version: Annotated[str, StringConstraints(max_length=64)]


class FormatResult(_Response):
    ok: Literal[True] = True
    source_id: SourceId | None = None
    formatted_source: Annotated[str, StringConstraints(max_length=262144)]
    formatted_sha256: Sha256
    unchanged: bool


class PlanResult(_Response):
    ok: Literal[True] = True
    source_id: SourceId | None = None
    semantic_sha256: Sha256
    mode: Literal['native']
    backend: Annotated[str, StringConstraints(max_length=64)]
    candidate_count: Annotated[int, Field(ge=1, le=16)]


class RevisionStatus(_Response):
    revision_id: Id32
    program_id: Id32
    parent: Id32 | None = None
    kind: Literal['edit', 'restore', 'rebuild']
    derived_from: Id32 | None = None
    evidence_revision_id: Id32 | None = None
    state: Literal['staged', 'running', 'published', 'rejected', 'failed', 'cancelled', 'conflict']
    reason: Annotated[str, StringConstraints(max_length=64, pattern=r'^[a-z0-9_]{1,64}$')] | None = None
    result_kind: ResultKind
    source_sha256: Sha256
    semantic_sha256: Sha256
    brief_sha256: Sha256
    fixed_requirements_sha256: Sha256
    lock_sha256: Sha256
    bundle: BundleDigests | None = None
    published_seq: Annotated[int, Field(ge=1, le=2 ** 53)] | None = None
    created_at: Stamp
    updated_at: Stamp
    artifacts: Literal['available', 'expired', 'missing', 'none']
    is_current: bool


class ProgramView(_Response):
    program_id: Id32
    head_revision_id: Id32 | None = None
    head_seq: _Count
    created_at: Stamp
    updated_at: Stamp
    current: RevisionStatus | None = None


class ProgramCreated(_Response):
    program_id: Id32


class RevisionResponse(_Response):
    """status, review, run, cancel, restore, rebuild: one revision view."""
    revision: RevisionStatus


class StageResponse(RevisionResponse):
    review_fields: Annotated[tuple[ReviewField, ...], Field(max_length=32)] = ()


class JournalLine(_Model):
    seq: Annotated[int, Field(ge=1, le=2 ** 53)]
    revision_id: Id32 | None = None
    event: Annotated[str, StringConstraints(max_length=32, pattern=r'^[a-z_]{1,32}$')]
    detail: dict[Annotated[str, StringConstraints(pattern=r'^[a-z_]{1,32}$')],
                 str | int | bool | None]
    created_at: Stamp


class EvidenceCheck(_Model):
    check_id: Annotated[str, StringConstraints(max_length=240)]
    category: Annotated[str, StringConstraints(max_length=32)]
    required: bool
    state: Literal['passed', 'failed', 'not_evaluated', 'unavailable']
    coverage: Literal['measured', 'policy_report', 'identity_only', 'not_evaluated']
    reason: Annotated[str, StringConstraints(max_length=64, pattern=r'^[a-z_]{1,64}$')] | None = None


class EvidenceView(_Model):
    status: Literal['present', 'none']
    verdict: Literal['verified', 'failed', 'ambiguous', 'unavailable'] | None = None
    claim_scope: Annotated[str, StringConstraints(max_length=64)] | None = None
    reasons: Annotated[tuple[Annotated[str, StringConstraints(max_length=64)], ...], Field(max_length=8)] = ()
    checks: Annotated[tuple[EvidenceCheck, ...], Field(max_length=32)] = ()
    candidate_sha256: Sha256 | None = None
    manifest_sha256: Sha256 | None = None
    step_sha256: Sha256 | None = None
    glb_sha256: Sha256 | None = None
    check_plan_sha256: Sha256 | None = None
    method_version: Annotated[str, StringConstraints(max_length=64)] | None = None
    run: EvidenceSummary | None = None


class InspectBundle(_Response):
    revision: RevisionStatus
    evidence: EvidenceView
    journal: Annotated[tuple[JournalLine, ...], Field(max_length=1024)] = ()


class HistoryView(_Response):
    program_id: Id32
    head_revision_id: Id32 | None = None
    revisions: Annotated[tuple[RevisionStatus, ...], Field(max_length=256)]


class DiffResponse(_Response):
    diff: RevisionDiff


class ExportMetadata(_Response):
    revision_id: Id32
    fmt: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    filename: Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9._-]{1,64}$')]
    content_type: Annotated[str, StringConstraints(max_length=64, pattern=r'^[a-z]+/[a-z0-9.+-]+$')]
    size_bytes: _Count
    sha256: Sha256


RESPONSE_MODELS = (MelMeta, ValidateResult, FormatResult, PlanResult, RevisionStatus, ProgramView,
                   ProgramCreated, RevisionResponse, StageResponse, JournalLine, EvidenceCheck,
                   EvidenceView, InspectBundle, HistoryView, DiffResponse, ExportMetadata)


# ------------------------------------------------------------------------------------ error envelope

ERROR_KINDS = ('invalid_source', 'unsupported', 'unavailable', 'refused', 'conflict', 'expired',
               'integrity', 'busy', 'not_found', 'too_large', 'programs_disabled', 'unauthorized',
               'invalid_request')
ERROR_CATEGORIES = ('lexical', 'syntax', 'semantic', 'resource', 'plan', 'revision', 'capacity', 'request',
                    'service')
CHANGED_VALUES = ('nothing_written', 'revision_staged', 'job_admitted')
NEXT_ACTIONS = ('none', 'fix_source', 'fix_request', 'refresh_and_rebase', 're_review', 'retry_later',
                'restage', 'use_supported_capability', 'contact_operator', 'authenticate')
KIND_STATUS = {
    'invalid_source': 422, 'unsupported': 422, 'unavailable': 503, 'refused': 409, 'conflict': 409,
    'expired': 410, 'integrity': 409, 'busy': 429, 'not_found': 404, 'too_large': 413,
    'programs_disabled': 503, 'unauthorized': 401, 'invalid_request': 422,
}
EXIT_OK, EXIT_TRANSPORT, EXIT_INVALID, EXIT_UNAVAILABLE, EXIT_CONFLICT = 0, 1, 2, 3, 4
# D13 plus decisions: not_found and too_large are bad input (2); expired and integrity are state
# conflicts the caller must resolve (4); unauthorized is a transport-level failure (1).
KIND_EXIT_CODE = {
    'invalid_source': EXIT_INVALID, 'unsupported': EXIT_INVALID, 'invalid_request': EXIT_INVALID,
    'not_found': EXIT_INVALID, 'too_large': EXIT_INVALID,
    'unavailable': EXIT_UNAVAILABLE, 'busy': EXIT_UNAVAILABLE, 'programs_disabled': EXIT_UNAVAILABLE,
    'conflict': EXIT_CONFLICT, 'refused': EXIT_CONFLICT, 'expired': EXIT_CONFLICT,
    'integrity': EXIT_CONFLICT,
    'unauthorized': EXIT_TRANSPORT,
}
RESULT_KIND_EXIT_CODE = {
    'published': EXIT_OK, 'pending': EXIT_OK, 'cancelled': EXIT_OK,
    'rejected': EXIT_INVALID, 'verification_failed': EXIT_INVALID,
    'unavailable': EXIT_UNAVAILABLE, 'failed': EXIT_UNAVAILABLE,
    'conflict': EXIT_CONFLICT,
}
_REQ_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')


class InterfaceError(_Model):
    api_version: Literal['mirum_interface_error_v0_1'] = INTERFACE_ERROR_VERSION
    kind: Literal['invalid_source', 'unsupported', 'unavailable', 'refused', 'conflict', 'expired',
                  'integrity', 'busy', 'not_found', 'too_large', 'programs_disabled', 'unauthorized',
                  'invalid_request']
    code: Code
    category: Literal['lexical', 'syntax', 'semantic', 'resource', 'plan', 'revision', 'capacity',
                      'request', 'service']
    message: Text240
    hint: Text240 | None = None
    span: ErrorSpan | None = None
    field: Name64 | None = None
    requirement_ids: Annotated[tuple[Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')], ...],
                               Field(max_length=16)] = ()
    operation_ids: Annotated[tuple[Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')], ...],
                             Field(max_length=16)] = ()
    retryable: bool
    retry_after: Annotated[int, Field(ge=1, le=86400)] | None = None
    changed: Literal['nothing_written', 'revision_staged', 'job_admitted'] = 'nothing_written'
    next_action: Literal['none', 'fix_source', 'fix_request', 'refresh_and_rebase', 're_review',
                         'retry_later', 'restage', 'use_supported_capability', 'contact_operator',
                         'authenticate']

    def model_post_init(self, __context) -> None:
        if self.kind == 'busy' and (self.retry_after is None or not self.retryable):
            raise ValueError('busy needs retry_after and retryable')


class InterfaceFailure(Exception):
    """The one exception type of the in-process interface and the offline functions. Carries an
    InterfaceError; its text is the fixed code only (never source text or an inner exception message)."""

    def __init__(self, err: InterfaceError):
        super().__init__(err.code)
        self.error = err


def http_status(err_or_kind) -> int:
    kind = err_or_kind if isinstance(err_or_kind, str) else err_or_kind.kind
    return KIND_STATUS[kind]


def exit_code(err_or_kind) -> int:
    kind = err_or_kind if isinstance(err_or_kind, str) else err_or_kind.kind
    return KIND_EXIT_CODE[kind]


def result_exit_code(kind: str) -> int:
    return RESULT_KIND_EXIT_CODE[kind]


@dataclass(frozen=True)
class ErrorEntry:
    kind: str
    category: str
    message: str
    hint: str | None
    retryable: bool
    next_action: str
    retry_after: int | None = None


def _e(kind, category, message, hint=None, *, retry=False, action='none', after=None):
    return ErrorEntry(kind, category, message, hint, retry, action, after)


_UNSUPPORTED_HINT = 'Use a capability, backend and exports this build supports.'
ERROR_TABLE: dict[str, ErrorEntry] = {
    # request shape (invalid_request 422) and bounded readers
    'malformed_json': _e('invalid_request', 'request', 'The request body is not valid JSON.',
                         'Send a JSON object of at most 64 KiB.', action='fix_request'),
    'json_too_deep': _e('invalid_request', 'request', 'The request body is nested too deeply.',
                        'Flatten the JSON body.', action='fix_request'),
    'wrong_media_type': _e('invalid_request', 'request', 'The request media type is not accepted.',
                           'Send application/vnd.mirum.mel for sources and application/json for fields.',
                           action='fix_request'),
    'invalid_field': _e('invalid_request', 'request', 'A request field is missing or invalid.',
                        'Check the named field against the published schema.', action='fix_request'),
    'invalid_header': _e('invalid_request', 'request', 'A request header is missing or invalid.',
                         'Check the header value format.', action='fix_request'),
    'body_too_large': _e('too_large', 'request', 'The request body is larger than the limit.',
                         'Keep a source at or below 64 KiB.', action='fix_request'),
    'header_too_large': _e('too_large', 'request', 'A request header value is longer than the limit.',
                           'Shorten the header value.', action='fix_request'),
    'message_too_large': _e('too_large', 'request', 'The message is larger than the limit.',
                            'Send a smaller message.', action='fix_request'),
    'response_too_large': _e('too_large', 'service', 'The response is larger than the limit.',
                             'Request a smaller view.', action='none'),
    # lookups
    'not_found': _e('not_found', 'request', 'The requested resource was not found.', action='none'),
    # service state
    'programs_disabled': _e('programs_disabled', 'service', 'Programs are not enabled on this server.',
                            'Ask the operator to enable programs.', action='contact_operator'),
    'unauthorized': _e('unauthorized', 'service', 'Authentication is required.',
                       'Provide the access token.', action='authenticate'),
    'service_unavailable': _e('unavailable', 'service', 'The service is temporarily unavailable.',
                              'Retry later.', retry=True, action='retry_later', after=30),
    'verifier_unavailable': _e('unavailable', 'service', 'The geometry verifier is unavailable.',
                               'Retry later.', retry=True, action='retry_later', after=30),
    # PlanRefusal: unsupported allowlist
    'capability_not_supported_by_backend': _e('unsupported', 'plan', 'The backend does not support this capability.',
                                              _UNSUPPORTED_HINT, action='use_supported_capability'),
    'backend_mismatch': _e('unsupported', 'plan', 'The program names a backend that cannot build it.',
                           _UNSUPPORTED_HINT, action='use_supported_capability'),
    'export_not_supported': _e('unsupported', 'plan', 'An export format is not supported by this backend.',
                               _UNSUPPORTED_HINT, action='use_supported_capability'),
    'operation_not_executable': _e('unsupported', 'plan', 'An operation cannot be executed by this backend.',
                                   _UNSUPPORTED_HINT, action='use_supported_capability'),
    'operation_not_supported': _e('unsupported', 'plan', 'An operation is not supported by this backend.',
                                  _UNSUPPORTED_HINT, action='use_supported_capability'),
    'plan_invalid': _e('unsupported', 'plan', 'The build plan is not valid for this program.',
                       _UNSUPPORTED_HINT, action='fix_source'),
    'candidate_invalid': _e('unsupported', 'plan', 'A candidate is not valid for this program.',
                            _UNSUPPORTED_HINT, action='fix_source'),
    # PlanRefusal: unavailable allowlist
    'runtime_missing': _e('unavailable', 'plan', 'The native runtime is not installed on this server.',
                          'Ask the operator to install the native runtime.', action='contact_operator'),
    'probe_failed': _e('unavailable', 'plan', 'The native backend probe failed.', 'Retry later.',
                       retry=True, action='retry_later', after=30),
    'backend_busy': _e('unavailable', 'plan', 'The native backend is busy.', 'Retry later.',
                       retry=True, action='retry_later', after=10),
    'deadline_exceeded': _e('unavailable', 'plan', 'The native backend did not answer in time.',
                            'Retry later.', retry=True, action='retry_later', after=30),
    'native_failed': _e('unavailable', 'plan', 'The native backend failed.', 'Retry later.',
                        retry=True, action='retry_later', after=30),
    'evidence_invalid': _e('unavailable', 'plan', 'The backend evidence was not valid.',
                           'Ask the operator to check the backend.', action='contact_operator'),
    'budget_exceeded': _e('unavailable', 'plan', 'The backend budget was exceeded.', 'Retry later.',
                          retry=True, action='retry_later', after=300),
    'mock_backend': _e('unavailable', 'plan', 'Only the mock backend is available, which never locks or publishes.',
                       'Ask the operator to enable the native backend.', action='contact_operator'),
    'lock_mismatch': _e('unavailable', 'plan', 'The execution lock no longer matches the backend.',
                        'Review the revision again to take a fresh lock.', action='re_review'),
    'lock_version_unsupported': _e('unavailable', 'plan', 'The execution lock version is not supported.',
                                   'Stage the program again.', action='restage'),
    # refused (409): gates and revision rules
    'not_finalized': _e('refused', 'revision', 'The revision review is not finalized.',
                        'Finalize the review before running.', action='re_review'),
    'review_mismatch': _e('refused', 'revision', 'The finalized review does not match this revision.',
                          'Review the revision again.', action='re_review'),
    'semantic_mismatch': _e('refused', 'revision', 'The program meaning changed since the review.',
                            'Review the revision again.', action='re_review'),
    'candidate_mismatch': _e('refused', 'revision', 'The candidates differ from the reviewed candidates.',
                             'Review the revision again.', action='re_review'),
    'not_restorable': _e('refused', 'revision', 'This revision cannot be restored.', action='none'),
    'already_current': _e('refused', 'revision', 'This revision is already the current revision.',
                          action='none'),
    'requirements_changed': _e('refused', 'revision', 'The fixed requirements differ from the expected requirements.',
                               'Read the current program and review the change.', action='refresh_and_rebase'),
    # conflict (409)
    'stale_parent': _e('conflict', 'revision', 'The program head moved since this edit was based on it.',
                       'Read the current head and rebase the edit.', action='refresh_and_rebase'),
    'revision_conflict': _e('conflict', 'revision', 'The revision state changed concurrently.',
                            'Read the revision again.', action='refresh_and_rebase'),
    # expired (410)
    'history_expired': _e('expired', 'revision', 'The revision history has expired.', action='none'),
    'artifacts_expired': _e('expired', 'revision', 'The revision artifacts have expired.',
                            'Rebuild the revision to produce new artifacts.', action='restage'),
    'artifacts_missing': _e('expired', 'revision', 'The revision artifacts are no longer present.',
                            'Rebuild the revision to produce new artifacts.', action='restage'),
    'review_expired': _e('expired', 'revision', 'The revision review has expired.',
                         'Stage the program again.', action='restage'),
    # integrity (409)
    'artifacts_altered': _e('integrity', 'revision', 'The stored artifacts failed their integrity check.',
                            'Ask the operator to inspect the store.', action='contact_operator'),
    'record_altered': _e('integrity', 'revision', 'The stored record failed its integrity check.',
                         'Ask the operator to inspect the store.', action='contact_operator'),
    # busy (429)
    'capacity': _e('busy', 'capacity', 'A capacity limit was reached.', 'Retry later.',
                   retry=True, action='retry_later', after=3600),
    'admission_busy': _e('busy', 'capacity', 'The server is at capacity.', 'Retry later.',
                         retry=True, action='retry_later', after=30),
    'slot_busy': _e('busy', 'capacity', 'Another request is using the only slot.', 'Retry shortly.',
                    retry=True, action='retry_later', after=2),
    'quota_programs': _e('busy', 'capacity', 'The per-client program limit was reached.', 'Retry later.',
                         retry=True, action='retry_later', after=3600),
    'quota_staged': _e('busy', 'capacity', 'The per-client staged revision limit was reached.',
                       'Run or cancel a staged revision, then retry.', retry=True, action='retry_later',
                       after=3600),
    'quota_operations': _e('busy', 'capacity', 'The per-client edit limit for 24 hours was reached.',
                           'Retry later.', retry=True, action='retry_later', after=3600),
    'quota_bytes': _e('busy', 'capacity', 'The per-client storage limit was reached.', 'Retry later.',
                      retry=True, action='retry_later', after=3600),
    'quota_drafts': _e('busy', 'capacity', 'The per-client draft limit was reached.', 'Retry later.',
                       retry=True, action='retry_later', after=3600),
}
_GENERIC_MEL = _e('invalid_source', 'semantic', 'Invalid program.', 'Fix the source.', action='fix_source')
_PLAN_UNAVAILABLE = frozenset({'mock_backend', 'lock_mismatch', 'lock_version_unsupported'})
_REVISION_REFUSAL_CODES = ('stale_parent', 'requirements_changed', 'not_restorable', 'already_current', 'history_expired',
                           'artifacts_expired', 'artifacts_missing', 'artifacts_altered', 'record_altered',
                           'review_expired', 'capacity')
_MEL_UNSUPPORTED = frozenset({'MEL-S011', 'MEL-S012', 'MEL-S013'})
_MEL_CODE_RE = re.compile(r'^MEL-[LPS]\d{3}$')
_NAME_RE = re.compile(r'^[a-z_][a-z0-9_]{0,63}$')


def _ids(values) -> tuple[str, ...]:
    return tuple(v for v in tuple(values)[:16] if type(v) is str and _REQ_ID_RE.match(v))


def error(code: str, *, changed: str = 'nothing_written', field: str | None = None,
          requirement_ids=(), operation_ids=(), retry_after: int | None = None) -> InterfaceError:
    """Envelope for a fixed ERROR_TABLE code. An unknown code becomes service_unavailable."""
    if code not in ERROR_TABLE:
        code = 'service_unavailable'
    entry = ERROR_TABLE[code]
    after = retry_after if retry_after is not None else entry.retry_after
    return InterfaceError(
        kind=entry.kind, code=code, category=entry.category, message=entry.message, hint=entry.hint,
        field=field if type(field) is str and _NAME_RE.match(field) else None,
        requirement_ids=_ids(requirement_ids), operation_ids=_ids(operation_ids),
        retryable=entry.retryable, retry_after=after, changed=changed, next_action=entry.next_action)


def busy(code: str = 'admission_busy', *, retry_after: int | None = None, changed: str = 'nothing_written'):
    return error(code if ERROR_TABLE.get(code, _GENERIC_MEL).kind == 'busy' else 'admission_busy',
                 retry_after=retry_after, changed=changed)


def not_found() -> InterfaceError:
    return error('not_found')


def too_large(code: str = 'body_too_large') -> InterfaceError:
    return error(code if ERROR_TABLE.get(code, _GENERIC_MEL).kind == 'too_large' else 'body_too_large')


def unavailable(code: str = 'service_unavailable') -> InterfaceError:
    return error(code if ERROR_TABLE.get(code, _GENERIC_MEL).kind == 'unavailable' else 'service_unavailable')


def programs_disabled() -> InterfaceError:
    return error('programs_disabled')


def unauthorized() -> InterfaceError:
    return error('unauthorized')


def invalid_request(code: str = 'invalid_field', *, field: str | None = None) -> InterfaceError:
    return error(code if ERROR_TABLE.get(code, _GENERIC_MEL).kind == 'invalid_request' else 'invalid_field',
                 field=field)


def from_mel(diagnostic_or_error, *, source_id: str | None = None) -> InterfaceError:
    """Envelope from a MelError or a language Diagnostic (duck-typed). Message and hint come from the
    fixed MEL tables by code, never from the exception text; the span is positions only."""
    diag = getattr(diagnostic_or_error, 'diagnostic', diagnostic_or_error)
    code = getattr(diag, 'code', None)
    if type(code) is not str or not _MEL_CODE_RE.match(code):
        return error('service_unavailable')
    if code in _MEL_LEXICAL:
        category, base = 'lexical', _MEL_LEXICAL[code]
    elif code in _MEL_SYNTAX:
        category, base = 'syntax', _MEL_SYNTAX[code]
    elif code in _MEL_SEMANTIC:
        category, base = ('resource' if code == 'MEL-S018' else 'semantic'), _MEL_SEMANTIC[code]
    else:
        category, base = 'semantic', 'Invalid program'
    kind = 'unsupported' if code in _MEL_UNSUPPORTED else 'invalid_source'
    span = getattr(diag, 'span', None)
    err_span = None
    if span is not None:
        err_span = ErrorSpan(line=span.start.line, column=span.start.column, end_line=span.end.line,
                             end_column=span.end.column,
                             source_id=source_id if type(source_id) is str and 0 < len(source_id) <= 128 else None)
    fld = getattr(diag, 'field', None)
    return InterfaceError(
        kind=kind, code=code, category=category, message=base,
        hint=_MEL_HINTS.get(code, 'Fix the value of this field.'), span=err_span,
        field=fld if type(fld) is str and _NAME_RE.match(fld) else None,
        retryable=False, changed='nothing_written',
        next_action='use_supported_capability' if kind == 'unsupported' else 'fix_source')


def from_plan_refusal(outcome: str, reasons, *, diagnostic_code: str | None = None,
                      requirement_ids=(), operation_ids=(), changed: str = 'nothing_written') -> InterfaceError:
    """Envelope from a PlanRefusal outcome and reasons (the first reason decides the code).

    ``unsupported`` reasons -> unsupported 422. Everything else is unavailable 503, except the gate
    reasons the planner reports as ``refused`` (not_finalized, review/semantic/candidate mismatch), which
    are refused 409. mock_backend, lock_mismatch and lock_version_unsupported are always unavailable
    (lock_mismatch asks for re_review). ``diagnostic_code`` is accepted for symmetry; it adds no text."""
    first = next(iter(reasons), None)
    if type(first) is not str:
        return error('service_unavailable', changed=changed)
    code = 'backend_busy' if first == 'busy' and outcome != 'refused' else first
    if code not in ERROR_TABLE:
        return error('service_unavailable', changed=changed)
    entry = ERROR_TABLE[code]
    if outcome == 'unsupported' and entry.kind != 'unsupported':
        return error('service_unavailable', changed=changed)
    if outcome in ('unavailable', 'refused') and entry.kind == 'unsupported':
        return error('service_unavailable', changed=changed)
    if code in _PLAN_UNAVAILABLE and entry.kind != 'unavailable':
        return error('service_unavailable', changed=changed)
    return error(code, changed=changed, requirement_ids=requirement_ids, operation_ids=operation_ids)


def from_revision_refusal(reasons, *, changed: str = 'nothing_written') -> InterfaceError:
    """Envelope from RevisionRefusal reasons (REFUSAL_REASONS); the first reason decides."""
    reasons = getattr(reasons, 'reasons', reasons)
    first = next(iter(reasons), None)
    if first not in _REVISION_REFUSAL_CODES:
        return error('service_unavailable', changed=changed)
    return error(first, changed=changed)


_ID_FIELDS = frozenset({'program_id', 'revision_id', 'other_revision_id', 'expected_parent',
                        'expected_head'})


def from_validation_error(exc: ValidationError) -> InterfaceError:
    """Envelope from a pydantic ValidationError. Only the first error's field name is used (never its
    message or input); a malformed id is not_found, identical to an unknown id."""
    try:
        loc = exc.errors(include_url=False, include_context=False, include_input=False)[0]['loc']
    except (IndexError, KeyError, TypeError):
        return error('invalid_field')
    name = next((p for p in loc if type(p) is str), None)
    if name in _ID_FIELDS:
        return not_found()
    return error('invalid_field', field=name)


# ----------------------------------------------------------------------------------- operation registry

TRANSPORTS = ('python', 'cli', 'http', 'mcp')
_ALL = TRANSPORTS
_NO_MCP = ('python', 'cli', 'http')


@dataclass(frozen=True)
class Route:
    method: str
    path: str  # relative to API_PREFIX


@dataclass(frozen=True)
class OperationSpec:
    name: str
    input_model: type
    http_method: str
    http_path: str
    mutating: bool
    transports: tuple[str, ...]
    human_only: bool
    response_model: type | None = None
    body: Literal['mel', 'json', 'none'] = 'none'
    routes: tuple[Route, ...] = ()
    description: str = ''

    @property
    def mcp(self) -> bool:
        return 'mcp' in self.transports


def _op(name, model, method, path, mutating, transports, *, human=False, response=None, body='none',
        extra=(), description=''):
    return OperationSpec(name, model, method, path, mutating, tuple(transports), human, response, body,
                         (Route(method, path),) + tuple(Route(m, p) for m, p in extra), description)


OPERATIONS: tuple[OperationSpec, ...] = (
    _op('validate', ValidateRequest, 'POST', '/mel/validate', False, _ALL, response=ValidateResult,
        body='mel', extra=[('GET', '/mel/meta')], description='Compile MEL source and report diagnostics.'),
    _op('format', FormatRequest, 'POST', '/mel/format', False, _ALL, response=FormatResult, body='mel',
        description='Return the canonical formatting of MEL source.'),
    _op('plan', PlanRequest, 'POST', '/mel/plan', False, _ALL, response=PlanResult, body='mel',
        description='Plan a MEL program without writing anything.'),
    _op('program_create', ProgramCreateRequest, 'POST', '/programs', True, _NO_MCP, response=ProgramCreated,
        description='Create an empty program.'),
    _op('stage', StageRequest, 'POST', '/programs/{pid}/revisions', True, _NO_MCP, response=StageResponse,
        body='mel', description='Stage a new revision from MEL source.'),
    _op('propose', ProposeRequest, 'POST', '/programs/{pid}/revisions', True, ('mcp',),
        response=StageResponse, body='mel',
        description='Stage a revision from MEL source with the requirements guard.'),
    _op('review', ReviewRequest, 'POST', '/revisions/{rid}/review', True, _NO_MCP, human=True,
        response=RevisionResponse, body='json', description='Human review of a staged revision.'),
    _op('run', RunRequest, 'POST', '/revisions/{rid}/run', True, _ALL, response=RevisionResponse,
        description='Run a reviewed revision.'),
    _op('status', StatusRequest, 'GET', '/revisions/{rid}', False, _ALL, response=RevisionResponse,
        description='Read one revision.'),
    _op('cancel', CancelRequest, 'POST', '/revisions/{rid}/cancel', True, _ALL, response=RevisionResponse,
        description='Cancel a revision.'),
    _op('inspect', InspectRequest, 'GET', '/revisions/{rid}/evidence', False, _ALL, response=InspectBundle,
        extra=[('GET', '/revisions/{rid}'), ('GET', '/programs/{pid}/journal')],
        description='Read a revision with its evidence and the program journal.'),
    _op('history', HistoryRequest, 'GET', '/programs/{pid}/history', False, _ALL, response=HistoryView,
        extra=[('GET', '/programs/{pid}')], description='List the revisions of a program.'),
    _op('diff', DiffRequest, 'POST', '/revisions/{rid}/compare', False, _ALL, response=DiffResponse,
        body='json', description='Compare two revisions.'),
    _op('restore', RestoreRequest, 'POST', '/revisions/{rid}/restore', True, _NO_MCP,
        response=RevisionResponse, description='Restore an earlier revision as the new head.'),
    _op('rebuild', RebuildRequest, 'POST', '/revisions/{rid}/rebuild', True, _NO_MCP,
        response=RevisionResponse, description='Stage a rebuild of a revision.'),
    _op('export', ExportRequest, 'GET', '/revisions/{rid}/artifacts/{fmt}', False, _NO_MCP,
        response=ExportMetadata, description='Download one artifact of a revision.'),
)
OPERATIONS_BY_NAME = {op.name: op for op in OPERATIONS}
MCP_TOOL_NAMES = tuple(op.name for op in OPERATIONS if op.mcp)


def get_operation(name: str) -> OperationSpec:
    return OPERATIONS_BY_NAME[name]


def full_path(spec_or_path) -> str:
    path = spec_or_path if isinstance(spec_or_path, str) else spec_or_path.http_path
    return API_PREFIX + path


def mcp_input_schema(name: str) -> dict:
    """JSON Schema for an MCP tool, generated from the registry input model. ``api_version`` is server
    set and dropped; MEL ops gain a ``source`` text argument (never a path or a URL)."""
    spec = OPERATIONS_BY_NAME[name]
    if not spec.mcp:
        raise KeyError(name)
    schema = json.loads(json.dumps(spec.input_model.model_json_schema(mode='validation')))
    props = schema.setdefault('properties', {})
    props.pop('api_version', None)
    schema['required'] = [r for r in schema.get('required', []) if r != 'api_version']
    if spec.body == 'mel':
        props['source'] = {'type': 'string', 'minLength': 0, 'maxLength': MAX_MEL_BODY_BYTES,
                           'title': 'Source', 'description': 'MEL source text, at most 64 KiB in UTF-8.'}
        schema['required'] = ['source'] + schema['required']
    schema['additionalProperties'] = False
    schema['title'] = spec.input_model.__name__
    return schema


def mcp_tool_descriptors() -> list[dict]:
    return [{'name': n, 'description': OPERATIONS_BY_NAME[n].description,
             'inputSchema': mcp_input_schema(n)} for n in MCP_TOOL_NAMES]


def _snake(name: str) -> str:
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()


def _build_schema_models() -> dict[str, type]:
    out = {'mirum_interface_error_v0_1.schema.json': InterfaceError}
    seen = {InterfaceError}
    for model in [op.input_model for op in OPERATIONS] + list(RESPONSE_MODELS):
        if model in seen:
            continue
        seen.add(model)
        out[f'mirum_program_api_v0_1_{_snake(model.__name__)}.schema.json'] = model
    return out


# File name -> model, merged into schema_export.SCHEMA_MODELS.
API_SCHEMA_MODELS = _build_schema_models()
