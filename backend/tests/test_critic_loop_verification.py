"""Integration tests for the verification-aware critic loop.

Monkeypatches every external boundary (Gemini, sandbox subprocess, STEP
export, OCC certificate) and drives run_critic_loop through scripted
attempt sequences, asserting:

  - a verification failure (dimensions/cert) consumes a retry and feeds
    measured feedback + the previous code into the next Machinist prompt,
  - the final attempt returns the geometry certificate with its verification
    payload flagged degraded (the caller stays fail-closed on cert["passed"]),
  - export failure consumes a retry inside the loop,
  - only fully-verified code is admitted to the retrieval index,
  - telemetry logs exactly one record per attempt with the check report,
  - the return contract stays (code, step_path, geometry_cert dict).

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
                                 retry_failure_class=None, retry_techniques=None,
                                 **_kwargs):
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

        def fake_certify(step_path, propulsion_spec=None, **_kwargs):
            return dict(self._certs[self.attempt - 1])

        def fake_log_attempt(**kwargs):
            self.logged.append(kwargs)

        def fake_add_to_index(description, code, domain):
            self.indexed.append(code)

        async def fake_score(*_a, **_k):
            return None

        monkeypatch.setattr(agents, "classify_part", fake_classify)
        monkeypatch.setattr(agents, "run_machinist", fake_machinist)
        monkeypatch.setattr(agents, "execute_cad_script", fake_execute)
        monkeypatch.setattr(agents, "_save_part_step", fake_save_step)
        monkeypatch.setattr(agents, "log_attempt", fake_log_attempt)
        monkeypatch.setattr(agents, "add_to_index", fake_add_to_index)
        monkeypatch.setattr(agents, "_score_and_log", fake_score)
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


def _run(coro):
    async def _wrapped():
        result = await coro
        await asyncio.sleep(0)  # let fire-and-forget scoring tasks finish
        return result
    return asyncio.run(_wrapped())


def test_dimension_failure_consumes_retry_then_passes(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=[True, True],
                certs=[BAD_BBOX_CERT, GOOD_CERT])
    code, step, cert = _run(
        run_critic_loop(PART, max_retries=3, requirements=REQS)
    )
    assert isinstance(cert, dict) and cert["passed"] is True
    assert cert["verification"]["passed"] is True
    assert cert["verification"]["degraded"] is False
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


def test_final_attempt_cert_failure_is_returned_for_caller_to_reject(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=[True, True],
                certs=[BAD_CERT, BAD_CERT])
    code, step, cert = _run(
        run_critic_loop(PART, max_retries=2, requirements=REQS)
    )
    assert cert["passed"] is False            # fail-closed signal untouched
    assert cert["verification"]["degraded"] is True
    assert step                               # geometry still handed back
    assert h.indexed == []                    # never a few-shot example
    assert [r["success"] for r in h.logged] == [False, False]


def test_final_attempt_dimension_mismatch_is_flagged_not_silent(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=[True, True],
                certs=[BAD_BBOX_CERT, BAD_BBOX_CERT])
    code, step, cert = _run(
        run_critic_loop(PART, max_retries=2, requirements=REQS)
    )
    assert cert["passed"] is True             # the certificate itself is valid
    assert cert["verification"]["degraded"] is True
    dims = [c for c in cert["verification"]["checks"] if c["name"] == "dimensions"][0]
    assert dims["passed"] is False and dims["failures"]
    assert h.indexed == []


def test_compile_failure_all_attempts_raises_with_context(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=["Traceback ...\nStdFail_NotDone: fillet", "SyntaxError: bad"],
                certs=[GOOD_CERT, GOOD_CERT])
    with pytest.raises(ScriptError):
        _run(run_critic_loop(PART, max_retries=2, requirements=REQS))
    # Second machinist call saw the first attempt's code + history.
    retry_ctx = h.machinist_calls[1]["error_context"]
    assert "Your previous code" in retry_ctx
    assert "# v1" in retry_ctx
    assert "attempt 1: compile failure" in retry_ctx


def test_history_accumulates_across_mixed_failures(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=["Traceback ...\nStdFail_NotDone", True, True],
                certs=[GOOD_CERT, BAD_BBOX_CERT, GOOD_CERT])
    code, step, cert = _run(
        run_critic_loop(PART, max_retries=3, requirements=REQS)
    )
    assert cert["passed"] is True and cert["verification"]["passed"] is True
    third_ctx = h.machinist_calls[2]["error_context"]
    # The third prompt remembers BOTH prior failure modes.
    assert "attempt 1: compile failure" in third_ctx
    assert "attempt 2:" in third_ctx and "dimensions" in third_ctx


def test_export_failure_consumes_retry_inside_loop(monkeypatch):
    h = Harness(monkeypatch,
                compile_results=[True, True],
                certs=[GOOD_CERT, GOOD_CERT],
                export_fail_attempts={1})
    code, step, cert = _run(
        run_critic_loop(PART, max_retries=3, requirements=REQS)
    )
    assert cert["passed"] is True
    assert len(h.machinist_calls) == 2
    assert h.machinist_calls[1]["retry_failure_class"] == "export"


def test_export_failure_on_final_attempt_raises(monkeypatch):
    Harness(monkeypatch,
            compile_results=[True],
            certs=[GOOD_CERT],
            export_fail_attempts={1})
    with pytest.raises(ScriptError):
        _run(run_critic_loop(PART, max_retries=1, requirements=REQS))


def test_save_step_false_without_dimensions_skips_export(monkeypatch):
    h = Harness(monkeypatch, compile_results=[True], certs=[GOOD_CERT])
    code, step, cert = _run(
        run_critic_loop(PART, max_retries=1, save_step=False)
    )
    assert step == "" and cert is None        # unchanged single-part fast path


def test_save_step_false_with_dimensions_verifies_but_returns_no_path(monkeypatch):
    h = Harness(monkeypatch, compile_results=[True], certs=[BAD_BBOX_CERT])
    code, step, cert = _run(
        run_critic_loop(PART, max_retries=1, save_step=False, requirements=REQS)
    )
    assert step == ""
    assert cert["verification"]["degraded"] is True


def test_default_retries_come_from_config(monkeypatch):
    import config
    h = Harness(monkeypatch,
                compile_results=["boom"] * config.CRITIC_MAX_RETRIES,
                certs=[GOOD_CERT] * config.CRITIC_MAX_RETRIES)
    with pytest.raises(ScriptError):
        _run(run_critic_loop(PART))
    assert len(h.machinist_calls) == config.CRITIC_MAX_RETRIES
