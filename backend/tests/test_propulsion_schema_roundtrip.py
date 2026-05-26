"""Schema round-trip tests for the propulsion REA extension.

Asserts that:
* The hand-written ``PROPULSION_SPEC_SCHEMA`` (aerospace flat shape) is Gemini-safe
  (no $ref, anyOf, default, min/max, title, $defs).
* ``agents._rea_schema_for(prompt)`` selects the right shape: ``propulsion_spec`` is
  present only when the prompt mentions propulsion vocabulary.
* The Pydantic ``DesignRequirements`` schema, after ``agents._inline_refs`` +
  ``agents._sanitize_schema``, leaves no forbidden keys and still carries
  ``propulsion_spec`` — proving the propulsion field does not regress the
  AssemblyManifest sanitization machinery.

Runnable via `python backend/tests/test_propulsion_schema_roundtrip.py` or pytest.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Stub google.generativeai so we can import agents.py in a clean dev env.
_google = sys.modules.setdefault("google", types.ModuleType("google"))
_genai = sys.modules.setdefault("google.generativeai", types.ModuleType("google.generativeai"))
if not hasattr(_genai, "GenerativeModel"):
    _genai.GenerativeModel = lambda *args, **kwargs: None  # type: ignore[attr-defined]
setattr(_google, "generativeai", _genai)


FORBIDDEN_KEYS = (
    "$ref", "anyOf", "default", "title",
    "minItems", "maxItems", "minimum", "maximum",
    "minLength", "maxLength", "$defs",
)

# The aerospace flat PropulsionSpec fields the REA schema must expose.
_EXPECTED_PROPULSION_FIELDS = (
    "propulsion_type", "throat_diameter_mm", "exit_diameter_mm",
    "chamber_radius_mm", "chamber_length_mm", "wall_thickness_mm",
    "num_fuel_inlets", "highlight_radius_mm", "fan_face_radius_mm",
    "conv_half_angle_deg", "div_half_angle_deg",
)


def _walk_assert_no_forbidden(obj: object, path: str = "$") -> None:
    if isinstance(obj, dict):
        bad = [k for k in FORBIDDEN_KEYS if k in obj]
        assert not bad, f"{path} contains forbidden Gemini keys: {bad}"
        for k, v in obj.items():
            _walk_assert_no_forbidden(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_assert_no_forbidden(v, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_propulsion_spec_schema_is_gemini_safe() -> None:
    from propulsion.rea_schema import PROPULSION_SPEC_SCHEMA
    _walk_assert_no_forbidden(PROPULSION_SPEC_SCHEMA)


def test_extension_carries_one_top_level_field() -> None:
    from propulsion.rea_schema import propulsion_rea_schema_extension
    ext = propulsion_rea_schema_extension()
    assert set(ext.keys()) == {"propulsion_spec"}, "extension must add exactly one top-level field"
    _walk_assert_no_forbidden(ext)


def test_rea_schema_omits_propulsion_for_non_propulsion_prompts() -> None:
    from agents import _rea_schema_for
    schema = _rea_schema_for("design an aluminum corner bracket with four M5 holes")
    assert "propulsion_spec" not in schema["properties"], (
        "non-propulsion prompts must not carry the propulsion_spec field"
    )
    _walk_assert_no_forbidden(schema)


def test_rea_schema_includes_propulsion_for_propulsion_prompts() -> None:
    from agents import _rea_schema_for
    schema = _rea_schema_for(
        "A De Laval nozzle, throat diameter 40 mm, exit diameter 130 mm, chamber radius 60 mm"
    )
    assert "propulsion_spec" in schema["properties"]
    spec_node = schema["properties"]["propulsion_spec"]
    assert spec_node["type"] == "object"
    for field in _EXPECTED_PROPULSION_FIELDS:
        assert field in spec_node["properties"], f"missing field: {field}"
    _walk_assert_no_forbidden(schema)


def test_design_requirements_pydantic_schema_sanitizes_to_gemini_safe() -> None:
    """Downstream code paths (planner, refiner) may auto-derive from Pydantic.
    After _inline_refs + _sanitize_schema there must be no $ref / anyOf / default
    left, including in the propulsion_spec subtree."""
    import agents
    from schemas import DesignRequirements
    raw = DesignRequirements.model_json_schema()
    defs = raw.pop("$defs", {})
    resolved = agents._inline_refs(raw, defs)
    sanitized = agents._sanitize_schema(resolved)
    _walk_assert_no_forbidden(sanitized)


def test_propulsion_spec_subtree_present_after_sanitization() -> None:
    """The propulsion_spec field must survive sanitization (regression
    protection if a future helper accidentally strips it as 'default')."""
    import agents
    from schemas import DesignRequirements
    raw = DesignRequirements.model_json_schema()
    defs = raw.pop("$defs", {})
    resolved = agents._inline_refs(raw, defs)
    sanitized = agents._sanitize_schema(resolved)
    assert "propulsion_spec" in sanitized.get("properties", {}), (
        "propulsion_spec disappeared from sanitized DesignRequirements schema"
    )


def _run_all() -> int:
    funcs = [
        test_propulsion_spec_schema_is_gemini_safe,
        test_extension_carries_one_top_level_field,
        test_rea_schema_omits_propulsion_for_non_propulsion_prompts,
        test_rea_schema_includes_propulsion_for_propulsion_prompts,
        test_design_requirements_pydantic_schema_sanitizes_to_gemini_safe,
        test_propulsion_spec_subtree_present_after_sanitization,
    ]
    failures = 0
    for fn in funcs:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failures += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {fn.__name__}")
    print(f"\n{len(funcs) - failures}/{len(funcs)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
