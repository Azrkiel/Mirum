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


@pytest.fixture
def ai_client(tmp_path,monkeypatch,offline_models):
    """Same app with MIRUM_ENGINEERING_AI_DRAFTS on (config constant, read at startup)."""
    import config
    monkeypatch.setattr(config,'ENGINEERING_AI_DRAFTS',True)
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR',str(tmp_path/'engineering'))
    with TestClient(main.app) as c:
        main.app.state.limiter.reset()
        yield c


def finalized(client,payload=None):
    response=client.post('/engineering/v1/drafts',json={'input':payload or data()['content']})
    assert response.status_code==201,response.text
    prepared=response.json()
    assert len(prepared['brief_sha256'])==64
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
    pub=main.app.state.engineering.publisher
    with pub.lease(job['job_id'],'zip') as held:
        original_expiry=pub._publication(job['job_id'])[1]
        with main.app.state.engineering.store.transaction() as db:
            db.execute('UPDATE publications SET expires=? WHERE job=?',(time.time()-1,job['job_id']))
        try:
            assert client.get('/engineering/v1/jobs/'+job['job_id']).json()['delivery']=='expired'
            assert held.file.read(2)==b'PK'
            assert client.get(f"/engineering/v1/jobs/{job['job_id']}/artifacts/zip").status_code==410
        finally:
            with main.app.state.engineering.store.transaction() as db:
                db.execute('UPDATE publications SET expires=? WHERE job=?',(original_expiry,job['job_id']))
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


def test_no_provider_typed_route_and_ai_failure_remains_reviewable(ai_client,monkeypatch):
    client=ai_client
    monkeypatch.delenv('GEMINI_API_KEY_HEAVY',raising=False)
    finalized(client)
    response=client.post('/engineering/v1/drafts',json={'mode':'ai','prompt':'Describe a beam','input':{}})
    assert response.status_code==503,response.text
    value=response.json()
    assert value['prepared']['draft']['content'] is None
    assert client.get('/engineering/v1/drafts/'+value['prepared']['draft']['draft_id']).status_code==200


def test_ai_fixture_uses_same_review_service_and_semantic_identity(ai_client):
    client=ai_client
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


def test_ai_flag_off_rejects_ai_mode_without_provider_or_key(client,monkeypatch):
    from engineering import proposer as proposer_module
    touched=[]
    def forbid(name):
        def fail(*args,**kwargs): touched.append(name); raise AssertionError(name)
        return fail
    monkeypatch.setattr(proposer_module.GeminiProposalProvider,'__init__',forbid('init'))
    monkeypatch.setattr(proposer_module.GeminiProposalProvider,'available',property(forbid('available')))
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY','must-not-be-read')
    assert main.app.state.engineering.proposer is None
    response=client.post('/engineering/v1/drafts',json={'mode':'ai','prompt':'Describe a beam','input':{}})
    assert response.status_code==503
    assert response.json()['detail']=='AI draft preparation is not enabled'
    capabilities=client.get('/engineering/v1/capabilities').json()
    assert capabilities['ai_enabled'] is False and capabilities['ai_available'] is False
    assert touched==[]


def test_ai_flag_off_leaves_form_mode_and_legacy_generate_unchanged(client):
    prepared,brief=finalized(client)
    assert prepared['draft']['revision']==1 and brief['brief_id']
    assert '/engineering/generate' in {getattr(route,'path',None) for route in main.app.routes}


def test_ai_flag_on_reports_enabled_and_availability_follows_key(ai_client,monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY','fixture-not-sent')
    on=ai_client.get('/engineering/v1/capabilities').json()
    assert on['ai_enabled'] is True and on['ai_available'] is True
    monkeypatch.delenv('GEMINI_API_KEY_HEAVY')
    assert ai_client.get('/engineering/v1/capabilities').json()['ai_available'] is False


def test_capabilities_keep_the_beam_first_and_add_review_fields_and_availability(client):
    value=client.get('/engineering/v1/capabilities').json()
    entries=value['capabilities']
    assert [e['id'] for e in entries]==['rectangular_cantilever','co2_dragster','regen_chamber','glider_cem']
    from engineering.contracts import REVIEW_FIELDS
    assert value['review_fields']==list(REVIEW_FIELDS) and entries[0]['review_fields']==list(REVIEW_FIELDS)
    assert entries[0]['version']=='1.0.0' and entries[0]['required_checks']
    for entry in entries[1:]:
        assert entry['engineering_certified'] is False and entry['standards']=='not_evaluated'
        assert entry['review_fields']==[] and entry['review_fields_prefix']=='specification.'
        assert entry['resources']['overall_seconds']==420 and entry['resources']['run_bytes']==32*1024**2
    from engineering.capabilities import GENERATORS,adapter_installed
    assert [e['available'] for e in entries[1:]]==[adapter_installed(g) for g in GENERATORS]


def test_generator_draft_refuses_unsupported_and_rules_mode_needs_a_declared_proposer(client):
    from backend.tests.test_engineering_generator_contracts import regen_spec,reference
    from engineering.capabilities import get_generator
    ref=reference(get_generator('regen_chamber'))
    refused=client.post('/engineering/v1/drafts',json={'capability':'regen_chamber',
        'input':{'capability':ref,'specification':regen_spec(voxel_size_mm=0.2)}})
    assert refused.status_code==201,refused.text
    body=refused.json()
    assert body['draft']['content'] is None and [i['state'] for i in body['issues']]==['unsupported']
    ok=client.post('/engineering/v1/drafts',json={'capability':'regen_chamber',
        'input':{'capability':ref,'specification':regen_spec()}})
    assert ok.status_code==201 and ok.json()['draft']['content']['schema_version']=='engineering_generator_brief_v1'
    assert len(ok.json()['brief_sha256'])==64
    for body in ({'capability':'nope','input':{}},
                 {'capability':'regen_chamber','input':{}},
                 {'capability':'regen_chamber','mode':'ai','prompt':'x','input':{}},
                 {'capability':'regen_chamber','mode':'rules','prompt':'x','input':{}},
                 {'capability':'rectangular_cantilever','mode':'rules','prompt':'x','input':{}}):
        assert client.post('/engineering/v1/drafts',json=body).status_code==422,body
