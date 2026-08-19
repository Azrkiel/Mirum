"""
Failure analysis and error attribution for the Critic Loop.

Classifies Critic Loop failures by CadQuery operation type, enabling
targeted training data collection and strategy prompt improvements.
"""

import json
import os
import pathlib
import re
from collections import Counter, defaultdict
from typing import Optional

# Operation classification patterns
# Each entry: (operation_name, [regex_patterns_in_traceback_or_code])
OPERATION_PATTERNS: list[tuple[str, list[str]]] = [
    ("extrude",    [r"\.extrude\(", r"BRep_API: command not done.*extrude"]),
    ("revolve",    [r"\.revolve\(", r"RevolutionAlgo"]),
    ("fillet",     [r"\.fillet\(", r"StdFail_NotDone", r"BRepFilletAPI"]),
    ("chamfer",    [r"\.chamfer\(", r"BRepChamfer"]),
    ("loft",       [r"\.loft\(", r"makeLoft", r"LoftAlgo", r"ValueError.*loft"]),
    ("boolean",    [r"BRepAlgoAPI", r"\.cut\(", r"\.union\(", r"\.intersect\(", r"BooleanAlgo"]),
    ("shell",      [r"\.shell\(", r"OffsetAlgo", r"BRepOffsetAPI"]),
    ("spline",     [r"\.spline\(", r"makeSpline", r"Geom_BSplineCurve"]),
    ("selector",   [r"CadQuery selector", r"No object found", r"Empty wire", r"NullShape"]),
    ("constraint", [r"assembly\.constrain", r"assembly\.solve", r"SOLVER_FAILURE"]),
    ("thread",     [r"makeThread", r"helix", r"Helix"]),
    ("text",       [r"\.text\(", r"fontsize", r"Font"]),
    ("import",     [r"importStep", r"importBrep", r"STEP"]),
    ("polarArray", [r"polarArray", r"PolarArray"]),
    ("syntax",     [r"SyntaxError", r"IndentationError", r"NameError"]),
    ("security",   [r"Security violation", r"not allowed"]),
    ("timeout",    [r"TIMEOUT", r"exceeded \d+-second"]),
]


def classify_failure(traceback: str, code: str = "") -> str:
    """Classify a Critic Loop failure by CadQuery operation type.

    Checks the traceback and generated code against known error patterns.
    Returns the operation name (e.g., 'fillet', 'loft', 'selector') or
    'unknown' if no pattern matches.
    """
    combined = (traceback + "\n" + code).lower()

    for op_name, patterns in OPERATION_PATTERNS:
        if any(re.search(p, combined, re.IGNORECASE) for p in patterns):
            return op_name

    return "unknown"


# Patterns where classify_failure's first-match-wins ordering produces the wrong
# answer. classify_failure attributes any StdFail_NotDone to "fillet", which is
# the right default but masks loft- and thread-specific failures.


