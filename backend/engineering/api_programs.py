"""F08 HTTP transport of the program runtime (D4, D5, D7, D8, D14).

A thin router under /engineering/v1: parse the request, call the in-process ProgramInterface (facade),
serialize. No business logic lives here. Paths come from interface.OPERATIONS, so the registry is the
single source of the route set. Every error response is the interface envelope (kind, code, ...) with
the status from interface.http_status; a busy envelope also carries Retry-After.

MEL bodies are raw bytes (application/vnd.mirum.mel) read through api_v1.limit_body, so the 64 KiB cap
is enforced before parsing and invalid UTF-8, BOM and NUL reach the compiler as MEL diagnostics. Small
fields travel in headers (never query strings): X-Mirum-Source-Id, X-Mirum-Expected-Parent (also the
expected head of restore), X-Mirum-Require-Requirements. review and compare take small JSON bodies.

Flag (D4): MIRUM_ENGINEERING_PROGRAMS, read here from os.environ, default OFF. Off, or a construction
failure, leaves app.state.programs None: every program route then answers 503 programs_disabled and no
program table or service exists. main.py wires the runtime inside the lifespan (router startup hooks
never run with lifespan=).

Nothing here logs an exception text, a path or an id.
"""
import logging
import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
# slowapi's RateLimitExceeded subclasses starlette's HTTPException (fastapi's is a subclass of it too): catch the base
# so a 429 from the limiter becomes a busy envelope, not the catch-all 503.
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api_v1 import CheckedRoute
from .program import interface as api
from .program.interface import InterfaceFailure

__all__ = ['PROGRAMS_ENV', 'programs_enabled', 'create_program_runtime', 'attach_program_routes']

_LOG = logging.getLogger(__name__)
PROGRAMS_ENV = 'MIRUM_ENGINEERING_PROGRAMS'
_TRUE = frozenset({'1', 'true', 'yes', 'on'})
_JSON_MEDIA = 'application/json'


def programs_enabled(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(PROGRAMS_ENV, '')).strip().lower() in _TRUE


def create_program_runtime(engineering):
    """The ProgramInterface over the API's own store, jobs, publisher and briefs, or None.

    None when the flag is off (nothing is imported or built, no table is created) and when construction
    fails (e.g. RevisionService.recover() raising RuntimeError): one fixed log line, v1 routes unaffected."""
    if engineering is None or not programs_enabled():
        return None
    try:
        from .program.facade import ProgramInterface, ProgramQuota
        from .program.transactions import RevisionService
        from .equipment_bay_adapter import EquipmentBayAdapter
        from .equipment_bay import CAPABILITY_ID as BAY_ID
        engineering.jobs.generators.setdefault(BAY_ID, EquipmentBayAdapter())
        service = RevisionService(engineering.store, engineering.jobs, engineering.publisher,
                                  engineering.briefs)
        return ProgramInterface(service, engineering.briefs, engineering.quota,
                                ProgramQuota.from_env(engineering.store))
    except Exception:  # noqa: BLE001 - fixed line only: never the exception text
        _LOG.warning('engineering.programs construction_failed')
        return None


# ------------------------------------------------------------------------------------------- envelopes

def _error_response(err) -> JSONResponse:
    headers = {'Cache-Control': 'no-store'}
    if err.kind == 'busy' and err.retry_after is not None:
        headers['Retry-After'] = str(err.retry_after)
    return JSONResponse(status_code=api.http_status(err), content=err.model_dump(mode='json'), headers=headers)


def _http_error(exc: StarletteHTTPException) -> JSONResponse:
    """An HTTPException raised by limit_body or the v1 CheckedRoute becomes an interface envelope."""
    status = exc.status_code
    if status == 413:
        err = api.too_large()
    elif status == 400:
        err = api.invalid_request('invalid_header')
    elif status == 404:
        err = api.not_found()
    elif status == 410:
        err = api.error('artifacts_expired')
    elif status == 409:
        err = api.error('revision_conflict')
    elif status == 422:
        err = api.invalid_request('invalid_field')
    elif status == 429:
        raw = (exc.headers or {}).get('Retry-After', '')
        after = int(raw) if str(raw).isdigit() and 1 <= int(raw) <= 86400 else None
        err = api.busy('admission_busy', retry_after=after)
    else:
        err = api.unavailable()
    return _error_response(err)


class ProgramRoute(CheckedRoute):
    """CheckedRoute (no-store, body cap before parsing) with HTTPException -> envelope."""

    def get_route_handler(self):
        inner = super().get_route_handler()

        async def guarded(request):
            try:
                return await inner(request)
            except StarletteHTTPException as exc:
                return _http_error(exc)
            except Exception:  # noqa: BLE001 - nothing escapes as a framework error page
                return _error_response(api.unavailable())
        return guarded


# ----------------------------------------------------------------------------------------- request parts

def _check_media(request, expected: str) -> None:
    """A missing Content-Type is accepted; a present one must be exactly the expected media type."""
    value = request.headers.get('content-type')
    if value is None or not value.strip():
        return
    if value.split(';', 1)[0].strip().lower() != expected:
        raise InterfaceFailure(api.error('wrong_media_type'))


