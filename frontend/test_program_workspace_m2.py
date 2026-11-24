"""M1 review follow-ups (M2): header digest vs the revision record, click-to-fetch, friendly busy message."""
from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import program_workspace as pw  # noqa: E402
from test_program_workspace_artifacts import DATA, FakeResponse, serve  # noqa: E402


@pytest.fixture
def client():
    return pw.WorkspaceClient('http://backend', lambda: {})


class Fake:
    def artifact(self, rid, fmt, expected_sha=None):
        return bytes(DATA)


def test_header_digest_is_checked_against_the_revision_record(monkeypatch, client):
    serve(monkeypatch, FakeResponse(DATA))
    assert bytes(client.request('GET', '/x', artifact_bytes=10**7, expected_sha=sha256(DATA).hexdigest())) == DATA
    with pytest.raises(pw.WorkspaceError) as info:
        client.request('GET', '/x', artifact_bytes=10**7, expected_sha='1' * 64)
    assert info.value.status == 502 and 'not associated' in str(info.value)


def test_artifact_value_does_not_rehash(monkeypatch):
    calls = []
    real = pw.sha256
    monkeypatch.setattr(pw, 'sha256', lambda *a: calls.append(1) or real(*a))
    pw._artifact_value(Fake(), 'a' * 32, 'step', 'model.step', sha256(DATA).hexdigest(), {})
    assert calls == []


def test_a_foreign_artifact_is_refused_end_to_end(monkeypatch, client):
    state = {}
    serve(monkeypatch, FakeResponse(DATA))
    with pytest.raises(pw.WorkspaceError):
        pw._artifact_value(client, 'a' * 32, 'step', 'model.step', '1' * 64, state)
    assert 'mel_artifact_cache' not in state


@pytest.mark.parametrize('name,needs', [('preview.glb', True), ('model.step', True), ('package.zip', True),
                                         ('generator-body.step', True), ('generator-cleaned.stl', True),
                                         ('report.json', False), ('generator-bom.json', False),
                                         ('generator-report.json', False)])
def test_large_binary_artifacts_need_an_explicit_click(name, needs):
    assert pw._needs_click(name) is needs


def test_busy_message_is_friendly_only_for_admission_busy():
    busy = pw.WorkspaceError(429, {'kind': 'busy', 'code': 'admission_busy', 'message': 'The server is at capacity.'})
    text = pw._busy_message(busy).lower()
    assert 'busy' in text and 'retry' in text
    assert pw._busy_message(pw.WorkspaceError(502, {'message': 'x'})) is None
