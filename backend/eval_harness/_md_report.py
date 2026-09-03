"""Markdown reporter for runner.py.

Pure-stdlib so backend/tests/test_md_report.py can lock the column contract
without pulling in the orchestrator. The output is a single table whose row
order (domain, id) is stable across runs — diffing two `--report-md` files
is the supported way to spot regressions between runs.

Columns:
  id · domain · difficulty · status · lvm · features · time_ms ·
  regression_for · notes

`features` is rendered as `<matched>/<expected_count>` where matched is
inferred from the runner's `missing_features` list (since missing is what
the LVM scorer returns). Cells that have no value render as empty rather
than `None`/`[]` so the table reads cleanly.
"""
from __future__ import annotations

from typing import Any, Iterable

_COLUMNS = (
    "id",
    "domain",
    "difficulty",
    "status",
    "lvm",
    "features",
    "time_ms",
    "regression_for",
    "notes",
)


def _fmt_lvm(record: dict[str, Any]) -> str:
    score = record.get("lvm_score")
    if isinstance(score, (int, float)):
        return f"{score:.1f}"
    return ""


def _fmt_features(record: dict[str, Any], prompt: dict[str, Any] | None) -> str:
    """Expected features the LVM judge did NOT flag as missing.

    The judge writes `missing_features` in its own words, so the previous
    `len(expected) - len(missing)` ran over two unrelated sets and could
    over- or under-count without bound (a judge naming three gaps for one
    expected feature drove the count negative; one naming a gap outside
    `expected_features` silently docked an unrelated feature).

    Now an expected feature is counted missing only when a judge entry
    actually corresponds to it, so the ratio is well defined over
    `expected_features`. This remains a judge-derived signal, not a
    deterministic check of the geometry.
    """
    expected = (prompt or {}).get("expected_features") or []
    n_expected = len(expected)
    if not n_expected:
        return ""
    missing = record.get("missing_features")
    if missing is None:
        # No LVM run on this record (likely a fail) — leave blank rather
        # than guess. The pass/fail column already conveys the outcome.
        return ""
    if not isinstance(missing, list):
        return ""
    norm_missing = [str(m).strip().lower() for m in missing if str(m).strip()]
    matched = sum(
        1
        for feat in expected
        if not any(
            m in str(feat).strip().lower() or str(feat).strip().lower() in m
            for m in norm_missing
        )
    )
    return f"{matched}/{n_expected}"


def _fmt_time_ms(record: dict[str, Any]) -> str:
    secs = record.get("elapsed_s")
    if isinstance(secs, (int, float)):
        return str(int(round(secs * 1000)))
    return ""


def _fmt_regression_for(prompt: dict[str, Any] | None) -> str:
    tags = (prompt or {}).get("regression_for")
    if isinstance(tags, list) and tags:
        return ",".join(str(t) for t in tags)
    return ""


def _fmt_notes(prompt: dict[str, Any] | None, record: dict[str, Any]) -> str:
    """Prefer the prompt's static `notes` field; fall back to the run-time
    error message on a fail (truncated). Pipe and newline characters are
    escaped so they don't break the table layout."""
    notes = (prompt or {}).get("notes")
    if not notes and record.get("status") == "fail":
        notes = record.get("error", "")
    if not notes:
        return ""
    text = str(notes).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    if len(text) > 160:
        text = text[:157] + "..."
    return text


def render_markdown_report(
    results: Iterable[dict[str, Any]],
    prompts: Iterable[dict[str, Any]],
) -> str:
    """Render a markdown table joining results with the prompt corpus.

    Rows are sorted by `(domain, id)` for line-by-line diffability across
    runs. Result rows whose id has no matching prompt entry are still
    emitted (their `expected_features`/`regression_for`/`notes` cells will
    be empty) so the report reflects what actually ran.
    """
    prompts_by_id: dict[str, dict[str, Any]] = {p["id"]: p for p in prompts}
    rows = sorted(results, key=lambda r: (r.get("domain", ""), r.get("id", "")))

    lines: list[str] = []
    lines.append("| " + " | ".join(_COLUMNS) + " |")
    lines.append("| " + " | ".join(["---"] * len(_COLUMNS)) + " |")
    for r in rows:
        prompt = prompts_by_id.get(r.get("id", ""))
        cells = [
            str(r.get("id", "")),
            str(r.get("domain", "")),
            str(r.get("difficulty", "")),
            str(r.get("status", "")),
            _fmt_lvm(r),
            _fmt_features(r, prompt),
            _fmt_time_ms(r),
            _fmt_regression_for(prompt),
            _fmt_notes(prompt, r),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
