"""Fastener representation evidence labels and the thread-fit acceptance gate.

``cad_utils`` fasteners are simplified geometry (``THREAD_REPRESENTATIONS``):
"preview" ribbed look-alikes and non-interfering "envelope" cylinders. No
qualified helical thread mode exists, so thread-fit / manufacturing-thread
acceptance always fails closed. A geometric fit, even when one exists, would
not establish stripping strength, preload, fatigue or certified fastener
performance.
"""
from __future__ import annotations

import ast

FASTENER_HELPERS = frozenset({"make_metric_thread", "make_hex_bolt", "make_hex_nut"})

EVIDENCE_LABELS = {
    "preview": "preview_only: annular-rib look-alike; interferes with a preview nut; not a thread",
    "envelope": "envelope_only: plain nominal-diameter envelope; packaging/clearance preview, not a thread",
    "unknown": "unknown: representation argument is not a literal; treated as preview_only",
}


def _helper_name(call: ast.Call) -> str | None:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def fastener_evidence(scripts: dict[str, str]) -> dict | None:
    """Label every cad_utils fastener helper call in the delivered part sources.

    Static AST scan only (the scripts are not executed). Returns None when no
    fastener helper is used.
    """
    calls = []
    for part_id, source in scripts.items():
        try:
            tree = ast.parse(source or "")
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _helper_name(node) not in FASTENER_HELPERS:
                continue
            mode = "preview"
            for keyword in node.keywords:
                if keyword.arg == "representation":
                    value = keyword.value
                    mode = value.value if isinstance(value, ast.Constant) and value.value in (
                        "preview", "envelope") else "unknown"
            calls.append({"part_id": part_id, "helper": _helper_name(node), "representation": mode,
                          "label": EVIDENCE_LABELS[mode]})
    if not calls:
        return None
    return {
        "fasteners": calls,
        "manufacturing_thread_geometry": False,
        "thread_fit_evaluated": False,
        "thread_fit_accepted": False,
        "note": ("Fastener geometry is simplified (no helical thread). Thread engagement, fit "
                 "class, stripping strength, preload and fatigue are not evaluated."),
    }


def assess_thread_fit(male_representation: str, female_representation: str) -> dict:
    """Thread-fit acceptance for a male/female pair: always fails closed today.

    Returns a structured result rather than raising so callers can report the
    reason. ``passed`` is never True: no supported representation carries a
    helical profile whose lead, pitch, flank contact or clearance could be
    measured.
    """
    reps = (male_representation, female_representation)
    if any(r not in ("preview", "envelope") for r in reps):
        status, reason = "unsupported", "unknown thread representation"
    elif "preview" in reps:
        status, reason = "preview_only", (
            "preview geometry is a look-alike; a preview bolt and preview nut interpenetrate")
    else:
        status, reason = "envelope_only", (
            "envelope geometry only shows packaging clearance; no thread is modelled")
    return {"passed": False, "status": status, "reason": reason, "thread_fit_evaluated": False,
            "engineering_certified": False}
