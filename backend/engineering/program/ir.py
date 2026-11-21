"""ProgramIR records and the strict loader (F02, D1)."""
from __future__ import annotations

import json
import math
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator

from .errors import IRModel, ProgramError, SourceSpan
from .units import CANONICAL_UNIT, Dimension
from .versions import (
    CONSTRUCTION_GRAPH_VERSION, EXECUTION_LOCK_VERSION, PROGRAM_IR_VERSION,
    REGISTRY_VERSION, SEMANTICS_VERSION,
)

__all__ = [
    'SourceSpan', 'SemanticId', 'QuantityValue', 'TextValue', 'Value', 'Requirement',
    'RequirementPlan', 'CandidateParameter', 'CandidateSpace', 'AnalysisPlanRef',
    'EvidenceExpectation', 'ConstructionStep', 'ConstructionGraph', 'CapabilityRef',
    'ExecutionSelection', 'ProgramProvenance', 'ProgramIR', 'ExecutionLock',
    'execution_lock', 'load_program_json', 'program_from_dict',
]

SemanticId = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$', max_length=128)]
Identifier = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
Name128 = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Name64 = Annotated[str, StringConstraints(min_length=1, max_length=64)]


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
        if self.unit != CANONICAL_UNIT[self.dimension]:
            raise ProgramError(
                'IR006', f'unit {self.unit!r} is not the canonical {self.dimension} unit '
                f'{CANONICAL_UNIT[self.dimension]!r}', 'unit')
        return self


class TextValue(IRModel):
    kind: Literal['text']
    text: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


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
    requirement_refs: tuple[SemanticId, ...]
    span: SourceSpan | None = None


class EvidenceExpectation(IRModel):
    id: SemanticId
    analysis_ref: SemanticId
    required: bool
    span: SourceSpan | None = None


class ConstructionStep(IRModel):
    """Record shape only; no execution in v0.1."""
    id: SemanticId
    operation: str
    operation_version: str
    args: dict[str, Value]
    inputs: tuple[SemanticId, ...] = ()
    span: SourceSpan | None = None


class ConstructionGraph(IRModel):
    schema_version: Literal['mirum_construction_graph_v0_1']
    steps: tuple[ConstructionStep, ...] = ()


class CapabilityRef(IRModel):
    id: Name128
    version: Name128
    numerical_policy_version: Name128
    resource_policy_version: Name128


class ExecutionSelection(IRModel):
    backend: Name64
    exports: Annotated[tuple[str, ...], Field(max_length=32)]

    @model_validator(mode='after')
    def _unique(self):
        if len(set(self.exports)) != len(self.exports):
            raise ProgramError('IR010', 'duplicate export', 'exports')
        return self


class ProgramProvenance(IRModel):
    kind: Literal['form', 'ai', 'language']
    source_sha256: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
    source_id: str | None = None
    translated_from: str | None = None


class ProgramIR(IRModel):
    schema_version: Literal['mirum_program_ir_v0_1']
    design: Identifier
    capability: CapabilityRef
    requirements: RequirementPlan
    candidates: CandidateSpace
    analysis: tuple[AnalysisPlanRef, ...]
    construction: ConstructionGraph
    evidence: tuple[EvidenceExpectation, ...]
    execution: ExecutionSelection
    provenance: ProgramProvenance
    span: SourceSpan | None = None

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
    program_ir_version: str
    semantics_version: str
    registry_version: str
    capability_id: str
    capability_version: str
    numerical_policy_version: str
    resource_policy_version: str
    backend: str
    exports: tuple[str, ...]


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


def load_program_json(text: str | bytes) -> ProgramIR:
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

    def constant(name):
        return _NonFinite(name)

    try:
        obj = json.loads(text, object_pairs_hook=hook, parse_constant=constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProgramError('IR010', f'invalid JSON: {exc}') from None
    if dups:
        path = _walk_dups(obj, [], dups)
        raise ProgramError('IR003', 'duplicate object key', path)
    bad = _walk_nonfinite(obj, [])
    if bad is not None:
        raise ProgramError('IR007', 'non-finite number literal', bad or None, _span_for(obj, bad.split('.') if bad else []))
    return _validate(obj)


def program_from_dict(obj) -> ProgramIR:
    if isinstance(obj, dict):
        try:
            json.dumps(obj, allow_nan=False)
        except ValueError:
            raise ProgramError('IR007', 'non-finite number in program') from None
        except TypeError as exc:
            raise ProgramError('IR010', f'program is not JSON-serializable: {exc}') from None
    return _validate(obj)


def _validate(obj) -> ProgramIR:
    if not isinstance(obj, dict) or obj.get('schema_version') != PROGRAM_IR_VERSION:
        raise ProgramError('IR001', f'unknown or missing schema_version (expected {PROGRAM_IR_VERSION!r})',
                           'schema_version')
    try:
        return ProgramIR.model_validate_json(json.dumps(obj, allow_nan=False))
    except ValidationError as exc:
        raise _map_error(exc.errors(include_url=False), obj) from None


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
    elif etype == 'literal_error' and loc and loc[-1] == 'schema_version':
        code = 'IR001'
    else:
        code = 'IR010'
    return ProgramError(code, msg, _join(loc), _span_for(obj, list(loc)))
