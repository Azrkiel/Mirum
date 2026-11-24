"""M1: program artifact exports are capped, streamed and release their slot (security review of PR #144).

Pure tests: a fake face behind the real program routes (no native kernel, no store), so they run wherever
fastapi and httpx are installed. The cap, the saturated envelope (429 admission_busy + Retry-After), the
slot release after success, failure and a mid-stream error, and byte/header identity of the streamed body.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip('fastapi')
httpx = pytest.importorskip('httpx')
from fastapi import FastAPI  # noqa: E402

from engineering import api_programs  # noqa: E402
from engineering.program import interface as api  # noqa: E402
from engineering.program.facade import ExportStream, ProgramInterface  # noqa: E402
from engineering.program.interface import InterfaceFailure  # noqa: E402

RID = 'a' * 32
V1 = api.API_PREFIX
PAYLOAD = bytes(range(256)) * 4099           # not a multiple of the 1 MiB block
URL = f'{V1}/revisions/{RID}/artifacts/step'
SOURCE_URL = f'{V1}/revisions/{RID}/artifacts/program-source'


def meta_for(data, fmt='step', name='model.step'):
    return api.ExportMetadata(revision_id=RID, fmt=fmt, filename=name, content_type='model/step',
                              size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


class Gated(io.BytesIO):
    """A file whose reads block until ``go`` is set (so exports stay in flight) or raise when ``boom``."""

    def __init__(self, data, go=None, boom=False):
        super().__init__(data)
        self.go, self.boom = go, boom

    def read(self, n=-1):
        if self.go is not None:
            assert self.go.wait(10)
        if self.boom:
            raise OSError('disk went away')
        return super().read(n)


class Download:
    def __init__(self, file):
        self.file, self.closed = file, 0

    def close(self):
        self.closed += 1
        self.file.close()


class FakeFace:
    def __init__(self, data=PAYLOAD, fail=None, go=None, boom=False):
        self.data, self.fail, self.go, self.boom = data, fail, go, boom
        self.downloads, self.calls = [], 0

    async def export_stream(self, request, *, client):
        self.calls += 1
        if self.fail is not None:
            raise InterfaceFailure(self.fail)
        download = Download(Gated(self.data, go=self.go if request.fmt == 'step' else None, boom=self.boom))
        self.downloads.append(download)
        return ExportStream(meta_for(self.data), download, api.MAX_EXPORT_BYTES)


class FakeLimiter:
    _key_func = staticmethod(lambda request: request.headers.get('x-test-client', 'alice'))

    def limit(self, rate):
        return lambda fn: fn


def make_app(face, monkeypatch, size='3'):
    monkeypatch.setenv(api_programs.EXPORT_CONCURRENCY_ENV, size)
    app = FastAPI()
    app.state.programs = face
    api_programs.attach_program_routes(app, FakeLimiter())
    return app


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://t')


def test_export_concurrency_env_parsing():
    parse = api_programs.export_concurrency
    assert parse({}) == 3
    assert parse({api_programs.EXPORT_CONCURRENCY_ENV: '5'}) == 5
    for bad in ('0', '-1', 'x', '17', '', '1.5'):
        assert parse({api_programs.EXPORT_CONCURRENCY_ENV: bad}) == 3


def test_gate_is_non_blocking_and_release_is_idempotent():
    gate = api_programs.ExportGate(1)
    first = gate.try_enter()
    assert first is not None and gate.try_enter() is None
    first()
    first()
    assert gate.active == 0 and gate.try_enter() is not None


def test_streamed_export_has_identical_bytes_and_headers(monkeypatch):
    face = FakeFace()
    app = make_app(face, monkeypatch)

    async def run():
        async with client_for(app) as c:
            return await c.get(URL)
    got = asyncio.run(run())
    assert got.status_code == 200 and got.content == PAYLOAD
    assert got.headers['x-content-sha256'] == hashlib.sha256(PAYLOAD).hexdigest()
    assert got.headers['cache-control'] == 'no-store'
    assert got.headers['content-disposition'] == 'attachment; filename="model.step"'
    assert got.headers['content-type'] == 'model/step'
    assert got.headers['content-length'] == str(len(PAYLOAD))
    assert face.downloads[0].closed == 1


def test_extra_concurrent_export_is_refused_busy_then_slot_is_reusable(monkeypatch):
    go = threading.Event()
    face = FakeFace(go=go)
    app = make_app(face, monkeypatch, size='3')

    async def run():
        async with client_for(app) as c:
            running = [asyncio.create_task(c.get(URL)) for _ in range(2)]
            for _ in range(500):
                if face.calls == 2:
                    break
                await asyncio.sleep(0.01)
            assert face.calls == 2
            refused = await c.get(URL)
            small = await c.get(SOURCE_URL)       # a source format is tiny: never counted against the cap
            go.set()
            done = await asyncio.gather(*running)
            again = await c.get(URL)
            return refused, small, done, again
    refused, small, done, again = asyncio.run(run())
    assert refused.status_code == 429
    body = refused.json()
    assert (body['kind'], body['code']) == ('busy', 'admission_busy')
    assert refused.headers['retry-after'] == str(api_programs.EXPORT_RETRY_AFTER_SECONDS)
    assert refused.headers['cache-control'] == 'no-store'
    assert small.status_code != 429
    assert face.calls == 4                          # the refused request never reached the face
    assert [r.status_code for r in done] == [200, 200] and all(r.content == PAYLOAD for r in done)
    assert again.status_code == 200 and again.content == PAYLOAD


def test_slot_is_released_after_failures(monkeypatch):
    face = FakeFace(fail=api.not_found())
    app = make_app(face, monkeypatch, size='1')

    async def run():
        async with client_for(app) as c:
            codes = [(await c.get(URL)).status_code for _ in range(3)]   # 3 > cap of 1: each reaches the face
            face.fail = None
            ok = await c.get(URL)
            return codes, ok
    codes, ok = asyncio.run(run())
    assert codes == [404, 404, 404] and ok.status_code == 200 and ok.content == PAYLOAD


def test_slot_and_lease_are_released_after_a_mid_stream_error(monkeypatch):
    face = FakeFace(boom=True)
    app = make_app(face, monkeypatch, size='1')

    async def run():
        async with client_for(app) as c:
            try:
                await c.get(URL)
            except Exception:  # noqa: BLE001 - the aborted body surfaces as an application error
                pass
            face.boom = False
            return await c.get(URL)
    again = asyncio.run(run())
    assert again.status_code == 200 and again.content == PAYLOAD
    assert face.downloads[0].closed == 1


# ------------------------------------------------------------------------------- ExportStream (facade)

def collect(stream):
    return b''.join(stream.blocks())


def test_stream_blocks_equal_the_file_and_close_is_idempotent():
    download = Download(io.BytesIO(PAYLOAD))
    stream = ExportStream(meta_for(PAYLOAD), download, api.MAX_EXPORT_BYTES)
    assert collect(stream) == PAYLOAD
    stream.close()
    stream.close()
    assert download.closed == 1


def test_stream_fails_closed_on_altered_bytes_and_on_a_size_overrun():
    altered = PAYLOAD[:-1] + bytes([PAYLOAD[-1] ^ 1])
    with pytest.raises(InterfaceFailure) as info:
        collect(ExportStream(meta_for(PAYLOAD), Download(io.BytesIO(altered)), api.MAX_EXPORT_BYTES))
    assert info.value.error.code == 'artifacts_altered'
    with pytest.raises(InterfaceFailure) as info:
        collect(ExportStream(meta_for(PAYLOAD), Download(io.BytesIO(PAYLOAD + b'x')), api.MAX_EXPORT_BYTES))
    assert info.value.error.kind == 'too_large'
    with pytest.raises(InterfaceFailure) as info:
        collect(ExportStream(meta_for(PAYLOAD), Download(io.BytesIO(PAYLOAD)), 10))
    assert info.value.error.kind == 'too_large'


def test_stream_never_holds_more_than_one_block():
    big = b'\x07' * (5 * 1024 * 1024 + 123)
    stream = ExportStream(meta_for(big), Download(io.BytesIO(big)), api.MAX_EXPORT_BYTES)
    sizes = [len(b) for b in stream.blocks()]
    assert max(sizes) <= 1024 * 1024 and sum(sizes) == len(big) and len(sizes) == 6


def facade_with(download):
    face = ProgramInterface.__new__(ProgramInterface)
    face.service = SimpleNamespace(open_artifact=lambda rid, fmt, client: download)
    return face


def lease(data, size, sha):
    download = Download(io.BytesIO(data))
    download.size, download.sha256 = size, sha
    download.filename, download.mime_type = 'model.step', 'model/step'
    return download


def test_export_stream_refuses_an_oversize_artifact_and_closes_the_lease():
    download = lease(b'', api.MAX_EXPORT_BYTES + 1, 'f' * 64)
    with pytest.raises(InterfaceFailure) as info:
        asyncio.run(facade_with(download).export_stream(api.ExportRequest(revision_id=RID, fmt='step'),
                                                        client='c'))
    assert info.value.error.kind == 'too_large' and download.closed == 1


def test_export_stream_metadata_and_digest_fallback_when_the_lease_has_none():
    download = lease(PAYLOAD, len(PAYLOAD), '')
    stream = asyncio.run(facade_with(download).export_stream(api.ExportRequest(revision_id=RID, fmt='step'),
                                                             client='c'))
    assert stream.metadata == meta_for(PAYLOAD) and collect(stream) == PAYLOAD
    stream.close()
