"""Pure tests for frontend/program_state.py and program_view.py."""
import hashlib
import json
import pathlib
import re

import pytest

from frontend import program_state as ps
from frontend import program_view as pv

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / 'docs/plans/checked-engineering-v1/phase-3-framework-language/examples/beam.mir'


def test_template_matches_example_file():
    if not EXAMPLE.exists():
        pytest.skip('docs not present')
    text = EXAMPLE.read_bytes().decode('utf-8').replace('\r\n', '\n')
    assert ps.BEAM_TEMPLATE == text
    assert ps.BEAM_TEMPLATE.isascii()


def test_can_stage_only_for_validated_buffer():
    sha = ps.sha256_text('abc')
    assert sha == hashlib.sha256(b'abc').hexdigest()
    assert ps.can_stage('abc', sha)
    assert not ps.can_stage('abcd', sha)
    assert not ps.can_stage('abc', None) and not ps.can_stage('abc', '')
    big = 'a' * 65537
    assert ps.buffer_too_large(big) and not ps.buffer_too_large('a' * 65536)
    assert not ps.can_stage(big, ps.sha256_text(big))
    assert ps.sha256_text(chr(233)) == hashlib.sha256(chr(233).encode()).hexdigest()


def span(line, col, end_line=None, end_col=None):
    return dict(line=line, column=col, end_line=end_line or line, end_column=end_col or col + 1)


def test_caret_excerpt_basic_and_edges():
    assert ps.caret_excerpt('abc\ndef ghi\n', span(2, 5, 2, 8)) == ('def ghi', '    ^^^')
    assert ps.caret_excerpt('abc\r\ndef\r\n', span(2, 2)) == ('def', ' ^')
    assert ps.caret_excerpt('abc', span(5, 1)) == ('', '')
    assert ps.caret_excerpt('abc', None) == ('', '')
    assert ps.caret_excerpt('abc', span(0, 1)) == ('', '')
    # column past the end of line still gives a caret
    assert ps.caret_excerpt('abc', span(1, 10, 1, 12)) == ('abc', '   ^')
    # multi-line span covers to end of the first line
    assert ps.caret_excerpt('abcdef\nx', span(1, 3, 2, 1)) == ('abcdef', '  ^^^^')
    # tab expansion keeps alignment
    text, caret = ps.caret_excerpt('\tx = 1', span(1, 2, 1, 3))
    assert text == '    x = 1' and caret == '    ^'


def test_caret_excerpt_is_bounded():
    line = 'x' * 5000
    text, caret = ps.caret_excerpt(line, span(1, 4000, 1, 4010))
    assert len(text) <= ps.MAX_EXCERPT_CHARS and len(caret) <= ps.MAX_EXCERPT_CHARS + 1
    assert caret.strip('^ ') == '' and '^' in caret
    text, caret = ps.caret_excerpt('abc', span(1, 1, 1, 10 ** 9))
    assert (text, caret) == ('abc', '^^^')


def test_stage_guard():
    head = 'a' * 64
    g = ps.stage_guard(head)
    assert g.require_requirements_sha256 == head and not g.bypassed
    assert ps.stage_guard(head, ['req_a'], ticked=False).require_requirements_sha256 == head
    assert ps.stage_guard(head, [], ticked=True).require_requirements_sha256 == head
    bypass = ps.stage_guard(head, ['req_a'], ticked=True)
    assert bypass.require_requirements_sha256 is None and bypass.bypassed
    assert ps.stage_guard(None, ['x'], ticked=True).require_requirements_sha256 is None


def test_changed_requirement_ids():
    diff = {'diff': {'changes': [
        {'section': 'requirements', 'key': 'length', 'field': 'value'},
        {'section': 'requirements', 'key': 'length', 'field': 'unit'},
        {'section': 'candidates', 'key': 'height', 'field': 'values'},
        {'section': 'requirements', 'key': 'tip_force', 'field': '*'}]}}
    assert ps.changed_requirement_ids(diff) == ('length', 'tip_force')
    assert ps.changed_requirement_ids(diff['diff']) == ('length', 'tip_force')

    class Err:
        requirement_ids = ('width', 'bad id!')
    assert ps.changed_requirement_ids(Err()) == ('width',)
    assert ps.changed_requirement_ids({}) == ()


def test_staleness():
    assert not ps.is_stale('a', 'a') and not ps.is_stale(None, None)
    assert ps.is_stale('a', 'b') and ps.is_stale(None, 'b') and ps.is_stale('a', None)
    assert ps.stale_notice('a', 'b') == 'changed in another tab: refresh and rebase'
    assert ps.stale_notice('a', 'a') is None


def test_labels_never_publish_unless_verified():
    assert 'Published' in ps.result_label('published', 'verified', 'geometry_only')
    assert 'geometry_only' in ps.result_label('published', 'verified', 'geometry_only')
    assert 'ublished' not in ps.result_label('published', 'failed')
    assert 'ublished' not in ps.result_label('published', None)
    for kind in ('verification_failed', 'rejected', 'unavailable', 'pending', 'failed', 'cancelled', 'conflict',
                 'weird'):
        for verdict in (None, 'verified', 'failed'):
            assert 'ublished' not in ps.result_label(kind, verdict, 'scope')
    assert ps.result_label('verification_failed') == 'Verification failed'
    assert ps.result_label('rejected') != ps.result_label('unavailable')
    assert ps.result_tone('published', 'verified') == 'success'
    assert ps.result_tone('verification_failed') == 'error'


