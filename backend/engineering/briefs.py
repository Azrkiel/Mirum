"""Offline preparation and exact-revision review, independent of transport.

Repository writes must be atomic compare-and-swap operations. The memory
implementation is for local use/tests; B09 owns durable storage and retention.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from threading import RLock
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import Field, ValidationError

from .contracts import (BriefContent, BriefDraft, ContractModel, EngineeringBrief,
                        FieldOrigin, Provenance, REVIEW_FIELDS, ReviewAcknowledgement, Text)


class RevisionConflict(ValueError):
    """Map to HTTP 409; the caller must read and review the current revision."""


def _check_revision(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError('Expected revision must be a positive integer')


class InputIssue(ContractModel):
    field: Text
    state: Literal['clarification_required', 'unsupported']
    reason: Text


class PreparedDraft(ContractModel):
    draft: BriefDraft
    input_json: str = Field(max_length=16384)
    issues: tuple[InputIssue, ...]

    def export(self) -> dict:
        return dict(draft=self.draft.model_dump(mode='json'), input=json.loads(self.input_json),
                    issues=[i.model_dump(mode='json') for i in self.issues])


class BriefRepository(Protocol):
    """B09 must preserve these CAS/uniqueness guarantees in one transaction.

    Read methods raise KeyError for unknown IDs. Finalize checks the latest draft
    revision, stores an immutable brief and uniquely indexes (draft, revision).
    An identical review retry returns that brief; a different review conflicts.
    """
    def create(self, prepared: PreparedDraft) -> None: ...
    def read(self, draft_id: str) -> PreparedDraft: ...
    def replace(self, prepared: PreparedDraft, *, expected_revision: int) -> None: ...
    def finalize_if_current(self, brief: EngineeringBrief) -> EngineeringBrief: ...
    def read_brief(self, brief_id: str) -> EngineeringBrief: ...
    def is_finalized(self, draft_id: str, revision: int) -> bool: ...
    def begin_proposal(self, draft_id: str, revision: int, owner: str) -> None: ...
    def end_proposal(self, draft_id: str, revision: int, owner: str) -> None: ...


class MemoryBriefRepository:
    def __init__(self):
        self._lock = RLock()
        self._drafts: dict[str, str] = {}
        self._briefs: dict[str, str] = {}
        self._finalized: dict[tuple[str, int], str] = {}
        self._proposals: dict[tuple[str, int], str] = {}

    def create(self, prepared: PreparedDraft) -> None:
        prepared = PreparedDraft.model_validate(prepared.model_dump())
        with self._lock:
            if prepared.draft.draft_id in self._drafts or prepared.draft.revision != 1:
                raise RevisionConflict('Draft already exists or is not an initial revision')
            self._drafts[prepared.draft.draft_id] = prepared.model_dump_json()

    def read(self, draft_id: str) -> PreparedDraft:
        with self._lock:
            return PreparedDraft.model_validate_json(self._drafts[draft_id])

    def replace(self, prepared: PreparedDraft, *, expected_revision: int) -> None:
        _check_revision(expected_revision)
        prepared = PreparedDraft.model_validate(prepared.model_dump())
        with self._lock:
            current = self.read(prepared.draft.draft_id)
            if current.draft.revision != expected_revision or prepared.draft.revision != expected_revision + 1:
                raise RevisionConflict('Draft revision changed; read and review again')
            self._drafts[prepared.draft.draft_id] = prepared.model_dump_json()

    def finalize_if_current(self, brief: EngineeringBrief) -> EngineeringBrief:
        brief = EngineeringBrief.model_validate(brief.model_dump())
        with self._lock:
            current = self.read(brief.draft_id).draft
            if (brief.draft_id, brief.draft_revision) in self._proposals:
                raise RevisionConflict('AI preparation is active; review its completed revision')
            if current.revision != brief.draft_revision:
                raise RevisionConflict('Draft changed during finalization')
            # Re-derive the brief under the same lock: no stale or invented record.
            expected = EngineeringBrief.from_draft(current, brief.review, brief_id=brief.brief_id)
            if expected != brief:
                raise RevisionConflict('Draft changed during finalization')
            key = (brief.draft_id, brief.draft_revision)
            if key in self._finalized:
                existing = self.read_brief(self._finalized[key])
                if existing.review != brief.review:
                    raise RevisionConflict('This revision already has a different immutable review')
                return existing
            if brief.brief_id in self._briefs:
                raise RevisionConflict('Brief ID already exists')
            self._briefs[brief.brief_id] = brief.model_dump_json()
            self._finalized[key] = brief.brief_id
            return brief

    def read_brief(self, brief_id: str) -> EngineeringBrief:
        with self._lock:
            return EngineeringBrief.model_validate_json(self._briefs[brief_id])

    def is_finalized(self, draft_id: str, revision: int) -> bool:
        with self._lock:
            return (draft_id, revision) in self._finalized

    def begin_proposal(self, draft_id: str, revision: int, owner: str) -> None:
        _check_revision(revision)
        with self._lock:
            key = draft_id, revision
            if (self.read(draft_id).draft.revision != revision or key in self._finalized or key in self._proposals):
                raise RevisionConflict('Draft changed, finalized or already has active AI preparation')
            self._proposals[key] = owner

    def end_proposal(self, draft_id: str, revision: int, owner: str) -> None:
        with self._lock:
            key = draft_id, revision
            if self._proposals.get(key) != owner:
                raise RevisionConflict('AI preparation reservation belongs to another owner')
            del self._proposals[key]


class BriefService:
    def __init__(self, repository: BriefRepository):
        self.repository = repository

    def validate(self, payload: dict) -> tuple[BriefContent | None, tuple[InputIssue, ...]]:
        encoded = self._encode(payload)
        try:
            return BriefContent.model_validate_json(encoded), ()
        except ValidationError as error:
            issues = []
            for e in error.errors(include_input=False, include_url=False):
                field = '.'.join(map(str, e['loc'])) or 'requirements'
                reason = e['msg'][:240]
                unsupported = (e['type'] in ('extra_forbidden', 'literal_error', 'less_than_equal')
                               or field.startswith('capability')
                               or 'Beam requires' in reason or 'capability' in reason.lower())
                # Missing units/assumptions need clarification; they are never inferred.
                issues.append(InputIssue(field=field[:240], state='unsupported' if unsupported
                                         else 'clarification_required', reason=reason))
            return None, tuple(issues)

    @staticmethod
    def _encode(payload: dict) -> str:
        if type(payload) is not dict:
            raise ValueError('Preparation requires a structured input object')
        encoded = json.dumps(payload, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode()) > 16384:
            raise ValueError('Structured preparation input exceeds 16 KiB')
        return encoded

    @staticmethod
    def input_provenance(payload: dict) -> Provenance:
        origins = []
        for name in REVIEW_FIELDS:
            path = name.split('.') if name in ('mode', 'candidate_heights_mm') else ['requirements', *name.split('.')]
            value = payload
            for part in path:
                if not isinstance(value, dict) or part not in value:
                    break
                value = value[part]
            else:
                origins.append(FieldOrigin(field=name, origin='user_supplied', reference='Typed normalized input'))
        return Provenance(record_id=uuid4().hex, created_at=datetime.now(timezone.utc),
                          input_mode='form', field_origins=tuple(origins))

    def _prepare(self, payload: dict, *, draft_id: str, revision: int,
                 provenance: Provenance | None, extra_issues: tuple[InputIssue, ...] = ()) -> PreparedDraft:
        content, validation_issues = self.validate(payload)
        issues = validation_issues + tuple(InputIssue.model_validate(i.model_dump()) for i in extra_issues)
        draft = BriefDraft(draft_id=draft_id, revision=revision, content=content,
                           clarifications=tuple(f'{i.field}: {i.reason}'[:240] for i in issues),
                           provenance=provenance or self.input_provenance(payload))
        return PreparedDraft(draft=draft, input_json=self._encode(payload), issues=issues)

    def create(self, payload: dict, *, provenance: Provenance | None = None,
               extra_issues: tuple[InputIssue, ...] = ()) -> PreparedDraft:
        prepared = self._prepare(payload, draft_id=uuid4().hex, revision=1, provenance=provenance, extra_issues=extra_issues)
        self.repository.create(prepared)
        return prepared

    def read(self, draft_id: str) -> PreparedDraft:
        return self.repository.read(draft_id)

    def export(self, draft_id: str) -> dict:
        return self.read(draft_id).export()

    def edit(self, draft_id: str, *, expected_revision: int, payload: dict,
             provenance: Provenance | None = None, extra_issues: tuple[InputIssue, ...] = ()) -> PreparedDraft:
        _check_revision(expected_revision)
        if self.read(draft_id).draft.revision != expected_revision:
            raise RevisionConflict('Draft revision changed; read and review again')
        prepared = self._prepare(payload, draft_id=draft_id, revision=expected_revision + 1,
                                 provenance=provenance, extra_issues=extra_issues)
        self.repository.replace(prepared, expected_revision=expected_revision)
        return prepared

    def finalize(self, review: ReviewAcknowledgement) -> EngineeringBrief:
        review = ReviewAcknowledgement.model_validate(review.model_dump())
        draft = self.read(review.draft_id).draft
        if draft.revision != review.draft_revision:
            raise RevisionConflict('Review is for a superseded draft revision')
        brief = EngineeringBrief.from_draft(draft, review, brief_id=uuid4().hex)
        return self.repository.finalize_if_current(brief)

    def read_brief(self, brief_id: str) -> EngineeringBrief:
        return self.repository.read_brief(brief_id)

    def is_finalized(self, draft_id: str, revision: int) -> bool:
        return self.repository.is_finalized(draft_id, revision)

    def begin_proposal(self, draft_id: str, revision: int, owner: str) -> None:
        self.repository.begin_proposal(draft_id, revision, owner)

    def end_proposal(self, draft_id: str, revision: int, owner: str) -> None:
        self.repository.end_proposal(draft_id, revision, owner)
