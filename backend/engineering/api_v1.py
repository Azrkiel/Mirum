"""Checked workflow transport. Physical inputs enter only through review."""
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
from starlette.background import BackgroundTask
from pydantic import Field,BeforeValidator

from .admission import AdmissionBusy
from .briefs import BriefService,RevisionConflict
from .bundles import BundlePublisher,FORMATS
from .capabilities import list_capabilities
from .contracts import ContractModel,ReviewAcknowledgement,Text,REVIEW_FIELDS
from .jobs import JobController,compact_run
from .proposer import BriefProposer
from .store import SQLiteStore,SQLiteBriefRepository,SQLiteProposalLedger,ArtifactExpired


class DraftCreate(ContractModel):
    mode: Literal['form','ai']='form'
    input: dict = Field(default_factory=dict)
    prompt: str | None = Field(default=None,max_length=8000)


class DraftEdit(ContractModel):
    expected_revision: int = Field(ge=1)
    input: dict


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


class CheckedRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def guarded(request):
            try:
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


def create_runtime():
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
    store=SQLiteStore(root)
    try:
        publisher=BundlePublisher(store)
        briefs=BriefService(SQLiteBriefRepository(store))
        jobs=JobController(store,publisher=publisher)
        return SimpleNamespace(store=store,publisher=publisher,briefs=briefs,jobs=jobs,
            proposer=BriefProposer(briefs,ledger=SQLiteProposalLedger(store)))
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
        value=runtime(request); value.publisher.cleanup(); return value

    def prepared_view(prepared):
        content=prepared.draft.content
        return dict(prepared.export(),brief_sha256=content.brief_sha256 if content else None,
                    fixed_requirements_sha256=content.fixed_requirements_sha256 if content else None)

    @router.get('/capabilities')
    @limiter.limit('30/minute')
    async def capabilities(request:Request):
        value=runtime(request)
        native=os.name=='posix' and all(importlib.util.find_spec(name) for name in ('cadquery','OCP','numpy'))
        return dict(capabilities=[c.model_dump(mode='json') for c in list_capabilities()],
            native_available=bool(native),ai_available=value.proposer.provider.available,
            review_fields=list(REVIEW_FIELDS),
            availability_scope='Runtime prerequisites; each execution still requires all native evidence',
            persistent_volume_required=os.environ.get('MIRUM_ENGINEERING_REQUIRE_VOLUME','0')=='1')

    @router.post('/drafts',status_code=201)
    @limiter.limit('10/minute')
    async def create_draft(body:DraftCreate,request:Request):
        value=clean(request)
        if body.mode=='form':
            if body.prompt is not None: raise ValueError('Form input does not take a prompt')
            return prepared_view(value.briefs.create(body.input))
        if body.input or body.prompt is None: raise ValueError('AI entry requires a prompt and empty initial input')
        prepared=value.briefs.create({})
        result=await value.proposer.propose(prepared.draft.draft_id,expected_revision=1,prompt=body.prompt)
        if result.state in ('unavailable','cancelled'):
            return JSONResponse(status_code=503,content=dict(state=result.state,reasons=result.reasons,prepared=prepared_view(result.prepared)),headers={'Cache-Control':'no-store'})
        return prepared_view(result.prepared)

    @router.get('/drafts/{draft_id}')
    @limiter.limit('30/minute')
    async def read_draft(draft_id:str,request:Request):
        return prepared_view(clean(request).briefs.read(draft_id))

    @router.patch('/drafts/{draft_id}')
    @limiter.limit('30/minute')
    async def edit_draft(draft_id:str,body:DraftEdit,request:Request):
        return prepared_view(clean(request).briefs.edit(draft_id,expected_revision=body.expected_revision,payload=body.input))

    @router.post('/briefs',status_code=201)
    @limiter.limit('30/minute')
    async def finalize_brief(body:ReviewInput,request:Request):
        return clean(request).briefs.finalize(body).model_dump(mode='json')

    @router.get('/briefs/{brief_id}')
    @limiter.limit('30/minute')
    async def read_brief(brief_id:str,request:Request):
        return clean(request).briefs.read_brief(brief_id).model_dump(mode='json')

    def job_view(value,job):
        record=job.model_dump(mode='json')
        if record.get('expired'): return dict(record,delivery='expired',artifacts={})
        with value.store._lock:
            row=value.store.db.execute('SELECT expires FROM jobs WHERE id=?',(job.job_id,)).fetchone()
        expires=row[0] if row else None
        try:
            publication_expiry=value.publisher.expires_at(job.job_id)
            expires=min(expires,publication_expiry) if expires is not None else publication_expiry
        except KeyError: pass
        if expires is not None and time.time()>=expires:
            return dict(compact_run(record),delivery='expired',artifacts={},artifact_expires_at=expires)
        delivery='pending'
        formats={}
        if record['lifecycle'] in ('completed','unavailable','cancelled'):
            try:
                value.publisher.validate(job.job_id,job.bundle)
                delivery='available'
                directory=value.publisher.directory(job.job_id)
                formats={format:(directory/name).is_file() for format,(name,_) in FORMATS.items()}
            except (KeyError,ValueError,OSError):
                delivery='unavailable'
        return dict(record,delivery=delivery,artifacts=formats,artifact_expires_at=expires)

    @router.post('/jobs',status_code=202)
    @limiter.limit('10/minute')
    async def submit_job(body:JobSubmit,request:Request):
        value=runtime(request)
        return job_view(value,value.jobs.submit(body.brief_id,body.idempotency_key))

    @router.get('/jobs/{job_id}')
    @limiter.limit('120/minute')
    async def read_job(job_id:str,request:Request):
        value=runtime(request); return job_view(value,value.jobs.read(job_id))

    @router.post('/jobs/{job_id}/cancel')
    @limiter.limit('30/minute')
    async def cancel_job(job_id:str,request:Request):
        value=runtime(request); return job_view(value,value.jobs.cancel(job_id))

    @router.get('/jobs/{job_id}/artifacts/{format}')
    @limiter.limit('60/minute')
    async def download(job_id:str,format:str,request:Request):
        value=runtime(request)
        job=value.jobs.read(job_id)
        if getattr(job,'expired',False): raise ArtifactExpired('Expired')
        if format not in FORMATS: raise KeyError(format)
        if job.lifecycle not in ('completed','unavailable','cancelled'):
            raise HTTPException(409,'Artifacts are not committed yet')
        if not value.publisher.directory(job_id).exists():
            if job.outcome=='accepted': raise HTTPException(503,'Required accepted publication unavailable')
            raise KeyError(format)
        lease=value.publisher.lease(job_id,format)
        try: resource=lease.__enter__()
        except ArtifactExpired: raise
        except KeyError:
            if job.outcome=='accepted': raise HTTPException(503,'Required publication metadata unavailable') from None
            raise
        except (ValueError,OSError):
            raise HTTPException(503,'Required publication integrity check failed') from None
        def close(): lease.__exit__(None,None,None)
        def chunks():
            try:
                while True:
                    block=resource.file.read(65536)
                    if not block: break
                    yield block
            finally: close()
        return StreamingResponse(chunks(),media_type=resource.mime_type,background=BackgroundTask(close),
            headers={'Cache-Control':'no-store','Content-Length':str(resource.size),
                'Content-Disposition':f'attachment; filename="{resource.filename}"',
                'X-Content-SHA256':resource.sha256})

    app.include_router(router)
