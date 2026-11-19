"""Pure parts of the B13 step-12 UI driver: field comparison, call budget, redaction.

No browser, no Docker, no network and no provider. The driver imports playwright lazily.
"""
import copy
import json
from pathlib import Path

import pytest

from scripts import b13_step12_ui as driver

CORPUS = Path('backend/tests/fixtures/engineering/ai-corpus-v1.json')


def case(case_id='C01'):
    return driver.load_case(CORPUS, case_id)


# ---------------------------------------------------------------- case selection

def test_default_case_is_a_complete_corpus_case():
    c = case('C01')
    assert c['kind'] == 'complete' and c['expected_content']['mode'] == 'bounded_thickness'


@pytest.mark.parametrize('bad', ['A01', 'U01', 'C11', 'C00', 'c01', '', 'C1', 'C01 ', '../C01'])
def test_only_complete_cases_are_selectable(bad):
    with pytest.raises(ValueError):
        driver.load_case(CORPUS, bad)


def test_changed_corpus_is_refused(tmp_path):
    changed = tmp_path / 'corpus.json'
    changed.write_bytes(CORPUS.read_bytes() + b' ')
    with pytest.raises(ValueError, match='corpus'):
        driver.load_case(changed, 'C01')


# ---------------------------------------------------------------- displayed vs expected

def json_view_text(content, quoted_keys=True, index_lines=True):
    """Render like a JSON tree viewer's text: one key/value per line, arrays as index lines."""
    out = []

    def key(k):
        return '"%s"' % k if quoted_keys else k

    def walk(node, name):
        if isinstance(node, dict):
            out.append('%s:{%d items' % (key(name), len(node)) if name else '{')
            for k, v in node.items():
                walk(v, k)
            out.append('}')
        elif isinstance(node, list):
            out.append('%s:[%d items' % (key(name), len(node)))
            for i, v in enumerate(node):
                if isinstance(v, (dict, list)):
                    walk(v, str(i))
                else:
                    out.append('%d:%s' % (i, json.dumps(v)) if index_lines else json.dumps(v) + ',')
            out.append(']')
        else:
            out.append('%s:%s' % (key(name), json.dumps(node)))
    walk(content, '')
    return '\n'.join(out)


@pytest.mark.parametrize('quoted', [True, False])
@pytest.mark.parametrize('index_lines', [True, False])
def test_json_view_text_round_trips_the_expected_leaves(quoted, index_lines):
    expected = case()['expected_content']
    shown = driver.parse_json_view_text(json_view_text(expected, quoted, index_lines))
    ok, rows = driver.compare_displayed(expected, shown)
    assert ok, [r for r in rows if not r['match']]
    assert {r['field'] for r in rows} >= {'length_mm', 'width_mm', 'tip_force_n', 'candidate_heights_mm',
                                          'reference', 'source_kind', 'mode'}


@pytest.mark.parametrize('field,value', [
    ('tip_force_n', 41.0), ('length_mm', 201.0), ('max_tip_deflection_mm', 2.0),
    ('youngs_modulus_mpa', 69000.0), ('allowable_stress_mpa', 121.0), ('width_mm', 11.0),
    ('dimensional_tolerance_mm', 0.1), ('source_kind', 'documented'),
    ('reference', 'Illustrative benchmark material inputs'), ('mode', 'fixed_design'),
    ('candidate_heights_mm', [5.0, 10.0]), ('candidate_heights_mm', [5.0, 10.0, 16.0]),
])
def test_any_changed_displayed_value_fails_the_comparison(field, value):
    expected = case()['expected_content']
    shown = driver.parse_json_view_text(json_view_text(expected))
    shown[field] = value
    ok, rows = driver.compare_displayed(expected, shown)
    assert not ok
    assert [r['field'] for r in rows if not r['match']] == [field]


def test_missing_displayed_value_fails_never_passes_silently():
    expected = case()['expected_content']
    shown = driver.parse_json_view_text(json_view_text(expected))
    del shown['tip_force_n']
    ok, rows = driver.compare_displayed(expected, shown)
    assert not ok and next(r for r in rows if r['field'] == 'tip_force_n')['shown'] is None
    assert driver.compare_displayed(expected, {})[0] is False


def test_numbers_compare_numerically_not_by_text():
    shown = driver.parse_json_view_text('"length_mm":200\n"width_mm":10.0')
    assert shown['length_mm'] == 200 and shown['width_mm'] == 10.0
    assert driver.compare_displayed({'requirements': {'length_mm': 200.0}}, shown)[0]
    assert not driver.compare_displayed({'requirements': {'length_mm': 200.5}}, shown)[0]


