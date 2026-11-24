"""MEL bridge for the existing versioned PicoGK release reference."""

from engineering.capabilities import REGEN
from engineering.contracts import CapabilityReference, GeneratorBriefContent

from .errors import ProgramError
from .hashing import semantic_sha256
from .ir import (
    AnalysisPlanRef, CandidateParameter, CandidateSpace, CapabilityRef, ConstructionGraph,
    EvidenceExpectation, ExecutionSelection, ProgramIR, Requirement, RequirementPlan, TextValue,
)
from .versions import CONSTRUCTION_GRAPH_VERSION, PROGRAM_IR_VERSION

PRESET = 'reference'
PRESET_PARAM = 'regen.preset'
SPEC_REQUIREMENT = 'regen.catalog'


def capability_reference():
    return CapabilityReference(id=REGEN.id, version=REGEN.version,
                               numerical_policy_version=REGEN.numerical_version,
                               resource_policy_version=REGEN.resources.version)


def preset_content():
    from engineering.regen_presets import EXAMPLES
    spec = REGEN.parse_spec(EXAMPLES[PRESET]).model_dump(mode='json')
    return GeneratorBriefContent(
        schema_version='engineering_generator_brief_v1', capability=capability_reference(),
        specification=spec, required_checks=REGEN.required_checks(spec))


def program_for(content, design, provenance, *, preset_span=None, source_span=None):
    from .planning import generator_candidates

    candidate, = generator_candidates(content)
    checks = tuple(AnalysisPlanRef(id='check.' + check, check=check,
                                  requirement_refs=(SPEC_REQUIREMENT, PRESET_PARAM))
                   for check in content.required_checks)
    return ProgramIR(
        schema_version=PROGRAM_IR_VERSION, design=design,
        capability=CapabilityRef.model_validate(content.capability.model_dump()),
        requirements=RequirementPlan(requirements=(Requirement(
            id=SPEC_REQUIREMENT, value=TextValue(kind='text', text=candidate.candidate_sha256)),)),
        candidates=CandidateSpace(mode='preset', parameters=(CandidateParameter(
            id=PRESET_PARAM, values=(TextValue(kind='text', text=PRESET),), span=preset_span),)),
        analysis=checks, construction=ConstructionGraph(schema_version=CONSTRUCTION_GRAPH_VERSION),
        evidence=tuple(EvidenceExpectation(id='evidence.' + check.check, analysis_ref=check.id, required=True)
                       for check in checks),
        execution=ExecutionSelection(backend='picogk', exports=('stl',)), provenance=provenance,
        span=source_span)


def content_from_program(ir):
    content = preset_content()
    if semantic_sha256(program_for(content, ir.design, ir.provenance)) != semantic_sha256(ir):
        raise ProgramError('IR010', 'PicoGK IR differs from the registered release preset', 'program')
    return content