def test_identical_glb_two_revisions_only_published_labelled_and_previewed():
    glb = b'same-glb-bytes'
    sha = hashlib.sha256(glb).hexdigest()
    ok = dict(result_kind='published', state='published')
    bad = dict(result_kind='verification_failed', state='failed')
    verified = ps.verify_payload(glb, sha)
    assert verified
    assert ps.preview_allowed(ok, verified)
    assert not ps.preview_allowed(bad, verified)
    assert not ps.preview_allowed(ok, False) and not ps.preview_allowed(ok, None)
    assert not ps.preview_allowed(ok, ps.verify_payload(b'other', sha))
    assert 'ublished' in ps.result_label(ok['result_kind'], 'verified', 's')
    assert 'ublished' not in ps.result_label(bad['result_kind'], 'failed', 's')
    assert ps.PREVIEW_CAPTION == 'geometry preview, not proof'


def test_expected_export_sha_from_own_bundle():
    bundle = {'manifest_sha256': 'm' * 64, 'zip_sha256': 'z' * 64,
              'payloads': [{'path': 'preview.glb', 'sha256': 'g' * 64}, {'path': 'report.json', 'sha256': 'r' * 64}]}
    assert ps.expected_export_sha(bundle, 'glb') == 'g' * 64
    assert ps.expected_export_sha(bundle, 'report') == 'r' * 64
    assert ps.expected_export_sha(bundle, 'zip') == 'z' * 64
    assert ps.expected_export_sha(bundle, 'manifest') == 'm' * 64
    assert ps.expected_export_sha(bundle, 'step') is None
    assert ps.expected_export_sha(None, 'glb') is None
    assert ps.expected_export_sha(bundle, 'program-ir') is None


def test_offer_rebuild():
    class Err:
        def __init__(self, kind, code=''):
            self.kind, self.code = kind, code
    assert ps.offer_rebuild(Err('expired')) and ps.offer_rebuild(Err('integrity'))
    assert ps.offer_rebuild(Err('not_found', 'artifacts_missing'))
    assert not ps.offer_rebuild(Err('conflict', 'stale_parent')) and not ps.offer_rebuild(Err('busy'))
    assert ps.artifacts_need_rebuild({'artifacts': 'expired'}) and ps.artifacts_need_rebuild({'artifacts': 'missing'})
    assert not ps.artifacts_need_rebuild({'artifacts': 'available'})


def report(*items):
    evals = [{'candidate_sha256': 'c' * 64, 'report_sha256': 'r' * 64,
              'native_report': {'passed': passed, 'measurements': {'height_mm': h},
                                'physics': {'solution': {'tip_displacement_mm': d, 'root_bending_stress_mpa': s}}}}
             for h, d, s, passed in items]
    return json.dumps({'schema_version': 'engineering_report_v1', 'evaluations': evals}).encode()


def test_measured_deltas_from_own_reports():
    a = report((5, 2.0, 100.0, False), (10, 0.5, 50.0, True), (15, 0.2, 30.0, True))
    b = report((3, 3.0, 90.0, False), (8, 0.25, 40.0, True))
    rows = {r['metric']: r for r in ps.measured_deltas(a, b)}
    assert rows['deflection_mm']['a'] == 2.0 and rows['deflection_mm']['b'] == 3.0
    assert rows['deflection_mm']['delta'] == 1.0
    assert rows['stress_mpa']['a'] == 100.0 and rows['stress_mpa']['delta'] == -10.0
    assert rows['height_mm']['a'] == 10.0 and rows['height_mm']['b'] == 8.0
    assert rows['height_mm']['delta'] == -2.0
    assert rows['height_mm']['unit'] == 'mm'


def test_measured_deltas_not_available_when_side_missing():
    a = report((10, 0.5, 50.0, True))
    for rows in (ps.measured_deltas(a, None), ps.measured_deltas(None, a), ps.measured_deltas(None, None),
                 ps.measured_deltas(a, b'not json'), ps.measured_deltas(a, b'[1]'),
                 ps.measured_deltas(a, b'{"evaluations": 3}')):
        for row in rows:
            assert row['delta'] is None and row['delta_text'] == 'not available'
            assert 'not available' in (row['a_text'], row['b_text'])
    rows = ps.measured_deltas(a, None)
    assert rows[0]['a_text'] == '0.5' and rows[0]['b'] is None
    assert ps.measured_deltas(a, b'x' * (ps.MAX_REPORT_BYTES + 1))[0]['b'] is None
    nan = b'{"evaluations":[{"native_report":{"physics":{"solution":{"tip_displacement_mm":NaN}}}}]}'
    assert ps.measured_deltas(a, nan)[0]['b'] is None


