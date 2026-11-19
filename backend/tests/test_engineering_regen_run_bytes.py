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


def _serve_brief(store, brief):
    """Store a finalized brief row directly (jobs reference briefs by foreign key)."""
    with store.transaction() as db:
        db.execute('INSERT INTO briefs VALUES (?,?,?,?)',
                   (brief.brief_id, brief.draft_id, brief.draft_revision, brief.model_dump_json()))


def _admit(jobs, key):
    from engineering.admission import native_slot
    admitted = jobs.admit('gen-brief', key)
    native_slot.release(admitted.owner)       # admission only; nothing runs in this test
    return admitted


def _reserved(store):
    with store.transaction() as db:
        return [r[0] for r in db.execute('SELECT reserved FROM jobs ORDER BY rowid')]


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store needs POSIX')
def test_regen_admission_reserves_its_own_run_bytes(tmp_path):
    store, jobs = _controller(tmp_path)
    _serve_brief(store, brief_for(content()))
    _admit(jobs, 'one')
    assert _reserved(store) == [256 * MIB]
    store.close()


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store needs POSIX')
def test_regen_is_refused_when_storage_cannot_hold_256_mib_more(tmp_path):
    from engineering.admission import AdmissionBusy
    store, jobs = _controller(tmp_path)
    _serve_brief(store, brief_for(content()))
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


# --- publication (CI only; payload built directly, evidence assessment is covered elsewhere) -------

def _publish_sized(tmp_path, size, free=None, monkeypatch=None):
    from types import SimpleNamespace
    from engineering.bundles import BundlePublisher
    from engineering.capabilities import GENERATOR_BASE_PAYLOADS
    from engineering.store import SQLiteStore
    brief = brief_for(content())
    payload = {n: b'x' for n in set(GENERATOR_BASE_PAYLOADS) | set(brief.content.descriptor.payloads)}
    payload['generator-cleaned.stl'] = b'x' * size
    result = SimpleNamespace(selected={'candidate': SimpleNamespace(candidate_sha256='d' * 64)})
    store = SQLiteStore(tmp_path)
    publisher = BundlePublisher(store)
    try:
        return asyncio.run(publisher._publish('a' * 32, brief, result, payload, 'accepted')), publisher
    finally:
        store.close()


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
def test_regen_publishes_a_run_over_32_mib_and_stays_within_256(tmp_path):
    bundle, publisher = _publish_sized(tmp_path, 40 * MIB)
    assert 32 * MIB < bundle['stored_bytes'] <= 256 * MIB
    assert publisher.directory('a' * 32).exists()


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
def test_regen_over_256_mib_is_refused_naming_the_real_budget(tmp_path):
    with pytest.raises(ValueError, match='exceeds 256 MiB budget'):
        _publish_sized(tmp_path, 257 * MIB)


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
def test_free_disk_check_uses_the_capabilitys_budget(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple
    usage = namedtuple('usage', 'total used free')
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: usage(10**12, 0, 100 * MIB))   # < 256 MiB free
    with pytest.raises(OSError, match='insufficient free capacity'):
        _publish_sized(tmp_path, 1024)
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: usage(10**12, 0, 300 * MIB))
    bundle, _ = _publish_sized(tmp_path, 1024)
    assert bundle['kind'] == 'accepted'


# --- schema ceiling and the other capabilities stay at 32 MiB ------------------------------

def test_bundle_identity_ceiling_is_the_largest_capability_budget():
    from engineering.capabilities import CAPABILITY, GENERATORS
    from engineering.contracts import MAX_BUNDLE_BYTES, BundleIdentity
    assert MAX_BUNDLE_BYTES == max(c.resources.run_bytes for c in (CAPABILITY, *GENERATORS)) == 256 * MIB
    for name in ('zip_bytes', 'stored_bytes'):
        assert any(getattr(m, 'le', None) == MAX_BUNDLE_BYTES for m in BundleIdentity.model_fields[name].metadata)


def _identity(**changes):
    from engineering.contracts import BundleIdentity
    base = dict(payloads=tuple(dict(path=n, bytes=1, sha256='a' * 64) for n in
                               ('brief.json', 'selected_spec.json', 'attempts.json', 'report.json', 'report.md',
                                'generator-cleaned.stl')),
                manifest_sha256='b' * 64, manifest_bytes=1, zip_sha256='c' * 64, zip_bytes=10, stored_bytes=100)
    return BundleIdentity.model_validate(dict(base, **changes))


def test_bundle_identity_accepts_up_to_256_mib_and_refuses_more():
    from pydantic import ValidationError
    _identity(zip_bytes=10, stored_bytes=256 * MIB)
    with pytest.raises(ValidationError):
        _identity(stored_bytes=256 * MIB + 1)
    with pytest.raises(ValidationError):
        _identity(zip_bytes=256 * MIB + 1, stored_bytes=256 * MIB)


def _brief_for(name):
    from backend.tests import test_engineering_generator_contracts as g
    if name == 'rectangular_cantilever':
        from backend.tests.test_engineering_contracts import data, review_for
        from engineering.briefs import BriefService, MemoryBriefRepository
        svc = BriefService(MemoryBriefRepository())
        return svc.finalize(review_for(svc.create(data()['content']).draft))
    if name == 'co2_dragster':
        from backend.tests.test_engineering_dragster_adapter import spec_for
        return g.brief_for(g.content('co2_dragster', spec_for()))
    if name == 'glider_cem':
        return g.brief_for(g.content('glider_cem', g.glider_spec()))
    return g.brief_for(g.content())


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
@pytest.mark.parametrize('name,budget_mib', [('rectangular_cantilever', 32), ('co2_dragster', 32),
                                             ('glider_cem', 32), ('regen_chamber', 256)])
def test_every_capability_is_refused_just_over_its_own_budget(tmp_path, name, budget_mib):
    """The 256 MiB schema ceiling must not loosen beam, dragster or glider (enforced in bundles.py)."""
    from types import SimpleNamespace
    from engineering.bundles import BundlePublisher, PAYLOADS
    from engineering.contracts import GeneratorBriefContent
    from engineering.store import SQLiteStore
    brief = _brief_for(name)
    if isinstance(brief.content, GeneratorBriefContent):
        from engineering.capabilities import GENERATOR_BASE_PAYLOADS
        required = set(GENERATOR_BASE_PAYLOADS) | set(brief.content.descriptor.payloads)
    else:
        required = set(PAYLOADS)
    payload = {n: b'x' for n in required}
    payload['report.md'] = b'x' * ((budget_mib + 1) * MIB)
    result = SimpleNamespace(selected={'candidate': SimpleNamespace(candidate_sha256='d' * 64)})
    store = SQLiteStore(tmp_path)
    publisher = BundlePublisher(store)
    with pytest.raises(ValueError, match=f'exceeds {budget_mib} MiB budget'):
        asyncio.run(publisher._publish('a' * 32, brief, result, payload, 'accepted'))
    assert not publisher.directory('a' * 32).exists()
    store.close()
