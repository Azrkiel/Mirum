"""Integration tests for the verification-driven critic loop (P1.2).

Monkeypatches every external boundary (Gemini, sandbox subprocess, STEP
export, OCC certificate) and drives run_critic_loop through scripted
attempt sequences, asserting:

  - a verification failure (dimensions/cert) consumes a retry and feeds
    measured feedback + the previous code into the next Machinist prompt,
  - the final attempt delivers-with-warnings (degraded report) instead of
    silently succeeding or hard-failing,
  - export failure consumes a retry inside the loop (previously it raised
    outside the loop with no retry),
  - only fully-verified code is admitted to the retrieval index,
  - telemetry logs exactly one record per attempt with the check report.

No Gemini key, CadQuery, or OCC required.
"""

import asyncio
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import agents  # noqa: E402
from agents import ScriptError, run_critic_loop  # noqa: E402
from schemas import DesignRequirements, PartDefinition  # noqa: E402


PART = PartDefinition(
    part_id="base_plate",
    description="A 100mm x 60mm x 5mm rectangular base plate",
    anchor_tags=[">Z", "<Z"],
)

REQS = DesignRequirements(
    primary_function="a base plate",
    key_dimensions={"total_length_mm": 100.0, "width_mm": 60.0, "height_mm": 5.0},
    connecting_interfaces=[],
    confidence=0.9,
)


class Harness:
    """Scripted stand-ins for every external boundary of the critic loop."""

    def __init__(self, monkeypatch, *, compile_results, certs,
                 export_fail_attempts=frozenset()):
        self.machinist_calls: list[dict] = []
        self.logged: list[dict] = []
        self.indexed: list[str] = []
        self.attempt = 0
        self._compile_results = compile_results
        self._certs = certs
        self._export_fail = set(export_fail_attempts)

        async def fake_classify(description):
            return {"domain": "A"}

        async def fake_machinist(part_def, domain="A", error_context="",
                                 retry_failure_class=None, retry_techniques=None):
            self.machinist_calls.append({
                "error_context": error_context,
                "retry_failure_class": retry_failure_class,
            })
            return f"import cadquery as cq\nresult = cq.Workplane().box(1,1,1)  # v{len(self.machinist_calls)}"

        async def fake_execute(code):
            self.attempt += 1
            res = self._compile_results[self.attempt - 1]
            return ({"status": "success", "output": ""} if res is True
                    else {"status": "error", "traceback": res})

        async def fake_save_step(part_id, script):
            if self.attempt in self._export_fail:
                raise ScriptError("export blew up", script=script)
            return f"/tmp/fake_{part_id}_{self.attempt}.step"

        def fake_certify(step_path, propulsion_spec=None):
            return self._certs[self.attempt - 1]

        def fake_log_attempt(**kwargs):
            self.logged.append(kwargs)

        def fake_add_to_index(description, code, domain):
            self.indexed.append(code)

        monkeypatch.setattr(agents, "classify_part", fake_classify)
        monkeypatch.setattr(agents, "run_machinist", fake_machinist)
        monkeypatch.setattr(agents, "execute_cad_script", fake_execute)
        monkeypatch.setattr(agents, "_save_part_step", fake_save_step)
        monkeypatch.setattr(agents, "log_attempt", fake_log_attempt)
        monkeypatch.setattr(agents, "add_to_index", fake_add_to_index)
        monkeypatch.setattr(agents, "_remove_quiet", lambda p: None)
        import verification.geometry as vg
        monkeypatch.setattr(vg, "certify_geometry", fake_certify)


GOOD_CERT = {
    "passed": True, "failures": [],
    "volume_mm3": 30000.0, "surface_area_mm2": 13600.0,
    "bounding_box": {"x_mm": 100.0, "y_mm": 60.0, "z_mm": 5.0},
    "checks": {},
}
BAD_BBOX_CERT = dict(GOOD_CERT, bounding_box={"x_mm": 50.0, "y_mm": 60.0, "z_mm": 5.0})
BAD_CERT = dict(GOOD_CERT, passed=False, failures=["Volume is 0.0000 mm³ (expected > 0)"])