# Maps a failure operation to (likely-violated rule name, actionable retry hint).
# The rule name must exist in strategies.RULE_BLOCKS — the critic loop highlights
# it back to the Machinist as the rule to re-anchor on.
OP_TO_RULE_HINT: dict[str, tuple[str, str]] = {
    "bbox_api": (
        "rule_no_hallucinated_methods",
        "A BoundingBox attribute does not exist. CadQuery's BoundBox uses "
        "LOWERCASE accessors: bb = solid.BoundingBox(); read bb.xmin/bb.xmax/"
        "bb.ymin/bb.ymax/bb.zmin/bb.zmax, bb.xlen/bb.ylen/bb.zlen, or bb.center. "
        "There is no bb.xMax/bb.XMax/bb.XLen. (solid.Center() returns a "
        "capitalised Vector, but the BoundBox accessors are lowercase.) "
        "Re-read rule_no_hallucinated_methods.",
    ),
    "loft": (
        "rule_loft_chaining",
        "Loft failed. The most common cause is assigning each cross-section to a "
        "variable and then calling .loft() on the last one — that empties the "
        "Workplane pending-wires stack. Re-read rule_loft_chaining: chain "
        "Workplane().pushPoints()/section()/etc. into a single fluent pipeline "
        "and call .loft(combine=True) at the end without intermediate variables.",
    ),
    "thread": (
        "rule_fasteners",
        "Threaded geometry failed. Helix sweeps are fragile under OCCT and the "
        "Machinist should not roll its own. Re-read rule_fasteners and use the "
        "deterministic helper from cad_utils (e.g. make_threaded_rod / "
        "make_hex_bolt); fall back to a smooth cylinder of nominal diameter if "
        "no helper exists.",
    ),
    "cooling_channels": (
        "rule_domain_f",
        "Helical/swept geometry failed on a propulsion body. OCC cannot reliably "
        "sweep a helix around a non-cylindrical (nozzle/nacelle/duct) surface — it "
        "always crashes. Do NOT roll your own helix or thread here. Build the body "
        "with the deterministic helpers make_nozzle_profile / make_combustion_chamber "
        "/ make_nacelle_cowl from cad_utils, and model fuel-inlet ports as an axial "
        "polarArray of bores cut with .cutThruAll() — never as tangential channels or "
        "spiral grooves. Re-read rule_domain_f.",
    ),
    "fillet": (
        "rule_safe_filleting",
        "Fillet failed (StdFail_NotDone). Either the radius is too large for an "
        "adjacent edge, or the edge selector matched an empty set. Re-read "
        "rule_safe_filleting: pick a radius < min(adjacent edge length)/2 and "
        "verify the selector with a more specific filter.",
    ),
    "chamfer": (
        "rule_safe_filleting",
        "Chamfer failed for the same family of reasons as a fillet failure. "
        "Re-read rule_safe_filleting and reduce the chamfer distance or narrow "
        "the edge selector.",
    ),
    "shell": (
        "rule_shell_api",
        "Shell failed — most often from inventing API. The correct CadQuery "
        "signature is .faces(SELECTOR).shell(thickness): select the open face(s) "
        "on the workplane FIRST, then call .shell() with ONE positional arg "
        "(positive grows outward, negative hollows inward). NEVER pass faces= "
        "as a kwarg — it is not a parameter. "
        "Canonical box-with-open-top: cq.Workplane('XY').box(L,W,H).faces('>Z').shell(-t). "
        "Wall thickness must be much less than the smallest dimension or OCCT "
        "raises StdFail_NotDone. Re-read rule_shell_api.",
    ),
    "boolean": (
        "rule_face_reselection",
        "Boolean operation failed (BRepAlgoAPI). The two operands either don't "
        "intersect or share zero overlap volume, OR a face selector after the "
        "boolean references a face that no longer exists. Re-read "
        "rule_face_reselection and reselect from the current solid after every "
        "boolean.",
    ),
    "selector": (
        "rule_face_selectors",
        "A face/edge selector returned an empty set or a stale reference. "
        "Re-read rule_face_selectors and never reuse a selector across booleans "
        "without reselecting from the new solid.",
    ),
    "polarArray": (
        "rule_polar_array",
        "Polar array failed. Use Workplane.polarArray() rather than building "
        "a list of points and calling .pushPoints() in a loop. Re-read "
        "rule_polar_array.",
    ),
    "text": (
        "rule_text_api",
        "Text/engraving failed. CadQuery's .text() requires a specific signature "
        "and a mounted font. Re-read rule_text_api.",
    ),
    # ---- Previously hint-less classes (the most common LLM failures) ----
    "syntax": (
        "rule_raw_python",
        "The script has a Python-level error (SyntaxError / IndentationError / "
        "NameError). Return ONLY plain Python — no markdown fences, no prose, "
        "no ellipses. Define every variable before use, close every bracket, "
        "and assign the final shape to `result`. Re-read your previous code "
        "below and fix the exact line the traceback names.",
    ),
    "security": (
        "rule_imports",
        "The sandbox's AST gate rejected the script. ONLY these imports are "
        "permitted: cadquery (as cq), math, cad_utils, cad_aero. There is no "
        "file access (open), no eval/exec/getattr/setattr, no __dunder__ "
        "attribute access, no os/sys/subprocess/numpy. Rewrite the script "
        "using only cadquery + math + the cad_utils/cad_aero helpers.",
    ),
    "timeout": (
        "rule_no_hallucinated_methods",
        "The script exceeded the subprocess time limit. Heavy shell/loft/"
        "boolean chains and large loops are the usual cause. Simplify: reduce "
        "segment counts, avoid per-item boolean loops (build one cutter solid "
        "and cut once), avoid shelling complex lofts, and prefer the "
        "deterministic cad_utils helpers which are already performance-tuned.",
    ),
    "import": (
        "rule_imports",
        "A STEP/BRep import call failed. Machinist scripts must build geometry "
        "from scratch — never call cq.importers.importStep or reference "
        "external files. Only the deterministic assembler imports STEP files.",
    ),
    "constraint": (
        "rule_no_part_tagging",
        "The script called assembly.constrain()/solve(). Individual part "
        "scripts must NOT do assembly-level constraint work — model the single "
        "part at the origin and assign it to `result`; the deterministic "
        "assembler handles mating.",
    ),
    "extrude": (
        "rule_wire_to_solid",
        "Extrude failed. The pending wire stack was empty or the profile is "
        "not a closed planar wire. Sketch the closed profile on a workplane "
        "(rect/circle/polyline().close()) and call .extrude(h) in the same "
        "fluent chain — do not extrude from a stale or consumed workplane.",
    ),
    "revolve": (
        "rule_wire_to_solid",
        "Revolve failed. The profile must be a CLOSED planar wire entirely on "
        "one side of the revolution axis. Draw the half-section with "
        "polyline(...).close() on the XZ plane and revolve about the Z axis; "
        "never let the profile cross the axis.",
    ),
    "spline": (
        "rule_loft_chaining",
        "Spline construction failed. Ensure the point list has no duplicate "
        "consecutive points, is planar for a workplane sketch, and is closed "
        "(.close()) before extruding/lofting. For organic curves prefer fewer, "
        "well-spaced control points.",
    ),
    # ---- Verification-gate failure classes (CheckReport) ----
    "geometry_cert": (
        "rule_result_var",
        "The script compiled but the produced geometry failed structural "
        "verification (see the measured failures above: zero volume, "
        "degenerate faces, invalid BRep, or no solid body). Make sure `result` "
        "is a SOLID (not a wire/face/empty Workplane), booleans actually "
        "intersect their operands, and shell thickness is far smaller than "
        "the smallest overall dimension.",
    ),
    "dimension_mismatch": (
        "rule_origin",
        "The script compiled but the measured bounding box does not match the "
        "requested dimensions (see the measured values above). Use the EXACT "
        "millimetre dimensions from the description — do not scale, swap "
        "axes, or invent sizes. Rebuild with the stated length/width/height "
        "and re-check each primitive's arguments against the spec.",
    ),
    "solver": (
        "rule_face_selectors",
        "The assembly constraint solver failed. The usual cause is an anchor "
        "selector that matches no face on the exported part. Use only "
        "standard directional selectors ('>Z', '<Z', '>X', '<X', '>Y', '<Y') "
        "and ensure the part actually has a planar face in that direction.",
    ),
    "export": (
        "rule_result_var",
        "The part compiled but could not be exported to STEP. `result` must "
        "be a cq.Workplane containing a solid, or a cq.Assembly — not a wire, "
        "sketch, or bare shape list. Ensure the final operation produces a "
        "solid body and assign exactly that to `result`.",
    ),
}


