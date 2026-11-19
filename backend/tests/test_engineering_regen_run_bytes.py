"""Regen's own 256 MiB run budget: policy value, publication limits and admission reservation.

Owner decision 2026-11-19 (Q1 a): regen run_bytes = 256 MiB, disk-capacity checks kept.
Beam, dragster and glider stay at 32 MiB; storage_bytes (512 MiB) is unchanged.
"""
import asyncio
import os

import pytest

from backend.tests.test_engineering_generator_contracts import brief_for, content

MIB = 1024 ** 2


def test_regen_budget_is_256_mib_and_nothing_else_moves():
    from engineering.capabilities import CAPABILITY, REGEN, ResourcePolicy, get_generator
    base = ResourcePolicy()
    assert REGEN.resources.run_bytes == 256 * MIB
    assert REGEN.resources.storage_bytes == base.storage_bytes == 512 * MIB
    changed = {k for k, v in REGEN.resources.model_dump().items() if v != base.model_dump()[k]}
    assert changed == {'run_bytes'}
    assert REGEN.resources.version == base.version == 'engineering_resources_v1'
    assert CAPABILITY.resources.run_bytes == 32 * MIB
    for generator in ('co2_dragster', 'glider_cem'):
        assert get_generator(generator).resources == base
        assert get_generator(generator).resources.run_bytes == 32 * MIB


def test_resource_values_feed_no_hashed_identity():
    """Only the resource policy *version* is bound into briefs; run_bytes never reaches a hash."""
    from engineering.capabilities import REGEN, ResourcePolicy
    encoded = content().model_dump_json()
    assert 'run_bytes' not in encoded and 'storage_bytes' not in encoded
    assert content().capability.resource_policy_version == REGEN.resources.version == ResourcePolicy().version


def test_run_budget_follows_the_briefs_capability():
    from engineering.bundles import run_budget
    from engineering.capabilities import CAPABILITY
    assert run_budget(brief_for(content())) == 256 * MIB
    beam = type('B', (), {'content': object()})()
    assert run_budget(beam) == CAPABILITY.resources.run_bytes == 32 * MIB


def test_budget_text_names_the_actual_budget():
    from engineering.bundles import budget_text
    assert budget_text(32 * MIB) == '32 MiB'
    assert budget_text(256 * MIB) == '256 MiB'


# --- admission ---------------------------------------------------------------------------

def _controller(tmp_path):
    from engineering.jobs import JobController
    from engineering.store import SQLiteStore
    store = SQLiteStore(tmp_path)
    return store, JobController(store)


def _serve_brief(monkeypatch, brief):
    from engineering import store as store_module
    monkeypatch.setattr(store_module.SQLiteBriefRepository, 'read_brief', lambda self, brief_id: brief)


def _admit(jobs, key):
    from engineering.admission import native_slot
    admitted = jobs.admit('gen-brief', key)
    native_slot.release(admitted.owner)       # admission only; nothing runs in this test
    return admitted


