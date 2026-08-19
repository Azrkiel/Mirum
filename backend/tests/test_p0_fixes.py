"""Regression tests for the P0 silent-quality-destroyer fixes.

Covers: MateType lookup (.value, never str()), the Fixed solver datum, the
translation None-guard, kinematic metadata extraction, the failure-report
event filter, retrieval-index description keying + per-candidate score
filter, config wiring, and the enriched build_retry_context.

Requires pydantic (schemas) but no CadQuery/OCC/Gemini.
    python -m pytest backend/tests/test_p0_fixes.py
"""

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from schemas import AssemblyManifest, MateType, MatingRule, PartDefinition  # noqa: E402
import agents  # noqa: E402
from agents import (  # noqa: E402
    MATE_CONSTRAINTS,
    _UNSUPPORTED_MATES,
    extract_kinematic_metadata,
    run_assembler,
    run_assembler_constraint,
    validate_dof_ranges,
)


def _part(pid: str) -> PartDefinition:
    return PartDefinition(
        part_id=pid,
        description=f"a 10mm cube named {pid}",
        anchor_tags=[">Z", "<Z"],
    )


def _manifest(rules: list[MatingRule], part_ids: list[str]) -> AssemblyManifest:
    return AssemblyManifest(
        assembly_name="test_asm",
        parts=[_part(p) for p in part_ids],
        mating_rules=rules,
    )


# ---------------------------------------------------------------------------
# P0.1 — MateType lookup
# ---------------------------------------------------------------------------

def test_every_mate_type_resolves_or_is_explicitly_unsupported():
    for mt in MateType:
        assert mt.value in MATE_CONSTRAINTS or mt.value in _UNSUPPORTED_MATES, (
            f"MateType.{mt.name} ({mt.value!r}) is neither mapped in "
            f"MATE_CONSTRAINTS nor listed in _UNSUPPORTED_MATES — it would "
            f"silently degrade to Plane"
        )


def test_str_of_matetype_is_not_a_valid_key():
    # Documents the Python 3.11 footgun the .value fix addresses.
    assert str(MateType.REVOLUTE) not in MATE_CONSTRAINTS


def test_revolute_emits_axis_constraint():
    m = _manifest(
        [MatingRule(source_part_id="base", source_anchor=">Z",
                    target_part_id="arm", target_anchor="<Z",
                    mate_type=MateType.REVOLUTE, clearance=0.0)],
        ["base", "arm"],
    )
    script = run_assembler_constraint(m, {"base": "/tmp/b.step", "arm": "/tmp/a.step"})
    assert "'Axis'" in script, "REVOLUTE must emit an Axis constraint, not Plane"


def test_base_part_is_fixed():
    m = _manifest(
        [MatingRule(source_part_id="base", source_anchor=">Z",
                    target_part_id="arm", target_anchor="<Z",
                    mate_type=MateType.FASTENED, clearance=0.0)],
        ["base", "arm"],
    )
    script = run_assembler_constraint(m, {"base": "/tmp/b.step", "arm": "/tmp/a.step"})
    assert "assembly.constrain('base', 'Fixed')" in script


def test_unsupported_gear_degrades_to_plane_but_still_constrains():
    m = _manifest(
        [MatingRule(source_part_id="base", source_anchor=">Z",
                    target_part_id="arm", target_anchor="<Z",
                    mate_type=MateType.GEAR, clearance=0.0)],
        ["base", "arm"],
    )
    script = run_assembler_constraint(m, {"base": "/tmp/b.step", "arm": "/tmp/a.step"})
    assert "'Plane'" in script


# ---------------------------------------------------------------------------
# P0.2 — translation None-guard in translation-mode assembler
# ---------------------------------------------------------------------------

def test_run_assembler_survives_mate_type_rule_without_translation():
    m = _manifest(
        [
            MatingRule(source_part_id="base", source_anchor=">Z",
                       target_part_id="base", target_anchor=">Z",
                       translation="0, 0, 0", clearance=0.0),
            # mate_type rule with NO translation — previously None.split crash
            MatingRule(source_part_id="base", source_anchor=">Z",
                       target_part_id="arm", target_anchor="<Z",
                       mate_type=MateType.FASTENED, clearance=0.0),
        ],
        ["base", "arm"],
    )
    script = run_assembler(m, {"base": "/tmp/b.step", "arm": "/tmp/a.step"})
    assert "assembly.save" in script  # generated without raising


# ---------------------------------------------------------------------------
# P0.1 (kinematic) — extract_kinematic_metadata / validate_dof_ranges
# ---------------------------------------------------------------------------

def test_gear_coupling_is_detected():
    m = _manifest(
        [MatingRule(source_part_id="base", source_anchor=">Z",
                    target_part_id="arm", target_anchor="<Z",
                    mate_type=MateType.GEAR, coupling_ratio=2.0, clearance=0.0)],
        ["base", "arm"],
    )
    meta = extract_kinematic_metadata(m)
    assert meta["has_kinematic_coupling"] is True
    assert meta["coupling_constraints"][0]["type"] == "GEAR"
    assert "2.000:1" in meta["coupling_constraints"][0]["description"]


def test_slider_with_degrees_unit_flags_violation():
    m = _manifest(
        [MatingRule(source_part_id="base", source_anchor=">Z",
                    target_part_id="arm", target_anchor="<Z",
                    mate_type=MateType.SLIDER, dof_min=0.0, dof_max=90.0,
                    dof_unit="deg", clearance=0.0)],
        ["base", "arm"],
    )
    violations = validate_dof_ranges(m)
    assert any("SLIDER" in v["issue"] for v in violations)


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
        retrieval._recompute_vectors()
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
                "geometry_cert", "dimension_mismatch", "solver", "export"):
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
