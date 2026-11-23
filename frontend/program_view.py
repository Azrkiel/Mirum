"""Pure view-data builders for the MEL program UI (F09). No streamlit import, no network.

Every model/server-provided string is passed through _plain (Markdown escape, same rule as
engineering_ui._plain; copied, not imported). Nothing here compares images or meshes.
"""
import json

try:
    from program_state import NOT_AVAILABLE, caret_excerpt
except ImportError:  # imported as frontend.program_view (tests) rather than from the frontend directory
    from frontend.program_state import NOT_AVAILABLE, caret_excerpt

MAX_IR_BYTES = 1048576
MAX_ROWS = 256

_MARKDOWN_SPECIAL = set('\\`*_{}[]()#+-.!|<>~$&:')

LABELS = {
    'validate': 'Validate', 'format': 'Format', 'plan': 'Plan', 'stage': 'Stage', 'review': 'Review',
    'run': 'Run', 'cancel': 'Cancel', 'compare': 'Compare', 'restore': 'Restore', 'rebuild': 'Rebuild',
    'refresh': 'Refresh',
    'requirements_changed': 'Fixed requirements changed',
    'hashes_equal': 'Same', 'hashes_differ': 'Different',
    'preview_caption': 'geometry preview, not proof',
    'section_requirements': 'Requirements', 'section_candidates': 'Candidates', 'section_analysis': 'Analysis',
    'section_evidence': 'Evidence', 'section_execution': 'Execution', 'section_lock': 'Lock',
    'section_backend_lock': 'Backend lock',
    'not_available': NOT_AVAILABLE,
}
HASH_ROWS = (('source', 'Source'), ('semantic', 'Meaning'), ('brief', 'Brief'),
             ('fixed_requirements', 'Fixed requirements'), ('lock', 'Execution lock'))
SECTION_ORDER = ('requirements', 'candidates', 'analysis', 'evidence', 'execution', 'lock', 'backend_lock')


def _plain(text):
    """Escape Markdown so model/server text renders as plain text (no links, images or emphasis)."""
    return ''.join(('\\' + c if c in _MARKDOWN_SPECIAL else c) for c in str(text))


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _value_text(value):
    if not isinstance(value, dict):
        return _plain(value)
    if value.get('kind') == 'quantity':
        number = value.get('value')
        text = '%.10g' % number if type(number) in (int, float) else str(number)
        return _plain('%s %s' % (text, value.get('unit', '')))
    if value.get('kind') == 'text':
        return _plain(value.get('text', ''))
    return _plain(json.dumps(value, sort_keys=True)[:240])


def _parse_ir(data):
    if isinstance(data, (bytes, bytearray)):
        if len(data) > MAX_IR_BYTES:
            return None
        try:
            data = json.loads(bytes(data).decode('utf-8'))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None
    return data if isinstance(data, dict) else None


def meaning_table(program_ir_json):
    """Rows {section, name, value} for requirements, candidates, backend and exports from the staged
    revision's program-ir export (bytes, text or parsed dict). Empty list when not parseable."""
    ir = _parse_ir(program_ir_json)
    if ir is None:
        return []
    rows = []
    plan = ir.get('requirements')
    for item in (plan.get('requirements') if isinstance(plan, dict) else None) or []:
        if isinstance(item, dict) and len(rows) < MAX_ROWS:
            rows.append({'section': 'requirements', 'name': _plain(item.get('id', '')),
                         'value': _value_text(item.get('value'))})
    cands = ir.get('candidates')
    if isinstance(cands, dict):
        rows.append({'section': 'candidates', 'name': 'mode', 'value': _plain(cands.get('mode', ''))})
        for param in (cands.get('parameters') or [])[:64]:
            if isinstance(param, dict) and len(rows) < MAX_ROWS:
                values = ', '.join(_value_text(v) for v in (param.get('values') or [])[:64])
                rows.append({'section': 'candidates', 'name': _plain(param.get('id', '')), 'value': values})
    execution = ir.get('execution')
    if isinstance(execution, dict):
        rows.append({'section': 'backend', 'name': 'backend', 'value': _plain(execution.get('backend', ''))})
        exports = execution.get('exports') or []
        rows.append({'section': 'exports', 'name': 'exports',
                     'value': ', '.join(_plain(e) for e in exports[:32])})
    return rows


