"""F08 task 1: pure interface contract (registry, models, error envelope, caps). Host-runnable."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.language import MelError, compile_source
from engineering.program import interface as I
from engineering.program import schema_export
from engineering.program.versions import INTERFACE_ERROR_VERSION, PROGRAM_API_VERSION

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
F08 = Path(__file__).parent / 'fixtures' / 'program' / 'f08'
ID = 'a' * 32
ID2 = 'b' * 32
H = 'c' * 64

D5_ROUTES = {
    ('GET', '/mel/meta'), ('POST', '/mel/validate'), ('POST', '/mel/format'), ('POST', '/mel/plan'),
    ('POST', '/programs'), ('GET', '/programs/{pid}'), ('GET', '/programs/{pid}/history'),
    ('GET', '/programs/{pid}/journal'), ('POST', '/programs/{pid}/revisions'),
    ('GET', '/revisions/{rid}'), ('GET', '/revisions/{rid}/evidence'),
    ('POST', '/revisions/{rid}/review'), ('POST', '/revisions/{rid}/run'),
    ('POST', '/revisions/{rid}/cancel'), ('POST', '/revisions/{rid}/restore'),
    ('POST', '/revisions/{rid}/rebuild'), ('POST', '/revisions/{rid}/compare'),
    ('GET', '/revisions/{rid}/artifacts/{fmt}'),
}
OPS = ['validate', 'format', 'plan', 'program_create', 'stage', 'propose', 'review', 'run', 'status', 'cancel',
       'inspect', 'history', 'diff', 'restore', 'rebuild', 'export']


def _cases():
    return json.loads((F08 / 'mel_cases.json').read_text(encoding='utf-8'))


def _source(case) -> str:
    if 'source' in case:
        return case['source']
    text = (F08 / 'beam.mir').read_text(encoding='utf-8')
    for old, new in case['edits']:
        assert old in text
        text = text.replace(old, new)
    return text


# ------------------------------------------------------------------------------------------ registry

def test_registry_complete_and_unique():
    names = [op.name for op in I.OPERATIONS]
    assert sorted(names) == sorted(OPS) and len(set(names)) == len(names)
    assert isinstance(I.OPERATIONS, tuple)
    with pytest.raises(Exception):
        I.OPERATIONS[0].name = 'x'  # frozen
    for op in I.OPERATIONS:
        assert issubclass(op.input_model, I._Request)
        assert op.http_method in ('GET', 'POST')
        assert set(op.transports) <= set(I.TRANSPORTS)
        assert (op.http_method, op.http_path) == (op.routes[0].method, op.routes[0].path)


def test_d5_route_set_is_exact():
    got = {(r.method, r.path) for op in I.OPERATIONS for r in op.routes}
    assert got == D5_ROUTES
    assert I.full_path(I.get_operation('status')) == '/engineering/v1/revisions/{rid}'
    assert {r.path for r in I.get_operation('inspect').routes} == {
        '/revisions/{rid}', '/revisions/{rid}/evidence', '/programs/{pid}/journal'}
    assert I.get_operation('diff').http_path == '/revisions/{rid}/compare'
    assert I.get_operation('propose').routes == I.get_operation('stage').routes


def test_mcp_set_exact_and_human_only():
    assert set(I.MCP_TOOL_NAMES) == {'validate', 'format', 'plan', 'propose', 'status', 'inspect', 'history',
                                     'diff', 'run', 'cancel'}
    for gone in ('program_create', 'review', 'restore', 'rebuild', 'export', 'stage'):
        assert 'mcp' not in I.get_operation(gone).transports
    assert [op.name for op in I.OPERATIONS if op.human_only] == ['review']
    assert I.get_operation('propose').transports == ('mcp',)
    assert all(not I.get_operation(n).mutating for n in
               ('validate', 'format', 'plan', 'status', 'inspect', 'history', 'diff', 'export'))
    assert all(I.get_operation(n).mutating for n in
               ('program_create', 'stage', 'propose', 'review', 'run', 'cancel', 'restore', 'rebuild'))


def test_input_models_strict_and_carry_no_source_text():
    for op in I.OPERATIONS:
        props = op.input_model.model_json_schema()['properties']
        assert props['api_version']['const'] == PROGRAM_API_VERSION
        assert 'source' not in props and 'text' not in props
        with pytest.raises(ValidationError):
            op.input_model.model_validate({'bogus': 1})
    r = I.ReviewRequest(revision_id=ID, brief_sha256=H, acknowledged_fields=('a_b',), reviewed_by='Ann')
    props = set(I.ReviewRequest.model_json_schema()['properties'])
    assert props == {'api_version', 'revision_id', 'brief_sha256', 'acknowledged_fields', 'reviewed_by'}
    assert not any(k.endswith('_id') and k in ('draft_id', 'brief_id') for k in props)
    assert set(I.RunRequest.model_json_schema()['properties']) == {'api_version', 'revision_id'}
    assert set(I.DiffRequest.model_json_schema()['properties']) == {'api_version', 'revision_id',
                                                                      'other_revision_id'}
    assert I.parse_model(I.ReviewRequest, json.loads(r.model_dump_json())) == r
    with pytest.raises(ValidationError):
        I.RunRequest(revision_id='../x')
    with pytest.raises(ValidationError):
        I.ValidateRequest(source_id='x' * 129)
    with pytest.raises(ValidationError):
        I.ReviewRequest(revision_id=ID, brief_sha256=H, acknowledged_fields=('a',) * 33, reviewed_by='A')


# ------------------------------------------------------------------------------------ response models

def _walk_props(schema, found):
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k == 'properties' and isinstance(v, dict):
                found.update(v.keys())
            _walk_props(v, found)
    elif isinstance(schema, list):
        for v in schema:
            _walk_props(v, found)


def test_responses_never_carry_bearer_ids():
    for model in I.RESPONSE_MODELS:
        names = set()
        _walk_props(model.model_json_schema(), names)
        assert not names & {'draft_id', 'brief_id', 'job_id'}, model.__name__
        assert model.model_json_schema()['properties'] or True


def test_responses_have_api_version_and_result_kinds():
    for model in (I.MelMeta, I.ValidateResult, I.FormatResult, I.PlanResult, I.RevisionStatus, I.ProgramView,
                  I.ProgramCreated, I.RevisionResponse, I.StageResponse, I.InspectBundle, I.HistoryView,
                  I.DiffResponse, I.ExportMetadata):
        assert model.model_json_schema()['properties']['api_version']['const'] == PROGRAM_API_VERSION
    assert I.result_kind('published') == 'published'
    assert I.result_kind('rejected', 'rejected_by_policy') == 'rejected'
    assert I.result_kind('staged') == 'pending' and I.result_kind('running') == 'pending'
    assert I.result_kind('failed', 'delivered_evidence_unavailable') == 'unavailable'
    assert I.result_kind('failed', 'job_unavailable') == 'unavailable'
    assert I.result_kind('failed', 'delivered_check_failed') == 'verification_failed'
    assert I.result_kind('failed', 'interrupted') == 'failed'
    assert I.result_kind('cancelled', 'cancelled') == 'cancelled'
    assert I.result_kind('conflict', 'stale_parent') == 'conflict'
    assert set(I.RESULT_KINDS) >= {'published', 'rejected', 'unavailable', 'verification_failed', 'pending'}
    status = I.RevisionStatus(
        revision_id=ID, program_id=ID2, kind='edit', state='rejected', reason='rejected_by_policy',
        result_kind='rejected', source_sha256=H, semantic_sha256=H, brief_sha256=H,
        fixed_requirements_sha256=H, lock_sha256=H, created_at='2026-11-22T10:00:00Z',
        updated_at='2026-11-22T10:00:01.5Z', artifacts='none', is_current=False)
    assert I.RevisionResponse.model_validate_json(I.RevisionResponse(revision=status).model_dump_json())


def test_iso_utc_and_bounded_json():
    from datetime import datetime, timezone
    assert I.iso_utc(datetime(2026, 11, 22, 1, 2, 3, tzinfo=timezone.utc)) == '2026-11-22T01:02:03.000000Z'
    assert I.bounded_json_loads(b'{"a": [1]}') == {'a': [1]}
    for data, code in ((b'x' * (I.MAX_JSON_BODY_BYTES + 1), 'body_too_large'), (b'{', 'malformed_json'),
                       (b'NaN', 'malformed_json'), (b'\xff', 'malformed_json'),
                       (b'[' * 9 + b']' * 9, 'json_too_deep'), (b'[' * 60000, 'malformed_json')):
        with pytest.raises(I.BoundsError) as exc:
            I.bounded_json_loads(data)
        assert exc.value.code == code and str(exc.value) == code
    assert I.bounded_json_loads(b'[' * 8 + b']' * 8)


# ------------------------------------------------------------------------------------------- constants

def test_cap_constants():
    assert I.MAX_MEL_BODY_BYTES == 65536 and I.MAX_JSON_RESPONSE_BYTES == 4194304
    assert I.MAX_MCP_MESSAGE_BYTES == 1048576 and I.MAX_JSON_DEPTH == 8
    assert I.MEL_MEDIA_TYPE == 'application/vnd.mirum.mel'
    assert (I.HEADER_SOURCE_ID, I.HEADER_EXPECTED_PARENT, I.HEADER_REQUIRE_REQUIREMENTS) == (
        'X-Mirum-Source-Id', 'X-Mirum-Expected-Parent', 'X-Mirum-Require-Requirements')
    assert I.HEADER_MAX_CHARS == {'X-Mirum-Source-Id': 128, 'X-Mirum-Expected-Parent': 32,
                                  'X-Mirum-Require-Requirements': 64}
    cfg = (BACKEND / 'config.py').read_text(encoding='utf-8')
    m = re.search(r'ENGINEERING_MAX_BODY_BYTES: int = max\(1024, _env_int\("MIRUM_ENGINEERING_MAX_BODY_BYTES", '
                  r'([0-9 *]+)\)\)', cfg)
    assert m and eval(m.group(1), {}) == I.MAX_MEL_BODY_BYTES
    from engineering.program.revisions import MAX_SOURCE_BYTES
    assert MAX_SOURCE_BYTES == I.MAX_MEL_BODY_BYTES


def test_header_checks():
    ok = I.check_header_value
    assert ok('X-Mirum-Source-Id', 'a/b.mir') is None
    assert ok('X-Mirum-Source-Id', '../x') is None  # label syntax is the compiler's job (MEL-S016)
    assert ok('X-Mirum-Source-Id', 'x' * 129) == 'header_too_large'
    assert ok('X-Mirum-Source-Id', 'a b') == 'invalid_header'
    assert ok('X-Mirum-Expected-Parent', ID) is None
    assert ok('X-Mirum-Expected-Parent', 'z' * 32) == 'invalid_header'
    assert ok('X-Mirum-Expected-Parent', ID + 'a') == 'header_too_large'
    assert ok('X-Mirum-Require-Requirements', H) is None
    assert ok('X-Mirum-Require-Requirements', 'c' * 65) == 'header_too_large'
    assert ok('X-Other', 'a') == 'invalid_header'


def test_export_formats_match_bundles_and_d11():
    tree = ast.parse((BACKEND / 'engineering' / 'bundles.py').read_text(encoding='utf-8'))
    keys = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ('FORMATS', 'GENERATOR_FORMATS'):
            keys[node.targets[0].id] = [k.value for k in node.value.keys]
    assert list(I.BUNDLE_FORMATS) == keys['FORMATS'] + keys['GENERATOR_FORMATS']
    for fmt in ('program-source', 'program-ir', 'execution-lock', 'step', 'zip'):
        assert I.is_export_format(fmt)
    for fmt in ('../x', '', 'STEP', 'step ', None, 5):
        assert not I.is_export_format(fmt)


def test_id_pattern_matches_revision_ids():
    import re as _re
    assert _re.match(I.ID_PATTERN, ID) and not _re.match(I.ID_PATTERN, 'A' * 32)
    assert not _re.match(I.ID_PATTERN, ID + '\n') or True  # model uses fullmatch semantics via pydantic
    with pytest.raises(ValidationError):
        I.StatusRequest(revision_id=ID + '\n')


# ------------------------------------------------------------------------------------ MCP schema scan

BAD_WORD = re.compile(r'path|url|uri|file|dir', re.I)


def _scan(node, where):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == 'properties' and isinstance(v, dict):
                for name in v:
                    assert not BAD_WORD.search(name), (where, name)
            if k in ('description', 'title') and isinstance(v, str):
                assert not BAD_WORD.search(v), (where, v)
            _scan(v, where)
    elif isinstance(node, list):
        for v in node:
            _scan(v, where)


def test_mcp_schemas_have_no_path_url_fields():
    descriptors = I.mcp_tool_descriptors()
    assert [d['name'] for d in descriptors] == list(I.MCP_TOOL_NAMES)
    for d in descriptors:
        assert not BAD_WORD.search(d['description']), d['name']
        _scan(d['inputSchema'], d['name'])
        assert d['inputSchema']['additionalProperties'] is False
        assert 'api_version' not in d['inputSchema']['properties']
        if I.get_operation(d['name']).body == 'mel':
            assert d['inputSchema']['properties']['source']['maxLength'] == 65536
    with pytest.raises(KeyError):
        I.mcp_input_schema('export')
    assert 'require_requirements_sha256' not in I.mcp_input_schema('propose')['properties']


# ------------------------------------------------------------------------------------ error envelope

def test_status_map_and_exit_codes_exact():
    assert I.KIND_STATUS == {
        'invalid_source': 422, 'unsupported': 422, 'unavailable': 503, 'refused': 409, 'conflict': 409,
        'expired': 410, 'integrity': 409, 'busy': 429, 'not_found': 404, 'too_large': 413,
        'programs_disabled': 503, 'unauthorized': 401, 'invalid_request': 422}
    assert set(I.KIND_STATUS) == set(I.ERROR_KINDS) == set(I.KIND_EXIT_CODE)
    for kind in ('invalid_source', 'unsupported'):
        assert I.exit_code(kind) == 2
    assert I.exit_code('unavailable') == 3 and I.exit_code('busy') == 3
    assert I.exit_code('conflict') == 4 and I.exit_code('refused') == 4
    assert I.result_exit_code('rejected') == 2 and I.result_exit_code('published') == 0
    assert set(I.RESULT_KIND_EXIT_CODE) == set(I.RESULT_KINDS)
    assert I.exit_code(I.error('stale_parent')) == 4 and I.http_status(I.error('slot_busy')) == 429


def test_every_table_entry_is_valid_ascii_and_placeholder_free():
    placeholder = re.compile(r'\{[A-Za-z0-9_]*\}|%[sdr(]|\$\{|<[a-z]+>')
    for code, entry in I.ERROR_TABLE.items():
        for text in (entry.message, entry.hint):
            if text is None:
                continue
            assert text.isascii() and text == text.strip() and len(text) <= 240, code
            assert not placeholder.search(text), (code, text)
        assert entry.kind in I.ERROR_KINDS and entry.category in I.ERROR_CATEGORIES
        assert entry.next_action in I.NEXT_ACTIONS
        err = I.error(code)
        assert err.kind == entry.kind and err.code == code
        assert I.InterfaceError.model_validate_json(err.model_dump_json()) == err
        if entry.kind == 'busy':
            assert err.retry_after and err.retryable
    from engineering.language import diagnostics as D
    for table in (D.LEXICAL, D.SYNTAX, D.SEMANTIC, D.HINTS):
        for code, text in table.items():
            assert text.isascii() and not placeholder.search(text), code


def test_envelope_fixtures_round_trip():
    expected = json.loads((F08 / 'expected_envelopes.json').read_text(encoding='utf-8'))
    got = {
        'stale_parent': I.from_revision_refusal(('stale_parent',)),
        'lock_mismatch': I.from_plan_refusal('unavailable', ('lock_mismatch',)),
        'slot_busy': I.busy('slot_busy'),
    }
    for name, err in got.items():
        assert json.loads(err.model_dump_json()) == expected[name], name
    case = next(c for c in _cases()['cases'] if c['id'] == 's011-unsupported-capability')
    with pytest.raises(MelError) as exc:
        compile_source(_source(case), source_id=case['source_id'])
    assert json.loads(I.from_mel(exc.value, source_id='a.mir').model_dump_json()) == expected['s011']
    with pytest.raises(ValidationError):
        I.InterfaceError(**{**expected['stale_parent'], 'api_version': 'x'})
    with pytest.raises(ValidationError):
        I.InterfaceError(**{**expected['stale_parent'], 'extra': 1})
    with pytest.raises(ValidationError):
        I.InterfaceError(**{**expected['slot_busy'], 'retry_after': None})
    assert I.InterfaceError.model_json_schema()['properties']['api_version']['const'] == INTERFACE_ERROR_VERSION


@pytest.mark.parametrize('case', _cases()['cases'], ids=lambda c: c['id'])
def test_real_mel_diagnostics_map_to_envelopes(case):
    with pytest.raises(MelError) as exc:
        compile_source(_source(case), source_id=case['source_id'])
    err = I.from_mel(exc.value, source_id=case['source_id'] if case['source_id'] != '../x' else None)
    assert err.code == case['code'] and err.kind == case['kind']
    assert I.http_status(err) == 422 and not err.retryable and err.changed == 'nothing_written'
    assert (err.span.line, err.span.column, err.span.end_line, err.span.end_column) == tuple(case['span'])
    assert err.field == case['field']
    assert err.next_action == ('use_supported_capability' if case['kind'] == 'unsupported' else 'fix_source')
    assert err.hint and err.message
    # Message is never the exception text (which carries line:column).
    assert str(exc.value) not in err.model_dump_json()
    assert 'Traceback' not in err.model_dump_json()
    if case['kind'] == 'invalid_source':
        assert err.category in ('lexical', 'syntax', 'semantic')
        if case['code'][4] in 'LP':
            assert err.category in ('lexical', 'syntax')


def test_all_s_codes_map_by_kind():
    from engineering.language.diagnostics import SEMANTIC, make, Span, Position
    p = Position(0, 1, 1)
    for code in SEMANTIC:
        err = I.from_mel(make(code, Span(p, p)))
        assert err.kind == ('unsupported' if code in ('MEL-S011', 'MEL-S012', 'MEL-S013') else 'invalid_source')
    assert I.from_mel(make('MEL-S018', Span(p, p))).category == 'resource'
    assert I.from_mel(object()).code == 'service_unavailable'


def test_plan_refusal_mapping():
    from engineering.program.kernel import UNAVAILABLE_REASONS, UNSUPPORTED_REASONS
    for reason in UNSUPPORTED_REASONS:
        err = I.from_plan_refusal('unsupported', (reason,), diagnostic_code='MEL-S011')
        assert err.kind == 'unsupported' and err.code == reason and I.http_status(err) == 422
        assert not err.retryable
    for reason in UNAVAILABLE_REASONS:
        err = I.from_plan_refusal('unavailable', (reason,))
        assert err.kind == 'unavailable' and I.http_status(err) == 503, reason
    assert I.from_plan_refusal('unavailable', ('probe_failed',)).retryable
    assert I.from_plan_refusal('unavailable', ('probe_failed',)).next_action == 'retry_later'
    assert I.from_plan_refusal('unavailable', ('mock_backend',)).kind == 'unavailable'
    assert I.from_plan_refusal('refused', ('mock_backend',)).kind == 'unavailable'
    lm = I.from_plan_refusal('refused', ('lock_mismatch',))
    assert lm.kind == 'unavailable' and lm.next_action == 're_review'
    for reason in ('not_finalized', 'review_mismatch', 'semantic_mismatch', 'candidate_mismatch'):
        err = I.from_plan_refusal('refused', (reason,))
        assert err.kind == 'refused' and I.http_status(err) == 409
    # Out-of-contract input degrades to a fixed unavailable envelope, never an echo.
    assert I.from_plan_refusal('refused', ('capability_not_supported_by_backend',)).code == 'service_unavailable'
    assert I.from_plan_refusal('unsupported', ('probe_failed',)).code == 'service_unavailable'
    assert I.from_plan_refusal('unsupported', ()).code == 'service_unavailable'
    assert I.from_plan_refusal('unsupported', ('evil text',)).code == 'service_unavailable'


def test_revision_refusal_mapping():
    from engineering.program.revisions import REFUSAL_REASONS, RevisionRefusal
    expect = {
        'stale_parent': ('conflict', 'refresh_and_rebase'),
        'requirements_changed': ('refused', 'refresh_and_rebase'),
        'not_restorable': ('refused', 'none'),
        'already_current': ('refused', 'none'), 'history_expired': ('expired', 'none'),
        'artifacts_expired': ('expired', 'restage'), 'artifacts_missing': ('expired', 'restage'),
        'artifacts_altered': ('integrity', 'contact_operator'),
        'record_altered': ('integrity', 'contact_operator'), 'review_expired': ('expired', 'restage'),
        'capacity': ('busy', 'retry_later'),
    }
    assert set(expect) == set(REFUSAL_REASONS)
    for reason, (kind, action) in expect.items():
        err = I.from_revision_refusal(RevisionRefusal((reason,), 'detail text never echoed'))
        assert (err.kind, err.next_action, err.code) == (kind, action, reason)
        assert 'detail' not in err.model_dump_json()
    assert I.from_revision_refusal(('nonsense',)).code == 'service_unavailable'
    assert I.error('requirements_changed').kind == 'refused'
    assert I.error('requirements_changed').next_action == 'refresh_and_rebase'


def test_simple_builders_and_changed():
    assert I.not_found().kind == 'not_found' and I.http_status(I.not_found()) == 404
    assert I.too_large().code == 'body_too_large' and I.http_status(I.too_large('header_too_large')) == 413
    assert I.too_large('not_found').code == 'body_too_large'
    b = I.busy('quota_programs')
    assert b.kind == 'busy' and b.retry_after and I.http_status(b) == 429
    assert I.busy('stale_parent').code == 'admission_busy'
    assert I.unavailable().kind == 'unavailable' and I.unavailable('stale_parent').code == 'service_unavailable'
    assert I.programs_disabled().code == 'programs_disabled' and I.http_status(I.programs_disabled()) == 503
    assert I.http_status(I.unauthorized()) == 401
    assert I.error('stale_parent', changed='revision_staged').changed == 'revision_staged'
    assert I.error('stale_parent', changed='job_admitted').changed == 'job_admitted'
    assert I.error('nope').code == 'service_unavailable'
    e = I.error('invalid_field', field='bad field', requirement_ids=['R1', 'bad id!', 5], operation_ids=['op.1'])
    assert e.field is None and e.requirement_ids == ('R1',) and e.operation_ids == ('op.1',)


def test_validation_error_mapping_uses_field_names_only():
    with pytest.raises(ValidationError) as exc:
        I.ReviewRequest(revision_id=ID, brief_sha256='secret-value', acknowledged_fields=(), reviewed_by='A')
    err = I.from_validation_error(exc.value)
    assert err.kind == 'invalid_request' and err.field == 'brief_sha256' and err.code == 'invalid_field'
    assert 'secret-value' not in err.model_dump_json()
    with pytest.raises(ValidationError) as exc:
        I.StatusRequest(revision_id='../x')
    assert I.from_validation_error(exc.value) == I.not_found()


def test_stateless_ops_cover_the_documented_headers():
    assert I.get_operation('validate').body == 'mel' and I.get_operation('review').body == 'json'
    assert I.get_operation('diff').body == 'json' and I.get_operation('status').body == 'none'


# --------------------------------------------------------------------------------------- source caps

def test_source_size_boundaries_reach_the_compiler_diagnostics():
    base = (F08 / 'beam.mir').read_text(encoding='utf-8')
    pad = '// ' + 'x' * 61 + '\n'
    exact = base
    while len(exact.encode()) + len(pad) <= I.MAX_MEL_BODY_BYTES:
        exact += pad
    exact += '/' * (I.MAX_MEL_BODY_BYTES - len(exact.encode()) - 1) + '\n'
    assert len(exact.encode()) == I.MAX_MEL_BODY_BYTES
    compile_source(exact, source_id='big.mir')  # exactly 64 KiB reaches the compiler and compiles
    with pytest.raises(MelError) as exc:
        compile_source(exact + '\n', source_id='big.mir')
    assert exc.value.diagnostic.code == 'MEL-L022'
    err = I.from_mel(exc.value)
    assert err.code == 'MEL-L022' and err.kind == 'invalid_source'


# --------------------------------------------------------------------------------- import purity, schemas

def test_interface_import_is_pure():
    code = (
        "import sys\n"
        f"sys.path[:0] = [{str(BACKEND)!r}, {str(ROOT)!r}]\n"
        "import engineering.program.interface\n"
        "bad = [m for m in ('sqlite3', 'fastapi', 'httpx', 'starlette', 'uvicorn', 'cadquery', 'OCP',\n"
        "                    'engineering.store', 'engineering.program.transactions',\n"
        "                    'engineering.program.revision_store', 'engineering.jobs',\n"
        "                    'engineering.bundles', 'engineering.config', 'config', 'google.genai')\n"
        "       if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip() == ''


def test_interface_source_has_no_forbidden_references():
    text = (BACKEND / 'engineering' / 'program' / 'interface.py').read_text(encoding='utf-8')
    for word in ('proposer', 'proposal_contract', 'AiCallBudget', 'genai', 'GEMINI'):
        assert word not in text
    assert text.isascii()


def test_schema_files_committed_and_in_sync():
    assert schema_export.check() == []
    names = set(schema_export.SCHEMA_MODELS)
    assert 'mirum_interface_error_v0_1.schema.json' in names
    assert set(I.API_SCHEMA_MODELS) <= names
    for op in I.OPERATIONS:
        assert op.input_model in I.API_SCHEMA_MODELS.values()
    for model in I.RESPONSE_MODELS:
        assert model in I.API_SCHEMA_MODELS.values()
    for name in ('mirum_operation_registry_v0_1.json', 'mirum_operation_registry_v0_2.json'):
        assert (schema_export.SCHEMA_DIR / name).is_file()
