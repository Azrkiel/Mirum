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
    ("timeout",    [r"TIMEOUT", r"exceeded 30-second"]),
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


def build_retry_context(
    traceback: str,
    code: str = "",
    active_rules: list[str] | None = None,
) -> str:
    """Build the enriched retry context handed to the Machinist on the next attempt.

    Pieces:
      1. The classified failure operation (e.g. "loft", "fillet", "thread").
      2. The rule the model most likely violated, plus an actionable hint.
      3. The full list of rules that were active in the system prompt — so the
         model re-anchors on the rule it broke instead of guessing.
      4. The raw traceback at the bottom (still useful for syntax errors etc.).
    """
    failure_op = classify_failure(traceback, code)
    parts: list[str] = [f"Failure class: {failure_op}"]

    hint = get_rule_hint(traceback, code)
    if hint is not None:
        rule_name, hint_text = hint
        in_scope = active_rules is not None and rule_name in active_rules
        marker = "(in your system prompt)" if in_scope else "(not currently injected)"
        parts.append(f"Likely violated rule: {rule_name} {marker}")
        parts.append(f"Hint: {hint_text}")

    if active_rules:
        parts.append(
            "Rules that were active in your system prompt: "
            + ", ".join(active_rules)
        )

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

                # Skip assembly-level and LVM events
                if r.get("event_type") in ("assembly", "lvm_score"):
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