def compare_view(diff_response, deltas=None):
    """View data for a POST compare response: hash-equality rows, banner flag, sectioned changes."""
    diff = _get(diff_response, 'diff', diff_response) or {}
    hashes = _get(diff, 'hashes') or {}
    hash_rows = [{'key': key, 'label': label, 'equal': _get(hashes, key) is True,
                  'text': LABELS['hashes_equal'] if _get(hashes, key) is True else LABELS['hashes_differ']}
                 for key, label in HASH_ROWS]
    sections = {name: [] for name in SECTION_ORDER}
    for entry in (_get(diff, 'changes') or [])[:MAX_ROWS]:
        section = _get(entry, 'section')
        if section in sections:
            sections[section].append({
                'key': _plain(_get(entry, 'key', '')), 'field': _plain(_get(entry, 'field', '')),
                'before': None if _get(entry, 'before') is None else _plain(_get(entry, 'before')),
                'after': None if _get(entry, 'after') is None else _plain(_get(entry, 'after'))})
    return {
        'hash_rows': hash_rows,
        'requirements_changed': _get(diff, 'requirements_changed') is True,
        'banner': LABELS['requirements_changed'] if _get(diff, 'requirements_changed') is True else None,
        'sections': [{'section': name, 'title': LABELS['section_' + name], 'changes': sections[name]}
                     for name in SECTION_ORDER if sections[name]],
        'change_count': _get(diff, 'change_count', 0),
        'changes_truncated': _get(diff, 'changes_truncated') is True,
        'detail_unavailable': _get(diff, 'detail_unavailable'),
        'deltas': list(deltas or []),
    }


def diagnostics_rows(validate_result, source=''):
    """Rows for a failed validate/plan/stage: a ProgramAPIError (or its envelope dict) -> one row with
    code, category, message, hint, line:col and a caret excerpt. A success result gives no rows."""
    if validate_result is None or _get(validate_result, 'ok') is True:
        return []
    span = _get(validate_result, 'span')
    message = _get(validate_result, 'detail_message', None)
    hint = _get(validate_result, 'detail_hint', None)
    if message is None:
        message = _get(validate_result, 'message', '')
    if hint is None:
        hint = _get(validate_result, 'hint', '')
    line, col = (_get(span, 'line'), _get(span, 'column')) if span else (None, None)
    excerpt, caret = caret_excerpt(source, span) if span else ('', '')
    return [{
        'code': _plain(_get(validate_result, 'code', '')),
        'category': _plain(_get(validate_result, 'category', '') or ''),
        'message': _plain(message or ''), 'hint': _plain(hint or ''),
        'location': '%d:%d' % (line, col) if line and col else '',
        'excerpt': excerpt, 'caret': caret}]


def history_rows(history, head_revision_id=None):
    """Rows for a HistoryView (newest first as given). IDs are returned for the UI's session dict only."""
    head = head_revision_id if head_revision_id is not None else _get(history, 'head_revision_id')
    rows = []
    for rev in (_get(history, 'revisions') or [])[:MAX_ROWS]:
        rows.append({
            'revision_id': _get(rev, 'revision_id'), 'kind': _plain(_get(rev, 'kind', '')),
            'state': _plain(_get(rev, 'state', '')), 'result_kind': _plain(_get(rev, 'result_kind', '')),
            'artifacts': _plain(_get(rev, 'artifacts', '')), 'created_at': _plain(_get(rev, 'created_at', '')),
            'is_head': _get(rev, 'revision_id') == head, 'is_current': _get(rev, 'is_current') is True})
    return rows
