"""Regression tests for the P0 silent-quality fixes ported from PR #4.

Covers: /stats failure-report event filter, retrieval-index description keying
and per-candidate score filter, config wiring (timeout, retries), the enriched
build_retry_context, and verification-payload sanitisation.

Dropped from the original PR #4 file because the code they pinned no longer
exists on this design: MateType/MATE_CONSTRAINTS lookup, Fixed solver datum,
run_assembler translation guard, and the kinematic-metadata tests (assembly
placement is closed-form via cad_utils.assemble_mated_parts now).

Requires pydantic (schemas) but no CadQuery/OCC/Gemini.
    python -m pytest backend/tests/test_p0_fixes.py
"""

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import agents  # noqa: E402


# ---------------------------------------------------------------------------
# P0.6 — /stats failure report skips typed events
# ---------------------------------------------------------------------------

def test_failure_report_ignores_token_usage_and_lvm_events():
    from failure_analysis import generate_failure_report
    records = [
        {"part_id": "p1", "domain_classification": "A", "attempt_number": 1,
         "generated_code": "x", "traceback_error": None, "success_status": True},
        {"part_id": "p2", "domain_classification": "A", "attempt_number": 1,
         "generated_code": "x", "traceback_error": "boom", "success_status": False,
         "failure_operation": "fillet"},
        {"event_type": "token_usage", "agent": "rea", "prompt_tokens": 10,
         "completion_tokens": 5},
        {"event_type": "lvm_score", "lvm_score": 4.5},
        {"event_type": "assembly", "solver_success": False},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
        path = f.name
    report = generate_failure_report(log_path=path)
    assert report["total_attempts"] == 2
    assert report["total_failures"] == 1
    assert "unknown" not in report["operation_counts"]


# ---------------------------------------------------------------------------
# P0.5 — retrieval index fixes
# ---------------------------------------------------------------------------

def test_build_index_prefers_description_over_part_id(tmp_path=None):
    import retrieval
    d = pathlib.Path(tempfile.mkdtemp())
    log = d / "critic_loop.jsonl"
    rows = [
        {"part_id": "leg_1", "domain_classification": "A", "attempt_number": 1,
         "generated_code": "import cadquery as cq\nresult = cq.Workplane().box(1,1,1)",
         "success_status": True,
         "description": "a cylindrical table leg 500mm tall"},
        {"part_id": "base_plate", "domain_classification": "A", "attempt_number": 1,
         "generated_code": "import cadquery as cq\nresult = cq.Workplane().box(2,2,2)",
         "success_status": True},  # legacy record — no description field
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    old_log, old_state = retrieval._LOG_FILE, (
        retrieval._INDEX, retrieval._IDF, retrieval._TFIDF, retrieval._INDEX_BUILT
    )
    try:
        retrieval._LOG_FILE = log
        retrieval._INDEX, retrieval._IDF, retrieval._TFIDF = [], {}, []
        retrieval._INDEX_BUILT = False
        retrieval.build_index()
        descs = {r["description"] for r in retrieval._INDEX}
        assert "a cylindrical table leg 500mm tall" in descs
        assert "base_plate" in descs  # legacy fallback still works
    finally:
        retrieval._LOG_FILE = old_log
        (retrieval._INDEX, retrieval._IDF,
         retrieval._TFIDF, retrieval._INDEX_BUILT) = old_state


def test_score_filter_applies_per_candidate():
    import retrieval
    old_state = (retrieval._INDEX, retrieval._IDF, retrieval._TFIDF,
                 retrieval._INDEX_BUILT)
    try:
        retrieval._INDEX, retrieval._IDF, retrieval._TFIDF = [], {}, []
        retrieval._INDEX_BUILT = True
        retrieval.add_to_index("hex bolt with metric thread", "code_a", "B")
        retrieval.add_to_index("organic ergonomic mouse shell", "code_b", "C")
        retrieval.add_to_index("involute spur gear", "code_c", "B")
        hits = retrieval.retrieve_few_shots("hex bolt metric thread", domain="B", k=3)
        # Only genuinely similar entries return — unrelated ones no longer
        # ride along on the top candidate's score.
        assert any(h["code"] == "code_a" for h in hits)
        assert not any(h["code"] == "code_b" for h in hits)
    finally:
        (retrieval._INDEX, retrieval._IDF,
         retrieval._TFIDF, retrieval._INDEX_BUILT) = old_state


# ---------------------------------------------------------------------------
# P0.8 — config wiring
# ---------------------------------------------------------------------------

def test_compiler_timeout_comes_from_config():
    import compiler
    import config
    assert compiler._TIMEOUT_S == config.CADQUERY_TIMEOUT_S


def test_sandbox_invariants_unchanged():
    import compiler
    assert compiler._ALLOWED_MODULES == {"cadquery", "cq", "math", "cad_utils", "cad_aero"}
    assert "eval" in compiler._BANNED_BUILTINS
    assert "exec" in compiler._BANNED_BUILTINS
    assert "open" in compiler._BANNED_BUILTINS


def test_critic_loop_default_retries_uses_config():
    import inspect
    import config
    sig = inspect.signature(agents.run_critic_loop)
    assert sig.parameters["max_retries"].default is None  # resolved to config at runtime
    assert config.CRITIC_MAX_RETRIES >= 1
    # The return contract (code, step_path, geometry_cert) is part of the signature.
    assert "requirements" in sig.parameters and "dims_part_scoped" in sig.parameters


# ---------------------------------------------------------------------------
# P1.4/P1.5 — enriched retry context + hint coverage
# ---------------------------------------------------------------------------

def test_retry_context_includes_previous_code_and_history():
    from failure_analysis import build_retry_context
    ctx = build_retry_context(
        traceback="Traceback ...\nStdFail_NotDone",
        code="cq.Workplane().box(1,1,1).fillet(9)",
        active_rules=["rule_safe_filleting"],
        previous_code="import cadquery as cq\nresult = cq.Workplane().box(1,1,1).fillet(9)",
        history=["attempt 1: compile failure (fillet)"],
    )
    assert "Your previous code" in ctx
    assert "result = cq.Workplane().box(1,1,1).fillet(9)" in ctx
    assert "attempt 1: compile failure (fillet)" in ctx
    assert "Raw traceback:" in ctx


def test_retry_context_previous_code_is_tail_truncated():
    from failure_analysis import build_retry_context, _MAX_PREV_CODE_CHARS
    long_code = "# header\n" + ("x = 1\n" * 2000) + "tail_marker = 42\n"
    ctx = build_retry_context(traceback="err", previous_code=long_code)
    assert "tail_marker = 42" in ctx
    assert "(earlier lines truncated)" in ctx
    assert len(long_code) > _MAX_PREV_CODE_CHARS  # sanity: truncation exercised


def test_retry_context_verification_failure_uses_class_override():
    from failure_analysis import build_retry_context
    ctx = build_retry_context(
        traceback="",
        code="import cadquery as cq\nresult = cq.Workplane().box(1,1,1).fillet(0.1)",
        check_failures="The script COMPILED but ...:\n- [dimensions] total_length_mm: expected 150mm",
        failure_class="dimension_mismatch",
    )
    assert "Failure class: dimension_mismatch" in ctx
    assert "EXACT millimetre dimensions" in ctx     # dimension hint text
    assert "total_length_mm" in ctx
    assert "Raw traceback" not in ctx               # no traceback section when empty


def test_common_failure_classes_now_have_hints():
    from failure_analysis import OP_TO_RULE_HINT
    for cls in ("syntax", "security", "timeout", "import", "constraint",
                "extrude", "revolve", "spline",
                "geometry_cert", "dimension_mismatch", "export"):
        assert cls in OP_TO_RULE_HINT, f"missing hint for {cls!r}"


def test_security_hint_names_the_actual_allowlist():
    from failure_analysis import OP_TO_RULE_HINT
    import compiler
    hint = OP_TO_RULE_HINT["security"][1]
    for mod in sorted(compiler._ALLOWED_MODULES - {"cq"}):
        assert mod in hint, f"security hint must name allowlisted module {mod!r}"


def test_timeout_classifier_matches_configurable_message():
    from failure_analysis import classify_failure
    assert classify_failure("TIMEOUT: Script exceeded 45-second execution limit.") == "timeout"


# ---------------------------------------------------------------------------
# Security-review fixes: verification payload sanitization + timeout clamp
# ---------------------------------------------------------------------------

def test_verification_payload_failures_are_path_redacted():
    import os
    os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-key")
    from main import _sanitize_report_payload
    payload = {
        "part_id": "p", "attempt": 1, "passed": False, "degraded": True,
        "checks": [{
            "name": "cert", "passed": False, "skipped": False,
            "failures": ["STEP file not found: '/opt/app/parts/base_9f2.step'"],
        }],
    }
    out = _sanitize_report_payload(payload)
    assert "/opt/app/parts" not in out["checks"][0]["failures"][0]
    assert "<internal>" in out["checks"][0]["failures"][0]
    assert _sanitize_report_payload(None) is None


def test_cadquery_timeout_is_clamped():
    # The module-level constant must sit inside the sandbox contract's bounds
    # regardless of what MIRUM_CADQUERY_TIMEOUT_S was set to.
    import config
    assert 5 <= config.CADQUERY_TIMEOUT_S <= 60
