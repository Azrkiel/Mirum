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
import asyncio
import logging
import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
# slowapi's RateLimitExceeded subclasses starlette's HTTPException (fastapi's is a subclass of it too): catch the base
# so a 429 from the limiter becomes a busy envelope, not the catch-all 503.
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api_v1 import CheckedRoute
from .program import interface as api
from .program.interface import InterfaceFailure

__all__ = ['PROGRAMS_ENV', 'EXPORT_CONCURRENCY_ENV', 'EXPORT_SEND_TIMEOUT_ENV', 'EXPORT_DEADLINE_ENV',
           'EXPORT_GATE_MIN_BYTES_ENV', 'EXPORT_MIN_RATE_ENV', 'programs_enabled', 'create_program_runtime',
           'attach_program_routes']

_LOG = logging.getLogger(__name__)
PROGRAMS_ENV = 'MIRUM_ENGINEERING_PROGRAMS'
_TRUE = frozenset({'1', 'true', 'yes', 'on'})
_JSON_MEDIA = 'application/json'
# M1: bundle exports (up to 128 MiB for generator-cleaned-stl) are capped process-wide. Over the cap the
# request is refused at once with the admission_busy envelope (429 + Retry-After), never queued.
EXPORT_CONCURRENCY_ENV = 'MIRUM_PROGRAM_EXPORT_CONCURRENCY'
EXPORT_CONCURRENCY_DEFAULT = 3
EXPORT_CONCURRENCY_MAX = 16
EXPORT_RETRY_AFTER_SECONDS = 2
# A reader that stops reading must not hold a slot and a file lease: every send of the body is bounded, and so
# is the whole stream (sized for 128 MiB over a slow link). On a timeout the response is cut and everything is
# released. ExportGate also caps slots per client at max(1, size - 1), keyed by client_key (the trusted
# forwarded address when MIRUM_TRUSTED_CLIENT_HEADER=1, else the transport peer). Without the trusted header
# every Streamlit UI user shares the UI server's key, so UI traffic gets size - 1 slots in total; this is a
# fairness control, not DDoS protection (a client with many addresses gets a key per address).
EXPORT_SEND_TIMEOUT_ENV = 'MIRUM_PROGRAM_EXPORT_SEND_TIMEOUT_SECONDS'
EXPORT_DEADLINE_ENV = 'MIRUM_PROGRAM_EXPORT_DEADLINE_SECONDS'
EXPORT_SEND_TIMEOUT_DEFAULT = 30.0
# Throughput floor: after the grace period, a reader that took fewer than MIN_BYTES_PER_SECOND over the last
# window is cut, so trickling just inside the per-send timeout cannot pin a slot for the whole deadline.
EXPORT_MIN_RATE_ENV = 'MIRUM_PROGRAM_EXPORT_MIN_BYTES_PER_SECOND'
EXPORT_MIN_RATE_DEFAULT = 256 * 1024
EXPORT_RATE_WINDOW_SECONDS = 10.0
EXPORT_RATE_GRACE_SECONDS = 5.0
_SEND_SLICE = 64 * 1024
EXPORT_DEADLINE_DEFAULT = 300.0
# Only an export larger than this takes a slot (small JSON reports and BOMs never do). Formats that are
# always big binaries take one before they are opened.
EXPORT_GATE_MIN_BYTES_ENV = 'MIRUM_PROGRAM_EXPORT_GATE_MIN_BYTES'
EXPORT_GATE_MIN_BYTES_DEFAULT = 8 * 1024 * 1024
_ALWAYS_GATED_FORMATS = frozenset({'zip', 'step', 'glb', 'generator-body-step', 'generator-preview-glb',
                                   'generator-cleaned-stl', 'generator-bundle-zip'})


def _env_seconds(environ, name, default):
    env = os.environ if environ is None else environ
    try:
        value = float(str(env.get(name, '')).strip())
    except ValueError:
        return default
    return value if 0 < value <= 3600 else default


def export_min_bytes_per_second(environ=None) -> int:
    env = os.environ if environ is None else environ
    raw = str(env.get(EXPORT_MIN_RATE_ENV, '')).strip()
    if raw.isascii() and raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return EXPORT_MIN_RATE_DEFAULT


