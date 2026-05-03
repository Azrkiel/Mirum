"""Tests for backend/eval_harness/_md_report.render_markdown_report.

Locks the columns and ordering of the run-comparison markdown report:
  id · domain · difficulty · pass/fail · LVM · features · time · regression_for · notes

Sorted by (domain, id) so two runs can be diffed line-by-line. Pure-stdlib;
runs via `python backend/tests/test_md_report.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from eval_harness._md_report import render_markdown_report  # noqa: E402


_PROMPTS = [
    {
        "id": "B_csv_iphone_case",
        "domain": "B",
        "difficulty": "hard",
        "expected_features": ["rounded rect", "cutout", "shell", "fillet"],
        "regression_for": ["B"],
        "notes": "User-feedback CSV: rect(rx=) hallucination.",
    },
    {
        "id": "A_csv_simple_circle",
        "domain": "A",
        "difficulty": "easy",
        "expected_features": ["circle", "extrude"],
        "regression_for": ["A"],
    },
    {
        "id": "A_simple_plate",
        "domain": "A",
        "difficulty": "easy",
        "expected_features": ["rect", "extrude"],
    },
]

_RESULTS = [
    {
        "id": "B_csv_iphone_case",
        "domain": "B",
        "difficulty": "hard",
        "status": "fail",
        "elapsed_s": 12.4,
        "error": "CqLintError: rect() got unexpected kwarg 'rx'",
    },
    {
        "id": "A_csv_simple_circle",
        "domain": "A",
        "difficulty": "easy",
        "status": "pass",
        "elapsed_s": 5.1,
        "lvm_score": 4.5,
        "missing_features": [],
    },
    {
        "id": "A_simple_plate",
        "domain": "A",
        "difficulty": "easy",
        "status": "pass",
        "elapsed_s": 3.0,
        "lvm_score": 5.0,
        "missing_features": [],
    },
]


def test_renders_markdown_table_with_required_columns() -> None:
    md = render_markdown_report(_RESULTS, _PROMPTS)
    header = md.splitlines()[0]
    for col in ("id", "domain", "difficulty", "status", "lvm",
                "features", "time", "regression_for", "notes"):
        assert col in header.lower(), f"missing column {col!r} in header: {header!r}"


def test_rows_sorted_by_domain_then_id() -> None:
    md = render_markdown_report(_RESULTS, _PROMPTS)
    body = [ln for ln in md.splitlines() if ln.startswith("|") and "---" not in ln][1:]
    ids = [ln.split("|")[1].strip() for ln in body]
    assert ids == ["A_csv_simple_circle", "A_simple_plate", "B_csv_iphone_case"], (
        f"rows out of (domain, id) order: {ids}"
    )


def test_pass_fail_status_present() -> None:
    md = render_markdown_report(_RESULTS, _PROMPTS)
    assert "pass" in md.lower() and "fail" in md.lower()


def test_lvm_blank_when_missing() -> None:
    md = render_markdown_report(_RESULTS, _PROMPTS)
    fail_line = next(ln for ln in md.splitlines() if "B_csv_iphone_case" in ln)
    cells = [c.strip() for c in fail_line.split("|")]
    assert "fail" in fail_line.lower()
    # No LVM score on a fail. Cell should be empty / dash, not crash, not "None".
    assert "None" not in cells, f"raw None leaked into markdown row: {fail_line!r}"


def test_features_matched_uses_missing_features_inverse() -> None:
    """Matched count = len(expected_features) - len(missing_features). If a
    pass has no missing_features, the cell shows '<n>/<n>'."""
    md = render_markdown_report(_RESULTS, _PROMPTS)
    plate_line = next(ln for ln in md.splitlines() if "A_simple_plate" in ln)
    assert "2/2" in plate_line, (
        f"expected '2/2' (all features matched) in row: {plate_line!r}"
    )


def test_regression_for_cell_lists_tags() -> None:
    md = render_markdown_report(_RESULTS, _PROMPTS)
    iphone_line = next(ln for ln in md.splitlines() if "B_csv_iphone_case" in ln)
    plate_line = next(ln for ln in md.splitlines() if "A_simple_plate" in ln)
    assert "B" in iphone_line.split("|")[-3]  # regression_for column
    # Untagged entry — empty cell, NOT a literal "None" or "[]"
    plate_cells = [c.strip() for c in plate_line.split("|")]
    assert "None" not in plate_cells and "[]" not in plate_cells, (
        f"raw empty marker leaked: {plate_line!r}"
    )


def test_handles_missing_optional_fields() -> None:
    """notes / regression_for are optional on a prompt; missing must not
    crash the renderer. A_simple_plate has neither."""
    md = render_markdown_report(_RESULTS, _PROMPTS)
    assert "A_simple_plate" in md


def test_does_not_break_on_unknown_prompt_id() -> None:
    """If a result references a prompt id not in the prompts list (e.g.
    --filter dropped it post-run), the renderer must still produce a row."""
    extra = _RESULTS + [{
        "id": "Z_orphan", "domain": "Z", "difficulty": "?",
        "status": "fail", "elapsed_s": 1.0, "error": "missing prompt"
    }]
    md = render_markdown_report(extra, _PROMPTS)
    assert "Z_orphan" in md


def _run_all() -> int:
    funcs = [
        test_renders_markdown_table_with_required_columns,
        test_rows_sorted_by_domain_then_id,
        test_pass_fail_status_present,
        test_lvm_blank_when_missing,
        test_features_matched_uses_missing_features_inverse,
        test_regression_for_cell_lists_tags,
        test_handles_missing_optional_fields,
        test_does_not_break_on_unknown_prompt_id,
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
