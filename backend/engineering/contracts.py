"""Strict V1 review and evidence records, independent of providers/native CAD.

Hashes bind normalized meaning/bytes, not authenticity or physical truth.
Service persistence, execution and actual-byte publication belong to consumers.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
import math
from typing import Annotated, Any, Literal

from pydantic import (AwareDatetime, ConfigDict, Field, StringConstraints, ValidationError,
                      field_validator, model_validator)

from cem.core import StrictModel, canonical_json, digest
from .capabilities import (CAPABILITY, GENERATOR_BASE_PAYLOADS, GENERATOR_PREFIX,
                           GENERATOR_SPEC_PREFIX, GeneratorDescriptor, get_capability, get_generator)
from .spec import CantileverSpec, specification_digest

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
Sha256 = Annotated[str, StringConstraints(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')]
DesignMode = Literal['fixed_design', 'bounded_thickness']
REVIEW_FIELDS = (
    'length_mm', 'width_mm', 'dimensional_tolerance_mm', 'tip_force_n',
    'max_tip_deflection_mm', 'material.youngs_modulus_mpa',
    'material.allowable_stress_mpa', 'material.reference', 'material.source_kind',
    'assumptions', 'mode', 'candidate_heights_mm',
)


def is_reviewable_field(name: str) -> bool:
    """Beam review fields, or a ``specification.<leaf>`` path of a generator brief."""
    return name in REVIEW_FIELDS or name.startswith(GENERATOR_SPEC_PREFIX)


class ContractModel(StrictModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True,
                              allow_inf_nan=False, revalidate_instances='always')


class CapabilityReference(ContractModel):
    id: Text
    version: Text
    numerical_policy_version: Text
    resource_policy_version: Text

    @model_validator(mode='after')
    def supported_versions(self):
        get_capability(self.id, self.version,
                       numerical_policy_version=self.numerical_policy_version,
                       resource_policy_version=self.resource_policy_version)
        return self


class Quantity(ContractModel):
    """Explicit conversions only; normalized values must still be reviewed."""
    value: float = Field(gt=0)
    unit: Literal['mm', 'cm', 'm', 'in', 'N', 'kN', 'Pa', 'MPa', 'GPa']

    def normalized(self, target: str) -> float:
        conversions = {
            'mm': {'mm': 1.0, 'cm': 10.0, 'm': 1000.0, 'in': 25.4},
            'N': {'N': 1.0, 'kN': 1000.0},
            'MPa': {'Pa': 1e-6, 'MPa': 1.0, 'GPa': 1000.0},
        }
        if target not in conversions or self.unit not in conversions[target]:
            raise ValueError('Unsupported or dimensionally conflicting quantity conversion')
        result = float(Decimal(str(self.value)) * Decimal(str(conversions[target][self.unit])))
        if not math.isfinite(result) or result <= 0:
            raise ValueError('Normalized quantity must remain finite and positive')
        return result


class ReviewedMassToForce(ContractModel):
    mass_kg: float = Field(gt=0)
    gravity_m_s2: float = Field(gt=0)
    acknowledged: Literal[True]
    reference: Text

    @field_validator('acknowledged', mode='before')
    @classmethod
    def explicit_boolean(cls, value):
        if value is not True:
            raise ValueError('Mass-to-force conversion requires explicit boolean acknowledgement')
        return value

    @model_validator(mode='after')
    def finite_product(self):
        if not math.isfinite(self.force_n) or self.force_n <= 0:
            raise ValueError('Mass-to-force result must remain finite and positive')
        return self

    @property
    def force_n(self) -> float:
        return float(Decimal(str(self.mass_kg)) * Decimal(str(self.gravity_m_s2)))


class MaterialReference(ContractModel):
    youngs_modulus_mpa: float = Field(gt=0)
    allowable_stress_mpa: float = Field(gt=0)
    source_kind: Literal['illustrative', 'user_supplied', 'documented']
    reference: Text


class FixedRequirements(ContractModel):
    units: Literal['mm_N_MPa']
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    dimensional_tolerance_mm: float = Field(gt=0)
    tip_force_n: float = Field(gt=0)
    max_tip_deflection_mm: float = Field(gt=0)
    material: MaterialReference
    assumptions: Literal['ideal_clamp_end_force_linear_elastic']

    def to_cantilever(self, height_mm: float) -> CantileverSpec:
        return CantileverSpec.model_validate(dict(
            family='rectangular_cantilever', units=self.units,
            length_mm=self.length_mm, width_mm=self.width_mm, height_mm=height_mm,
            dimensional_tolerance_mm=self.dimensional_tolerance_mm,
            youngs_modulus_mpa=self.material.youngs_modulus_mpa,
            allowable_stress_mpa=self.material.allowable_stress_mpa,
            material_reference=self.material.reference, tip_force_n=self.tip_force_n,
            max_tip_deflection_mm=self.max_tip_deflection_mm, assumptions=self.assumptions))

    @model_validator(mode='after')
    def legacy_fixed_envelope(self):
        # A square section validates fixed inputs without inventing a selected
        # thickness; each actual thickness is separately validated below.
        self.to_cantilever(self.width_mm)
        return self


class BriefContent(ContractModel):
    schema_version: Literal['engineering_brief_v1']
    capability: CapabilityReference
    requirements: FixedRequirements
    mode: DesignMode
    candidate_heights_mm: tuple[float, ...] = Field(min_length=1, max_length=4)
    required_checks: tuple[str, ...] = CAPABILITY.required_checks

    @field_validator('candidate_heights_mm')
    @classmethod
    def normalized_choices(cls, values):
        return tuple(sorted(set(values)))

    @field_validator('required_checks')
    @classmethod
    def complete_required_checks(cls, values):
        if len(values) != len(CAPABILITY.required_checks) or set(values) != set(CAPABILITY.required_checks):
            raise ValueError('All mandatory capability checks are required exactly once')
        return CAPABILITY.required_checks

    @model_validator(mode='after')
    def validate_choices(self):
        if self.mode == 'fixed_design' and len(self.candidate_heights_mm) != 1:
            raise ValueError('Fixed design requires exactly one reviewed thickness')
        for height in self.candidate_heights_mm:
            self.requirements.to_cantilever(height)
        return self

    def fixed_meaning(self) -> dict:
        return dict(schema_version=self.schema_version,
                    capability=self.capability.model_dump(mode='json'),
                    requirements=self.requirements.model_dump(mode='json'),
                    required_checks=self.required_checks)

    @property
    def fixed_requirements_sha256(self) -> str:
        return digest(self.fixed_meaning())

    @property
    def brief_sha256(self) -> str:
        return digest(dict(self.fixed_meaning(), mode=self.mode,
                           candidate_heights_mm=self.candidate_heights_mm))


class GeneratorBriefContent(ContractModel):
    """One reviewed specification for a registry generator; one candidate, no mode.

    ``specification`` is the spec model's ``model_dump(mode='json')`` with defaults
    included, so the reviewed brief is exactly the specification that runs.
    """
    schema_version: Literal['engineering_generator_brief_v1']
    capability: CapabilityReference
    specification: dict[str, Any]
    required_checks: tuple[str, ...]

    @property
    def descriptor(self) -> GeneratorDescriptor:
        descriptor = get_capability(self.capability.id, self.capability.version,
                                    numerical_policy_version=self.capability.numerical_policy_version,
                                    resource_policy_version=self.capability.resource_policy_version)
        if not isinstance(descriptor, GeneratorDescriptor):
            raise ValueError('Capability is not a registry generator')
        return descriptor

    @model_validator(mode='after')
    def reviewed_specification(self):
        descriptor = self.descriptor
        refused = descriptor.refuse(self.specification)
        if refused:
            raise ValueError('Unsupported request: ' + refused[0][1])
        try:
            normalized = descriptor.parse_spec(self.specification).model_dump(mode='json')
        except ValidationError as error:
            first = error.errors(include_input=False, include_url=False)[0]
            raise ValueError('Specification invalid at ' + '.'.join(map(str, first['loc']))
                             + ': ' + first['msg'][:160]) from None
        if canonical_json(normalized) != canonical_json(self.specification):
            raise ValueError('Specification must be the normalized form with every default included')
        if self.required_checks != descriptor.required_checks(normalized):
            raise ValueError('Required checks must equal the capability set for this specification')
        return self

    @property
    def review_fields(self) -> tuple[str, ...]:
        return self.descriptor.review_fields(self.specification)

    def fixed_meaning(self) -> dict:
        return dict(schema_version=self.schema_version,
                    capability=self.capability.model_dump(mode='json'),
                    specification=self.specification, required_checks=self.required_checks)

    @property
    def fixed_requirements_sha256(self) -> str:
        meaning = self.fixed_meaning()
        if self.capability.id == 'equipment_bay':
            from .equipment_bay import FIXED_FIELDS, catalog_digest
            meaning['specification'] = {name: self.specification[name] for name in FIXED_FIELDS}
            meaning['catalog_sha256'] = catalog_digest()
        elif self.capability.id == 'editable_plate':
            from .editable_plate import FIXED_FIELDS
            meaning['specification'] = {name: self.specification[name] for name in FIXED_FIELDS}
        return digest(meaning)

    @property
    def brief_sha256(self) -> str:
        if self.capability.id in ('equipment_bay', 'editable_plate'):
            return digest(dict(fixed_requirements_sha256=self.fixed_requirements_sha256,
                               specification=self.specification))
        return self.fixed_requirements_sha256


AnyContent = Annotated[BriefContent | GeneratorBriefContent, Field(discriminator='schema_version')]


class FieldOrigin(ContractModel):
    field: Text
    origin: Literal['user_supplied', 'ai_proposed', 'converted', 'policy']
    reference: Text

    @field_validator('field')
    @classmethod
    def known_field(cls, value):
        if not is_reviewable_field(value):
            raise ValueError('Unknown field origin')
        return value


class Provenance(ContractModel):
    record_id: Text
    created_at: AwareDatetime
    input_mode: Literal['form', 'ai']
    field_origins: tuple[FieldOrigin, ...]
    force_conversion: ReviewedMassToForce | None = None

    @model_validator(mode='after')
    def unique_origins(self):
        fields = [origin.field for origin in self.field_origins]
        if len(fields) != len(set(fields)):
            raise ValueError('Each field must have one unambiguous origin')
        return self


class BriefDraft(ContractModel):
    schema_version: Literal['engineering_draft_v1'] = 'engineering_draft_v1'
    draft_id: Text
    revision: int = Field(ge=1)
    content: AnyContent | None
    clarifications: tuple[Text, ...]
    provenance: Provenance

    @model_validator(mode='after')
    def missing_content_needs_clarification(self):
        if self.content is None and not self.clarifications:
            raise ValueError('Incomplete content requires explicit clarification')
        return self

    def revised(self, *, content: BriefContent | GeneratorBriefContent | None, provenance: Provenance,
                clarifications: tuple[str, ...] = ()) -> BriefDraft:
        return BriefDraft(draft_id=self.draft_id, revision=self.revision + 1,
                          content=content, clarifications=clarifications, provenance=provenance)


class ReviewAcknowledgement(ContractModel):
    draft_id: Text
    draft_revision: int = Field(ge=1)
    brief_sha256: Sha256
    acknowledged_fields: tuple[str, ...]
    reviewed_at: AwareDatetime
    reviewed_by: Text

    @field_validator('acknowledged_fields')
    @classmethod
    def known_unique_fields(cls, values):
        if len(values) != len(set(values)) or not all(is_reviewable_field(v) for v in values):
            raise ValueError('Review must identify unique supported fields')
        return values


class EngineeringBrief(ContractModel):
    schema_version: Literal['engineering_brief_v1'] = 'engineering_brief_v1'
    brief_id: Text
    draft_id: Text
    draft_revision: int = Field(ge=1)
    content: AnyContent
    fixed_requirements_sha256: Sha256
    brief_sha256: Sha256
    review: ReviewAcknowledgement
    provenance: Provenance

    @model_validator(mode='after')
    def exact_reviewed_meaning(self):
        generator = isinstance(self.content, GeneratorBriefContent)
        fields = self.content.review_fields if generator else REVIEW_FIELDS
        if (self.fixed_requirements_sha256 != self.content.fixed_requirements_sha256
                or self.brief_sha256 != self.content.brief_sha256):
            raise ValueError('Brief identity does not match normalized meaning')
        if (self.review.draft_id != self.draft_id
                or self.review.draft_revision != self.draft_revision
                or self.review.brief_sha256 != self.brief_sha256
                or set(self.review.acknowledged_fields) != set(fields)):
            raise ValueError('Exact draft revision and all values/assumptions require review')
        if {origin.field for origin in self.provenance.field_origins} != set(fields):
            raise ValueError('Every reviewed input requires a recorded field origin')
        if generator and self.provenance.force_conversion is not None:
            raise ValueError('Generator briefs take no mass-to-force conversion')
        if not generator and self.provenance.force_conversion is not None:
            conversion = self.provenance.force_conversion
            force_origin = next(o for o in self.provenance.field_origins if o.field == 'tip_force_n')
            if (conversion.force_n != self.content.requirements.tip_force_n
                    or force_origin.origin != 'converted'):
                raise ValueError('Reviewed force conversion does not match the normalized force/origin')
        return self

    @classmethod
    def from_draft(cls, draft: BriefDraft, review: ReviewAcknowledgement, *, brief_id: str) -> EngineeringBrief:
        draft = BriefDraft.model_validate(draft.model_dump())
        if draft.content is None or draft.clarifications:
            raise ValueError('Unresolved clarification prevents final review')
        return cls(brief_id=brief_id, draft_id=draft.draft_id, draft_revision=draft.revision,
                   content=draft.content, fixed_requirements_sha256=draft.content.fixed_requirements_sha256,
                   brief_sha256=draft.content.brief_sha256, review=review, provenance=draft.provenance)


class CandidateSpec(ContractModel):
    schema_version: Literal['engineering_candidate_v1'] = 'engineering_candidate_v1'
    content: BriefContent
    brief_sha256: Sha256
    fixed_requirements_sha256: Sha256
    specification: CantileverSpec
    candidate_sha256: Sha256

    @model_validator(mode='after')
    def bound_candidate(self):
        spec = CantileverSpec.model_validate(self.specification.model_dump())
        if (self.brief_sha256 != self.content.brief_sha256
                or self.fixed_requirements_sha256 != self.content.fixed_requirements_sha256
                or spec.height_mm not in self.content.candidate_heights_mm
                or spec != self.content.requirements.to_cantilever(spec.height_mm)
                or self.candidate_sha256 != specification_digest(spec)):
            raise ValueError('Candidate does not match the complete reviewed brief/specification')
        return self

    @classmethod
    def from_brief(cls, brief: EngineeringBrief, height_mm: float) -> CandidateSpec:
        brief = EngineeringBrief.model_validate(brief.model_dump())
        spec = brief.content.requirements.to_cantilever(height_mm)
        return cls(content=brief.content, brief_sha256=brief.brief_sha256,
                   fixed_requirements_sha256=brief.fixed_requirements_sha256,
                   specification=spec, candidate_sha256=specification_digest(spec))


class GeneratorCandidate(ContractModel):
    """The single reviewed specification of a generator; ``candidate_sha256`` is the
    generator's own specification digest, supplied by its adapter."""
    schema_version: Literal['engineering_generator_candidate_v1'] = 'engineering_generator_candidate_v1'
    content: GeneratorBriefContent
    brief_sha256: Sha256
    fixed_requirements_sha256: Sha256
    candidate_sha256: Sha256

    @model_validator(mode='after')
    def bound_candidate(self):
        if (self.brief_sha256 != self.content.brief_sha256
                or self.fixed_requirements_sha256 != self.content.fixed_requirements_sha256):
            raise ValueError('Candidate does not match the complete reviewed brief/specification')
        return self

    @classmethod
    def from_brief(cls, brief: EngineeringBrief, candidate_sha256: str) -> GeneratorCandidate:
        brief = EngineeringBrief.model_validate(brief.model_dump())
        if not isinstance(brief.content, GeneratorBriefContent):
            raise ValueError('Brief does not belong to a registry generator')
        return cls(content=brief.content, brief_sha256=brief.brief_sha256,
                   fixed_requirements_sha256=brief.fixed_requirements_sha256,
                   candidate_sha256=candidate_sha256)


