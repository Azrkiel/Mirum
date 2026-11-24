"""M1: the program workspace downloads an artifact into one buffer and caches identity, never bytes."""
from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import program_workspace as pw  # noqa: E402


class FakeResponse:
    def __init__(self, content, ok=True, sha=None, status=200):
        self.content, self.ok, self.status_code = content, ok, status
        self.headers = {'X-Content-SHA256': sha if sha is not None else sha256(content).hexdigest()}
        self.chunk_sizes = []

    def iter_content(self, size):
        for start in range(0, len(self.content), size):
            piece = self.content[start:start + size]
            self.chunk_sizes.append(len(piece))
            yield piece

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def client():
    return pw.WorkspaceClient('http://backend', lambda: {})


def serve(monkeypatch, response):
    monkeypatch.setattr(pw.requests, 'request', lambda *a, **k: response)


DATA = bytes(range(251)) * 2000   # ~500 KB: several 64 KiB chunks


def test_artifact_bytes_round_trip_with_fingerprint(monkeypatch, client):
    response = FakeResponse(DATA)
    serve(monkeypatch, response)
    got = client.artifact('a' * 32, 'step')
    assert bytes(got) == DATA and len(response.chunk_sizes) > 1


def test_fingerprint_mismatch_is_refused(monkeypatch, client):
    serve(monkeypatch, FakeResponse(DATA, sha='0' * 64))
    with pytest.raises(pw.WorkspaceError) as info:
        client.artifact('a' * 32, 'step')
    assert info.value.status == 502 and 'fingerprint' in str(info.value)


def test_size_limit_is_refused_mid_stream(monkeypatch, client):
    serve(monkeypatch, FakeResponse(DATA))
    monkeypatch.setattr(pw, '_EXPORT_BYTES', len(DATA) - 1)
    with pytest.raises(pw.WorkspaceError) as info:
        client.artifact('a' * 32, 'step')
    assert info.value.status == 502 and 'size limit' in str(info.value)


def test_a_busy_envelope_surfaces_its_message(monkeypatch, client):
    import json
    body = json.dumps({'kind': 'busy', 'code': 'admission_busy', 'message': 'The server is at capacity.'}).encode()
    serve(monkeypatch, FakeResponse(body, ok=False, status=429))
    with pytest.raises(pw.WorkspaceError) as info:
        client.artifact('a' * 32, 'step')
    assert info.value.status == 429 and 'capacity' in str(info.value)


def held_bytes(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    if isinstance(value, (tuple, list)):
        return any(held_bytes(v) for v in value)
    if isinstance(value, dict):
        return any(held_bytes(v) for v in value.values())
    return False


class Fake:
    def __init__(self, data):
        self.data, self.calls = data, 0

    def artifact(self, rid, fmt):
        self.calls += 1
        return bytes(self.data)


def test_session_cache_holds_identity_only_and_refetches():
    state = {}
    fake, digest = Fake(DATA), sha256(DATA).hexdigest()
    first = pw._artifact_value(fake, 'a' * 32, 'step', 'model.step', digest, state)
    again = pw._artifact_value(fake, 'a' * 32, 'step', 'model.step', digest, state)
    assert first == again == DATA and fake.calls == 2
    assert state['mel_artifact_cache'] == (('a' * 32, 'model.step', digest), digest)
    assert not held_bytes(state)


def test_a_foreign_artifact_is_refused_and_not_remembered():
    state = {}
    with pytest.raises(pw.WorkspaceError):
        pw._artifact_value(Fake(DATA), 'a' * 32, 'step', 'model.step', '1' * 64, state)
    assert 'mel_artifact_cache' not in state
