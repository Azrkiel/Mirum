"""K37: PicogkAdapter.inspect runs the STL reread on a dedicated bounded executor with a non-waiting slot.

Offline: _reread_mesh is replaced by a stub gated on threading.Event; no native runtime, no scipy.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
import time

import pytest

from backend.tests.test_program_kernel_picogk import (
    HEX_A, HEX_B, MESH, artifact, fake_provenance, fake_web, plan, reread_report)
from engineering.capabilities import REGEN
from engineering.program.adapters import picogk as picogk_module
from engineering.program.adapters.picogk import PicogkAdapter

DATA = b'k37 stl bytes'


def _wait_slot_free(timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not picogk_module._REREAD_SLOT.locked():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def free_slot():
    assert _wait_slot_free(), 'a previous test left the reread slot held'
    yield
    assert _wait_slot_free(), 'this test left the reread slot held'


class Gate:
    """A reread stub that blocks until released and records every entry."""

    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.lock = threading.Lock()
        self.calls = 0
        self.threads = []

    def __call__(self, data):
        with self.lock:
            self.calls += 1
            self.threads.append(threading.current_thread().name)
        self.entered.set()
        self.release.wait(10)
        return dict(MESH)


@pytest.fixture
def gate(monkeypatch):
    g = Gate()
    monkeypatch.setattr(picogk_module, '_reread_mesh', g)
    yield g
    g.release.set()   # never leave a blocked stub behind


def _inspect(adapter):
    return adapter.inspect(artifact(DATA), plan(), report=reread_report(DATA))


def test_deadline_is_tighter_than_the_overall_budget():
    assert 0 < picogk_module.INSPECT_DEADLINE_S < float(REGEN.resources.overall_seconds)


def test_busy_slot_never_queues_and_occupancy_is_one(gate):
    async def go():
        adapter = PicogkAdapter()
        first = asyncio.create_task(_inspect(adapter))
        assert await asyncio.to_thread(gate.entered.wait, 5)
        hostile = await asyncio.wait_for(
            asyncio.gather(*[_inspect(PicogkAdapter()) for _ in range(20)]), 5)
        gate.release.set()
        return hostile, await asyncio.wait_for(first, 5)

    hostile, first = asyncio.run(go())
    assert all((r.outcome, r.reasons) == ('unavailable', ('busy',)) for r in hostile)
    assert gate.calls == 1
    assert first.outcome == 'consistent', first.reasons


def test_timeout_keeps_the_slot_until_the_thread_finishes(gate, monkeypatch):
    monkeypatch.setattr(picogk_module, 'INSPECT_DEADLINE_S', 0.05)

    async def go():
        adapter = PicogkAdapter()
        t0 = time.monotonic()
        timed_out = await _inspect(adapter)
        elapsed = time.monotonic() - t0
        still_busy = await _inspect(adapter)
        gate.release.set()
        assert await asyncio.to_thread(_wait_slot_free)
        after = await _inspect(adapter)
        return timed_out, elapsed, still_busy, after

    timed_out, elapsed, still_busy, after = asyncio.run(go())
    assert (timed_out.outcome, timed_out.reasons) == ('unavailable', ('deadline_exceeded',))
    assert elapsed < 2.0
    assert (still_busy.outcome, still_busy.reasons) == ('unavailable', ('busy',))
    assert after.reasons not in (('busy',), ('deadline_exceeded',))
    assert after.outcome == 'consistent', after.reasons
    assert gate.calls == 2


def test_probe_and_default_executor_stay_free_while_the_slot_is_held(gate, monkeypatch, tmp_path):
    fake_web(monkeypatch, True)
    fake_provenance(monkeypatch, tmp_path)

    async def go():
        adapter = PicogkAdapter(runner=lambda: object())
        task = asyncio.create_task(_inspect(adapter))
        assert await asyncio.to_thread(gate.entered.wait, 5)
        side = await asyncio.wait_for(asyncio.to_thread(lambda: 'done'), 2)
        probe = await asyncio.wait_for(adapter.probe(), 5)
        gate.release.set()
        await asyncio.wait_for(task, 5)
        return side, probe

    side, probe = asyncio.run(go())
    assert side == 'done'
    assert probe.available is True, probe.reasons
    assert {v.version for v in probe.native_versions} >= {HEX_A, HEX_B}


def test_reread_runs_only_on_the_dedicated_executor(gate):
    async def go():
        task = asyncio.create_task(_inspect(PicogkAdapter()))
        assert await asyncio.to_thread(gate.entered.wait, 5)
        gate.release.set()
        await asyncio.wait_for(task, 5)

    asyncio.run(go())
    assert gate.threads and all(n.startswith('picogk-reread') for n in gate.threads)
    assert hashlib.sha256(DATA).hexdigest() == artifact(DATA).ref.sha256


def test_reread_exception_frees_the_slot(monkeypatch):
    def boom(data):
        raise ValueError('bad stl')

    monkeypatch.setattr(picogk_module, '_reread_mesh', boom)
    r = asyncio.run(_inspect(PicogkAdapter()))
    assert (r.outcome, r.reasons) == ('unavailable', ('evidence_invalid',))
    assert _wait_slot_free()
