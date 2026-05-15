"""Schema and regression-coverage checks for backend/eval_harness/prompts.json.

Pure-JSON validation. Runnable via `python backend/tests/test_prompts_schema.py`
or via pytest once that's installed in the env.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROMPTS_PATH = Path(__file__).resolve().parents[1] / "eval_harness" / "prompts.json"

REQUIRED_FIELDS = ("id", "domain", "difficulty", "description", "expected_features")
ALLOWED_DOMAINS = {"A", "B", "C", "D", "E"}
ALLOWED_REGRESSION_TAGS = {"A", "B", "C", "D", "E"}

# IDs that track every CSV-sourced failure prompt (Form Responses,
# 4/27-4/30/2026) where the user-rated result was <= 3 OR the pipeline never
# returned. ID convention: <DOMAIN>_csv_<short-slug> where <DOMAIN> matches
# the entry's `domain` field. The `regression_for` array on each entry tags
# which downstream worktree's fix the case validates. See plan §9 (Worktree F).
EXPECTED_REGRESSION_IDS = {
    # Domain A — primitives, structural, architectural decomposition
    "A_csv_cube_no_load",
    "A_csv_simple_circle",
    "A_csv_simple_square",
    "A_csv_apartment_building",
    "A_csv_dog_cupcake",
    "A_csv_bus_driver_figurine",
    "A_csv_smooth_sphere_3in",
    "A_csv_bolt_threads_timeout",
    "A_csv_mouse_shell_timeout",
    # Domain B — mechanical / parametric (threads, fasteners, phone case)
    "B_csv_iphone_case",
    "B_csv_passenger_car_shell",
    # Domain C — organic, ergonomic, figurine, surface-of-revolution
    "C_csv_cat_20m",
    "C_csv_cat_head_spline",
    "C_csv_cat_leg_cut",
    "C_csv_baseball_wires_item",
    "C_csv_baseball_stitches",
    "C_csv_spoon_funnel",
    "C_csv_pacman",
    "C_csv_gremlin_chair",
    # Domain D — aerospace
    "D_csv_f14_jet",
}


def _load() -> list[dict]:
    return json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))


def test_file_parses_as_list_of_objects() -> None:
    entries = _load()
    assert isinstance(entries, list), "prompts.json must be a JSON array"
    assert all(isinstance(e, dict) for e in entries), "each entry must be an object"
    assert entries, "prompts.json must not be empty"


def test_required_fields_present_on_every_entry() -> None:
    entries = _load()
    for entry in entries:
        missing = [f for f in REQUIRED_FIELDS if f not in entry]
        assert not missing, f"entry {entry.get('id', '<no id>')} missing fields: {missing}"


def test_id_uniqueness() -> None:
    entries = _load()
    ids = [e["id"] for e in entries]
    duplicates = {x for x in ids if ids.count(x) > 1}
    assert not duplicates, f"duplicate ids: {sorted(duplicates)}"


def test_domain_values_in_allowed_set() -> None:
    entries = _load()
    bad = [(e["id"], e["domain"]) for e in entries if e["domain"] not in ALLOWED_DOMAINS]
    assert not bad, f"entries with invalid domain (expected one of {sorted(ALLOWED_DOMAINS)}): {bad}"


def test_expected_features_is_list_of_strings() -> None:
    entries = _load()
    for entry in entries:
        feats = entry["expected_features"]
        assert isinstance(feats, list), f"{entry['id']}: expected_features must be a list"
        assert all(isinstance(f, str) for f in feats), f"{entry['id']}: expected_features items must be strings"


def test_regression_for_is_list_of_strings_with_allowed_tags() -> None:
    """`regression_for` (when present) MUST be a list of strings drawn from
    {A, B, C, D, E}. A bare-string value would silently break the
    `runner.py --regression-for <letter>` filter (string `in` test would pass
    on character match), so we reject the type before checking membership."""
    entries = _load()
    for entry in entries:
        tags = entry.get("regression_for")
        if tags is None:
            continue
        assert isinstance(tags, list), (
            f"{entry['id']}: regression_for must be a list (got {type(tags).__name__})"
        )
        for t in tags:
            assert isinstance(t, str), (
                f"{entry['id']}: regression_for items must be strings (got {type(t).__name__}: {t!r})"
            )
        bad = [t for t in tags if t not in ALLOWED_REGRESSION_TAGS]
        assert not bad, f"{entry['id']}: regression_for has invalid tags {bad}"


def test_id_prefix_matches_domain() -> None:
    """Convention (consistent with the pre-existing entries A_simple_plate,
    B_spur_gear, ..., E_pcb_tray): every id begins with its domain letter
    followed by an underscore. Worktree association is captured separately
    in `regression_for`. Mixing the two leads to ids that lie about what
    the test exercises."""
    entries = _load()
    bad = [
        (e["id"], e["domain"])
        for e in entries
        if not e["id"].startswith(e["domain"] + "_")
    ]
    assert not bad, (
        f"id prefix must match domain (id startswith '<DOMAIN>_'); offenders: {bad}"
    )


def test_all_csv_failure_prompts_present() -> None:
    """Each CSV-sourced failure prompt must appear in prompts.json."""
    entries = _load()
    present = {e["id"] for e in entries}
    missing = sorted(EXPECTED_REGRESSION_IDS - present)
    assert not missing, (
        f"missing CSV regression prompts (must be added per plan §9 F-01): {missing}"
    )


def test_csv_regression_prompts_carry_regression_for_tag() -> None:
    """Every CSV-sourced regression prompt must declare regression_for tags."""
    entries = {e["id"]: e for e in _load()}
    for prompt_id in EXPECTED_REGRESSION_IDS:
        entry = entries.get(prompt_id)
        if entry is None:
            continue  # caught by previous test
        tags = entry.get("regression_for")
        assert tags, f"{prompt_id}: missing required regression_for tag(s)"


def _run_all() -> int:
    funcs = [
        test_file_parses_as_list_of_objects,
        test_required_fields_present_on_every_entry,
        test_id_uniqueness,
        test_domain_values_in_allowed_set,
        test_expected_features_is_list_of_strings,
        test_regression_for_is_list_of_strings_with_allowed_tags,
        test_id_prefix_matches_domain,
        test_all_csv_failure_prompts_present,
        test_csv_regression_prompts_carry_regression_for_tag,
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