# Domain F helper calls that mark a generated script as propulsion
# (nozzle / chamber / nacelle / duct) code, so a helix/thread OCC failure routes
# to the Domain F revolve-first guidance rather than the generic fastener hint.
_PROPULSION_CODE_MARKERS: tuple[str, ...] = (
    "make_nozzle_profile",
    "make_combustion_chamber",
    "make_nacelle_cowl",
)


def _is_propulsion_code(code: str) -> bool:
    lo = code.lower()
    return any(marker in lo for marker in _PROPULSION_CODE_MARKERS)


def get_rule_hint(traceback: str, code: str = "") -> tuple[str, str] | None:
    """Pick the most likely violated rule + actionable retry hint.

    Applies specific overrides for failure modes that classify_failure misattributes
    (the fillet-StdFail bias swallows loft and thread failures), then falls back to
    the generic operation classifier. Returns (rule_name, hint_text) or None.
    """
    combined = traceback + "\n" + code

    # A hallucinated BoundBox attribute (bb.xMax/XLen/…) — CadQuery's accessors are
    # lowercase. Catch the AttributeError before the generic classifier so the Critic
    # gets the casing fix instead of an unrelated op hint.
    if re.search(r"BoundBox.*has no attribute|\bbb?\.[xyz](Max|Min|Len)\b|'BoundBox' object has no attribute",
                 combined, re.IGNORECASE):
        return OP_TO_RULE_HINT["bbox_api"]

    # CadQuery's loft path raises UnboundLocalError on `newS` when its workplane
    # pending-wires stack is empty — almost always a loft-chaining bug.
    if re.search(r"UnboundLocalError.*newS", combined, re.IGNORECASE | re.DOTALL):
        return OP_TO_RULE_HINT["loft"]
    if re.search(r"Nothing to loft|ValueError.*loft", combined, re.IGNORECASE | re.DOTALL):
        return OP_TO_RULE_HINT["loft"]

    # Thread/helix mention plus an OCCT not-done signal beats the fillet-StdFail bias.
    if re.search(r"makeThread|HelixSweep|\bhelix\b", combined, re.IGNORECASE) and \
       re.search(r"StdFail|BRep_API: command not done", combined, re.IGNORECASE):
        # Domain F (propulsion) code must avoid helix/thread sweeps entirely and
        # use the revolve-first helpers, not the generic fastener helpers the plain
        # "thread" hint points at.
        if _is_propulsion_code(code):
            return OP_TO_RULE_HINT["cooling_channels"]
        return OP_TO_RULE_HINT["thread"]

    op = classify_failure(traceback, code)
    if op == "thread" and _is_propulsion_code(code):
        return OP_TO_RULE_HINT["cooling_channels"]
    return OP_TO_RULE_HINT.get(op)


