"""Checked workflow transport. Physical inputs enter only through review."""
import asyncio
import importlib.util
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal,Annotated

from fastapi import APIRouter,HTTPException,Request,Response
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse,StreamingResponse
from pydantic import Field,BeforeValidator,field_validator

from .admission import AdmissionBusy,ClientFairness
from .briefs import BriefService,RevisionConflict
from .bundles import BundlePublisher,FORMATS,ALL_FORMATS
from .capabilities import CAPABILITY,CapabilityDescriptor,adapter_installed,get_capability,get_generator,list_capabilities,resolve
from .contracts import ContractModel,ReviewAcknowledgement,Text,REVIEW_FIELDS
from .jobs import JobController,compact_run
from .limits import AiCallBudget,run_in_thread_owned
from .proposer import make_proposer,rules_proposer
from .store import SQLiteStore,SQLiteBriefRepository,SQLiteProposalLedger,ArtifactExpired,PreparationQuota

MAX_INPUT_BYTES=16*1024  # the reviewed brief bound; enforced on the model, after the body cap


def _bounded_input(value):
    if len(json.dumps(value,allow_nan=False,separators=(',',':')).encode())>MAX_INPUT_BYTES:
        raise ValueError('Engineering input exceeds 16 KiB')
    return value


class DraftCreate(ContractModel):
    mode: Literal['form','ai','rules']='form'
    capability: str=Field(default=CAPABILITY.id,min_length=1,max_length=64)
    input: dict = Field(default_factory=dict)
    prompt: str | None = Field(default=None,max_length=8000)

    _bound=field_validator('input')(_bounded_input)


class DraftEdit(ContractModel):
    expected_revision: int = Field(ge=1)
    input: dict

    _bound=field_validator('input')(_bounded_input)


class JobSubmit(ContractModel):
    brief_id: Text
    idempotency_key: str = Field(min_length=1,max_length=128)


def _review_json(value):
    # FastAPI decodes JSON into Python before Pydantic. Re-enter the shared
    # contract's JSON boundary so JSON arrays/dates work without relaxing its
    # strict Python constructor or any physical-number validation.
    if isinstance(value,ReviewAcknowledgement): value=value.model_dump(mode='json')
    return ReviewAcknowledgement.model_validate_json(json.dumps(value,allow_nan=False))


ReviewInput=Annotated[ReviewAcknowledgement,BeforeValidator(_review_json)]


async def limit_body(request):
    """Reject oversized bodies before JSON/model parsing: by Content-Length, and while
    reading when the length is absent or false. The bounded body is cached on the
    request, so the normal parser reads it without a second pass."""
    import config
    limit=config.ENGINEERING_MAX_BODY_BYTES
    declared=request.headers.get('content-length')
    if declared is not None:
        try: size=int(declared)
        except ValueError: raise HTTPException(400,'Invalid Content-Length') from None
        if size>limit: raise HTTPException(413,'Request body too large')
    received=bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received)>limit: raise HTTPException(413,'Request body too large')
    request._body=bytes(received)


class LeasedResponse(StreamingResponse):
    """Streams one leased file and releases the lease when the response ends for any
    reason, including client disconnect and cancellation (no reliance on GC)."""
    def __init__(self,download,**kwargs):
        self.download=download
        super().__init__(self._chunks(),**kwargs)

    def _chunks(self):
        try:
            while True:
                try: block=self.download.file.read(65536)
                except ValueError:  # closed by an abort while this read was queued
                    return
                if not block: break
                yield block
        finally: self.download.close()

    async def __call__(self,scope,receive,send):
        try: await super().__call__(scope,receive,send)
        finally: self.download.close()


class CheckedRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def guarded(request):
            try:
                if request.method in ('POST','PUT','PATCH'): await limit_body(request)
                response=await handler(request)
                response.headers['Cache-Control']='no-store'
                return response
            except ArtifactExpired:
                raise HTTPException(410,'Engineering record or artifacts expired') from None
            except RevisionConflict:
                raise HTTPException(409,'Revision or idempotency conflict; read the current record and review again') from None
            except AdmissionBusy as error:
                raise HTTPException(429,'Engineering capacity busy; retry later',headers={'Retry-After':str(error.retry_after)}) from None
            except KeyError:
                raise HTTPException(404,'Engineering record or format not found') from None
            except ValueError:
                raise HTTPException(422,'Invalid or unsupported engineering input') from None
            except (OSError,RuntimeError):
                raise HTTPException(503,'Required engineering service unavailable') from None
        return guarded


