"""Filter-logic tests for backend/eval_harness/_prompts_loader.

Pure-Python; runnable via `python backend/tests/test_runner_filters.py` with
no third-party dependencies. Locks the contract for the runner CLI flags:

  --filter <prefix>      keeps entries whose id startswith <prefix>
  --regression-for <X>   keeps entries whose regression_for array contains X

The two filters AND together. Critical contract that this test pins:
`regression_for` filtering MUST NOT collapse to `domain` filtering. An
A-domain entry that lacks a `regression_for` tag must NOT appear in the
output of `--regression-for A` — only entries explicitly tagged for that
worktree's regression suite should.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from eval_harness._prompts_loader import filter_prompts, load_prompts  # noqa: E402


def test_load_returns_nonempty_list() -> None:
    prompts = load_prompts()
    assert isinstance(prompts, list), "load_prompts must return a list"
    assert prompts, "prompts.json should not be empty"


def test_filter_by_id_prefix() -> None:
    prompts = load_prompts()
    a_only = filter_prompts(prompts, id_prefix="A_")
    assert a_only, "expected at least one entry with id prefix A_"
    assert all(p["id"].startswith("A_") for p in a_only)


def test_regression_for_picks_only_tagged_entries() -> None:
    prompts = load_prompts()
    tagged_a = filter_prompts(prompts, regression_for="A")
    assert tagged_a, "expected at least one entry tagged regression_for=A"
    for p in tagged_a:
        rf = p.get("regression_for")
        assert isinstance(rf, list), f"{p['id']} returned but regression_for is not a list"
        assert "A" in rf, f"{p['id']} returned for regression_for=A but tag missing"


def test_regression_for_does_not_select_by_domain() -> None:
    """Critical: domain != regression_for. A domain-A entry that lacks a
    regression_for tag must NOT appear in --regression-for A output. If this
    contract breaks, every A-domain prompt would silently get dragged into
    Worktree A's pre-merge gate."""
    prompts = load_prompts()
    tagged_a_ids = {p["id"] for p in filter_prompts(prompts, regression_for="A")}
    untagged_a_domain = [
        p for p in prompts
        if p["domain"] == "A" and not isinstance(p.get("regression_for"), list)
    ]
    assert untagged_a_domain, (
        "test premise broken: expected at least one A-domain prompt with no "
        "regression_for tag in the corpus"
    )
    for p in untagged_a_domain:
        assert p["id"] not in tagged_a_ids, (
            f"{p['id']} (domain A, no regression_for) leaked into "
            f"--regression-for A — filter is collapsing to domain"
        )


def test_regression_for_b_returns_only_b_tagged() -> None:
    prompts = load_prompts()
    by_id = {p["id"]: p for p in prompts}
    tagged_b_ids = {p["id"] for p in filter_prompts(prompts, regression_for="B")}
    assert tagged_b_ids, "expected at least one entry tagged regression_for=B"
    for pid in tagged_b_ids:
        rf = by_id[pid].get("regression_for", [])
        assert isinstance(rf, list) and "B" in rf, (
            f"{pid} returned for regression_for=B but tag missing or wrong shape"
        )


def test_filter_combination_is_and() -> None:
    """--filter X_ --regression-for Y returns only entries that match BOTH."""
    prompts = load_prompts()
    both = filter_prompts(prompts, id_prefix="C_", regression_for="C")
    for p in both:
        assert p["id"].startswith("C_"), f"{p['id']} matched but id prefix != C_"
        rf = p.get("regression_for") or []
        assert "C" in rf, f"{p['id']} matched but regression_for missing C"


def test_no_filter_returns_all() -> None:
    prompts = load_prompts()
    assert len(filter_prompts(prompts)) == len(prompts)


def test_unknown_regression_tag_returns_empty() -> None:
    """Tag not in {A,B,C,D,E} → no matches. Lets `--regression-for Z` be a
    silent no-op rather than an opaque crash, but the runner CLI should
    still surface that as 'no prompts matched'."""
    prompts = load_prompts()
    assert filter_prompts(prompts, regression_for="Z") == []


def _run_all() -> int:
    funcs = [
        test_load_returns_nonempty_list,
        test_filter_by_id_prefix,
        test_regression_for_picks_only_tagged_entries,
        test_regression_for_does_not_select_by_domain,
        test_regression_for_b_returns_only_b_tagged,
        test_filter_combination_is_and,
        test_no_filter_returns_all,
        test_unknown_regression_tag_returns_empty,
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