def export_send_timeout(environ=None) -> float:
    return _env_seconds(environ, EXPORT_SEND_TIMEOUT_ENV, EXPORT_SEND_TIMEOUT_DEFAULT)


def export_deadline(environ=None) -> float:
    return _env_seconds(environ, EXPORT_DEADLINE_ENV, EXPORT_DEADLINE_DEFAULT)


def export_gate_min_bytes(environ=None) -> int:
    env = os.environ if environ is None else environ
    raw = str(env.get(EXPORT_GATE_MIN_BYTES_ENV, '')).strip()
    if raw.isascii() and raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return EXPORT_GATE_MIN_BYTES_DEFAULT


def export_concurrency(environ=None) -> int:
    """Size of the export gate from the environment: an integer in 1..16, else the default (3)."""
    env = os.environ if environ is None else environ
    raw = str(env.get(EXPORT_CONCURRENCY_ENV, '')).strip()
    if raw.isascii() and raw.isdigit() and 1 <= int(raw) <= EXPORT_CONCURRENCY_MAX:
        return int(raw)
    return EXPORT_CONCURRENCY_DEFAULT


class ExportGate:
    """A non-blocking counting gate with a per-client cap of max(1, size - 1), so no single client can take
    every slot. The event loop is single threaded, so plain counters are race free; release() is idempotent
    per slot (a slot is a one-shot callable)."""

    def __init__(self, size: int):
        self.size = size
        self.per_client = max(1, size - 1)
        self.active = 0
        self._by_client = {}

    def try_enter(self, client=None):
        """A one-shot release callable, or None when every slot (or this client's share) is taken."""
        if self.active >= self.size or self._by_client.get(client, 0) >= self.per_client:
            return None
        self.active += 1
        self._by_client[client] = self._by_client.get(client, 0) + 1
        done = []

        def release():
            if not done:
                done.append(True)
                self.active -= 1
                left = self._by_client.get(client, 1) - 1
                if left > 0:
                    self._by_client[client] = left
                else:
                    self._by_client.pop(client, None)
        return release


class _TooSlow(Exception):
    pass


async def _until_disconnect(receive):
    while True:
        message = await receive()
        if message.get('type') == 'http.disconnect':
            return