class CheckState(str, Enum):
    PASSED = 'passed'
    FAILED = 'failed'
    NOT_EVALUATED = 'not_evaluated'
    UNAVAILABLE = 'unavailable'


class Lifecycle(str, Enum):
    ADMITTED = 'admitted'
    RUNNING = 'running'
    PACKAGING = 'packaging'
    COMPLETED = 'completed'
    UNAVAILABLE = 'unavailable'
    CANCELLED = 'cancelled'


class Outcome(str, Enum):
    ACCEPTED = 'accepted'
    REJECTED = 'rejected'
    NOT_EVALUATED = 'not_evaluated'


class Measurement(ContractModel):
    name: Text
    value: float
    unit: Literal['mm', 'mm2', 'mm3', 'mm4', 'N', 'N_mm', 'MPa', 'ratio', 'count', 'rad', 'N_mm_energy',
                  'kg', 'm', 'm4', 'Pa', 's', 'm_s']


class ComparisonLimit(Measurement):
    comparison: Literal['le', 'ge', 'eq', 'abs_le']
    tolerance: float = Field(default=0.0, ge=0)


class ArtifactIdentity(ContractModel):
    path: Literal['brief.json', 'selected_spec.json', 'model.py', 'model.step', 'preview.glb',
                  'attempts.json', 'report.json', 'report.md', 'manifest.json',
                  'generator-spec.json', 'generator-report.json', 'generator-body.step',
                  'generator-preview.glb', 'generator-cleaned.stl', 'generator-spec.canonical.json', 'generator-bom.json',
                  'generator-bundle.zip', 'generator-result.json']
    sha256: Sha256
    bytes: int = Field(gt=0)


