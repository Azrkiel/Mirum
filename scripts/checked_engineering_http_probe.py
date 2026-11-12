"""Real loopback checked API/native smoke; provider credentials are excluded."""
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

import httpx


def main(output,sha):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='checked-http-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        env={k:os.environ[k] for k in ('PATH','HOME','LANG','PYTHONPATH','OPENBLAS_NUM_THREADS','OMP_NUM_THREADS') if k in os.environ}
        env.update(MIRUM_WORK_DIR=directory+'/work',MIRUM_TELEMETRY_DIR=directory+'/telemetry',
            MIRUM_ENGINEERING_DATA_DIR=directory+'/persistent',MIRUM_ACCESS_TOKEN='verification-access')
        log=(output/'server.log').open('w')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','main:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=log,stderr=log)
        observed=[]
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'X-Mirum-Access-Token':'verification-access'},timeout=30,trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get('/openapi.json').status_code==200: break
                    except httpx.TransportError: pass
                    time.sleep(.2)
                else: raise RuntimeError('Application did not start')
                assert httpx.get(f'http://127.0.0.1:{port}/engineering/v1/capabilities',trust_env=False).status_code==401
                capability=client.get('/engineering/v1/capabilities').json()
                assert capability['native_available'] and capability['ai_available'] is False
                payload=json.loads(Path('backend/tests/fixtures/engineering/brief-draft.json').read_text())['content']
                def reviewed(content):
                    r=client.post('/engineering/v1/drafts',json={'input':content}); assert r.status_code==201,r.text
                    draft=r.json()['draft']
                    from engineering.contracts import REVIEW_FIELDS
                    from datetime import datetime,timezone
                    body=dict(draft_id=draft['draft_id'],draft_revision=draft['revision'],
                        brief_sha256=__import__('engineering.contracts',fromlist=['BriefContent']).BriefContent.model_validate_json(json.dumps(draft['content'])).brief_sha256,
                        acknowledged_fields=list(REVIEW_FIELDS),reviewed_at=datetime.now(timezone.utc).isoformat(),reviewed_by='explicit HTTP verification caller')
                    r=client.post('/engineering/v1/briefs',json=body); assert r.status_code==201,r.text
                    return r.json()
                def wait(job_id):
                    for _ in range(840):
                        r=client.get('/engineering/v1/jobs/'+job_id); assert r.status_code==200,r.text
                        job=r.json()
                        if job['lifecycle'] in ('completed','cancelled','unavailable'): return job
                        time.sleep(.5)
                    raise RuntimeError('Job did not complete in its overall budget')
                for name,content in [('accepted',payload),('rejected',json.loads(json.dumps(payload)))]:
                    if name=='rejected': content['requirements']['material']['allowable_stress_mpa']=1.
                    brief=reviewed(content)
                    r=client.post('/engineering/v1/jobs',json=dict(brief_id=brief['brief_id'],idempotency_key=name)); assert r.status_code==202,r.text
                    job=wait(r.json()['job_id']); assert job['outcome']==name,job
                    (output/(name+'-job.json')).write_text(json.dumps(job,indent=2)+'\n')
                    from engineering.bundles import FORMATS
                    files={}
                    for format,available in job['artifacts'].items():
                        if not available: continue
                        r=client.get(f"/engineering/v1/jobs/{job['job_id']}/artifacts/{format}"); assert r.status_code==200,r.text
                        filename=FORMATS[format][0]
                        assert hashlib.sha256(r.content).hexdigest()==r.headers['x-content-sha256']
                        folder=output/name; folder.mkdir(exist_ok=True); (folder/filename).write_bytes(r.content)
                        files[filename]=dict(bytes=len(r.content),sha256=hashlib.sha256(r.content).hexdigest())
                    with zipfile.ZipFile(output/name/'package.zip') as z:
                        if name=='accepted': assert len(z.namelist())==9
                        else: assert 'model.step' not in z.namelist() and 'preview.glb' not in z.namelist()
                    observed.append(dict(case=name,outcome=job['outcome'],heights=[a['candidate']['specification']['height_mm'] for a in job['attempts']],files=files))
                brief=reviewed(payload)
                r=client.post('/engineering/v1/jobs',json=dict(brief_id=brief['brief_id'],idempotency_key='cancel')); assert r.status_code==202
                key=r.json()['job_id']; assert client.post('/engineering/v1/jobs/'+key+'/cancel').status_code==200
                assert wait(key)['lifecycle']=='cancelled'
                observed.append(dict(case='cancelled',lifecycle='cancelled'))
                manifest=dict(tested_implementation_sha=sha,transport='real loopback HTTP',provider_calls=0,
                    provider_credentials_in_server=False,review='Shared fixture and explicit HTTP caller, not a browser/human review',cases=observed)
                (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
                print(json.dumps(dict(sha=sha,cases=[v['case'] for v in observed],provider_calls=0)))
        finally:
            process.terminate()
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
            log.close()


if __name__=='__main__': main(*sys.argv[1:])
