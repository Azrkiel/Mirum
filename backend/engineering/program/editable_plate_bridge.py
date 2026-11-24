"""Round-trip between editable plate MEL/ProgramIR and its reviewed generator brief."""

from engineering.capabilities import EDITABLE_PLATE
from engineering.contracts import CapabilityReference, GeneratorBriefContent
from engineering.editable_plate import CHECKS
from vendor.mirum_editable_plate import FRAME_ID, LEFT_ID, PART_ID, PATTERN_ID, RIGHT_ID

from .errors import ProgramError
from .hashing import semantic_sha256
from .ir import (
    AnalysisPlanRef, CandidateParameter, CandidateSpace, CapabilityRef, ConstructionGraph,
    EvidenceExpectation, ExecutionSelection, ProgramIR, Requirement, RequirementPlan, TextValue,
)
from .units import normalize
from .versions import CONSTRUCTION_GRAPH_VERSION, PROGRAM_IR_VERSION

# The side datums are planes at ±width/2 in the fixed lower-center frame.
LENGTHS = {'width_mm': 'plate.width', 'height_mm': 'plate.height', 'thickness_mm': 'plate.thickness'}
TEXT = {'intent': 'plate.bores.intent', 'datum_ref': 'plate.bores.datum_ref'}
AUTHORED = {'plate.part': PART_ID, 'plate.datum': FRAME_ID, 'plate.bores': PATTERN_ID,
            'plate.side.left': LEFT_ID, 'plate.side.right': RIGHT_ID}


def capability_reference():
    return CapabilityReference(
        id=EDITABLE_PLATE.id, version=EDITABLE_PLATE.version,
        numerical_policy_version=EDITABLE_PLATE.numerical_version,
        resource_policy_version=EDITABLE_PLATE.resources.version)


def content_for(specification):
    spec = EDITABLE_PLATE.parse_spec(specification).model_dump(mode='json')
    return GeneratorBriefContent(schema_version='engineering_generator_brief_v1',
                                 capability=capability_reference(), specification=spec, required_checks=CHECKS)


def program_for(content, design, provenance, spans=None, source_span=None):
    spec = content.specification
    spans = spans or {}
    requirements = [
        Requirement(id=identifier, value=normalize(spec[key], 'mm', 'length', field=identifier),
                    span=spans.get(key))
        for key, identifier in LENGTHS.items() if key != 'width_mm'
    ]
    requirements.extend(Requirement(id=identifier, value=TextValue(kind='text', text=spec[key]),
                                    span=spans.get(key)) for key, identifier in TEXT.items())
    requirements.extend(Requirement(id=identifier, value=TextValue(kind='text', text=value))
                        for identifier, value in AUTHORED.items())
    x_refs = f'{LEFT_ID} {RIGHT_ID}' if spec['intent'] == 'edge-driven' else spec['datum_ref']
    requirements.append(Requirement(id='plate.bores.x_refs', value=TextValue(kind='text', text=x_refs)))
    width = CandidateParameter(id=LENGTHS['width_mm'], span=spans.get('width_mm'),
                               values=(normalize(spec['width_mm'], 'mm', 'length', field='plate.width'),))
    refs = tuple(row.id for row in requirements) + (width.id,)
    checks = tuple(AnalysisPlanRef(id='check.' + check, check=check, requirement_refs=refs) for check in CHECKS)
    return ProgramIR(
        schema_version=PROGRAM_IR_VERSION, design=design,
        capability=CapabilityRef.model_validate(content.capability.model_dump()),
        requirements=RequirementPlan(requirements=tuple(requirements)),
        candidates=CandidateSpace(mode='width_edit', parameters=(width,)),
        analysis=checks, construction=ConstructionGraph(schema_version=CONSTRUCTION_GRAPH_VERSION),
        evidence=tuple(EvidenceExpectation(id='evidence.' + check.check, analysis_ref=check.id, required=True)
                       for check in checks),
        execution=ExecutionSelection(backend='occt', exports=('step',)), provenance=provenance,
        span=source_span)


def content_from_program(ir):
    requirements = {row.id: row.value for row in ir.requirements.requirements}
    try:
        (parameter,) = ir.candidates.parameters
        (width,) = parameter.values
        raw = {'width_mm': width.value,
               **{key: requirements[identifier].value for key, identifier in LENGTHS.items() if key != 'width_mm'},
               **{key: requirements[identifier].text for key, identifier in TEXT.items()}}
        content = content_for(raw)
        if semantic_sha256(program_for(content, ir.design, ir.provenance)) != semantic_sha256(ir):
            raise ValueError('meaning differs')
    except (KeyError, AttributeError, TypeError, ValueError):
        raise ProgramError('IR010', 'editable plate IR differs from its versioned contract', 'program') from None
    return content
