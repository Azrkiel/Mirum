"""
backend/tests/test_gemini_schema_sanitize.py
=============================================

Pins that every Gemini structured-output schema we generate from Pydantic
models is free of JSON-Schema keywords the Gemini proto rejects. Found live
2026-06-12: ``span_efficiency_e: Field(gt=0.0, ...)`` emits
``exclusiveMinimum``, which ``protos.Schema`` rejects -> run_planner crashed
every /generate. The eval harness bypasses run_planner, so only a live
endpoint call could surface it.

Run directly:
    cd T2CIII/backend
    python tests/test_gemini_schema_sanitize.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-dummy-key-not-used")

_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_ROOT))

# Keywords protos.Schema rejects (superset of agents._UNSUPPORTED_SCHEMA_KEYS
# on purpose — this test is the source of truth for "rejected by Gemini").
_REJECTED = {
    "title", "default", "minItems", "maxItems", "minimum", "maximum",
    "minLength", "maxLength", "$defs", "$ref", "anyOf",
    "exclusiveMinimum", "exclusiveMaximum",
}


def _find_rejected(node, path="root"):
    hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _REJECTED:
                hits.append(f"{path}.{k}")
            hits += _find_rejected(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            hits += _find_rejected(item, f"{path}[{i}]")
    return hits


def test_planner_schema_has_no_rejected_keys():
    import agents
    hits = _find_rejected(agents._get_gemini_schema())
    assert not hits, f"Gemini-rejected keys in planner schema: {hits[:10]}"


def test_rea_schema_has_no_rejected_keys():
    import agents
    hits = _find_rejected(agents._rea_schema_for(
        "Design a NACA 2412 wing, margin of safety at least 0.5"))
    assert not hits, f"Gemini-rejected keys in REA schema: {hits[:10]}"


def _run_all() -> int:
    funcs = [
        test_planner_schema_has_no_rejected_keys,
        test_rea_schema_has_no_rejected_keys,
    ]
    failures = 0
    for fn in funcs:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {fn.__name__}")
    print(f"\n{len(funcs) - failures}/{len(funcs)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
