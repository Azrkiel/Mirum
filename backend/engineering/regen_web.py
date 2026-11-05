"""Local browser interface for the fixed PicoGK runner; no model service required."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError

from .regen_cli import MAX_SPEC_BYTES, RUNTIME_SO, SAFETY_NOTICE
from .regen_spec import RegenChamberSpec, canonical_spec
from .regen_presets import EXAMPLES

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
FILES = {'stl': 'cleaned.stl', 'report': 'report.json', 'spec': 'spec.canonical.json'}


class BrowserSpec(RegenChamberSpec):
    # Keep browser requests within the documented coarse-resolution workflow.
    voxel_size_mm: float = Field(ge=0.4, le=1.0)


def _builder() -> Path:
    return Path(os.environ.get('REGEN_BUILDER', '/opt/regen/app/builder'))


def _ready() -> bool:
    builder = _builder()
    return builder.is_file() and os.access(builder, os.X_OK) and (builder.parent / RUNTIME_SO).is_file()


def _worker_command(spec: Path, out: Path) -> list[str]:
    return [sys.executable, '-m', 'engineering.regen_web', '--worker', str(spec), str(out)]


def _cross_origin(request: Request) -> bool:
    origin = request.headers.get('origin')
    return bool((origin and urlsplit(origin).netloc != request.headers.get('host'))
                or request.headers.get('sec-fetch-site') == 'cross-site')


class JobRunner:
    """One disposable generation process at a time; bounded, temporary local results."""

    def __init__(self, root: Path | None = None, *, timeout=900, retention=3600, max_jobs=4, output_limit=1 << 30):
        self.root = root or Path(tempfile.mkdtemp(prefix='regen-web-'))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.timeout, self.retention, self.max_jobs = timeout, retention, max_jobs
        self.output_limit = output_limit
        self.jobs = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='regen')
        self.process = None
        self.closed = False

    def _prune(self):
        finished = sorted((j for j in self.jobs.values() if j['status'] not in ('queued', 'running')),
                          key=lambda j: j['updated'])
        for job in finished:
            if time.time() - job['updated'] > self.retention or len(self.jobs) >= self.max_jobs:
                shutil.rmtree(self.root / job['id'], ignore_errors=True)
                del self.jobs[job['id']]

    def submit(self, spec: BrowserSpec) -> dict:
        with self.lock:
            if self.closed:
                raise HTTPException(503, 'Generator is shutting down')
            if any(j['status'] in ('queued', 'running') for j in self.jobs.values()):
                raise HTTPException(409, 'A generation is already running. Wait for it to finish.')
            self._prune()
            job_id = uuid.uuid4().hex
            folder = self.root / job_id
            folder.mkdir(mode=0o700)
            (folder / 'input.json').write_text(canonical_spec(spec))
            job = dict(id=job_id, status='queued', message='Waiting for generator', updated=time.time(),
                       report=None, artifacts={})
            self.jobs[job_id] = job
            self.pool.submit(self._run, job_id)
            return dict(job)

    def get(self, job_id: str) -> dict:
        with self.lock:
            self._prune_expired()
            if job_id not in self.jobs:
                raise HTTPException(404, 'Result not found or expired. Generate again.')
            return dict(self.jobs[job_id])

    def _prune_expired(self):
        for job in list(self.jobs.values()):
            if job['status'] not in ('queued', 'running') and time.time() - job['updated'] > self.retention:
                shutil.rmtree(self.root / job['id'], ignore_errors=True)
                del self.jobs[job['id']]

    def _update(self, job_id, **fields):
        with self.lock:
            self.jobs[job_id].update(fields, updated=time.time())

    def _run(self, job_id):
        folder = self.root / job_id
        out = folder / 'output'
        out.mkdir()
        self._update(job_id, status='running', message='Building and checking geometry')
        # Do not pass provider credentials or unrelated process variables to the worker.
        env = dict(PATH=os.environ.get('PATH', '/usr/bin:/bin'), PYTHONPATH=str(BACKEND),
                   PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='2', OMP_NUM_THREADS='2',
                   DOTNET_GCHeapHardLimit='0x10000000',
                   REGEN_BUILDER=str(_builder()),
                   REGEN_GCC_LIB=os.environ.get('REGEN_GCC_LIB', '/opt/regen/lib'))
        try:
            with (folder / 'worker.log').open('wb') as output:
                with self.lock:
                    if self.closed:
                        return
                    proc = subprocess.Popen(_worker_command(folder / 'input.json', out), cwd=folder,
                                            env=env, stdout=output, stderr=subprocess.STDOUT,
                                            start_new_session=True)
                    self.process = proc
                try:
                    deadline = time.monotonic() + self.timeout
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            self._kill(proc)
                            self._update(job_id, status='unavailable', message='Generation exceeded its time limit.')
                            return
                        if self._output_size(folder) > self.output_limit:
                            self._kill(proc)
                            self._update(job_id, status='unavailable', message='Generation exceeded its output size limit.')
                            return
                        try:
                            code = proc.wait(timeout=min(0.5, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    if self._output_size(folder) > self.output_limit:
                        raise ValueError('Generation exceeded its output size limit')
                finally:
                    with self.lock:
                        self.process = None
            if code == 2:
                report = self._report(out / 'rejected_report.json')
                self._update(job_id, status='rejected', message='Geometry did not pass the checks.', report=report)
            elif code == 0:
                report = self._report(out / 'report.json')
                mesh = out / FILES['stl']
                if (not report or report.get('passed') is not True
                        or report.get('geometry_source') != 'picogk_builder'
                        or report.get('engineering_certified') is not False
                        or not (out / FILES['spec']).is_file()
                        or self._digest(out / FILES['spec']) != self._digest(folder / 'input.json')
                        or report.get('spec_sha256') != self._digest(folder / 'input.json')
                        or not mesh.is_file() or self._digest(mesh) != report.get('stl_sha256')):
                    raise ValueError('Accepted artifacts were incomplete or inconsistent')
                self._update(job_id, status='accepted', message='Mesh passed the geometry checks.', report=report,
                             artifacts={name: f'/api/jobs/{job_id}/artifacts/{name}' for name in FILES})
            else:
                self._update(job_id, status='unavailable', message='Generator could not complete. Check the server log.')
                log.error('Regen worker %s exited %s; log: %s', job_id, code, folder / 'worker.log')
        except Exception:
            log.exception('Regen job %s failed', job_id)
            self._update(job_id, status='unavailable', message='Generator could not complete. Check the server log.')
        finally:
            # Failed outputs must not accumulate large unservable meshes.
            if self.jobs[job_id]['status'] != 'accepted':
                shutil.rmtree(out, ignore_errors=True)

    @staticmethod
    def _output_size(folder):
        total = 0
        for path in folder.rglob('*'):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass  # The worker atomically replaces and removes temporary files.
        return total

    @staticmethod
    def _digest(path):
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1 << 20), b''):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _report(path):
        if not path.is_file() or path.stat().st_size > 4 << 20:
            return None
        report = json.loads(path.read_text())
        return report if isinstance(report, dict) else None

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

    def close(self):
        with self.lock:
            self.closed = True
            proc = self.process
        if proc is not None:
            self._kill(proc)
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.process = None


@asynccontextmanager
async def regen_lifespan(app, runner: JobRunner | None = None):
    """Own one serial runner for either the main API or the standalone app."""
    app.state.regen_runner = runner or JobRunner()
    try:
        yield
    finally:
        await asyncio.to_thread(app.state.regen_runner.close)


def attach_regen_routes(app, *, prefix='/engineering/regen', limiter=None):
    """Share the same bounded generation and acceptance path across both UIs."""
    def limited(rate):
        return limiter.limit(rate) if limiter else lambda function: function

    def public_job(job):
        return dict(job, artifacts={name: f'{prefix}/jobs/{job["id"]}/artifacts/{name}'
                                   for name in job['artifacts']})

    @app.get(f'{prefix}/config')
    @limited('120/minute')
    def config(request: Request):
        return dict(ready=_ready(), examples=EXAMPLES, notice=SAFETY_NOTICE,
                    schema=BrowserSpec.model_json_schema())

    @app.post(f'{prefix}/jobs', status_code=202)
    @limited('2/minute')
    async def generate(request: Request):
        if _cross_origin(request):
            raise HTTPException(403, 'Cross-origin generation is not allowed')
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_SPEC_BYTES:
                raise HTTPException(413, 'Specification is too large')
            raw.extend(chunk)
        try:
            spec = BrowserSpec.model_validate_json(raw)
        except ValidationError as exc:
            return JSONResponse({'detail': exc.errors(include_url=False, include_context=False, include_input=False)}, status_code=422)
        if not _ready():
            raise HTTPException(503, 'PicoGK runtime is not installed. Run the repository setup script first.')
        return public_job(request.app.state.regen_runner.submit(spec))

    @app.get(f'{prefix}/jobs/{{job_id}}')
    @limited('120/minute')
    def status(job_id: str, request: Request):
        return public_job(request.app.state.regen_runner.get(job_id))

    @app.get(f'{prefix}/jobs/{{job_id}}/artifacts/{{name}}')
    @limited('30/minute')
    def artifact(job_id: str, name: str, request: Request):
        runner = request.app.state.regen_runner
        job = runner.get(job_id)
        if name not in FILES:
            raise HTTPException(404, 'Artifact not found')
        if job['status'] != 'accepted':
            raise HTTPException(409, 'Only accepted results have downloadable artifacts')
        path = runner.root / job_id / 'output' / FILES[name]
        return FileResponse(path, filename=FILES[name], media_type='model/stl' if name == 'stl' else 'application/json')


def create_app(runner: JobRunner | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        async with regen_lifespan(app, runner):
            yield

    app = FastAPI(title='Mirum PicoGK', lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware('http')
    async def headers(request, call_next):
        # Same-origin mutations prevent another website from submitting local builds.
        if request.method == 'POST' and _cross_origin(request):
            return JSONResponse({'detail': 'Cross-origin generation is not allowed'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                                       "connect-src 'self'; img-src 'self' blob: data:; frame-ancestors 'none'")
        return response

    attach_regen_routes(app, prefix='/api')
    app.mount('/', StaticFiles(directory=ROOT / 'frontend' / 'regen', html=True), name='browser')
    return app


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--worker':
        raise SystemExit('Start the web app with codex-cloud/web.sh')
    import resource
    # Caps apply to this disposable worker and its builder, never the web server.
    resource.setrlimit(resource.RLIMIT_AS, (4 << 30, 4 << 30))
    # RLIMIT_FSIZE would break .NET's sparse 2 TiB doublemapper memfd. The parent
    # monitors actual output directory size and kills the process group instead.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from .regen_cli import main
    raise SystemExit(main(sys.argv[2:]))
