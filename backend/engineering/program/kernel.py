"""Kernel adapter protocol, records, reason allowlists and plan helpers (F03).

Pure records and pure helpers. Nothing here imports a native kernel, a web framework or a client
library; native imports happen lazily inside the adapters' build/inspect/probe only.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any, Literal, Mapping, Protocol, runtime_checkable

from pydantic import Field, StringConstraints, model_validator

from engineering.contracts import CandidateSpec, GeneratorCandidate

from .errors import IRModel, ProgramError, SourceSpan, Str240
from .ir import MAX_LIST, Backend, ExportFormat, Name64, Name128, SemanticId, _prepare_input
from .registry import (
    PLATE_OPERATION_VERSION, THERMAL_OPERATION_VERSION, ArtifactGraph, OpStream, OpStreamEntry, Representation,
    registry_for_capability, validate_op_stream,
)

__all__ = [
    'KERNEL_RECORDS_VERSION', 'BackendId', 'Mode', 'KernelFormat', 'ClaimScope',
    'UNSUPPORTED_REASONS', 'UNAVAILABLE_REASONS', 'PRECISION_REASONS',
    'UnsupportedReason', 'UnavailableReason', 'PrecisionReason',
    'NativeVersion', 'ToleranceConvention', 'SpanBinding', 'BackendDescription', 'ProbeResult',
    'SupportDecision', 'ExecutionContext', 'BuildPlan', 'ArtifactRef', 'Measurement', 'BuildResult',
    'InspectResult', 'ExportResult', 'BackendLock', 'Artifact', 'Built', 'KernelAdapter',
    'OCCT_DECLARATION', 'PICOGK_DECLARATION', 'make_description', 'plan_for', 'check_plan',
    'backend_lock', 'canonical_entries', 'ExecutionLockV0_2',
]

KERNEL_RECORDS_VERSION = 'mirum_kernel_v0_1'
SchemaVersion = Literal['mirum_kernel_v0_1']

BackendId = Literal['occt', 'picogk']
Mode = Literal['native', 'mock']
KernelFormat = Literal['step', 'glb', 'stl']
ClaimScope = Literal['checked_design', 'geometry_checks_only', 'screening_only', 'thermal_benchmark']

UNSUPPORTED_REASONS = ('capability_not_supported_by_backend', 'backend_mismatch',
                       'export_not_supported', 'operation_not_executable', 'operation_not_supported',
                       'plan_invalid', 'candidate_invalid')
UNAVAILABLE_REASONS = ('runtime_missing', 'probe_failed', 'busy', 'deadline_exceeded',
                       'native_failed', 'evidence_invalid', 'budget_exceeded', 'mock_backend')
PRECISION_REASONS = ('measurement_out_of_tolerance', 'reread_mismatch')

UnsupportedReason = Literal[
    'capability_not_supported_by_backend', 'backend_mismatch', 'export_not_supported',
    'operation_not_executable', 'operation_not_supported', 'plan_invalid', 'candidate_invalid']
UnavailableReason = Literal[
    'runtime_missing', 'probe_failed', 'busy', 'deadline_exceeded', 'native_failed',
    'evidence_invalid', 'budget_exceeded', 'mock_backend']
PrecisionReason = Literal['measurement_out_of_tolerance', 'reread_mismatch']

Sha256Hex = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
_Reasons = Annotated[tuple[UnsupportedReason | UnavailableReason | PrecisionReason, ...],
                     Field(max_length=MAX_LIST)]


def _bad(msg: str, field: str | None = None):
    raise ProgramError('IR010', msg, field)


# ------------------------------------------------------------------ small records

class NativeVersion(IRModel):
    name: Name64
    version: Str240


class ToleranceConvention(IRModel):
    name: Name64
    value: Annotated[float, Field(ge=0)]
    unit: Literal['mm', 'mm3', 'relative', 'count']
    source: Str240


class SpanBinding(IRModel):
    key: Literal['build', 'inspect', 'export']
    span: SourceSpan


class BackendDescription(IRModel):
    schema_version: SchemaVersion = KERNEL_RECORDS_VERSION
    backend: BackendId
    adapter_version: Name64
    mode: Mode
    representations: Annotated[tuple[Representation, ...], Field(max_length=MAX_LIST)]
    export_formats: Annotated[tuple[KernelFormat, ...], Field(max_length=MAX_LIST)]
    tolerances: Annotated[tuple[ToleranceConvention, ...], Field(max_length=MAX_LIST)]
    capabilities: Annotated[tuple[Str240, ...], Field(max_length=MAX_LIST)]
    operations: Annotated[tuple[Str240, ...], Field(max_length=MAX_LIST)]


class ProbeResult(IRModel):
    schema_version: SchemaVersion = KERNEL_RECORDS_VERSION
    backend: BackendId
    mode: Mode
    available: bool
    native_versions: Annotated[tuple[NativeVersion, ...], Field(max_length=MAX_LIST)] = ()
    reasons: Annotated[tuple[UnavailableReason, ...], Field(max_length=MAX_LIST)] = ()

    @model_validator(mode='after')
    def _rules(self):
        if self.available == bool(self.reasons):
            _bad('probe is available if and only if it has no reasons', 'reasons')
        if self.mode == 'mock' and (self.available or 'mock_backend' not in self.reasons):
            _bad("a mock probe is never available and carries reason 'mock_backend'", 'mode')
        return self


class SupportDecision(IRModel):
    schema_version: SchemaVersion = KERNEL_RECORDS_VERSION
    backend: BackendId
    mode: Mode
    supported: bool
    reasons: Annotated[tuple[UnsupportedReason, ...], Field(max_length=MAX_LIST)] = ()
    diagnostic_code: Name64 | None = None

    @model_validator(mode='after')
    def _rules(self):
        if self.supported == bool(self.reasons):
            _bad('a decision is supported if and only if it has no reasons', 'reasons')
        return self


class ExecutionContext(IRModel):
    """Deadline and byte budget for one build. A scope label only: no credentials, paths or env."""
    schema_version: SchemaVersion = KERNEL_RECORDS_VERSION
    deadline_s: Annotated[float, Field(gt=0, le=3600)]
    max_artifact_bytes: Annotated[int, Field(gt=0)]
    workspace: Name128


class BuildPlan(IRModel):
    schema_version: SchemaVersion = KERNEL_RECORDS_VERSION
    capability_id: Name64
    capability_version: Name64
    backend: BackendId
    candidate: CandidateSpec | GeneratorCandidate
    exports: Annotated[tuple[KernelFormat, ...], Field(min_length=1, max_length=3)]
    op_stream: OpStream

    @model_validator(mode='after')
    def _unique_exports(self):
        if len(set(self.exports)) != len(self.exports):
            _bad('exports must be unique', 'exports')
        return self


class ArtifactRef(IRModel):
    name: Name64
    format: KernelFormat
    representation: Representation
    sha256: Sha256Hex
    size: Annotated[int, Field(ge=0)]
    node_id: SemanticId


class Measurement(IRModel):
    name: SemanticId
    value: float
    unit: Literal['mm', 'mm2', 'mm3', 'mm4', '1', 'count']
    source: Literal['report', 'reread']


class BuildResult(IRModel):
    schema_version: SchemaVersion = KERNEL_RECORDS_VERSION
    backend: BackendId
    adapter_version: Name64
    mode: Mode
    capability_id: Name64
    capability_version: Name64
    outcome: Literal['completed', 'planned', 'unsupported', 'unavailable']
    reasons: Annotated[tuple[UnsupportedReason | UnavailableReason, ...], Field(max_length=MAX_LIST)] = ()
    diagnostic_code: Name64 | None = None
    domain_outcome: Literal['accepted', 'rejected'] | None = None
    claim_scope: ClaimScope | None = None
    artifacts: Annotated[tuple[ArtifactRef, ...], Field(max_length=MAX_LIST)] = ()
    measurements: Annotated[tuple[Measurement, ...], Field(max_length=MAX_LIST)] = ()
    report_sha256: Sha256Hex | None = None
    op_stream: OpStream | None = None
    artifact_graph: ArtifactGraph | None = None

    @model_validator(mode='after')
    def _rules(self):
        o = self.outcome
        bare = (self.domain_outcome is None and self.claim_scope is None and not self.artifacts
                and not self.measurements)
        if self.mode == 'mock' and o not in ('planned', 'unsupported'):
            _bad("a mock result is only ever 'planned' or 'unsupported'", 'outcome')
        if o == 'completed':
            if self.reasons:
                _bad('a completed result carries no reasons', 'reasons')
            if (self.domain_outcome is None or self.claim_scope is None or self.op_stream is None
                    or self.artifact_graph is None):
                _bad('a completed result needs domain_outcome, claim_scope, op_stream and artifact_graph',
                     'outcome')
            if self.artifact_graph.op_stream != self.op_stream:
                _bad('artifact_graph.op_stream must equal op_stream', 'artifact_graph')
            solids = {n.id for n in self.artifact_graph.nodes if n.kind == 'solid'}
            for i, a in enumerate(self.artifacts):
                if a.node_id not in solids:
                    _bad(f'artifact {a.name!r} is not bound to a solid node of the graph',
                         f'artifacts.{i}.node_id')
        elif o == 'planned':
            if self.mode != 'mock':
                _bad("only a mock result can be 'planned'", 'mode')
            if self.reasons or not bare:
                _bad('a planned result carries no reasons, verdict, claim scope, artifacts or measurements',
                     'outcome')
        elif o == 'unsupported':
            if not self.reasons or any(r not in UNSUPPORTED_REASONS for r in self.reasons):
                _bad('an unsupported result needs reasons from the unsupported allowlist', 'reasons')
            if not bare:
                _bad('an unsupported result carries no verdict, artifacts or measurements', 'outcome')
        else:
            if not self.reasons or any(r not in UNAVAILABLE_REASONS for r in self.reasons):
                _bad('an unavailable result needs reasons from the unavailable allowlist', 'reasons')
            if not bare:
                _bad('an unavailable result carries no verdict, artifacts or measurements', 'outcome')
        return self


class InspectResult(IRModel):
    schema_version: SchemaVersion = KERNEL_RECORDS_VERSION
    backend: BackendId
    mode: Mode
    outcome: Literal['consistent', 'precision_failure', 'unavailable', 'unsupported']
    reasons: _Reasons = ()
    artifact_sha256: Sha256Hex
    measurements: Annotated[tuple[Measurement, ...], Field(max_length=MAX_LIST)] = ()
    deviations: Annotated[tuple[Measurement, ...], Field(max_length=MAX_LIST)] = ()

    @model_validator(mode='after')
    def _rules(self):
        allowed = {'consistent': (), 'precision_failure': PRECISION_REASONS,
                   'unavailable': UNAVAILABLE_REASONS, 'unsupported': UNSUPPORTED_REASONS}[self.outcome]
        if self.outcome == 'consistent':
            if self.reasons:
                _bad('a consistent inspection carries no reasons', 'reasons')
        elif not self.reasons or any(r not in allowed for r in self.reasons):
            _bad(f'reasons must be a non-empty subset of the {self.outcome} allowlist', 'reasons')
        if self.mode == 'mock' and (self.outcome in ('consistent', 'precision_failure')
                                    or self.measurements or self.deviations):
            _bad('a mock inspection yields no measurements and is never consistent', 'mode')
        return self


class ExportResult(IRModel):
    schema_version: SchemaVersion = KERNEL_RECORDS_VERSION
    backend: BackendId
    mode: Mode
    outcome: Literal['exported', 'unsupported']
    reasons: Annotated[tuple[UnsupportedReason, ...], Field(max_length=MAX_LIST)] = ()
    format: KernelFormat
    artifact: ArtifactRef | None = None

    @model_validator(mode='after')
    def _rules(self):
        if self.outcome == 'exported':
            if self.reasons or self.artifact is None or self.artifact.format != self.format:
                _bad('an exported result has an artifact of its format and no reasons', 'outcome')
            if self.mode == 'mock':
                _bad('a mock result never exports', 'mode')
        elif not self.reasons or self.artifact is not None:
            _bad('an unsupported export has reasons and no artifact', 'outcome')
        return self


class BackendLock(IRModel):
    schema_version: SchemaVersion = KERNEL_RECORDS_VERSION
    backend: BackendId
    adapter_version: Name64
    mode: Mode
    native_versions: Annotated[tuple[NativeVersion, ...], Field(max_length=MAX_LIST)]
    representations: Annotated[tuple[Representation, ...], Field(max_length=MAX_LIST)]
    export_formats: Annotated[tuple[KernelFormat, ...], Field(max_length=MAX_LIST)]
    tolerances: Annotated[tuple[ToleranceConvention, ...], Field(max_length=MAX_LIST)]


def backend_lock(description: BackendDescription, probe: ProbeResult) -> BackendLock:
    """Pin the backend actually probed. Raises ValueError unless the probe is available and agrees."""
    if not probe.available:
        raise ValueError('cannot lock a backend whose probe is not available')
    if probe.backend != description.backend or probe.mode != description.mode:
        raise ValueError('probe and description disagree on backend or mode')
    return BackendLock(
        backend=description.backend, adapter_version=description.adapter_version,
        mode=description.mode, native_versions=probe.native_versions,
        representations=description.representations, export_formats=description.export_formats,
        tolerances=description.tolerances)


class ExecutionLockV0_2(IRModel):
    """F05 execution lock: the v0_1 fields plus language/compiler versions and the F03 BackendLock.

    Lives here (not ir.py) because it embeds BackendLock and kernel imports ir; ir.load_execution_lock_json
    imports it lazily."""
    schema_version: Literal['mirum_execution_lock_v0_2']
    program_ir_version: Name128
    semantics_version: Name128
    registry_version: Name128
    capability_id: Name128
    capability_version: Name128
    numerical_policy_version: Name128
    resource_policy_version: Name128
    backend: Backend
    exports: Annotated[tuple[ExportFormat, ...], Field(max_length=32)]
    language_version: Name128
    compiler_version: Name128
    backend_lock: BackendLock

    @model_validator(mode='before')
    @classmethod
    def _budgets(cls, data):
        return _prepare_input(data)

    @model_validator(mode='after')
    def _consistent(self):
        if self.backend_lock.mode != 'native':
            _bad('a mock backend lock can never be part of an execution lock', 'backend_lock.mode')
        if self.backend_lock.backend != self.backend:
            _bad('backend_lock.backend must equal backend', 'backend_lock.backend')
        for i, e in enumerate(self.exports):
            if e not in self.backend_lock.export_formats:
                _bad(f'export {e!r} is not in backend_lock.export_formats', f'exports.{i}')
        return self


# ------------------------------------------------------------------ carriers

@dataclass(frozen=True)
class Artifact:
    ref: ArtifactRef
    data: bytes

    def __post_init__(self):
        if len(self.data) != self.ref.size:
            raise ValueError('artifact size does not match its reference')
        if hashlib.sha256(self.data).hexdigest() != self.ref.sha256:
            raise ValueError('artifact sha256 does not match its reference')


@dataclass(frozen=True)
class Built:
    result: BuildResult
    artifacts: tuple[Artifact, ...] = ()
    evaluation: Mapping[str, Any] | None = None

    def __post_init__(self):
        if self.evaluation is not None and not isinstance(self.evaluation, MappingProxyType):
            object.__setattr__(self, 'evaluation', MappingProxyType(self.evaluation))


@runtime_checkable
class KernelAdapter(Protocol):
    backend: BackendId
    mode: Mode

    def describe(self) -> BackendDescription: ...

    async def probe(self) -> ProbeResult: ...

    def supports(self, plan: BuildPlan) -> SupportDecision: ...

    async def build(self, plan: BuildPlan, context: ExecutionContext) -> Built: ...

    async def inspect(self, artifact: Artifact, plan: BuildPlan) -> InspectResult: ...

    def export(self, artifact: Artifact, fmt: KernelFormat) -> ExportResult: ...


# ------------------------------------------------------------------ declarations

# Static per-backend declarations shared by the native adapters and the mock.
OCCT_DECLARATION: Mapping[str, tuple[str, ...]] = MappingProxyType(dict(
    representations=('brep', 'mesh', 'report'),
    export_formats=('step', 'glb'),
    capabilities=('rectangular_cantilever@1.0.0',),
    operations=('capability.build@0.1.0', 'capability.inspect@0.1.0', 'capability.export@0.1.0'),
))
PICOGK_DECLARATION: Mapping[str, tuple[str, ...]] = MappingProxyType(dict(
    representations=('voxel', 'mesh', 'report'),
    export_formats=('stl',),
    capabilities=('regen_chamber@1.0.0',),
    operations=('capability.build@0.2.0', 'capability.inspect@0.2.0', 'capability.export@0.2.0'),
))
_DECLARATIONS = {'occt': OCCT_DECLARATION, 'picogk': PICOGK_DECLARATION}


def make_description(backend: str, *, mode: Mode, adapter_version: str,
                     tolerances: tuple[ToleranceConvention, ...] = ()) -> BackendDescription:
    decl = _DECLARATIONS[backend]
    return BackendDescription(
        backend=backend, adapter_version=adapter_version, mode=mode,
        representations=decl['representations'], export_formats=decl['export_formats'],
        tolerances=tolerances, capabilities=decl['capabilities'], operations=decl['operations'])


# ------------------------------------------------------------------ plan helpers

_OP_VERSION = {'rectangular_cantilever': '0.1.0', 'regen_chamber': '0.2.0', 'equipment_bay': '0.3.0',
               'editable_plate': PLATE_OPERATION_VERSION, 'thermal_bar': THERMAL_OPERATION_VERSION}


def canonical_entries(capability_id: str, exports: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """(operation, operation_version) in canonical order: build, inspect, then one export each."""
    v = _OP_VERSION.get(capability_id, '0.1.0')
    return ((('capability.build', v), ('capability.inspect', v))
            + tuple(('capability.export', v) for _ in exports))


def plan_for(capability_id: str, capability_version: str, backend: str,
             candidate: CandidateSpec | GeneratorCandidate, exports: tuple[str, ...],
             spans: tuple[SpanBinding, ...] = ()) -> BuildPlan:
    bound = {s.key: s.span for s in spans}
    keys = ('build', 'inspect') + ('export',) * len(exports)
    entries = tuple(
        OpStreamEntry(seq=i, operation=op, operation_version=ver, span=bound.get(keys[i]))
        for i, (op, ver) in enumerate(canonical_entries(capability_id, exports)))
    return BuildPlan(
        capability_id=capability_id, capability_version=capability_version, backend=backend,
        candidate=candidate, exports=exports,
        op_stream=OpStream(schema_version='mirum_op_stream_v0_1', entries=entries))


def check_plan(description: BackendDescription, plan: BuildPlan) -> SupportDecision:
    """Pure support check, first failing step wins (contract order 1..7)."""
    def no(reason: str, code: str | None = None) -> SupportDecision:
        return SupportDecision(backend=description.backend, mode=description.mode, supported=False,
                               reasons=(reason,), diagnostic_code=code)

    if plan.backend != description.backend:
        return no('backend_mismatch')
    registry = registry_for_capability(plan.capability_id)
    try:
        validate_op_stream(plan.op_stream, registry, require_executable=True,
                           field_prefix='op_stream')
    except ProgramError as e:
        return no('operation_not_executable' if e.code == 'IR009' else 'operation_not_supported', e.code)
    key = f'{plan.capability_id}@{plan.capability_version}'
    if key not in description.capabilities:
        return no('capability_not_supported_by_backend')
    for e in plan.op_stream.entries:
        try:
            d = registry.get(e.operation, e.operation_version)
        except ProgramError as err:
            return no('operation_not_supported', err.code)
        if key not in d.capability_versions:
            return no('capability_not_supported_by_backend')
    if any(x not in description.export_formats for x in plan.exports):
        return no('export_not_supported')
    expected = canonical_entries(plan.capability_id, plan.exports)
    actual = tuple((e.operation, e.operation_version, e.step_ref) for e in plan.op_stream.entries)
    if actual != tuple((op, ver, None) for op, ver in expected):
        return no('plan_invalid')
    c = plan.candidate
    try:
        if plan.capability_id in ('regen_chamber', 'equipment_bay', 'editable_plate', 'thermal_bar'):
            ok = (isinstance(c, GeneratorCandidate)
                  and (c.content.capability.id, c.content.capability.version)
                  == (plan.capability_id, plan.capability_version))
        elif plan.capability_id == 'rectangular_cantilever':
            ok = (isinstance(c, CandidateSpec) and c.content.capability.id == plan.capability_id
                  and c.content.capability.version == plan.capability_version)
        else:
            ok = False
    except ValueError:
        ok = False
    if not ok:
        return no('candidate_invalid')
    return SupportDecision(backend=description.backend, mode=description.mode, supported=True)