def test_reference_content_parses_and_compares_to_itself():
    shown = driver.parse_json_view_text(json_view_text(driver.REFERENCE_CONTENT))
    assert driver.compare_displayed(driver.REFERENCE_CONTENT, shown)[0]


def test_caption_parsing():
    text = 'noise\nDraft abc123 | revision 2 | brief SHA-256 ' + 'a' * 64 + '\nmore'
    assert driver.parse_draft_caption(text) == dict(draft_id='abc123', revision=2, brief_sha256='a' * 64)
    assert driver.parse_draft_caption('Draft x | revision 1 | brief SHA-256 None') is None
    assert driver.parse_draft_caption('nothing') is None


# ---------------------------------------------------------------- call budget

def sample(calls, rows=1, initialized=True, statuses=('proposed',)):
    return dict(calls=calls, rows=rows, initialized=initialized, statuses=list(statuses))


def test_budget_passes_for_one_proposal_with_no_later_calls():
    assert driver.check_budget(dict(before=sample(0, 0, statuses=()), after_proposal=sample(1),
                                    after_run=sample(1), final=sample(1))) == []
    assert driver.check_budget(dict(before=sample(0, 0, statuses=()), after_proposal=sample(2),
                                    after_run=sample(2), final=sample(2))) == []


@pytest.mark.parametrize('samples,fragment', [
    (dict(before=sample(0, 0), after_proposal=sample(3), after_run=sample(3), final=sample(3)), 'proposal'),
    (dict(before=sample(0, 0), after_proposal=sample(1), after_run=sample(2), final=sample(2)), 'after the proposal'),
    (dict(before=sample(0, 0), after_proposal=sample(1), after_run=sample(1), final=sample(2)), 'after the proposal'),
    (dict(before=sample(1), after_proposal=sample(2), after_run=sample(2), final=sample(2)), 'before'),
    (dict(before=sample(0, 0), after_proposal=sample(1, rows=2), after_run=sample(1, rows=2), final=sample(1, rows=2)), 'one'),
    (dict(before=sample(0, 0), after_proposal=sample(1, initialized=False), after_run=sample(1), final=sample(1)), 'ledger'),
    (dict(before=sample(0, 0), after_proposal=sample(1), after_run=sample(0), final=sample(0)), 'decreas'),
])
def test_budget_violations_are_reported(samples, fragment):
    problems = driver.check_budget(samples)
    assert problems and any(fragment in p for p in problems), problems


def test_total_above_three_always_fails():
    problems = driver.check_budget(dict(before=sample(0, 0), after_proposal=sample(2),
                                        after_run=sample(4), final=sample(4)))
    assert any('3' in p for p in problems)


def test_missing_checkpoint_fails():
    assert driver.check_budget(dict(before=sample(0, 0), after_proposal=sample(1)))


def test_counter_script_reads_calls_only_and_is_read_only():
    script = driver.COUNTER_SCRIPT
    assert 'mode=ro' in script and 'SELECT record FROM audits' in script
    for forbidden in ('raw_reply', 'validation_failures', 'INSERT', 'UPDATE', 'DELETE', 'DROP'):
        assert forbidden not in script


def test_counter_script_output_shape(tmp_path):
    import sqlite3
    db_path = tmp_path / 'engineering.sqlite3'
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE audits(draft TEXT, revision INTEGER, record TEXT NOT NULL)')
        db.execute('INSERT INTO audits VALUES (?,?,?)',
                   ('d', 1, json.dumps(dict(calls=2, status='proposed', validation_failures=[dict(raw_reply='SECRET')]))))
    out = driver.read_counter_from(str(db_path))
    assert out == dict(initialized=True, calls=2, rows=1, statuses=['proposed'])
    assert driver.read_counter_from(str(tmp_path / 'missing')) == dict(initialized=False, calls=0, rows=0, statuses=[])


def test_classify_proposal():
    assert driver.classify_proposal(['proposed']) == 'proposed'
    assert driver.classify_proposal(['unavailable']) == 'unavailable'
    assert driver.classify_proposal(['clarification_required']) == 'incomplete'
    assert driver.classify_proposal(['unsupported']) == 'incomplete'
    assert driver.classify_proposal(['running']) == 'running'
    assert driver.classify_proposal([]) == 'running'
    assert driver.classify_proposal(['conflict']) == 'unavailable'


# ---------------------------------------------------------------- redaction

