"""Provider-free checked beam form and bounded AI review over /engineering/v1."""
from datetime import datetime,timezone
import hashlib
import io
import json
import math

import requests
import streamlit as st

from engineering_state import CheckedState,checked_bytes,merge_form,identity

FORMATS={'zip':('package.zip','application/zip'),'brief':('brief.json','application/json'),
    'spec':('selected_spec.json','application/json'),'source':('model.py','text/plain'),
    'step':('model.step','application/step'),'glb':('preview.glb','model/gltf-binary'),
    'attempts':('attempts.json','application/json'),'report':('report.json','application/json'),
    'markdown':('report.md','text/markdown'),'manifest':('manifest.json','application/json')}
REFERENCE=dict(requirements=dict(units='mm_N_MPa',length_mm=200.,width_mm=10.,dimensional_tolerance_mm=.01,
    tip_force_n=50.,max_tip_deflection_mm=1.,material=dict(youngs_modulus_mpa=70000.,allowable_stress_mpa=120.,
    source_kind='illustrative',reference='Illustrative material inputs; not certified stock'),
    assumptions='ideal_clamp_end_force_linear_elastic'),mode='bounded_thickness',candidate_heights_mm=[5.,10.,15.])


class APIError(ValueError):
    def __init__(self,status,message,body=None):
        self.status,self.body=status,body
        super().__init__(message)


class CheckedClient:
    def __init__(self,backend,headers): self.backend,self.headers=backend.rstrip('/')+'/engineering/v1',headers

    def request(self,method,path,body=None,*,binary=False):
        try:
            response=requests.request(method,self.backend+path,headers=self.headers(),json=body,timeout=(5,100))
        except requests.RequestException:
            raise APIError(503,'Checked engineering could not be reached. Your reviewed inputs are retained.') from None
        if not response.ok:
            try: value=response.json()
            except ValueError: value={}
            message={409:'This revision changed or conflicts. Load the latest draft and review it.',
                429:'Engineering capacity is busy. Retry after the current job finishes.',410:'This record or package expired. Create and review a new request.',
                503:'A required service is unavailable. Typed preparation remains available.',422:'Resolve the invalid or unsupported input before review.'}.get(response.status_code,'Checked engineering record or download was not found.')
            raise APIError(response.status_code,message,value)
        if binary:
            value=response.content
            if hashlib.sha256(value).hexdigest()!=response.headers.get('X-Content-SHA256'):
                raise APIError(503,'Download fingerprint mismatch. No file is offered.')
            return value
        return response.json()


def _remember(state):
    if state.prepared: st.query_params['checked_draft']=state.prepared['draft']['draft_id']
    if state.brief: st.query_params['checked_brief']=state.brief['brief_id']
    elif 'checked_brief' in st.query_params: del st.query_params['checked_brief']
    if state.job_id: st.query_params['checked_job']=state.job_id


def _clear_files(job_id):
    cache=st.session_state.setdefault('checked_files',{})
    for key in list(cache):
        if key[0]==job_id: del cache[key]