def test_view_meaning_table_and_escaping():
    ir = {'requirements': {'requirements': [
              {'id': 'length', 'value': {'kind': 'quantity', 'dimension': 'length', 'value': 200.0, 'unit': 'mm'}},
              {'id': 'note', 'value': {'kind': 'text', 'text': '[x](http://evil) *b*'}}]},
          'candidates': {'mode': 'bounded_thickness', 'parameters': [
              {'id': 'height', 'values': [{'kind': 'quantity', 'dimension': 'length', 'value': 5.0, 'unit': 'mm'},
                                          {'kind': 'quantity', 'dimension': 'length', 'value': 10.0, 'unit': 'mm'}]}]},
          'execution': {'backend': 'occt', 'exports': ['step', 'glb']}}
    rows = pv.meaning_table(json.dumps(ir).encode())
    sections = [r['section'] for r in rows]
    assert sections == ['requirements', 'requirements', 'candidates', 'candidates', 'backend', 'exports']
    note = rows[1]['value']
    assert '[' not in note.replace('\\[', '') and '](' not in note
    assert rows[3]['value'].startswith('5 mm') and '10 mm' in rows[3]['value']
    assert pv.meaning_table(b'nope') == [] and pv.meaning_table(b'[]') == []
    assert pv.meaning_table(b'x' * (pv.MAX_IR_BYTES + 1)) == []
    assert pv.meaning_table(ir)[0]['name'] == 'length'


def test_view_compare():
    diff = {'diff': {'hashes': {'source': False, 'semantic': False, 'brief': True, 'fixed_requirements': False,
                                'lock': True},
                     'requirements_changed': True, 'change_count': 2,
                     'changes': [{'section': 'requirements', 'key': 'len_gth', 'field': 'value',
                                  'before': '"a"', 'after': None},
                                 {'section': 'candidates', 'key': 'height', 'field': 'values', 'before': '1',
                                  'after': '2'}]}}
    view = pv.compare_view(diff, ps.measured_deltas(None, None))
    assert view['requirements_changed'] and view['banner'] == 'Fixed requirements changed'
    assert {r['key']: r['equal'] for r in view['hash_rows']}['brief'] is True
    assert [s['section'] for s in view['sections']] == ['requirements', 'candidates']
    assert view['sections'][0]['changes'][0]['key'] == 'len\\_gth'
    assert view['sections'][0]['changes'][0]['after'] is None
    assert len(view['deltas']) == 3
    plain = pv.compare_view({'diff': {'hashes': {}, 'requirements_changed': False}})
    assert plain['banner'] is None and plain['sections'] == []


def test_view_diagnostics_rows():
    class Err:
        code = 'MEL-S001'
        category = 'syntax'
        detail_message = 'Expected `;`.'
        detail_hint = 'Add one.'
        span = dict(line=2, column=3, end_line=2, end_column=5)
    rows = pv.diagnostics_rows(Err(), 'ab\ncdefg\n')
    assert len(rows) == 1
    row = rows[0]
    assert row['location'] == '2:3' and row['excerpt'] == 'cdefg' and row['caret'] == '  ^^'
    assert row['code'] == 'MEL\\-S001' and '\\`' in row['message'] and row['category'] == 'syntax'
    assert pv.diagnostics_rows({'ok': True}) == [] and pv.diagnostics_rows(None) == []
    env = pv.diagnostics_rows({'code': 'x', 'message': 'm', 'hint': 'h'}, '')
    assert env[0]['location'] == '' and env[0]['caret'] == ''


def test_view_history_rows():
    hist = {'head_revision_id': 'b' * 32, 'revisions': [
        {'revision_id': 'b' * 32, 'kind': 'edit', 'state': 'published', 'result_kind': 'published',
         'artifacts': 'available', 'created_at': '2026-01-01T00:00:00Z', 'is_current': True},
        {'revision_id': 'a' * 32, 'kind': 'edit', 'state': 'failed', 'result_kind': 'failed',
         'artifacts': 'none', 'created_at': '2026-01-01T00:00:00Z', 'is_current': False}]}
    rows = pv.history_rows(hist)
    assert [r['is_head'] for r in rows] == [True, False]
    assert rows[0]['created_at'].startswith('2026\\-01')


def test_no_similarity_code_in_program_modules():
    banned = ('similarity', 'ssim', 'perceptual', 'hausdorff', 'chamfer', 'iou', 'pixel', 'histogram')
    for path in sorted((ROOT / 'frontend').glob('program_*.py')):
        if path.name.startswith('test_'):
            continue
        text = path.read_text(encoding='utf-8').lower()
        for word in banned:
            hits = [m for m in re.finditer(r'\b' + word + r'\b', text)]
            allowed = text.count('no image or mesh similarity')
            assert len(hits) <= (allowed if word == 'similarity' else 0), (path.name, word)


def test_modules_are_ascii_and_free_of_backend_imports():
    for name in ('program_client.py', 'program_state.py', 'program_view.py'):
        text = (ROOT / 'frontend' / name).read_text(encoding='utf-8')
        assert text.isascii(), name
        assert not re.search(r'^\s*(from|import)\s+(engineering|backend)\b', text, re.M), name
        assert 'st.query_params' not in text and 'import streamlit' not in text
