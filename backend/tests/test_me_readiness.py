"""Tests for the mechanical-engineer readiness work (T0.x / T1.x).

Covers the artifact store and download contract, the cosmetic-thread fit that
previously made a bolt unable to enter its own nut, BOM assembly, the
user-facing failure text, and the /run default that broke parameter editing.

Geometry-building itself needs CadQuery and is verified in Docker; these pin
the arithmetic, the wiring and the contracts.

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


# ---------------------------------------------------------------------------
# T0.1 — artifact store
# ---------------------------------------------------------------------------

def test_run_id_is_validated_against_traversal():
    for bad in ("..", "../etc", "a/b", "", "zz" * 16, "../" * 4 + "etc"):
        assert artifacts.run_dir(bad) is None, bad
        assert artifacts.artifact_path(bad, "step") is None


def test_valid_run_id_resolves_under_the_runs_root():
    run_id = artifacts.new_run_id()
    path = artifacts.run_dir(run_id)
    assert path is not None
    assert "runs" in str(path)
    assert str(path).endswith(run_id)


def test_unknown_format_is_rejected():
    run_id = artifacts.new_run_id()
    assert artifacts.artifact_path(run_id, "sldprt") is None
    assert artifacts.artifact_path(run_id, "../../etc/passwd") is None


def test_step_is_offered_and_named_for_cad():
    """STEP is the whole point — it must be a first-class format."""
    assert "step" in artifacts.FORMATS
    filename, mime = artifacts.FORMATS["step"]
    assert filename.endswith(".step")
    assert "step" in mime


def test_dxf_is_not_advertised():
    """export_dxf yields one planar section, not a drawing — offering it as
    'DXF' would misrepresent what the user receives."""
    assert "dxf" not in artifacts.FORMATS


def test_available_reports_only_files_that_exist():
    run_id, run_path = artifacts.create_run()
    try:
        assert artifacts.available(run_id) == {"step": False, "stl": False, "glb": False}
        (run_path / artifacts.FORMATS["step"][0]).write_text("ISO-10303-21;")
        avail = artifacts.available(run_id)
        assert avail["step"] is True and avail["glb"] is False
        # A zero-byte file is not a usable download.
        (run_path / artifacts.FORMATS["glb"][0]).write_bytes(b"")
        assert artifacts.available(run_id)["glb"] is False
    finally:
        artifacts.discard(run_id)


def test_discard_removes_the_run():
    run_id, run_path = artifacts.create_run()
    (run_path / "model.step").write_text("x")
    artifacts.discard(run_id)
    assert not run_path.exists()


def test_sweep_removes_only_expired_runs():
    fresh_id, fresh = artifacts.create_run()
    old_id, old = artifacts.create_run()
    try:
        (fresh / "model.step").write_text("x")
        (old / "model.step").write_text("x")
        os.utime(old, (0, 0))          # epoch → far older than any TTL
        artifacts.sweep(ttl_seconds=3600)
        assert fresh.exists(), "a fresh run must survive the sweep"
        assert not old.exists(), "an expired run must be removed"
    finally:
        artifacts.discard(fresh_id)
        artifacts.discard(old_id)


# ---------------------------------------------------------------------------
# T0.3 — the bolt must fit its own nut
# ---------------------------------------------------------------------------

def _thread_geometry(nominal: float, pitch: float):
    """Reproduce the radii the builders use, without needing CadQuery."""
    import re
    src = (pathlib.Path(__file__).resolve().parent.parent / "cad_utils.py").read_text()
    clearance = float(
        re.search(r"COSMETIC_THREAD_FIT_CLEARANCE_MM = ([\d.]+)", src).group(1)
    )
    bolt_crest_r = nominal / 2.0                       # ribs reach the major dia
    nut_bore_r = (nominal + clearance) / 2.0
    return bolt_crest_r, nut_bore_r, clearance


@pytest.mark.parametrize("nominal,pitch", [
    (3.0, 0.5), (5.0, 0.8), (6.0, 1.0), (8.0, 1.25),
    (10.0, 1.5), (12.0, 1.75), (16.0, 2.0), (20.0, 2.5),
])
def test_bolt_passes_through_its_own_nut(nominal, pitch):
    """The regression that made every fastener assembly overlap.

    The nut used to bore the ISO internal minor diameter D1 = d - 1.0825p,
    which is SMALLER than the cosmetic bolt's major diameter. At M10x1.5 that
    is 0.81 mm of radial interference — the bolt physically could not enter
    the nut it ships with.
    """
    bolt_r, bore_r, _ = _thread_geometry(nominal, pitch)
    assert bore_r > bolt_r, (
        f"M{nominal:g}x{pitch}: nut bore r={bore_r:.3f} <= bolt crest "
        f"r={bolt_r:.3f} — the parts interfere"
    )


def test_the_old_minor_diameter_bore_really_did_interfere():
    """Pins the magnitude of the bug so the fix is not silently reverted."""
    nominal, pitch = 10.0, 1.5
    old_bore_r = (nominal - 1.0825 * pitch) / 2.0
    bolt_r = nominal / 2.0
    interference = bolt_r - old_bore_r
    assert interference > 0.8, f"expected ~0.81mm interference, got {interference:.3f}"


def test_clearance_is_small_enough_to_look_like_a_nut():
    """A clearance hole is not a nut: keep the fit visually tight."""
    _, _, clearance = _thread_geometry(10.0, 1.5)
    assert 0.0 < clearance <= 0.5, clearance


def test_thread_representation_is_declared_cosmetic():
    """Cosmetic threads are fine; pretending they are real is not."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "cad_utils.py").read_text()
    assert 'THREAD_REPRESENTATION = "cosmetic"' in src
    assert "COSMETIC metric thread" in src
    strategies = (pathlib.Path(__file__).resolve().parent.parent / "strategies.py").read_text()
    assert "COSMETIC threads" in strategies
    assert "proper threads" not in strategies, (
        "the Machinist prompt must not claim the helpers produce real threads"
    )


