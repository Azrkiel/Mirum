"""Atomic exact-byte publication, validated downloads, retention and leases."""
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import time
import zipfile
from typing import Literal
from pydantic import Field, model_validator

from .capabilities import CAPABILITY
from .contracts import ArtifactIdentity, BundleIdentity, EngineeringBrief, Outcome, ContractModel, Sha256
from .policy import assess
from .store import ArtifactExpired

FORMATS = {
    'zip': ('package.zip','application/zip'),
    'brief': ('brief.json','application/json'),
    'spec': ('selected_spec.json','application/json'),
    'source': ('model.py','text/plain'),
    'step': ('model.step','application/step'),
    'glb': ('preview.glb','model/gltf-binary'),
    'attempts': ('attempts.json','application/json'),
    'report': ('report.json','application/json'),
    'markdown': ('report.md','text/markdown'),
    'manifest': ('manifest.json','application/json'),
}
MIME = dict(FORMATS.values())
PAYLOADS = set(MIME)-{'package.zip','manifest.json'}
METADATA_RESERVE = 1024**2


class ManifestPayload(ArtifactIdentity):
    mime_type: Literal['application/json','text/plain','application/step','model/gltf-binary','text/markdown']


class BundleManifest(ContractModel):
    schema_version: Literal['engineering_manifest_v1']
    job_id: str = Field(pattern='^[0-9a-f]{32}$',max_length=32)
    brief_sha256: Sha256
    selected_candidate_sha256: Sha256 | None
    kind: Literal['accepted','diagnostic']
    payloads: tuple[ManifestPayload,...] = Field(min_length=4,max_length=8)

    @model_validator(mode='after')
    def complete(self):
        expected=PAYLOADS if self.kind=='accepted' else {'brief.json','attempts.json','report.json','report.md'}
        if (len(self.payloads)!=len(expected) or {p.path for p in self.payloads}!=expected
                or any(p.mime_type!=MIME[p.path] for p in self.payloads)
                or (self.selected_candidate_sha256 is not None)!=(self.kind=='accepted')):
            raise ValueError('Incomplete or incorrectly typed manifest')
        return self


def encode(value):
    return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


@dataclass
class Download:
    file: object
    filename: str
    mime_type: str
    size: int
    sha256: str


