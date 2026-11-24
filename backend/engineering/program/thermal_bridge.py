"""Round-trip the reviewed thermal generator brief and the typed ProgramIR."""

from engineering.capabilities import THERMAL_BAR
from engineering.contracts import CapabilityReference, GeneratorBriefContent
from engineering.thermal_bar import FIELDS

from .errors import ProgramError
from .hashing import semantic_sha256
from .ir import (
    AnalysisPlanRef, CandidateSpace, CapabilityRef, ConstructionGraph,
    EvidenceExpectation, ExecutionSelection, ProgramIR, Requirement, RequirementPlan, TextValue,
)
from .units import CANONICAL_UNIT, normalize
from .versions import CONSTRUCTION_GRAPH_VERSION, PROGRAM_IR_VERSION


def capability_reference():
    return CapabilityReference(id=THERMAL_BAR.id, version=THERMAL_BAR.version,
                               numerical_policy_version=THERMAL_BAR.numerical_version,
                               resource_policy_version=THERMAL_BAR.resources.version)


def content_for(specification):
    spec = THERMAL_BAR.parse_spec(specification).model_dump(mode='json')
    return GeneratorBriefContent(schema_version='engineering_generator_brief_v1',
                                 capability=capability_reference(), specification=spec,
                                 required_checks=THERMAL_BAR.base_checks)


def program_for(content, design, provenance, spans=None, source_span=None):
    spans = spans or {}
    requirements = []
    for name, (key, dimension) in FIELDS.items():
        value = content.specification[key]
        identifier = f'thermal.{name}'
        typed = (normalize(value, CANONICAL_UNIT[dimension], dimension, field=identifier)
                 if dimension else TextValue(kind='text', text=value))
        requirements.append(Requirement(id=identifier, value=typed, span=spans.get(key)))
    refs = tuple(row.id for row in requirements)
    checks = tuple(AnalysisPlanRef(id=f'check.{check}', check=check, requirement_refs=refs)
                   for check in content.required_checks)
    return ProgramIR(
        schema_version=PROGRAM_IR_VERSION, design=design,
        capability=CapabilityRef.model_validate(content.capability.model_dump()),
        requirements=RequirementPlan(requirements=tuple(requirements)),
        candidates=CandidateSpace(mode='fixed_thermal_bar', parameters=()),
        analysis=checks, construction=ConstructionGraph(schema_version=CONSTRUCTION_GRAPH_VERSION),
        evidence=tuple(EvidenceExpectation(id='evidence.' + row.check, analysis_ref=row.id, required=True)
                       for row in checks),
        execution=ExecutionSelection(backend='occt', exports=('step',)), provenance=provenance,
        span=source_span)


def content_from_program(ir):
    requirements = {row.id: row.value for row in ir.requirements.requirements}
    try:
        spec = {key: getattr(requirements[f'thermal.{name}'], 'value' if dimension else 'text')
                for name, (key, dimension) in FIELDS.items()}
        content = content_for(spec)
        if semantic_sha256(program_for(content, ir.design, ir.provenance)) != semantic_sha256(ir):
            raise ValueError('meaning differs')
    except (KeyError, AttributeError, TypeError, ValueError):
        raise ProgramError('IR010', 'thermal bar IR differs from its versioned contract', 'program') from None
    return content
