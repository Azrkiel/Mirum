"""Durable integrated repositories, with actual SQLite transactions."""
from concurrent.futures import ThreadPoolExecutor
import pytest

from backend.tests.test_engineering_contracts import data, review_for
from engineering.briefs import BriefService, RevisionConflict


def setup(tmp_path):
    from engineering.store import SQLiteStore, SQLiteBriefRepository, SQLiteProposalLedger
    store = SQLiteStore(tmp_path)
    return store, BriefService(SQLiteBriefRepository(store)), SQLiteProposalLedger(store)


def test_durable_cas_immutable_reviews_and_owner_reservation(tmp_path):
    store, svc, _ = setup(tmp_path)
    first = svc.create(data()['content'])
    key = first.draft.draft_id
    svc.begin_proposal(key, 1, 'owner')
    with pytest.raises(RevisionConflict):
        svc.finalize(review_for(first.draft))
    with pytest.raises(RevisionConflict):
        svc.end_proposal(key, 1, 'other')
    svc.end_proposal(key, 1, 'owner')
    review = review_for(first.draft)
    brief = svc.finalize(review)
    store.close()
    store, svc, _ = setup(tmp_path)
    assert svc.finalize(review) == brief
    assert svc.read_brief(brief.brief_id) == brief
    def edit(_):
        try:
            return svc.edit(key, expected_revision=1, payload=data()['content'])
        except RevisionConflict:
            return None
    with ThreadPoolExecutor(2) as pool:
        assert sum(x is not None for x in pool.map(edit, range(2))) == 1
    with pytest.raises(RevisionConflict):
        svc.finalize(review)
    assert svc.read_brief(brief.brief_id) == brief
    store.close()


def test_restart_releases_abandoned_owner_but_never_call_budget(tmp_path):
    from engineering.proposer import ProposalAudit, ProviderSettings
    store, svc, ledger = setup(tmp_path)
    first = svc.create(data()['content'])
    key = first.draft.draft_id
    audit = ProposalAudit(draft_id=key, draft_revision=1,
        settings=ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture', temperature=0., max_tokens=1024),
        status='running', calls=2, request_characters=10, request_sha256='0'*64)
    ledger.claim(audit)
    svc.begin_proposal(key, 1, 'abandoned')
    store.close()
    store, svc, ledger = setup(tmp_path)
    store.recover()
    assert ledger.read(key, 1).calls == 2
    with pytest.raises(RevisionConflict):
        ledger.claim(audit)
    with pytest.raises(RevisionConflict):
        ledger.save(ProposalAudit.model_validate(dict(audit.model_dump(), calls=0)))
    assert svc.finalize(review_for(first.draft)).draft_revision == 1
    store.close()


def test_reject_second_process_volume_owner_and_configuration(tmp_path, monkeypatch):
    from engineering.store import SQLiteStore
    first = SQLiteStore(tmp_path)
    with pytest.raises(RuntimeError, match='owner'):
        SQLiteStore(tmp_path)
    first.close()
    monkeypatch.setenv('WEB_CONCURRENCY', '2')
    with pytest.raises(RuntimeError, match='single'):
        SQLiteStore(tmp_path)