class CheckResult(ContractModel):
    schema_version: Literal['engineering_check_v1'] = 'engineering_check_v1'
    check_id: Text
    state: CheckState
    method: Text
    method_version: Text
    brief_sha256: Sha256
    candidate_sha256: Sha256
    measurements: tuple[Measurement, ...]
    limits: tuple[ComparisonLimit, ...]
    artifacts: tuple[ArtifactIdentity, ...]
    reason: Text | None = None

    @model_validator(mode='after')
    def evidence_shape(self):
        if self.check_id not in CAPABILITY.required_checks:
            raise ValueError('Check is not declared by the capability version')
        if self.state == CheckState.PASSED and not (
                self.measurements and self.limits and any(a.path == 'model.step' for a in self.artifacts)):
            raise ValueError('Passed checks require measurements, limits and measured STEP identity')
        _evidence_common(self)
        return self


def _evidence_common(check) -> None:
    if check.state != CheckState.PASSED and not check.reason:
        raise ValueError('A non-passing check requires a specific reason')
    for items, label in ((check.measurements, 'measurement'), (check.limits, 'limit')):
        names = [item.name for item in items]
        if len(names) != len(set(names)):
            raise ValueError(f'Duplicate {label} names')
    measured = {m.name: m.unit for m in check.measurements}
    if any(limit.name not in measured or measured[limit.name] != limit.unit for limit in check.limits):
        raise ValueError('Comparison limits must identify a measured quantity with matching units')
    paths = [a.path for a in check.artifacts]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate artifact identities')


