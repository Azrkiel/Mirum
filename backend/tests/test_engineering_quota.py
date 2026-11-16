"""Draft/audit/brief quotas, short draft expiry, separate accounting, and job fairness."""
import asyncio

import pytest

from backend.tests.test_engineering_contracts import data, review_for
from engineering.admission import AdmissionBusy
from engineering.briefs import BriefService
from engineering.store import ArtifactExpired, PreparationQuota, SQLiteBriefRepository, SQLiteStore


def make(tmp_path, **limits):
    store = SQLiteStore(tmp_path)
    settings = dict(ttl_seconds=3600, max_drafts=100, max_draft_bytes=10**9, per_client_drafts=100,
                    max_audits=100, max_audit_bytes=10**9, max_briefs=100, per_client_briefs=100)
    settings.update(limits)
    quota = PreparationQuota(store, **settings)
    return store, quota, BriefService(SQLiteBriefRepository(store))


def new_draft(service, quota, client, payload=None):
    quota.admit_draft(client)
    prepared = service.create(payload if payload is not None else {})
    quota.record_draft(prepared.draft.draft_id, client, len(prepared.model_dump_json()))
    return prepared


def test_per_client_and_global_draft_caps(tmp_path):
    store, quota, service = make(tmp_path, per_client_drafts=2, max_drafts=3)
    new_draft(service, quota, 'a'); new_draft(service, quota, 'a')
    with pytest.raises(AdmissionBusy):
        quota.admit_draft('a')
    new_draft(service, quota, 'b')           # a different client is unaffected...
    with pytest.raises(AdmissionBusy):
        quota.admit_draft('c')               # ...until the global cap is reached
    store.close()


def test_global_draft_byte_cap(tmp_path):
    store, quota, service = make(tmp_path, max_draft_bytes=1)
    new_draft(service, quota, 'a')           # its record is larger than 1 byte
    with pytest.raises(AdmissionBusy):
        quota.admit_draft('b')
    store.close()


def test_finalized_drafts_leave_the_unfinalized_quota(tmp_path):
    store, quota, service = make(tmp_path, per_client_drafts=1)
    prepared = new_draft(service, quota, 'a', data()['content'])
    with pytest.raises(AdmissionBusy):
        quota.admit_draft('a')
    service.finalize(review_for(prepared.draft))
    quota.admit_draft('a')                   # finalized work no longer holds the draft cap
    store.close()


def test_unfinalized_drafts_expire_after_the_short_ttl_and_free_quota(tmp_path):
    import time
    store, quota, service = make(tmp_path, per_client_drafts=1, ttl_seconds=3600)
    prepared = new_draft(service, quota, 'a')
    draft_id = prepared.draft.draft_id
    quota.expire(now=time.time() + 3599)
    assert service.read(draft_id)            # still live just inside the TTL
    quota.expire(now=time.time() + 3601)
    with pytest.raises(ArtifactExpired):
        service.read(draft_id)
    quota.admit_draft('a')                   # quota released
    store.close()


def test_edit_refreshes_the_ttl(tmp_path):
    import time
    store, quota, service = make(tmp_path, ttl_seconds=3600)
    prepared = new_draft(service, quota, 'a')
    edited = service.edit(prepared.draft.draft_id, expected_revision=1, payload=data()['content'])
    quota.touch(prepared.draft.draft_id, len(edited.model_dump_json()))
    quota.expire(now=time.time() + 3000)
    assert service.read(prepared.draft.draft_id)
    store.close()


def test_draft_with_active_ai_preparation_is_not_expired(tmp_path):
    import time
    store, quota, service = make(tmp_path, ttl_seconds=60)
    prepared = new_draft(service, quota, 'a')
    service.begin_proposal(prepared.draft.draft_id, 1, 'owner')
    quota.expire(now=time.time() + 120)
    assert service.read(prepared.draft.draft_id)
    store.close()


def test_audit_storage_is_capped_separately_and_only_for_ai(tmp_path):
    store, quota, service = make(tmp_path, max_audit_bytes=50)
    with store.transaction() as db:
        db.execute('INSERT INTO audits VALUES (?,?,?)', ('d', 1, 'x' * 100))
    quota.admit_draft('a')                   # typed drafts are unaffected by audit storage
    with pytest.raises(AdmissionBusy):
        quota.admit_draft('a', ai=True)
    store.close()


