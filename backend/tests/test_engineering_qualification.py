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
    assert report['calls'] == 1 and len(provider.calls) == 1
    assert report['cases'][0]['error_classification'] == 'preparation_deadline'
    assert run_evaluation(tmp_path, monkeypatch, provider) == 2
    assert len(provider.calls) == 1 and result_file(tmp_path)['calls'] == 1


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
