"""Real reviewed search; control failures explicitly inject an adapter seam."""
import asyncio
import json
import time

import pytest

from backend.tests.test_engineering_contracts import data, draft, finalized


@pytest.fixture(scope='module')
def reference_search():
    from engineering.search import search
    events = []
    async def progress(event):
        events.append(event)
    result = asyncio.run(search(finalized(), on_progress=progress))
    return result, events


def test_native_reference_search_and_replayable_history(reference_search):
    from engineering.contracts import AttemptRecord, Outcome
    result, events = reference_search
    assert result.state == 'selected'
    assert [a.outcome for a in result.attempts] == [Outcome.REJECTED, Outcome.REJECTED, Outcome.ACCEPTED]
    assert result.selected['candidate'].specification.height_mm == 15
    assert result.selected['report']['physics']['solution']['tip_displacement_mm'] == pytest.approx(0.677248677)
    assert [e.phase for e in events] == ['starting', 'attempt', 'starting', 'attempt', 'starting', 'attempt', 'selected']
    assert len(result.evaluations) == 3
    for attempt in result.attempts:
        assert AttemptRecord.model_validate_json(attempt.model_dump_json()) == attempt


def test_native_all_fail_is_only_no_reviewed_choice_passed():
    from engineering.search import search
    payload = data()
    payload['content']['requirements']['material']['allowable_stress_mpa'] = 1.0
    result = asyncio.run(search(finalized(draft(payload))))
    assert result.state == 'rejected' and result.selected is None
    assert len(result.attempts) == 3
    assert all(a.failures for a in result.attempts)
    assert all(not e['artifacts'] for e in result.evaluations)
    assert result.reasons == ('No candidate in the reviewed set passed',)


@pytest.mark.parametrize('mode,choices', [('fixed_design', [15.0]), ('bounded_thickness', [15.0, 18.0, 20.0])])
def test_native_stops_at_first_passing_choice(mode, choices):
    from engineering.search import search
    payload = data()
    payload['content'].update(mode=mode, candidate_heights_mm=choices)
    result = asyncio.run(search(finalized(draft(payload))))
    assert result.state == 'selected' and len(result.attempts) == 1
    assert result.attempts[0].candidate.specification.height_mm == 15


class NoNative:
    def __init__(self):
        from engineering.cantilever import CantileverAdapter
        self.real = CantileverAdapter()
        self.calls = 0
    def candidates(self, brief):
        return self.real.candidates(brief)
    async def evaluate(self, candidate):
        from engineering.service import EngineeringUnavailable
        self.calls += 1
        raise EngineeringUnavailable('Injected unavailable native-stage seam')


def test_unavailable_stage_stops_without_expansion():
    from engineering.search import search
    adapter = NoNative()
    result = asyncio.run(search(finalized(), adapter=adapter))
    assert result.state == 'unavailable' and adapter.calls == 1
    assert not result.attempts and result.selected is None


def test_whole_candidate_set_preflight_and_identity_binding():
    from engineering.search import search
    adapter = NoNative()
    original = adapter.candidates
    adapter.candidates = lambda b: tuple(reversed(original(b)))
    with pytest.raises(ValueError):
        asyncio.run(search(finalized(), adapter=adapter))
    assert adapter.calls == 0
    adapter.candidates = original
    b = finalized()
    bad = b.model_copy(update={'brief_sha256': 'f'*64})
    with pytest.raises(ValueError):
        asyncio.run(search(bad, adapter=adapter))
    assert adapter.calls == 0


def test_cancel_before_native_and_expired_deadline():
    from engineering.search import search
    adapter = NoNative()
    cancelled = asyncio.run(search(finalized(), adapter=adapter, cancelled=lambda: True))
    assert cancelled.state == 'cancelled' and adapter.calls == 0
    expired = asyncio.run(search(finalized(), adapter=adapter, deadline=time.monotonic()-1))
    assert expired.state == 'unavailable' and adapter.calls == 0


@pytest.mark.parametrize('control', ['hook', 'deadline', 'task'])
def test_inflight_control_cancels_and_awaits_owned_work(control):
    from engineering.search import search
    cleaned = []
    class Waiting(NoNative):
        async def evaluate(self, candidate):
            self.calls += 1
            try:
                await asyncio.sleep(30)
            finally:
                cleaned.append(True)
    adapter = Waiting()
    async def run():
        event = asyncio.Event()
        task = asyncio.create_task(search(finalized(), adapter=adapter,
            cancelled=event.is_set,
            deadline=time.monotonic()+0.15 if control == 'deadline' else None))
        await asyncio.sleep(0.1)
        if control == 'hook': event.set()
        if control == 'task': task.cancel()
        if control == 'task':
            with pytest.raises(asyncio.CancelledError): await task
        else:
            result = await task
            assert result.state == ('cancelled' if control == 'hook' else 'unavailable')
            assert result.selected is None
    asyncio.run(run())
    assert adapter.calls == 1 and cleaned == [True]


def test_progress_can_cancel_before_next_native_attempt(reference_search):
    from engineering.search import search
    result, _ = reference_search
    class Recorded(NoNative):
        async def evaluate(self, candidate):
            self.calls += 1
            return result.evaluations[0]
    adapter = Recorded()
    stop = False
    async def progress(event):
        nonlocal stop
        if event.phase == 'attempt': stop = True
    result = asyncio.run(search(finalized(), adapter=adapter, cancelled=lambda: stop, on_progress=progress))
    assert result.state == 'cancelled' and adapter.calls == 1
    assert len(result.attempts) == 1


def test_cancel_after_passing_attempt_exposes_no_accepted_bytes(reference_search):
    from engineering.search import search
    recorded, _ = reference_search
    class Recorded(NoNative):
        async def evaluate(self, candidate):
            entry = recorded.evaluations[self.calls]
            self.calls += 1
            return entry
    stop = False
    async def progress(event):
        nonlocal stop
        if event.phase == 'attempt' and event.ordinal == 2: stop = True
    result = asyncio.run(search(finalized(), adapter=Recorded(), cancelled=lambda: stop, on_progress=progress))
    assert result.state == 'cancelled' and result.selected is None
    assert all(not entry['artifacts'] for entry in result.evaluations)


def test_observer_failure_stops_before_native_work():
    from engineering.search import search
    adapter = NoNative()
    async def progress(_):
        raise RuntimeError('Injected observer failure')
    result = asyncio.run(search(finalized(), adapter=adapter, on_progress=progress))
    assert result.state == 'unavailable' and adapter.calls == 0
