"""Public checked workflow: real native TestClient, offline AI explicitly labelled."""
import hashlib
import io
import json
import time
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend.tests.test_native_api_hardening_ports import main,offline_models
from backend.tests.test_engineering_contracts import data,review_for
from engineering.contracts import BriefDraft


@pytest.fixture
def client(tmp_path,monkeypatch,offline_models):
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR',str(tmp_path/'engineering'))
    with TestClient(main.app) as c:
        main.app.state.limiter.reset()
        yield c


def finalized(client,payload=None):
    response=client.post('/engineering/v1/drafts',json={'input':payload or data()['content']})
    assert response.status_code==201,response.text
    prepared=response.json()
    review=review_for(BriefDraft.model_validate_json(json.dumps(prepared['draft'])))
    response=client.post('/engineering/v1/briefs',json=review.model_dump(mode='json'))
    assert response.status_code==201,response.text
    return prepared,response.json()


def poll(client,job_id):
    for _ in range(500):
        response=client.get('/engineering/v1/jobs/'+job_id)
        assert response.status_code==200,response.text
        value=response.json()
        if value['lifecycle'] in ('completed','unavailable','cancelled'): return value
        time.sleep(.1)
    pytest.fail('Job did not reach terminal state')


def test_real_native_http_contract_and_allowlisted_downloads(client,monkeypatch):
    from process_runner import ProcessRunner
    original=ProcessRunner.run
    calls=[]
    async def observe(self,command,**kwargs):
        calls.append(tuple(command))
        return await original(self,command,**kwargs)
    monkeypatch.setattr(ProcessRunner,'run',observe)
    assert client.get('/engineering/v1/capabilities').status_code==200
    draft,brief=finalized(client)
    body={'brief_id':brief['brief_id'],'idempotency_key':'native'}
    response=client.post('/engineering/v1/jobs',json=body)
    assert response.status_code==202,response.text
    job=response.json()
    from backend.tests.test_engineering_spec import spec_data
    busy=client.post('/engineering/generate',json={'specification':spec_data()})
    assert busy.status_code==429 and busy.headers['retry-after']=='5'
    assert client.post('/engineering/v1/jobs',json=body).json()['job_id']==job['job_id']
    assert client.post('/engineering/v1/jobs',json=dict(body,specification={})).status_code==422
    result=poll(client,job['job_id'])
    assert result['outcome']=='accepted' and result['delivery']=='available',result
    executions=len(calls)
    assert [a['candidate']['specification']['height_mm'] for a in result['attempts']]==[5,10,15]
    from engineering.bundles import FORMATS
    values={}
    for format,(name,mime) in FORMATS.items():
        r=client.get(f"/engineering/v1/jobs/{job['job_id']}/artifacts/{format}")
        assert r.status_code==200,r.text
        assert r.headers['cache-control']=='no-store'
        assert r.headers['content-type'].startswith(mime)
        assert hashlib.sha256(r.content).hexdigest()==r.headers['x-content-sha256']
        values[name]=r.content
    with zipfile.ZipFile(io.BytesIO(values['package.zip'])) as z:
        for name in z.namelist(): assert z.read(name)==values[name]
    assert client.post('/engineering/v1/jobs/'+job['job_id']+'/cancel').json()['outcome']=='accepted'
    assert len(calls)==executions
    assert client.get(f"/engineering/v1/jobs/{job['job_id']}/artifacts/unknown").status_code==404
    # Integrity failure is unavailable delivery, never a misleading file.
    pub=main.app.state.engineering.publisher
    (pub.directory(job['job_id'])/'report.json').write_bytes(b'wrong')
    assert client.get(f"/engineering/v1/jobs/{job['job_id']}/artifacts/zip").status_code==503
    with main.app.state.engineering.store.transaction() as db:
        db.execute('UPDATE jobs SET expires=?',(time.time()-1,))
    assert client.get('/engineering/v1/jobs/'+job['job_id']).json()['delivery']=='expired'
    assert client.get(f"/engineering/v1/jobs/{job['job_id']}/artifacts/zip").status_code==410


