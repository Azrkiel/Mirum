"""Real native packages; mutation/failure tests reuse captured native evidence."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
import time
import zipfile
import pytest

from backend.tests.test_engineering_contracts import data, review_for
from engineering.briefs import BriefService
from engineering.store import SQLiteStore, SQLiteBriefRepository


@pytest.fixture(scope='module')
def native():
    from engineering.search import search
    from engineering.briefs import MemoryBriefRepository
    svc = BriefService(MemoryBriefRepository())
    d = svc.create(data()['content'])
    brief = svc.finalize(review_for(d.draft))
    result = asyncio.run(search(brief))
    assert result.state == 'selected'
    return brief, result


def publisher(tmp_path):
    from engineering.bundles import BundlePublisher
    store = SQLiteStore(tmp_path)
    return store, BundlePublisher(store)


def test_native_package_complete_exact_bytes_and_external_identities(tmp_path, native):
    store, pub = publisher(tmp_path)
    brief, result = native
    bundle = asyncio.run(pub.publish('a'*32, brief, result))
    pub.validate('a'*32, bundle)
    with pub.lease('a'*32, 'zip', envelope=bundle) as download:
        archive = download.file.read()
    assert hashlib.sha256(archive).hexdigest() == bundle.zip_sha256
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        assert set(z.namelist()) == {p.path for p in bundle.payloads} | {'manifest.json'}
        for name, value in result.selected['artifacts'].items():
            assert z.read(name) == value
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['brief_sha256'] == brief.brief_sha256
        assert all('mime_type' in p for p in manifest['payloads'])
        assert 'zip_sha256' not in manifest and 'manifest_sha256' not in manifest
        assert json.loads(z.read('brief.json'))['brief_id'] == brief.brief_id
        assert 'not physical' in z.read('report.md').decode().lower()
    assert bundle.stored_bytes <= 32*1024**2
    store.close()


@pytest.mark.parametrize('name', ['brief.json','model.step','preview.glb','report.json','manifest.json','package.zip'])
def test_mutation_prevents_validation_and_delivery(tmp_path, native, name):
    store, pub = publisher(tmp_path)
    brief, result = native
    bundle = asyncio.run(pub.publish('a'*32, brief, result))
    path = pub.directory('a'*32) / name
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError): pub.validate('a'*32, bundle)
    with pytest.raises(ValueError):
        with pub.lease('a'*32, 'zip', envelope=bundle): pass
    store.close()


def test_bad_evidence_or_failed_write_cannot_publish(tmp_path, native, monkeypatch):
    store, pub = publisher(tmp_path)
    brief, result = native
    from dataclasses import replace
    mutated = dict(result.selected, artifacts=dict(result.selected['artifacts'], **{'preview.glb':b'wrong'}))
    with pytest.raises(ValueError): asyncio.run(pub.publish('a'*32, brief, replace(result, selected=mutated)))
    assert not pub.directory('a'*32).exists()
    original = pub._write
    def fail(path, value):
        if path.name == 'report.json': raise OSError('injected disk full')
        original(path,value)
    monkeypatch.setattr(pub,'_write',fail)
    with pytest.raises(OSError): asyncio.run(pub.publish('a'*32, brief, result))
    assert not pub.directory('a'*32).exists()
    assert not list(pub.staging.iterdir())
    store.close()


def test_expiry_lease_and_legacy_cleanup_boundary(tmp_path, native, monkeypatch):
    store, pub = publisher(tmp_path)
    brief, result = native
    bundle = asyncio.run(pub.publish('a'*32, brief, result))
    from engineering.bundles import ArtifactExpired
    import artifacts
    monkeypatch.setattr(artifacts,'_RUNS_DIR',tmp_path/'legacy'/'runs')
    artifacts.sweep(ttl_seconds=-1)
    artifacts.enforce_caps(max_runs=0,max_bytes=0)
    pub.validate('a'*32,bundle)
    # An active lease survives expiry; new leases are explicitly expired.
    with pub.lease('a'*32,'zip',envelope=bundle) as download:
        with store.transaction() as db:
            db.execute('UPDATE publications SET expires=?',(time.time()-1,))
        pub.cleanup()
        assert download.file.read(2) == b'PK'
        assert pub.directory('a'*32).exists()
        with pytest.raises(ArtifactExpired):
            with pub.lease('a'*32,'zip',envelope=bundle): pass
    pub.cleanup()
    assert not pub.directory('a'*32).exists()
    store.close()


def test_controller_publication_diagnostics_and_capacity(tmp_path, native):
    from engineering.jobs import JobController
    from engineering.cantilever import CantileverAdapter
    from engineering.admission import AdmissionBusy
    async def run():
        store, pub = publisher(tmp_path)
        svc=BriefService(SQLiteBriefRepository(store))
        draft=svc.create(data()['content']); brief=svc.finalize(review_for(draft.draft))
        jobs=JobController(store,publisher=pub)
        job=jobs.submit(brief.brief_id,'real')
        await jobs.wait(job.job_id)
        accepted=jobs.read(job.job_id)
        assert accepted.outcome == 'accepted' and accepted.bundle
        with pub.lease(job.job_id,'step') as download:
            assert download.file.read()[:4] == b'ISO-'
        with store.transaction() as db:
            db.execute('UPDATE jobs SET reserved=?',(512*1024**2,))
        with pytest.raises(AdmissionBusy): jobs.submit(brief.brief_id,'over-capacity')
        with store.transaction() as db:
            db.execute('UPDATE jobs SET reserved=?',(accepted.bundle.stored_bytes,))
        payload=data()['content']; payload['requirements']['material']['allowable_stress_mpa']=1.
        d=svc.create(payload); b=svc.finalize(review_for(d.draft))
        job2=jobs.submit(b.brief_id,'reject')
        await jobs.wait(job2.job_id)
        assert jobs.read(job2.job_id).outcome == 'rejected'
        with pub.lease(job2.job_id,'zip') as download:
            with zipfile.ZipFile(download.file) as z:
                assert {'brief.json','attempts.json','report.json','report.md','manifest.json'} == set(z.namelist())
        latest=svc.edit(draft.draft.draft_id,expected_revision=1,payload=data()['content'])
        with store.transaction() as db:
            db.execute('UPDATE jobs SET expires=?',(time.time()-1,))
        pub.cleanup()
        assert jobs.read(job.job_id).schema_version == 'engineering_expired_run_v1'
        from engineering.bundles import ArtifactExpired
        with pytest.raises(ArtifactExpired): svc.read_brief(brief.brief_id)
        assert svc.read(draft.draft.draft_id)==latest
        with pytest.raises(ArtifactExpired):
            with pub.lease(job.job_id,'zip'): pass
        assert jobs.submit(brief.brief_id,'real').job_id==job.job_id
        with store.transaction() as db:
            db.execute('UPDATE jobs SET deadline=?',(time.time()-8*86400,))
            db.execute('UPDATE retention SET created=?',(time.time()-8*86400,))
        pub.cleanup()
        with pytest.raises(KeyError): jobs.read(job.job_id)
        with pytest.raises(KeyError): svc.read_brief(brief.brief_id)
        await jobs.close(); store.close()
    asyncio.run(run())


def test_cancellation_after_real_publication_wins_and_removes_bundle(tmp_path,native):
    from engineering.jobs import JobController
    from engineering.cantilever import CantileverAdapter
    from engineering.bundles import BundlePublisher
    brief,result=native
    class Replay(CantileverAdapter):
        async def evaluate(self,candidate):
            return next(e for e in result.evaluations if e['candidate']==candidate)
    class CancelOnPublish(BundlePublisher):
        async def publish(self,*args):
            bundle=await super().publish(*args)
            self.controller.cancel(args[0])
            return bundle
    async def run():
        store=SQLiteStore(tmp_path); pub=CancelOnPublish(store)
        svc=BriefService(SQLiteBriefRepository(store)); d=svc.create(data()['content'])
        b=svc.finalize(review_for(d.draft))
        jobs=JobController(store,publisher=pub,adapter=Replay())
        pub.controller=jobs
        job=jobs.submit(b.brief_id,'cancel-publication')
        await jobs.wait(job.job_id)
        assert jobs.read(job.job_id).lifecycle=='cancelled'
        assert not pub.directory(job.job_id).exists()
        await jobs.close(); store.close()
    asyncio.run(run())


def test_oversized_diagnostic_and_path_allowlist_fail_before_writes(tmp_path,native):
    store,pub=publisher(tmp_path); brief,result=native
    payload=pub._reports('a'*32,brief,result,'not_evaluated')
    payload['report.json']=b'x'*(32*1024**2)
    with pytest.raises(ValueError,match='budget'):
        asyncio.run(pub._publish('a'*32,brief,result,payload,'diagnostic'))
    for key in ['../escape','a'*32+'\n','/tmp/escape']:
        with pytest.raises(KeyError): pub.directory(key)
    assert not list(pub.root.iterdir())
    store.close()


def test_namespace_symlink_cannot_escape_owned_volume(tmp_path):
    from engineering.bundles import BundlePublisher
    root=tmp_path/'volume'; root.mkdir()
    outside=tmp_path/'outside'; outside.mkdir()
    (root/'bundles').symlink_to(outside,target_is_directory=True)
    store=SQLiteStore(root)
    try:
        with pytest.raises(ValueError,match='namespace'): BundlePublisher(store)
    finally: store.close()
