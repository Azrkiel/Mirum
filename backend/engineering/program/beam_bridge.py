"""Lossless bridge between the reviewed beam BriefContent and ProgramIR (F02 T4, D5).

No new digest rule: brief_sha256 and fixed_requirements_sha256 are computed by the
existing contracts code on the BriefContent this module rebuilds.
"""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from engineering.capabilities import CAPABILITY
from engineering.contracts import BriefContent

from .errors import ProgramError
from .ir import (
    AnalysisPlanRef, CandidateParameter, CandidateSpace, CapabilityRef, ConstructionGraph,
    EvidenceExpectation, ExecutionSelection, ProgramIR, ProgramProvenance, QuantityValue,
    Requirement, RequirementPlan, TextValue,
)
from .units import normalize
from .versions import CONSTRUCTION_GRAPH_VERSION, PROGRAM_IR_VERSION

__all__ = ['beam_capability_ref', 'program_from_brief_content', 'brief_content_from_program']

HEIGHT_ID = 'beam.height'
BEAM_BACKEND = 'occt'
BEAM_EXPORTS = ('step', 'glb')  # the geometry artifacts the beam path produces (model.step, preview.glb)
# (requirement id, kind/dimension)
_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ('beam.length', 'length'),
    ('beam.width', 'length'),
    ('beam.dimensional_tolerance', 'length'),
    ('load.tip_force', 'force'),
    ('limit.max_tip_deflection', 'length'),
    ('material.youngs_modulus', 'pressure'),
    ('material.allowable_stress', 'pressure'),
    ('material.reference', 'text'),
    ('material.source_kind', 'text'),
    ('model.assumptions', 'text'),
)
_UNIT = {'length': 'mm', 'force': 'N', 'pressure': 'MPa'}


def beam_capability_ref() -> CapabilityRef:
    return CapabilityRef(
        id=CAPABILITY.id, version=CAPABILITY.version,
        numerical_policy_version=CAPABILITY.numerical.version,
        resource_policy_version=CAPABILITY.resources.version)


def _suffix(check: str) -> str:
    return re.sub(r'[^a-z0-9_]', '_', check.lower())


def program_from_brief_content(content: BriefContent, *, provenance: ProgramProvenance,
                               design: str = 'reference_beam', backend: str = BEAM_BACKEND,
                               exports: tuple[str, ...] = BEAM_EXPORTS) -> ProgramIR:
    req = content.requirements
    mat = req.material
    raw: dict[str, float | str] = {
        'beam.length': req.length_mm,
        'beam.width': req.width_mm,
        'beam.dimensional_tolerance': req.dimensional_tolerance_mm,
        'load.tip_force': req.tip_force_n,
        'limit.max_tip_deflection': req.max_tip_deflection_mm,
        'material.youngs_modulus': mat.youngs_modulus_mpa,
        'material.allowable_stress': mat.allowable_stress_mpa,
        'material.reference': mat.reference,
        'material.source_kind': mat.source_kind,  # frozen identity translation
        'model.assumptions': req.assumptions,
    }
    requirements = []
    for rid, kind in _REQUIREMENTS:
        if kind == 'text':
            value = TextValue(kind='text', text=raw[rid])
        else:
            value = normalize(raw[rid], _UNIT[kind], kind, field=rid)
        requirements.append(Requirement(id=rid, value=value))
    ids = tuple(rid for rid, _ in _REQUIREMENTS)
    analysis = tuple(
        AnalysisPlanRef(id='check.' + _suffix(c), check=c, requirement_refs=(*ids, HEIGHT_ID))
        for c in content.required_checks)
    evidence = tuple(
        EvidenceExpectation(id='evidence.' + _suffix(c), analysis_ref='check.' + _suffix(c), required=True)
        for c in content.required_checks)
    heights = tuple(normalize(h, 'mm', 'length', field=HEIGHT_ID) for h in content.candidate_heights_mm)
    return ProgramIR(
        schema_version=PROGRAM_IR_VERSION,
        design=design,
        capability=beam_capability_ref(),
        requirements=RequirementPlan(requirements=tuple(requirements)),
        candidates=CandidateSpace(mode=content.mode, parameters=(
            CandidateParameter(id=HEIGHT_ID, values=heights),)),
        analysis=analysis,
        construction=ConstructionGraph(schema_version=CONSTRUCTION_GRAPH_VERSION, steps=()),
        evidence=evidence,
        execution=ExecutionSelection(backend=backend, exports=tuple(exports)),
        provenance=provenance,
    )


def _number(req: Requirement, dimension: str) -> float:
    value = req.value
    if not isinstance(value, QuantityValue):
        raise ProgramError('IR010', f'requirement {req.id!r} must be a quantity', req.id, req.span)
    if value.dimension != dimension:
        raise ProgramError('IR006', f'requirement {req.id!r} has dimension {value.dimension}, '
                           f'expected {dimension}', req.id, req.span)
    return value.value


def _text(req: Requirement) -> str:
    if not isinstance(req.value, TextValue):
        raise ProgramError('IR010', f'requirement {req.id!r} must be text', req.id, req.span)
    return req.value.text