class GeneratorCheckResult(ContractModel):
    """One check of a registry generator's own verifier, in the shared evidence shape."""
    schema_version: Literal['engineering_generator_check_v1'] = 'engineering_generator_check_v1'
    capability_id: Text
    check_id: Text
    state: CheckState
    method: Text
    method_version: Text
    brief_sha256: Sha256
    candidate_sha256: Sha256
    measurements: tuple[Measurement, ...]
    limits: tuple[ComparisonLimit, ...]
    artifacts: tuple[ArtifactIdentity, ...]
    reason: Text | None = None

    @model_validator(mode='after')
    def evidence_shape(self):
        descriptor = get_generator(self.capability_id)
        if descriptor is None or self.check_id not in descriptor.all_check_ids:
            raise ValueError('Check is not declared by the capability version')
        if any(a.path not in descriptor.payloads for a in self.artifacts):
            raise ValueError('Check artifact is not a payload of this capability')
        # The primary artifact identity of a passed check is required by ``assess_generator`` for
        # accepted runs; a rejected package delivers no geometry, so its checks carry none.
        if self.state == CheckState.PASSED and not (self.measurements and self.limits):
            raise ValueError('Passed checks require measurements and limits')
        _evidence_common(self)
        return self


class AttemptRecord(ContractModel):
    ordinal: int = Field(ge=0, lt=4)
    candidate: CandidateSpec | GeneratorCandidate
    outcome: Outcome
    checks: tuple[CheckResult | GeneratorCheckResult, ...]
    failures: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def consistent_evidence(self):
        generator = isinstance(self.candidate, GeneratorCandidate)
        if generator and (self.ordinal != 0 or any(not isinstance(c, GeneratorCheckResult)
                                                   or c.capability_id != self.candidate.content.capability.id
                                                   for c in self.checks)):
            raise ValueError("Generator attempts are single and carry that capability's own checks")
        if not generator and any(not isinstance(c, CheckResult) for c in self.checks):
            raise ValueError('Beam attempts carry beam checks')
        required = self.candidate.content.required_checks if generator else CAPABILITY.required_checks
        ids = [c.check_id for c in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError('Each check must occur once per attempt')
        if any(c.brief_sha256 != self.candidate.brief_sha256
               or c.candidate_sha256 != self.candidate.candidate_sha256 for c in self.checks):
            raise ValueError('Check evidence belongs to another brief/candidate')
        if self.outcome == Outcome.ACCEPTED and (
                set(ids) != set(required)
                or any(c.state != CheckState.PASSED for c in self.checks) or self.failures):
            raise ValueError('Accepted attempt requires all mandatory passing evidence')
        if self.outcome == Outcome.REJECTED and (
                not self.failures or not any(c.state == CheckState.FAILED for c in self.checks)
                or any(c.state == CheckState.UNAVAILABLE for c in self.checks)):
            raise ValueError('Rejected attempt requires measured failure evidence')
        return self


MAX_BUNDLE_BYTES = 256 * 1024**2


class BundleIdentity(ContractModel):
    """External delivery envelope; no circular manifest/ZIP self-hashes."""
    payloads: tuple[ArtifactIdentity, ...]
    manifest_sha256: Sha256
    manifest_bytes: int = Field(gt=0)
    zip_sha256: Sha256
    # Schema ceiling = the largest per-capability run_bytes (regen, 256 MiB; owner decision 2026-11-19,
    # Q1 a). The per-capability budget (32 MiB for the others) is enforced in bundles.py before anything
    # is written; a test pins this ceiling to max(run_bytes over all capabilities).
    zip_bytes: int = Field(gt=0, le=MAX_BUNDLE_BYTES)
    stored_bytes: int = Field(gt=0, le=MAX_BUNDLE_BYTES)

    @model_validator(mode='after')
    def complete_payload(self):
        paths = [p.path for p in self.payloads]
        expected = {'brief.json', 'selected_spec.json', 'model.py', 'model.step',
                    'preview.glb', 'attempts.json', 'report.json', 'report.md'}
        base = set(GENERATOR_BASE_PAYLOADS)
        generated = (len(paths) == len(set(paths)) and base <= set(paths) and len(paths) > len(base)
                     and all(p.startswith(GENERATOR_PREFIX) for p in set(paths) - base))
        if not generated and (len(paths) != len(expected) or set(paths) != expected):
            raise ValueError('Bundle requires all eight payloads; manifest/ZIP hashes stay external')
        minimum = sum(p.bytes for p in self.payloads) + self.manifest_bytes + self.zip_bytes
        if minimum > self.stored_bytes:
            raise ValueError('Stored size must include payload, manifest, archive and retained metadata')
        return self


class EngineeringRun(ContractModel):
    schema_version: Literal['engineering_run_v1']
    job_id: Text
    capability: CapabilityReference
    brief_sha256: Sha256
    fixed_requirements_sha256: Sha256
    lifecycle: Lifecycle
    outcome: Outcome
    attempts: tuple[AttemptRecord, ...] = Field(max_length=4)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    selected_candidate_sha256: Sha256 | None = None
    bundle: BundleIdentity | None = None
    failures: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def consistent_run(self):
        if self.updated_at < self.created_at:
            raise ValueError('Run update cannot precede creation')
        if [a.ordinal for a in self.attempts] != list(range(len(self.attempts))):
            raise ValueError('Attempt ordinals must be contiguous')
        if any(a.candidate.brief_sha256 != self.brief_sha256
               or a.candidate.fixed_requirements_sha256 != self.fixed_requirements_sha256
               or a.candidate.content.capability != self.capability for a in self.attempts):
            raise ValueError('Run attempts do not belong to this reviewed brief/version')
        if any(a.outcome == Outcome.ACCEPTED for a in self.attempts[:-1]):
            raise ValueError('No attempt may follow an accepted candidate')
        generator = any(isinstance(a.candidate, GeneratorCandidate) for a in self.attempts)
        if generator and (len(self.attempts) > 1 or any(not isinstance(a.candidate, GeneratorCandidate)
                                                        for a in self.attempts)):
            raise ValueError('A generator run has at most one attempt')
        if self.attempts and not generator:
            choices = self.attempts[0].candidate.content.candidate_heights_mm
            actual = tuple(a.candidate.specification.height_mm for a in self.attempts)
            if actual != choices[:len(actual)]:
                raise ValueError('Attempt order must be a prefix of the ascending reviewed choices')
            if any(a.outcome != Outcome.REJECTED for a in self.attempts[:-1]):
                raise ValueError('Only a rejected attempt permits advancing to the next choice')
        if self.lifecycle != Lifecycle.COMPLETED and self.outcome != Outcome.NOT_EVALUATED:
            raise ValueError('Engineering outcome is committed only with completed lifecycle')
        if self.bundle is not None and self.outcome != Outcome.ACCEPTED:
            raise ValueError('Only a completed accepted run exposes an accepted bundle')
        if self.outcome == Outcome.ACCEPTED:
            if (not self.attempts or self.attempts[-1].outcome != Outcome.ACCEPTED
                    or self.selected_candidate_sha256 != self.attempts[-1].candidate.candidate_sha256
                    or self.bundle is None):
                raise ValueError('Accepted run requires selected candidate, complete checks and bundle')
            # The schema ceiling is the largest capability budget; each run is held to its own.
            budget = (self.attempts[-1].candidate.content.descriptor if generator else CAPABILITY).resources.run_bytes
            if max(self.bundle.zip_bytes, self.bundle.stored_bytes) > budget:
                raise ValueError('Bundle exceeds the capability run budget')
            files = {p.path: p.sha256 for p in self.bundle.payloads}
            if generator and set(files) != set(GENERATOR_BASE_PAYLOADS) | set(
                    self.attempts[-1].candidate.content.descriptor.payloads):
                raise ValueError('Generator bundle does not match the capability payload set')
            if any(files[a.path] != a.sha256 for c in self.attempts[-1].checks
                   for a in c.artifacts if a.path in files):
                raise ValueError('Published artifacts differ from checked artifacts')
        if self.outcome == Outcome.REJECTED and (
                not self.attempts or any(a.outcome != Outcome.REJECTED for a in self.attempts)
                or self.selected_candidate_sha256 is not None
                or len(self.attempts) != (1 if generator else
                                          len(self.attempts[0].candidate.content.candidate_heights_mm))):
            raise ValueError('Rejected run cannot select or publish accepted CAD')
        if self.lifecycle == Lifecycle.COMPLETED and self.outcome == Outcome.NOT_EVALUATED:
            raise ValueError('Completed runs require an evaluated outcome')
        if self.lifecycle in (Lifecycle.UNAVAILABLE, Lifecycle.CANCELLED) and not self.failures:
            raise ValueError('Unavailable/cancelled runs require a reason')
        return self
