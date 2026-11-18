"""PMI annotate: run_id resolution (no client paths) and sanitised client errors."""
import base64
import os
import uuid
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
def runs(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(main.artifacts, "_RUNS_DIR", root)
    return root


def make_run(runs, data=b"ISO-10303-21;ORIGINAL"):
    run_id = uuid.uuid4().hex
    d = runs / run_id
    d.mkdir()
    (d / "model.step").write_bytes(data)
    return run_id, d / "model.step"


@pytest.fixture
def client(work, runs):
    with TestClient(main.app) as c:
        yield c


PROMPT = "bracket bore with surface finish Ra 0.8 and ISO 2768-m"


def post(client, run_id, part_id="bracket"):
    return client.post("/pmi/annotate", json={"prompt": PROMPT, "part_id": part_id, "run_id": run_id})


def assert_rejected(response, *leaks):
    body = response.text
    assert response.json()["status"] == "error"
    for leak in leaks:
        assert leak not in body
    assert "/tmp" not in body and "Traceback" not in body


def copy_stub(src, pmi, dst):
    Path(dst).write_bytes(Path(src).read_bytes())
    return True, dst


def test_valid_run_id_annotates_and_artifact_untouched(client, work, runs, monkeypatch):
    run_id, art = make_run(runs)
    monkeypatch.setattr(main, "attach_pmi_to_step", copy_stub)
    r = post(client, run_id).json()
    assert r["status"] == "ok" and r["has_pmi"] is True
    assert base64.b64decode(r["data_b64"]) == b"ISO-10303-21;ORIGINAL"
    assert art.read_bytes() == b"ISO-10303-21;ORIGINAL"
    assert list(work.iterdir()) == []


def test_legacy_step_path_rejected_422(client, work):
    f = work / "victim.step"
    f.write_text("SECRET-GEOMETRY")
    r = client.post("/pmi/annotate", json={"prompt": PROMPT, "part_id": "a", "step_path": str(f)})
    assert r.status_code == 422
    assert "SECRET-GEOMETRY" not in r.text and "data_b64" not in r.text
    r = client.post("/pmi/annotate", json={"prompt": PROMPT, "part_id": "a", "run_id": "a" * 32, "step_path": str(f)})
    assert r.status_code == 422


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "a/b", "a" * 65, "", "A" * 32, ("a" * 32) + "\n", "a" * 31])
def test_malformed_run_id_generic(client, bad):
    r = post(client, bad)
    if r.status_code == 422:  # over max_length is a validation error; no echo either way
        assert bad not in r.text or bad == ""
        return
    assert_rejected(r, "Traceback")
    assert r.json()["detail"] == "Artifact not found or expired."
    if bad:
        assert bad not in r.text


def test_unknown_wellformed_run_id_generic(client):
    rid = uuid.uuid4().hex
    r = post(client, rid)
    assert_rejected(r, rid)
    assert r.json()["detail"] == "Artifact not found or expired."


def test_malformed_and_unknown_indistinguishable(client):
    a = post(client, "../x").json()
    b = post(client, uuid.uuid4().hex).json()
    assert a == b


def test_work_dir_step_not_reachable_by_name(client, work):
    (work / "victim.step").write_text("SECRET")
    r = post(client, "victim")
    assert_rejected(r, "SECRET", "victim")


def test_attach_failure_is_generic(client, runs, monkeypatch):
    run_id, _ = make_run(runs)
    monkeypatch.setattr(main, "attach_pmi_to_step",
                        lambda *a, **k: (False, "/tmp/mirum/work/secret.step: boom XCAF"))
    assert_rejected(post(client, run_id), "secret.step", "boom", "XCAF", run_id)


def test_read_failure_is_generic(client, work, runs, monkeypatch):
    run_id, _ = make_run(runs)
    monkeypatch.setattr(main, "attach_pmi_to_step",
                        lambda *a, **k: (True, str(work / "missing_out.step")))
    assert_rejected(post(client, run_id), "missing_out", "No such file", "Errno", run_id)


def test_part_id_sanitised(client, runs, monkeypatch):
    run_id, _ = make_run(runs)
    monkeypatch.setattr(main, "attach_pmi_to_step", copy_stub)
    body = post(client, run_id, part_id="../../etc/pa\nss").json()
    assert body["status"] == "ok"
    name = body["filename"]
    assert "/" not in name and "\\" not in name and "\n" not in name and ".." not in name
    assert name.endswith("_pmi.step")


def test_sidecar_and_output_both_removed(client, runs, monkeypatch):
    run_id, art = make_run(runs)
    written = []

    def stub(src, pmi, dst):
        Path(dst).write_bytes(b"STEPDATA")
        sidecar = dst.replace(".step", ".pmi.json")
        Path(sidecar).write_text("{}")
        written.extend([dst, sidecar])
        return True, dst

    monkeypatch.setattr(main, "attach_pmi_to_step", stub)
    assert post(client, run_id).json()["status"] == "ok"
    assert len(written) == 2
    assert not any(Path(p).exists() for p in written)
    assert art.read_bytes() == b"ISO-10303-21;ORIGINAL"


def test_overlong_prompt_and_run_id_rejected(client):
    r = client.post("/pmi/annotate", json={"prompt": "x" * 5001, "part_id": "a", "run_id": "a" * 32})
    assert r.status_code == 422
    r = client.post("/pmi/extract", json={"prompt": "x" * 5001, "part_id": "a"})
    assert r.status_code == 422
    r = client.post("/pmi/annotate", json={"prompt": "Ra 0.8", "part_id": "a", "run_id": "a" * 65})
    assert r.status_code == 422


def test_attach_failure_leaves_no_files(client, work, runs, monkeypatch):
    run_id, art = make_run(runs)

    def stub(src, pmi, dst):
        Path(dst).write_bytes(b"PARTIAL")
        Path(dst.replace(".step", ".pmi.json")).write_text("{}")
        return False, "/tmp/mirum/work/x.step: boom"

    monkeypatch.setattr(main, "attach_pmi_to_step", stub)
    r = post(client, run_id)
    assert_rejected(r, "boom", run_id)
    assert list(work.iterdir()) == []
    assert art.read_bytes() == b"ISO-10303-21;ORIGINAL"


def test_no_pmi_prompt_not_an_existence_oracle(client, runs):
    run_id, _ = make_run(runs)
    mk = lambda rid: client.post("/pmi/annotate", json={"prompt": "plain box", "part_id": "a", "run_id": rid})
    assert mk(run_id).json() == mk(uuid.uuid4().hex).json()


def test_overlong_run_id_422_no_server_paths(client):
    r = post(client, "a" * 65)
    assert r.status_code == 422
    assert "/tmp" not in r.text and "Traceback" not in r.text


def test_overlong_part_id_rejected(client, runs):
    run_id, _ = make_run(runs)
    r = post(client, run_id, part_id="p" * 129)
    assert r.status_code == 422
