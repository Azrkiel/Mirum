"""Offline tests for the R4 evidence writer."""

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.evidence import Evidence
from harness import evidence as evidence_module


def test_write_creates_deterministic_utf8_json(tmp_path: Path) -> None:
    def clock() -> datetime:
        return datetime(2026, 11, 19, 8, 30, tzinfo=timezone(timedelta(hours=-5)))

    out_dir = tmp_path / "nested" / "artifacts"
    evidence = Evidence(out_dir, clock=clock, redact=[""])
    evidence.set_source("commit", "refs/heads/main")
    evidence.set_images({"frontend": "image-b", "backend": "image-a"}, {"backend": "base"})
    evidence.set_pip_freeze("backend", "a==1\nb==2\n")
    evidence.add_note("résumé", "café")
    for nodeid, outcome in [
        ("z", "skipped"),
        ("c", "error"),
        ("b", "failed"),
        ("a", "passed"),
    ]:
        evidence.record_test(nodeid, outcome, 0.25)

    expected = {
        "schema_version": "mirum_e2e_evidence_v1",
        "generated_at": "2026-11-19T13:30:00+00:00",
        "source": {"sha": "commit", "ref": "refs/heads/main"},
        "image_ids": {"backend": "image-a", "frontend": "image-b"},
        "base_digests": {"backend": "base"},
        "lock_hashes": {},
        "pip_freeze": {"backend": "a==1\nb==2\n"},
        "tests": [
            {"nodeid": "a", "outcome": "passed", "duration_s": 0.25},
            {"nodeid": "b", "outcome": "failed", "duration_s": 0.25},
            {"nodeid": "c", "outcome": "error", "duration_s": 0.25},
            {"nodeid": "z", "outcome": "skipped", "duration_s": 0.25},
        ],
        "screenshots": [],
        "notes": {"résumé": "café"},
    }
    assert evidence.to_dict() == expected
    path = evidence.write()
    assert path == out_dir / "evidence.json"
    expected_bytes = (
        json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    assert path.read_bytes() == expected_bytes
    assert evidence.write().read_bytes() == expected_bytes


def test_default_clock_is_utc(tmp_path: Path) -> None:
    generated_at = datetime.fromisoformat(Evidence(tmp_path).to_dict()["generated_at"])
    assert generated_at.utcoffset() == timedelta(0)


def test_hashes_lock_bytes_by_repo_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    lock_path = repo_root / "e2e" / "requirements.lock"
    lock_path.parent.mkdir(parents=True)
    contents = b"dependency==1\r\n"
    lock_path.write_bytes(contents)
    monkeypatch.setattr(evidence_module, "_REPO_ROOT", repo_root)
    evidence = Evidence(tmp_path / "artifacts")

    evidence.add_lock_hash(lock_path)

    assert evidence.to_dict()["lock_hashes"] == {
        "e2e/requirements.lock": hashlib.sha256(contents).hexdigest()
    }


def test_rejects_unknown_outcome(tmp_path: Path) -> None:
    evidence = Evidence(tmp_path)
    with pytest.raises(ValueError):
        evidence.record_test("test_flow.py::test_case", "xfailed", 0.0)
    assert evidence.to_dict()["tests"] == []


def test_sorts_and_hashes_screenshots(tmp_path: Path) -> None:
    evidence = Evidence(tmp_path)
    first = tmp_path / "flows" / "a.png"
    first.parent.mkdir()
    second = tmp_path / "z.png"
    first.write_bytes(b"\x89PNG\r\n")
    second.write_bytes(b"second")

    evidence.add_screenshot(second, flow="first call")
    evidence.add_screenshot(first, flow="second call")

    assert evidence.to_dict()["screenshots"] == [
        {
            "flow": "second call",
            "path": "flows/a.png",
            "sha256": hashlib.sha256(b"\x89PNG\r\n").hexdigest(),
            "bytes": 6,
        },
        {
            "flow": "first call",
            "path": "z.png",
            "sha256": hashlib.sha256(b"second").hexdigest(),
            "bytes": 6,
        },
    ]


def test_rejects_screenshots_outside_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()
    (tmp_path / "outside.png").write_bytes(b"outside")
    evidence = Evidence(out_dir)

    with pytest.raises(ValueError):
        evidence.add_screenshot(out_dir / ".." / "outside.png", flow="outside")
    assert evidence.to_dict()["screenshots"] == []


@pytest.mark.parametrize("in_key", [False, True])
@pytest.mark.parametrize("secret", ["0123456789abcdef" * 4, 'café"token', "e"])
def test_write_rejects_redact_values(
    tmp_path: Path, in_key: bool, secret: str
) -> None:
    out_dir = tmp_path / "missing"
    evidence = Evidence(out_dir, redact=["", secret])
    if in_key:
        evidence.add_note(f"prefix-{secret}-suffix", "value")
    else:
        evidence.add_note("note", f"prefix-{secret}-suffix")

    with pytest.raises(ValueError) as error:
        evidence.write()
    assert secret not in str(error.value)
    assert not out_dir.exists()


def test_write_replaces_atomically_and_cleans_up_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.json"
    path.write_bytes(b"previous evidence")
    evidence = Evidence(tmp_path)
    replace = os.replace

    def blocked_replace(source: Path, destination: Path) -> None:
        assert source.parent == path.parent
        assert destination == path
        assert source != path
        assert json.loads(source.read_bytes())["schema_version"] == "mirum_e2e_evidence_v1"
        assert path.read_bytes() == b"previous evidence"
        raise OSError("replace blocked")

    monkeypatch.setattr(os, "replace", blocked_replace)
    with pytest.raises(OSError, match="replace blocked"):
        evidence.write()
    assert path.read_bytes() == b"previous evidence"
    assert list(tmp_path.iterdir()) == [path]

    monkeypatch.setattr(os, "replace", replace)
    assert evidence.write() == path
    assert json.loads(path.read_bytes())["schema_version"] == "mirum_e2e_evidence_v1"
    assert list(tmp_path.iterdir()) == [path]