def brief_content_from_program(ir: ProgramIR) -> BriefContent:
    cap = ir.capability
    if (cap.id, cap.version) != (CAPABILITY.id, CAPABILITY.version):
        raise ProgramError('IR010', f'capability {cap.id}@{cap.version} is not the beam capability',
                           'capability.id')
    known = {rid for rid, _ in _REQUIREMENTS}
    by_id = {r.id: r for r in ir.requirements.requirements}
    for r in ir.requirements.requirements:
        if r.id not in known:
            raise ProgramError('IR002', f'requirement {r.id!r} is not a beam requirement', r.id, r.span)
    for rid, _ in _REQUIREMENTS:
        if rid not in by_id:
            raise ProgramError('IR004', f'missing requirement {rid!r}', rid)
    params = {p.id: p for p in ir.candidates.parameters}
    for p in ir.candidates.parameters:
        if p.id != HEIGHT_ID:
            raise ProgramError('IR002', f'candidate parameter {p.id!r} is not a beam parameter', p.id, p.span)
    if HEIGHT_ID not in params:
        raise ProgramError('IR004', f'missing candidate parameter {HEIGHT_ID!r}', HEIGHT_ID)
    height_param = params[HEIGHT_ID]
    heights = []
    for v in height_param.values:
        if not isinstance(v, QuantityValue):
            raise ProgramError('IR010', 'candidate height must be a quantity', HEIGHT_ID, height_param.span)
        if v.dimension != 'length':
            raise ProgramError('IR006', f'candidate height has dimension {v.dimension}, expected length',
                               HEIGHT_ID, height_param.span)
        heights.append(v.value)
    checks = [a.check for a in ir.analysis]
    if len(checks) != len(CAPABILITY.required_checks) or set(checks) != set(CAPABILITY.required_checks):
        raise ProgramError('IR010', 'analysis must list every required beam check exactly once', 'analysis')
    # The legacy brief cannot carry any of the following; anything but the bridge's canonical mapping
    # is rejected rather than silently dropped. List order is not semantic (hashes sort), so it is
    # normalized by the bridge; names, references, flags and execution are not.
    canonical_refs = sorted((*(rid for rid, _ in _REQUIREMENTS), HEIGHT_ID))
    for i, a in enumerate(ir.analysis):
        if a.id != 'check.' + _suffix(a.check):
            raise ProgramError('IR010', f'analysis id {a.id!r} is not the canonical id for check {a.check!r}',
                               f'analysis.{i}.id', a.span)
        if sorted(a.requirement_refs) != canonical_refs:
            raise ProgramError('IR010', 'analysis requirement_refs differ from the bridge mapping, which the '
                               'legacy brief cannot represent', f'analysis.{i}.requirement_refs', a.span)
    expected_evidence = {'evidence.' + _suffix(c): 'check.' + _suffix(c) for c in checks}
    for i, e in enumerate(ir.evidence):
        if e.id not in expected_evidence:
            raise ProgramError('IR010', f'evidence id {e.id!r} is not a canonical beam evidence id',
                               f'evidence.{i}.id', e.span)
        if e.analysis_ref != expected_evidence[e.id]:
            raise ProgramError('IR010', f'evidence {e.id!r} does not reference its canonical analysis',
                               f'evidence.{i}.analysis_ref', e.span)
        if not e.required:
            raise ProgramError('IR010', 'evidence.required must be true: the legacy brief requires every '
                               'check', f'evidence.{i}.required', e.span)
    if len(ir.evidence) != len(expected_evidence):
        raise ProgramError('IR010', 'evidence must list every required beam check exactly once', 'evidence')
    if ir.execution.backend != BEAM_BACKEND:
        raise ProgramError('IR010', f'backend {ir.execution.backend!r} is not the beam backend '
                           f'{BEAM_BACKEND!r}', 'execution.backend')
    if tuple(ir.execution.exports) != BEAM_EXPORTS:
        raise ProgramError('IR010', f'exports {list(ir.execution.exports)} differ from the beam path exports '
                           f'{list(BEAM_EXPORTS)}', 'execution.exports')

    num = {rid: _number(by_id[rid], dim) for rid, dim in _REQUIREMENTS if dim != 'text'}
    payload = {
        'schema_version': 'engineering_brief_v1',
        'capability': {
            'id': cap.id, 'version': cap.version,
            'numerical_policy_version': cap.numerical_policy_version,
            'resource_policy_version': cap.resource_policy_version,
        },
        'requirements': {
            'units': 'mm_N_MPa',
            'length_mm': num['beam.length'],
            'width_mm': num['beam.width'],
            'dimensional_tolerance_mm': num['beam.dimensional_tolerance'],
            'tip_force_n': num['load.tip_force'],
            'max_tip_deflection_mm': num['limit.max_tip_deflection'],
            'material': {
                'youngs_modulus_mpa': num['material.youngs_modulus'],
                'allowable_stress_mpa': num['material.allowable_stress'],
                'source_kind': _text(by_id['material.source_kind']),
                'reference': _text(by_id['material.reference']),
            },
            'assumptions': _text(by_id['model.assumptions']),
        },
        'mode': ir.candidates.mode,
        'candidate_heights_mm': heights,
        'required_checks': checks,
    }
    try:
        return BriefContent.model_validate_json(json.dumps(payload, allow_nan=False))
    except ValidationError as exc:
        err = exc.errors(include_input=False, include_url=False)[0]
        field = '.'.join(str(p) for p in err['loc']) or 'requirements'
        raise ProgramError('IR010', err['msg'][:240], field) from None