def test_draft_and_audit_bytes_never_count_against_job_artifact_storage(tmp_path):
    store, quota, service = make(tmp_path)
    before = store.storage_bytes()
    for _ in range(20):
        new_draft(service, quota, 'a', data()['content'])
    with store.transaction() as db:
        db.execute('INSERT INTO audits VALUES (?,?,?)', ('d', 1, 'x' * 100000))
    assert store.metadata_bytes() > 0
    assert store.storage_bytes() == before == 0
    store.close()


def test_brief_caps_global_and_per_client(tmp_path):
    store, quota, service = make(tmp_path, per_client_briefs=1, max_briefs=2)
    first = new_draft(service, quota, 'a', data()['content'])
    quota.admit_brief(first.draft.draft_id)
    service.finalize(review_for(first.draft))
    second = new_draft(service, quota, 'a', data()['content'])
    with pytest.raises(AdmissionBusy):
        quota.admit_brief(second.draft.draft_id)
    other = new_draft(service, quota, 'b', data()['content'])
    quota.admit_brief(other.draft.draft_id)
    service.finalize(review_for(other.draft))
    third = new_draft(service, quota, 'c', data()['content'])
    with pytest.raises(AdmissionBusy):
        quota.admit_brief(third.draft.draft_id)  # global cap
    store.close()


# --- job fairness and client-scoped idempotency (native search is cancelled immediately) ---

def controller(tmp_path, fairness):
    from engineering.jobs import JobController
    store = SQLiteStore(tmp_path)
    service = BriefService(SQLiteBriefRepository(store))
    brief = service.finalize(review_for(service.create(data()['content']).draft))
    return store, brief, JobController(store, fairness=fairness)


def test_idempotency_keys_are_scoped_to_the_client(tmp_path):
    from engineering.admission import ClientFairness
    async def run():
        store, brief, jobs = controller(tmp_path, ClientFairness.unlimited())
        alice = jobs.submit(brief.brief_id, 'shared-key', 'alice')
        assert jobs.submit(brief.brief_id, 'shared-key', 'alice').job_id == alice.job_id
        jobs.cancel(alice.job_id); await jobs.wait(alice.job_id)
        bob = jobs.submit(brief.brief_id, 'shared-key', 'bob')   # same text, different client: new job
        assert bob.job_id != alice.job_id
        jobs.cancel(bob.job_id); await jobs.wait(bob.job_id)
        assert jobs.submit(brief.brief_id, 'shared-key', 'alice').job_id == alice.job_id
        await jobs.close(); store.close()
    asyncio.run(run())


def test_hourly_job_quota_and_turn_rule_share_the_single_slot_fairly(tmp_path):
    from engineering.admission import ClientFairness, ClientQuotaExceeded
    async def run():
        fairness = ClientFairness(per_hour=2, memory_seconds=120)
        store, brief, jobs = controller(tmp_path, fairness)
        first = jobs.submit(brief.brief_id, 'k1', 'alice')
        with pytest.raises(AdmissionBusy) as busy:               # bob refused while alice runs
            jobs.submit(brief.brief_id, 'k2', 'bob')
        assert not isinstance(busy.value, ClientQuotaExceeded)
        jobs.cancel(first.job_id); await jobs.wait(first.job_id)
        with pytest.raises(ClientQuotaExceeded):                 # slot free, but bob has been waiting
            jobs.submit(brief.brief_id, 'k3', 'alice')
        second = jobs.submit(brief.brief_id, 'k4', 'bob')        # bob takes his turn
        jobs.cancel(second.job_id); await jobs.wait(second.job_id)
        third = jobs.submit(brief.brief_id, 'k5', 'alice')       # nobody waiting now
        jobs.cancel(third.job_id); await jobs.wait(third.job_id)
        with pytest.raises(ClientQuotaExceeded):                 # alice used her 2 hourly admissions
            jobs.submit(brief.brief_id, 'k6', 'alice')
        await jobs.close(); store.close()
    asyncio.run(run())
