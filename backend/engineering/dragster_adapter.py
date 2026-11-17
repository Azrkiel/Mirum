"""CO2 dragster (specification v2) adapter for the checked-design registry (U6.3).

``evaluate()`` runs the existing ``dragsters.service.generate`` on the shared dragster runner
(the same runner as ``/dragsters/generate``), reads the four published artifacts, discards the
legacy run, and maps the verifier report into the shared evidence shape. Builders, verifiers and
tolerances are untouched. ``dragsters.service`` needs CadQuery's runtime and the artifact store, so
both are imported inside ``evaluate`` only; the mapping code is pure and runs anywhere.

Mapping rules:

- one check per ``report['checks']`` entry, all against ``body.step``: the 11 ``verify_step_v2``
  checks and the 3 ``hardware.cad_checks`` checks (14 in total, no more, no fewer);
- measurements are the report's numbers; limits come from the closed table in ``capabilities``
  (pinned constants and the reviewed spec), never from the report;
- the report must agree with the reviewed spec (style, wall, clearances); anything missing or
  inconsistent raises ``DragsterUnavailable``: never an accepted run;
- ``verify_step_v2`` raises on any failed check and writes no rejected report, so a failure reaches
  this adapter as ``GenerationError`` and ends unavailable with no package. A rejected evaluation
  exists only for a report that itself records a failed check (``build_evaluation``).
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import math
import os
import re
import stat

from pydantic import ValidationError

from cem.core import canonical_json
from dragsters.spec_v2 import canonical_spec_v2, parse_v2, specification_digest_v2
from dragsters.styles import REGISTRY
from .capabilities import DRAGSTER, UnsupportedCapability
from .contracts import (ArtifactIdentity, CheckState, EngineeringBrief, GeneratorBriefContent,
                        GeneratorCandidate, GeneratorCheckResult, Measurement)

METHOD = 'dragsters.validation_v2.verify_step_v2'
HARDWARE_METHOD = 'dragsters.hardware.cad_checks'
METHOD_VERSION = DRAGSTER.numerical_version
SPEC, REPORT, STEP, GLB = ('generator-spec.json', 'generator-report.json', 'generator-body.step',
                           'generator-preview.glb')
FORMATS = ('spec', 'report', 'step', 'glb')
HARDWARE_CHECKS = ('wheel_clearance_front', 'wheel_clearance_rear', 'body_above_ground')
VERIFY_CHECKS = tuple(c for c in DRAGSTER.base_checks if c not in HARDWARE_CHECKS)
WALL_CHECKS = ('pocket_wall', 'front_bore_wall', 'rear_bore_wall')
VOID_CHECKS = ('pocket_void', 'front_axle_void', 'rear_axle_void')
UNITS = {
    'solid_count': 'count', 'bounds_deviation': 'mm', 'ymin_plus_ymax': 'mm', 'centroid_y': 'mm',
    'volume_relative_error': 'ratio', 'residual_void': 'mm3', 'min_wall_mm': 'mm', 'revalidated': 'count',
    'min_distance_mm': 'mm', 'max_intersection_volume_mm3': 'mm3', 'zmin_mm': 'mm',
}
_FAILED = re.compile(r'STEP checks failed: ([a-z_]+(?:, [a-z_]+)*)\s*$')


class DragsterUnavailable(RuntimeError):
    """Required evidence cannot be obtained; nothing is accepted."""


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _num(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DragsterUnavailable('Report holds a non-numeric measurement')
    return float(value)


def _mapping(value) -> dict:
    if not isinstance(value, dict):
        raise DragsterUnavailable('Report check is malformed')
    return value


def _get(check: dict, key):
    if key not in check:
        raise DragsterUnavailable('Report check lacks its measurements')
    return check[key]


def _sides(check: dict, key) -> dict:
    sides = _mapping(_get(check, key))
    if set(sides) != {'left', 'right'}:
        raise DragsterUnavailable('Report check lacks its measurements')
    return sides


def parse_report(report: dict) -> dict:
    """``{check_id: {measurement name: value}}`` from a v2 report (raises if incomplete)."""
    if not isinstance(report, dict):
        raise DragsterUnavailable('Report is malformed')
    checks = _mapping(report.get('checks'))
    if set(checks) != set(DRAGSTER.base_checks):
        raise DragsterUnavailable('Report checks differ from the verifier set')
    out = {}
    for name in DRAGSTER.base_checks:
        c = _mapping(checks[name])
        if type(c.get('passed')) is not bool:
            raise DragsterUnavailable('Report check state is unknown')
        if name == 'single_valid_solid':
            out[name] = dict(solid_count=_num(_get(c, 'solid_count')))
        elif name == 'absolute_bounds':
            deviations = _mapping(_get(c, 'deviation_mm'))
            if not deviations:
                raise DragsterUnavailable('Report check lacks its measurements')
            out[name] = dict(bounds_deviation=max(abs(_num(v)) for v in deviations.values()))
        elif name == 'symmetry':
            measured = _mapping(_get(c, 'measured'))
            out[name] = dict(ymin_plus_ymax=_num(_get(measured, 'ymin_plus_ymax_mm')),
                             centroid_y=_num(_get(measured, 'centroid_y_mm')))
        elif name == 'reference_volume':
            expected = _num(_get(c, 'expected_mm3'))
            if expected <= 0:
                raise DragsterUnavailable('Report check lacks its measurements')
            out[name] = dict(volume_relative_error=abs(_num(_get(c, 'deviation_mm3'))) / expected)
        elif name in VOID_CHECKS:
            out[name] = dict(residual_void=abs(_num(_get(c, 'residual_body_mm3'))))
        elif name in WALL_CHECKS:
            measured = _mapping(_get(c, 'measured'))
            axes = _get(measured, 'axes')
            if not isinstance(axes, list) or not axes:
                raise DragsterUnavailable('Report check lacks its measurements')
            worst = min(_num(_mapping(a).get('wall_mm')) for a in axes)
            if worst != _num(_get(measured, 'min_wall_mm')):
                raise DragsterUnavailable('Report wall minimum differs from its axes')
            out[name] = dict(min_wall_mm=worst)
        elif name == 'spec_revalidation':
            out[name] = dict(revalidated=1.0 if c['passed'] else 0.0)
        elif name in ('wheel_clearance_front', 'wheel_clearance_rear'):
            distances, volumes = _sides(c, 'measured_distance_mm'), _sides(c, 'intersection_volume_mm3')
            out[name] = dict(min_distance_mm=min(_num(v) for v in distances.values()),
                             max_intersection_volume_mm3=max(_num(v) for v in volumes.values()))
        else:
            out[name] = dict(zmin_mm=_num(_get(c, 'zmin_mm')))
    return out


def read_bounded(path, limit: int) -> bytes:
    """Read a regular file (never a symlink) of at most ``limit`` bytes."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise DragsterUnavailable('Dragster output is not a regular file')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise DragsterUnavailable('Dragster output is not a regular file')
            data = source.read(limit + 1)
    except OSError:
        raise DragsterUnavailable('Dragster output unreadable') from None
    if len(data) > limit:
        raise DragsterUnavailable('Dragster output exceeds the run budget')
    return data


