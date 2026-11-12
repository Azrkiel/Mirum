"""Deterministic V1 search over an unchanged, fully reviewed candidate set.

Selection is a library result, not a committed accepted run. B08 assesses
evidence; B09/B10 own admission, lifecycle, storage and atomic publication.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import time
from typing import Awaitable, Callable, Literal

from .capabilities import CAPABILITY, CapabilityAdapter, get_adapter
from .contracts import (AttemptRecord, CandidateSpec, ContractModel, EngineeringBrief,
                        Outcome, Sha256)
from .service import EngineeringUnavailable


class SearchProgress(ContractModel):
    phase: Literal['starting', 'attempt', 'selected', 'rejected', 'unavailable', 'cancelled']
    ordinal: int | None = None
    candidate_sha256: Sha256 | None = None
    attempt: AttemptRecord | None = None


@dataclass(frozen=True)
class SearchResult:
    state: Literal['selected', 'rejected', 'unavailable', 'cancelled']
    attempts: tuple[AttemptRecord, ...]
    evaluations: tuple[dict, ...]
    selected: dict | None
    reasons: tuple[str, ...]
    selection_rule: str = CAPABILITY.selection_rule


class _Stopped(Exception):
    def __init__(self, state: str, reason: str):
        self.state, self.reason = state, reason


def _check_control(cancelled, deadline):
    if cancelled is not None and cancelled():
        raise _Stopped('cancelled', 'Cancellation requested before selection commit')
    if time.monotonic() >= deadline:
        raise _Stopped('unavailable', 'Overall engineering deadline exceeded')


async def _cancel_owned(task):
    if not task.done():
        task.cancel()
    cleanup = asyncio.gather(task, return_exceptions=True)
    # Repeated caller cancellation cannot detach the owned native cleanup.
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue


async def _controlled(awaitable, *, cancelled, deadline):
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            _check_control(cancelled, deadline)
            if task.done():
                return task.result()
            await asyncio.wait({task}, timeout=min(0.05, max(0, deadline-time.monotonic())))
    except BaseException:
        await _cancel_owned(task)
        raise


async def search(brief: EngineeringBrief, *, adapter: CapabilityAdapter | None = None,
                 cancelled: Callable[[], bool] | None = None, deadline: float | None = None,
                 on_progress: Callable[[SearchProgress], Awaitable[None]] | None = None) -> SearchResult:
    brief = EngineeringBrief.model_validate(brief.model_dump())
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (float, int))
                                 or not math.isfinite(deadline)):
        raise ValueError('Deadline must be a finite monotonic time')
    deadline = deadline if deadline is not None else time.monotonic() + CAPABILITY.resources.overall_seconds
    reference = brief.content.capability
    adapter = adapter or get_adapter(reference.id, reference.version,
        numerical_policy_version=reference.numerical_policy_version,
        resource_policy_version=reference.resource_policy_version)
    # Do not sort/repair a different adapter list at execution time.
    candidates = adapter.candidates(brief)
    expected = tuple(CandidateSpec.from_brief(brief, h) for h in brief.content.candidate_heights_mm)
    if type(candidates) is not tuple or candidates != expected:
        raise ValueError('Execution must use exactly the finalized ascending candidate set')
    candidates = tuple(CandidateSpec.model_validate(c.model_dump()) for c in candidates)
    attempts, evaluations = [], []

    async def progress(event):
        if on_progress is not None:
            await _controlled(on_progress(event), cancelled=cancelled, deadline=deadline)

    async def finish(state, reasons=(), selected=None):
        if on_progress is not None:
            # Terminal notification is best effort only for control stops; it
            # must not restart work or delay cancellation/deadline cleanup.
            if state not in ('cancelled', 'unavailable'):
                await progress(SearchProgress(phase=state))
        retained = tuple(evaluations) if state == 'selected' else tuple(
            dict(entry, artifacts={}) for entry in evaluations)
        return SearchResult(state, tuple(attempts), retained, selected, reasons)

    try:
        for ordinal, candidate in enumerate(candidates):
            _check_control(cancelled, deadline)
            await progress(SearchProgress(phase='starting', ordinal=ordinal, candidate_sha256=candidate.candidate_sha256))
            _check_control(cancelled, deadline)
            result = await _controlled(adapter.evaluate(candidate), cancelled=cancelled, deadline=deadline)
            if result['candidate'] != candidate:
                raise EngineeringUnavailable('Adapter returned another reviewed candidate')
            outcome = Outcome(result['outcome'])
            if outcome == Outcome.NOT_EVALUATED:
                raise EngineeringUnavailable('Required candidate evidence unavailable')
            attempt = AttemptRecord(ordinal=ordinal, candidate=candidate, outcome=outcome,
                                    checks=result['checks'], failures=tuple(result['report']['failures']))
            attempts.append(attempt)
            evaluations.append(result)
            await progress(SearchProgress(phase='attempt', ordinal=ordinal,
                                          candidate_sha256=candidate.candidate_sha256, attempt=attempt))
            _check_control(cancelled, deadline)
            if outcome == Outcome.ACCEPTED:
                return await finish('selected', selected=result)
        return await finish('rejected', ('No candidate in the reviewed set passed',))
    except _Stopped as stop:
        return await finish(stop.state, (stop.reason,))
    except Exception:
        return await finish('unavailable', ('Required candidate evidence unavailable or malformed',))
