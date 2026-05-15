"""Tests for backend/eval_harness/_md_report.render_markdown_report.

Locks the columns and ordering of the run-comparison markdown report:
  id · domain · difficulty · pass/fail · LVM · features · time · regression_for · notes

Sorted by (domain, id) so two runs can be diffed line-by-line. Pure-stdlib;
runs via `python backend/tests/test_md_report.py`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Splits a rendered table row on `|` separators while respecting backslash
# escapes (notes cells may contain `\|` for literal pipes).
_ROW_SPLIT = re.compile(r"(?<!\\)\|")

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
        "notes": "CSV: rect(rx=) hallucination | with a literal pipe to test escaping",
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
    # Sort-order discriminator: id starts with `Z_` but domain is `A`.
    # Under (domain, id) sort it groups with the other A entries (alphabetically
    # last within them). Under id-only sort it would sit at the very bottom.
    # The two orderings disagree on this row → catches an id-only sort bug.
    {
        "id": "Z_in_domain_a",
        "domain": "A",
        "difficulty": "easy",
        "expected_features": ["alpha", "beta", "gamma", "delta"],
        "regression_for": ["A"],
    },
    # Partial-match fixture: 4 expected features, 2 missing → 2/4 matched.
    # Catches a renderer that hardcodes any specific n/n string.
    {
        "id": "C_partial_match",
        "domain": "C",
        "difficulty": "medium",
        "expected_features": ["loft", "spline", "fillet", "shell"],
        "regression_for": ["C", "D"],
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
    {
        "id": "Z_in_domain_a",
        "domain": "A",
        "difficulty": "easy",
        "status": "pass",
        "elapsed_s": 2.0,
        "lvm_score": 4.0,
        "missing_features": [],
    },
    {
        "id": "C_partial_match",
        "domain": "C",
        "difficulty": "medium",
        "status": "pass",
        "elapsed_s": 8.7,
        "lvm_score": 3.0,
        "missing_features": ["fillet", "shell"],  # 2 of 4 missing → 2/4 matched
    },
]


def test_renders_markdown_table_with_required_columns() -> None:
    md = render_markdown_report(_RESULTS, _PROMPTS)
    header = md.splitlines()[0]
    for col in ("id", "domain", "difficulty", "status", "lvm",
                "features", "time", "regression_for", "notes"):
        assert col in header.lower(), f"missing column {col!r} in header: {header!r}"


def test_rows_sorted_by_domain_then_id() -> None:
    """The fixture includes `Z_in_domain_a` (id=Z, domain=A) — this row
    distinguishes a (domain, id) sort from an id-only sort. Under the
    correct sort it lives among the A-domain rows; under id-only it would
    be at the very bottom of the table."""
    md = render_markdown_report(_RESULTS, _PROMPTS)
    body = [ln for ln in md.splitlines() if ln.startswith("|") and "---" not in ln][1:]
    ids = [ln.split("|")[1].strip() for ln in body]
    expected = [
        "A_csv_simple_circle",
        "A_simple_plate",
        "Z_in_domain_a",       # domain A — must group with A even though id starts Z
        "B_csv_iphone_case",
        "C_partial_match",
    ]
    assert ids == expected, f"rows out of (domain, id) order: {ids}"


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
    """Matched count = len(expected_features) - len(missing_features). The
    fixture has three distinct cardinalities (2/2, 4/4, 2/4) so a renderer
    that hardcodes a single n/m string cannot pass them all."""
    md = render_markdown_report(_RESULTS, _PROMPTS)
    plate_line = next(ln for ln in md.splitlines() if "A_simple_plate" in ln)
    z_line = next(ln for ln in md.splitlines() if "Z_in_domain_a" in ln)
    partial_line = next(ln for ln in md.splitlines() if "C_partial_match" in ln)
    assert "2/2" in plate_line, f"expected '2/2' in plate row: {plate_line!r}"
    assert "4/4" in z_line, f"expected '4/4' in Z row: {z_line!r}"
    assert "2/4" in partial_line, (
        f"expected '2/4' (4 expected, 2 missing) in partial-match row: {partial_line!r}"
    )


def test_features_matched_clamps_negative() -> None:
    """If a buggy LVM run reports more missing_features than expected, the
    matched count must clamp to 0 rather than render as a negative number."""
    prompts = [{
        "id": "X_overcount", "domain": "X", "difficulty": "easy",
        "expected_features": ["a", "b"],
    }]
    results = [{
        "id": "X_overcount", "domain": "X", "difficulty": "easy",
        "status": "pass", "elapsed_s": 1.0, "lvm_score": 2.0,
        "missing_features": ["a", "b", "c", "d"],  # over-reported
    }]
    md = render_markdown_report(results, prompts)
    assert "-2/2" not in md and "0/2" in md, (
        f"matched count must clamp to 0 on over-report; got: {md}"
    )


def test_pipe_in_notes_is_escaped() -> None:
    """Markdown tables break if a cell contains an unescaped pipe. The
    iPhone fixture's notes contains a literal `|` which must render as `\\|`."""
    md = render_markdown_report(_RESULTS, _PROMPTS)
    iphone_line = next(ln for ln in md.splitlines() if "B_csv_iphone_case" in ln)
    assert "\\|" in iphone_line, (
        f"expected escaped pipe in notes cell; row: {iphone_line!r}"
    )
    # And the row must still have the canonical 9 separators (10 pipes total).
    assert iphone_line.count("|") - iphone_line.count("\\|") == 10, (
        f"unescaped pipe count off; row: {iphone_line!r}"
    )