# Max characters of the previous script echoed back into the retry prompt.
# Tail-preserved: errors usually reference later lines.
_MAX_PREV_CODE_CHARS = 4000


def _truncate_code_tail(code: str, limit: int = _MAX_PREV_CODE_CHARS) -> str:
    """Truncate a script to its last ``limit`` characters, marking the cut."""
    if len(code) <= limit:
        return code
    return "# ... (earlier lines truncated) ...\n" + code[-limit:]


def build_retry_context(
    traceback: str,
    code: str = "",
    active_rules: list[str] | None = None,
    check_failures: str = "",
    previous_code: str = "",
    history: list[str] | None = None,
    failure_class: str | None = None,
) -> str:
    """Build the enriched retry context handed to the Machinist on the next attempt.

    Pieces:
      1. A one-line-per-attempt digest of every prior attempt, so the model
         stops ping-ponging between failure modes.
      2. The classified failure operation (e.g. "loft", "fillet", "thread").
         ``failure_class`` overrides the traceback classifier — used for
         verification-gate failures (geometry_cert, dimension_mismatch, ...)
         where there is no traceback to classify.
      3. The rule the model most likely violated, plus an actionable hint.
      4. Measured verification failures (``check_failures``), when the script
         compiled but the geometry failed a check.
      5. The previous script verbatim (tail-truncated) — the model must EDIT
         its prior attempt, not regenerate blind.
      6. The full list of rules that were active in the system prompt.
      7. The raw traceback at the bottom (still useful for syntax errors etc.).
    """
    parts: list[str] = []

    if history:
        parts.append("What happened on your prior attempts:")
        parts.extend(f"  {line}" for line in history)
        parts.append("")

    failure_op = failure_class or classify_failure(traceback, code)
    parts.append(f"Failure class: {failure_op}")

    if failure_class is not None:
        hint = OP_TO_RULE_HINT.get(failure_class)
    else:
        hint = get_rule_hint(traceback, code)
    if hint is not None:
        rule_name, hint_text = hint
        in_scope = active_rules is not None and rule_name in active_rules
        marker = "(in your system prompt)" if in_scope else "(not currently injected)"
        parts.append(f"Likely violated rule: {rule_name} {marker}")
        parts.append(f"Hint: {hint_text}")

    if check_failures:
        parts.append("")
        parts.append(check_failures)

    if previous_code:
        parts.append("")
        parts.append(
            "Your previous code (EDIT this to fix the failure — do not start "
            "over from scratch; keep everything that already works):"
        )
        parts.append("```python")
        parts.append(_truncate_code_tail(previous_code))
        parts.append("```")

    if active_rules:
        parts.append("")
        parts.append(
            "Rules that were active in your system prompt: "
            + ", ".join(active_rules)
        )

    if traceback.strip():
        parts.append("")
        parts.append("Raw traceback:")
        parts.append(traceback.strip())

    return "\n".join(parts)