def generator_entry(c):
    """Explicit allowlist: no import strings, module names or source paths leave the server."""
    return dict(id=c.id,version=c.version,label=c.label,numerical_version=c.numerical_version,
        base_checks=list(c.base_checks),conditional_checks=[x.model_dump(mode='json') for x in c.conditional_checks],
        unsupported_features=list(c.unsupported_features),selection_rule=c.selection_rule,
        limitations=list(c.limitations),primary_artifact=c.primary_artifact,payloads=list(c.payloads),
        engineering_certified=c.engineering_certified,standards=c.standards,claim=c.claim,
        limits=[dict(name=r.name,value=r.value) for r in c.numerical+c.runtime_limits],
        numerical=dict(version=c.numerical_version),resources=c.resources.model_dump(mode='json'),
        available=adapter_installed(c),
        review_fields=[],review_fields_prefix='specification.')


def create_runtime(generators=None):
    """``generators``: ``{capability id: runner getter}``; getters are late-bound (the app owns the runners)."""
    root=Path(os.environ.get('MIRUM_ENGINEERING_DATA_DIR','/tmp/mirum/engineering'))
    scratch=Path(os.environ.get('MIRUM_WORK_DIR','/tmp/mirum/work')).resolve()
    resolved=root.resolve()
    # Explicit development roots permit isolated test volumes. Production requires
    # a mounted owned path under the permitted runtime tree, separate from scratch.
    if os.environ.get('MIRUM_ENGINEERING_REQUIRE_VOLUME','0')=='1':
        if Path('/tmp/mirum') not in resolved.parents or not os.path.ismount(resolved):
            raise RuntimeError('Engineering requires an owned mounted volume under /tmp/mirum')
    if resolved==scratch or scratch in resolved.parents or resolved in scratch.parents:
        raise RuntimeError('Engineering persistent data and disposable scratch must be separate')
    # Read once from config; off by default. Off means no provider object is built.
    import config
    ai_enabled=bool(config.ENGINEERING_AI_DRAFTS)
    store=SQLiteStore(root)
    try:
        publisher=BundlePublisher(store)
        briefs=BriefService(SQLiteBriefRepository(store))
        fairness=ClientFairness(config.ENGINEERING_JOBS_PER_CLIENT_HOUR,config.ENGINEERING_REFUSAL_MEMORY_SECONDS)
        adapters={}
        for capability_id,getter in (generators or {}).items():
            descriptor=get_generator(capability_id)
            if descriptor is None: raise ValueError('Unknown generator capability')
            adapters[capability_id]=resolve(descriptor.adapter)(getter)
        jobs=JobController(store,publisher=publisher,fairness=fairness,generators=adapters)
        ledger=SQLiteProposalLedger(store)
        budget=None
        if ai_enabled:
            # Spend survives restarts: seed the rolling budget from the last 24 h of audit records.
            budget=AiCallBudget.from_config()
            budget.seed(ledger.recent_calls(window=budget.window))
        return SimpleNamespace(store=store,publisher=publisher,briefs=briefs,jobs=jobs,
            ai_enabled=ai_enabled,fairness=fairness,quota=PreparationQuota.from_config(store),
            proposer=make_proposer(briefs,ledger=ledger,enabled=ai_enabled,budget=budget))
    except BaseException:
        store.close()
        raise


