"""Regen chamber adapter for the checked-design registry (U6.1).

``evaluate()`` drives the existing ``regen_web.JobRunner`` (the same runner and single
slot as the old ``/engineering/regen`` endpoint), then maps the verifier report that
``regen_cli`` wrote into the shared evidence shape. Builder, verifier and tolerances are
untouched. ``regen_web`` needs fastapi, so it is imported inside ``evaluate`` only; the
mapping code is pure and runs anywhere.

Mapping rules:

- one check per ``regen_mesh.verify`` check, plus ``evidence_binding`` (the acceptance test
  ``JobRunner._run`` already applies);
- measurements are the report's numbers; limits come from the closed table in
  ``capabilities`` (pinned constants and the reviewed spec), never from the report;
- ``unresolved`` counts a failed check whose own numbers do not explain the failure (a
  ``not_evaluated`` or budget-exhausted check, an unmeasurable station), so the failure
  stays a failure the policy can recompute;
- anything missing or inconsistent raises ``RegenUnavailable``: never an accepted run.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat

from pydantic import ValidationError

from cem.core import canonical_json
from .capabilities import REGEN, UnsupportedCapability
from .contracts import (ArtifactIdentity, CheckState, EngineeringBrief, GeneratorBriefContent,
                        GeneratorCandidate, GeneratorCheckResult, Measurement)
from .regen_spec import RegenChamberSpec, canonical_spec, specification_digest

POLL_SECONDS = 0.25
METHOD = 'regen_mesh.verify'
METHOD_VERSION = REGEN.numerical_version
STL, REPORT, SPEC = 'generator-cleaned.stl', 'generator-report.json', 'generator-spec.canonical.json'
VERIFY_CHECKS = ('components', 'edge_manifold', 'winding', 'bbox', 'volume', 'channel_count', 'hot_wall',
                 'cold_wall', 'rib', 'circuit_fidelity', 'circuit_coverage', 'wall_thickness')
WALL_CHECKS = ('hot_wall', 'cold_wall', 'rib')

UNITS = {
    'unexpected_components': 'count', 'edges_not_used_twice': 'count', 'directed_edge_conflicts': 'count',
    'non_positive_volume': 'count', 'bbox_max_error': 'mm', 'volume_relative_error': 'ratio',
    'channel_count': 'count', 'channel_count_max': 'count', 'floor_applied': 'mm', 'minimum_mm': 'mm',
    'stations_below_floor': 'count', 'unresolved': 'count', 'nonconforming_patches': 'count',
    'unsplit_over_radius_target': 'count', 'failed_samples': 'count', 'void_components': 'count',
    'violations': 'count', 'marginal': 'count', 'too_deep': 'count', 'exhausted': 'count',
    'passed': 'count', 'geometry_source_picogk_builder': 'count', 'not_certified': 'count',
}


class RegenUnavailable(RuntimeError):
    """Required evidence cannot be obtained; nothing is accepted."""


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _num(value) -> float:
    if isinstance(value, bool):
        value = int(value)
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RegenUnavailable('Report holds a non-numeric measurement')
    return float(value)


def _mapping(value) -> dict:
    if not isinstance(value, dict):
        raise RegenUnavailable('Report check is malformed')
    return value


def _state(check: dict) -> str:
    if check.get('state') not in ('pass', 'fail', 'not_evaluated'):
        raise RegenUnavailable('Report check state is unknown')
    return check['state']


def _needs(check: dict, *keys):
    if any(k not in check for k in keys):
        raise RegenUnavailable('Report check lacks its measurements')


def _whole_part(check, state, numeric):
    """Numbers when the check produced them; otherwise a failed, unresolved check."""
    try:
        return {**numeric(check), 'unresolved': 0.0}
    except (KeyError, TypeError, AttributeError):
        if state == 'pass':
            raise RegenUnavailable('Passed report check lacks its measurements') from None
        return {**numeric(None), 'unresolved': 1.0}


def _fidelity(c):
    if c is None:
        return dict(nonconforming_patches=0.0, unsplit_over_radius_target=0.0)
    return dict(nonconforming_patches=_num(c['nonconforming_patches']),
                unsplit_over_radius_target=_num(c['unsplit_over_radius_target']))


def _coverage(c):
    if c is None:
        return dict(failed_samples=0.0, void_components=1.0)
    return dict(failed_samples=_num(c['failed_samples']), void_components=_num(c['void_components']))


def _thickness(c):
    if c is None:
        return dict(violations=0.0, marginal=0.0, too_deep=0.0, exhausted=0.0)
    classes = c['classes']
    if not isinstance(classes, dict) or not classes:
        raise KeyError('classes')
    total = lambda key: sum(_num(k[key]) for k in classes.values())
    return dict(violations=total('violation_count'), marginal=total('marginal_count'),
                too_deep=total('too_deep'), exhausted=total('exhausted'))


def _wall(check, state):
    _needs(check, 'per_station', 'threshold_mm')
    stations, floor = check['per_station'], _num(check['threshold_mm'])
    if not isinstance(stations, list):
        raise RegenUnavailable('Report check is malformed')
    below = sum(1 for v in stations if _num(v) < floor)
    out = dict(floor_applied=floor, stations_below_floor=float(below),
               unresolved=1.0 if state != 'pass' and below == 0 else 0.0)
    if check.get('minimum') is not None:
        out['minimum_mm'] = _num(check['minimum'])
    return out


def parse_report(report: dict) -> dict:
    """``{check_id: {measurement name: value}}`` from a regen report (raises if incomplete)."""
    if not isinstance(report, dict):
        raise RegenUnavailable('Report is malformed')
    checks = _mapping(report.get('checks'))
    if set(checks) != set(VERIFY_CHECKS):
        raise RegenUnavailable('Report checks differ from the verifier set')
    out = {}
    for name in VERIFY_CHECKS:
        c = _mapping(checks[name])
        state = _state(c)
        if state == 'not_evaluated' and name not in ('circuit_fidelity', 'circuit_coverage', 'wall_thickness'):
            raise RegenUnavailable('Report check was not evaluated')
        if name == 'components':
            _needs(c, 'unexpected_components')
            out[name] = dict(unexpected_components=float(len(c['unexpected_components'])))
        elif name == 'edge_manifold':
            _needs(c, 'edges_not_used_twice')
            out[name] = dict(edges_not_used_twice=_num(c['edges_not_used_twice']))
        elif name == 'winding':
            _needs(c, 'directed_edge_conflicts', 'signed_volume_mm3')
            out[name] = dict(directed_edge_conflicts=_num(c['directed_edge_conflicts']),
                             non_positive_volume=0.0 if _num(c['signed_volume_mm3']) > 0 else 1.0)
        elif name == 'bbox':
            _needs(c, 'max_error_mm')
            out[name] = dict(bbox_max_error=_num(c['max_error_mm']))
        elif name == 'volume':
            _needs(c, 'relative_error')
            out[name] = dict(volume_relative_error=abs(_num(c['relative_error'])))
        elif name == 'channel_count':
            _needs(c, 'per_station')
            loops = c['per_station']
            if not isinstance(loops, list) or not loops:
                raise RegenUnavailable('Report check lacks its measurements')
            out[name] = dict(channel_count=min(_num(v) for v in loops), channel_count_max=max(_num(v) for v in loops))
        elif name in WALL_CHECKS:
            out[name] = _wall(c, state)
        elif name == 'circuit_fidelity':
            out[name] = _whole_part(c, state, _fidelity)
        elif name == 'circuit_coverage':
            out[name] = _whole_part(c, state, _coverage)
        else:
            out[name] = _whole_part(c, state, _thickness)
    out['evidence_binding'] = dict(
        passed=1.0 if report.get('passed') is True else 0.0,
        geometry_source_picogk_builder=1.0 if report.get('geometry_source') == 'picogk_builder' else 0.0,
        not_certified=1.0 if report.get('engineering_certified') is False else 0.0)
    return out


def read_bounded(path, limit: int) -> bytes:
    """Read a regular file (never a symlink) of at most ``limit`` bytes."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise RegenUnavailable('Regen output is not a regular file')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise RegenUnavailable('Regen output is not a regular file')
            data = source.read(limit + 1)
    except OSError:
        raise RegenUnavailable('Regen output unreadable') from None
    if len(data) > limit:
        raise RegenUnavailable('Regen output exceeds the run budget')
    return data