class _ExportResponse(StreamingResponse):
    """Streams an export and always releases the gate slot and the artifact lease: after the last byte, on a
    timeout, on a disconnect, on cancellation and on a mid-stream error. Bounds: each send (send_timeout), a
    throughput floor over a sliding window after a grace period (min_rate), and the whole stream (deadline).
    A disconnect reported through receive() stops the stream at once."""

    def __init__(self, content, release, *, send_timeout, deadline, min_rate=0, **kwargs):
        super().__init__(content, **kwargs)
        self._release = release
        self._send_timeout = send_timeout
        self._deadline = deadline
        self._min_rate = min_rate

    async def _stream(self, send):
        loop = asyncio.get_running_loop()
        started, sent = loop.time(), []          # sent: (time, bytes) of completed sends inside the window

        async def bounded(message):
            await asyncio.wait_for(send(message), self._send_timeout)

        def check(now):
            window = EXPORT_RATE_WINDOW_SECONDS
            while sent and sent[0][0] < now - window:
                sent.pop(0)
            elapsed = now - started
            if self._min_rate and elapsed >= EXPORT_RATE_GRACE_SECONDS:
                if sum(n for _, n in sent) < self._min_rate * min(window, elapsed):
                    raise _TooSlow()

        await bounded({'type': 'http.response.start', 'status': self.status_code, 'headers': self.raw_headers})
        async for chunk in self.body_iterator:
            view = memoryview(chunk)
            for start in range(0, len(view), _SEND_SLICE):
                piece = bytes(view[start:start + _SEND_SLICE])
                await bounded({'type': 'http.response.body', 'body': piece, 'more_body': True})
                now = loop.time()
                sent.append((now, len(piece)))
                check(now)
        await bounded({'type': 'http.response.body', 'body': b'', 'more_body': False})

    async def __call__(self, scope, receive, send):
        stream = asyncio.ensure_future(self._stream(send))
        watch = asyncio.ensure_future(_until_disconnect(receive))
        try:
            done, _ = await asyncio.wait({stream, watch}, timeout=self._deadline,
                                         return_when=asyncio.FIRST_COMPLETED)
            if stream in done:
                try:
                    stream.result()
                except (asyncio.TimeoutError, _TooSlow):
                    _LOG.warning('engineering.programs export_cut')   # fixed line: no id, path or client
        finally:
            # The release sits in its own finally: a second cancellation landing in either await below
            # (e.g. server shutdown after a disconnect cancel) must not leak the slot and the file lease.
            try:
                for task in (stream, watch):
                    task.cancel()
                await asyncio.gather(stream, watch, return_exceptions=True)
                try:
                    await self.body_iterator.aclose()
                except Exception:  # noqa: BLE001 - a worker thread may still be inside the generator
                    pass
            finally:
                self._release()


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
        from .program.adapters import adapter_for
        from .editable_plate import CAPABILITY_ID as PLATE_ID
        from .editable_plate_adapter import EditablePlateAdapter
        from .equipment_bay_adapter import EquipmentBayAdapter
        from .thermal_bar import CAPABILITY_ID as THERMAL_ID
        from .thermal_bar_adapter import ThermalBarAdapter
        from .equipment_bay import CAPABILITY_ID as BAY_ID
        def registered(backend, **options):
            if backend == 'picogk':
                regen = engineering.jobs.generators.get('regen_chamber')
                options['runner'] = getattr(regen, '_runner', None)
            return adapter_for(backend, **options)

        service = RevisionService(engineering.store, engineering.jobs, engineering.publisher,
                                  engineering.briefs, adapter_factory=registered)
        engineering.jobs.generators.setdefault(BAY_ID, EquipmentBayAdapter())
        engineering.jobs.generators.setdefault(PLATE_ID, EditablePlateAdapter())
        engineering.jobs.generators.setdefault(THERMAL_ID, ThermalBarAdapter())
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

    gate = ExportGate(export_concurrency())
    send_timeout, deadline, gate_min = export_send_timeout(), export_deadline(), export_gate_min_bytes()
    min_rate = export_min_bytes_per_second()

    async def revision_export(request: Request, rid: str, fmt: str):
        async def call(face, client):
            if not api.is_export_format(fmt):
                raise InterfaceFailure(api.not_found())
            def busy():
                return InterfaceFailure(api.busy('admission_busy', retry_after=EXPORT_RETRY_AFTER_SECONDS))
            # Source formats are tiny. Always-large binary formats take a slot before they are opened; any other
            # bundle format takes one only when its verified size is above the threshold.
            slot = None
            if fmt in _ALWAYS_GATED_FORMATS:
                slot = gate.try_enter(client)
                if slot is None:
                    raise busy()
            try:
                stream = await face.export_stream(api.ExportRequest(revision_id=rid, fmt=fmt), client=client)
            except BaseException:
                if slot is not None:
                    slot()
                raise
            if slot is None and fmt not in api.EXPORT_SOURCE_FORMATS and stream.metadata.size_bytes > gate_min:
                slot = gate.try_enter(client)
                if slot is None:
                    stream.close()
                    raise busy()
            meta = stream.metadata

            def release():
                try:
                    stream.close()
                finally:
                    if slot is not None:
                        slot()

            async def body():
                blocks = stream.blocks()
                try:
                    while True:
                        block = await asyncio.to_thread(next, blocks, None)
                        if block is None:
                            break
                        yield block
                finally:
                    try:
                        blocks.close()
                    except ValueError:  # a worker thread is still inside next(): release() closes the file
                        pass

            try:
                return _ExportResponse(body(), release, send_timeout=send_timeout, deadline=deadline,
                                       min_rate=min_rate, media_type=meta.content_type, headers={
                    'Cache-Control': 'no-store',
                    'Content-Disposition': 'attachment; filename="%s"' % meta.filename,
                    'Content-Length': str(meta.size_bytes),
                    'X-Content-SHA256': meta.sha256})
            except BaseException:
                release()
                raise
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