def test_handles_missing_elapsed_s() -> None:
    """A result with no `elapsed_s` (e.g. crash before timing) must render
    a blank time cell rather than the literal string 'None'."""
    prompts = [{
        "id": "X_no_time", "domain": "X", "difficulty": "easy",
        "expected_features": [],
    }]
    results = [{
        "id": "X_no_time", "domain": "X", "difficulty": "easy",
        "status": "fail", "error": "crashed before timer",
    }]
    md = render_markdown_report(results, prompts)
    cells = [c.strip() for c in md.splitlines()[-1].split("|")]
    assert "None" not in cells, f"raw None leaked into time cell: {md!r}"


def test_regression_for_cell_lists_tags() -> None:
    md = render_markdown_report(_RESULTS, _PROMPTS)
    iphone_line = next(ln for ln in md.splitlines() if "B_csv_iphone_case" in ln)
    plate_line = next(ln for ln in md.splitlines() if "A_simple_plate" in ln)
    # Use the escape-aware splitter so the iPhone notes' literal pipe
    # doesn't shift the column count.
    iphone_cells = [c.strip() for c in _ROW_SPLIT.split(iphone_line)]
    # Layout: ['', id, domain, difficulty, status, lvm, features, time_ms,
    # regression_for, notes, '']  → 11 cells, regression_for at index 8.
    assert iphone_cells[8] == "B", (
        f"regression_for cell mismatch; cells={iphone_cells}"
    )
    plate_cells = [c.strip() for c in _ROW_SPLIT.split(plate_line)]
    assert plate_cells[8] == "", (
        f"untagged plate row should have empty regression_for cell, got "
        f"{plate_cells[8]!r}; full cells={plate_cells}"
    )
    assert "None" not in plate_cells and "[]" not in plate_cells, (
        f"raw empty marker leaked: {plate_line!r}"
    )
    # Multi-tag entry renders comma-separated.
    partial_line = next(ln for ln in md.splitlines() if "C_partial_match" in ln)
    partial_cells = [c.strip() for c in _ROW_SPLIT.split(partial_line)]
    assert partial_cells[8] == "C,D", (
        f"multi-tag regression_for should be comma-joined; got {partial_cells[8]!r}"
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
        test_features_matched_clamps_negative,
        test_pipe_in_notes_is_escaped,
        test_handles_missing_elapsed_s,
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