def revalidate(specification: dict):
    """The reviewed specification, validated again at job time. Unsupported means refused."""
    if DRAGSTER.refuse(specification):
        raise UnsupportedCapability('Dragster specification is not supported')
    try:
        spec = DRAGSTER.parse_spec(specification)
    except (ValidationError, ValueError, TypeError):
        raise UnsupportedCapability('Dragster specification is not valid') from None
    if spec.schema_version != 2 or spec.style not in REGISTRY or spec.stock is not None:
        raise UnsupportedCapability('Dragster specification is not supported')
    return spec


def _bind_to_spec(report: dict, spec: dict):
    """Numbers the report echoes must be the reviewed spec's, not values the report chose."""
    checks = report['checks']
    try:
        same = (report.get('style') == spec['style']
                and all(checks[c]['min_wall_mm'] == spec['min_wall_mm'] for c in WALL_CHECKS)
                and all(checks[c]['side_clearance_mm'] == spec['wheels']['side_clearance_mm']
                        for c in ('wheel_clearance_front', 'wheel_clearance_rear'))
                and checks['body_above_ground']['ground_clearance_mm'] == spec['ground_clearance_mm'])
    except (KeyError, TypeError):
        same = False
    if not same:
        raise DragsterUnavailable('Report differs from the reviewed specification')