def rejected_report_bytes(report: dict) -> bytes:
    """Exactly what ``regen_cli`` writes for ``rejected_report.json``."""
    return (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def revalidate(specification: dict) -> RegenChamberSpec:
    """The reviewed specification, validated again at job time. Unsupported means refused."""
    if REGEN.refuse(specification):
        raise UnsupportedCapability('Regen specification is not supported')
    try:
        return REGEN.parse_spec(specification)
    except (ValidationError, ValueError, TypeError):
        raise UnsupportedCapability('Regen specification is not valid') from None


def build_evaluation(candidate: GeneratorCandidate, report: dict, *, report_bytes: bytes, spec_bytes: bytes,
                     stl_bytes: bytes | None = None) -> dict:
    """The evaluation dict ``assess_generator`` documents. ``stl_bytes`` is given for accepted jobs only."""
    from .policy import report_digest
    candidate = GeneratorCandidate.model_validate(candidate.model_dump())
    if candidate.content.descriptor.id != REGEN.id:
        raise RegenUnavailable('Candidate belongs to another capability')
    try:
        captured = json.loads(report_bytes)
    except ValueError:
        raise RegenUnavailable('Report bytes are not JSON') from None
    if not isinstance(report, dict) or canonical_json(captured) != canonical_json(report):
        raise RegenUnavailable('Report bytes differ from the job report')
    accepted = stl_bytes is not None
    if report.get('spec_sha256') != _sha(spec_bytes) or report.get('spec_sha256') != candidate.candidate_sha256:
        raise RegenUnavailable('Specification digest mismatch')
    if accepted and report.get('stl_sha256') != _sha(stl_bytes):
        raise RegenUnavailable('Mesh digest mismatch')
    parsed = parse_report(report)
    # Only delivered bytes get an identity: a rejected package carries no geometry.
    identities = (ArtifactIdentity(path=STL, sha256=_sha(stl_bytes), bytes=len(stl_bytes)),) if accepted else ()
    limits = REGEN.expected_limits(candidate.content.specification)
    checks = []
    for check_id in (*VERIFY_CHECKS, 'evidence_binding'):
        values = parsed[check_id]
        if check_id == 'evidence_binding':
            ok = all(v == 1.0 for v in values.values())
        else:
            ok = report['checks'][check_id]['state'] == 'pass'
        if ok:
            reason = None
        elif values.get('unresolved') == 1.0:
            reason = f'Regen {check_id} was not evaluated or could not be resolved'
        else:
            reason = f'Regen {check_id} did not pass'
        checks.append(GeneratorCheckResult(
            capability_id=REGEN.id, check_id=check_id, state=CheckState.PASSED if ok else CheckState.FAILED,
            method=METHOD, method_version=METHOD_VERSION, brief_sha256=candidate.brief_sha256,
            candidate_sha256=candidate.candidate_sha256,
            measurements=tuple(Measurement(name=n, value=v, unit=UNITS[n]) for n, v in values.items()),
            limits=limits[check_id], artifacts=identities, reason=reason))
    failed = [c for c in checks if c.state == CheckState.FAILED]
    if accepted and (failed or report.get('passed') is not True):
        raise RegenUnavailable('Accepted job report fails its own checks')
    if not accepted and not failed:
        raise RegenUnavailable('Rejected job report passes every check')
    if accepted:
        artifacts = {STL: stl_bytes, REPORT: report_bytes, SPEC: spec_bytes}
        stated = {STL: report['stl_sha256'], REPORT: _sha(report_bytes), SPEC: report['spec_sha256']}
        diagnostics = {}
    else:
        artifacts = {}
        diagnostics = {REPORT: report_bytes, SPEC: spec_bytes}
        stated = {REPORT: _sha(report_bytes), SPEC: _sha(spec_bytes)}
    return dict(candidate=candidate, checks=tuple(checks), outcome='rejected' if failed else 'accepted',
                passed=not failed, spec_sha256=report['spec_sha256'], report=report,
                report_sha256=report_digest(report), report_bytes=report_bytes, artifacts=artifacts,
                diagnostics=diagnostics, stated_digests=stated, extra_digests=())


class RegenAdapter:
    """``runner`` is a getter for the shared ``JobRunner`` (late-bound: lifespan owns it)."""
    descriptor = REGEN

    def __init__(self, runner=None):
        self._runner = runner

    def candidates(self, brief: EngineeringBrief) -> tuple[GeneratorCandidate, ...]:
        brief = EngineeringBrief.model_validate(brief.model_dump())
        content = brief.content
        if not isinstance(content, GeneratorBriefContent) or content.descriptor.id != REGEN.id:
            raise UnsupportedCapability('Brief does not belong to the regen chamber capability')
        spec = revalidate(content.specification)
        return (GeneratorCandidate.from_brief(brief, specification_digest(spec)),)

    def measurements(self, report_bytes: bytes) -> dict:
        return parse_report(json.loads(report_bytes))

    @staticmethod
    async def _discard(runner, submit, job_id):
        try:
            if job_id is None:
                job_id = (await asyncio.shield(submit))['id']
            await asyncio.to_thread(runner.cancel, job_id)
        except Exception:
            pass

    async def evaluate(self, candidate: GeneratorCandidate) -> dict:
        if self._runner is None:
            raise RegenUnavailable('Regen runner unavailable')
        from .regen_web import FILES, BrowserSpec, _ready      # needs fastapi: never at import time
        candidate = GeneratorCandidate.model_validate(candidate.model_dump())
        spec = revalidate(candidate.content.specification)
        if specification_digest(spec) != candidate.candidate_sha256:
            raise RegenUnavailable('Candidate digest mismatch')
        try:
            browser = BrowserSpec.model_validate(spec.model_dump())
        except ValidationError:
            raise UnsupportedCapability('Regen specification is not supported') from None
        if not _ready():
            raise RegenUnavailable('PicoGK runtime unavailable')
        runner = self._runner()
        submit = asyncio.ensure_future(asyncio.to_thread(runner.submit, browser))   # a busy slot (409) is unavailable
        job_id = None
        try:
            job = await asyncio.shield(submit)
            job_id = job['id']
            while job['status'] in ('queued', 'running'):
                await asyncio.sleep(POLL_SECONDS)
                job = await asyncio.to_thread(runner.get, job_id)
        except asyncio.CancelledError:
            # Discard the result and free the shared slot: kill this job's worker, then re-raise.
            cleanup = asyncio.ensure_future(self._discard(runner, submit, job_id))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            raise
        except Exception:
            raise RegenUnavailable('Regen runner could not complete the job') from None
        try:
            return await self._collect(runner, job, spec, candidate)
        finally:
            # The evaluation holds every byte it needs; free the runner's tmpfs copy now.
            await asyncio.to_thread(runner.release, job['id'])

    async def _collect(self, runner, job, spec, candidate) -> dict:
        from .regen_web import FILES
        spec_bytes = canonical_spec(spec).encode()
        if job['status'] == 'rejected' and isinstance(job.get('report'), dict):
            return build_evaluation(candidate, job['report'], report_bytes=rejected_report_bytes(job['report']),
                                    spec_bytes=spec_bytes)
        if job['status'] != 'accepted':
            raise RegenUnavailable('Regen job did not finish')
        folder = runner.root / job['id'] / 'output'
        limit = REGEN.resources.run_bytes

        def read():
            files, remaining = {}, limit
            for name in ('stl', 'report', 'spec'):
                files[name] = read_bounded(folder / FILES[name], remaining)
                remaining -= len(files[name])
            return files
        files = await asyncio.to_thread(read)
        report = json.loads(files['report'])
        if report != job.get('report') or files['spec'] != spec_bytes:
            raise RegenUnavailable('Regen output differs from the reviewed specification')
        return build_evaluation(candidate, report, report_bytes=files['report'], spec_bytes=files['spec'],
                                stl_bytes=files['stl'])