async def _mel_body(request) -> bytes:
    _check_media(request, api.MEL_MEDIA_TYPE)
    return await request.body()  # already bounded and cached by limit_body


async def _json_body(request) -> dict:
    _check_media(request, _JSON_MEDIA)
    value = api.bounded_json_loads(await request.body())
    if not isinstance(value, dict):
        raise InterfaceFailure(api.error('malformed_json'))
    return value


def _header(request, name: str):
    value = request.headers.get(name)
    if value is None:
        return None
    code = api.check_header_value(name, value)
    if code is not None:
        raise InterfaceFailure(api.error(code))
    return value


def attach_program_routes(app, limiter):
    router = APIRouter(prefix=api.API_PREFIX, tags=['Programs'], route_class=ProgramRoute)

    def client_key(request):
        # The same identity the rate limiter and the v1 routes use.
        try:
            return str(limiter._key_func(request))
        except Exception:  # noqa: BLE001
            return request.client.host if request.client else 'unknown'

    async def serve(request, call, status=200):
        try:
            face = getattr(request.app.state, 'programs', None)
            if face is None:
                raise InterfaceFailure(api.programs_disabled())
            result = await call(face, client_key(request))
        except InterfaceFailure as failure:
            return _error_response(failure.error)
        except Exception as exc:  # noqa: BLE001 - one mapping; the exception text is never read
            from .program.facade import map_exception
            return _error_response(map_exception(exc))
        if isinstance(result, Response):
            return result
        return JSONResponse(status_code=status, content=result.model_dump(mode='json'))

    # ------------------------------------------------------------------------------------- MEL (stateless)

    async def mel_meta(request: Request):
        async def call(face, client):
            from .program.versions import MEL_COMPILER_VERSION, MEL_LANGUAGE_VERSION
            return api.MelMeta(language_version=MEL_LANGUAGE_VERSION, compiler_version=MEL_COMPILER_VERSION)
        return await serve(request, call)

    async def mel_validate(request: Request):
        async def call(face, client):
            source = await _mel_body(request)
            return await face.validate(source, api.ValidateRequest(source_id=_header(request, api.HEADER_SOURCE_ID)),
                                       client=client)
        return await serve(request, call)

    async def mel_format(request: Request):
        async def call(face, client):
            source = await _mel_body(request)
            return await face.format(source, api.FormatRequest(source_id=_header(request, api.HEADER_SOURCE_ID)),
                                     client=client)
        return await serve(request, call)

    async def mel_plan(request: Request):
        async def call(face, client):
            source = await _mel_body(request)
            return await face.plan(source, api.PlanRequest(source_id=_header(request, api.HEADER_SOURCE_ID)),
                                   client=client)
        return await serve(request, call)

    # ------------------------------------------------------------------------------------------ programs

    async def program_create(request: Request):
        async def call(face, client):
            return await face.program_create(api.ProgramCreateRequest(), client=client)
        return await serve(request, call, status=201)

    async def program_read(request: Request, pid: str):
        async def call(face, client):
            return await face.program(api.HistoryRequest(program_id=pid), client=client)
        return await serve(request, call)

    async def program_history(request: Request, pid: str):
        async def call(face, client):
            return await face.history(api.HistoryRequest(program_id=pid), client=client)
        return await serve(request, call)

    async def program_journal(request: Request, pid: str):
        async def call(face, client):
            lines = await face.journal(api.HistoryRequest(program_id=pid), client=client)
            body = {'api_version': api.PROGRAM_API_VERSION, 'program_id': pid,
                    'journal': [line.model_dump(mode='json') for line in lines]}
            return JSONResponse(content=body)
        return await serve(request, call)

    async def revision_stage(request: Request, pid: str):
        async def call(face, client):
            source = await _mel_body(request)
            return await face.stage(source, api.StageRequest(
                program_id=pid, source_id=_header(request, api.HEADER_SOURCE_ID),
                expected_parent=_header(request, api.HEADER_EXPECTED_PARENT),
                require_requirements_sha256=_header(request, api.HEADER_REQUIRE_REQUIREMENTS)), client=client)
        return await serve(request, call, status=201)

    # ----------------------------------------------------------------------------------------- revisions

    async def revision_status(request: Request, rid: str):
        async def call(face, client):
            return await face.status(api.StatusRequest(revision_id=rid), client=client)
        return await serve(request, call)

    async def revision_evidence(request: Request, rid: str):
        async def call(face, client):
            return await face.inspect(api.InspectRequest(revision_id=rid), client=client)
        return await serve(request, call)

    async def revision_review(request: Request, rid: str):
        async def call(face, client):
            data = await _json_body(request)
            if 'revision_id' in data:
                raise InterfaceFailure(api.invalid_request('invalid_field', field='revision_id'))
            return await face.review(api.parse_model(api.ReviewRequest, dict(data, revision_id=rid)),
                                     client=client)
        return await serve(request, call)

    async def revision_run(request: Request, rid: str):
        async def call(face, client):
            return await face.run(api.RunRequest(revision_id=rid), client=client)
        return await serve(request, call)

    async def revision_cancel(request: Request, rid: str):
        async def call(face, client):
            return await face.cancel(api.CancelRequest(revision_id=rid), client=client)
        return await serve(request, call)

    async def revision_restore(request: Request, rid: str):
        async def call(face, client):
            return await face.restore(api.RestoreRequest(
                revision_id=rid, expected_head=_header(request, api.HEADER_EXPECTED_PARENT)), client=client)
        return await serve(request, call)

    async def revision_rebuild(request: Request, rid: str):
        async def call(face, client):
            return await face.rebuild(api.RebuildRequest(
                revision_id=rid, expected_parent=_header(request, api.HEADER_EXPECTED_PARENT)), client=client)
        return await serve(request, call)

    async def revision_compare(request: Request, rid: str):
        async def call(face, client):
            data = await _json_body(request)
            if 'revision_id' in data:
                raise InterfaceFailure(api.invalid_request('invalid_field', field='revision_id'))
            return await face.diff(api.parse_model(api.DiffRequest, dict(data, revision_id=rid)), client=client)
        return await serve(request, call)

    async def revision_export(request: Request, rid: str, fmt: str):
        async def call(face, client):
            if not api.is_export_format(fmt):
                raise InterfaceFailure(api.not_found())
            result = await face.export(api.ExportRequest(revision_id=rid, fmt=fmt), client=client)
            meta = result.metadata
            return Response(content=result.data, media_type=meta.content_type, headers={
                'Cache-Control': 'no-store',
                'Content-Disposition': 'attachment; filename="%s"' % meta.filename,
                'X-Content-SHA256': meta.sha256})
        return await serve(request, call)

    handlers = {
        ('GET', '/mel/meta'): mel_meta, ('POST', '/mel/validate'): mel_validate,
        ('POST', '/mel/format'): mel_format, ('POST', '/mel/plan'): mel_plan,
        ('POST', '/programs'): program_create, ('GET', '/programs/{pid}'): program_read,
        ('GET', '/programs/{pid}/history'): program_history, ('GET', '/programs/{pid}/journal'): program_journal,
        ('POST', '/programs/{pid}/revisions'): revision_stage,
        ('GET', '/revisions/{rid}'): revision_status, ('GET', '/revisions/{rid}/evidence'): revision_evidence,
        ('POST', '/revisions/{rid}/review'): revision_review, ('POST', '/revisions/{rid}/run'): revision_run,
        ('POST', '/revisions/{rid}/cancel'): revision_cancel,
        ('POST', '/revisions/{rid}/restore'): revision_restore,
        ('POST', '/revisions/{rid}/rebuild'): revision_rebuild,
        ('POST', '/revisions/{rid}/compare'): revision_compare,
        ('GET', '/revisions/{rid}/artifacts/{fmt}'): revision_export,
    }
    # Per-client rate limits, same scale as the v1 routes (security review of PR #143): reads 120/minute like the
    # v1 job read (exports 60/minute like the v1 download); stateless MEL work and light actions 30/minute; anything that creates a program,
    # stages, runs, restores or rebuilds 10/minute like v1 job creation. A route without a rate is a startup error.
    rates = {
        ('GET', '/mel/meta'): '120/minute', ('POST', '/mel/validate'): '30/minute',
        ('POST', '/mel/format'): '30/minute', ('POST', '/mel/plan'): '30/minute',
        ('POST', '/programs'): '10/minute', ('GET', '/programs/{pid}'): '120/minute',
        ('GET', '/programs/{pid}/history'): '120/minute', ('GET', '/programs/{pid}/journal'): '120/minute',
        ('POST', '/programs/{pid}/revisions'): '10/minute',
        ('GET', '/revisions/{rid}'): '120/minute', ('GET', '/revisions/{rid}/evidence'): '120/minute',
        ('POST', '/revisions/{rid}/review'): '30/minute', ('POST', '/revisions/{rid}/run'): '10/minute',
        ('POST', '/revisions/{rid}/cancel'): '30/minute',
        ('POST', '/revisions/{rid}/restore'): '10/minute',
        ('POST', '/revisions/{rid}/rebuild'): '10/minute',
        ('POST', '/revisions/{rid}/compare'): '30/minute',
        ('GET', '/revisions/{rid}/artifacts/{fmt}'): '60/minute',
    }
    if set(rates) != set(handlers):
        raise RuntimeError('Program route rate table is incomplete')
    # The registry decides which routes exist; a route the table cannot serve is a startup error.
    seen = set()
    for spec in api.OPERATIONS:
        for route in spec.routes:
            key = (route.method, route.path)
            if key in seen:
                continue
            seen.add(key)
            endpoint = limiter.limit(rates[key])(handlers[key])
            router.add_api_route(route.path, endpoint, methods=[route.method], response_model=None)
    app.include_router(router)