def build_evaluation(candidate: GeneratorCandidate, report: dict, *, report_bytes: bytes, spec_bytes: bytes,
                     step_bytes: bytes | None = None, glb_bytes: bytes | None = None) -> dict:
    """The evaluation dict ``assess_generator`` documents. Step and preview bytes: accepted runs only."""
    from .policy import report_digest
    candidate = GeneratorCandidate.model_validate(candidate.model_dump())
    if candidate.content.descriptor.id != DRAGSTER.id:
        raise DragsterUnavailable('Candidate belongs to another capability')
    try:
        captured = json.loads(report_bytes)
    except ValueError:
        raise DragsterUnavailable('Report bytes are not JSON') from None
    if not isinstance(report, dict) or canonical_json(captured) != canonical_json(report):
        raise DragsterUnavailable('Report bytes differ from the service report')
    accepted = step_bytes is not None
    if accepted != (glb_bytes is not None):
        raise DragsterUnavailable('Accepted runs deliver both the STEP and the preview')
    if (report.get('schema_version') != 2 or report.get('engineering_certified') is not False
            or report.get('spec_sha256') != _sha(spec_bytes) or report.get('spec_sha256') != candidate.candidate_sha256):
        raise DragsterUnavailable('Report is not the reviewed specification version 2 evidence')
    _bind_to_spec(report, candidate.content.specification)
    preview = report.get('preview')
    if accepted:
        if (not isinstance(preview, dict) or report.get('step_sha256') != _sha(step_bytes)
                or preview.get('source_step_sha256') != _sha(step_bytes)
                or preview.get('glb_sha256') != _sha(glb_bytes)):
            raise DragsterUnavailable('STEP or preview digest mismatch')
    parsed = parse_report(report)
    # Only delivered bytes get an identity: a rejected package carries no geometry.
    identities = (ArtifactIdentity(path=STEP, sha256=_sha(step_bytes), bytes=len(step_bytes)),) if accepted else ()
    limits = DRAGSTER.expected_limits(candidate.content.specification)
    checks = []
    for check_id in DRAGSTER.base_checks:
        ok = report['checks'][check_id]['passed'] is True
        checks.append(GeneratorCheckResult(
            capability_id=DRAGSTER.id, check_id=check_id, state=CheckState.PASSED if ok else CheckState.FAILED,
            method=HARDWARE_METHOD if check_id in HARDWARE_CHECKS else METHOD, method_version=METHOD_VERSION,
            brief_sha256=candidate.brief_sha256, candidate_sha256=candidate.candidate_sha256,
            measurements=tuple(Measurement(name=n, value=v, unit=UNITS[n]) for n, v in parsed[check_id].items()),
            limits=limits[check_id], artifacts=identities, reason=None if ok else f'Dragster {check_id} did not pass'))
    failed = [c for c in checks if c.state == CheckState.FAILED]
    if accepted and (failed or report.get('passed') is not True):
        raise DragsterUnavailable('Accepted run report fails its own checks')
    if not accepted and (not failed or report.get('passed') is not False):
        raise DragsterUnavailable('Rejected run report passes every check')
    if accepted:
        artifacts = {SPEC: spec_bytes, REPORT: report_bytes, STEP: step_bytes, GLB: glb_bytes}
        stated = {SPEC: report['spec_sha256'], REPORT: _sha(report_bytes), STEP: report['step_sha256'],
                  GLB: preview['glb_sha256']}
        diagnostics = {}
    else:
        artifacts = {}
        diagnostics = {SPEC: spec_bytes, REPORT: report_bytes}
        stated = {SPEC: _sha(spec_bytes), REPORT: _sha(report_bytes)}
    return dict(candidate=candidate, checks=tuple(checks), outcome='rejected' if failed else 'accepted',
                passed=not failed, spec_sha256=report['spec_sha256'], report=report,
                report_sha256=report_digest(report), report_bytes=report_bytes, artifacts=artifacts,
                diagnostics=diagnostics, stated_digests=stated, extra_digests=())


