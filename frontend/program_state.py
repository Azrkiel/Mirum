"""Pure session/state logic for the MEL program UI (F09). No streamlit import, no network.

Nothing here stores or emits ids for URLs; the UI keeps ids in a plain session dict.
No image or mesh comparison of any kind lives in the program_* frontend modules: a revision is only
labelled from its result kind, evidence verdict and claim scope.
"""
import hashlib
import json
import re
from dataclasses import dataclass

MAX_BUFFER_BYTES = 65536
MAX_REPORT_BYTES = 4194304
MAX_EXCERPT_CHARS = 120
TAB_WIDTH = 4
NOT_AVAILABLE = 'not available'
STALE_NOTICE = 'changed in another tab: refresh and rebase'
PREVIEW_CAPTION = 'geometry preview, not proof'

BEAM_TEMPLATE = '''// Proposed MEL v0.1 source fixture. Requires F04/F05 implementation.
// Illustrative properties only; no certified material or physical test claim.
language mirum "0.1";
design reference_beam {
  capability = "rectangular_cantilever";
  capability_version = "1.0.0";
  backend = "occt";
  numerical_policy = "beam_numerical_v1";
  resource_policy = "engineering_resources_v1";
  length = 200 mm;
  width = 10 mm;
  dimensional_tolerance = 0.01 mm;
  tip_force = 50 N;
  max_tip_deflection = 1 mm;
  youngs_modulus = 70000 MPa;
  allowable_stress = 120 MPa;
  material_reference = "Illustrative user-provided properties; not certified stock values";
  material_source_kind = "illustrative";
  assumptions = "ideal_clamp_end_force_linear_elastic";
  mode = "bounded_thickness";
  candidate_heights = [5 mm, 10 mm, 15 mm];
  exports = ["step", "glb"];
}
'''

# Bundle export format -> payload path inside the revision's own bundle (engineering.bundles.FORMATS).
EXPORT_PAYLOAD_PATH = {
    'brief': 'brief.json', 'spec': 'selected_spec.json', 'source': 'model.py', 'step': 'model.step',
    'glb': 'preview.glb', 'attempts': 'attempts.json', 'report': 'report.json', 'markdown': 'report.md'}

_SAFE_SCOPE = re.compile(r'[^A-Za-z0-9_.-]')
_REQ_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')


def sha256_text(text):
    """sha256 hex of the editor buffer encoded as UTF-8 (the digest the backend reports as source_sha256)."""
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


def buffer_size(buffer):
    return len(str(buffer).encode('utf-8'))


def buffer_too_large(buffer):
    return buffer_size(buffer) > MAX_BUFFER_BYTES


def can_stage(buffer, last_validated_sha):
    """D3: Stage is enabled only when the buffer is within the cap and equals the last validated source."""
    if not last_validated_sha or buffer_too_large(buffer):
        return False
    return sha256_text(buffer) == last_validated_sha


# ------------------------------------------------------------------------------------- caret excerpt

def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _int(value):
    return value if type(value) is int else None


