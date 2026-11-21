"""Rectangular plate adapter for the checked-design registry (E03b).

The generator "specification" is the whole reviewed ``PlateBrief`` (mode, requirements, one to four
candidate thicknesses), so the registry sees exactly one ``GeneratorCandidate``. ``evaluate()`` runs the
thickness candidates in ascending order, one bounded subprocess each (same runner policy and environment
allowlist as ``service.generate_verified``), and stops at the first that passes: the smallest passing
thickness in the reviewed set. Native work is ``plate_geometry.export_step`` plus
``plate_verify.verify_plate_step``; this module imports neither cadquery nor OCP.

Evidence mapping. Every guard and check gets an ``evaluated`` count (1 or 0) beside its real measurements.
A check never reached because an earlier guard or check rejected the decisive candidate is failed with
``evaluated`` 0 and a reason naming the rejecting id. Checks describe the decisive attempt (the selected
one, or the last attempted when all fail); the generator report keeps every attempt. Limits come from the
closed table in ``capabilities`` (``plate_spec.PLATE_NUMERICAL_V1`` and the reviewed brief), never from a
report. Anything missing or inconsistent raises ``EngineeringUnavailable``: never an accepted run.
"""
from __future__ import annotations

import sys

if __name__ == '__main__' and not __package__:
    # Worker entry: the host runs this file as a script in a subprocess.
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from engineering.plate_adapter import worker_main
    raise SystemExit(worker_main(sys.argv[1:]))

import asyncio
import hashlib
import json
import math
import os
import stat
import tempfile
from pathlib import Path

from pydantic import ValidationError

from .capabilities import PLATE, UnsupportedCapability
from .contracts import (ArtifactIdentity, CheckState, EngineeringBrief, GeneratorBriefContent,
                        GeneratorCandidate, GeneratorCheckResult, Measurement)
from .plate_spec import CHECK_IDS, GUARD_IDS, PLATE_NUMERICAL_V1, PlateBrief, PlateSpec, canonical_spec, specification_digest
from .service import EngineeringUnavailable

METHOD = 'plate_verify.verify_plate_step'
METHOD_VERSION = PLATE.numerical_version
STEP, REPORT, SPEC = 'generator-body.step', 'generator-report.json', 'generator-spec.canonical.json'
REPORT_SCHEMA = 'plate_generator_report_v1'
REPORT_LIMIT = 1048576
ENV_KEYS = ('PATH', 'HOME', 'LANG', 'SYSTEMROOT', 'WINDIR', 'USERPROFILE', 'TEMP', 'TMP')
UNITS = {'evaluated': 'count', 'width_to_thickness': 'ratio', 'aspect_ratio_lower': 'ratio',
         'aspect_ratio_upper': 'ratio', 'poissons_ratio_lower': 'ratio', 'poissons_ratio_upper': 'ratio',
         'youngs_modulus_positive': 'count', 'solid_count': 'count', 'face_count': 'count',
         'planar_face_count': 'count', 'axis_aligned_face_count': 'count', 'dimension_error_mm': 'mm',
         'origin_offset_mm': 'mm', 'volume_relative_error': 'ratio', 'deflection_to_thickness': 'ratio',
         'equilibrium_relative_error': 'ratio', 'deflection_relative_change': 'ratio',
         'stress_relative_change': 'ratio', 'quarter_model_by_construction': 'count',
         'deflection_reference_error': 'ratio', 'stress_reference_error': 'ratio',
         'deflection_with_shear_allowance_mm': 'mm', 'von_mises_max_mpa': 'MPa'}
