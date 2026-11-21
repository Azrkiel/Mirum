"""Typed operation registry, construction validation and D7 records (F02 T2).

Capability operations are executable (beam only in v0.1); construction operations are
reserved: typed and validated but never executable.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from .errors import IRModel, ProgramError, SourceSpan, Str240
from .ir import (
    MAX_LIST, ConstructionGraph, Name64, Name128, QuantityValue, SemanticId, TextValue, Value,
    _dumps_strict, _map_error, _parse_json_bounded,
)
from .units import Dimension
from .versions import ARTIFACT_GRAPH_VERSION, REGISTRY_VERSION

__all__ = [
    'ArgSpec', 'OutputSpec', 'OperationDescriptor', 'OperationRegistry', 'DEFAULT_REGISTRY',
    'OpStreamEntry', 'OpStream', 'ArtifactNode', 'ArtifactGraph', 'validate_op_stream',
    'load_artifact_graph_json',
]

Representation = Literal['brep', 'mesh', 'voxel', 'report', 'file']


class ArgSpec(IRModel):
    name: Name64
    kind: Literal['quantity', 'text', 'reference']
    dimension: Dimension | None = None
    required: bool = True
    allowed: Annotated[tuple[Str240, ...], Field(max_length=MAX_LIST)] = ()

    @model_validator(mode='after')
    def _dimension_rule(self):
        if (self.kind == 'quantity') != (self.dimension is not None):
            raise ProgramError('IR010', 'dimension is required iff kind is quantity', 'dimension')
        return self


class OutputSpec(IRModel):
    name: Name64
    type: Literal['solid', 'sketch', 'artifact', 'measurement', 'report', 'file']


class OperationDescriptor(IRModel):
    id: Name128
    version: Name64
    summary: Str240
    args: Annotated[tuple[ArgSpec, ...], Field(max_length=MAX_LIST)]
    preconditions: Annotated[tuple[Str240, ...], Field(max_length=MAX_LIST)]
    outputs: Annotated[tuple[OutputSpec, ...], Field(max_length=MAX_LIST)]
    representations: tuple[Representation, ...]
    backend_requirements: Annotated[tuple[Str240, ...], Field(max_length=MAX_LIST)]
    capability_versions: Annotated[tuple[Str240, ...], Field(max_length=MAX_LIST)]
    executable: bool

    @model_validator(mode='after')
    def _unique_args(self):
        seen: set[str] = set()
        for i, a in enumerate(self.args):
            if a.name in seen:
                raise ProgramError('IR011', f'duplicate argument {a.name!r}', f'args.{i}.name')
            seen.add(a.name)
        return self


def _path(base: str | None, tail: str) -> str:
    return f'{base}.{tail}' if base else tail


class OperationRegistry:
    version = REGISTRY_VERSION

    def __init__(self, descriptors):
        self._by_key: dict[tuple[str, str], OperationDescriptor] = {}
        for d in descriptors:
            key = (d.id, d.version)
            if key in self._by_key:
                raise ValueError(f'duplicate operation {d.id}@{d.version}')
            self._by_key[key] = d

    def descriptors(self) -> tuple[OperationDescriptor, ...]:
        return tuple(self._by_key[k] for k in sorted(self._by_key))

    def get(self, op_id: str, version: str, *, field: str | None = None,
            span: SourceSpan | None = None) -> OperationDescriptor:
        d = self._by_key.get((op_id, version))
        if d is not None:
            return d
        if any(k[0] == op_id for k in self._by_key):
            raise ProgramError('IR001', f'unknown version {version!r} of operation {op_id!r}', field, span)
        raise ProgramError('IR008', f'unknown operation {op_id!r}', field, span)

    def require_executable(self, op_id: str, version: str, *, field: str | None = None,
                           span: SourceSpan | None = None) -> OperationDescriptor:
        d = self.get(op_id, version, field=field, span=span)
        if not d.executable:
            raise ProgramError('IR009', f'operation {op_id}@{version} is reserved and not executable',
                               field, span)
        return d

    def check_call(self, op_id: str, version: str, args: dict[str, Value], *,
                   field: str | None = None, span: SourceSpan | None = None) -> OperationDescriptor:
        d = self.get(op_id, version, span=span, field=field)
        base = field or 'args'
        specs = {a.name: a for a in d.args}
        for name in args:
            if name not in specs:
                raise ProgramError('IR002', f'operation {op_id} has no argument {name!r}',
                                   _path(base, name), span)
        for spec in d.args:
            if spec.name not in args:
                if spec.required:
                    raise ProgramError('IR010', f'missing required argument {spec.name!r}',
                                       _path(base, spec.name), span)
                continue
            val = args[spec.name]
            where = _path(base, spec.name)
            if spec.kind == 'quantity':
                if not isinstance(val, QuantityValue):
                    raise ProgramError('IR010', f'argument {spec.name!r} must be a {spec.dimension} quantity',
                                       where, span)
                if val.dimension != spec.dimension:
                    raise ProgramError('IR006', f'argument {spec.name!r} expects {spec.dimension}, '
                                       f'got {val.dimension}', where, span)
            else:
                if not isinstance(val, TextValue):
                    raise ProgramError('IR010', f'argument {spec.name!r} must be text', where, span)
                if spec.allowed and val.text not in spec.allowed:
                    raise ProgramError('IR010', f'argument {spec.name!r} must be one of '
                                       f'{list(spec.allowed)}', where, span)
        return d

    def validate_construction(self, graph: ConstructionGraph, *, require_executable: bool = False) -> None:
        seen: set[str] = set()
        for i, s in enumerate(graph.steps):
            at = f'steps.{i}'
            if s.id in seen:
                raise ProgramError('IR011', f'duplicate step id {s.id!r}', f'{at}.id', s.span)
            if require_executable:
                self.require_executable(s.operation, s.operation_version, field=f'{at}.operation', span=s.span)
            else:
                self.get(s.operation, s.operation_version, field=f'{at}.operation', span=s.span)
            for j, ref in enumerate(s.inputs):
                if ref not in seen:
                    raise ProgramError('IR004', f'step {s.id!r} input {ref!r} is not an earlier step',
                                       f'{at}.inputs.{j}', s.span)
            d = self.check_call(s.operation, s.operation_version, s.args, field=f'{at}.args', span=s.span)
            for spec in d.args:
                val = s.args.get(spec.name)
                if spec.kind == 'reference' and val is not None and val.text not in seen:
                    raise ProgramError('IR004', f'step {s.id!r} argument {spec.name!r} references '
                                       f'{val.text!r}, which is not an earlier step',
                                       f'{at}.args.{spec.name}', s.span)
            seen.add(s.id)


# ------------------------------------------------------------------ default content

_CAP = ('rectangular_cantilever@1.0.0',)


def _cap(op: str, summary: str, args, pre, out, rep) -> OperationDescriptor:
    return OperationDescriptor(
        id=f'capability.{op}', version='0.1.0', summary=summary, args=args, preconditions=pre,
        outputs=out, representations=rep, backend_requirements=('capability_runtime',),
        capability_versions=_CAP, executable=True)


def _res(op: str, summary: str, args, pre, out, rep=('brep',)) -> OperationDescriptor:
    return OperationDescriptor(
        id=f'construct.{op}', version='0.1.0', summary=summary, args=args, preconditions=pre,
        outputs=out, representations=rep, backend_requirements=('occt',),
        capability_versions=(), executable=False)


def _q(name, dim, required=True):
    return ArgSpec(name=name, kind='quantity', dimension=dim, required=required)


def _ref(name, required=True):
    return ArgSpec(name=name, kind='reference', required=required)


_SOLID = (OutputSpec(name='solid', type='solid'),)

_DESCRIPTORS = (
    _cap('build', 'Build the geometry of a validated capability design.', (),
         ('program validated against the capability',),
         (OutputSpec(name='geometry', type='solid'),), ('brep', 'mesh')),
    _cap('inspect', 'Inspect built geometry and report measurements.', (),
         ('capability.build completed',),
         (OutputSpec(name='measurements', type='measurement'),), ('report',)),
    _cap('analyze', 'Run a required analysis check.',
         (ArgSpec(name='check', kind='text', required=False),),
         ('capability.build completed',),
         (OutputSpec(name='analysis', type='report'),), ('report',)),
    _cap('export', 'Export built geometry to a file format.',
         (ArgSpec(name='format', kind='text', allowed=('step', 'glb')),),
         ('capability.build completed',),
         (OutputSpec(name='file', type='file'),), ('file',)),
    _res('sketch', 'Create a planar sketch on a datum plane.',
         (ArgSpec(name='plane', kind='text', required=False, allowed=('xy', 'yz', 'xz')),),
         ('a datum plane exists',), (OutputSpec(name='sketch', type='sketch'),)),
    _res('extrude', 'Extrude a sketch along its normal.',
         (_q('distance', 'length'),), ('input is a closed sketch',), _SOLID),
    _res('revolve', 'Revolve a sketch about an axis.',
         (_q('angle', 'angle'),), ('input is a closed sketch', 'axis does not cross the sketch'), _SOLID),
    _res('sweep', 'Sweep a profile along a path.',
         (_ref('profile'), _ref('path')), ('profile is a closed sketch', 'path is a connected curve'), _SOLID),
    _res('loft', 'Loft through an ordered list of profiles.',
         (_ref('profiles'),), ('at least two compatible profiles',), _SOLID),
    _res('fillet', 'Round selected edges of a solid.',
         (_q('radius', 'length'), _ref('edges', False)), ('input is a solid', 'radius fits the edge geometry'),
         _SOLID),
    _res('chamfer', 'Bevel selected edges of a solid.',
         (_q('distance', 'length'), _ref('edges', False)), ('input is a solid', 'distance fits the edge geometry'),
         _SOLID),
    _res('shell', 'Hollow a solid to a wall thickness.',
         (_q('thickness', 'length'), _ref('faces', False)), ('input is a solid', 'thickness less than half the minimum feature size'),
         _SOLID),
    _res('pattern', 'Repeat a feature at a fixed spacing.',
         (_q('count', 'dimensionless'), _q('spacing', 'length')), ('input is a solid or feature', 'count is at least 1'),
         _SOLID),
    _res('boolean', 'Combine two solids.',
         (ArgSpec(name='operation', kind='text', allowed=('union', 'difference', 'intersection')),
          _ref('tool', False)),
         ('two solids are available',), _SOLID),
)

DEFAULT_REGISTRY = OperationRegistry(_DESCRIPTORS)


# ------------------------------------------------------------------ D7 records

class OpStreamEntry(IRModel):
    seq: Annotated[int, Field(ge=0)]
    operation: Name128
    operation_version: Name64
    step_ref: SemanticId | None = None
    span: SourceSpan | None = None


class OpStream(IRModel):
    schema_version: Literal['mirum_op_stream_v0_1']
    entries: Annotated[tuple[OpStreamEntry, ...], Field(max_length=MAX_LIST)]

    @model_validator(mode='after')
    def _seq(self):
        for i, e in enumerate(self.entries):
            if e.seq != i:
                raise ProgramError('IR010', f'op stream seq must equal its index (got {e.seq} at {i})',
                                   f'entries.{i}.seq', e.span)
        return self


class ArtifactNode(IRModel):
    id: SemanticId
    kind: Literal['solid', 'face', 'edge']
    op_seq: Annotated[int, Field(ge=0)]
    span: SourceSpan | None = None


class ArtifactGraph(IRModel):
    schema_version: Literal['mirum_artifact_graph_v0_1']
    op_stream: OpStream
    nodes: Annotated[tuple[ArtifactNode, ...], Field(max_length=MAX_LIST)]

    @model_validator(mode='after')
    def _cross(self):
        seen: set[str] = set()
        count = len(self.op_stream.entries)
        for i, n in enumerate(self.nodes):
            if n.id in seen:
                raise ProgramError('IR011', f'duplicate node id {n.id!r}', f'nodes.{i}.id', n.span)
            seen.add(n.id)
            if n.op_seq >= count:
                raise ProgramError('IR004', f'node {n.id!r} references op seq {n.op_seq} not in the op stream',
                                   f'nodes.{i}.op_seq', n.span)
        return self


def validate_op_stream(stream: OpStream, registry: OperationRegistry = DEFAULT_REGISTRY, *,
                       require_executable: bool = False, field_prefix: str | None = None) -> None:
    for i, e in enumerate(stream.entries):
        where = _path(field_prefix, f'entries.{i}.operation')
        if require_executable:
            registry.require_executable(e.operation, e.operation_version, field=where, span=e.span)
        else:
            registry.get(e.operation, e.operation_version, field=where, span=e.span)


def load_artifact_graph_json(text: str | bytes, *, require_executable: bool = False,
                             registry: OperationRegistry | None = None) -> ArtifactGraph:
    """Strict, budgeted load. Operations are always validated against the registry (unknown operation
    or version is rejected); reserved `construct.*` operations load as records unless
    `require_executable` is set, which is what any execution-facing caller must pass."""
    obj = _parse_json_bounded(text)
    if not isinstance(obj, dict) or obj.get('schema_version') != ARTIFACT_GRAPH_VERSION:
        raise ProgramError('IR001', f'unknown or missing schema_version (expected {ARTIFACT_GRAPH_VERSION!r})',
                           'schema_version')
    try:
        graph = ArtifactGraph.model_validate_json(_dumps_strict(obj))
    except ValidationError as exc:
        raise _map_error(exc.errors(include_url=False), obj) from None
    validate_op_stream(graph.op_stream, registry or DEFAULT_REGISTRY, require_executable=require_executable,
                       field_prefix='op_stream')
    return graph
