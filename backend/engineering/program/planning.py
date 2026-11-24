"""Plan, lock, review and run gate for a compiled MEL program (F05, LANGUAGE.md stages 5-7).

Planning never decides acceptance (that stays in ``policy.assess`` behind the adapter), never derives
thresholds from geometry and never mutates requirements. One adapter serves one plan: no backend is
iterated or substituted. There is no route, table, journal or UI here; the draft and its review live
in the existing BriefService. The mock backend is labelled mock and can never be locked, authorized
or run. No native work happens before ``authorize_run`` has passed every check.

Native adapters and the cantilever builder are imported lazily, so importing this module loads no
native kernel, web framework or client library.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from engineering.contracts import BriefContent, CandidateSpec, GeneratorBriefContent, GeneratorCandidate
from engineering.equipment_bay import CAPABILITY_ID as BAY_ID
from engineering.program.hashing import canonical_json
from engineering.spec import specification_digest

from .adapters import adapter_for
from .hashing import execution_lock_v0_2_sha256, semantic_sha256
from .ir import ExecutionLock, execution_lock_v0_2
from .kernel import (
    UNAVAILABLE_REASONS, UNSUPPORTED_REASONS, BackendLock, Built, BuildPlan, ExecutionContext,
    ExecutionLockV0_2, SpanBinding, SupportDecision, backend_lock, plan_for,
)

__all__ = [
    'GATE_REASONS', 'PlanRefusal', 'ProgramPlan', 'ProgramReview', 'RunAuthorization',
    'plan_program', 'lock_program', 'open_review', 'authorize_run', 'run_program',
]

GATE_REASONS = ('not_finalized', 'review_mismatch', 'semantic_mismatch', 'lock_mismatch',
                'lock_version_unsupported', 'mock_backend', 'candidate_mismatch')
_ALLOWED = {
    'unsupported': frozenset(UNSUPPORTED_REASONS),
    'unavailable': frozenset(UNAVAILABLE_REASONS) | frozenset(GATE_REASONS),
    'refused': frozenset(GATE_REASONS),
}
_NO_BUILDER = 'capability_not_supported_by_backend'


@dataclass(frozen=True)
class PlanRefusal:
    outcome: Literal['unsupported', 'unavailable', 'refused']
    reasons: tuple[str, ...]
    diagnostic_code: str | None = None
    decision: SupportDecision | None = None

    def __post_init__(self):
        allowed = _ALLOWED.get(self.outcome)
        if allowed is None:
            raise ValueError(f'unknown refusal outcome {self.outcome!r}')
        object.__setattr__(self, 'reasons', tuple(self.reasons))
        if not self.reasons or any(r not in allowed for r in self.reasons):
            raise ValueError(f'reasons for {self.outcome!r} must be a non-empty subset of its allowlist')
        if self.decision is not None and not isinstance(self.decision, SupportDecision):
            raise ValueError('decision must be a SupportDecision')


@dataclass(frozen=True)
class ProgramPlan:
    compiled: object  # CompiledProgram
    mode: Literal['native', 'mock']
    backend: str
    plans: tuple[BuildPlan, ...]  # one per candidate height, in BriefContent order

    def __post_init__(self):
        if self.mode not in ('native', 'mock'):
            raise ValueError('mode must be native or mock')
        if not self.plans or not all(isinstance(p, BuildPlan) for p in self.plans):
            raise ValueError('a program plan needs one or more BuildPlans')
        if any(p.backend != self.backend for p in self.plans):
            raise ValueError('every BuildPlan must target the plan backend')


@dataclass(frozen=True)
class ProgramReview:
    draft_id: str
    draft_revision: int
    brief_sha256: str
    semantic_sha256: str
    lock: ExecutionLockV0_2 | ExecutionLock  # a v0_1 lock is readable but cannot authorize a run
    lock_sha256: str
    source_sha256: str  # the source that was reviewed

    def __post_init__(self):
        if not isinstance(self.lock, (ExecutionLockV0_2, ExecutionLock)):
            raise ValueError('lock must be an execution lock')
        # Security review LOW-3 (PR #138): a v0_2 lock is bound to its hash. (A v0_1 lock never authorizes.)
        if isinstance(self.lock, ExecutionLockV0_2) and execution_lock_v0_2_sha256(self.lock) != self.lock_sha256:
            raise ValueError('lock_sha256 does not match the lock')


@dataclass(frozen=True)
class RunAuthorization:
    review: ProgramReview
    brief_id: str
    backend_lock: BackendLock  # fresh, from this run's probe
    plans: tuple[BuildPlan, ...]  # rebuilt from the finalized brief
    source_sha256: str  # provenance of the source being run; may differ from review.source_sha256


def _refuse(outcome, *reasons, code=None, decision=None) -> PlanRefusal:
    return PlanRefusal(outcome, tuple(reasons), code, decision)


def _is_mock(adapter) -> bool:
    if getattr(adapter, 'mode', None) != 'native':
        return True
    try:
        return adapter.describe().mode != 'native'
    except Exception:
        return True


def _spans(ir) -> tuple[SpanBinding, ...]:
    out: list[SpanBinding] = []
    param = next((p.span for p in ir.candidates.parameters if p.span is not None), None)
    if param is not None:
        out.append(SpanBinding(key='build', span=param))
    if ir.span is not None:
        out.append(SpanBinding(key='inspect', span=ir.span))
        out.append(SpanBinding(key='export', span=ir.span))
    return tuple(out)


def _candidates_from_content(content: BriefContent) -> tuple[CandidateSpec, ...]:
    out = []
    for h in content.candidate_heights_mm:
        spec = content.requirements.to_cantilever(h)
        out.append(CandidateSpec(
            content=content, brief_sha256=content.brief_sha256,
            fixed_requirements_sha256=content.fixed_requirements_sha256,
            specification=spec, candidate_sha256=specification_digest(spec)))
    return tuple(out)


def _plans(ir, candidates) -> tuple[BuildPlan, ...]:
    spans = _spans(ir)
    return tuple(plan_for(ir.capability.id, ir.capability.version, ir.execution.backend, c,
                          tuple(ir.execution.exports), spans) for c in candidates)


def generator_candidates(content):
    if content.capability.id == 'regen_chamber':
        from engineering.regen_spec import specification_digest
        candidate_sha = specification_digest(content.descriptor.parse_spec(content.specification))
    else:
        from hashlib import sha256
        candidate_sha = sha256(canonical_json(content.specification).encode()).hexdigest()
    return (GeneratorCandidate(content=content, brief_sha256=content.brief_sha256,
                               fixed_requirements_sha256=content.fixed_requirements_sha256,
                               candidate_sha256=candidate_sha),)


def adapter_for_program(factory, compiled, *, mode='native'):
    options = {'capability': BAY_ID} if compiled.ir.capability.id == BAY_ID else {}
    return factory(compiled.ir.execution.backend, mode=mode, **options)


def _program_candidates(content):
    return generator_candidates(content) if isinstance(content, GeneratorBriefContent) else _candidates_from_content(content)


def _has_builder(compiled) -> bool:
    return (compiled.ir.capability.id == 'rectangular_cantilever'
            and isinstance(compiled.brief_content, BriefContent)) or (
                compiled.ir.capability.id in (BAY_ID, 'regen_chamber')
                and isinstance(compiled.brief_content, GeneratorBriefContent))


# ------------------------------------------------------------------ stage 5: plan (offline)

def plan_program(compiled, *, mode: str = 'native', adapter_factory=adapter_for) -> ProgramPlan | PlanRefusal:
    """Plan through the one adapter for the IR backend. Sync, offline, no probe, no fallback."""
    backend = compiled.ir.execution.backend
    if not _has_builder(compiled):
        return _refuse('unsupported', _NO_BUILDER)
    adapter = adapter_for_program(adapter_factory, compiled, mode=mode)
    plans = _plans(compiled.ir, _program_candidates(compiled.brief_content))
    for plan in plans:
        decision = adapter.supports(plan)
        if not decision.supported:
            return PlanRefusal('unsupported', tuple(decision.reasons), decision.diagnostic_code, decision)
    return ProgramPlan(compiled=compiled, mode=mode, backend=backend, plans=plans)


# ------------------------------------------------------------------ stage 6: lock (probe)

async def _fresh_lock(compiled, adapter):
    """(lock | None, refusal | None) from one fresh probe of `adapter`."""
    if adapter.backend != compiled.ir.execution.backend:
        return None, _refuse('refused', 'lock_mismatch')
    if _is_mock(adapter):
        return None, _refuse('unavailable', 'mock_backend')
    probe = await adapter.probe()
    if not probe.available:
        return None, _refuse('unavailable', *probe.reasons)
    try:
        return execution_lock_v0_2(compiled.ir, backend_lock(adapter.describe(), probe)), None
    except ValueError:
        return None, _refuse('refused', 'lock_mismatch')


async def lock_program(plan: ProgramPlan, *, adapter) -> ExecutionLockV0_2 | PlanRefusal:
    if plan.mode == 'mock':
        return _refuse('unavailable', 'mock_backend')
    lock, refusal = await _fresh_lock(plan.compiled, adapter)
    return refusal if refusal is not None else lock


# ------------------------------------------------------------------ stage 7: review and run gate

def open_review(plan: ProgramPlan, lock: ExecutionLockV0_2, brief_service) -> ProgramReview:
    """Open the draft through the existing BriefService. The user finalizes it with
    ``brief_service.finalize(ReviewAcknowledgement(...))``."""
    if not isinstance(lock, ExecutionLockV0_2):
        raise ValueError('a review needs a v0_2 execution lock')
    compiled = plan.compiled
    payload = json.loads(compiled.brief_content.model_dump_json())
    prepared = brief_service.create(payload)
    content = prepared.draft.content
    if content is None or prepared.issues or content.brief_sha256 != compiled.brief_sha256:
        raise ValueError('the draft content does not match the compiled brief')
    return ProgramReview(
        draft_id=prepared.draft.draft_id, draft_revision=prepared.draft.revision,
        brief_sha256=compiled.brief_sha256, semantic_sha256=compiled.semantic_sha256,
        lock=lock, lock_sha256=execution_lock_v0_2_sha256(lock), source_sha256=compiled.source_sha256)


async def authorize_run(review: ProgramReview, compiled, brief_service, brief_id: str, *,
                        adapter) -> RunAuthorization | PlanRefusal:
    """Refuse on the first failing check, before any build."""
    try:
        brief = brief_service.read_brief(brief_id)
    except KeyError:
        return _refuse('refused', 'not_finalized')
    if (brief.draft_id != review.draft_id or brief.draft_revision != review.draft_revision
            or not brief_service.is_finalized(review.draft_id, review.draft_revision)):
        return _refuse('refused', 'not_finalized')
    if not (brief.brief_sha256 == review.brief_sha256 == compiled.brief_sha256):
        return _refuse('refused', 'review_mismatch')
    if not (semantic_sha256(compiled.ir) == review.semantic_sha256 == compiled.semantic_sha256):
        return _refuse('refused', 'semantic_mismatch')
    if not isinstance(review.lock, ExecutionLockV0_2):
        return _refuse('refused', 'lock_version_unsupported')
    fresh, refusal = await _fresh_lock(compiled, adapter)
    if refusal is not None:
        return refusal
    if execution_lock_v0_2_sha256(fresh) != review.lock_sha256:
        return _refuse('unavailable', 'lock_mismatch')  # re-review
    if not _has_builder(compiled) or type(brief.content) is not type(compiled.brief_content):
        return _refuse('refused', 'candidate_mismatch')
    planned = _program_candidates(compiled.brief_content)
    if isinstance(brief.content, GeneratorBriefContent):
        candidates = generator_candidates(brief.content)
    else:
        from engineering.cantilever import CantileverAdapter
        candidates = CantileverAdapter().candidates(brief)
    if (len(candidates) != len(planned)
            or any(a.candidate_sha256 != b.candidate_sha256 or a.brief_sha256 != b.brief_sha256
                   for a, b in zip(candidates, planned))):
        return _refuse('refused', 'candidate_mismatch')
    ir = compiled.ir  # one read: the plans and the re-check below use the same object
    plans = _plans(ir, candidates)
    for p in plans:
        decision = adapter.supports(p)
        if not decision.supported:
            return PlanRefusal('unsupported', tuple(decision.reasons), decision.diagnostic_code, decision)
    # Security review LOW-2 (PR #138): the probe above awaited; re-bind the IR the plans were built
    # from to the reviewed semantic and lock hashes, with no await before the authorization is returned.
    if semantic_sha256(ir) != review.semantic_sha256:
        return _refuse('refused', 'semantic_mismatch')
    try:
        rebound = execution_lock_v0_2_sha256(execution_lock_v0_2(ir, fresh.backend_lock))
    except ValueError:  # includes pydantic ValidationError: a typed refusal, never an exception
        return _refuse('refused', 'lock_mismatch')
    if rebound != review.lock_sha256:
        return _refuse('refused', 'lock_mismatch')
    return RunAuthorization(review=review, brief_id=brief_id, backend_lock=fresh.backend_lock,
                            plans=plans, source_sha256=compiled.source_sha256)


async def run_program(review: ProgramReview, compiled, brief_service, brief_id: str, *, adapter,
                      context: ExecutionContext) -> tuple[Built, ...] | PlanRefusal:
    auth = await authorize_run(review, compiled, brief_service, brief_id, adapter=adapter)
    if isinstance(auth, PlanRefusal):
        return auth
    built = []
    for plan in auth.plans:
        built.append(await adapter.build(plan, context))
    return tuple(built)
