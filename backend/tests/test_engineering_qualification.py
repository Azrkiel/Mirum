"""Qualification tools enforce fixed corpus, literal credentials and budgets."""
import json
import hashlib
import pytest
from scripts.checked_engineering_live_eval import load_credential,CORPUS_SHA256

import asyncio
from pathlib import Path
from scripts import checked_engineering_live_eval as evaluation
from backend.tests.test_engineering_proposer import FixtureProvider


def corpus():
    return json.loads(Path('backend/tests/fixtures/engineering/ai-corpus-v1.json').read_bytes())['cases']


class EvaluationFixture(FixtureProvider):
    def __init__(self, outputs):
        super().__init__(outputs)
        self.failures = []


def run_evaluation(tmp_path, monkeypatch, provider, **kwargs):
    monkeypatch.setattr(evaluation, 'ObservedProvider', lambda: provider)
    return asyncio.run(evaluation.main(tmp_path/'output', tmp_path/'state', 'a'*40,
        run_id='offline-test', authorization='offline-fixtures-only',
        previous_run='mirum-checked-b13-live', image_id='sha256:'+'b'*64, **kwargs))


def result_file(tmp_path):
    return json.loads((tmp_path/'output'/'live.json').read_text())


def test_only_literal_existing_gemini_key_is_loaded(tmp_path,monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY_HEAVY',raising=False)
    monkeypatch.delenv('OTHER_SECRET',raising=False)
    path=tmp_path/'provider.env'
    path.write_text('OTHER_SECRET=private\nGEMINI_API_KEY="fixture-key.stage" # comment\nGEMINI_API_KEYS=ignored-pool\n')
    assert load_credential(path)=='GEMINI_API_KEY'
    import os
    assert os.environ['GEMINI_API_KEY_HEAVY']=='fixture-key.stage'
    assert 'OTHER_SECRET' not in os.environ and 'GEMINI_API_KEYS' not in os.environ
    path.write_text('GEMINI_API_KEY=$(do_not_execute)')
    with pytest.raises(ValueError): load_credential(path)


def test_live_corpus_identity_and_category_counts_are_fixed():
    from pathlib import Path
    from collections import Counter
    raw=Path('backend/tests/fixtures/engineering/ai-corpus-v1.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==CORPUS_SHA256
    assert Counter(c['kind'] for c in json.loads(raw)['cases'])==dict(complete=10,ambiguous=10,unsupported=10)


@pytest.mark.parametrize('altered', [False, True])
def test_complete_accuracy_uses_fixed_content_and_detects_silent_requirement_change(altered):
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.proposer import BriefProposer, MemoryProposalLedger
    case = corpus()[0]
    response = case['fixture_response']
    if altered: response['fields']['requirements']['tip_force_n'] += 1
    provider = EvaluationFixture([json.dumps(response)])
    service = BriefService(MemoryBriefRepository())
    draft = service.create({}).draft
    result = asyncio.run(BriefProposer(service, ledger=MemoryProposalLedger(), provider=provider).propose(
        draft.draft_id, expected_revision=1, prompt=case['prompt']))
    measured = evaluation.score(case, result)
    assert measured['accurate_reviewable_draft'] is (not altered)
    assert measured['silent_requirement_drop'] is altered


def test_full_offline_run_is_accounted_and_restart_never_repeats_calls(tmp_path, monkeypatch):
    provider = EvaluationFixture([json.dumps(c['fixture_response']) for c in corpus()])
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    first = result_file(tmp_path)
    assert first['calls'] == 30 and len(first['cases']) == 30
    assert first['complete_accurate'] == 10 and first['unreviewed_executions'] == 0
    assert first['settings']['provider'] == 'fixture'  # explicitly offline evidence
    assert all(c['elapsed_seconds'] >= 0 for c in first['cases'])
    assert all(c['provider_failures'] == [] for c in first['cases'])
    assert all(c['review_blocked'] for c in first['cases'] if c['kind'] != 'complete')
    assert all('fixture_response' not in call[0] for call in provider.calls)
    assert all(call[0].endswith(json.dumps(case['prompt'])) for call, case in zip(provider.calls, corpus()))
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    assert len(provider.calls) == 30 and result_file(tmp_path)['calls'] == 30


def test_failures_belong_only_to_current_case_and_supported_failure_is_explicit(tmp_path, monkeypatch):
    from backend.tests.test_engineering_contracts import review_for
    outputs = [RuntimeError('private failure')] + [json.dumps(c['fixture_response']) for c in corpus()[1:]]
    class Observed(EvaluationFixture):
        async def generate(self, *args):
            try: return await super().generate(*args)
            except RuntimeError:
                self.failures.append(dict(exception='RuntimeError', tls_certificate_failure=False))
                raise
    provider = Observed(outputs)
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    report = result_file(tmp_path)
    assert report['cases'][0]['state'] == 'unavailable'
    assert report['cases'][0]['provider_failures']
    assert not any(c['provider_failures'] for c in report['cases'][1:])
    from engineering.store import SQLiteStore, SQLiteBriefRepository
    from engineering.briefs import BriefService
    store = SQLiteStore(tmp_path/'state')
    try:
        service = BriefService(SQLiteBriefRepository(store))
        case = report['cases'][0]
        prepared = service.edit(case['audit']['draft_id'], expected_revision=1, payload=corpus()[0]['expected_content'])
        assert service.finalize(review_for(prepared.draft))
    finally: store.close()


def test_outer_deadline_stops_shared_blocker_and_records_safe_classification(tmp_path, monkeypatch):
    from engineering.proposer import BriefProposer
    class Slow(EvaluationFixture):
        async def generate(self, *args):
            self.calls.append(args)
            await asyncio.sleep(30)
    provider = Slow([])
    monkeypatch.setattr(evaluation, 'BriefProposer', lambda *a, **k: BriefProposer(*a, **k, call_seconds=0.05))
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    report = result_file(tmp_path)
    # Adapted 2026-11-17 (owner Q1 b): three deadline cases continue; the fourth stops the run.
    assert report['calls'] == 4 and len(provider.calls) == 4
    assert report['cases'][0]['error_classification'] == 'preparation_deadline'
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    assert len(provider.calls) == 4 and result_file(tmp_path)['calls'] == 4


def test_interrupted_call_is_counted_and_cannot_repeat_after_restart(tmp_path, monkeypatch):
    class Interrupted(EvaluationFixture):
        async def generate(self, *args):
            self.calls.append(args)
            raise asyncio.CancelledError()
    provider = Interrupted([])
    with pytest.raises(asyncio.CancelledError): run_evaluation(tmp_path, monkeypatch, provider)
    assert result_file(tmp_path)['calls'] == 1
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    assert result_file(tmp_path)['calls'] == 1 and len(provider.calls) == 1
    assert 'Interrupted claimed case' in result_file(tmp_path)['blocker']


def test_native_guard_is_reported_and_restored(tmp_path, monkeypatch):
    from process_runner import ProcessRunner
    original = ProcessRunner.run
    class Native(EvaluationFixture):
        async def generate(self, *args):
            self.calls.append(args)
            await ProcessRunner().run('forbidden')
    provider = Native([])
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    report = result_file(tmp_path)
    assert report['unreviewed_executions'] == 1 and report['calls'] == 1
    assert ProcessRunner.run is original
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    assert len(provider.calls) == 1 and result_file(tmp_path)['unreviewed_executions'] == 1


def test_exhausted_durable_budget_stops_before_provider(tmp_path, monkeypatch):
    provider = EvaluationFixture([json.dumps(c['fixture_response']) for c in corpus()])
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    import sqlite3
    with sqlite3.connect(tmp_path/'state'/'engineering.sqlite3') as db:
        for draft, revision, raw in db.execute('SELECT * FROM audits').fetchall():
            record = json.loads(raw); record['calls'] = 2
            db.execute('UPDATE audits SET record=? WHERE draft=? AND revision=?', (json.dumps(record), draft, revision))
        last = db.execute('SELECT draft FROM v15_cases WHERE id=?', (corpus()[-1]['id'],)).fetchone()[0]
        db.execute('UPDATE v15_cases SET result=NULL WHERE draft=?', (last,))
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    assert len(provider.calls) == 30 and result_file(tmp_path)['calls'] == 60
    assert 'budget' in result_file(tmp_path)['blocker'].lower()


@pytest.mark.parametrize('changed', ['sha', 'image_id', 'run_id', 'settings'])
def test_resume_rejects_changed_candidate_without_new_calls(tmp_path, monkeypatch, changed):
    provider = EvaluationFixture([json.dumps(c['fixture_response']) for c in corpus()])
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    candidate = dict(sha='a'*40, run_id='offline-test', authorization='offline-fixtures-only',
        previous_run='mirum-checked-b13-live', image_id='sha256:'+'b'*64)
    if changed == 'settings':
        settings = provider.settings().model_copy(update={'temperature': 0.1})
        monkeypatch.setattr(provider, 'settings', lambda: settings)
    else: candidate[changed] = {'sha':'c'*40, 'image_id':'sha256:'+'c'*64, 'run_id':'changed-run'}[changed]
    with pytest.raises(ValueError, match='identity'):
        asyncio.run(evaluation.main(tmp_path/'output', tmp_path/'state', **candidate))
    assert len(provider.calls) == 30


def test_historical_ledger_cannot_be_relabelled_as_fresh_run(tmp_path, monkeypatch):
    provider = EvaluationFixture([json.dumps(c['fixture_response']) for c in corpus()])
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    import sqlite3
    with sqlite3.connect(tmp_path/'state'/'engineering.sqlite3') as db:
        db.execute("DELETE FROM metadata WHERE key='v15_identity'")
    with pytest.raises(ValueError, match='Historical evaluation'):
        run_evaluation(tmp_path, monkeypatch, provider)
    assert len(provider.calls) == 30


def test_shared_blocker_on_last_case_cannot_qualify(tmp_path, monkeypatch):
    class LastBlocked(EvaluationFixture):
        async def generate(self, *args):
            if len(self.calls) == 29:
                self.calls.append(args)
                self.failures.append(dict(exception='DeadlineExceeded', tls_certificate_failure=False))
                raise RuntimeError('private failure')
            return await super().generate(*args)
    provider = LastBlocked([json.dumps(c['fixture_response']) for c in corpus()])
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    report = result_file(tmp_path)
    assert len(report['cases']) == 30 and report['complete_accurate'] == 10
    assert report['qualification'] == 'blocked'


def test_evidence_keeps_validation_locations_but_never_raw_replies(tmp_path, monkeypatch):
    marker = 'RAW-REPLY-MARKER-must-stay-in-ledger'
    outputs = ['{"not valid json ' + marker] + [json.dumps(c['fixture_response']) for c in corpus()]
    provider = EvaluationFixture(outputs)
    run_evaluation(tmp_path, monkeypatch, provider)
    text = (tmp_path/'output'/'live.json').read_text()
    assert marker not in text
    failures = result_file(tmp_path)['cases'][0]['audit']['validation_failures']
    assert failures and failures[0]['errors'][0]['type'] == 'json_invalid'
    assert 'raw_reply' not in failures[0] and failures[0]['raw_reply_characters'] > 0
    from engineering.store import SQLiteStore, SQLiteProposalLedger
    store = SQLiteStore(tmp_path/'state')
    try:
        audit = SQLiteProposalLedger(store).read(result_file(tmp_path)['cases'][0]['audit']['draft_id'], 1)
        assert marker in audit.validation_failures[0].raw_reply  # ledger keeps it for local diagnosis
    finally: store.close()


def test_live_diagnostic_is_bounded_safe_and_never_repeats(tmp_path, monkeypatch):
    from scripts import checked_engineering_live_diagnostic as diagnostic
    marker = 'RAW-REPLY-MARKER-diagnostic'
    case = corpus()[0]
    provider = EvaluationFixture(['{"broken ' + marker, json.dumps(case['fixture_response'])])
    monkeypatch.setattr(diagnostic, 'ObservedProvider', lambda: provider)
    run = lambda: asyncio.run(diagnostic.main(tmp_path/'out', tmp_path/'state', case_id=case['id'],
                                              authorization='offline-fixtures-only'))
    assert run() == 0
    text = (tmp_path/'out'/'diagnostic.json').read_text()
    assert marker not in text
    report = json.loads(text)
    assert report['brief_matches_expected'] and report['audit']['calls'] == 2
    assert report['native_execution_attempts'] == 0
    assert report['audit']['validation_failures'][0]['errors'][0]['type'] == 'json_invalid'
    with pytest.raises(ValueError, match='never repeated'):
        run()
    assert len(provider.calls) == 2


def test_pacing_pauses_between_cases_without_changing_calls_or_scoring(tmp_path, monkeypatch):
    """B13 2026-11-17: a live run hit HTTP 429 at case 16. A fixed pause between
    cases respects the key's rate limit; it never retries and never changes scoring."""
    pauses = []
    async def fake_sleep(seconds):
        pauses.append(seconds)
    monkeypatch.setattr(evaluation.asyncio, 'sleep', fake_sleep)
    provider = EvaluationFixture([json.dumps(c['fixture_response']) for c in corpus()])
    assert run_evaluation(tmp_path, monkeypatch, provider, pace_seconds=12) == 0
    report = result_file(tmp_path)
    assert report['calls'] == 30 and report['complete_accurate'] == 10
    assert report['pace_seconds'] == 12
    assert pauses == [12] * 29  # between cases only, never before the first


def test_pacing_rejects_negative_or_huge_values(tmp_path, monkeypatch):
    provider = EvaluationFixture([])
    for bad in (-1, 121):
        with pytest.raises(ValueError):
            run_evaluation(tmp_path, monkeypatch, provider, pace_seconds=bad)


# --- Owner Q1(b), 2026-11-17: 429/503 mark one case unavailable and the run continues ---

def failure_record(exception, code, **flags):
    record = dict(exception=exception, code=code, tls_certificate_failure=False, credential_rejected=False,
                  model_unavailable=False, schema_rejected=False, incomplete_response=False,
                  provider_error_masked=False)
    record.update(flags)
    return record


class Scripted(EvaluationFixture):
    """Fails or hangs on chosen call indexes. Outputs stay aligned to call index (one call per case)."""
    def __init__(self, failures=None, hangs=()):
        super().__init__([json.dumps(c['fixture_response']) for c in corpus()])
        self.scripted = dict(failures or {})
        self.hangs = set(hangs)

    async def generate(self, *args):
        index = len(self.calls)
        if index in self.hangs:
            self.calls.append(args); self.outputs.pop(0)
            await asyncio.Event().wait()
        if index in self.scripted:
            self.calls.append(args); self.outputs.pop(0)
            self.failures.append(self.scripted[index])
            raise type(self.scripted[index]['exception'], (Exception,), {})('private failure')
        return await super().generate(*args)


def indexes(kind):
    return [i for i, c in enumerate(corpus()) if c['kind'] == kind]


def fast_deadline(monkeypatch):
    from engineering.proposer import BriefProposer
    monkeypatch.setattr(evaluation, 'BriefProposer', lambda *a, **k: BriefProposer(*a, **k, call_seconds=0.05))


def test_429_on_ambiguous_case_marks_it_unavailable_and_run_continues(tmp_path, monkeypatch):
    bad = indexes('ambiguous')[3]
    provider = Scripted({bad: failure_record('ClientError', '429')})
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    report = result_file(tmp_path)
    assert len(report['cases']) == 30 and 'blocker' not in report and report['qualification'] == 'passed'
    case = report['cases'][bad]
    assert case['provider_unavailable'] is True and case['unavailable_reason'] == 'http_429'
    assert case['state'] == 'unavailable' and case['audit']['calls'] == 1 and 'stop_reason' not in case
    assert case['accurate_reviewable_draft'] is False and case['silent_requirement_drop'] is False
    assert len(provider.calls) == 30 and report['calls'] == 30  # no retry, no extra call
    assert report['provider_unavailable_cases'] == 1 and report['provider_unavailable_case_ids'] == [corpus()[bad]['id']]
    assert all(c['provider_unavailable'] is False and c['unavailable_reason'] is None
               for i, c in enumerate(report['cases']) if i != bad)


def test_503_on_complete_case_is_unavailable_not_accurate_and_still_qualifies(tmp_path, monkeypatch):
    bad = indexes('complete')[0]
    provider = Scripted({bad: failure_record('ServerError', '503')})
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    report = result_file(tmp_path)
    case = report['cases'][bad]
    assert case['provider_unavailable'] is True and case['unavailable_reason'] == 'http_503'
    assert case['accurate_reviewable_draft'] is False and case['review_blocked'] is True
    assert report['complete_accurate'] == 9 and report['qualification'] == 'passed'
    assert len(report['cases']) == 30 and len(provider.calls) == 30 and report['calls'] == 30


def test_504_on_complete_case_is_unavailable_and_run_continues(tmp_path, monkeypatch):
    bad = indexes('complete')[0]
    provider = Scripted({bad: failure_record('ServerError', '504')})
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    report = result_file(tmp_path)
    case = report['cases'][bad]
    assert case['provider_unavailable'] is True and case['unavailable_reason'] == 'http_504'
    assert case['accurate_reviewable_draft'] is False
    assert len(report['cases']) == 30 and len(provider.calls) == 30 and report['calls'] == 30
    assert report['provider_unavailable_cases'] == 1 and report['provider_unavailable_case_ids'] == [case['case_id']]


def test_policy_v2_digest_differs_from_v1_table():
    import hashlib
    v1 = json.dumps([sorted([list(i) for i in {('ClientError', '429'), ('ServerError', '503')}]),
                     evaluation.MAXIMUM_DEADLINE_CASES, list(evaluation.STOP_FLAGS)],
                    sort_keys=True, separators=(',', ':'))
    assert evaluation.policy_digest() != hashlib.sha256(v1.encode()).hexdigest()
    assert ('ServerError', '504') in evaluation.TRANSIENT_PROVIDER_FAILURES


def test_429_on_three_complete_cases_fails_the_unchanged_accuracy_gate(tmp_path, monkeypatch):
    bad = indexes('complete')[:3]
    provider = Scripted({i: failure_record('ClientError', '429') for i in bad})
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    report = result_file(tmp_path)
    assert len(report['cases']) == 30 and report['complete_accurate'] == 7
    assert report['qualification'] == 'blocked' and 'accuracy' in report['blocker']
    assert report['provider_unavailable_cases'] == 3 and len(provider.calls) == 30


def test_three_deadline_cases_continue_and_a_fourth_stops(tmp_path, monkeypatch):
    fast_deadline(monkeypatch)
    ambiguous = indexes('ambiguous')
    provider = Scripted(hangs=ambiguous[:3])
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    report = result_file(tmp_path)
    assert len(report['cases']) == 30 and report['deadline_cases'] == 3 and report['qualification'] == 'passed'
    for i in ambiguous[:3]:
        assert report['cases'][i]['unavailable_reason'] == 'preparation_deadline'
        assert report['cases'][i]['provider_unavailable'] is True
    tmp_path2 = tmp_path/'four'; tmp_path2.mkdir()
    provider = Scripted(hangs=ambiguous[:4])
    assert run_evaluation(tmp_path2, monkeypatch, provider) == 2
    report = result_file(tmp_path2)
    assert len(report['cases']) == ambiguous[3] + 1 and len(provider.calls) == ambiguous[3] + 1
    assert report['cases'][-1]['stop_reason'] and 'preparation_deadline' in report['blocker']
    assert report['deadline_cases'] == 4 and report['qualification'] == 'blocked'


def test_deadlines_before_a_restart_count_and_unavailable_cases_are_never_recalled(tmp_path, monkeypatch):
    fast_deadline(monkeypatch)
    ambiguous = indexes('ambiguous')
    interrupt_at = ambiguous[3]
    provider = Scripted({ambiguous[4]: failure_record('ClientError', '429')}, hangs=ambiguous[:3] + [interrupt_at])
    sleeps = []
    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == interrupt_at: raise asyncio.CancelledError()  # simulated crash before the call
    real_sleep = evaluation.asyncio.sleep
    monkeypatch.setattr(evaluation.asyncio, 'sleep', fake_sleep)
    with pytest.raises(asyncio.CancelledError): run_evaluation(tmp_path, monkeypatch, provider, pace_seconds=1)
    assert len(provider.calls) == interrupt_at  # three deadlines recorded, fourth not yet called
    monkeypatch.setattr(evaluation.asyncio, 'sleep', real_sleep)
    assert run_evaluation(tmp_path, monkeypatch, provider, pace_seconds=1) == 2
    report = result_file(tmp_path)
    assert len(provider.calls) == interrupt_at + 1  # only the fourth deadline case was called; replays were free
    assert report['cases'][-1]['stop_reason'] and report['deadline_cases'] == 4
    assert run_evaluation(tmp_path, monkeypatch, provider, pace_seconds=1) == 2
    assert len(provider.calls) == interrupt_at + 1


def test_unavailable_case_is_not_called_again_on_resume(tmp_path, monkeypatch):
    provider = Scripted({indexes('ambiguous')[0]: failure_record('ServerError', '503')})
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    assert len(provider.calls) == 30 and result_file(tmp_path)['provider_unavailable_cases'] == 1


@pytest.mark.parametrize('record', [
    failure_record('ClientError', '401', credential_rejected=True),
    failure_record('ClientError', '429', credential_rejected=True),
    failure_record('ClientError', '404', model_unavailable=True),
    failure_record('ServerError', '503', model_unavailable=True),
    failure_record('ConnectError', None, tls_certificate_failure=True),
    failure_record('ClientError', '429', tls_certificate_failure=True),
])
def test_credential_model_and_tls_failures_stop_the_run(tmp_path, monkeypatch, record):
    provider = Scripted({0: record})
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    report = result_file(tmp_path)
    assert len(report['cases']) == 1 and len(provider.calls) == 1
    assert report['cases'][0]['stop_reason'].startswith('Configured provider access/runtime failed')
    assert report['cases'][0]['provider_unavailable'] is False and report['provider_unavailable_cases'] == 0


@pytest.mark.parametrize('record', [
    failure_record('ClientError', '400'),
    failure_record('ClientError', '403'),
    failure_record('ServerError', '500'),
    failure_record('ServerError', '502'),
    failure_record('ClientError', None),
])
def test_other_provider_failures_still_stop_the_run(tmp_path, monkeypatch, record):
    provider = Scripted({0: record})
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    report = result_file(tmp_path)
    assert len(report['cases']) == 1 and len(provider.calls) == 1
    assert report['cases'][0]['stop_reason'] and report['cases'][0]['provider_unavailable'] is False


def test_evidence_and_identity_carry_the_failure_policy(tmp_path, monkeypatch):
    provider = Scripted()
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    report = result_file(tmp_path)
    for holder in (report, report['candidate']):
        assert holder['provider_failure_policy'] == 'b13-unavailable-429-503-504-v2'
        assert holder['maximum_deadline_cases'] == 3
    assert report['provider_unavailable_cases'] == 0 and report['provider_unavailable_case_ids'] == []
    assert report['deadline_cases'] == 0
    import sqlite3
    with sqlite3.connect(tmp_path/'state'/'engineering.sqlite3') as db:
        raw = db.execute("SELECT value FROM metadata WHERE key='v15_identity'").fetchone()[0]
        old = json.loads(raw)
        del old['provider_failure_policy'], old['maximum_deadline_cases']
        db.execute("UPDATE metadata SET value=? WHERE key='v15_identity'", (json.dumps(old, sort_keys=True),))
    with pytest.raises(ValueError, match='identity'):
        run_evaluation(tmp_path, monkeypatch, provider)
    assert len(provider.calls) == 30


# --- Security review fix round (PR #63): masked errors, real exceptions, policy digest ---

def test_masked_provider_error_stops_but_incomplete_candidate_runtime_error_continues(tmp_path, monkeypatch):
    # The proposer replaces any key-echoing provider error with this exact RuntimeError, which hides
    # 429/credential/model class; the run cannot trust it, so it stops.
    provider = Scripted({0: failure_record('RuntimeError', None, provider_error_masked=True)})
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    report = result_file(tmp_path)
    assert len(report['cases']) == 1 and len(provider.calls) == 1
    assert report['cases'][0]['stop_reason'].startswith('Configured provider access/runtime failed')
    assert report['cases'][0]['provider_unavailable'] is False
    # Other RuntimeErrors (model output problems) keep today's behaviour: blocked case, run continues.
    tmp_path2 = tmp_path/'incomplete'; tmp_path2.mkdir()
    provider = Scripted({indexes('ambiguous')[0]: failure_record('RuntimeError', None)})
    assert run_evaluation(tmp_path2, monkeypatch, provider) == 0
    report = result_file(tmp_path2)
    assert len(report['cases']) == 30 and report['provider_unavailable_cases'] == 0


def standin(name, code):
    error = type(name, (Exception,), {})('private failure')
    error.code = code
    return error


def observed_failure(monkeypatch, error):
    async def stub(self, *args, **kwargs):
        raise error
    monkeypatch.setattr(evaluation.GeminiProposalProvider, 'generate', stub)
    provider = evaluation.ObservedProvider()
    with pytest.raises(type(error)):
        asyncio.run(provider.generate('prompt', {}, None))
    assert len(provider.failures) == 1
    return provider.failures[0]


@pytest.mark.parametrize('name,code', [('ClientError', 429), ('ServerError', 503), ('ClientError', 400)])
def test_observed_provider_records_exception_name_and_code_string(monkeypatch, name, code):
    record = observed_failure(monkeypatch, standin(name, code))
    assert record['exception'] == name and record['code'] == str(code)
    assert record['provider_error_masked'] is False


@pytest.mark.parametrize('name,code', [('ClientError', 429), ('ServerError', 503)])
def test_observed_provider_records_real_genai_errors(monkeypatch, name, code):
    errors = pytest.importorskip('google.genai.errors')
    error = getattr(errors, name)(code, {'error': {'code': code, 'message': 'quota', 'status': 'RESOURCE_EXHAUSTED'}})
    record = observed_failure(monkeypatch, error)
    assert record['exception'] == name and record['code'] == str(code)


def test_observed_provider_flags_the_masked_key_echo_error(monkeypatch):
    record = observed_failure(monkeypatch, RuntimeError('AI preparation provider call failed'))
    assert record['exception'] == 'RuntimeError' and record['provider_error_masked'] is True
    record = observed_failure(monkeypatch, RuntimeError('AI preparation did not return a complete candidate'))
    assert record['provider_error_masked'] is False and record['incomplete_response'] is True


def end_to_end_provider(monkeypatch, bad, error):
    from backend.tests.test_engineering_proposer import FixtureProvider
    from engineering.proposer import ProviderReply
    class Real(evaluation.ObservedProvider):
        available = True
        settings = FixtureProvider.settings
        def __init__(self):
            super().__init__()
            self.calls = []
            self.outputs = [json.dumps(c['fixture_response']) for c in corpus()]
    async def stub(self, *args, **kwargs):
        index = len(self.calls); self.calls.append(args)
        output = self.outputs.pop(0)
        if index == bad: raise error
        return ProviderReply(text=output, prompt_tokens=10, output_tokens=20)
    monkeypatch.setattr(evaluation.GeminiProposalProvider, 'generate', stub)
    return Real()


@pytest.mark.parametrize('real', [False, True])
def test_real_exception_429_flows_through_observed_provider_and_run_continues(tmp_path, monkeypatch, real):
    if real:
        errors = pytest.importorskip('google.genai.errors')
        error = errors.ClientError(429, {'error': {'code': 429, 'message': 'quota', 'status': 'RESOURCE_EXHAUSTED'}})
    else:
        error = standin('ClientError', 429)
    bad = indexes('ambiguous')[2]
    provider = end_to_end_provider(monkeypatch, bad, error)
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    report = result_file(tmp_path)
    case = report['cases'][bad]
    assert case['provider_unavailable'] is True and case['unavailable_reason'] == 'http_429'
    assert case['provider_failures'][0]['exception'] == 'ClientError' and case['provider_failures'][0]['code'] == '429'
    assert len(report['cases']) == 30 and len(provider.calls) == 30 and 'blocker' not in report


def test_policy_digest_is_in_identity_and_changing_the_table_changes_it(tmp_path, monkeypatch):
    provider = Scripted()
    assert run_evaluation(tmp_path, monkeypatch, provider) == 0
    digest = result_file(tmp_path)['candidate']['provider_failure_policy_sha256']
    assert len(digest) == 64 and int(digest, 16) >= 0
    monkeypatch.setattr(evaluation, 'TRANSIENT_PROVIDER_FAILURES',
                        evaluation.TRANSIENT_PROVIDER_FAILURES | {('ServerError', '500')})
    with pytest.raises(ValueError, match='identity'):
        run_evaluation(tmp_path, monkeypatch, provider)
    assert len(provider.calls) == 30