def attach_v1_routes(app,limiter):
    router=APIRouter(prefix='/engineering/v1',tags=['Checked cantilever'],route_class=CheckedRoute)

    def runtime(request):
        value=getattr(request.app.state,'engineering',None)
        if value is None: raise HTTPException(503,'Checked engineering runtime unavailable')
        return value

    def clean(request):
        value=runtime(request); value.publisher.cleanup(); value.quota.expire(); return value

    def client_key(request):
        # The same identity the rate limiter uses.
        try: return str(limiter._key_func(request))
        except Exception: return request.client.host if request.client else 'unknown'

    def prepared_view(prepared):
        content=prepared.draft.content
        return dict(prepared.export(),brief_sha256=content.brief_sha256 if content else None,
                    fixed_requirements_sha256=content.fixed_requirements_sha256 if content else None)

    @router.get('/capabilities')
    @limiter.limit('30/minute')
    async def capabilities(request:Request):
        value=runtime(request)
        native=os.name=='posix' and all(importlib.util.find_spec(name) for name in ('cadquery','OCP','numpy'))
        entries=[]
        for c in list_capabilities():
            entry=c.model_dump(mode='json')
            if isinstance(c,CapabilityDescriptor):
                entry.update(review_fields=list(REVIEW_FIELDS),available=bool(native))
            else:
                # A generator's review fields are the leaves of its normalized specification.
                entry=generator_entry(c)
            entries.append(entry)
        return dict(capabilities=entries,
            native_available=bool(native),ai_enabled=value.ai_enabled,
            ai_available=bool(value.ai_enabled and value.proposer is not None and value.proposer.provider.available),
            review_fields=list(REVIEW_FIELDS),
            availability_scope='Runtime prerequisites; each execution still requires all native evidence',
            persistent_volume_required=os.environ.get('MIRUM_ENGINEERING_REQUIRE_VOLUME','0')=='1')

    @router.post('/drafts',status_code=201)
    @limiter.limit('10/minute')
    async def create_draft(body:DraftCreate,request:Request):
        value=await asyncio.to_thread(clean,request)
        given=body.input.get('capability')
        if given is not None and not (isinstance(given,dict) and given.get('id')==body.capability):
            raise ValueError('Input must name the requested capability')
        if body.capability!=CAPABILITY.id:
            if get_generator(body.capability) is None: raise ValueError('Unknown capability')
            if body.mode=='ai':
                # E05 generator AI drafts (Wave E P3). The beam's B13 path below is untouched; this branch
                # shares its flag, its AiCallBudget (value.proposer.budget) and its unavailable report.
                from . import proposal_contract
                def unavailable(reasons=('AI preparation unavailable; typed form remains available',)):
                    return JSONResponse(status_code=503,content=dict(state='unavailable',reasons=list(reasons),prepared=None),
                        headers={'Cache-Control':'no-store'})
                if not value.ai_enabled or value.proposer is None:
                    # Flag off: no proposer is resolved, no module imported, no key read.
                    raise HTTPException(503,'AI draft preparation is not enabled')
                if body.input or body.prompt is None: raise ValueError('AI entry requires a prompt and empty initial input')
                if len(body.prompt)>proposal_contract.MAX_PROMPT: raise ValueError('AI entry prompt is too long')
                client=client_key(request)
                size=len(body.prompt.encode())
                # Deterministic family gate for every generator (contract rule 1; dragster_proposer is
                # pinned, so it lives here): another family -> unsupported, several -> clarification.
                # Decided without a provider call or a budget unit; the draft records the refusal.
                from .briefs import InputIssue
                from .family_gate import family_gate
                refusal=family_gate(body.prompt,body.capability)
                if refusal is not None:
                    await asyncio.to_thread(value.quota.admit_draft,client,ai=False,size=size)
                    def create_refusal():
                        created=value.briefs.create(dict(capability=proposal_contract.reference(get_generator(body.capability))),
                            extra_issues=(InputIssue(field='request',state=refusal.state,reason=refusal.reason),))
                        value.quota.record_draft(created.draft.draft_id,client,len(created.model_dump_json()))
                        return created
                    return prepared_view(await asyncio.to_thread(create_refusal))
                budget=value.proposer.budget
                if not budget.has_capacity(): return unavailable()
                await asyncio.to_thread(value.quota.admit_draft,client,ai=True,size=size)
                # Each reserved unit is written to the ledger before its provider call, so a restart
                # re-seeds generator spend too (capability id and request sha only; never the prompt).
                import hashlib
                from .store import GeneratorSpendBudget
                spend=GeneratorSpendBudget(budget,SQLiteProposalLedger(value.store),body.capability,
                                           hashlib.sha256(body.prompt.encode()).hexdigest())
                proposer=proposal_contract.ai_proposer(body.capability,enabled=value.ai_enabled,budget=spend)
                try:
                    proposal=proposal_contract.conform(body.capability,await proposer.propose(body.prompt))
                except proposal_contract.NonConformingProposal:
                    return unavailable()
                if proposal.state=='unavailable': return unavailable(proposal.reasons)
                def create_ai():
                    created=value.briefs.create(proposal.payload,provenance=proposal.provenance,extra_issues=tuple(proposal.issues))
                    value.quota.record_draft(created.draft.draft_id,client,len(created.model_dump_json()))
                    return created
                return prepared_view(await asyncio.to_thread(create_ai))
            if body.mode=='form':
                if not isinstance(given,dict): raise ValueError('Input must name the requested capability')
                # Resolve the exact versions before admission: an unknown version is a 422, not a draft.
                get_capability(given.get('id'),given.get('version'),
                    numerical_policy_version=given.get('numerical_policy_version'),
                    resource_policy_version=given.get('resource_policy_version'))
        elif body.mode=='rules': raise ValueError('The beam has no rules proposer')
        if body.mode=='rules':
            # Stdlib proposer: no provider, no key; allowed with the AI flag off.
            if body.prompt is None or body.input: raise ValueError('Rules entry requires a prompt and empty initial input')
            proposer=rules_proposer(body.capability)
            size=len(body.prompt.encode())
            client=client_key(request)
            # Admission (quota) comes before any proposer work.
            await asyncio.to_thread(value.quota.admit_draft,client,ai=False,size=size)
            payload,issues,provenance=proposer.propose(body.prompt)
            def create_rules():
                created=value.briefs.create(payload,provenance=provenance,extra_issues=tuple(issues))
                value.quota.record_draft(created.draft.draft_id,client,len(created.model_dump_json()))
                return created
            return prepared_view(await asyncio.to_thread(create_rules))
        if body.mode=='ai' and (not value.ai_enabled or value.proposer is None):
            # 503 matches this router's other "feature unavailable" reports. Nothing is built or read.
            raise HTTPException(503,'AI draft preparation is not enabled')
        client=client_key(request)
        size=len(json.dumps(body.input,allow_nan=False,separators=(',',':')).encode())
        def create(payload,incoming,ai=False):
            # Blocking SQLite work: quota check, insert and accounting run off the event loop.
            value.quota.admit_draft(client,ai=ai,size=incoming)
            created=value.briefs.create(payload)
            value.quota.record_draft(created.draft.draft_id,client,len(created.model_dump_json()))
            return created
        if body.mode=='form':
            if body.prompt is not None: raise ValueError('Form input does not take a prompt')
            return prepared_view(await asyncio.to_thread(create,body.input,size))
        if body.input or body.prompt is None: raise ValueError('AI entry requires a prompt and empty initial input')
        if not value.proposer.budget.has_capacity():
            # Over the provider-call budget: the existing AI-unavailable report, before any record or call.
            return JSONResponse(status_code=503,content=dict(state='unavailable',
                reasons=['AI preparation unavailable; typed form remains available'],prepared=None),headers={'Cache-Control':'no-store'})
        prepared=await asyncio.to_thread(create,{},len(body.prompt.encode()),True)
        result=await value.proposer.propose(prepared.draft.draft_id,expected_revision=1,prompt=body.prompt)
        await asyncio.to_thread(value.quota.touch,prepared.draft.draft_id,len(result.prepared.model_dump_json()))
        if result.state in ('unavailable','cancelled'):
            return JSONResponse(status_code=503,content=dict(state=result.state,reasons=result.reasons,prepared=prepared_view(result.prepared)),headers={'Cache-Control':'no-store'})
        return prepared_view(result.prepared)

    @router.get('/drafts/{draft_id}')
    @limiter.limit('30/minute')
    async def read_draft(draft_id:str,request:Request):
        return prepared_view((await asyncio.to_thread(clean,request)).briefs.read(draft_id))

    @router.patch('/drafts/{draft_id}')
    @limiter.limit('30/minute')
    async def edit_draft(draft_id:str,body:DraftEdit,request:Request):
        value=await asyncio.to_thread(clean,request)
        size=len(json.dumps(body.input,allow_nan=False,separators=(',',':')).encode())
        def edit():
            current=value.briefs.read(draft_id)
            stored=json.loads(current.input_json).get('capability')
            drafted=stored.get('id') if isinstance(stored,dict) else CAPABILITY.id
            given=body.input.get('capability')
            # A draft keeps its capability: PATCH can neither switch it nor drop it.
            if (given.get('id') if isinstance(given,dict) else (None if drafted!=CAPABILITY.id else drafted))!=drafted:
                raise ValueError('A draft cannot change its capability')
            value.quota.admit_edit(draft_id,size)
            edited=value.briefs.edit(draft_id,expected_revision=body.expected_revision,payload=body.input)
            value.quota.touch(draft_id,len(edited.model_dump_json()))
            return edited
        return prepared_view(await asyncio.to_thread(edit))

    @router.post('/briefs',status_code=201)
    @limiter.limit('30/minute')
    async def finalize_brief(body:ReviewInput,request:Request):
        value=await asyncio.to_thread(clean,request)
        def finalize():
            value.quota.admit_brief(body.draft_id)
            return value.briefs.finalize(body).model_dump(mode='json')
        return await asyncio.to_thread(finalize)

    @router.get('/briefs/{brief_id}')
    @limiter.limit('30/minute')
    async def read_brief(brief_id:str,request:Request):
        return (await asyncio.to_thread(clean,request)).briefs.read_brief(brief_id).model_dump(mode='json')

    def job_view(value,job):
        record=job.model_dump(mode='json')
        # What a passing run means, next to every outcome: clients never read 'accepted' alone.
        capability_id=record['capability']['id']
        descriptor=get_generator(capability_id)
        record['claim']='checked_design' if capability_id==CAPABILITY.id else (descriptor.claim if descriptor else None)
        if record.get('expired'): return dict(record,delivery='expired',artifacts={})
        with value.store._lock:
            row=value.store.db.execute('SELECT expires FROM jobs WHERE id=?',(job.job_id,)).fetchone()
        expires=row[0] if row else None
        try:
            publication_expiry=value.publisher.expires_at(job.job_id)
            expires=min(expires,publication_expiry) if expires is not None else publication_expiry
        except KeyError: pass
        if expires is not None and time.time()>=expires:
            return dict(compact_run(record),claim=record['claim'],delivery='expired',artifacts={},artifact_expires_at=expires)
        delivery='pending'
        formats={}
        if record['lifecycle'] in ('completed','unavailable','cancelled'):
            try:
                # Publish-time validation is authoritative; polling only does stat-level checks.
                value.publisher.quick_check(job.job_id,job.bundle)
                delivery='available'
                directory=value.publisher.directory(job.job_id)
                known=FORMATS if record['capability']['id']==CAPABILITY.id else ALL_FORMATS
                formats={format:(directory/name).is_file() for format,(name,_) in known.items()}
            except (KeyError,ValueError,OSError):
                delivery='unavailable'
        return dict(record,delivery=delivery,artifacts=formats,artifact_expires_at=expires)

    @router.post('/jobs',status_code=202)
    @limiter.limit('10/minute')
    async def submit_job(body:JobSubmit,request:Request):
        value=runtime(request)
        client=client_key(request)
        # Admission (cleanup, quota, slot claim, insert) is blocking: run it in a worker thread,
        # then create the asyncio task on the loop. If this request is cancelled in between, the
        # already-admitted job is still started on the loop rather than leaking its slot.
        admitted=await run_in_thread_owned(value.jobs.admit,body.brief_id,body.idempotency_key,client,
            on_orphan=value.jobs.start)
        job=value.jobs.start(admitted)
        return await asyncio.to_thread(job_view,value,job)

    @router.get('/jobs/{job_id}')
    @limiter.limit('120/minute')
    async def read_job(job_id:str,request:Request):
        value=runtime(request)
        return await asyncio.to_thread(lambda:job_view(value,value.jobs.read(job_id)))

    @router.post('/jobs/{job_id}/cancel')
    @limiter.limit('30/minute')
    async def cancel_job(job_id:str,request:Request):
        value=runtime(request)
        return await asyncio.to_thread(lambda:job_view(value,value.jobs.cancel(job_id)))

    @router.get('/jobs/{job_id}/artifacts/{format}')
    @limiter.limit('60/minute')
    async def download(job_id:str,format:str,request:Request):
        value=runtime(request)
        job=await asyncio.to_thread(value.jobs.read,job_id)
        if getattr(job,'expired',False): raise ArtifactExpired('Expired')
        if format not in ALL_FORMATS: raise KeyError(format)
        if job.lifecycle not in ('completed','unavailable','cancelled'):
            raise HTTPException(409,'Artifacts are not committed yet')
        if not value.publisher.directory(job_id).exists():
            if job.outcome=='accepted': raise HTTPException(503,'Required accepted publication unavailable')
            raise KeyError(format)
        try: resource=await run_in_thread_owned(value.publisher.open_lease,job_id,format,
            on_orphan=lambda orphan:orphan.close())
        except ArtifactExpired: raise
        except KeyError:
            if job.outcome=='accepted': raise HTTPException(503,'Required publication metadata unavailable') from None
            raise
        except (ValueError,OSError):
            raise HTTPException(503,'Required publication integrity check failed') from None
        try:
            return LeasedResponse(resource,media_type=resource.mime_type,
                headers={'Cache-Control':'no-store','Content-Length':str(resource.size),
                    'Content-Disposition':f'attachment; filename="{resource.filename}"',
                    'X-Content-SHA256':resource.sha256})
        except BaseException:
            resource.close()   # never leak the lease, including on cancellation
            raise

    app.include_router(router)