def test_revision_conflicts_missing_inputs_and_auth(client,monkeypatch):
    prepared,brief=finalized(client)
    key=prepared['draft']['draft_id']
    change={'expected_revision':1,'input':data()['content']}
    assert client.patch('/engineering/v1/drafts/'+key,json=change).status_code==200
    assert client.patch('/engineering/v1/drafts/'+key,json=change).status_code==409
    stale=review_for(BriefDraft.model_validate_json(json.dumps(prepared['draft'])))
    assert client.post('/engineering/v1/briefs',json=stale.model_dump(mode='json')).status_code==409
    assert client.get('/engineering/v1/briefs/'+brief['brief_id']).json()==brief
    incomplete=client.post('/engineering/v1/drafts',json={'input':{}}).json()
    assert incomplete['issues'] and incomplete['draft']['content'] is None
    assert client.post('/engineering/v1/jobs',json={'brief_id':incomplete['draft']['draft_id'],'idempotency_key':'unreviewed'}).status_code==404
    monkeypatch.setenv('MIRUM_ACCESS_TOKEN','fixture-access')
    assert client.get('/engineering/v1/capabilities').status_code==401
    assert client.get('/engineering/v1/capabilities',headers={'X-Mirum-Access-Token':'fixture-access'}).status_code==200


def test_no_provider_typed_route_and_ai_failure_remains_reviewable(client,monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY_HEAVY',raising=False)
    finalized(client)
    response=client.post('/engineering/v1/drafts',json={'mode':'ai','prompt':'Describe a beam','input':{}})
    assert response.status_code==503,response.text
    value=response.json()
    assert value['prepared']['draft']['content'] is None
    assert client.get('/engineering/v1/drafts/'+value['prepared']['draft']['draft_id']).status_code==200


def test_ai_fixture_uses_same_review_service_and_semantic_identity(client):
    from engineering.proposer import ProviderReply,ProviderSettings
    corpus=json.loads(__import__('pathlib').Path('backend/tests/fixtures/engineering/ai-corpus-v1.json').read_text())
    cases=corpus['cases'] if isinstance(corpus,dict) else corpus
    case=cases[0]
    class Fixture:
        available=True
        def settings(self): return ProviderSettings(provider='fixture',sdk_version='fixture',model='fixture',temperature=0.,max_tokens=4096)
        async def generate(self,*args): return ProviderReply(text=json.dumps(case['fixture_response']))
    main.app.state.engineering.proposer.provider=Fixture()
    response=client.post('/engineering/v1/drafts',json={'mode':'ai','prompt':case['prompt'],'input':{}})
    assert response.status_code==201,response.text
    draft=BriefDraft.model_validate_json(json.dumps(response.json()['draft']))
    review=review_for(draft)
    ai=client.post('/engineering/v1/briefs',json=review.model_dump(mode='json')).json()
    _,typed=finalized(client,case['expected_content'])
    assert ai['brief_sha256']==typed['brief_sha256']


def test_http_cancel_overload_conflicting_idempotency_and_no_partial_download(client):
    _,brief=finalized(client)
    body={'brief_id':brief['brief_id'],'idempotency_key':'controlled'}
    job=client.post('/engineering/v1/jobs',json=body).json()
    assert client.get(f"/engineering/v1/jobs/{job['job_id']}/artifacts/zip").status_code==409
    assert client.post('/engineering/v1/jobs',json=dict(body,idempotency_key='overload')).status_code==429
    _,other=finalized(client)
    assert client.post('/engineering/v1/jobs',json=dict(body,brief_id=other['brief_id'])).status_code==409
    assert client.post('/engineering/v1/jobs/'+job['job_id']+'/cancel').status_code==200
    assert poll(client,job['job_id'])['lifecycle']=='cancelled'
    assert client.get(f"/engineering/v1/jobs/{job['job_id']}/artifacts/zip").status_code==404
    assert client.get('/engineering/v1/jobs/unknown').status_code==404


def test_public_rate_limit_applies_without_native_execution(client,monkeypatch):
    monkeypatch.setattr(main.limiter,'enabled',True)
    main.limiter.reset()
    codes=[client.get('/engineering/v1/capabilities').status_code for _ in range(31)]
    assert codes.count(200)==30 and codes[-1]==429