class BundlePublisher:
    def __init__(self, store):
        self.store = store
        self.root, self.staging = store.root/'bundles', store.root/'staging'
        if any(p.is_symlink() or p.resolve().parent!=store.root for p in (self.root,self.staging)):
            raise ValueError('Engineering namespace must remain inside its owned volume')
        # Legacy sweeps may be configured, but may never own this namespace.
        from artifacts import _RUNS_DIR
        legacy = _RUNS_DIR.resolve()
        if legacy == store.root or legacy in store.root.parents or store.root in legacy.parents:
            raise ValueError('Engineering and legacy artifact roots must be separate')
        self.root.mkdir(exist_ok=True)
        self.staging.mkdir(exist_ok=True)
        self.leases = {}
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS publications(job TEXT PRIMARY KEY, record TEXT NOT NULL, expires REAL NOT NULL)')
            # Backfill pre-B10 rows from their recorded times, never reset TTL on restart.
            for table,kind,field in [('drafts','draft','draft'),('briefs','brief','review')]:
                for row in db.execute(f'SELECT id,record FROM {table}').fetchall():
                    record=json.loads(row['record'])
                    if record.get('expired'): continue
                    from datetime import datetime
                    stamp=(record['draft']['provenance']['created_at'] if field=='draft' else record['review']['reviewed_at'])
                    db.execute('INSERT OR IGNORE INTO retention VALUES (?,?,?)',(kind,row['id'],datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()))

    def directory(self, job_id):
        if type(job_id) is not str or re.fullmatch('[0-9a-f]{32}',job_id) is None:
            raise KeyError('Unknown engineering job')
        path = self.root/job_id
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError('Invalid engineering artifact directory')
        return path

    @staticmethod
    def _write(path, value):
        with path.open('xb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _sync_directory(path):
        fd=os.open(path,os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def _publication(self, job_id):
        row=self.store.db.execute('SELECT record,expires FROM publications WHERE job=?',(job_id,)).fetchone()
        if row is None: raise KeyError(job_id)
        return json.loads(row['record']),row['expires']

    async def publish(self, job_id, brief, result):
        brief=EngineeringBrief.model_validate(brief.model_dump())
        selected=result.selected
        if result.state != 'selected' or not selected or not result.attempts:
            raise ValueError('Publication requires selected evidence')
        candidate=selected['candidate']
        if (candidate.brief_sha256 != brief.brief_sha256 or candidate.content != brief.content
                or result.attempts[-1].candidate != candidate
                or result.attempts[-1].checks != selected['checks']):
            raise ValueError('Selected evidence belongs to another reviewed revision')
        decision=assess(candidate,selected)
        if not decision.available or decision.outcome != Outcome.ACCEPTED:
            raise ValueError('Publication evidence is unavailable or rejected')
        # Reassess every retained attempt, preserving measured rejections.
        if len(result.evaluations) != len(result.attempts):
            raise ValueError('Attempt evidence is incomplete')
        for attempt,evaluation in zip(result.attempts,result.evaluations):
            check=assess(attempt.candidate,evaluation)
            if not check.available or check.outcome != attempt.outcome or check.checks != attempt.checks:
                raise ValueError('Attempt evidence changed')
        payload=self._reports(job_id,brief,result,'accepted')
        payload.update(selected['artifacts'])
        payload['selected_spec.json']=encode(candidate.specification.model_dump(mode='json'))
        publication=await self._publish(job_id,brief,result,payload,'accepted')
        return BundleIdentity.model_validate_json(json.dumps(publication['bundle']))

    def _reports(self,job_id,brief,result,outcome):
        report=dict(schema_version='engineering_report_v1',job_id=job_id,brief_id=brief.brief_id,
            brief_sha256=brief.brief_sha256,fixed_requirements_sha256=brief.fixed_requirements_sha256,
            capability=brief.content.capability.model_dump(mode='json'),outcome=outcome,
            scope=CAPABILITY.geometry_scope,standards='not_evaluated',engineering_certified=False,
            reasons=list(result.reasons),
            evaluations=[dict(candidate_sha256=e['candidate'].candidate_sha256,
                              report_sha256=e['report_sha256'],native_report=e['report']) for e in result.evaluations])
        rows=['# Checked cantilever result', '',f'Job: {job_id}',f'Brief: {brief.brief_sha256}',
            f'Outcome: {outcome}', '',
            'Digital beam-model evidence, not physical testing, manufacturing release or standards certification.',
            'Ideal clamp at X=0; static +Z end force at X=L; homogeneous linear elasticity.',
            'Material source: '+brief.content.requirements.material.reference,
            'Selection: smallest passing thickness in the reviewed set.', '',
            '| Thickness (mm) | Outcome | Reasons |','|---|---|---|']
        rows += [f'| {a.candidate.specification.height_mm:g} | {a.outcome.value} | {"; ".join(a.failures) or "All mandatory digital checks passed"} |' for a in result.attempts]
        rows += ['',*result.reasons]
        return {'brief.json':encode(brief.model_dump(mode='json')),
            'attempts.json':encode([a.model_dump(mode='json') for a in result.attempts]),
            'report.json':encode(report),'report.md':('\n'.join(rows)+'\n').encode()}

    async def publish_diagnostics(self,job_id,brief,result):
        payload=self._reports(job_id,brief,result,'rejected' if result.state=='rejected' else 'not_evaluated')
        return await self._publish(job_id,brief,result,payload,'diagnostic')

    async def _publish(self,job_id,brief,result,payload,kind):
        directory=self.directory(job_id)
        if directory.exists(): raise ValueError('Immutable package already exists')
        required=PAYLOADS if kind=='accepted' else {'brief.json','attempts.json','report.json','report.md'}
        if set(payload) != required: raise ValueError('Incomplete publication')
        identities=[ArtifactIdentity(path=name,bytes=len(value),sha256=digest(value)) for name,value in sorted(payload.items())]
        manifest=dict(schema_version='engineering_manifest_v1',job_id=job_id,brief_sha256=brief.brief_sha256,
            selected_candidate_sha256=result.selected['candidate'].candidate_sha256 if kind=='accepted' else None,
            kind=kind,payloads=[dict(i.model_dump(),mime_type=MIME[i.path]) for i in identities])
        manifest_data=encode(BundleManifest.model_validate_json(json.dumps(manifest)).model_dump(mode='json'))
        files=dict(payload,**{'manifest.json':manifest_data})
        # Limit uncompressed bytes before creating the ZIP or writing anything.
        if sum(map(len,files.values()))+METADATA_RESERVE > CAPABILITY.resources.run_bytes:
            raise ValueError('Engineering run exceeds 32 MiB budget')
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name,value in files.items(): z.writestr(name,value)
        files['package.zip']=archive.getvalue()
        stored=sum(map(len,files.values()))+METADATA_RESERVE
        if stored>CAPABILITY.resources.run_bytes: raise ValueError('Complete stored run exceeds 32 MiB budget')
        if shutil.disk_usage(self.store.root).free < CAPABILITY.resources.run_bytes:
            raise OSError('Engineering storage has insufficient free capacity')
        with self.store._lock:
            job=self.store.db.execute('SELECT record FROM jobs WHERE id=?',(job_id,)).fetchone()
            if job and len(job[0].encode())+len(payload['brief.json'])+16384+65536>METADATA_RESERVE:
                raise ValueError('Retained run metadata exceeds its reserved budget')
        publication=dict(kind=kind,payloads=[i.model_dump() for i in identities],manifest_sha256=digest(manifest_data),
            manifest_bytes=len(manifest_data),zip_sha256=digest(files['package.zip']),zip_bytes=len(files['package.zip']),stored_bytes=stored)
        if kind=='accepted':
            publication['bundle']=BundleIdentity(payloads=tuple(identities),
                **{k:publication[k] for k in ('manifest_sha256','manifest_bytes','zip_sha256','zip_bytes','stored_bytes')}).model_dump(mode='json')
        stage=self.staging/job_id
        stage.mkdir()
        try:
            for name,value in files.items():
                self._write(stage/name,value)
                await asyncio.sleep(0)
            self._sync_directory(stage)
            os.replace(stage,directory)
            self._sync_directory(self.root)
            with self.store.transaction() as db:
                db.execute('INSERT INTO publications VALUES (?,?,?)',
                    (job_id,json.dumps(publication),time.time()+CAPABILITY.resources.bundle_retention_seconds))
            self.validate(job_id,publication.get('bundle'))
            return publication
        except BaseException:
            if stage.exists(): shutil.rmtree(stage)
            if directory.exists(): shutil.rmtree(directory)
            with self.store.transaction() as db:
                db.execute('DELETE FROM publications WHERE job=?',(job_id,))
            raise

    def validate(self,job_id,envelope=None):
        # Caller may already hold the terminal write transaction. RLock protects
        # the connection; do not start a nested SQLite transaction here.
        with self.store._lock:
            publication,_=self._publication(job_id)
            if envelope is not None:
                expected=envelope.model_dump(mode='json') if isinstance(envelope,BundleIdentity) else envelope
                if publication.get('bundle') != expected: raise ValueError('Publication envelope changed')
            directory=self.directory(job_id)
            identities={p['path']:p for p in publication['payloads']}
            names=set(identities)|{'manifest.json','package.zip'}
            if set(p.name for p in directory.iterdir()) != names: raise ValueError('Incomplete package')
            captured={}
            for name in names:
                path=directory/name
                if name not in MIME or path.is_symlink() or path.resolve().parent != directory.resolve():
                    raise ValueError('Invalid package path')
                expected=identities.get(name) or dict(bytes=publication['manifest_bytes' if name=='manifest.json' else 'zip_bytes'],
                    sha256=publication['manifest_sha256' if name=='manifest.json' else 'zip_sha256'])
                if path.stat().st_size != expected['bytes']: raise ValueError('Package byte count changed')
                value=path.read_bytes()
                if digest(value) != expected['sha256']: raise ValueError('Package fingerprint changed')
                captured[name]=value
            manifest=BundleManifest.model_validate_json(captured['manifest.json']).model_dump(mode='json')
            if manifest['job_id']!=job_id or manifest['kind']!=publication['kind']:
                raise ValueError('Manifest revision changed')
            if ({p['path']:dict(path=p['path'],sha256=p['sha256'],bytes=p['bytes']) for p in manifest['payloads']} != identities
                    or any(p['mime_type']!=MIME[p['path']] for p in manifest['payloads'])):
                raise ValueError('Manifest content mismatch')
            with zipfile.ZipFile(io.BytesIO(captured['package.zip'])) as z:
                if set(z.namelist()) != names-{'package.zip'} or len(z.namelist()) != len(names)-1:
                    raise ValueError('Archive layout mismatch')
                if any(z.read(name)!=captured[name] for name in z.namelist()): raise ValueError('Archive content mismatch')

    @contextmanager
    def lease(self,job_id,format,*,envelope=None):
        if format not in FORMATS: raise KeyError(format)
        with self.store._lock:
            publication,expires=self._publication(job_id)
            if time.time()>=expires: raise ArtifactExpired('Engineering artifacts expired')
            if envelope is None:
                row=self.store.db.execute('SELECT record FROM jobs WHERE id=?',(job_id,)).fetchone()
                if not row or json.loads(row[0])['lifecycle'] not in ('completed','unavailable','cancelled'):
                    raise KeyError('Publication is not committed')
            self.validate(job_id,envelope)
            name,mime=FORMATS[format]
            path=self.directory(job_id)/name
            if not path.is_file(): raise KeyError(format)
            file=path.open('rb')
            self.leases[job_id]=self.leases.get(job_id,0)+1
        try:
            size,sha=path.stat().st_size,digest(file.read())
            file.seek(0)
            yield Download(file,name,mime,size,sha)
        finally:
            file.close()
            with self.store._lock: self.leases[job_id]-=1

    def stored_bytes(self,job_id):
        with self.store._lock:
            try: return self._publication(job_id)[0]['stored_bytes']
            except KeyError: return 0

    def expires_at(self,job_id):
        with self.store._lock:
            return self._publication(job_id)[1]

    def discard(self,job_id):
        with self.store._lock:
            if self.leases.get(job_id): return
            for path in (self.directory(job_id),self.staging/job_id):
                if path.exists(): shutil.rmtree(path)
            with self.store.transaction() as db:
                db.execute('DELETE FROM publications WHERE job=?',(job_id,))
                row=db.execute('SELECT record FROM jobs WHERE id=?',(job_id,)).fetchone()
                if row and json.loads(row[0])['lifecycle'] in ('cancelled','unavailable'):
                    db.execute('UPDATE jobs SET reserved=0 WHERE id=?',(job_id,))

    def recover(self):
        with self.store._lock:
            for path in list(self.staging.iterdir()):
                if path.is_dir() and not path.is_symlink(): shutil.rmtree(path)
            for path in list(self.root.iterdir()):
                row=self.store.db.execute('SELECT record FROM jobs WHERE id=?',(path.name,)).fetchone()
                if not row or json.loads(row[0])['lifecycle'] not in ('completed','unavailable','cancelled'):
                    self.discard(path.name)
                elif json.loads(row[0]).get('outcome') != 'accepted':
                    # Diagnostics may survive; orphan accepted files may not.
                    try: kind=self._publication(path.name)[0]['kind']
                    except KeyError: kind='accepted'
                    if kind=='accepted': self.discard(path.name)
        self.cleanup()

    def cleanup(self):
        from .jobs import compact_run
        now=time.time()
        with self.store.transaction() as db:
            rows=db.execute('SELECT p.* FROM publications p LEFT JOIN jobs j ON j.id=p.job WHERE p.expires<=? OR j.expires<=?',(now,now)).fetchall()
            for row in rows:
                job_id=row['job']
                if self.leases.get(job_id): continue
                path=self.directory(job_id)
                if path.exists(): shutil.rmtree(path)
                db.execute('UPDATE publications SET expires=MIN(expires,?) WHERE job=?',(now,job_id))
                db.execute('UPDATE jobs SET reserved=0 WHERE id=?',(job_id,))
            expired_jobs=db.execute('SELECT id,record,brief FROM jobs WHERE expires<=?',(now,)).fetchall()
            for job in expired_jobs:
                job_id=job['id']
                if self.leases.get(job_id): continue
                if not json.loads(job['record']).get('expired'):
                    db.execute('UPDATE jobs SET record=?,reserved=0 WHERE id=?',(json.dumps(compact_run(json.loads(job['record']))),job_id))
                    # Keep a known-ID tombstone; do not retain private brief input.
                    live=db.execute('SELECT 1 FROM jobs WHERE brief=? AND (expires>? OR reserved>0)',(job['brief'],now)).fetchone()
                    if not live:
                        briefrow=db.execute('SELECT draft,revision FROM briefs WHERE id=?',(job['brief'],)).fetchone()
                        db.execute('UPDATE briefs SET record=? WHERE id=?',('{"expired":true}',job['brief']))
                        if briefrow:
                            newer=db.execute('SELECT 1 FROM briefs b JOIN jobs j ON b.id=j.brief WHERE b.draft=? AND (j.expires>? OR j.reserved>0)',(briefrow[0],now)).fetchone()
                            current=db.execute('SELECT record FROM drafts WHERE id=?',(briefrow[0],)).fetchone()
                            latest=json.loads(current[0]) if current else {}
                            same_revision=latest.get('draft',{}).get('revision')==briefrow['revision']
                            if not newer and same_revision:
                                db.execute('UPDATE drafts SET record=? WHERE id=?',('{"expired":true}',briefrow[0]))
            # Seven days after admission, expire compact job/idempotency state.
            old=db.execute('SELECT id,record FROM jobs WHERE deadline<? AND reserved=0',
                           (now-CAPABILITY.resources.state_retention_seconds,)).fetchall()
            for row in old:
                if self.leases.get(row['id']): continue
                db.execute('DELETE FROM publications WHERE job=?',(row['id'],))
                db.execute('DELETE FROM jobs WHERE id=?',(row['id'],))
            for row in db.execute('SELECT * FROM retention WHERE created<=?',(now-CAPABILITY.resources.bundle_retention_seconds,)).fetchall():
                kind,key=row['kind'],row['id']
                if kind=='audit':
                    if row['created']<=now-CAPABILITY.resources.state_retention_seconds:
                        draft,revision=key.rsplit(':',1)
                        db.execute('DELETE FROM audits WHERE draft=? AND revision=?',(draft,int(revision)))
                        db.execute('DELETE FROM retention WHERE kind=? AND id=?',(kind,key))
                    continue
                if kind=='brief':
                    if db.execute('SELECT 1 FROM jobs WHERE brief=?',(key,)).fetchone(): continue
                    table='briefs'
                else:
                    if db.execute('SELECT 1 FROM proposals WHERE draft=?',(key,)).fetchone(): continue
                    if db.execute('SELECT 1 FROM briefs b JOIN jobs j ON b.id=j.brief WHERE b.draft=? AND (j.expires>? OR j.reserved>0)',(key,now)).fetchone(): continue
                    table='drafts'
                if row['created']<=now-CAPABILITY.resources.state_retention_seconds:
                    db.execute(f'DELETE FROM {table} WHERE id=?',(key,))
                    db.execute('DELETE FROM retention WHERE kind=? AND id=?',(kind,key))
                else:
                    db.execute(f'UPDATE {table} SET record=? WHERE id=? AND record!=?',('{"expired":true}',key,'{"expired":true}'))