def _preview(value,job):
    import numpy as np
    import plotly.graph_objects as go
    import trimesh
    scene=trimesh.load(io.BytesIO(value),file_type='glb',force='scene')
    mesh=scene.dump(concatenate=True)
    vertices=np.asarray(mesh.vertices)
    # CadQuery GLB uses glTF Y-up: (X, Z, -Y). Display CAD XYZ in mm.
    x,y,z=vertices[:,0],-vertices[:,2],vertices[:,1]
    faces=np.asarray(mesh.faces)
    fig=go.Figure(go.Mesh3d(x=x,y=y,z=z,i=faces[:,0],j=faces[:,1],k=faces[:,2],
        color='#4c9bc7',flatshading=True,opacity=.95,name='Measured STEP preview'))
    spec=job['attempts'][-1]['candidate']['specification']
    length,width,height=spec['length_mm'],spec['width_mm'],spec['height_mm']
    top=height+max(height*2,length*.15)
    fig.add_trace(go.Scatter3d(x=[length,length],y=[width/2,width/2],z=[height/2,top],mode='lines+text',
        line=dict(color='#bd4b36',width=6),text=['',f"+Z end force: {spec['tip_force_n']:g} N"],textposition='top center',name='Reviewed force'))
    fig.add_trace(go.Cone(x=[length],y=[width/2],z=[top],u=[0],v=[0],w=[1],sizemode='absolute',
        sizeref=max(height*.4,2),colorscale=[[0,'#bd4b36'],[1,'#bd4b36']],showscale=False,name='+Z'))
    fig.add_trace(go.Scatter3d(x=[0]*5,y=[0,width,width,0,0],z=[0,0,height,height,0],mode='lines',
        line=dict(color='#344453',width=8),name='Ideal clamp X=0'))
    fig.update_layout(height=430,margin=dict(l=0,r=0,b=0,t=20),showlegend=False,
        scene=dict(aspectmode='manual',aspectratio=dict(x=2.3,y=2.3*width/length,z=2.3*top/length),
                   xaxis_title='X (mm)',yaxis_title='Y (mm)',zaxis_title='Z (mm)',
                   camera=dict(eye=dict(x=1.5,y=-1.5,z=1.1))))
    st.plotly_chart(fig,use_container_width=True,key='checked_preview_'+job['job_id'])
    st.caption('Actual captured GLB from the measured STEP. Arrows and clamp show the reviewed digital beam model.')


def _result(client,state):
    if not state.job_id: return
    try: job=client.request('GET','/jobs/'+state.job_id)
    except APIError as error:
        _clear_files(state.job_id); st.error(str(error)); return
    st.session_state['checked_current_job']=job
    st.subheader('Checked result')
    st.caption('Job '+job['job_id']+' | brief SHA-256 '+job['brief_sha256'])
    if state.prepared and state.prepared.get('brief_sha256')!=job['brief_sha256']:
        st.info('This result belongs to the earlier reviewed design. Current edits need a new review and do not change this job, preview or report.')
    if job.get('expired') or job.get('delivery')=='expired':
        _clear_files(state.job_id); st.warning('Expired. The six-hour download window ended. Review a new request to generate again.'); return
    active=job['lifecycle'] not in ('completed','unavailable','cancelled')
    st.write('State: **'+job['lifecycle']+'** | engineering outcome: **'+job['outcome']+'**')
    if active:
        st.info('Native checks are running for this immutable reviewed brief. Refresh preserves this job identity.')
        if st.button('Cancel current job',key='checked_cancel'):
            try: client.request('POST','/jobs/'+state.job_id+'/cancel'); st.rerun()
            except APIError as error: st.error(str(error))
    elif job['outcome']=='accepted':
        st.success('Accepted under the declared digital beam model. Smallest passing thickness in the reviewed set.')
    elif job['outcome']=='rejected':
        st.warning('No reviewed candidate passed. The report gives the measured reasons; no accepted geometry is offered.')
    elif job['lifecycle']=='cancelled':
        st.warning('Cancelled before terminal commit. No accepted package is offered.')
    else: st.error('A required execution or publication stage is unavailable. No accepted package is offered.')
    for reason in job.get('failures',[]): st.write(reason)
    attempts=job.get('attempts',[])
    if attempts:
        st.dataframe([dict(thickness_mm=a['candidate']['specification']['height_mm'],outcome=a['outcome'],
            reasons='; '.join(a['failures']) or 'All mandatory digital checks passed') for a in attempts],hide_index=True,use_container_width=True)
    if active: return
    if job.get('delivery')!='available':
        _clear_files(state.job_id)
        if job['outcome']=='accepted': st.error('Required package integrity or storage is unavailable. Downloads and preview are withheld.')
        return
    cache=st.session_state.setdefault('checked_files',{})
    seal=(job.get('bundle') or {}).get('manifest_sha256','diagnostic')
    files={}
    try:
        for format,available in job.get('artifacts',{}).items():
            if not available: continue
            name,mime=FORMATS[format]; key=(state.job_id,seal,format)
            if key not in cache:
                value=client.request('GET',f'/jobs/{state.job_id}/artifacts/{format}',binary=True)
                if job['outcome']=='accepted': checked_bytes(job,name,value)
                cache[key]=value
            files[format]=cache[key]
    except (APIError,ValueError) as error:
        _clear_files(state.job_id); st.error(str(error)); return
    if job['outcome']=='accepted':
        try: _preview(files['glb'],job)
        except Exception:
            st.error('The captured preview could not be displayed. The verified GLB remains available for inspection.')
    if job.get('artifact_expires_at'):
        expiry=datetime.fromtimestamp(job['artifact_expires_at'],timezone.utc)
        st.caption('Available until '+expiry.isoformat()+' (UTC). Active downloads finish safely.')
    st.write('Standards conformity, manufactured behavior and physical material qualification: **not evaluated**.')
    if 'markdown' in files:
        with st.expander('Read report',expanded=False): st.text(files['markdown'].decode('utf-8'))
    st.subheader('Revision-bound downloads')
    columns=st.columns(3)
    for n,(format,value) in enumerate(files.items()):
        name,mime=FORMATS[format]
        columns[n%3].download_button('Download '+name,value,file_name=name,mime=mime,
            key=f'checked_download_{state.job_id}_{format}')


