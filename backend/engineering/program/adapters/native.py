"""mirum-native plate adapter (N1c-2): builds editable_plate@1.0.0 with the pinned native kernel.

Trust model. The binary is pinned by SHA-256 (adapters/native_pin.py) and every probe re-checks the file
(absolute path, regular file, not a symlink, not group/world-writable, bounded size, SHA, version
handshake, an analytic volume build). The kernel never measures its own output: the STEP it writes is
re-read by the existing OCCT plate worker, and that report is the evidence. A disagreement between the two
makes the build `rejected`. Nothing here falls back to the OCCT adapter; this module never imports or
constructs it.

Residual risk (documented): the SHA check and the exec are two steps on the same path. The file must be
owned by root or the service user, and neither it nor its directory may be group/world-writable, so only
the owner could swap it between the two.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ..kernel import (
    NATIVE_CLAIMS, Artifact, BackendDescription, Built, BuildPlan, BuildResult, ExecutionContext,
    ExportResult, InspectResult, KernelFormat, Measurement, NativeVersion, ProbeResult, SupportDecision,
    ToleranceConvention, check_plan, make_description,
)
from .native_pin import BINARY_PATH, MAX_BINARY_BYTES, PIN, NativePin

__all__ = ['NativePlateAdapter', 'ADAPTER_VERSION']

ADAPTER_VERSION = 'mirum-native-plate-0.1.0'
ENV_FLAG = 'MIRUM_NATIVE_KERNEL'
ENV_BIN = 'MIRUM_NATIVE_BIN'
REPORT_SCHEMA = 'mirum_native_plate_report_v0_1'
PART_ID = 'plate'
CLAIM = NATIVE_CLAIMS['editable_plate@1.0.0']

_STDOUT_LIMIT = 1024 * 1024
_RUN_MEMORY_MB = 256
_RUN_SECONDS = 30
_VERSION_KEYS = frozenset({'name', 'version', 'commit', 'protocol'})
_PROBE_SPEC = {'width_mm': 80.0, 'height_mm': 50.0, 'thickness_mm': 6.0, 'intent': 'edge-driven',
               'datum_ref': 'plate.datum', 'units': 'mm'}
_BORE_DIAMETER = 6.0
_PROBE_VOLUME_RELATIVE = 1e-9
_NATIVE_LENGTH_TOL = 1e-6
_NATIVE_METHOD = 'mirum-native-plate-v1'


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class _Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class NativePlateAdapter:
    backend = 'mirum-native'
    mode = 'native'

    def __init__(self, *, binary: str | None = None, pin: NativePin | None = None, python: str | None = None,
                 capability: str = 'editable_plate'):
        # `pin` exists so tests can pin a fake binary. It is a constructor argument on purpose: no
        # environment variable or configuration can reach it, and production callers never pass it.
        self._binary = binary
        self._pin = pin or PIN
        self._python = python or sys.executable
        self._capability = capability

    # ------------------------------------------------------------------ description

    def describe(self) -> BackendDescription:
        from vendor.mirum_editable_plate import AXIS_TOL, LINEAR_TOL, VOLUME_TOL
        method = 'editable-plate-occt-v1'
        tolerances = (
            ToleranceConvention(name='native_length', value=_NATIVE_LENGTH_TOL, unit='mm', source=_NATIVE_METHOD),
            ToleranceConvention(name='length', value=LINEAR_TOL, unit='mm', source=method),
            ToleranceConvention(name='axis', value=AXIS_TOL, unit='relative', source=method),
            ToleranceConvention(name='volume', value=VOLUME_TOL, unit='mm3', source=method),
        )
        return make_description(self.backend, mode='native', adapter_version=ADAPTER_VERSION,
                                tolerances=tolerances)

    def supports(self, plan: BuildPlan) -> SupportDecision:
        return check_plan(self.describe(), plan)

    # ------------------------------------------------------------------ binary checks

    def _path(self) -> str:
        return self._binary or os.environ.get(ENV_BIN) or BINARY_PATH

    def _check_file(self, path: str) -> None:
        """Raises _Refused. Synchronous; called in a thread."""
        if not isinstance(path, str) or not os.path.isabs(path) or '\x00' in path:
            raise _Refused('probe_failed')
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            raise _Refused('runtime_missing') from None
        except OSError:
            raise _Refused('probe_failed') from None
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BINARY_BYTES or before.st_size == 0:
            raise _Refused('probe_failed')
        if before.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or not before.st_mode & stat.S_IXUSR:
            raise _Refused('probe_failed')
        geteuid = getattr(os, 'geteuid', None)
        if geteuid is not None and before.st_uid not in (0, geteuid()):
            raise _Refused('probe_failed')
        try:
            parent = os.lstat(os.path.dirname(path))
        except OSError:
            raise _Refused('probe_failed') from None
        if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise _Refused('probe_failed')
        digest = hashlib.sha256()
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as source:
                after = os.fstat(source.fileno())
                if (after.st_ino, after.st_dev) != (before.st_ino, before.st_dev) or not stat.S_ISREG(after.st_mode):
                    raise _Refused('probe_failed')
                total = 0
                while True:
                    block = source.read(1 << 20)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_BINARY_BYTES:
                        raise _Refused('probe_failed')
                    digest.update(block)
        except OSError:
            raise _Refused('probe_failed') from None
        if digest.hexdigest() != self._pin.sha256:
            raise _Refused('probe_failed')

    async def _run(self, argv: list[str], *, seconds: float, cwd: str | None = None):
        from process_runner import ProcessRunner
        runner = ProcessRunner(timeout=seconds, output_limit=_STDOUT_LIMIT, memory_mb=_RUN_MEMORY_MB)
        try:
            return await runner.run(argv, env={'LANG': 'C'}, cwd=cwd)
        finally:
            await runner.close()

    @staticmethod
    def _stage_dir():
        from compiler import _WORK_DIR
        os.makedirs(_WORK_DIR, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix='kernel-native-', dir=str(_WORK_DIR))

    @staticmethod
    def _outcome_reason(result) -> str | None:
        """None when the process exited 0 cleanly, else the unavailable reason."""
        if not isinstance(result, Mapping):
            return 'native_failed'
        code = result.get('code')
        if code == 'TIMEOUT':
            return 'deadline_exceeded'
        if code == 'OUTPUT_LIMIT':
            return 'budget_exceeded'
        if code or result.get('returncode') != 0:
            return 'native_failed'
        return None

    @staticmethod
    def _parse(stdout) -> Any:
        from ..delivered import _Problem, parse_json_bounded
        if not isinstance(stdout, str):
            raise _Refused('evidence_invalid')
        try:
            return parse_json_bounded(stdout.encode('utf-8', 'ignore'), _STDOUT_LIMIT)
        except (_Problem, ValueError, RecursionError):
            raise _Refused('evidence_invalid') from None

    async def _native_plate(self, path: str, spec: dict, seconds: float) -> tuple[dict, bytes]:
        """Run `plate` on `spec` in a temp dir; returns (validated report, STEP bytes). Raises _Refused."""
        from engineering.editable_plate_adapter import MAX_STEP_BYTES
        from engineering.regen_adapter import RegenUnavailable, read_bounded
        with self._stage_dir() as temporary:
            stage = Path(temporary)
            plan_path, step_path = stage / 'plan.json', stage / 'out.step'
            with open(plan_path, 'x', encoding='utf-8') as out:
                json.dump(spec, out, sort_keys=True, allow_nan=False)
            result = await self._run([path, 'plate', str(plan_path), '--step', str(step_path)],
                                     seconds=seconds, cwd=str(stage))
            reason = self._outcome_reason(result)
            if reason is not None:
                raise _Refused(reason)
            report = self._parse(result.get('stdout'))
            if (not isinstance(report, dict) or report.get('schema') != REPORT_SCHEMA
                    or report.get('part_id') != PART_ID or report.get('outcome') != 'pass'
                    or report.get('diagnostics') != [] or report.get('specification') != spec):
                raise _Refused('evidence_invalid')
            volume, envelope = report.get('volume_mm3'), report.get('envelope_mm')
            if (not _finite(volume) or not isinstance(envelope, list) or len(envelope) != 3
                    or not all(_finite(v) for v in envelope)):
                raise _Refused('evidence_invalid')
            try:
                step = read_bounded(step_path, MAX_STEP_BYTES)
            except (RegenUnavailable, OSError):
                raise _Refused('evidence_invalid') from None
            if not step:
                raise _Refused('evidence_invalid')
        return report, step

    # ------------------------------------------------------------------ probe

    async def probe(self) -> ProbeResult:
        def no(reason: str) -> ProbeResult:
            return ProbeResult(backend=self.backend, mode='native', available=False, reasons=(reason,))

        if os.environ.get(ENV_FLAG) != '1':
            return no('runtime_missing')
        path = self._path()
        try:
            await asyncio.to_thread(self._check_file, path)
            result = await self._run([path, 'version'], seconds=10)
            if self._outcome_reason(result) is not None:
                return no('probe_failed')
            handshake = self._parse(result.get('stdout'))
            pin = self._pin
            if (not isinstance(handshake, dict) or set(handshake) != _VERSION_KEYS
                    or handshake != {'name': pin.name, 'version': pin.version, 'commit': pin.commit,
                                     'protocol': pin.protocol}):
                return no('probe_failed')
            report, _step = await self._native_plate(path, dict(_PROBE_SPEC), 20)
        except _Refused as refusal:
            return no('runtime_missing' if refusal.reason == 'runtime_missing' else 'probe_failed')
        w, h, t = (_PROBE_SPEC[k] for k in ('width_mm', 'height_mm', 'thickness_mm'))
        expected = w * h * t - 4 * math.pi * (_BORE_DIAMETER / 2) ** 2 * t
        if abs(report['volume_mm3'] - expected) > _PROBE_VOLUME_RELATIVE * expected:
            return no('probe_failed')
        return ProbeResult(backend=self.backend, mode='native', available=True, native_versions=(
            NativeVersion(name='mirum-native', version=self._pin.version),
            NativeVersion(name='mirum-native-commit', version=self._pin.commit),
            NativeVersion(name='mirum-native-sha256', version=self._pin.sha256)))

    # ------------------------------------------------------------------ build

    async def build(self, plan: BuildPlan, context: ExecutionContext) -> Built:
        import engineering.editable_plate_adapter as epa
        from base64 import b64encode
        from engineering.service import EngineeringUnavailable
        common = dict(backend=self.backend, adapter_version=ADAPTER_VERSION, mode='native',
                      capability_id=plan.capability_id, capability_version=plan.capability_version)

        def refuse(outcome: str, reasons: tuple, code: str | None = None) -> Built:
            return Built(BuildResult(outcome=outcome, reasons=reasons, diagnostic_code=code, **common))

        decision = self.supports(plan)
        if not decision.supported:
            return refuse('unsupported', decision.reasons, decision.diagnostic_code)
        probe = await self.probe()
        if not probe.available:
            return refuse('unavailable', probe.reasons)
        spec = dict(plan.candidate.content.specification)
        try:
            report, step = await self._native_plate(self._path(), spec, min(context.deadline_s, _RUN_SECONDS))
        except _Refused as refusal:
            return refuse('unavailable', (refusal.reason,))
        try:
            output = await epa.worker(self._python, 'inspect', spec, step=step, seconds=context.deadline_s)
            occt = dict(output['report'])
        except (KeyError, TypeError, OSError, ValueError, EngineeringUnavailable):
            return refuse('unavailable', ('native_failed',))
        occt['diagnostics'] = list(occt.get('diagnostics', ()))
        disagreements = self._disagreements(report, occt)
        if disagreements:
            occt['diagnostics'].extend(disagreements)
            occt['outcome'] = 'fail'
        w, h, t = report['envelope_mm']
        measurements = tuple(
            Measurement(name=name, value=float(value), unit=unit, source='report')
            for name, value, unit in (('plate.volume', report['volume_mm3'], 'mm3'), ('plate.width', w, 'mm'),
                                      ('plate.height', h, 'mm'), ('plate.thickness', t, 'mm')))
        try:
            return epa.build_result(plan, {'report': occt, 'step': b64encode(step).decode('ascii')}, context,
                                    backend=self.backend, adapter_version=ADAPTER_VERSION,
                                    claim_scope=CLAIM, measurements=measurements)
        except (KeyError, TypeError, ValueError):
            return refuse('unavailable', ('evidence_invalid',))

    @staticmethod
    def _disagreements(native: dict, occt: dict) -> list[dict]:
        from vendor.mirum_editable_plate import LINEAR_TOL, VOLUME_TOL
        found = []
        volume, envelope = occt.get('volume_mm3'), occt.get('envelope_mm')
        if _finite(volume) and abs(volume - native['volume_mm3']) > VOLUME_TOL:
            found.append({'code': 'native_disagreement', 'field': 'volume_mm3',
                          'message': 'the native and OCCT volumes differ'})
        if (isinstance(envelope, list) and len(envelope) == 3 and all(_finite(v) for v in envelope)
                and any(abs(a - b) > LINEAR_TOL for a, b in zip(envelope, native['envelope_mm']))):
            found.append({'code': 'native_disagreement', 'field': 'envelope_mm',
                          'message': 'the native and OCCT envelopes differ'})
        return found

    # ------------------------------------------------------------------ inspect / export

    async def measure_plate(self, step: bytes, plan: BuildPlan) -> dict:
        import engineering.editable_plate_adapter as epa
        from engineering.service import EngineeringUnavailable
        if not self.supports(plan).supported:
            raise EngineeringUnavailable('Native plate inspection is unsupported')
        return await epa.worker(self._python, 'inspect', plan.candidate.content.specification, step=step)

    async def inspect(self, artifact: Artifact, plan: BuildPlan) -> InspectResult:
        from engineering.service import EngineeringUnavailable
        sha = artifact.ref.sha256

        def refuse(outcome: str, reason: str) -> InspectResult:
            return InspectResult(backend=self.backend, mode='native', outcome=outcome, reasons=(reason,),
                                 artifact_sha256=sha)

        if artifact.ref.format != 'step' or not self.supports(plan).supported:
            return refuse('unsupported', 'operation_not_supported')
        try:
            output = await self.measure_plate(artifact.data, plan)
            consistent = output['report']['outcome'] == 'pass'
        except (KeyError, TypeError, OSError, ValueError, EngineeringUnavailable):
            return refuse('unavailable', 'native_failed')
        if not consistent:
            return refuse('precision_failure', 'measurement_out_of_tolerance')
        return InspectResult(backend=self.backend, mode='native', outcome='consistent', artifact_sha256=sha)

    def export(self, artifact: Artifact | None, fmt: KernelFormat) -> ExportResult:
        if artifact is not None and fmt == 'step' and artifact.ref.format == 'step':
            return ExportResult(backend=self.backend, mode='native', outcome='exported', format=fmt,
                                artifact=artifact.ref)
        return ExportResult(backend=self.backend, mode='native', outcome='unsupported',
                            reasons=('export_not_supported',), format=fmt)
