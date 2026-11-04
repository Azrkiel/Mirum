"""Bounded CEM jobs and evidence endpoints, accessible without model calls."""
import asyncio
import json
import logging
import math
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field

from process_runner import ProcessRunner
from solver_slot import slot
from cem.core import StrictModel, canonical_json
from cem.glider.requirements import GliderMissionRequirements
from cem.glider import calibration
from cem.glider.feasibility import feasibility
from cem.glider.service import identity

logger = logging.getLogger('mirum.cem')


class MissionRequest(StrictModel):
    requirements: GliderMissionRequirements
    calibration_version: str | None = Field(None, pattern=r'^[a-f0-9]{64}$', max_length=64)


class DesignRequest(StrictModel):
    discovery_job_id: str = Field(pattern=r'^[a-f0-9]{32}$', max_length=32)
    elite_id: str = Field(pattern=r'^[a-f0-9]{16}$', max_length=16)


class FlightMeasurement(StrictModel):
    launch_height_m: float = Field(ge=.01, le=100)
    launch_method: str = Field(pattern=r'^(drop|hand_toss|discus|catapult|hi_start)$', max_length=16)
    duration_s: float = Field(ge=.1, le=100000)
    distance_m: float | None = Field(None, ge=.1, le=100000)


class FlightTestRequest(StrictModel):
    design_job_id: str = Field(pattern=r'^[a-f0-9]{32}$', max_length=32)
    output_id: str = Field(pattern=r'^[a-f0-9]{12}$', max_length=12)
    measured: FlightMeasurement