def generate_failure_report(log_path: Optional[str] = None) -> dict:
    """Read telemetry JSONL and generate a failure rate report by operation.

    Returns a dict with:
    - operation_counts: {op_name: {"attempts": N, "failures": N, "rate": float}}
    - domain_counts: {domain: {"attempts": N, "failures": N}}
    - total_attempts: int
    - total_failures: int
    """
    if log_path is None:
        log_path = str(
            pathlib.Path(os.environ.get("MIRUM_TELEMETRY_DIR", "/tmp/mirum/telemetry"))
            / "critic_loop.jsonl"
        )

    op_stats: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "failures": 0})
    domain_stats: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "failures": 0})
    total_attempts = 0
    total_failures = 0

    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Skip every typed event (assembly, lvm_score, token_usage,
                # and any future event kinds) — only untyped critic-loop
                # attempt records belong in the failure report. token_usage
                # records were previously counted as failed attempts of
                # operation "unknown", inflating every rate on /stats.
                if r.get("event_type"):
                    continue

                domain = r.get("domain_classification", "unknown")
                success = r.get("success_status", False)
                op = r.get("failure_operation", "unknown")

                total_attempts += 1
                domain_stats[domain]["attempts"] += 1

                if not success:
                    total_failures += 1
                    domain_stats[domain]["failures"] += 1
                    op_stats[op]["attempts"] += 1
                    op_stats[op]["failures"] += 1
                else:
                    # Track successes per operation too (from classified retries)
                    if op and op != "unknown":
                        op_stats[op]["attempts"] += 1

    except FileNotFoundError:
        pass

    # Compute rates
    for op, stats in op_stats.items():
        attempts = max(stats["attempts"], 1)
        stats["rate"] = round(stats["failures"] / attempts, 3)

    for domain, stats in domain_stats.items():
        attempts = max(stats["attempts"], 1)
        stats["rate"] = round(stats["failures"] / attempts, 3)

    return {
        "operation_counts": dict(op_stats),
        "domain_counts": dict(domain_stats),
        "total_attempts": total_attempts,
        "total_failures": total_failures,
        "overall_failure_rate": (
            round(total_failures / max(total_attempts, 1), 3)
        ),
    }


def print_failure_report() -> None:
    """Print a formatted failure report to stdout."""
    report = generate_failure_report()

    print(f"\n{'='*60}")
    print(f"Mirum Failure Analysis Report")
    print(f"{'='*60}")
    print(f"Total attempts: {report['total_attempts']}")
    print(f"Total failures: {report['total_failures']}")
    print(f"Overall failure rate: {report['overall_failure_rate']:.1%}")

    print(f"\nFailure by Operation Type:")
    print(f"{'Operation':<16} {'Failures':>8} {'Rate':>8}")
    print("-" * 36)
    op_sorted = sorted(
        report["operation_counts"].items(),
        key=lambda x: x[1]["failures"],
        reverse=True,
    )
    for op, stats in op_sorted:
        print(f"{op:<16} {stats['failures']:>8} {stats['rate']:>7.1%}")

    print(f"\nFailure by Domain:")
    print(f"{'Domain':<10} {'Attempts':>10} {'Failures':>10} {'Rate':>8}")
    print("-" * 44)
    for domain, stats in sorted(report["domain_counts"].items()):
        print(
            f"{domain:<10} {stats['attempts']:>10} "
            f"{stats['failures']:>10} {stats['rate']:>7.1%}"
        )

    print(f"{'='*60}\n")


if __name__ == "__main__":
    print_failure_report()