def render_checked(backend,headers):
    st.header('Checked beam')
    st.caption('One solid rectangular cantilever. Review requirements, run deterministic checks, inspect one complete evidence package.')
    st.info('Digital model only: ideal clamp, homogeneous linear elasticity, static end force. No physical testing, manufacturing release or standards certification.')
    client=CheckedClient(backend,headers)
    state=st.session_state.setdefault('checked_state',CheckedState())
    if state.job_id is None: state.job_id=st.query_params.get('checked_job')
    st.query_params['checked_workflow']='1'
    try:
        metadata=st.session_state.get('checked_capabilities')
        if metadata is None:
            metadata=client.request('GET','/capabilities'); st.session_state['checked_capabilities']=metadata
        if state.prepared is None and st.query_params.get('checked_draft'):
            state.adopt_draft(client.request('GET','/drafts/'+st.query_params['checked_draft']))
        if state.prepared:
            state.observe(client.request('GET','/drafts/'+state.prepared['draft']['draft_id']))
        if state.brief is None and not state.stale and st.query_params.get('checked_brief'):
            try: state.reviewed(client.request('GET','/briefs/'+st.query_params['checked_brief']))
            except ValueError:
                if 'checked_brief' in st.query_params: del st.query_params['checked_brief']
        if state.job_id is None: state.job_id=st.query_params.get('checked_job')
    except APIError as error:
        st.error(str(error))
        if error.status==410:
            state.prepared=None; state.brief=None
            for key in ('checked_draft','checked_brief'):
                if key in st.query_params: del st.query_params[key]
        else: return
    descriptor=metadata['capabilities'][0]
    if state.stale:
        st.warning('This draft changed in another tab. Load the latest revision before editing, confirming or running it.')
        if st.button('Load latest revision'):
            state.adopt_draft(client.request('GET','/drafts/'+state.prepared['draft']['draft_id'])); _remember(state); st.rerun()
    if st.button('Start a new typed request',help='Creates a separate supported request. Prior requirements are retained in their old revision; the current job remains available for cancellation.'):
        state.prepared=None; state.brief=None; state.stale=False
        for key in ('checked_draft','checked_brief'):
            if key in st.query_params: del st.query_params[key]
        st.session_state['checked_entry']='Typed form'; st.rerun()
    entry=st.radio('Entry method',['Typed form','AI-assisted draft'],horizontal=True,key='checked_entry')
    if not metadata['ai_available']: st.caption('AI is unavailable. The typed form works without provider credentials.')
    if entry=='AI-assisted draft':
        prompt=st.text_area('Engineering request',max_chars=8000,key='checked_prompt',
            help='Include all dimensions, force, material source and limits. Unsupported requirements remain visible and block execution.')
        if st.button('Propose draft',disabled=not prompt.strip()):
            try:
                prepared=client.request('POST','/drafts',dict(mode='ai',input={},prompt=prompt))
                state.adopt_draft(prepared); _remember(state); st.rerun()
            except APIError as error:
                if error.body and error.body.get('prepared'):
                    state.adopt_draft(error.body['prepared']); _remember(state)
                st.error(str(error))
        st.caption('AI only proposes data. It cannot finalize a brief or execute geometry.')
    prepared=state.prepared
    issues=prepared.get('issues',[]) if prepared else []
    for issue in issues:
        (st.error if issue['state']=='unsupported' else st.warning)(issue['field']+': '+issue['reason'])
    blocked=any(i['state']=='unsupported' for i in issues)
    if blocked: st.error('Unsupported requirements cannot be removed by saving this form. Start a new typed request only if you intend a separate supported design.')
    resolved=st.checkbox('I resolved the listed clarification questions in these typed fields.',key='checked_resolved_'+str(identity(state.prepared))) if issues and not blocked else True
    original=prepared['input'] if prepared else REFERENCE
    req=original.get('requirements',{}); material=req.get('material',{})
    epoch='new' if not prepared else prepared['draft']['draft_id']+'_'+str(prepared['draft']['revision'])
    with st.form('checked_form_'+epoch):
        st.subheader('1. Prepare requirements')
        if prepared is None: st.caption('Reference example with illustrative material values. Review or replace every value.')
        columns=st.columns(3); values={}
        fields=[('length_mm','Length (mm)'),('width_mm','Width (mm)'),('tip_force_n','Tip force (+Z, N)'),
            ('max_tip_deflection_mm','Maximum tip displacement (mm)'),('dimensional_tolerance_mm','Digital dimensional tolerance (mm)')]
        for n,(key,label) in enumerate(fields):
            value=req.get(key); value=float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) else None
            values[key]=columns[n%3].number_input(label,value=value,format='%.5f',key='checked_'+epoch+'_'+key)
        modulus=columns[2].number_input('Elastic modulus (MPa)',value=float(material['youngs_modulus_mpa']) if material.get('youngs_modulus_mpa') is not None else None,key='checked_'+epoch+'_modulus')
        allowable=columns[0].number_input('Allowable stress (MPa)',value=float(material['allowable_stress_mpa']) if material.get('allowable_stress_mpa') is not None else None,key='checked_'+epoch+'_allowable')
        kinds=['illustrative','user_supplied','documented']; kind=material.get('source_kind')
        source_kind=columns[1].selectbox('Material source kind',kinds,index=kinds.index(kind) if kind in kinds else None,
            format_func=lambda value:{'illustrative':'Illustrative example','user_supplied':'User supplied','documented':'Documented reference'}[value],key='checked_'+epoch+'_kind')
        reference=st.text_input('Material / allowable source reference',value=material.get('reference',''),max_chars=240,key='checked_'+epoch+'_reference')
        modes=['bounded_thickness','fixed_design']; mode=original.get('mode')
        mode=st.selectbox('Design mode',modes,index=modes.index(mode) if mode in modes else None,
            format_func=lambda value:'Choose the smallest passing reviewed thickness' if value=='bounded_thickness' else 'Check one reviewed thickness',key='checked_'+epoch+'_mode')
        choices=st.text_input('Thickness choices to review (mm, comma-separated; 1 to 4)',
            value=', '.join(str(h) for h in original.get('candidate_heights_mm',[])),key='checked_'+epoch+'_choices')
        st.code('Ideal clamp X=0  |====================| X=L\n                                        +Z end force',language=None)
        save=st.form_submit_button('Save draft',disabled=state.stale or blocked or not resolved)
    if save:
        try:
            heights=[float(value.strip()) for value in choices.split(',')]
            if not all(math.isfinite(h) for h in heights): raise ValueError('Thicknesses must be finite')
            payload=merge_form(original,dict(schema_version='engineering_brief_v1',
                capability=dict(id=descriptor['id'],version=descriptor['version'],numerical_policy_version=descriptor['numerical']['version'],resource_policy_version=descriptor['resources']['version']),
                requirements=dict(values,units='mm_N_MPa',assumptions='ideal_clamp_end_force_linear_elastic',
                    material=dict(youngs_modulus_mpa=modulus,allowable_stress_mpa=allowable,source_kind=source_kind,reference=reference)),
                mode=mode,candidate_heights_mm=heights,required_checks=descriptor['required_checks']))
            if prepared:
                result=client.request('PATCH','/drafts/'+prepared['draft']['draft_id'],dict(expected_revision=prepared['draft']['revision'],input=payload))
            else: result=client.request('POST','/drafts',dict(input=payload))
            state.adopt_draft(result); _remember(state); st.rerun()
        except (APIError,ValueError) as error:
            if isinstance(error,APIError) and error.status==409: state.stale=True; state.brief=None
            st.error(str(error))
    prepared=state.prepared
    if prepared:
        st.subheader('2. Review the exact draft')
        draft=prepared['draft']; st.caption(f"Draft {draft['draft_id']} | revision {draft['revision']} | brief SHA-256 {prepared.get('brief_sha256')}")
        content=draft.get('content')
        if content:
            req=content['requirements']; material=req['material']
            rows=[('Length',str(req['length_mm'])+' mm'),('Width',str(req['width_mm'])+' mm'),
                ('Digital dimensional tolerance',str(req['dimensional_tolerance_mm'])+' mm'),
                ('Static +Z end force at X=L',str(req['tip_force_n'])+' N'),('Maximum tip displacement',str(req['max_tip_deflection_mm'])+' mm'),
                ('Elastic modulus',str(material['youngs_modulus_mpa'])+' MPa'),('Allowable stress',str(material['allowable_stress_mpa'])+' MPa'),
                ('Material source kind',material['source_kind']),('Support / assumptions','Ideal clamp X=0; homogeneous linear elasticity'),
                ('Reviewed thickness set',', '.join(str(h) for h in content['candidate_heights_mm'])+' mm'),
                ('Selection','Smallest passing thickness in the reviewed set' if content['mode']=='bounded_thickness' else 'One fixed thickness')]
            st.dataframe([{'Requirement':name,'Value to confirm':value} for name,value in rows],hide_index=True,use_container_width=True)
            st.text('Material / allowable source: '+material['reference'])
            with st.expander('Complete normalized input and required checks'): st.json(content)
        else: st.json(prepared['input'])
        with st.expander('Input provenance and unresolved requirements'):
            st.json(dict(provenance=draft['provenance'],issues=prepared['issues']))
        complete=bool(draft.get('content') and not prepared['issues'] and not state.stale)
        ack=st.checkbox('I reviewed every value, the normalized thickness set, material source, ideal clamp, +Z end force and all assumptions.',key='checked_ack_'+epoch,disabled=not complete)
        if st.button('Finalize reviewed brief',disabled=not complete or not ack or state.can_start):
            try:
                brief=client.request('POST','/briefs',dict(draft_id=draft['draft_id'],draft_revision=draft['revision'],brief_sha256=prepared['brief_sha256'],
                    acknowledged_fields=metadata['review_fields'],reviewed_at=datetime.now(timezone.utc).isoformat(),reviewed_by='local browser reviewer'))
                state.reviewed(brief); _remember(state); st.rerun()
            except (APIError,ValueError) as error: st.error(str(error))
    st.subheader('3. Run the reviewed design')
    if state.brief: st.caption('Finalized brief '+state.brief['brief_id']+' | '+state.brief['brief_sha256'])
    if st.button('Run reviewed beam checks',disabled=not state.can_start):
        try:
            job=client.request('POST','/jobs',dict(brief_id=state.brief['brief_id'],idempotency_key='checked-ui:'+state.brief['brief_id']))
            state.job_id=job['job_id']; _remember(state); st.rerun()
        except APIError as error: st.error(str(error))
    @st.fragment(run_every='1s')
    def result_fragment(): _result(client,state)
    result_fragment()
