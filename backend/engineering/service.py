"""No-LLM deterministic builder -> existing acceptance -> geometry-bound FEA."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from .spec import CantileverSpec, build_source, canonical_spec, specification_digest


class EngineeringUnavailable(RuntimeError):
    """Required evidence cannot be obtained; accepted artifacts must not escape."""


async def generate_verified(spec: CantileverSpec) -> dict:
    from compiler import _WORK_DIR
    from process_runner import ProcessRunner
    from verification.acceptance import accept_script

    source = build_source(spec)
    delivery = await accept_script(source, output_variable='result')
    digest = hashlib.sha256(delivery['step']).hexdigest()
    with tempfile.TemporaryDirectory(prefix='engineering-', dir=_WORK_DIR) as temporary:
        stage = Path(temporary)
        step_path, spec_path, report_path = (stage/name for name in ('model.step','spec.json','report.json'))
        step_path.write_bytes(delivery['step'])
        spec_path.write_text(canonical_spec(spec), encoding='utf-8')
        env = {key: os.environ[key] for key in ('PATH','HOME','LANG','SYSTEMROOT','WINDIR',
               'USERPROFILE','TEMP','TMP') if key in os.environ}
        env.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
        runner = ProcessRunner(timeout=30, output_limit=1048576, memory_mb=1536)
        try:
            outcome = await runner.run([sys.executable, str(Path(__file__).with_name('worker.py')),
                                       str(step_path), str(spec_path), str(report_path)], cwd=str(stage), env=env)
        finally:
            await runner.close()
        if outcome.get('code') or outcome.get('returncode') != 0:
            raise EngineeringUnavailable('Engineering worker failed or exceeded its resource budget')
        try:
            if report_path.stat().st_size > 1048576:
                raise ValueError('report budget')
            report = json.loads(report_path.read_text(encoding='utf-8'),
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        except (OSError, ValueError):
            raise EngineeringUnavailable('Required engineering evidence is unavailable') from None
        if not isinstance(report, dict) or type(report.get('passed')) is not bool:
            raise EngineeringUnavailable('Engineering evidence is malformed')
        if report.get('spec_sha256') != specification_digest(spec):
            raise EngineeringUnavailable('Engineering evidence does not match the approved specification')
        if report['passed'] is not True:
            return {'status': 'rejected', 'engineering_report': report}
        required = ('native_geometry','dimensional_evidence_budget','independent_step_read','independent_step_transfer',
                    'single_rectangular_solid','planar_faces','finite_measurements','size_requirements',
                    'datum_frame','axis_aligned_faces','rectangular_face_pairs','complete_prism_metrics',
                    'measured_root_section','mesh_convergence','analytic_benchmark','equilibrium',
                    'small_deflection','small_strain','stress_allowable','displacement_requirement')
        checks = report.get('checks')
        if not isinstance(checks, dict) or any(not isinstance(checks.get(name), dict)
                or checks[name].get('state') != 'passed' for name in required):
            raise EngineeringUnavailable('Required engineering checks are missing or failed')
        if (report.get('step_sha256') != digest or hashlib.sha256(step_path.read_bytes()).hexdigest() != digest
                or report.get('design_intent', {}).get('state') != 'passed'
                or report.get('physics', {}).get('state') != 'passed'
                or report.get('standards', {}).get('state') != 'not_evaluated'
                or report.get('engineering_certified') is not False):
            raise EngineeringUnavailable('Engineering evidence is inconsistent with the delivered revision')
    # Explicitly bind all artifacts, including the same source used by capture.
    report['artifacts'] = dict(delivery['geometry_certificate']['artifacts'])
    report['assumptions'] = spec.assumptions
    return dict(status='accepted', script=source, specification=spec.model_dump(mode='json'),
                engineering_report=report, **delivery)


def approved_candidates(spec: CantileverSpec, heights: list[float]) -> list[CantileverSpec]:
    if not isinstance(heights, list) or len(heights) > 4:
        raise ValueError('At most four explicitly approved candidate heights are supported')
    if len(heights) != len(set(heights)):
        raise ValueError('Approved candidate heights must be unique')
    # Validate the complete search space before starting any native work.
    return [CantileverSpec.model_validate(dict(spec.model_dump(), height_mm=height))
            for height in heights] if heights else [spec]


async def generate_with_feedback(spec: CantileverSpec, heights: list[float]) -> dict:
    candidates = approved_candidates(spec, heights)
    approved = json.dumps({'specification': spec.model_dump(mode='json'),
                           'candidate_heights_mm': heights}, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    request_digest = hashlib.sha256(approved.encode()).hexdigest()
    attempts = []
    for candidate in candidates:
        result = await generate_verified(candidate)
        report = result['engineering_report']
        attempts.append({'height_mm': candidate.height_mm, 'status': result['status'],
                         'spec_sha256': specification_digest(candidate),
                         'failures': report['failures']})
        if result['status'] == 'accepted':
            break
    report['approved_request_sha256'] = request_digest
    result['approved_request'] = {'specification': spec.model_dump(mode='json'),
                                 'candidate_heights_mm': heights}
    result['attempts'] = attempts
    return result

