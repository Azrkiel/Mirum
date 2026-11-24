"""Versioned mapping between equipment-bay MEL/ProgramIR and reviewed generator content."""

from engineering.capabilities import EQUIPMENT_BAY
from engineering.contracts import CapabilityReference, GeneratorBriefContent
from engineering.equipment_bay import CANDIDATE_FIELDS, CHECKS, FIXED_FIELDS, catalog_digest

from .errors import ProgramError
from .hashing import semantic_sha256
from .ir import (
    AnalysisPlanRef, CandidateParameter, CandidateSpace, CapabilityRef, ConstructionGraph,
    EvidenceExpectation, ExecutionSelection, ProgramIR, Requirement, RequirementPlan, TextValue,
)
from .units import normalize
from .versions import CONSTRUCTION_GRAPH_VERSION, PROGRAM_IR_VERSION

FIELDS = {
    'length_mm': ('bay.length', 'length', 'mm'),
    'width_mm': ('bay.width', 'length', 'mm'),
    'module_count': ('bay.module_count', 'dimensionless', '1'),
    'preferred_angle_rad': ('bay.preferred_angle', 'angle', 'rad'),
    'supplier': ('bay.supplier', None, None),
}


def capability_reference():
    return CapabilityReference(
        id=EQUIPMENT_BAY.id, version=EQUIPMENT_BAY.version,
        numerical_policy_version=EQUIPMENT_BAY.numerical_version,
        resource_policy_version=EQUIPMENT_BAY.resources.version)


def content_for(specification):
    spec = EQUIPMENT_BAY.parse_spec(specification).model_dump(mode='json')
    return GeneratorBriefContent(
        schema_version='engineering_generator_brief_v1', capability=capability_reference(),
        specification=spec, required_checks=CHECKS)


def program_for(content, design, provenance, spans=None, source_span=None):
    spec = content.specification
    spans = spans or {}
    values = {}
    for key, (identifier, dimension, unit) in FIELDS.items():
        values[key] = (TextValue(kind='text', text=spec[key]) if dimension is None else
                       normalize(spec[key], unit, dimension, field=identifier))
    requirements = tuple(Requirement(id=FIELDS[key][0], value=values[key], span=spans.get(key))
                         for key in FIXED_FIELDS)
    requirements += (Requirement(id='bay.catalog', value=TextValue(kind='text', text=catalog_digest())),)
    candidates = tuple(CandidateParameter(id=FIELDS[key][0], values=(values[key],), span=spans.get(key))
                       for key in CANDIDATE_FIELDS)
    refs = tuple(item.id for item in (*requirements, *candidates))
    checks = tuple(AnalysisPlanRef(id='check.' + check, check=check, requirement_refs=refs) for check in CHECKS)
    return ProgramIR(
        schema_version=PROGRAM_IR_VERSION, design=design,
        capability=CapabilityRef.model_validate(content.capability.model_dump()),
        requirements=RequirementPlan(requirements=requirements),
        candidates=CandidateSpace(mode='bounded_layout', parameters=candidates),
        analysis=checks, construction=ConstructionGraph(schema_version=CONSTRUCTION_GRAPH_VERSION),
        evidence=tuple(EvidenceExpectation(id='evidence.' + check.check, analysis_ref=check.id, required=True)
                       for check in checks),
        execution=ExecutionSelection(backend='occt', exports=('step',)), provenance=provenance,
        span=source_span)


def content_from_program(ir):
    """Fail closed if the typed representation contains meaning the product cannot carry."""
    reqs = {item.id: item.value for item in ir.requirements.requirements}
    choices = {item.id: item.values for item in ir.candidates.parameters}
    try:
        raw = {name: reqs[FIELDS[name][0]].value for name in FIXED_FIELDS}
        count = raw['module_count']
        if not count.is_integer():
            raise ValueError('nonintegral count')
        raw['module_count'] = int(count)
        for name in CANDIDATE_FIELDS:
            (value,) = choices[FIELDS[name][0]]
            raw[name] = value.text if name == 'supplier' else value.value
        content = content_for(raw)
        if semantic_sha256(program_for(content, ir.design, ir.provenance)) != semantic_sha256(ir):
            raise ValueError('meaning differs')
    except (KeyError, AttributeError, ValueError, TypeError):
        raise ProgramError('IR010', 'equipment-bay IR differs from its versioned product contract', 'program') from None
    return content


def check_plan_record(semantic_sha, lock_sha, candidate):
    """The reviewed values the delivered verifier applies to the leased geometry."""
    limits = candidate.content.descriptor.expected_limits(candidate.content.specification)
    return {
        'reviewed_revision': semantic_sha, 'lock': lock_sha, 'candidate': candidate.candidate_sha256,
        'limits': {key: [row.model_dump(mode='json') for row in rows] for key, rows in limits.items()},
    }