def test_dimension_failure_consumes_retry_then_passes(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=[True, True],
                certs=[BAD_BBOX_CERT, GOOD_CERT])
    code, step, report = asyncio.run(
        run_critic_loop(PART, max_retries=3, requirements=REQS)
    )
    assert report.passed and not report.degraded
    assert len(h.machinist_calls) == 2
    # Retry prompt carries the measured failure AND the previous code.
    retry_ctx = h.machinist_calls[1]["error_context"]
    assert "dimension_mismatch" in retry_ctx
    assert "total_length_mm" in retry_ctx
    assert "Your previous code" in retry_ctx
    assert "# v1" in retry_ctx
    assert h.machinist_calls[1]["retry_failure_class"] == "dimension_mismatch"
    # Only the verified attempt reached the index.
    assert h.indexed == [code]
    # One telemetry record per attempt: fail then success, both with reports.
    assert [r["success"] for r in h.logged] == [False, True]
    assert all(r["check_report"] is not None for r in h.logged)
    assert all(r["description"] == PART.description for r in h.logged)


def test_final_attempt_delivers_degraded_not_silent(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=[True, True],
                certs=[BAD_CERT, BAD_CERT])
    code, step, report = asyncio.run(
        run_critic_loop(PART, max_retries=2, requirements=REQS)
    )
    assert report.degraded
    assert not report.passed
    assert step  # geometry still delivered for inspection
    assert h.indexed == []  # degraded code NEVER becomes a few-shot example
    assert [r["success"] for r in h.logged] == [False, False]


def test_compile_failure_all_attempts_raises_with_context(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=["Traceback ...\nStdFail_NotDone: fillet", "SyntaxError: bad"],
                certs=[GOOD_CERT, GOOD_CERT])
    with pytest.raises(ScriptError):
        asyncio.run(run_critic_loop(PART, max_retries=2, requirements=REQS))
    # Second machinist call saw the first attempt's code + history.
    retry_ctx = h.machinist_calls[1]["error_context"]
    assert "Your previous code" in retry_ctx
    assert "# v1" in retry_ctx


def test_history_accumulates_across_mixed_failures(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=["Traceback ...\nStdFail_NotDone", True, True],
                certs=[GOOD_CERT, BAD_BBOX_CERT, GOOD_CERT])
    code, step, report = asyncio.run(
        run_critic_loop(PART, max_retries=3, requirements=REQS)
    )
    assert report.passed
    third_ctx = h.machinist_calls[2]["error_context"]
    # The third prompt remembers BOTH prior failure modes.
    assert "attempt 1: compile failure" in third_ctx
    assert "attempt 2:" in third_ctx and "dimensions" in third_ctx


def test_export_failure_consumes_retry_inside_loop(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=[True, True],
                certs=[GOOD_CERT, GOOD_CERT],
                export_fail_attempts={1})
    code, step, report = asyncio.run(
        run_critic_loop(PART, max_retries=3, requirements=REQS)
    )
    assert report.passed
    assert len(h.machinist_calls) == 2
    assert h.machinist_calls[1]["retry_failure_class"] == "export"


def test_export_failure_on_final_attempt_raises(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=[True],
                certs=[GOOD_CERT],
                export_fail_attempts={1})
    with pytest.raises(ScriptError):
        asyncio.run(run_critic_loop(PART, max_retries=1, requirements=REQS))


def test_classifier_failure_defaults_to_domain_a(monkeypatch):
    h = Harness(monkeypatch, compile_results=[True], certs=[GOOD_CERT])

    async def broken_classify(description):
        raise RuntimeError("429 quota")

    monkeypatch.setattr(agents, "classify_part", broken_classify)
    code, step, report = asyncio.run(
        run_critic_loop(PART, max_retries=1, requirements=REQS)
    )
    assert report.passed
    assert h.logged[0]["domain"] == "A"


def test_save_step_false_verifies_but_returns_no_path(monkeypatch):
    h = Harness(monkeypatch, compile_results=[True], certs=[BAD_BBOX_CERT])
    code, step, report = asyncio.run(
        run_critic_loop(PART, max_retries=1, save_step=False, requirements=REQS)
    )
    # Verification RAN (previously save_step=False skipped it entirely)…
    assert report.degraded
    dims = report.get("dimensions")
    assert dims is not None and not dims.passed
    # …but no STEP path is returned.
    assert step == ""