class Jobs:
    def __init__(self):
        self.jobs = {}
        self.task = None

    def get(self, key, kind=None):
        value = self.jobs.get(key)
        if value is None or (kind and value['kind'] != kind):
            raise HTTPException(404, 'CEM job not found or expired')
        return value

    def completed(self, key, kind):
        job = self.get(key, kind)
        if job['status'] != 'completed':
            raise HTTPException(409, 'CEM job has not completed')
        return job

    def start(self, payload):
        if self.task and not self.task.done():
            raise HTTPException(429, 'A CEM task is running; retry after it completes')
        slot.check()
        for key, job in list(self.jobs.items()):
            if time.monotonic() - job['created'] > 3600 or len(self.jobs) >= 8:
                shutil.rmtree(job['directory'], ignore_errors=True)
                del self.jobs[key]
        key = uuid.uuid4().hex
        directory = Path(os.environ.get('MIRUM_WORK_DIR', '/tmp/mirum/work')) / 'cem/jobs' / key
        directory.mkdir(parents=True)
        req = GliderMissionRequirements.model_validate(payload['requirements'])
        self.jobs[key] = dict(status='running', directory=directory, created=time.monotonic(),
                              kind=payload['kind'], identity=identity(req, payload['calibration']))
        self.task = asyncio.create_task(self.run(key, payload))
        slot.claim(self)
        return dict(job_id=key, status='running', **self.jobs[key]['identity'])

    async def run(self, key, payload):
        job = self.jobs[key]
        runner = ProcessRunner(max_processes=1, timeout=1800, memory_mb=3072, output_limit=65536)
        env = dict(PATH=os.environ.get('PATH', ''), LANG='C.UTF-8',
                   OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
                   MPLCONFIGDIR=str(job['directory'] / 'matplotlib'))
        try:
            result = await runner.run([sys.executable, str(Path(__file__).with_name('worker.py')), str(job['directory'])],
                                      input=canonical_json(payload).encode(), env=env)
            if result['code'] or result['returncode'] != 0:
                logger.error('CEM worker failed: %s %s', result['code'], result['stderr'][-2000:])
                raise ValueError('Worker failed')
            path = job['directory'] / 'result.json'
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('Report budget exceeded')
            report = json.loads(path.read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite')))
            from cem.evidence import validate
            validate(report, payload, job['identity'], job['directory'])
            job.update(status='completed', result=report)
        except asyncio.CancelledError:
            job.update(status='failed', detail='CEM task cancelled; no outputs accepted')
            raise
        except Exception:
            logger.exception('CEM evidence unavailable')
            job.update(status='failed', detail='CEM task failed or exceeded its evidence/resource budget; no outputs accepted')
        finally:
            try:
                await runner.close()
            finally:
                slot.release(self)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        slot.release(self)


jobs = Jobs()


def attach_cem_routes(app, limiter):
    @app.get('/cem/glider/metadata')
    @limiter.limit('30/minute')
    async def glider_metadata(request: Request):
        from cem.provenance import provenance
        from cem.glider.model import SUPPORTED_FAMILIES
        from cem.glider.processes import PROCESSES
        return dict(cem_version=provenance()['cem_version'], calibration_version=calibration.version(calibration.initial()),
                    supported_families=list(SUPPORTED_FAMILIES), processes=list(PROCESSES),
                    requirements_schema=GliderMissionRequirements.model_json_schema(),
                    capabilities=dict(novel_shape_discovery=False, picogk_builder=False,
                                      parametric_wing_shape_discovery=True,
                                      physical_manufacturing_closure=False, thermal_ridge=False))

    from cem.extraction import TextRequest

    @app.post('/cem/glider/requirements')
    @limiter.limit('10/minute')
    async def glider_requirements(body: TextRequest, request: Request):
        from cem.extraction import extract
        try:
            return await extract(body.prompt)
        except Exception:
            logger.exception('CEM requirements translation failed')
            raise HTTPException(503, 'Requirements translation unavailable; use the typed mission form') from None

    @app.post('/cem/glider/feasibility')
    @limiter.limit('30/minute')
    async def glider_feasibility(body: MissionRequest, request: Request):
        try:
            cal = calibration.load(body.calibration_version)
            return dict(**identity(body.requirements, cal), **feasibility(body.requirements, cal))
        except (OSError, ValueError, KeyError):
            raise HTTPException(422, 'Required calibration or bound evidence is unavailable') from None

    @app.post('/cem/glider/discover', status_code=202)
    @limiter.limit('3/minute')
    async def glider_discover(body: MissionRequest, request: Request):
        if body.requirements.environment.kind in ('thermal', 'ridge'):
            raise HTTPException(422, 'Thermal and ridge models are not qualified in this CEM version')
        try:
            cal = calibration.load(body.calibration_version)
            from cem.glider.model import configuration
            configuration(body.requirements, cal, 'balsa' if body.requirements.process == 'auto' else body.requirements.process)
        except (ValueError, OSError, KeyError):
            raise HTTPException(422, 'Selected calibration, family or process is unavailable') from None
        return jobs.start(dict(kind='discovery', requirements=body.requirements.model_dump(mode='json'), calibration=cal))

    @app.post('/cem/glider/design', status_code=202)
    @limiter.limit('3/minute')
    async def glider_design(body: DesignRequest, request: Request):
        source = jobs.completed(body.discovery_job_id, 'discovery')['result']
        elite = next((e for e in source['candidates'] if e['elite_id'] == body.elite_id), None)
        if elite is None:
            # All occupied archive cells, including those outside the top-k list,
            # are selectable, but arbitrary client supplied designs are not.
            elite = next((e for p in source['per_process'].values() for e in p['archive'] if e['elite_id'] == body.elite_id), None)
        if elite is None:
            raise HTTPException(404, 'Elite does not belong to this archive')
        return jobs.start(dict(kind='design', requirements=source['requirements'], calibration=source['calibration'], elite=elite))

    @app.get('/cem/glider/jobs/{job_id}')
    @limiter.limit('120/minute')
    async def glider_job(job_id: str, request: Request):
        job = jobs.get(job_id)
        response = dict(job_id=job_id, status=job['status'], **job['identity'])
        if job['status'] == 'completed':
            response['result'] = job['result']
        elif job['status'] == 'failed':
            response['detail'] = job['detail']
        else:
            path = job['directory'] / 'progress.json'
            if path.exists() and path.stat().st_size < 4096:
                response['progress'] = json.loads(path.read_text())
        return response

    @app.get('/cem/glider/jobs/{job_id}/bundle')
    @limiter.limit('30/minute')
    async def glider_bundle(job_id: str, request: Request):
        job = jobs.completed(job_id, 'design')
        if job['result']['status'] != 'screened':
            raise HTTPException(409, 'Closure failed; no geometry bundle is deliverable')
        from cem.evidence import file_digest
        try:
            if file_digest(job['directory'] / 'bundle.zip') != job['result']['bundle_sha256']:
                raise ValueError('Revision mismatch')
        except (ValueError, OSError, KeyError):
            raise HTTPException(409, 'Bound artifact evidence is unavailable or changed') from None
        return FileResponse(job['directory'] / 'bundle.zip', media_type='application/zip',
                            filename=f"mirum-cem-{job['result']['output_id']}.zip")

    @app.post('/cem/glider/flight-test')
    @limiter.limit('10/minute')
    async def glider_flight_test(body: FlightTestRequest, request: Request):
        report = jobs.completed(body.design_job_id, 'design')['result']
        if report['output_id'] != body.output_id or report['status'] != 'screened':
            raise HTTPException(409, 'Flight test must identify a completed screened design')
        if body.measured.launch_method != 'drop' or report['requirements']['launch']['method'] != 'drop':
            raise HTTPException(422, 'Profile drag is not identifiable from a powered release without independent launch energy evidence')
        if report['requirements']['environment']['kind'] != 'still_air':
            raise HTTPException(422, 'Calibration requires a still-air flight log in this version')
        aero = report['closure']['reanalysis']
        observed = body.measured
        if observed.distance_m is None:
            raise HTTPException(422, 'Distance is needed to identify drag independently of duration and launch efficiency')
        ld = observed.distance_m / observed.launch_height_m
        cd = aero['CL_trim'] / ld
        profile = aero['cd_profile']
        ratio = (cd - (aero['CD_trim'] - profile)) / profile
        # Observed range/time must be compatible with the pinned trim model.
        measured_speed = math.hypot(observed.distance_m, observed.launch_height_m) / observed.duration_s
        if abs(measured_speed / aero['glide_speed_m_s'] - 1) > .20:
            raise HTTPException(422, 'Observed flight speed is outside the fixed-trim calibration envelope')
        try:
            base = report['calibration']
            absolute_factor = base['cd0_factor']['median'] * ratio
            updated = calibration.update_drag(base, [absolute_factor])
            updated['flight_test'] = dict(output_id=body.output_id, measured=observed.model_dump(),
                                         elite_id=report['elite_id'], design_revision_sha256=report['design_revision_sha256'],
                                         cem_version=report['cem_version'],
                                         assumptions='Still air, fixed trim speed, unchanged mass/CL/e; profile drag alone updated')
            identifier = calibration.save(updated)
        except (ValueError, OSError, KeyError):
            raise HTTPException(422, 'Flight evidence cannot support a bounded calibration update') from None
        return dict(calibration_version=identifier, parent_calibration_version=report['calibration_version'],
                    coefficients_updated=['cd0_factor'], other_coefficients='not_identifiable; unchanged',
                    archive_rescore='Run discovery with the new calibration_version; old outputs retain their pinned version')
