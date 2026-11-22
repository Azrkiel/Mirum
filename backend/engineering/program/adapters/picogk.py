"""PicoGK kernel adapter (F03): a thin wrapper over the existing regen_chamber 1.0.0 path.

It never decides engineering acceptance and never re-implements the verifier: the verdict is copied
from the evaluation dict returned by engineering.regen_adapter.RegenAdapter.evaluate (policy-side
checks already ran inside it). PicoGK declares mesh/voxel (STL) only, never STEP, and its claim scope is
always the existing REGEN.claim ('geometry_checks_only'): geometry evidence never implies physics
acceptance. Module-level imports stay native-free; engineering.regen_web (which needs fastapi) is only
imported inside probe(), and numpy/scipy readers only inside inspect().
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import math
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from engineering import regen_adapter
from engineering.capabilities import REGEN, UnsupportedCapability
from engineering.regen_adapter import RegenAdapter, RegenUnavailable

from ..errors import SourceSpan
from ..kernel import (
    Artifact, ArtifactRef, BackendDescription, Built, BuildPlan, BuildResult, ExecutionContext,
    ExportResult, InspectResult, KernelFormat, Measurement, NativeVersion, ProbeResult,
    SupportDecision, ToleranceConvention, check_plan, make_description,
)
from ..registry import ArtifactGraph, ArtifactNode

__all__ = ['PicogkAdapter', 'ADAPTER_VERSION', 'compare_reread']

ADAPTER_VERSION = 'picogk-0.1.0'

_HEX64 = re.compile(r'^[0-9a-f]{64}$')
_HEX64_TOKEN = re.compile(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])')
# backend/Dockerfile line 38 copies the manifest to /opt/regen/picogk-runtime.sha256, one level above
# the builder's directory (/opt/regen/app/builder). The Dockerfile reads only its first field.
_MANIFEST_NAME = 'picogk-runtime.sha256'
_MANIFEST_BUDGET_BYTES = 4096
# K37: STL reread deadline, strictly tighter than REGEN.resources.overall_seconds (420). Evidence: the
# real reread of the small cleaned STL (68,073,484 bytes) took 1.804 s in picogk-runtime (F05 PR #138,
# run 45002982642); 20 s keeps a > 10x margin. backend/tests/test_regen_native_k37.py re-measures it
# natively and requires >= 5x. The reread runs on a dedicated one-thread executor behind a non-waiting slot.
INSPECT_DEADLINE_S = 20.0
# Legacy alias (older tests patch this name); the tighter of the two values applies.
_INSPECT_DEADLINE_S = INSPECT_DEADLINE_S
_REREAD_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='picogk-reread')
# Held from before submit until the worker thread finishes (not when the caller times out).
_REREAD_SLOT = threading.Lock()

_NODE_ID = 'regen.chamber'
# regen_adapter.UNITS value -> kernel Measurement unit literal. 'ratio' has no mapping and is skipped.
_UNIT_MAP = {'mm': 'mm', 'mm2': 'mm2', 'mm3': 'mm3', 'mm4': 'mm4', '1': '1', 'count': 'count'}
_MEASURED_CHECKS = ('bbox', 'volume')
_SEMANTIC_BAD = re.compile(r'[^a-z0-9_.]')


def _pinned() -> dict[str, float]:
    """The pinned regen numerical constants (engineering.capabilities.REGEN.numerical)."""
    return {r.name: r.value for r in REGEN.numerical}


def _tolerances() -> tuple[ToleranceConvention, ...]:
    # Only VOLUME_TOLERANCE has a unit this record can carry. BBOX_TOLERANCE_VOXELS (2.0) is in voxels,
    # and its millimetre value depends on the spec's voxel size, so it is deliberately not declared.
    return (
        ToleranceConvention(
            name='volume_relative', value=float(_pinned()['VOLUME_TOLERANCE']), unit='relative',
            source='engineering.regen_mesh.VOLUME_TOLERANCE via engineering.capabilities.REGEN.numerical'),
    )


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _semantic(*parts: str) -> str:
    return '.'.join(_SEMANTIC_BAD.sub('_', p.lower()) for p in parts)


def _report_measurements(report: Any) -> tuple[Measurement, ...]:
    """bbox and volume check numbers from the existing parse_report; unmapped units are skipped."""
    parsed = regen_adapter.parse_report(report)
    out = []
    for check in _MEASURED_CHECKS:
        for name, value in parsed[check].items():
            unit = _UNIT_MAP.get(regen_adapter.UNITS.get(name, ''))
            if unit is None or not _finite(value):
                continue
            out.append(Measurement(name=_semantic('regen', check, name), value=float(value),
                                   unit=unit, source='report'))
    return tuple(out)


def _vec(value: Any):
    if isinstance(value, (list, tuple)) and len(value) == 3 and all(_finite(v) for v in value):
        return tuple(float(v) for v in value)
    return None


def compare_reread(report: Mapping[str, Any], mesh: Mapping[str, Any], artifact_sha256: str,
                   *, bbox_tolerance_mm: float | None = None) -> InspectResult:
    """Pure comparison of a reread mesh against the regen report (no native call, no file access).

    ``mesh`` is {'bbox_min': 3 floats, 'bbox_max': 3 floats, 'volume_mm3': float} measured from the
    reread STL. Tolerances: bbox is ``bbox_tolerance_mm`` (the caller derives it as
    REGEN.numerical BBOX_TOLERANCE_VOXELS * spec.voxel_size_mm, the rule regen_mesh.check_bbox uses;
    a limit is never taken from the report); volume is relative, REGEN.numerical VOLUME_TOLERANCE
    (regen_mesh.VOLUME_TOLERANCE), measured against the report's measured volume.
    """
    def result(outcome, reasons=(), measurements=(), deviations=()):
        return InspectResult(backend='picogk', mode='native', outcome=outcome, reasons=reasons,
                             artifact_sha256=artifact_sha256, measurements=measurements,
                             deviations=deviations)

    if not isinstance(report, Mapping) or not isinstance(mesh, Mapping):
        return result('unavailable', ('evidence_invalid',))
    if report.get('stl_sha256') != artifact_sha256:
        return result('precision_failure', ('reread_mismatch',))
    checks = report.get('checks')
    bbox = checks.get('bbox') if isinstance(checks, Mapping) else None
    volume = checks.get('volume') if isinstance(checks, Mapping) else None
    if not isinstance(bbox, Mapping) or not isinstance(volume, Mapping):
        return result('unavailable', ('evidence_invalid',))
    rep_min, rep_max = _vec(bbox.get('measured_min')), _vec(bbox.get('measured_max'))
    got_min, got_max = _vec(mesh.get('bbox_min')), _vec(mesh.get('bbox_max'))
    rep_vol, got_vol = volume.get('measured_mm3'), mesh.get('volume_mm3')
    if (None in (rep_min, rep_max, got_min, got_max) or not _finite(rep_vol) or not _finite(got_vol)
            or rep_vol <= 0 or not _finite(bbox_tolerance_mm) or bbox_tolerance_mm < 0):
        return result('unavailable', ('evidence_invalid',))
    axes = 'xyz'
    measured, deviations = [], []
    bbox_dev = 0.0
    for label, got, rep in (('bbox_min', got_min, rep_min), ('bbox_max', got_max, rep_max)):
        for axis, g, r in zip(axes, got, rep):
            name = _semantic('regen', 'reread', f'{label}_{axis}')
            measured.append(Measurement(name=name, value=g, unit='mm', source='reread'))
            deviations.append(Measurement(name=name, value=abs(g - r), unit='mm', source='reread'))
            bbox_dev = max(bbox_dev, abs(g - r))
    vname = _semantic('regen', 'reread', 'volume_mm3')
    measured.append(Measurement(name=vname, value=float(got_vol), unit='mm3', source='reread'))
    deviations.append(Measurement(name=vname, value=abs(float(got_vol) - float(rep_vol)), unit='mm3',
                                  source='reread'))
    volume_rel = abs(float(got_vol) - float(rep_vol)) / float(rep_vol)
    ok = bbox_dev <= bbox_tolerance_mm and volume_rel <= _pinned()['VOLUME_TOLERANCE']
    if not ok:
        return result('precision_failure', ('measurement_out_of_tolerance',), tuple(measured),
                      tuple(deviations))
    return result('consistent', (), tuple(measured), tuple(deviations))


def _reread_mesh(data: bytes) -> dict:
    """Parse and clean a binary STL with the existing regen_mesh readers; raises ValueError/OSError."""
    import numpy as np
    from engineering import regen_mesh
    with tempfile.TemporaryDirectory(prefix='kernel-picogk-') as temporary:
        path = Path(temporary) / 'reread.stl'
        path.write_bytes(data)
        tris = regen_mesh.read_stl(path)
    if not len(tris):
        raise ValueError('empty mesh')
    r = regen_mesh.clean_and_check(tris)
    if not len(r.faces):
        raise ValueError('no faces after cleaning')
    pts = r.verts[np.unique(r.faces)]
    return {'bbox_min': [float(x) for x in pts.min(axis=0)], 'bbox_max': [float(x) for x in pts.max(axis=0)],
            'volume_mm3': float(r.volume)}


class PicogkAdapter:
    backend = 'picogk'
    mode = 'native'

    def __init__(self, *, runner=None):
        # The shared regen_web.JobRunner getter: the same late-bound callable RegenAdapter takes.
        self._runner = runner

    def describe(self) -> BackendDescription:
        return make_description('picogk', mode='native', adapter_version=ADAPTER_VERSION,
                                tolerances=_tolerances())

    # ------------------------------------------------------------------ probe

    @staticmethod
    def _identities() -> tuple[dict, str | None]:
        provenance = regen_adapter.regen_provenance()
        manifest = None
        builder_path = provenance.get('builder_path')
        if isinstance(builder_path, str):
            try:
                text = regen_adapter.read_bounded(
                    Path(builder_path).parent.parent / _MANIFEST_NAME, _MANIFEST_BUDGET_BYTES)
                found = _HEX64_TOKEN.search(text.decode('ascii', 'ignore').lower())
                manifest = found.group(0) if found else None
            except RegenUnavailable:
                manifest = None
        return provenance, manifest

    async def probe(self) -> ProbeResult:
        """Readiness is the existing regen_web._ready() executable/runtime check plus file identities.

        No PicoGK geometry is built here; a real build happens only in build(). The builder and
        runtime hashes are taken with the existing regen_adapter.regen_provenance.
        """
        def no(reason: str) -> ProbeResult:
            return ProbeResult(backend='picogk', mode='native', available=False, reasons=(reason,))

        if self._runner is None:
            return no('runtime_missing')
        try:
            from engineering.regen_web import _ready    # needs fastapi: never at import time
        except ImportError:
            return no('runtime_missing')
        if not _ready():
            return no('runtime_missing')
        try:
            provenance, manifest = await asyncio.to_thread(self._identities)
        except OSError:
            return no('probe_failed')
        runtime, builder = provenance.get('runtime_so_sha256'), provenance.get('builder_sha256')
        if not (isinstance(runtime, str) and runtime and isinstance(builder, str) and builder):
            return no('probe_failed')
        versions = [NativeVersion(name='picogk_runtime_so_sha256', version=runtime),
                    NativeVersion(name='regen_builder_sha256', version=builder)]
        if manifest:
            versions.append(NativeVersion(name='picogk_runtime_manifest_sha256', version=manifest))
        image_id = provenance.get('image_id')
        if image_id:
            versions.append(NativeVersion(name='image_id', version=image_id))
        try:
            return ProbeResult(backend='picogk', mode='native', available=True,
                               native_versions=tuple(versions))
        except ValidationError:
            return no('probe_failed')

    # ------------------------------------------------------------------ support / build

    def supports(self, plan: BuildPlan) -> SupportDecision:
        return check_plan(self.describe(), plan)

    async def build(self, plan: BuildPlan, context: ExecutionContext) -> Built:
        common = dict(backend='picogk', adapter_version=ADAPTER_VERSION, mode='native',
                      capability_id=plan.capability_id, capability_version=plan.capability_version)

        def refuse(outcome: str, reasons: tuple, code: str | None = None) -> Built:
            return Built(BuildResult(outcome=outcome, reasons=reasons, diagnostic_code=code, **common))

        decision = self.supports(plan)
        if not decision.supported:
            return refuse('unsupported', decision.reasons, decision.diagnostic_code)
        probe = await self.probe()
        if not probe.available:
            return refuse('unavailable', probe.reasons)

        try:
            candidate = type(plan.candidate).model_validate(plan.candidate.model_dump())
        except ValueError:
            return refuse('unsupported', ('candidate_invalid',))
        try:
            evaluation = await asyncio.wait_for(RegenAdapter(runner=self._runner).evaluate(candidate),
                                                context.deadline_s)
        except (asyncio.TimeoutError, TimeoutError):
            return refuse('unavailable', ('deadline_exceeded',))
        except RegenUnavailable:
            return refuse('unavailable', ('native_failed',))
        except UnsupportedCapability:
            return refuse('unsupported', ('candidate_invalid',))

        domain = evaluation.get('outcome')
        domain = getattr(domain, 'value', domain)
        if domain not in ('accepted', 'rejected') or REGEN.claim != 'geometry_checks_only':
            return refuse('unavailable', ('evidence_invalid',))
        report = evaluation.get('report')
        report_sha = evaluation.get('report_sha256')
        try:
            measurements = _report_measurements(report)
        except (RegenUnavailable, ValidationError):
            return refuse('unavailable', ('evidence_invalid',))
        refs: list[ArtifactRef] = []
        carried: list[Artifact] = []
        if domain == 'accepted':
            produced = evaluation.get('artifacts')
            blob = produced.get(regen_adapter.STL) if isinstance(produced, Mapping) else None
            if not isinstance(blob, (bytes, bytearray)):
                return refuse('unavailable', ('evidence_invalid',))
            blob = bytes(blob)
            if len(blob) > context.max_artifact_bytes:
                return refuse('unavailable', ('budget_exceeded',))
            ref = ArtifactRef(name=regen_adapter.STL, format='stl', representation='mesh',
                              sha256=hashlib.sha256(blob).hexdigest(), size=len(blob), node_id=_NODE_ID)
            refs.append(ref)
            carried.append(Artifact(ref, blob))
        build_span: SourceSpan | None = plan.op_stream.entries[0].span
        graph = ArtifactGraph(
            schema_version='mirum_artifact_graph_v0_1', op_stream=plan.op_stream,
            nodes=tuple(ArtifactNode(id=r.node_id, kind='solid', op_seq=0, span=build_span)
                        for r in refs))
        result = BuildResult(
            outcome='completed', domain_outcome=domain, claim_scope=REGEN.claim,
            artifacts=tuple(refs), measurements=measurements,
            report_sha256=report_sha if isinstance(report_sha, str) and _HEX64.match(report_sha) else None,
            op_stream=plan.op_stream, artifact_graph=graph, **common)
        return Built(result, tuple(carried), evaluation)

    # ------------------------------------------------------------------ inspect / export

    async def inspect(self, artifact: Artifact, plan: BuildPlan, *,
                      report: Mapping[str, Any] | None = None) -> InspectResult:
        """Reread the STL with the existing regen_mesh readers and compare it with the report.

        The plan does not carry the verifier report, so the caller passes it as ``report`` (the
        ``evaluation['report']`` of the build). Without it the inspection is unavailable
        ('evidence_invalid'). The data is bounded by REGEN.resources.run_bytes before any parsing.
        """
        sha = artifact.ref.sha256

        def refuse(outcome: str, reason: str) -> InspectResult:
            return InspectResult(backend='picogk', mode='native', outcome=outcome, reasons=(reason,),
                                 artifact_sha256=sha)

        if artifact.ref.format != 'stl' or not self.supports(plan).supported:
            return refuse('unsupported', 'operation_not_supported')
        if len(artifact.data) > REGEN.resources.run_bytes:
            return refuse('unavailable', 'budget_exceeded')
        if not isinstance(report, Mapping):
            return refuse('unavailable', 'evidence_invalid')
        try:
            spec = regen_adapter.revalidate(plan.candidate.content.specification)
            tolerance = float(_pinned()['BBOX_TOLERANCE_VOXELS']) * spec.voxel_size_mm
        except (UnsupportedCapability, ValueError, OSError):
            return refuse('unavailable', 'evidence_invalid')
        # K37: one dedicated reread thread, one non-waiting slot. Busy never queues or waits.
        if not _REREAD_SLOT.acquire(blocking=False):
            return refuse('unavailable', 'busy')
        data = artifact.data

        def reread():
            try:
                return _reread_mesh(data)
            finally:
                _REREAD_SLOT.release()   # the worker frees the slot, not the timed-out caller

        try:
            future = _REREAD_EXECUTOR.submit(reread)
        except BaseException:
            _REREAD_SLOT.release()
            raise
        # A future cancelled while still pending never runs reread(), so free the slot here.
        future.add_done_callback(lambda f: _REREAD_SLOT.release() if f.cancelled() else None)
        try:
            # The reread thread cannot be cancelled, but the caller gets a bounded answer.
            mesh = await asyncio.wait_for(asyncio.wrap_future(future),
                                          min(INSPECT_DEADLINE_S, _INSPECT_DEADLINE_S))
        except (asyncio.TimeoutError, TimeoutError):
            return refuse('unavailable', 'deadline_exceeded')
        except (UnsupportedCapability, ValueError, OSError):
            return refuse('unavailable', 'evidence_invalid')
        return compare_reread(report, mesh, sha, bbox_tolerance_mm=tolerance)

    def export(self, artifact: Artifact, fmt: KernelFormat) -> ExportResult:
        if fmt == 'stl' and artifact.ref.format == 'stl':
            return ExportResult(backend='picogk', mode='native', outcome='exported', format=fmt,
                                artifact=artifact.ref)
        # No conversion and no STEP, ever.
        return ExportResult(backend='picogk', mode='native', outcome='unsupported',
                            reasons=('export_not_supported',), format=fmt)
