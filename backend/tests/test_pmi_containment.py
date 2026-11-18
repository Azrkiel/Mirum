"""PMI endpoints: path containment and sanitised client errors."""
import os
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


@pytest.fixture
def work(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.setattr(main, "_WORK_DIR", root)
    monkeypatch.setattr(main.limiter, "enabled", False)
    return root


@pytest.fixture
def client(work):
    with TestClient(main.app) as c:
        yield c


PROMPT = "bracket bore with surface finish Ra 0.8 and ISO 2768-m"


def post(client, step_path, part_id="bracket"):
    return client.post("/pmi/annotate", json={"prompt": PROMPT, "part_id": part_id, "step_path": step_path})


def assert_rejected(response, *leaks):
    body = response.text
    assert response.json()["status"] == "error"
    for leak in leaks:
        assert leak not in body
    assert "/tmp" not in body and "Traceback" not in body


def test_sibling_prefix_dir_rejected(client, work, tmp_path):
    evil = tmp_path / (work.name + "_evil")
    evil.mkdir()
    target = evil / "x.step"
    target.write_text("ISO-10303-21;")
    assert_rejected(post(client, str(target)), str(target))


def test_dotdot_traversal_rejected(client, work, tmp_path):
    outside = tmp_path / "outside.step"
    outside.write_text("ISO-10303-21;")
    path = str(work / ".." / "outside.step")
    assert_rejected(post(client, path), path)


@pytest.mark.skipif(os.name != "posix", reason="symlinks need POSIX")
def test_symlink_out_of_work_dir_rejected(client, work, tmp_path):
    outside = tmp_path / "secret.step"
    outside.write_text("ISO-10303-21;")
    link = work / "link.step"
    link.symlink_to(outside)
    assert_rejected(post(client, str(link)), str(link), str(outside))


def test_wrong_suffix_rejected(client, work):
    f = work / "x.txt"
    f.write_text("hi")
    assert_rejected(post(client, str(f)), str(f))


def test_directory_rejected(client, work):
    d = work / "dir.step"
    d.mkdir()
    assert_rejected(post(client, str(d)), str(d))


def test_missing_file_generic_no_path(client, work):
    path = str(work / "nope.step")
    response = post(client, path)
    assert_rejected(response, path, "nope.step")


def test_attach_failure_is_generic(client, work, monkeypatch):
    f = work / "ok.step"
    f.write_text("ISO-10303-21;")
    monkeypatch.setattr(main, "attach_pmi_to_step",
                        lambda *a, **k: (False, "/tmp/mirum/work/secret.step: boom XCAF"))
    response = post(client, str(f))
    assert_rejected(response, "secret.step", "boom", "XCAF")


def test_read_failure_is_generic(client, work, monkeypatch):
    f = work / "ok.step"
    f.write_text("ISO-10303-21;")
    monkeypatch.setattr(main, "attach_pmi_to_step",
                        lambda *a, **k: (True, str(work / "missing_out.step")))
    response = post(client, str(f))
    assert_rejected(response, "missing_out", "No such file", "Errno")


def test_valid_file_works_and_part_id_sanitised(client, work, monkeypatch):
    f = work / "ok.step"
    f.write_text("ISO-10303-21;")
    out = work / "out.step"

    def stub(src, pmi, dst):
        Path(dst).write_bytes(b"STEPDATA")
        return True, dst

    monkeypatch.setattr(main, "attach_pmi_to_step", stub)
    response = post(client, str(f), part_id="../../etc/pa\nss")
    body = response.json()
    assert body["status"] == "ok"
    name = body["filename"]
    assert "/" not in name and "\\" not in name and "\n" not in name and ".." not in name
    assert name.endswith("_pmi.step")


def test_sidecar_and_output_both_removed(client, work, monkeypatch):
    f = work / "ok.step"
    f.write_text("ISO-10303-21;")
    written = []

    def stub(src, pmi, dst):
        Path(dst).write_bytes(b"STEPDATA")
        sidecar = dst.replace(".step", ".pmi.json")
        Path(sidecar).write_text("{}")
        written.extend([dst, sidecar])
        return True, dst

    monkeypatch.setattr(main, "attach_pmi_to_step", stub)
    assert post(client, str(f)).json()["status"] == "ok"
    assert len(written) == 2
    assert not any(Path(p).exists() for p in written)


def test_overlong_prompt_and_step_path_rejected(client):
    r = client.post("/pmi/annotate", json={"prompt": "x" * 5001, "part_id": "a", "step_path": "a.step"})
    assert r.status_code == 422
    r = client.post("/pmi/extract", json={"prompt": "x" * 5001, "part_id": "a"})
    assert r.status_code == 422
    r = client.post("/pmi/annotate", json={"prompt": "Ra 0.8", "part_id": "a", "step_path": "a" * 4097})
    assert r.status_code == 422
