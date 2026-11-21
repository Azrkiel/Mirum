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
                               design: str = 'reference_beam', backend: str = 'occt',
                               exports: tuple[str, ...] = ('step', 'glb')) -> ProgramIR:
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
