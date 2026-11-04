import asyncio
import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address
from cem import api
from cem.glider.calibration import initial
from cem.glider.requirements import GliderMissionRequirements
from cem.glider.service import identity


def app_for(manager,monkeypatch):
    monkeypatch.setattr(api,'jobs',manager)
    app=FastAPI()
    api.attach_cem_routes(app,Limiter(key_func=get_remote_address,enabled=False))
    return app


def payload():
    return dict(kind='discovery',requirements=GliderMissionRequirements(launch={'method':'drop'}).model_dump(),calibration=initial())


def test_feasibility_and_clarification_do_not_call_provider(monkeypatch):
    from cem import extraction
    async def forbidden(*a,**kw): raise AssertionError('Provider must not run')
    # The actual clarification gate returns before any provider import/call.
    with TestClient(app_for(api.Jobs(),monkeypatch)) as c:
        r=c.post('/cem/glider/requirements',json={'prompt':'1200 m of flight duration from 10 m'})
        assert r.status_code==200 and r.json()['status']=='needs_clarification'
        f=c.post('/cem/glider/feasibility',json={'requirements':{'launch':{'method':'drop'},'reference_target':1200.}})
        assert f.status_code==200 and f.json()['continue_search']
        assert c.post('/cem/glider/feasibility',json={'requirements':{'launch':{'method':'drop'},'oops':True}}).status_code==422
        assert c.post('/cem/glider/discover',json={'requirements':{'launch':{'method':'drop'},'discovery':{'families':['box']}}}).status_code==422


def test_busy_states_membership_and_no_rejected_bundle(monkeypatch,tmp_path):
    manager=api.Jobs()
    monkeypatch.setenv('MIRUM_WORK_DIR',str(tmp_path))
    async def hold(*args): await asyncio.sleep(100)
    monkeypatch.setattr(manager,'run',hold)
    with TestClient(app_for(manager,monkeypatch)) as c:
        response=c.post('/cem/glider/discover',json={'requirements':{'launch':{'method':'drop'}}})
        assert response.status_code==202
        key=response.json()['job_id']
        assert c.post('/cem/glider/discover',json={'requirements':{'launch':{'method':'drop'}}}).status_code==429
        assert c.get(f'/cem/glider/jobs/{key}').json()['status']=='running'
        assert c.get(f'/cem/glider/jobs/{key}/bundle').status_code==404
        assert c.post('/cem/glider/design',json={'discovery_job_id':key,'elite_id':'a'*16}).status_code==409
        client_close=c.portal.call(manager.close)
        manager.jobs[key].update(kind='design',status='completed',result={'status':'rejected'})
        assert c.get(f'/cem/glider/jobs/{key}/bundle').status_code==409
        assert c.post('/cem/glider/design',json={'discovery_job_id':'../escape','elite_id':'a'*16}).status_code==422


@pytest.mark.asyncio
async def test_worker_timeout_secrets_and_limits(monkeypatch,tmp_path):
    manager=api.Jobs();p=payload()
    req=GliderMissionRequirements.model_validate(p['requirements'])
    manager.jobs['test']=dict(directory=tmp_path,status='running',identity=identity(req,p['calibration']))
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY','not-for-worker')
    async def fail(self,command,**kw):
        assert self.memory_mb==3072 and self.timeout==1800
        assert 'GEMINI_API_KEY_HEAVY' not in kw['env']
        assert 'HTTPS_PROXY' not in kw['env'] and 'FLY_API_TOKEN' not in kw['env']
        return dict(code='TIMEOUT',returncode=-9,stderr='',stdout='')
    monkeypatch.setattr(api.ProcessRunner,'run',fail)
    await manager.run('test',p)
    assert manager.jobs['test']['status']=='failed'
    assert 'result' not in manager.jobs['test']