def caret_excerpt(source, span):
    """(line_text, caret_line) for a 1-based span; ('', '') when the span is absent or out of range.

    Tabs become TAB_WIDTH spaces, CRLF/CR/LF all end a line, the line is windowed to MAX_EXCERPT_CHARS and
    the caret run covers the span on its first line (to end of line for a multi-line span)."""
    line_no, col = _int(_get(span, 'line')), _int(_get(span, 'column'))
    if line_no is None or col is None or line_no < 1 or col < 1:
        return '', ''
    lines = re.split(r'\r\n|\r|\n', str(source))
    if line_no > len(lines):
        return '', ''
    raw = lines[line_no - 1]
    end_line, end_col = _int(_get(span, 'end_line')), _int(_get(span, 'end_column'))
    if end_line is None or end_line < line_no:
        end_line = line_no
    if end_col is None or end_col < 1:
        end_col = col + 1
    if end_line > line_no:
        end_col = len(raw) + 1
    col0 = min(col - 1, len(raw))
    end0 = max(min(end_col - 1, len(raw)), col0 + 1) if col0 < len(raw) else col0 + 1

    def expand(prefix):
        return len(prefix.replace('\t', ' ' * TAB_WIDTH))

    text = raw.replace('\t', ' ' * TAB_WIDTH)
    start = expand(raw[:col0])
    width = max(1, expand(raw[col0:end0]))
    shift = 0
    if len(text) > MAX_EXCERPT_CHARS or start + width > MAX_EXCERPT_CHARS:
        shift = max(0, min(start - MAX_EXCERPT_CHARS // 3, max(0, len(text) - MAX_EXCERPT_CHARS)))
        text = text[shift:shift + MAX_EXCERPT_CHARS]
    start = max(0, start - shift)
    width = max(1, min(width, MAX_EXCERPT_CHARS - start))
    return text, ' ' * start + '^' * width


# ------------------------------------------------------------------------------- requirement guard (D5)

@dataclass(frozen=True)
class StageGuard:
    require_requirements_sha256: object   # str to send as X-Mirum-Require-Requirements, or None
    bypassed: bool                        # True only when the explicit checkbox with ids was ticked
    changed_requirement_ids: tuple = ()


def changed_requirement_ids(source):
    """Requirement ids from a compare response (changes in the requirements section), a diff dict, or a
    requirements_changed refusal (anything with requirement_ids). Deduplicated, ordered, bounded."""
    found = []
    ids = _get(source, 'requirement_ids')
    if isinstance(ids, (list, tuple)):
        found.extend(ids)
    diff = _get(source, 'diff', source)
    changes = _get(diff, 'changes') if diff is not None else None
    if isinstance(changes, (list, tuple)):
        for entry in changes:
            if _get(entry, 'section') == 'requirements':
                found.append(_get(entry, 'key'))
    out = []
    for item in found:
        if type(item) is str and _REQ_ID_RE.match(item) and item not in out:
            out.append(item)
    return tuple(out[:16])


def stage_guard(head_fixed_requirements_sha256, changed_ids=(), ticked=False):
    """D5: always guard with the head's fixed requirements hash. The guard is dropped only when the user
    ticked the explicit checkbox AND there is at least one changed requirement id to list. No head ->
    nothing to guard."""
    ids = tuple(changed_ids or ())
    if not head_fixed_requirements_sha256:
        return StageGuard(None, False, ids)
    if ticked and ids:
        return StageGuard(None, True, ids)
    return StageGuard(head_fixed_requirements_sha256, False, ids)


# ------------------------------------------------------------------------------------ freshness (D8)

def is_stale(seen_head_revision_id, listed_head_revision_id):
    return seen_head_revision_id != listed_head_revision_id


def stale_notice(seen_head_revision_id, listed_head_revision_id):
    return STALE_NOTICE if is_stale(seen_head_revision_id, listed_head_revision_id) else None


# --------------------------------------------------------------------------------------- labels (D6)

def _scope(claim_scope):
    return _SAFE_SCOPE.sub('', str(claim_scope or ''))[:64]


def result_label(result_kind, verdict=None, claim_scope=None):
    """Label from result_kind + evidence verdict + claim scope only. 'published' appears only for a
    published result whose evidence verdict is verified."""
    if result_kind == 'published':
        if verdict != 'verified':
            return 'Evidence not verified'
        scope = _scope(claim_scope)
        return 'Published (verified, scope: %s)' % scope if scope else 'Published (verified)'
    return {
        'verification_failed': 'Verification failed',
        'rejected': 'Rejected by policy',
        'unavailable': 'Evidence unavailable',
        'pending': 'Pending',
        'failed': 'Failed',
        'cancelled': 'Cancelled',
        'conflict': 'Conflict',
    }.get(result_kind, 'Unknown')


def result_tone(result_kind, verdict=None):
    if result_kind == 'published' and verdict == 'verified':
        return 'success'
    if result_kind in ('pending', 'cancelled'):
        return 'info'
    if result_kind in ('unavailable', 'conflict') or result_kind == 'published':
        return 'warning'
    return 'error'


def preview_allowed(revision_status, glb_bytes_verified):
    """Only a published revision whose own GLB passed the sha check may be previewed."""
    if glb_bytes_verified is not True:
        return False
    return _get(revision_status, 'result_kind') == 'published' and _get(revision_status, 'state') == 'published'


def verify_payload(data, expected_sha256):
    return (isinstance(data, (bytes, bytearray)) and type(expected_sha256) is str
            and hashlib.sha256(bytes(data)).hexdigest() == expected_sha256)


def expected_export_sha(bundle, fmt):
    """Digest of one export taken from the revision's own bundle (inspect), or None when it has none."""
    if not bundle:
        return None
    if fmt == 'zip':
        return _get(bundle, 'zip_sha256')
    if fmt == 'manifest':
        return _get(bundle, 'manifest_sha256')
    path = EXPORT_PAYLOAD_PATH.get(fmt)
    for item in _get(bundle, 'payloads', ()) or ():
        if path is not None and _get(item, 'path') == path:
            return _get(item, 'sha256')
    return None


# ------------------------------------------------------------------------------------ expiry (D7)

def offer_rebuild(error):
    """True for expired / integrity / missing refusals (ProgramAPIError-like) so the UI offers Rebuild."""
    if _get(error, 'kind') in ('expired', 'integrity'):
        return True
    return _get(error, 'code') in ('artifacts_missing', 'artifacts_expired', 'artifacts_altered')


def artifacts_need_rebuild(revision_status):
    return _get(revision_status, 'artifacts') in ('expired', 'missing')


# ---------------------------------------------------------------------------------- measured deltas

def _load_report(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_REPORT_BYTES:
        return None
    try:
        value = json.loads(bytes(data).decode('utf-8'), parse_constant=lambda _n: (_ for _ in ()).throw(ValueError()))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _num(value):
    if type(value) in (int, float) and value == value and abs(value) != float('inf'):
        return float(value)
    return None


def report_measurements(data):
    """{'deflection_mm', 'stress_mpa', 'height_mm'} (each float or None) from one verified report.json."""
    report = _load_report(data)
    out = {'deflection_mm': None, 'stress_mpa': None, 'height_mm': None}
    evaluations = report.get('evaluations') if report else None
    if not isinstance(evaluations, list):
        return out
    deflections, stresses, heights = [], [], []
    for item in evaluations[:64]:
        native = item.get('native_report') if isinstance(item, dict) else None
        if not isinstance(native, dict):
            continue
        physics = native.get('physics')
        solution = physics.get('solution') if isinstance(physics, dict) else None
        if isinstance(solution, dict):
            d, s = _num(solution.get('tip_displacement_mm')), _num(solution.get('root_bending_stress_mpa'))
            if d is not None:
                deflections.append(abs(d))
            if s is not None:
                stresses.append(abs(s))
        measured = native.get('measurements')
        height = _num(measured.get('height_mm')) if isinstance(measured, dict) else None
        if height is not None and native.get('passed') is True:
            heights.append(height)
    out['deflection_mm'] = max(deflections) if deflections else None
    out['stress_mpa'] = max(stresses) if stresses else None
    out['height_mm'] = min(heights) if heights else None
    return out


def _fmt(value):
    return NOT_AVAILABLE if value is None else '%.6g' % value


def measured_deltas(report_a, report_b):
    """Rows (max tip deflection, max stress, selected height) from each side's own verified report bytes.
    A side without a verified report (None or unparseable) is 'not available'; nothing is inferred."""
    a, b = report_measurements(report_a), report_measurements(report_b)
    rows = []
    for key, label, unit in (('deflection_mm', 'Max tip deflection', 'mm'),
                             ('stress_mpa', 'Max stress', 'MPa'),
                             ('height_mm', 'Selected height', 'mm')):
        va, vb = a[key], b[key]
        delta = None if va is None or vb is None else vb - va
        rows.append({'metric': key, 'label': label, 'unit': unit, 'a': va, 'b': vb, 'delta': delta,
                     'a_text': _fmt(va), 'b_text': _fmt(vb), 'delta_text': _fmt(delta)})
    return rows