_SINGLE = {'thin_ratio': 'width_to_thickness', 'small_deflection': 'deflection_to_thickness',
           'equilibrium': 'equilibrium_relative_error',
           'mesh_convergence_deflection': 'deflection_relative_change',
           'mesh_convergence_stress': 'stress_relative_change',
           'benchmark_deflection': 'deflection_reference_error', 'benchmark_stress': 'stress_reference_error',
           'deflection_limit': 'deflection_with_shear_allowance_mm', 'stress_limit': 'von_mises_max_mpa'}


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_brief(brief: PlateBrief) -> bytes:
    return json.dumps(brief.model_dump(mode='json'), sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def _num(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EngineeringUnavailable('Plate report holds a non-numeric measurement')
    return float(value)


def _gate(verify: dict, check_id: str) -> dict:
    gate = (verify['guards'] if check_id in GUARD_IDS else verify['checks']).get(check_id)
    if not isinstance(gate, dict) or gate.get('state') not in ('passed', 'failed', 'not_evaluated'):
        raise EngineeringUnavailable('Plate report check is malformed')
    return gate


def _values(check_id: str, gate: dict, nominal: dict, evaluated: bool) -> dict:
    """Measurement rows for one check; finite sentinels (0.0) when not evaluated."""
    quantity = _num(gate['quantity']) if evaluated and check_id != 'symmetry' else 0.0
    if check_id in _SINGLE:
        return {_SINGLE[check_id]: quantity}
    if check_id == 'aspect_ratio':
        return {'aspect_ratio_lower': quantity, 'aspect_ratio_upper': quantity}
    if check_id == 'material':
        return {'poissons_ratio_lower': quantity, 'poissons_ratio_upper': quantity,
                'youngs_modulus_positive': 1.0 if _num(nominal['youngs_modulus_mpa']) > 0 else 0.0}
    if check_id == 'symmetry':
        return {'quarter_model_by_construction': 1.0 if evaluated else 0.0}
    # geometry
    zero = dict(solid_count=0.0, face_count=0.0, planar_face_count=0.0, axis_aligned_face_count=0.0,
                dimension_error_mm=0.0, origin_offset_mm=0.0, volume_relative_error=0.0)
    if not evaluated:
        return zero
    m = gate.get('measurements')
    if not isinstance(m, dict):
        raise EngineeringUnavailable('Plate geometry measurements are missing')
    dims = ('length_a_mm', 'width_b_mm', 'thickness_mm')
    return dict(solid_count=_num(m['solids']), face_count=_num(m['faces']),
                planar_face_count=_num(m['planar_faces']), axis_aligned_face_count=_num(m['axis_aligned_faces']),
                dimension_error_mm=max(abs(_num(m[k]) - _num(nominal[k])) for k in dims),
                origin_offset_mm=max(abs(_num(v)) for v in m['bbox_min']), volume_relative_error=quantity)


def _reason(check_id: str, gate: dict, verify: dict, evaluated: bool) -> str | None:
    state = gate['state']
    if state == 'passed':
        return None
    if state == 'not_evaluated':
        rejection = verify.get('rejection')
        if not isinstance(rejection, dict) or not isinstance(rejection.get('id'), str):
            raise EngineeringUnavailable('Plate report has an unevaluated check without a rejection')
        return f"not evaluated: rejected earlier by {rejection['id']}"[:240]
    if check_id == 'thin_ratio' and evaluated:
        return f"thin_ratio: b/h = {gate['quantity']:.4g} < {_num(gate['limit']):g}"
    text = gate.get('reason') if isinstance(gate.get('reason'), str) and gate['reason'] else 'failed'
    if evaluated and gate.get('quantity') is not None:
        text += f" (value {gate['quantity']:.6g}, limit {json.dumps(gate.get('limit'), sort_keys=True)})"
    return f'{check_id}: {text}'[:240]


def check_data(attempt: dict) -> dict:
    """``{check_id: {'rows': {name: value}, 'reason': str | None, 'gate_state': str}}`` for one attempt."""
    verify, nominal = attempt['verify'], attempt['nominal']
    data = {}
    for check_id in PLATE.base_checks:
        gate = _gate(verify, check_id)
        evaluated = gate['state'] in ('passed', 'failed') and (gate.get('quantity') is not None
                                                                or check_id == 'symmetry')
        rows = {'evaluated': 1.0 if evaluated else 0.0, **_values(check_id, gate, nominal, evaluated)}
        data[check_id] = dict(rows=rows, reason=_reason(check_id, gate, verify, evaluated),
                              gate_state=gate['state'])
    return data


def parse_report(report: dict) -> dict:
    """``{check_id: {measurement name: value}}`` of the decisive (last) attempt (raises if incomplete)."""
    if not isinstance(report, dict) or report.get('schema') != REPORT_SCHEMA:
        raise EngineeringUnavailable('Plate report is malformed')
    attempts = report.get('attempts')
    if not isinstance(attempts, list) or not attempts or not isinstance(attempts[-1], dict):
        raise EngineeringUnavailable('Plate report has no attempts')
    return {cid: dict(d['rows']) for cid, d in check_data(attempts[-1]).items()}


def read_bounded(path, limit: int) -> bytes:
    """Read a regular file (never a symlink) of at most ``limit`` bytes."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise EngineeringUnavailable('Plate output is not a regular file')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise EngineeringUnavailable('Plate output is not a regular file')
            data = source.read(limit + 1)
    except OSError:
        raise EngineeringUnavailable('Plate output unreadable') from None
    if len(data) > limit:
        raise EngineeringUnavailable('Plate output exceeds the run budget')
    return data


def _nominal(spec: PlateSpec) -> dict:
    return dict(length_a_mm=spec.length_a_mm, width_b_mm=spec.width_b_mm, thickness_mm=spec.thickness_mm,
                youngs_modulus_mpa=spec.youngs_modulus_mpa)


def build_evaluation(candidate: GeneratorCandidate, brief: PlateBrief, attempts: list, step_bytes: bytes | None) -> dict:
    """The evaluation dict ``assess_generator`` documents.

    ``attempts``: ``[{'spec': PlateSpec, 'verify': plate_verify report, 'step': bytes}]`` in ascending
    thickness order; ``step_bytes`` is the selected STEP (accepted runs only).
    """
    from .policy import _comparison, report_digest
    candidate = GeneratorCandidate.model_validate(candidate.model_dump())
    if candidate.content.descriptor.id != PLATE.id:
        raise EngineeringUnavailable('Candidate belongs to another capability')
    spec_bytes = canonical_brief(brief)
    if _sha(spec_bytes) != candidate.candidate_sha256:
        raise EngineeringUnavailable('Candidate digest mismatch')
    specs = brief.candidates()
    if not attempts or len(attempts) > len(specs) or [a['spec'] for a in attempts] != list(specs[:len(attempts)]):
        raise EngineeringUnavailable('Attempts differ from the reviewed thickness set')
    passes = [a['verify']['passed'] is True for a in attempts]
    accepted = passes[-1]
    if any(passes[:-1]) or (not accepted and len(attempts) != len(specs)):
        raise EngineeringUnavailable('Attempts do not follow the smallest-passing-thickness rule')
    if accepted != (step_bytes is not None):
        raise EngineeringUnavailable('Accepted runs deliver the STEP; rejected runs deliver none')
    record = [dict(ordinal=i, thickness_mm=float(a['spec'].thickness_mm), spec_sha256=specification_digest(a['spec']),
                   nominal=_nominal(a['spec']), passed=passes[i], verify=a['verify'])
              for i, a in enumerate(attempts)]
    decisive = record[-1]
    report = dict(
        schema=REPORT_SCHEMA, scope=decisive['verify']['scope'], family=PLATE.id, family_version=PLATE.version,
        passed=accepted, outcome='accepted' if accepted else 'rejected', engineering_certified=False,
        standards=dict(state='not_evaluated', reason='No engineering standard conformity implemented'),
        spec_sha256=candidate.candidate_sha256, mode=brief.mode,
        candidate_thicknesses_mm=list(brief.candidate_thicknesses_mm),
        selected_thickness_mm=decisive['thickness_mm'] if accepted else None,
        numerical_policy_version=PLATE_NUMERICAL_V1['version'], method=METHOD, selection_rule=PLATE.selection_rule,
        implementation_sha256=decisive['verify'].get('implementation_sha256'),
        versions=decisive['verify'].get('versions'), corner_forces=decisive['verify'].get('corner_forces'),
        limitations=list(PLATE.limitations), attempts=record)
    if accepted:
        report['step_sha256'] = _sha(step_bytes)
        if decisive['verify'].get('step_sha256') != report['step_sha256']:
            raise EngineeringUnavailable('STEP digest mismatch')
    try:
        report_bytes = (json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    except (TypeError, ValueError):
        raise EngineeringUnavailable('Plate report is not finite JSON') from None
    if len(report_bytes) > REPORT_LIMIT:
        raise EngineeringUnavailable('Plate report exceeds the budget')
    report = json.loads(report_bytes)
    data = check_data(report['attempts'][-1])
    limits = PLATE.expected_limits(candidate.content.specification)
    identities = (ArtifactIdentity(path=STEP, sha256=_sha(step_bytes), bytes=len(step_bytes)),) if accepted else ()
    checks = []
    for check_id in PLATE.base_checks:
        rows = data[check_id]['rows']
        ok = all(_comparison(rows[limit.name], limit) for limit in limits[check_id])
        if ok != (data[check_id]['gate_state'] == 'passed'):
            raise EngineeringUnavailable('Plate check state contradicts its measurements')
        reason = None if ok else (data[check_id]['reason'] or f'{check_id} did not pass')
        checks.append(GeneratorCheckResult(
            capability_id=PLATE.id, check_id=check_id, state=CheckState.PASSED if ok else CheckState.FAILED,
            method=METHOD, method_version=METHOD_VERSION, brief_sha256=candidate.brief_sha256,
            candidate_sha256=candidate.candidate_sha256,
            measurements=tuple(Measurement(name=n, value=v, unit=UNITS[n]) for n, v in rows.items()),
            limits=limits[check_id], artifacts=identities, reason=reason))
    failed = [c for c in checks if c.state == CheckState.FAILED]
    if accepted and failed or (not accepted and not failed):
        raise EngineeringUnavailable('Plate verdict contradicts its checks')
    if accepted:
        artifacts = {STEP: step_bytes, REPORT: report_bytes, SPEC: spec_bytes}
        stated = {STEP: report['step_sha256'], REPORT: _sha(report_bytes), SPEC: _sha(spec_bytes)}
        diagnostics = {}
    else:
        artifacts = {}
        diagnostics = {REPORT: report_bytes, SPEC: spec_bytes}
        stated = {REPORT: _sha(report_bytes), SPEC: _sha(spec_bytes)}
    return dict(candidate=candidate, checks=tuple(checks), outcome='rejected' if failed else 'accepted',
                passed=not failed, spec_sha256=report['spec_sha256'], report=report,
                report_sha256=report_digest(report), report_bytes=report_bytes, artifacts=artifacts,
                diagnostics=diagnostics, stated_digests=stated, extra_digests=())


def _checked_stage(spec: PlateSpec, result) -> tuple[dict, bytes]:
    try:
        verify, step = result
        sets = (set(verify['guards']) == set(GUARD_IDS), set(verify['checks']) == set(CHECK_IDS))
        ok = (isinstance(verify, dict) and type(verify['passed']) is bool and all(sets)
              and verify['spec_sha256'] == specification_digest(spec) and verify.get('engineering_certified') is False
              and type(step) is bytes and step and verify.get('step_sha256') == _sha(step))
    except (KeyError, TypeError, ValueError):
        ok = False
    if not ok:
        raise EngineeringUnavailable('Plate stage evidence is malformed or does not match the reviewed thickness')
    return verify, step


def worker_main(argv) -> int:
    """Subprocess entry: ``spec.json step report.json`` -> STEP + verifier report."""
    spec_path, step_path, report_path = map(Path, argv)
    spec = PlateSpec.model_validate_json(spec_path.read_text(encoding='utf-8'))
    from .plate_geometry import export_step
    from .plate_verify import verify_plate_step
    export_step(spec, step_path)
    report = verify_plate_step(step_path, spec)
    report_path.write_text(json.dumps(report, allow_nan=False), encoding='utf-8')
    return 0


class PlateAdapter:
    """``runner``: optional getter of a shared ``ProcessRunner``; by default each candidate gets its own
    (timeout 30 s, 1 MiB output, 1536 MiB). ``stage``: injectable ``async (PlateSpec) -> (report, step bytes)``."""
    descriptor = PLATE

    def __init__(self, runner=None, stage=None):
        self._runner = runner
        self._stage = stage or self._native_stage

    def candidates(self, brief: EngineeringBrief) -> tuple[GeneratorCandidate, ...]:
        brief = EngineeringBrief.model_validate(brief.model_dump())
        content = brief.content
        if not isinstance(content, GeneratorBriefContent) or content.descriptor.id != PLATE.id:
            raise UnsupportedCapability('Brief does not belong to the rectangular plate capability')
        try:
            plate = PLATE.parse_spec(content.specification)
        except (ValidationError, ValueError, TypeError):
            raise UnsupportedCapability('Plate specification is not valid') from None
        return (GeneratorCandidate.from_brief(brief, _sha(canonical_brief(plate))),)

    def measurements(self, report_bytes: bytes) -> dict:
        return parse_report(json.loads(report_bytes))

    async def _native_stage(self, spec: PlateSpec):
        from compiler import _WORK_DIR
        from process_runner import ProcessRunner
        with tempfile.TemporaryDirectory(prefix='engineering-plate-', dir=_WORK_DIR) as temporary:
            stage = Path(temporary)
            spec_path, step_path, report_path = (stage / n for n in ('spec.json', 'model.step', 'report.json'))
            spec_path.write_text(canonical_spec(spec), encoding='utf-8')
            env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
            env.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
            owned = self._runner is None
            runner = ProcessRunner(timeout=30, output_limit=1048576, memory_mb=1536) if owned else self._runner()
            try:
                outcome = await runner.run([sys.executable, str(Path(__file__).resolve()), str(spec_path),
                                            str(step_path), str(report_path)], cwd=str(stage), env=env)
            finally:
                if owned:
                    await runner.close()
            if outcome.get('code') or outcome.get('returncode') != 0:
                raise EngineeringUnavailable('Plate worker failed or exceeded its resource budget')
            report_bytes = read_bounded(report_path, REPORT_LIMIT)
            step_bytes = read_bounded(step_path, PLATE.resources.native_output_bytes)
        try:
            report = json.loads(report_bytes, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        except ValueError:
            raise EngineeringUnavailable('Plate report is not valid finite JSON') from None
        return report, step_bytes

    async def evaluate(self, candidate: GeneratorCandidate) -> dict:
        candidate = GeneratorCandidate.model_validate(candidate.model_dump())
        try:
            brief = PLATE.parse_spec(candidate.content.specification)
        except (ValidationError, ValueError, TypeError):
            raise UnsupportedCapability('Plate specification is not valid') from None
        if _sha(canonical_brief(brief)) != candidate.candidate_sha256:
            raise EngineeringUnavailable('Candidate digest mismatch')
        attempts, step = [], None
        try:
            for spec in brief.candidates():
                verify, step_bytes = _checked_stage(spec, await self._stage(spec))
                attempts.append(dict(spec=spec, verify=verify))
                if verify['passed']:
                    step = step_bytes
                    break
            return build_evaluation(candidate, brief, attempts, step)
        except (asyncio.CancelledError, EngineeringUnavailable):
            raise
        except Exception:
            raise EngineeringUnavailable('Plate evaluation unavailable') from None