# ---------------------------------------------------------------------------
# _pipeline temp-file cleanup (regression: STEP leak on early-return paths)
# ---------------------------------------------------------------------------
#
# The single-part branch keeps the critic loop's verified STEP so the aero and
# certificate stages can reuse it. Nine early returns sit between creating that
# file and the end of the generator, so cleanup lives in a finally: on the
# _pipeline wrapper rather than on the happy path. Domain D exercises this
# constantly — the planner rule forces wings into a single part.

import os as _os          # noqa: E402
import tempfile           # noqa: E402

import main as _main      # noqa: E402
from verification.report import CheckReport, CheckResult  # noqa: E402


def _stub_pipeline(monkeypatch, step_path, *, export_status="error"):
    """Stub every boundary _pipeline touches; return the manifest it will use."""
    from schemas import AssemblyManifest, MatingRule, PartDefinition

    manifest = AssemblyManifest(
        assembly_name="leak_probe",
        parts=[PartDefinition(part_id="wing_segment_assembly",
                              description="A NACA 2412 wing segment",
                              anchor_tags=[">Z", "<Z"])],
        mating_rules=[MatingRule(source_part_id="wing_segment_assembly",
                                 source_anchor=">Z",
                                 target_part_id="wing_segment_assembly",
                                 target_anchor=">Z",
                                 translation="0, 0, 0", clearance=0.0)],
    )

    report = CheckReport(part_id="wing_segment_assembly", attempt=1)
    report.add(CheckResult("compile", passed=True))

    async def _rea(_prompt):
        return None

    async def _planner(_prompt, requirements=None):
        return manifest

    async def _critic(part, **kwargs):
        return "result = None", step_path, report

    async def _export(_code, _out):
        return "result = None"

    async def _execute(_script):
        if export_status == "error":
            return {"status": "error", "traceback": "boom"}
        return {"status": "success", "output": ""}

    monkeypatch.setattr(_main, "run_rea", _rea)
    monkeypatch.setattr(_main, "run_planner", _planner)
    monkeypatch.setattr(_main, "run_critic_loop", _critic)
    monkeypatch.setattr(_main, "run_single_part_export", _export)
    monkeypatch.setattr(_main, "execute_cad_script", _execute)
    monkeypatch.setattr(_main, "parameterize_script", lambda s: (s, {}))
    return manifest


def _make_step_file():
    fd, path = tempfile.mkstemp(suffix=".step")
    with _os.fdopen(fd, "w") as fh:
        fh.write("ISO-10303-21;\n")   # not real STEP; only existence matters
    return path


def test_pipeline_cleans_up_step_on_export_error(monkeypatch):
    """The export-error early return must not leak the reused STEP."""
    step_path = _make_step_file()
    _stub_pipeline(monkeypatch, step_path, export_status="error")

    async def _drain():
        events = []
        async for ev in _main._pipeline("a NACA 2412 wing"):
            events.append(ev)
        return events

    events = asyncio.run(_drain())
    assert any('"status": "error"' in e for e in events), "expected the error path"
    assert not _os.path.exists(step_path), (
        "STEP file leaked on the export-error return — /tmp/mirum/parts is a "
        "256MB tmpfs cleared only at startup"
    )


def test_pipeline_cleans_up_step_on_client_disconnect(monkeypatch):
    """Abandoning the SSE stream mid-flight must still clean up.

    This is the case no return-site cleanup can cover: the consumer closes
    the generator, GeneratorExit is raised at the yield, and only a finally:
    on the generator runs.
    """
    step_path = _make_step_file()
    _stub_pipeline(monkeypatch, step_path, export_status="error")

    async def _abandon():
        agen = _main._pipeline("a NACA 2412 wing")
        # Consume past the critic loop (which is what registers the STEP),
        # then walk away mid-stream exactly as a disconnecting client does.
        async for ev in agen:
            if '"machinist:' in ev and '"done"' in ev:
                break
        await agen.aclose()

    asyncio.run(_abandon())
    assert not _os.path.exists(step_path), "STEP file leaked on client disconnect"


def test_pipeline_cleanup_tolerates_missing_file(monkeypatch):
    """Cleanup of an already-removed path must not raise."""
    step_path = _make_step_file()
    _os.remove(step_path)
    _stub_pipeline(monkeypatch, step_path, export_status="error")

    async def _drain():
        return [ev async for ev in _main._pipeline("a NACA 2412 wing")]

    asyncio.run(_drain())   # must not raise
