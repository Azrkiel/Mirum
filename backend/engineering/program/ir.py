"""ProgramIR records and the strict loader (F02, D1).

Records accept reserved (non-executable) operations; any executor must call
``registry.DEFAULT_REGISTRY.require_executable`` itself before running an operation (K30).
"""
from __future__ import annotations

import json
import math
import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, Field, StringConstraints, ValidationError, field_validator, model_validator,
)

from .errors import MAX_POSITION, IRModel, ProgramError, SourceSpan, Str240
from .units import CANONICAL_UNIT, Dimension
from .versions import (
    CONSTRUCTION_GRAPH_VERSION, EXECUTION_LOCK_VERSION, EXECUTION_LOCK_VERSION_V0_2,
    MEL_COMPILER_VERSION, MEL_LANGUAGE_VERSION, PROGRAM_IR_VERSION, REGISTRY_VERSION,
    SEMANTICS_VERSION,
)

__all__ = [
    'SourceSpan', 'SemanticId', 'QuantityValue', 'TextValue', 'Value', 'Requirement',
    'RequirementPlan', 'CandidateParameter', 'CandidateSpace', 'AnalysisPlanRef',
    'EvidenceExpectation', 'ConstructionStep', 'ConstructionGraph', 'CapabilityRef',
    'ExecutionSelection', 'ProgramProvenance', 'ProgramIR', 'ExecutionLock',
    'execution_lock', 'execution_lock_v0_2', 'load_execution_lock_json', 'load_program_json', 'program_from_dict', 'MAX_POSITION',
]

def _semantic_id_length(value: str) -> str:
    # An identifier-format limit, not a budget: IR010 (budget-bounded lists and free strings are IR012).
    if len(value) > 128:
        raise ProgramError('IR010', 'identifier is longer than 128 characters')
    return value


