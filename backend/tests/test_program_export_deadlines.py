"""M1 review follow-ups: stalled readers (H1), small exports take no slot (M2), cancelled open (L3)."""
from __future__ import annotations

import asyncio
import hashlib
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')

from engineering import api_programs  # noqa: E402
from engineering.program import interface as api  # noqa: E402
from engineering.program.facade import ProgramInterface  # noqa: E402
from tests.test_program_export_concurrency import (  # noqa: E402
    PAYLOAD, RID, URL, V1, FakeFace, client_for, lease, make_app,
)


def scope_for(path):
    return {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
            'method': 'GET', 'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': b'',
            'root_path': '', 'headers': [], 'client': ('127.0.0.1', 1), 'server': ('t', 80)}


async def receive_forever():
    await asyncio.Event().wait()


def test_a_reader_that_never_reads_loses_its_slot_after_the_send_deadline(monkeypatch):
    monkeypatch.setenv(api_programs.EXPORT_SEND_TIMEOUT_ENV, '0.2')
    face = FakeFace()
    app = make_app(face, monkeypatch, size='1')
    sent = []

    async def stalled(message):
        sent.append(message['type'])
        if message['type'] == 'http.response.body':
            await asyncio.Event().wait()           # accepts the head, never reads the body

    async def run():
        await asyncio.wait_for(app(scope_for(URL), receive_forever, stalled), 5)
        async with client_for(app) as c:           # the single slot must be free again
            return await c.get(URL)
    again = asyncio.run(run())
    assert sent == ['http.response.start', 'http.response.body']
    assert again.status_code == 200 and again.content == PAYLOAD
    assert face.downloads[0].closed == 1


def test_a_slow_reader_is_cut_at_the_total_deadline(monkeypatch):
    monkeypatch.setenv(api_programs.EXPORT_SEND_TIMEOUT_ENV, '5')
    monkeypatch.setenv(api_programs.EXPORT_DEADLINE_ENV, '0.3')
    face = FakeFace(data=b'\x01' * (6 * 1024 * 1024))
    app = make_app(face, monkeypatch, size='1')
    sent = []

    async def trickle(message):
        sent.append(message['type'])
        await asyncio.sleep(0.2)                   # each send succeeds, but the whole body takes > 1 s

    asyncio.run(asyncio.wait_for(app(scope_for(URL), receive_forever, trickle), 5))
    assert 0 < sent.count('http.response.body') < 6
    assert face.downloads[0].closed == 1 and face.downloads[0].file.closed


def test_small_exports_do_not_take_a_slot_but_large_ones_do(monkeypatch):
    monkeypatch.setenv(api_programs.EXPORT_GATE_MIN_BYTES_ENV, str(len(PAYLOAD) + 1))
    go = threading.Event()
    face = FakeFace(go=go)
    app = make_app(face, monkeypatch, size='1')
    report_url = f'{V1}/revisions/{RID}/artifacts/report'

    async def run():
        async with client_for(app) as c:
            held = asyncio.create_task(c.get(URL))              # a step export holds the only slot
            for _ in range(500):
                if face.calls == 1:
                    break
                await asyncio.sleep(0.01)
            small = await c.get(report_url)                      # report within the threshold: no slot needed
            face.data = PAYLOAD + b'x' * 10
            large = await c.get(report_url)                      # report above the threshold: gated
            go.set()
            await held
            return small, large
    small, large = asyncio.run(run())
    assert small.status_code == 200 and small.content == PAYLOAD
    assert large.status_code == 429 and large.json()['code'] == 'admission_busy'


def test_gate_min_bytes_env_parsing():
    parse = api_programs.export_gate_min_bytes
    assert parse({}) == 8 * 1024 * 1024
    assert parse({api_programs.EXPORT_GATE_MIN_BYTES_ENV: '1000'}) == 1000
    assert parse({api_programs.EXPORT_GATE_MIN_BYTES_ENV: '-4'}) == 8 * 1024 * 1024


def test_a_cancelled_open_still_closes_the_lease_it_returns():
    started, release = threading.Event(), threading.Event()
    download = lease(PAYLOAD, len(PAYLOAD), hashlib.sha256(PAYLOAD).hexdigest())

    def slow_open(rid, fmt, client):
        started.set()
        assert release.wait(10)
        return download
    face = ProgramInterface.__new__(ProgramInterface)
    face.service = SimpleNamespace(open_artifact=slow_open)

    async def run():
        task = asyncio.create_task(face.export_stream(api.ExportRequest(revision_id=RID, fmt='step'), client='c'))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        for _ in range(200):
            if download.closed:
                break
            await asyncio.sleep(0.01)
    asyncio.run(run())
    assert download.closed == 1