def failure_reason(message: str) -> str:
    """Fixed text; names failing checks only when the worker's message matches and each is required."""
    found = _FAILED.search(message or '')
    if found:
        ids = found.group(1).split(', ')
        if all(i in DRAGSTER.base_checks for i in ids):
            return 'Dragster verifier failed checks: ' + ', '.join(ids)
    return 'Dragster generation failed'


class DragsterAdapter:
    """``runner`` is a getter for the shared ``ProcessRunner`` (late-bound: lifespan owns it)."""
    descriptor = DRAGSTER

    def __init__(self, runner=None):
        self._runner = runner

    def candidates(self, brief: EngineeringBrief) -> tuple[GeneratorCandidate, ...]:
        brief = EngineeringBrief.model_validate(brief.model_dump())
        content = brief.content
        if not isinstance(content, GeneratorBriefContent) or content.descriptor.id != DRAGSTER.id:
            raise UnsupportedCapability('Brief does not belong to the CO2 dragster capability')
        spec = revalidate(content.specification)
        return (GeneratorCandidate.from_brief(brief, specification_digest_v2(spec)),)

    def measurements(self, report_bytes: bytes) -> dict:
        return parse_report(json.loads(report_bytes))

    async def evaluate(self, candidate: GeneratorCandidate) -> dict:
        if self._runner is None:
            raise DragsterUnavailable('Dragster runner unavailable')
        candidate = GeneratorCandidate.model_validate(candidate.model_dump())
        spec = revalidate(candidate.content.specification)
        if specification_digest_v2(spec) != candidate.candidate_sha256:
            raise DragsterUnavailable('Candidate digest mismatch')
        service = importlib.import_module('dragsters.service')     # never at import time
        artifacts = importlib.import_module('artifacts')
        runner = self._runner()
        try:
            result = await service.generate(spec, runner=runner)
        except asyncio.CancelledError:
            raise
        except service.GenerationError as error:
            raise DragsterUnavailable(failure_reason(str(error))) from None
        except Exception:
            raise DragsterUnavailable('Dragster generation unavailable') from None
        run_id = result.get('run_id') if isinstance(result, dict) else None
        if not isinstance(run_id, str) or not run_id:
            raise DragsterUnavailable('Dragster run has no identity')
        limit = DRAGSTER.resources.run_bytes

        def read():
            # Capture and discard in one thread step: a cancelled caller never leaves a legacy run behind.
            try:
                files, remaining = {}, limit
                for fmt in FORMATS:
                    path = artifacts.artifact_path(run_id, fmt)
                    if path is None:
                        raise DragsterUnavailable('Dragster output missing')
                    files[fmt] = read_bounded(path, remaining)
                    remaining -= len(files[fmt])
                return files
            finally:
                artifacts.discard(run_id)
        files = await asyncio.to_thread(read)
        digests = result.get('digests')
        report = result.get('report')
        if (not isinstance(digests, dict) or any(digests.get(f) != _sha(files[f]) for f in FORMATS)
                or not isinstance(report, dict)):
            raise DragsterUnavailable('Dragster output differs from the service result')
        spec_bytes = canonical_spec_v2(parse_v2(files['spec'].decode('utf-8'))).encode()
        if files['spec'] != spec_bytes or specification_digest_v2(parse_v2(files['spec'].decode('utf-8'))) \
                != candidate.candidate_sha256:
            raise DragsterUnavailable('Dragster output differs from the reviewed specification')
        return build_evaluation(candidate, report, report_bytes=files['report'], spec_bytes=files['spec'],
                                step_bytes=files['step'], glb_bytes=files['glb'])