@pytest.mark.asyncio
async def test_stale_worker_evidence_is_rejected(monkeypatch,tmp_path):
    manager=api.Jobs();p=payload()
    req=GliderMissionRequirements.model_validate(p['requirements'])
    manager.jobs['test']=dict(directory=tmp_path,status='running',identity=identity(req,p['calibration']))
    (tmp_path/'result.json').write_text(json.dumps(dict(output_id='stale',candidates=[])))
    async def success(*a,**kw): return dict(code=None,returncode=0,stderr='',stdout='')
    monkeypatch.setattr(api.ProcessRunner,'run',success)
    await manager.run('test',p)
    assert manager.jobs['test']['status']=='failed'


@pytest.mark.asyncio
async def test_correct_identity_does_not_replace_missing_closure_evidence(monkeypatch,tmp_path):
    manager=api.Jobs();p=payload();p['kind']='design';p['elite']={'elite_id':'a'*16,'process':'balsa'}
    req=GliderMissionRequirements.model_validate(p['requirements']); ids=identity(req,p['calibration'])
    manager.jobs['test']=dict(directory=tmp_path,status='running',identity=ids)
    report=dict(**ids, requirements=p['requirements'], calibration=p['calibration'],
        status='screened',accepted_for_manufacture=False,engineering_certified=False,elite_id='a'*16,process='balsa')
    (tmp_path/'result.json').write_text(json.dumps(report))
    async def success(*a,**kw): return dict(code=None,returncode=0,stderr='',stdout='')
    monkeypatch.setattr(api.ProcessRunner,'run',success)
    await manager.run('test',p)
    assert manager.jobs['test']['status']=='failed' and 'result' not in manager.jobs['test']


@pytest.mark.asyncio
async def test_cem_and_legacy_jobs_share_one_solver_slot(monkeypatch,tmp_path):
    from gliders.api import Jobs as LegacyJobs
    from gliders.spec import GliderRequest
    from fastapi import HTTPException
    monkeypatch.setenv('MIRUM_WORK_DIR',str(tmp_path))
    cem,legacy=api.Jobs(),LegacyJobs()
    async def hold(*a): await asyncio.sleep(100)
    monkeypatch.setattr(cem,'run',hold);monkeypatch.setattr(legacy,'run',hold)
    cem.start(payload())
    try:
        with pytest.raises(HTTPException) as error: legacy.start(GliderRequest())
        assert error.value.status_code==429
    finally: await cem.close()
    legacy.start(GliderRequest())
    try:
        with pytest.raises(HTTPException): cem.start(payload())
    finally: await legacy.close()


def test_comparison_requires_an_archived_shaped_elite_and_preserves_inputs(monkeypatch, tmp_path):
    from backend.tests.test_cem_comparison import shaped_example, elite_for
    req, _, gl = shaped_example()
    elite = elite_for(gl)
    source = dict(requirements=req.model_dump(mode='json'), calibration=initial(), candidates=[],
                  per_process={'sheet_foam': {'archive': [elite]}})
    manager = api.Jobs()
    manager.jobs['a'*32] = dict(status='completed', kind='discovery', result=source)
    submitted = []
    def start(value):
        submitted.append(value)
        return dict(job_id='b'*32, status='running')
    monkeypatch.setattr(manager, 'start', start)
    with TestClient(app_for(manager, monkeypatch)) as client:
        base = dict(discovery_job_id='a'*32, elite_id=elite['elite_id'], independent_recheck=True)
        assert client.post('/cem/glider/compare-shape', json=base).status_code == 202
        assert submitted[-1] == dict(kind='comparison', requirements=source['requirements'],
            calibration=source['calibration'], elite=elite, independent_recheck=True)
        assert client.post('/cem/glider/compare-shape', json=dict(base, elite_id='f'*16)).status_code == 404
        assert client.post('/cem/glider/compare-shape', json=dict(base, independent_recheck='true')).status_code == 422
        del elite['parameters']['wing']['chord_factors']
        assert client.post('/cem/glider/compare-shape', json=base).status_code == 422
