"""Bounded asynchronous searches, polled results and geometry downloads."""
import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from process_runner import ProcessRunner
from .spec import GliderRequest

logger = logging.getLogger('mirum.gliders')


class Jobs:
    def __init__(self):
        self.jobs = {}
        self.task = None

    def start(self, spec):
        if self.task and not self.task.done():
            raise HTTPException(429, 'A glider search is running; try again after it finishes.')
        # Bounded retention. Downloads expire with jobs; do not touch other application files.
        for key, job in list(self.jobs.items()):
            if time.monotonic() - job['created'] > 3600 or len(self.jobs) >= 8:
                shutil.rmtree(job['directory'], ignore_errors=True)
                del self.jobs[key]
        key = uuid.uuid4().hex
        directory = Path(os.environ.get('MIRUM_WORK_DIR', '/tmp/mirum/work')) / 'gliders' / key
        directory.mkdir(parents=True)
        self.jobs[key] = {'status': 'running', 'created': time.monotonic(), 'directory': directory}
        self.task = asyncio.create_task(self.run(key, spec))
        return {'job_id': key, 'status': 'running'}

    async def run(self, key, spec):
        job = self.jobs[key]
        runner = ProcessRunner(max_processes=1, timeout=900, memory_mb=3072, output_limit=65536)
        # Explicit allowlist: no API tokens or proxy secret placeholders in the solver process.
        env = {'PATH': os.environ.get('PATH', ''), 'LANG': 'C.UTF-8',
               'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
               'MPLCONFIGDIR': str(job['directory'] / 'matplotlib')}
        try:
            result = await runner.run([sys.executable, str(Path(__file__).with_name('worker.py')),
                                       str(job['directory'])], input=spec.model_dump_json().encode(), env=env)
            if result['code'] or result['returncode'] != 0:
                logger.error('Glider worker failed: %s %s', result['code'], result['stderr'][-3000:])
                job.update(status='failed', detail='Search failed or exceeded its time/resource budget. Reduce evaluations and retry.')
            else:
                report = json.loads((job['directory'] / 'result.json').read_text())
                job.update(status='completed', result=report)
        except asyncio.CancelledError:
            job.update(status='failed', detail='Search cancelled by server shutdown.')
            raise
        except Exception:
            logger.exception('Glider job failed')
            job.update(status='failed', detail='Search could not complete; no results are accepted.')
        finally:
            await runner.close()

    def get(self, key):
        if key not in self.jobs:
            raise HTTPException(404, 'Search not found or expired. Searches expire on server restart.')
        return self.jobs[key]

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


jobs = Jobs()


def attach_glider_routes(app, limiter):
    @app.post('/gliders/search', status_code=202)
    @limiter.limit('3/minute')
    async def glider_start(body: GliderRequest, request: Request):
        return jobs.start(body)

    @app.get('/gliders/search/{job_id}')
    async def glider_status(job_id: str):
        job = jobs.get(job_id)
        response = {'job_id': job_id, 'status': job['status']}
        if job['status'] == 'completed':
            response['result'] = job['result']
        elif job['status'] == 'failed':
            response['detail'] = job['detail']
        else:
            progress = job['directory'] / 'progress.json'
            if progress.exists():
                response['progress'] = json.loads(progress.read_text())
        return response

    @app.get('/gliders/search/{job_id}/geometry/{candidate_id}')
    async def glider_geometry(job_id: str, candidate_id: str):
        job = jobs.get(job_id)
        if job['status'] != 'completed':
            raise HTTPException(409, 'Geometry is available after a successful search.')
        accepted = {candidate['id'] for candidate in job['result']['candidates']}
        if candidate_id not in accepted:
            raise HTTPException(404, 'Candidate not found')
        return FileResponse(job['directory'] / f'{candidate_id}.zip', media_type='application/zip',
                            filename=f'mirum-glider-{candidate_id}.zip')