def test_redact_removes_every_secret_and_key_shape():
    token, key = 'f' * 64, 'AIza' + 'x' * 35
    text = 'token %s key %s header X-Mirum-Access-Token: %s' % (token, key, token)
    out = driver.redact(text, [token, key])
    assert token not in out and key not in out and 'AIza' not in out


def test_scrub_evidence_is_recursive_and_raises_if_secret_survives():
    token = 'e' * 64
    evidence = dict(a=[dict(b='x%sy' % token)], c=token)
    clean = driver.scrub(evidence, [token])
    assert token not in json.dumps(clean)
    with pytest.raises(ValueError):
        driver.assert_clean(json.dumps(evidence), [token])
    driver.assert_clean(json.dumps(clean), [token])


def test_evidence_never_holds_a_raw_reply_field():
    with pytest.raises(ValueError):
        driver.assert_no_raw_reply(dict(x=dict(raw_reply='r')))
    driver.assert_no_raw_reply(dict(x=dict(raw_reply_characters=3)))


def test_short_or_empty_secrets_are_ignored_by_redaction():
    assert driver.redact('abc', ['', 'a']) == 'abc'


# ---------------------------------------------------------------- L1: budget check at exit, on every path

def ledger(calls, rows=1, initialized=True):
    return dict(initialized=initialized, calls=calls, rows=rows, statuses=['proposed'] * rows)


def test_at_exit_unreadable_ledger_fails_closed():
    assert driver.check_at_exit({}, None)
    assert driver.check_at_exit({}, ledger(0, initialized=False))


def test_at_exit_before_the_proposal_allows_only_the_proposals_two_calls():
    assert driver.check_at_exit({}, ledger(0, rows=0)) == []
    assert driver.check_at_exit({}, ledger(2)) == []
    assert driver.check_at_exit({}, ledger(3))


def test_at_exit_after_the_proposal_allows_no_further_calls():
    samples = dict(after_proposal=ledger(1))
    assert driver.check_at_exit(samples, ledger(1)) == []
    assert driver.check_at_exit(samples, ledger(2))


def test_at_exit_two_proposal_rows_fail():
    assert driver.check_at_exit({}, ledger(1, rows=2))


def test_at_exit_runs_the_full_check_when_all_checkpoints_exist():
    samples = dict(before=ledger(0, rows=0), after_proposal=ledger(1), after_run=ledger(1), final=ledger(1))
    assert driver.check_at_exit(samples, ledger(1)) == []
    samples['after_run'] = ledger(2)
    assert driver.check_at_exit(samples, ledger(1))


# ---------------------------------------------------------------- M1: access log of un-ledgered provider routes

LOG = '''INFO:     172.18.0.1:5 - "GET /auth/check HTTP/1.1" 200 OK
INFO:     172.18.0.1:5 - "GET /engineering/v1/capabilities HTTP/1.1" 200 OK
INFO:     172.18.0.1:5 - "POST /engineering/v1/drafts HTTP/1.1" 200 OK
INFO:     172.18.0.1:5 - "GET /engineering/v1/jobs/0123456789abcdef0123456789abcdef HTTP/1.1" 200 OK
INFO:     172.18.0.1:5 - "GET /engineering/v1/jobs/*** HTTP/1.1" 200 OK
'''


def test_access_log_counts_normalise_ids_and_queries():
    counts = driver.count_paths(LOG + 'INFO: x - "GET /engineering/v1/drafts?x=1 HTTP/1.1" 200 OK\n')
    assert counts['/engineering/v1/jobs/{id}'] == 2
    assert counts['/engineering/v1/drafts'] == 2 and counts['/auth/check'] == 1


def test_clean_access_log_passes():
    assert driver.check_access_log(driver.count_paths(LOG)) == []


@pytest.mark.parametrize('route', ['/generate', '/dragsters/interpret', '/clarify', '/requirements', '/plan', '/refine',
                                   '/pmi/extract', '/pmi/annotate', '/cem/glider/requirements',
                                   '/some/future/route', '/engineering/v1x', '/engineering', '/auth/check/x'])
def test_any_route_off_the_allowlist_fails(route):
    bad = LOG + 'INFO: x - "POST %s HTTP/1.1" 200 OK\n' % route
    problems = driver.check_access_log(driver.count_paths(bad))
    assert len(problems) == 1 and route in problems[0]


def test_log_without_engineering_requests_proves_nothing():
    assert driver.check_access_log({}) and driver.check_access_log({'/auth/check': 1})


def test_main_evidence_error_text_carries_no_exception_message():
    source = Path('scripts/b13_step12_ui.py').read_text()
    assert "driver stopped (details withheld)" in source and "str(error)" not in source