def _reserved(store):
    with store.transaction() as db:
        return [r[0] for r in db.execute('SELECT reserved FROM jobs ORDER BY rowid')]


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store needs POSIX')
def test_regen_admission_reserves_its_own_run_bytes(tmp_path, monkeypatch):
    store, jobs = _controller(tmp_path)
    _serve_brief(monkeypatch, brief_for(content()))
    _admit(jobs, 'one')
    assert _reserved(store) == [256 * MIB]
    store.close()


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store needs POSIX')
def test_regen_is_refused_when_storage_cannot_hold_256_mib_more(tmp_path, monkeypatch):
    from engineering.admission import AdmissionBusy
    store, jobs = _controller(tmp_path)
    _serve_brief(monkeypatch, brief_for(content()))
    _admit(jobs, 'one')
    with store.transaction() as db:
        db.execute('UPDATE jobs SET reserved=?', (300 * MIB,))       # 300 + 256 > 512, but 300 + 32 < 512
    with pytest.raises(AdmissionBusy):
        jobs.admit('gen-brief', 'two')
    with store.transaction() as db:
        db.execute('UPDATE jobs SET reserved=?', (256 * MIB,))       # 256 + 256 == 512 still fits
    _admit(jobs, 'three')
    store.close()


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store needs POSIX')
def test_beam_admission_is_unaffected(tmp_path):
    from backend.tests.test_engineering_contracts import data, review_for
    from engineering.briefs import BriefService
    from engineering.store import SQLiteBriefRepository
    from engineering.admission import native_slot
    store, jobs = _controller(tmp_path)
    service = BriefService(SQLiteBriefRepository(store))
    draft = service.create(data()['content'])
    brief = service.finalize(review_for(draft.draft))
    first = jobs.admit(brief.brief_id, 'one')
    native_slot.release(first.owner)
    assert _reserved(store) == [32 * MIB]
    with store.transaction() as db:
        db.execute('UPDATE jobs SET reserved=?', (300 * MIB,))       # a regen job would be refused here
    second = jobs.admit(brief.brief_id, 'two')
    native_slot.release(second.owner)
    assert _reserved(store) == [300 * MIB, 32 * MIB]
    store.close()


# --- publication (real run through the fake adapter; CI only) ------------------------------

def _oversized_run(size):
    """A regen search result whose primary STL has ``size`` bytes, every digest rebound to it."""
    from backend.tests.test_engineering_generator_contracts import (FakeAdapter, make_evaluation, sha)
    from engineering.contracts import ArtifactIdentity
    from engineering.search import search
    brief = brief_for(content())
    primary = brief.content.descriptor.primary_artifact
    stl = b'x' * size

    def rebind(e):
        e['artifacts'][primary] = stl
        e['stated_digests'][primary] = sha(stl)
        identity = ArtifactIdentity(path=primary, sha256=sha(stl), bytes=len(stl))
        e['checks'] = tuple(c.model_copy(update={'artifacts': (identity,)}) for c in e['checks'])

    class Adapter(FakeAdapter):
        async def evaluate(self, candidate):
            return make_evaluation(candidate, tamper=rebind)

    return brief, asyncio.run(search(brief, adapter=Adapter(brief)))


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
def test_regen_publishes_a_run_over_32_mib_and_stays_within_256(tmp_path):
    from engineering.bundles import BundlePublisher
    from engineering.store import SQLiteStore
    store = SQLiteStore(tmp_path)
    publisher = BundlePublisher(store)
    brief, result = _oversized_run(40 * MIB)
    assert result.state == 'selected'
    bundle = asyncio.run(publisher.publish('a' * 32, brief, result))
    assert 32 * MIB < bundle.stored_bytes <= 256 * MIB
    publisher.validate('a' * 32, bundle)
    store.close()


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
def test_regen_over_256_mib_is_refused_naming_the_real_budget(tmp_path):
    from engineering.bundles import BundlePublisher
    from engineering.store import SQLiteStore
    store = SQLiteStore(tmp_path)
    publisher = BundlePublisher(store)
    brief, result = _oversized_run(257 * MIB)
    with pytest.raises(ValueError, match='256 MiB'):
        asyncio.run(publisher.publish('a' * 32, brief, result))
    assert not publisher.directory('a' * 32).exists()
    store.close()


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
def test_free_disk_check_uses_the_capabilitys_budget(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple
    from engineering.bundles import BundlePublisher
    from engineering.store import SQLiteStore
    usage = namedtuple('usage', 'total used free')
    store = SQLiteStore(tmp_path)
    publisher = BundlePublisher(store)
    brief, result = _oversized_run(1024)
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: usage(10**12, 0, 100 * MIB))   # < 256 MiB free
    with pytest.raises(OSError, match='insufficient free capacity'):
        asyncio.run(publisher.publish('a' * 32, brief, result))
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: usage(10**12, 0, 300 * MIB))
    asyncio.run(publisher.publish('a' * 32, brief, result))
    store.close()