SemanticId = Annotated[
    str, StringConstraints(pattern=r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$'),
    AfterValidator(_semantic_id_length), Field(json_schema_extra={'maxLength': 128})]
Identifier = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
Name128 = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Name64 = Annotated[str, StringConstraints(min_length=1, max_length=64)]

# Loader budgets (FRAMEWORK.md v0.1).
MAX_SOURCE_BYTES = 64 * 1024
MAX_DEPTH = 16
MAX_NODES = 2000
MAX_LIST = 256
MAX_ARGS = 64

# Symbolic backend and export selectors. The compiler and reviewed plans apply the narrower
# capability-specific format policies.
Backend = Literal['occt', 'picogk']
ExportFormat = Literal['step', 'glb', 'stl', 'source', 'brief', 'specification', 'attempts',
                       'report_json', 'report_markdown', 'manifest', 'zip']


class QuantityValue(IRModel):
    """A physical number in its canonical unit. Build through units.normalize."""
    kind: Literal['quantity']
    dimension: Dimension
    value: float
    unit: str

    @field_validator('value')
    @classmethod
    def _finite_zero(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ProgramError('IR007', 'quantity value is not finite')
        return 0.0 if v == 0 else v

    @model_validator(mode='after')
    def _canonical(self):
        if self.dimension == 'temperature' and self.value < 0:
            raise ProgramError('IR007', 'absolute temperature is below absolute zero', 'value')
        if self.unit != CANONICAL_UNIT[self.dimension]:
            raise ProgramError(
                'IR006', f'unit {self.unit!r} is not the canonical {self.dimension} unit '
                f'{CANONICAL_UNIT[self.dimension]!r}', 'unit')
        return self


class TextValue(IRModel):
    kind: Literal['text']
    # Same bound as the legacy contracts Text; edge whitespace is rejected (not stripped) so IR text
    # equals legacy text exactly.
    text: Annotated[str, StringConstraints(min_length=1, max_length=240)]

    @field_validator('text')
    @classmethod
    def _no_edge_whitespace(cls, v: str) -> str:
        if v != v.strip():
            raise ProgramError('IR010', 'text must not have leading or trailing whitespace', 'text')
        return v


Value = Annotated[QuantityValue | TextValue, Field(discriminator='kind')]


class Requirement(IRModel):
    id: SemanticId
    value: Value
    span: SourceSpan | None = None


class RequirementPlan(IRModel):
    requirements: Annotated[tuple[Requirement, ...], Field(min_length=1, max_length=256)]

    @model_validator(mode='after')
    def _unique(self):
        seen: set[str] = set()
        for i, r in enumerate(self.requirements):
            if r.id in seen:
                raise ProgramError('IR011', f'duplicate requirement id {r.id!r}', f'requirements.{i}.id', r.span)
            seen.add(r.id)
        return self


class CandidateParameter(IRModel):
    id: SemanticId
    values: Annotated[tuple[Value, ...], Field(min_length=1, max_length=256)]
    span: SourceSpan | None = None

    @model_validator(mode='after')
    def _unique_values(self):
        seen: set[tuple] = set()
        for i, v in enumerate(self.values):
            key = (v.kind, getattr(v, 'dimension', ''), getattr(v, 'value', 0.0), getattr(v, 'text', ''))
            if key in seen:
                raise ProgramError('IR010', f'duplicate candidate value in {self.id!r}', f'values.{i}', self.span)
            seen.add(key)
        return self


class CandidateSpace(IRModel):
    mode: Identifier
    parameters: Annotated[tuple[CandidateParameter, ...], Field(max_length=64)]
    span: SourceSpan | None = None

    @model_validator(mode='after')
    def _unique(self):
        seen: set[str] = set()
        for i, p in enumerate(self.parameters):
            if p.id in seen:
                raise ProgramError('IR011', f'duplicate candidate parameter id {p.id!r}',
                                   f'parameters.{i}.id', p.span)
            seen.add(p.id)
        return self


class AnalysisPlanRef(IRModel):
    id: SemanticId
    check: Name128
    requirement_refs: Annotated[tuple[SemanticId, ...], Field(max_length=MAX_LIST)]
    span: SourceSpan | None = None


class EvidenceExpectation(IRModel):
    id: SemanticId
    analysis_ref: SemanticId
    required: bool
    span: SourceSpan | None = None


class ConstructionStep(IRModel):
    """Record shape only; no execution in v0.1."""
    id: SemanticId
    operation: Name128
    operation_version: Name64
    args: Annotated[dict[Name64, Value], Field(max_length=MAX_ARGS)]
    inputs: Annotated[tuple[SemanticId, ...], Field(max_length=MAX_LIST)] = ()
    span: SourceSpan | None = None

    @model_validator(mode='before')
    @classmethod
    def _budgets(cls, data):
        return _prepare_input(data)


class ConstructionGraph(IRModel):
    schema_version: Literal['mirum_construction_graph_v0_1']
    steps: Annotated[tuple[ConstructionStep, ...], Field(max_length=MAX_LIST)] = ()


class CapabilityRef(IRModel):
    id: Name128
    version: Name128
    numerical_policy_version: Name128
    resource_policy_version: Name128


class ExecutionSelection(IRModel):
    backend: Backend
    exports: Annotated[tuple[ExportFormat, ...], Field(max_length=32)]

    @model_validator(mode='after')
    def _unique(self):
        if len(set(self.exports)) != len(self.exports):
            raise ProgramError('IR010', 'duplicate export', 'exports')
        return self


class ProgramProvenance(IRModel):
    kind: Literal['form', 'ai', 'language']
    source_sha256: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
    source_id: Str240 | None = None
    translated_from: Str240 | None = None


class ProgramIR(IRModel):
    schema_version: Literal['mirum_program_ir_v0_1']
    design: Identifier
    capability: CapabilityRef
    requirements: RequirementPlan
    candidates: CandidateSpace
    analysis: Annotated[tuple[AnalysisPlanRef, ...], Field(max_length=MAX_LIST)]
    construction: ConstructionGraph
    evidence: Annotated[tuple[EvidenceExpectation, ...], Field(max_length=MAX_LIST)]
    execution: ExecutionSelection
    provenance: ProgramProvenance
    span: SourceSpan | None = None

    @model_validator(mode='before')
    @classmethod
    def _budgets(cls, data):
        return _prepare_input(data)

    @classmethod
    def model_validate_json(cls, json_data, **kwargs):  # type: ignore[override]
        """Routed through the strict loader (byte cap, duplicate keys, non-finite, budgets)."""
        if kwargs:
            raise TypeError('ProgramIR.model_validate_json takes no options; use load_program_json')
        return load_program_json(json_data)

    @model_validator(mode='after')
    def _cross_checks(self):
        req_ids = {r.id for r in self.requirements.requirements}
        for i, p in enumerate(self.candidates.parameters):
            if p.id in req_ids:
                raise ProgramError('IR011', f'candidate parameter id {p.id!r} collides with a requirement id',
                                   f'candidates.parameters.{i}.id', p.span)
        known = req_ids | {p.id for p in self.candidates.parameters}
        analysis_ids: set[str] = set()
        for i, a in enumerate(self.analysis):
            if a.id in analysis_ids:
                raise ProgramError('IR011', f'duplicate analysis id {a.id!r}', f'analysis.{i}.id', a.span)
            analysis_ids.add(a.id)
            for j, ref in enumerate(a.requirement_refs):
                if ref not in known:
                    raise ProgramError('IR004', f'analysis {a.id!r} references unknown requirement {ref!r}',
                                       f'analysis.{i}.requirement_refs.{j}', a.span)
        evidence_ids: set[str] = set()
        for i, e in enumerate(self.evidence):
            if e.id in evidence_ids:
                raise ProgramError('IR011', f'duplicate evidence id {e.id!r}', f'evidence.{i}.id', e.span)
            evidence_ids.add(e.id)
            if e.analysis_ref not in analysis_ids:
                raise ProgramError('IR004', f'evidence {e.id!r} references unknown analysis {e.analysis_ref!r}',
                                   f'evidence.{i}.analysis_ref', e.span)
        if self.construction.steps:
            raise ProgramError('IR009', 'construction graphs are not executable in v0.1', 'construction.steps')
        return self


class ExecutionLock(IRModel):
    schema_version: Literal['mirum_execution_lock_v0_1']
    program_ir_version: Name128
    semantics_version: Name128
    registry_version: Name128
    capability_id: Name128
    capability_version: Name128
    numerical_policy_version: Name128
    resource_policy_version: Name128
    backend: Backend
    exports: Annotated[tuple[ExportFormat, ...], Field(max_length=32)]

    @model_validator(mode='before')
    @classmethod
    def _budgets(cls, data):
        return _prepare_input(data)


def execution_lock(ir: ProgramIR) -> ExecutionLock:
    return ExecutionLock(
        schema_version=EXECUTION_LOCK_VERSION,
        program_ir_version=ir.schema_version,
        semantics_version=SEMANTICS_VERSION,
        registry_version=REGISTRY_VERSION,
        capability_id=ir.capability.id,
        capability_version=ir.capability.version,
        numerical_policy_version=ir.capability.numerical_policy_version,
        resource_policy_version=ir.capability.resource_policy_version,
        backend=ir.execution.backend,
        exports=ir.execution.exports,
    )


# ---------------------------------------------------------------- loader

class _NonFinite:
    def __init__(self, literal: str):
        self.literal = literal


_TAGS = ('quantity', 'text')


def _clean_loc(loc) -> list:
    """Drop discriminated-union tag segments from a pydantic loc."""
    out: list = []
    stripped_after = -1  # index in `out` after which a tag was already dropped
    for el in loc:
        if el in _TAGS and out and stripped_after != len(out):
            prev = out[-1]
            prev2 = out[-2] if len(out) > 1 else None
            if prev == 'value' or (isinstance(prev, int) and prev2 == 'values') or prev2 == 'args':
                stripped_after = len(out)
                continue
        out.append(el)
    return out


def _join(parts) -> str | None:
    s = '.'.join(str(p) for p in parts)
    return s or None


def _walk_dups(node, path, dups):
    if isinstance(node, dict):
        if id(node) in dups:
            return _join(path + [dups[id(node)][1]])
        for k, v in node.items():
            r = _walk_dups(v, path + [k], dups)
            if r:
                return r
    elif isinstance(node, list):
        for i, v in enumerate(node):
            r = _walk_dups(v, path + [i], dups)
            if r:
                return r
    return None


def _walk_nonfinite(node, path):
    if isinstance(node, _NonFinite):
        return _join(path) or ''
    if isinstance(node, dict):
        for k, v in node.items():
            r = _walk_nonfinite(v, path + [k])
            if r is not None:
                return r
    elif isinstance(node, list):
        for i, v in enumerate(node):
            r = _walk_nonfinite(v, path + [i])
            if r is not None:
                return r
    return None


def _span_for(obj, path):
    node, found = obj, None
    try:
        for el in [None] + list(path):
            if el is not None:
                node = node[el]
            if isinstance(node, dict) and isinstance(node.get('span'), dict):
                found = node['span']
    except (KeyError, IndexError, TypeError):
        pass
    if found is None:
        return None
    try:
        return SourceSpan.model_validate_json(json.dumps(found))
    except (ValidationError, ValueError, TypeError):
        return None


_SURROGATE = re.compile('[\ud800-\udfff]')


def _check_budgets(obj) -> None:
    """Iterative depth / node / list-length / surrogate pre-pass (safe on any depth)."""
    stack = [(obj, 1)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise ProgramError('IR012', f'program exceeds the {MAX_NODES} node budget')
        if isinstance(node, dict):
            if depth > MAX_DEPTH:
                raise ProgramError('IR012', f'program exceeds the depth budget of {MAX_DEPTH}')
            nodes += len(node)  # keys count against the node budget too
            if nodes > MAX_NODES:
                raise ProgramError('IR012', f'program exceeds the {MAX_NODES} node budget')
            for k, v in node.items():
                if isinstance(k, str) and _SURROGATE.search(k):
                    raise ProgramError('IR010', 'object key contains a lone surrogate')
                stack.append((v, depth + 1))
        elif isinstance(node, (list, tuple)):
            if depth > MAX_DEPTH:
                raise ProgramError('IR012', f'program exceeds the depth budget of {MAX_DEPTH}')
            if len(node) > MAX_LIST:
                raise ProgramError('IR012', f'list exceeds the {MAX_LIST} element budget')
            stack.extend((v, depth + 1) for v in node)
        elif isinstance(node, str) and _SURROGATE.search(node):
            raise ProgramError('IR010', 'string contains a lone surrogate')


def _tuplify(node):
    # Depth is already bounded by _check_budgets. Arrays become tuples because the records are strict
    # (a function-before validator hands its result to the strict validator as Python data).
    if isinstance(node, dict):
        return {k: _tuplify(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return tuple(_tuplify(v) for v in node)
    return node


def _prepare_input(data):
    """`mode='before'` hook of every top-level record: budgets for every construction path."""
    if isinstance(data, dict):
        _check_budgets(data)
        return _tuplify(data)
    return data


def _has_nonfinite(obj) -> bool:
    """Iterative scan for non-finite floats; containers are visited once (cycles terminate)."""
    stack, seen = [obj], set()
    while stack:
        node = stack.pop()
        if isinstance(node, float):
            if not math.isfinite(node):
                return True
        elif isinstance(node, (dict, list, tuple)):
            if id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, dict):
                stack.extend(node.keys())  # a Python caller can use float keys
                stack.extend(node.values())
            else:
                stack.extend(node)
    return False


def _dumps_strict(obj) -> str:
    # Non-finite numbers are found from the values (K30), never from json's error wording.
    if _has_nonfinite(obj):
        raise ProgramError('IR007', 'non-finite number in program')
    try:
        return json.dumps(obj, allow_nan=False)
    except (ValueError, TypeError):
        # Circular references, unsupported types (and, defensively, any other json failure).
        raise ProgramError('IR010', 'program is not JSON-serializable') from None
    except RecursionError:
        raise ProgramError('IR012', 'program exceeds the depth budget') from None


def _parse_json_bounded(text: str | bytes):
    """Size-checked strict JSON parse shared by every loader: budgets run before any recursive walk."""
    if isinstance(text, str):
        too_big = len(text) > MAX_SOURCE_BYTES or len(text.encode('utf-8', 'surrogatepass')) > MAX_SOURCE_BYTES
    else:
        too_big = len(text) > MAX_SOURCE_BYTES
    if too_big:
        raise ProgramError('IR012', f'source exceeds the {MAX_SOURCE_BYTES} byte budget')
    dups: dict[int, tuple[dict, str]] = {}

    def hook(pairs):
        d: dict = {}
        dup_key = None
        for k, v in pairs:
            if k in d and dup_key is None:
                dup_key = k
            d[k] = v
        if dup_key is not None:
            dups[id(d)] = (d, dup_key)
        return d

    def parse_float(literal):
        value = float(literal)
        return value if math.isfinite(value) else _NonFinite(literal)

    try:
        obj = json.loads(text, object_pairs_hook=hook, parse_constant=_NonFinite, parse_float=parse_float)
    except RecursionError:
        raise ProgramError('IR012', 'source exceeds the depth budget') from None
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProgramError('IR010', f'invalid JSON: {exc}') from None
    _check_budgets(obj)
    if dups:
        raise ProgramError('IR003', 'duplicate object key', _walk_dups(obj, [], dups))
    bad = _walk_nonfinite(obj, [])
    if bad is not None:
        raise ProgramError('IR007', 'non-finite number literal', bad or None,
                           _span_for(obj, bad.split('.') if bad else []))
    return obj


def execution_lock_v0_2(ir: ProgramIR, backend_lock):
    """v0_2 lock for `ir` pinned to a native kernel.BackendLock (ValueError for a mock or non-lock).

    The record lives in kernel.py (it embeds BackendLock and kernel imports this module), so it is
    imported lazily here."""
    from .kernel import BackendLock, ExecutionLockV0_2
    from .registry import registry_for_capability
    if not isinstance(backend_lock, BackendLock):
        raise ValueError('backend_lock must be a BackendLock')
    if backend_lock.mode != 'native':
        raise ValueError('a mock backend lock cannot be part of an execution lock')
    base = execution_lock(ir)
    # A backend or export mismatch fails the record validators (pydantic ValidationError, a ValueError).
    return ExecutionLockV0_2(
        schema_version=EXECUTION_LOCK_VERSION_V0_2,
        program_ir_version=base.program_ir_version,
        semantics_version=base.semantics_version,
        registry_version=registry_for_capability(base.capability_id).version,
        capability_id=base.capability_id,
        capability_version=base.capability_version,
        numerical_policy_version=base.numerical_policy_version,
        resource_policy_version=base.resource_policy_version,
        backend=base.backend,
        exports=base.exports,
        language_version=MEL_LANGUAGE_VERSION,
        compiler_version=MEL_COMPILER_VERSION,
        backend_lock=backend_lock)


def load_execution_lock_json(text: str | bytes):
    """Strict versioned lock loader: ExecutionLock (v0_1) or kernel.ExecutionLockV0_2 (v0_2)."""
    from .kernel import ExecutionLockV0_2
    obj = _parse_json_bounded(text)
    version = obj.get('schema_version') if isinstance(obj, dict) else None
    if version == EXECUTION_LOCK_VERSION_V0_2:
        return _validate_record(ExecutionLockV0_2, obj, EXECUTION_LOCK_VERSION_V0_2)
    return _validate_record(ExecutionLock, obj, EXECUTION_LOCK_VERSION)


def load_program_json(text: str | bytes) -> ProgramIR:
    return _validate(_parse_json_bounded(text))


def program_from_dict(obj) -> ProgramIR:
    _check_budgets(obj)
    if isinstance(obj, dict):
        _dumps_strict(obj)
    return _validate(obj)


def _validate_record(cls, obj, version: str):
    """Validate an already strictly parsed object into `cls`, mapping failures to typed errors.

    Uses the pydantic validator directly: the public `model_validate_json` of the IR records is
    overridden to route through the strict loaders (which end up here)."""
    if not isinstance(obj, dict) or obj.get('schema_version') != version:
        raise ProgramError('IR001', f'unknown or missing schema_version (expected {version!r})',
                           'schema_version')
    try:
        return cls.__pydantic_validator__.validate_json(_dumps_strict(obj))
    except ValidationError as exc:
        raise _map_error(exc.errors(include_url=False), obj) from None


def _validate(obj) -> ProgramIR:
    return _validate_record(ProgramIR, obj, PROGRAM_IR_VERSION)


def _map_error(errors, obj) -> ProgramError:
    err = errors[0]
    loc = _clean_loc(err['loc'])
    etype = err['type']
    ctx_err = (err.get('ctx') or {}).get('error')
    if isinstance(ctx_err, ProgramError):
        parts = list(loc)
        if ctx_err.field:
            parts.append(ctx_err.field)
        span = ctx_err.span or _span_for(obj, list(loc))
        return ProgramError(ctx_err.code, ctx_err.message, _join(parts), span)
    msg = err.get('msg', 'invalid value')
    if etype == 'extra_forbidden':
        code = 'IR002'
    elif etype == 'finite_number':
        code = 'IR007'
    elif etype in ('too_long', 'string_too_long'):
        code = 'IR012'
    elif etype in ('literal_error', 'missing') and loc and loc[-1] == 'schema_version':
        code = 'IR001'
    else:
        code = 'IR010'
    return ProgramError(code, msg, _join(loc), _span_for(obj, list(loc)))
