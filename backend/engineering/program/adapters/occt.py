"""OCCT kernel adapter (F03): a thin wrapper over the existing CadQuery beam path.

It never decides engineering acceptance and never re-implements beam mathematics: the verdict is
copied from the evaluation dict returned by engineering.cantilever.CantileverAdapter.evaluate
(policy.assess already ran inside it). Module-level imports stay native-free; cadquery and OCP are
only ever imported inside owned subprocesses (the probe script and the existing worker.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from ..errors import SourceSpan
from ..kernel import (
    Artifact, ArtifactRef, BackendDescription, Built, BuildPlan, BuildResult, ExecutionContext,
    ExportResult, InspectResult, KernelFormat, Measurement, NativeVersion, ProbeResult,
    SupportDecision, ToleranceConvention, check_plan, make_description,
)
from ..registry import ArtifactGraph, ArtifactNode

__all__ = ['OcctAdapter', 'ADAPTER_VERSION', 'compare_reread']

ADAPTER_VERSION = 'occt-0.1.0'

_PROBE_SCRIPT = Path(__file__).with_name('_occt_probe.py')
_WORKER_SCRIPT = Path(__file__).resolve().parents[2] / 'worker.py'

# Limits cited from engineering/service.py generate_verified (line 34): the worker runs under
# ProcessRunner(timeout=30, output_limit=1048576, memory_mb=1536) and its report is capped at 1 MiB.
_WORKER_TIMEOUT_S = 30
_WORKER_OUTPUT_LIMIT = 1048576
_WORKER_MEMORY_MB = 1536
_REPORT_CAP_BYTES = 1048576
# The probe prints one short JSON line; same memory and timeout budget as the worker.
_PROBE_OUTPUT_LIMIT = 65536

# The beam capability descriptor (engineering.capabilities.CAPABILITY) has no `claim` field. The
# existing code states the beam claim as this literal (engineering/api_v1.py:384, router.CHECKED).
_BEAM_CLAIM = 'checked_design'

_BOX_VOLUME = 1 * 2 * 3  # probe box 1 x 2 x 3 mm
_HEX64 = re.compile(r'^[0-9a-f]{64}$')

# report['measurements'] key -> (record name, unit)
_MEASUREMENT_NAMES: tuple[tuple[str, str, str], ...] = (
    ('length_mm', 'beam.length', 'mm'),
    ('width_mm', 'beam.width', 'mm'),
    ('height_mm', 'beam.height', 'mm'),
    ('volume_mm3', 'beam.volume', 'mm3'),
    ('surface_area_mm2', 'beam.surface_area', 'mm2'),
    ('section_area_mm2', 'beam.section_area', 'mm2'),
    ('section_iy_mm4', 'beam.section_iy', 'mm4'),
)
_REQUIRED_REREAD = ('length_mm', 'width_mm', 'height_mm', 'volume_mm3')
# verify_step checks that compare the reread geometry with the requested dimensions.
_DIMENSION_CHECKS = ('size_requirements', 'complete_prism_metrics', 'measured_root_section')
_MEASUREMENT_UNITS = ('mm', 'mm2', 'mm3', 'mm4')
_SEMANTIC_TAIL = re.compile(r'^[a-z][a-z0-9_]{0,63}$')

_ARTIFACT_SPECS = (
    # (artifact name in evaluation['artifacts'], format, representation, node id)
    ('model.step', 'step', 'brep', 'beam.solid'),
    ('preview.glb', 'glb', 'mesh', 'beam.preview'),
)


def _tolerances() -> tuple[ToleranceConvention, ...]:
    # The per-brief dimensional tolerance (spec.dimensional_tolerance_mm) has no static constant, so
    # it is deliberately not declared here.
    return (
        ToleranceConvention(
            name='topology_tolerance_max', value=1e-6, unit='mm',
            source='engineering.verify.verify_step dimensional_evidence_budget'),
        ToleranceConvention(
            name='datum_origin', value=1e-6, unit='mm',
            source='engineering.verify.verify_step datum_frame'),
        ToleranceConvention(
            name='volume_relative', value=1e-8, unit='relative',
            source='engineering.verify.verify_step complete_prism_metrics'),
        ToleranceConvention(
            name='volume_absolute_floor', value=1e-6, unit='mm3',
            source='engineering.verify.verify_step complete_prism_metrics'),
    )


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _reject_constant(_name):
    raise ValueError('non-finite JSON constant')


def _measurements(raw: Any, source: str) -> tuple[Measurement, ...]:
    """Map report measurement keys to records; only keys present and finite, never guessed."""
    if not isinstance(raw, Mapping):
        return ()
    return tuple(Measurement(name=name, value=float(raw[key]), unit=unit, source=source)
                 for key, name, unit in _MEASUREMENT_NAMES if _finite(raw.get(key)))


def compare_reread(report: Mapping[str, Any], spec: Any, artifact_sha256: str) -> InspectResult:
    """Pure comparison of a reread report against the requested dimensions (no native call)."""
    def result(outcome, reasons=(), measurements=(), deviations=()):
        return InspectResult(backend='occt', mode='native', outcome=outcome, reasons=reasons,
                             artifact_sha256=artifact_sha256, measurements=measurements,
                             deviations=deviations)

    if not isinstance(report, Mapping):
        return result('unavailable', ('evidence_invalid',))
    if report.get('step_sha256') != artifact_sha256:
        return result('precision_failure', ('reread_mismatch',))
    # verify_step stops before recording measurements when a dimensional check fails; the failed
    # check's own evidence (from the independent reader, never the adapter) is the deviation.
    checks = report.get('checks')
    failed = [checks[n] for n in _DIMENSION_CHECKS
              if isinstance(checks, Mapping) and isinstance(checks.get(n), Mapping)
              and checks[n].get('state') == 'failed']
    if failed:
        deviations = tuple(
            Measurement(name=f'beam.reread.{m["name"]}', value=float(m['value']), unit=m['unit'],
                        source='reread')
            for check in failed for m in (check.get('measurements') or ())
            if isinstance(m, Mapping) and isinstance(m.get('name'), str)
            and _SEMANTIC_TAIL.match(m['name']) and _finite(m.get('value'))
            and m.get('unit') in _MEASUREMENT_UNITS)
        return result('precision_failure', ('measurement_out_of_tolerance',), (), deviations)
    raw = report.get('measurements')
    if not isinstance(raw, Mapping) or not all(_finite(raw.get(k)) for k in _REQUIRED_REREAD):
        return result('unavailable', ('evidence_invalid',))
    measured = _measurements(raw, 'reread')
    length, width, height, volume = (float(raw[k]) for k in _REQUIRED_REREAD)
    requested = (spec.length_mm, spec.width_mm, spec.height_mm)
    dims = tuple(abs(a - b) for a, b in zip((length, width, height), requested))
    # Same rule as verify.verify_step size_requirements (tolerance = spec.dimensional_tolerance_mm).
    dims_ok = all(d <= spec.dimensional_tolerance_mm for d in dims)
    volume_error = abs(volume - length * width * height)
    # Same rule as verify.verify_step complete_prism_metrics.
    volume_ok = volume_error <= max(1e-6, volume * 1e-8)
    deviations = tuple(
        Measurement(name=name, value=value, unit=unit, source='reread')
        for name, value, unit in (('beam.length', dims[0], 'mm'), ('beam.width', dims[1], 'mm'),
                                  ('beam.height', dims[2], 'mm'), ('beam.volume', volume_error, 'mm3')))
    if not (dims_ok and volume_ok):
        return result('precision_failure', ('measurement_out_of_tolerance',), measured, deviations)
    return result('consistent', (), measured, deviations)


class OcctAdapter:
    backend = 'occt'
    mode = 'native'

    def __init__(self, *, python: str | None = None):
        self._python = python or sys.executable

    def describe(self) -> BackendDescription:
        return make_description('occt', mode='native', adapter_version=ADAPTER_VERSION,
                                tolerances=_tolerances())

    # ------------------------------------------------------------------ probe

    async def probe(self) -> ProbeResult:
        def no(reason: str) -> ProbeResult:
            return ProbeResult(backend='occt', mode='native', available=False, reasons=(reason,))

        if not os.path.exists(self._python):
            return no('runtime_missing')
        from process_runner import ProcessRunner
        # Same timeout and memory budget as the worker (service.py:34); output capped at 64 KiB.
        runner = ProcessRunner(timeout=30, output_limit=_PROBE_OUTPUT_LIMIT, memory_mb=1536)
        try:
            outcome = await runner.run([self._python, str(_PROBE_SCRIPT)], env=None)
        finally:
            await runner.close()
        if not isinstance(outcome, Mapping):
            return no('probe_failed')
        if outcome.get('code'):  # includes PROCESS_TREE_UNAVAILABLE, TIMEOUT, OUTPUT_LIMIT
            return no('probe_failed')
        if outcome.get('returncode') == 3:
            return no('runtime_missing')
        if outcome.get('returncode') != 0:
            return no('probe_failed')
        stdout = outcome.get('stdout')
        if not isinstance(stdout, str) or len(stdout.encode('utf-8', 'ignore')) > _PROBE_OUTPUT_LIMIT:
            return no('probe_failed')
        try:
            data = json.loads(stdout, parse_constant=_reject_constant)
        except ValueError:
            return no('probe_failed')
        if not isinstance(data, dict):
            return no('probe_failed')
        if data.get('error') == 'import':
            return no('runtime_missing')
        cad, ocp, volume = data.get('cadquery'), data.get('ocp'), data.get('volume')
        if not (isinstance(cad, str) and isinstance(ocp, str) and cad and ocp and _finite(volume)):
            return no('probe_failed')
        # Same rule as verify.verify_step complete_prism_metrics.
        if abs(volume - _BOX_VOLUME) > max(1e-6, _BOX_VOLUME * 1e-8):
            return no('probe_failed')
        try:
            versions = (NativeVersion(name='cadquery', version=cad), NativeVersion(name='OCP', version=ocp))
            return ProbeResult(backend='occt', mode='native', available=True, native_versions=versions)
        except ValidationError:
            return no('probe_failed')

    # ------------------------------------------------------------------ support / build

    def supports(self, plan: BuildPlan) -> SupportDecision:
        return check_plan(self.describe(), plan)

    async def build(self, plan: BuildPlan, context: ExecutionContext) -> Built:
        common = dict(backend='occt', adapter_version=ADAPTER_VERSION, mode='native',
                      capability_id=plan.capability_id, capability_version=plan.capability_version)

        def refuse(outcome: str, reasons: tuple, code: str | None = None) -> Built:
            return Built(BuildResult(outcome=outcome, reasons=reasons, diagnostic_code=code, **common))

        decision = self.supports(plan)
        if not decision.supported:
            return refuse('unsupported', decision.reasons, decision.diagnostic_code)
        probe = await self.probe()
        if not probe.available:
            return refuse('unavailable', probe.reasons)

        # Lazy: these modules sit next to native code and are only needed once a build starts.
        from engineering.cantilever import CantileverAdapter
        from engineering.capabilities import UnsupportedCapability
        from engineering.service import EngineeringUnavailable
        try:
            candidate = type(plan.candidate).model_validate(plan.candidate.model_dump())
        except ValueError:
            return refuse('unsupported', ('candidate_invalid',))
        try:
            evaluation = await asyncio.wait_for(CantileverAdapter().evaluate(candidate),
                                                context.deadline_s)
        except (asyncio.TimeoutError, TimeoutError):
            return refuse('unavailable', ('deadline_exceeded',))
        except EngineeringUnavailable:
            return refuse('unavailable', ('native_failed',))
        except UnsupportedCapability:
            return refuse('unsupported', ('candidate_invalid',))

        domain = getattr(evaluation.get('outcome'), 'value', None)
        if domain not in ('accepted', 'rejected'):
            return refuse('unavailable', ('evidence_invalid',))
        report = evaluation.get('report')
        report_sha = evaluation.get('report_sha256')
        measurements = _measurements(report.get('measurements') if isinstance(report, Mapping) else None,
                                     'report')
        refs: list[ArtifactRef] = []
        carried: list[Artifact] = []
        if domain == 'accepted':
            produced = evaluation.get('artifacts')
            produced = produced if isinstance(produced, Mapping) else {}
            data = {name: produced.get(name) for name, *_ in _ARTIFACT_SPECS}
            if not all(isinstance(v, (bytes, bytearray)) for v in data.values()):
                return refuse('unavailable', ('evidence_invalid',))
            if sum(len(v) for v in data.values()) > context.max_artifact_bytes:
                return refuse('unavailable', ('budget_exceeded',))
            for name, fmt, representation, node_id in _ARTIFACT_SPECS:
                blob = bytes(data[name])
                ref = ArtifactRef(name=name, format=fmt, representation=representation,
                                  sha256=hashlib.sha256(blob).hexdigest(), size=len(blob),
                                  node_id=node_id)
                refs.append(ref)
                carried.append(Artifact(ref, blob))
        build_span: SourceSpan | None = plan.op_stream.entries[0].span
        graph = ArtifactGraph(
            schema_version='mirum_artifact_graph_v0_1', op_stream=plan.op_stream,
            nodes=tuple(ArtifactNode(id=r.node_id, kind='solid', op_seq=0, span=build_span)
                        for r in refs))
        result = BuildResult(
            outcome='completed', domain_outcome=domain, claim_scope=_BEAM_CLAIM,
            artifacts=tuple(refs), measurements=measurements,
            report_sha256=report_sha if isinstance(report_sha, str) and _HEX64.match(report_sha) else None,
            op_stream=plan.op_stream, artifact_graph=graph, **common)
        return Built(result, tuple(carried), evaluation)

    # ------------------------------------------------------------------ inspect / export

    async def inspect(self, artifact: Artifact, plan: BuildPlan) -> InspectResult:
        sha = artifact.ref.sha256

        def refuse(outcome: str, reason: str) -> InspectResult:
            return InspectResult(backend='occt', mode='native', outcome=outcome, reasons=(reason,),
                                 artifact_sha256=sha)

        if artifact.ref.format != 'step' or not self.supports(plan).supported:
            return refuse('unsupported', 'operation_not_supported')

        from compiler import _WORK_DIR
        from process_runner import ProcessRunner
        from engineering.spec import canonical_spec
        spec = plan.candidate.specification
        with tempfile.TemporaryDirectory(prefix='kernel-occt-', dir=_WORK_DIR) as temporary:
            stage = Path(temporary)
            step_path, spec_path, report_path = (stage / n for n in ('model.step', 'spec.json', 'report.json'))
            step_path.write_bytes(artifact.data)
            spec_path.write_text(canonical_spec(spec), encoding='utf-8')
            # Same environment construction as engineering.service.generate_verified.
            env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'SYSTEMROOT', 'WINDIR',
                   'USERPROFILE', 'TEMP', 'TMP') if key in os.environ}
            env.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
            runner = ProcessRunner(timeout=_WORKER_TIMEOUT_S, output_limit=_WORKER_OUTPUT_LIMIT,
                                   memory_mb=_WORKER_MEMORY_MB)
            try:
                outcome = await runner.run([self._python, str(_WORKER_SCRIPT), str(step_path),
                                            str(spec_path), str(report_path)], cwd=str(stage), env=env)
            finally:
                await runner.close()
            if not isinstance(outcome, Mapping) or outcome.get('code') or outcome.get('returncode') != 0:
                return refuse('unavailable', 'native_failed')
            # Regular file only (lstat + O_NOFOLLOW + fstat), at most _REPORT_CAP_BYTES.
            from engineering.regen_adapter import RegenUnavailable, read_bounded
            try:
                raw = read_bounded(report_path, _REPORT_CAP_BYTES)
                report = json.loads(raw.decode('utf-8'), parse_constant=_reject_constant)
            except (RegenUnavailable, OSError, ValueError):
                return refuse('unavailable', 'native_failed')
        if not isinstance(report, dict):
            return refuse('unavailable', 'native_failed')
        return compare_reread(report, spec, sha)

    def export(self, artifact: Artifact, fmt: KernelFormat) -> ExportResult:
        if fmt in ('step', 'glb') and artifact.ref.format == fmt:
            return ExportResult(backend='occt', mode='native', outcome='exported', format=fmt,
                                artifact=artifact.ref)
        # No conversion, ever.
        return ExportResult(backend='occt', mode='native', outcome='unsupported',
                            reasons=('export_not_supported',), format=fmt)

    # ------------------------------------------------------------------ measure (F07, additive)

    async def measure(self, artifact_step: Artifact, artifact_glb: Artifact | None,
                      plan: BuildPlan) -> 'MeasureOutcome':
        """Measure the EXACT delivered bytes in an owned subprocess (_occt_measure.py).

        Not part of the KernelAdapter Protocol. Never raises on bad bytes: any failure is a
        MeasureOutcome with table None and a reason (native_failed / too_many_faces / unsupported).
        """
        if (artifact_step.ref.format != 'step'
                or (artifact_glb is not None and artifact_glb.ref.format != 'glb')
                or not self.supports(plan).supported):
            return MeasureOutcome(None, None, 'unsupported')
        from compiler import _WORK_DIR
        from process_runner import ProcessRunner
        try:
            with tempfile.TemporaryDirectory(prefix='kernel-occt-', dir=_WORK_DIR) as temporary:
                stage = Path(temporary)
                step_path, glb_path = stage / 'model.step', stage / 'preview.glb'
                step_path.write_bytes(artifact_step.data)
                command = [self._python, str(_MEASURE_SCRIPT), str(step_path)]
                if artifact_glb is not None:
                    glb_path.write_bytes(artifact_glb.data)
                    command.append(str(glb_path))
                env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'SYSTEMROOT', 'WINDIR',
                       'USERPROFILE', 'TEMP', 'TMP') if key in os.environ}
                env.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
                runner = ProcessRunner(timeout=_WORKER_TIMEOUT_S, output_limit=_WORKER_OUTPUT_LIMIT,
                                       memory_mb=_WORKER_MEMORY_MB)
                try:
                    outcome = await runner.run(command, cwd=str(stage), env=env)
                finally:
                    await runner.close()
        except OSError:
            return MeasureOutcome(None, None, 'native_failed')
        if not isinstance(outcome, Mapping) or outcome.get('code') or outcome.get('returncode') != 0:
            return MeasureOutcome(None, None, 'native_failed')
        return parse_measure_stdout(outcome.get('stdout'), want_mesh=artifact_glb is not None)


@dataclass(frozen=True)
class MeasureOutcome:
    """Result of OcctAdapter.measure. table None means no usable measurement (see reason).

    mesh_error is set when a GLB was supplied but was unparsable or out of budget; mesh is then an
    empty MeshSummary so the evaluator fails the preview closed (empty mesh), never silently skips it.
    """
    table: 'FaceTable | None'
    mesh: 'MeshSummary | None'
    reason: str | None = None
    mesh_error: str | None = None


_MEASURE_SCRIPT = Path(__file__).with_name('_occt_measure.py')
_MEASURE_STDOUT_LIMIT = 1048576


def parse_measure_stdout(stdout: Any, *, want_mesh: bool) -> MeasureOutcome:
    """Validate the measurement worker's single JSON object. Fails closed; never raises."""
    from ..evidence import GLB_LINEAR_DEFLECTION_MM, FaceTable, MeshSummary

    def fail(reason: str) -> MeasureOutcome:
        return MeasureOutcome(None, None, reason)

    if not isinstance(stdout, str) or len(stdout.encode('utf-8', 'ignore')) > _MEASURE_STDOUT_LIMIT:
        return fail('native_failed')
    try:
        data = json.loads(stdout, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return fail('native_failed')
    if not isinstance(data, dict):
        return fail('native_failed')
    if data.get('error') == 'too_many_faces':
        return fail('too_many_faces')
    table_raw = data.get('table')
    if data.get('error') or not isinstance(table_raw, dict):
        return fail('native_failed')
    try:
        table = FaceTable.model_validate_json(json.dumps(table_raw))
    except (ValidationError, ValueError):
        return fail('native_failed')
    if not want_mesh:
        return MeasureOutcome(table, None)
    mesh_raw = data.get('mesh')
    empty = MeshSummary(triangle_count=0, vertex_count=0, bounds_mm=(0.0,) * 6,
                        mesh_bound_mm=GLB_LINEAR_DEFLECTION_MM)
    if not isinstance(mesh_raw, dict) or mesh_raw.get('error') is not None or 'bounds_mm' not in mesh_raw:
        error = mesh_raw.get('error') if isinstance(mesh_raw, dict) else None
        code = error if isinstance(error, str) and re.fullmatch(r'[a-z_]{1,40}', error) else 'glb_invalid'
        return MeasureOutcome(table, empty, None, code)
    try:
        mesh = MeshSummary.model_validate_json(json.dumps({
            'triangle_count': mesh_raw.get('triangle_count'), 'vertex_count': mesh_raw.get('vertex_count'),
            'bounds_mm': mesh_raw.get('bounds_mm'), 'mesh_bound_mm': GLB_LINEAR_DEFLECTION_MM,
            'node_names': mesh_raw.get('node_names', [])}))
    except (ValidationError, ValueError):
        return MeasureOutcome(table, empty, None, 'glb_invalid')
    return MeasureOutcome(table, mesh)
