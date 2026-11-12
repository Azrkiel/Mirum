"""Strict V1 review and evidence records, independent of providers/native CAD.

Hashes bind normalized meaning/bytes, not authenticity or physical truth.
Service persistence, execution and actual-byte publication belong to consumers.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
import math
from typing import Annotated, Literal

from pydantic import (AwareDatetime, ConfigDict, Field, StringConstraints,
                      field_validator, model_validator)

from cem.core import StrictModel, digest
from .capabilities import CAPABILITY, get_capability
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


class FieldOrigin(ContractModel):
    field: Text
    origin: Literal['user_supplied', 'ai_proposed', 'converted', 'policy']
    reference: Text

    @field_validator('field')
    @classmethod
    def known_field(cls, value):
        if value not in REVIEW_FIELDS:
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
    content: BriefContent | None
    clarifications: tuple[Text, ...]
    provenance: Provenance

    @model_validator(mode='after')
    def missing_content_needs_clarification(self):
        if self.content is None and not self.clarifications:
            raise ValueError('Incomplete content requires explicit clarification')
        return self

    def revised(self, *, content: BriefContent | None, provenance: Provenance,
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
        if len(values) != len(set(values)) or set(values) - set(REVIEW_FIELDS):
            raise ValueError('Review must identify unique supported fields')
        return values


class EngineeringBrief(ContractModel):
    schema_version: Literal['engineering_brief_v1'] = 'engineering_brief_v1'
    brief_id: Text
    draft_id: Text
    draft_revision: int = Field(ge=1)
    content: BriefContent
    fixed_requirements_sha256: Sha256
    brief_sha256: Sha256
    review: ReviewAcknowledgement
    provenance: Provenance

    @model_validator(mode='after')
    def exact_reviewed_meaning(self):
        if (self.fixed_requirements_sha256 != self.content.fixed_requirements_sha256
                or self.brief_sha256 != self.content.brief_sha256):
            raise ValueError('Brief identity does not match normalized meaning')
        if (self.review.draft_id != self.draft_id
                or self.review.draft_revision != self.draft_revision
                or self.review.brief_sha256 != self.brief_sha256
                or set(self.review.acknowledged_fields) != set(REVIEW_FIELDS)):
            raise ValueError('Exact draft revision and all values/assumptions require review')
        if {origin.field for origin in self.provenance.field_origins} != set(REVIEW_FIELDS):
            raise ValueError('Every reviewed input requires a recorded field origin')
        if self.provenance.force_conversion is not None:
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
    unit: Literal['mm', 'mm2', 'mm3', 'mm4', 'N', 'N_mm', 'MPa', 'ratio', 'count', 'rad', 'N_mm_energy']


class ComparisonLimit(Measurement):
    comparison: Literal['le', 'ge', 'eq', 'abs_le']
    tolerance: float = Field(default=0.0, ge=0)


class ArtifactIdentity(ContractModel):
    path: Literal['brief.json', 'selected_spec.json', 'model.py', 'model.step', 'preview.glb',
                  'attempts.json', 'report.json', 'report.md', 'manifest.json']
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
        if self.state != CheckState.PASSED and not self.reason:
            raise ValueError('A non-passing check requires a specific reason')
        for items, label in ((self.measurements, 'measurement'), (self.limits, 'limit')):
            names = [item.name for item in items]
            if len(names) != len(set(names)):
                raise ValueError(f'Duplicate {label} names')
        measured = {m.name: m.unit for m in self.measurements}
        if any(limit.name not in measured or measured[limit.name] != limit.unit for limit in self.limits):
            raise ValueError('Comparison limits must identify a measured quantity with matching units')
        paths = [a.path for a in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError('Duplicate artifact identities')
        return self


class AttemptRecord(ContractModel):
    ordinal: int = Field(ge=0, lt=4)
    candidate: CandidateSpec
    outcome: Outcome
    checks: tuple[CheckResult, ...]
    failures: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def consistent_evidence(self):
        ids = [c.check_id for c in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError('Each check must occur once per attempt')
        if any(c.brief_sha256 != self.candidate.brief_sha256
               or c.candidate_sha256 != self.candidate.candidate_sha256 for c in self.checks):
            raise ValueError('Check evidence belongs to another brief/candidate')
        if self.outcome == Outcome.ACCEPTED and (
                set(ids) != set(CAPABILITY.required_checks)
                or any(c.state != CheckState.PASSED for c in self.checks) or self.failures):
            raise ValueError('Accepted attempt requires all mandatory passing evidence')
        if self.outcome == Outcome.REJECTED and (
                not self.failures or not any(c.state == CheckState.FAILED for c in self.checks)
                or any(c.state == CheckState.UNAVAILABLE for c in self.checks)):
            raise ValueError('Rejected attempt requires measured failure evidence')
        return self


class BundleIdentity(ContractModel):
    """External delivery envelope; no circular manifest/ZIP self-hashes."""
    payloads: tuple[ArtifactIdentity, ...]
    manifest_sha256: Sha256
    manifest_bytes: int = Field(gt=0)
    zip_sha256: Sha256
    zip_bytes: int = Field(gt=0, le=32 * 1024**2)
    stored_bytes: int = Field(gt=0, le=32 * 1024**2)

    @model_validator(mode='after')
    def complete_payload(self):
        paths = [p.path for p in self.payloads]
        expected = {'brief.json', 'selected_spec.json', 'model.py', 'model.step',
                    'preview.glb', 'attempts.json', 'report.json', 'report.md'}
        if len(paths) != len(expected) or set(paths) != expected:
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
        if self.attempts:
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
            files = {p.path: p.sha256 for p in self.bundle.payloads}
            if any(files[a.path] != a.sha256 for c in self.attempts[-1].checks
                   for a in c.artifacts if a.path in files):
                raise ValueError('Published artifacts differ from checked artifacts')
        if self.outcome == Outcome.REJECTED and (
                not self.attempts or any(a.outcome != Outcome.REJECTED for a in self.attempts)
                or self.selected_candidate_sha256 is not None
                or len(self.attempts) != len(self.attempts[0].candidate.content.candidate_heights_mm)):
            raise ValueError('Rejected run cannot select or publish accepted CAD')
        if self.lifecycle == Lifecycle.COMPLETED and self.outcome == Outcome.NOT_EVALUATED:
            raise ValueError('Completed runs require an evaluated outcome')
        if self.lifecycle in (Lifecycle.UNAVAILABLE, Lifecycle.CANCELLED) and not self.failures:
            raise ValueError('Unavailable/cancelled runs require a reason')
        return self