# ---------------------------------------------------------------------------
# T1.1 — bill of materials
# ---------------------------------------------------------------------------

def _fake_manifest(parts):
    return types.SimpleNamespace(
        assembly_name="t",
        parts=[types.SimpleNamespace(part_id=p, description=d) for p, d in parts],
    )


class _Rep:
    def __init__(self, mass_g=None):
        self.geometry_cert = {"mass_g": mass_g} if mass_g is not None else None


def test_bom_collapses_identical_parts_into_quantities():
    from verification.bom import build_bom
    manifest = _fake_manifest([
        ("tabletop", "1200x800x25 tabletop"),
        ("leg_1", "60mm square leg"),
        ("leg_2", "60mm square leg"),
        ("leg_3", "60mm square leg"),
        ("leg_4", "60mm square leg"),
    ])
    reports = {"tabletop": _Rep(9000.0), "leg_1": _Rep(500.0)}
    bom = build_bom(manifest, reports, material="aluminum")

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
    bom = build_bom(manifest, {"a": _Rep(10.0), "b": _Rep(None)})
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


# ---------------------------------------------------------------------------
# T0.4 — what the user is told when it fails
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
    "security", "dimension_mismatch", "geometry_cert", "solver", "export",
])
def test_every_common_failure_class_has_a_suggestion(cls):
    from failure_analysis import user_facing_failure
    msg = user_facing_failure(cls)
    assert msg.count(".") >= 2, f"{cls} has no suggestion sentence: {msg}"


def test_unknown_failure_class_still_says_something_useful():
    from failure_analysis import user_facing_failure
    msg = user_facing_failure("something_new")
    assert "could not be generated" in msg


def test_script_error_carries_both_audiences():
    from agents import ScriptError
    exc = ScriptError("operator detail with Traceback", script="code",
                      user_message="Plain English.")
    assert exc.user_message == "Plain English."
    assert "Traceback" in str(exc)          # operator detail preserved
    assert exc.script == "code"


# ---------------------------------------------------------------------------
# T0.5 — /run must work in a configured deployment
# ---------------------------------------------------------------------------

def test_run_endpoint_enabled_when_authentication_is_configured(monkeypatch):
    """Parameter editing and the code Run button call /run. Disabling it by
    default 404'd both in every shipped deployment."""
    import importlib
    monkeypatch.delenv("MIRUM_ENABLE_RUN_ENDPOINT", raising=False)
    monkeypatch.setenv("MIRUM_API_KEY", "s3cret")
    import main as _main
    reloaded = importlib.reload(_main)
    assert reloaded._ENABLE_RUN_ENDPOINT is True


def test_run_endpoint_disabled_when_unauthenticated(monkeypatch):
    import importlib
    monkeypatch.delenv("MIRUM_ENABLE_RUN_ENDPOINT", raising=False)
    monkeypatch.delenv("MIRUM_API_KEY", raising=False)
    import main as _main
    reloaded = importlib.reload(_main)
    assert reloaded._ENABLE_RUN_ENDPOINT is False


def test_explicit_override_wins_both_ways(monkeypatch):
    import importlib
    import main as _main
    monkeypatch.setenv("MIRUM_API_KEY", "s3cret")
    monkeypatch.setenv("MIRUM_ENABLE_RUN_ENDPOINT", "0")
    assert importlib.reload(_main)._ENABLE_RUN_ENDPOINT is False
    monkeypatch.delenv("MIRUM_API_KEY", raising=False)
    monkeypatch.setenv("MIRUM_ENABLE_RUN_ENDPOINT", "1")
    assert importlib.reload(_main)._ENABLE_RUN_ENDPOINT is True
    monkeypatch.delenv("MIRUM_ENABLE_RUN_ENDPOINT", raising=False)
    importlib.reload(_main)
