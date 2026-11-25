"""M1 second review: throughput floor, per-client slot cap, disconnect, setup-failure cleanup."""
from __future__ import annotations

import asyncio
import threading

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')

from engineering import api_programs  # noqa: E402
from tests.test_program_export_concurrency import (  # noqa: E402
    PAYLOAD, URL, FakeFace, client_for, make_app,
)


async def receive_forever():
    await asyncio.Event().wait()


def test_per_client_cap_leaves_a_slot_for_others(monkeypatch):
    go = threading.Event()
    face = FakeFace(go=go)
    app = make_app(face, monkeypatch, size='3')          # cap per client = max(1, 3 - 1) = 2

    async def run():
        async with client_for(app) as c:
            a = [asyncio.create_task(c.get(URL, headers={'x-test-client': 'a'})) for _ in range(2)]
            for _ in range(500):
                if face.calls == 2:
                    break
                await asyncio.sleep(0.01)
            a_third = await c.get(URL, headers={'x-test-client': 'a'})        # a already holds its cap
            b = asyncio.create_task(c.get(URL, headers={'x-test-client': 'b'}))   # b still gets the last slot
            for _ in range(500):
                if face.calls == 3:
                    break
                await asyncio.sleep(0.01)
            b_second = await c.get(URL, headers={'x-test-client': 'b'})       # all 3 slots are taken
            go.set()
            done = await asyncio.gather(*a, b)
            return a_third, b_second, done
    a_third, b_second, done = asyncio.run(run())
    assert a_third.status_code == 429 and b_second.status_code == 429
    assert [r.status_code for r in done] == [200, 200, 200] and face.calls == 3


def test_gate_per_client_cap_arithmetic():
    assert api_programs.ExportGate(1).per_client == 1
    assert api_programs.ExportGate(2).per_client == 1
    assert api_programs.ExportGate(3).per_client == 2
    gate = api_programs.ExportGate(3)
    first, second = gate.try_enter('a'), gate.try_enter('a')
    assert gate.try_enter('a') is None and gate.try_enter('b') is not None
    first()
    first()
    assert gate.active == 2 and gate.try_enter('a') is not None
    second()


def test_min_throughput_env_parsing():
    parse = api_programs.export_min_bytes_per_second
    assert parse({}) == 256 * 1024
    assert parse({api_programs.EXPORT_MIN_RATE_ENV: '1000'}) == 1000
    assert parse({api_programs.EXPORT_MIN_RATE_ENV: '0'}) == 256 * 1024


def test_a_reader_trickling_below_the_floor_is_cut_and_its_slot_released(monkeypatch):
    monkeypatch.setenv(api_programs.EXPORT_MIN_RATE_ENV, str(1024 * 1024))        # 1 MiB/s floor
    monkeypatch.setattr(api_programs, 'EXPORT_RATE_WINDOW_SECONDS', 0.4)
    monkeypatch.setattr(api_programs, 'EXPORT_RATE_GRACE_SECONDS', 0.2)
    monkeypatch.setenv(api_programs.EXPORT_SEND_TIMEOUT_ENV, '5')
    face = FakeFace(data=b'\x02' * (6 * 1024 * 1024))
    app = make_app(face, monkeypatch, size='1')
    sent = []

    async def trickle(message):
        sent.append(message['type'])
        await asyncio.sleep(0.1)                         # 64 KiB per 0.1 s = 640 KiB/s < 1 MiB/s

    async def run():
        await asyncio.wait_for(app(scope(URL), receive_forever, trickle), 5)
        face.data = PAYLOAD
        async with client_for(app) as c:
            return await c.get(URL)
    from tests.test_program_export_deadlines import scope_for as scope
    again = asyncio.run(run())
    assert 0 < sent.count('http.response.body') < 90      # 6 MiB would be 96 sends
    assert face.downloads[0].closed == 1
    assert again.status_code == 200 and again.content == PAYLOAD


def test_a_reader_above_the_floor_is_served_in_full(monkeypatch):
    monkeypatch.setenv(api_programs.EXPORT_MIN_RATE_ENV, str(64 * 1024))
    monkeypatch.setattr(api_programs, 'EXPORT_RATE_WINDOW_SECONDS', 0.4)
    monkeypatch.setattr(api_programs, 'EXPORT_RATE_GRACE_SECONDS', 0.1)
    app = make_app(FakeFace(data=b'\x03' * (3 * 1024 * 1024)), monkeypatch, size='1')

    async def run():
        async with client_for(app) as c:
            return await c.get(URL)
    got = asyncio.run(run())
    assert got.status_code == 200 and len(got.content) == 3 * 1024 * 1024


def test_a_disconnect_stops_the_stream_and_releases(monkeypatch):
    face = FakeFace(data=b'\x04' * (6 * 1024 * 1024))
    app = make_app(face, monkeypatch, size='1')
    from tests.test_program_export_deadlines import scope_for as scope
    messages = [{'type': 'http.request', 'body': b'', 'more_body': False}]
    state = {'sends': 0}

    async def receive():
        if messages:
            return messages.pop(0)
        while state['sends'] < 3:
            await asyncio.sleep(0.01)
        return {'type': 'http.disconnect'}

    async def send(message):
        state['sends'] += 1
        await asyncio.sleep(0.05)

    asyncio.run(asyncio.wait_for(app(scope(URL), receive, send), 5))
    assert state['sends'] < 90 and face.downloads[0].closed == 1


def test_a_failure_after_the_stream_opens_releases_slot_and_lease(monkeypatch):
    face = FakeFace()
    app = make_app(face, monkeypatch, size='1')
    real = api_programs._ExportResponse

    def explode(*a, **k):
        raise RuntimeError('construction failed')

    async def run():
        async with client_for(app) as c:
            monkeypatch.setattr(api_programs, '_ExportResponse', explode)
            first = await c.get(URL)
            monkeypatch.setattr(api_programs, '_ExportResponse', real)
            return first, await c.get(URL)            # a leaked slot (cap 1) would answer 429
    first, second = asyncio.run(run())
    assert first.status_code >= 500 and face.downloads[0].closed == 1
    assert second.status_code == 200 and second.content == PAYLOAD


def test_a_second_cancel_during_cleanup_still_releases():
    """Round-3 L9: a cancel that lands while __call__ is awaiting its cleanup must not skip the release."""
    from tests.test_program_export_deadlines import scope_for as scope
    released = []

    async def run():
        streaming = asyncio.Event()

        async def body():
            try:
                yield b'x'
                streaming.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.Event().wait()            # cleanup that only a second cancel interrupts

        async def send(message):
            return None

        resp = api_programs._ExportResponse(body(), lambda: released.append(1), send_timeout=5, deadline=60,
                                            media_type='application/octet-stream')
        task = asyncio.create_task(resp(scope(URL), receive_forever, send))
        await asyncio.wait_for(streaming.wait(), 5)
        task.cancel()                                   # first cancel: __call__ enters its cleanup
        await asyncio.sleep(0.05)
        assert not task.done() and released == []      # blocked inside the cleanup await
        task.cancel()                                   # second cancel interrupts the cleanup
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    asyncio.run(run())
    assert released == [1]
