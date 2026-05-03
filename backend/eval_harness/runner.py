"""Machinist reliability eval harness.

Runs a curated set of part-level prompts through ``run_critic_loop`` and
records pass/fail + elapsed time per prompt. Skips the Planner: each prompt
is fed directly as a ``PartDefinition``, which isolates Machinist quality
from upstream planner variance.

Usage (from inside the backend conda env, with GEMINI_API_KEY_HEAVY set):

    python -m eval_harness.runner                       # full eval, latest results
    python -m eval_harness.runner --concurrency 4
    python -m eval_harness.runner --filter A_           # only prompts whose id starts with A_
    python -m eval_harness.runner --regression-for B    # only prompts tagged regression_for: ["B", ...]
    python -m eval_harness.runner --regression-for B --dry-run   # list matching ids, run nothing
    python -m eval_harness.runner --baseline runs/<file>.jsonl   # diff against a prior run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import time
from collections import defaultdict
from typing import Any

_HERE = pathlib.Path(__file__).resolve().parent
_BACKEND = _HERE.parent

# Make the backend modules importable when invoked as `python -m eval_harness.runner`.
sys.path.insert(0, str(_BACKEND))

# Heavy imports (google.generativeai, agents, evaluation, schemas) are loaded
# lazily inside _run_one / main() so --dry-run and the unit tests can resolve
# `_filter_prompts` without paying for the Gemini SDK or the orchestrator.

# Threshold for the --strict-lvm gate. 3 of 5 = "mostly correct".
_STRICT_LVM_THRESHOLD = 3.0


_PROMPTS_FILE = _HERE / "prompts.json"
# Default to a writable location. /app is bind-mounted read-only inside the
# backend container, so we can't write under _HERE there. Override via
# MIRUM_EVAL_RUNS_DIR if you want runs alongside the source tree.
_RUNS_DIR = pathlib.Path(
    os.environ.get("MIRUM_EVAL_RUNS_DIR", "/tmp/mirum/eval_runs")
)

# Filter helpers live in a dep-free sibling so the test suite (and any
# future tooling) can call them without pulling in google-generativeai.
from eval_harness._prompts_loader import filter_prompts as _filter_prompts  # noqa: E402


def _suppress_side_effects() -> None:
    """Disable retrieval-index updates and LVM scoring during eval.

    Eval prompts shouldn't pollute the few-shot retrieval index — that would
    make retrieval-augmented retrieval circular and make production users see
    eval text in their prompts. Likewise, the fire-and-forget LVM scorer adds
    a per-success Gemini call we don't need.
    """
    import agents as _agents_mod
    _agents_mod.add_to_index = lambda **kwargs: None  # type: ignore[assignment]
    _agents_mod._score_and_log = lambda *a, **kw: asyncio.sleep(0)  # type: ignore[assignment]


async def _run_one(
    prompt: dict[str, Any],
    max_retries: int,
    strict_lvm: bool = False,
) -> dict[str, Any]:
    from agents import run_critic_loop
    from evaluation import compute_lvm_score
    from schemas import PartDefinition

    part = PartDefinition(
        part_id=prompt["id"],
        description=prompt["description"],
        anchor_tags=[">Z", "<Z", ">X", "<X", ">Y", "<Y"],
    )
    t0 = time.monotonic()
    record: dict[str, Any] = {
        "id": prompt["id"],
        "domain": prompt["domain"],
        "difficulty": prompt.get("difficulty", "unknown"),
    }
    code: str | None = None
    try:
        code, _ = await run_critic_loop(part, max_retries=max_retries, save_step=False)
        record["status"] = "pass"
        record["code_length"] = len(code)
    except Exception as exc:  # ScriptError or anything else
        record["status"] = "fail"
        msg = str(exc)
        # Pull the last traceback line if the message includes one — usually
        # the actual exception type that killed the script.
        last_line = next(
            (ln.strip() for ln in reversed(msg.splitlines()) if ln.strip()),
            msg,
        )
        record["error"] = last_line[:280]

    # LVM scoring on every pass (cost is one extra Gemini call per success).
    # Independent of the production fire-and-forget call, which the runner
    # no-ops in _suppress_side_effects.
    if record["status"] == "pass" and code is not None:
        score = await compute_lvm_score(prompt["description"], code, prompt["id"])
        record["lvm_score"] = score.get("lvm_score")
        record["missing_features"] = score.get("missing_features", [])
        if strict_lvm and isinstance(record["lvm_score"], (int, float)) \
                and record["lvm_score"] < _STRICT_LVM_THRESHOLD:
            record["status"] = "fail"
            record["error"] = (
                f"LVM_BELOW_THRESHOLD: score={record['lvm_score']:.1f} "
                f"< {_STRICT_LVM_THRESHOLD}"
            )

    record["elapsed_s"] = round(time.monotonic() - t0, 2)
    return record


async def _run_all(
    prompts: list[dict[str, Any]],
    max_retries: int,
    concurrency: int,
    strict_lvm: bool = False,
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(p: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            print(f"[run]  {p['id']}", flush=True)
            r = await _run_one(p, max_retries, strict_lvm=strict_lvm)
            mark = "PASS" if r["status"] == "pass" else "FAIL"
            err = f"  ({r.get('error', '')[:80]})" if r["status"] == "fail" else ""
            lvm = r.get("lvm_score")
            lvm_tag = f"  lvm={lvm:.1f}" if isinstance(lvm, (int, float)) else ""
            print(f"[{mark}] {p['id']}  {r['elapsed_s']}s{lvm_tag}{err}", flush=True)
            return r

    return await asyncio.gather(*[_bounded(p) for p in prompts])


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[len(s) // 2]


def _summarize(results: list[dict[str, Any]]) -> str:
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_domain[r["domain"]].append(r)
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "pass")
    elapsed = [r["elapsed_s"] for r in results]
    elapsed.sort()
    p50 = elapsed[len(elapsed) // 2] if elapsed else 0.0
    p95 = elapsed[int(len(elapsed) * 0.95)] if elapsed else 0.0

    lvm_all = [r["lvm_score"] for r in results if isinstance(r.get("lvm_score"), (int, float))]
    lvm_median = _median(lvm_all)

    lines = [
        f"Total: {passed}/{total} passed ({100 * passed / total:.0f}%)"
        if total else "Total: 0",
    ]
    for domain in sorted(by_domain):
        rs = by_domain[domain]
        p = sum(1 for r in rs if r["status"] == "pass")
        domain_lvms = [r["lvm_score"] for r in rs if isinstance(r.get("lvm_score"), (int, float))]
        if domain_lvms:
            lines.append(
                f"  Domain {domain}: {p}/{len(rs)} ({100 * p / len(rs):.0f}%) "
                f"| lvm median {_median(domain_lvms):.1f}"
            )
        else:
            lines.append(f"  Domain {domain}: {p}/{len(rs)} ({100 * p / len(rs):.0f}%)")
    lines.append(f"Elapsed p50: {p50}s | p95: {p95}s")
    if lvm_all:
        lines.append(f"LVM median: {lvm_median:.1f} (n={len(lvm_all)})")

    fails = [r for r in results if r["status"] == "fail"]
    if fails:
        lines.append("")
        lines.append("Failures:")
        for r in fails:
            lines.append(
                f"  - {r['id']} ({r['domain']}, {r['difficulty']}) "
                f"[{r['elapsed_s']}s]: {r.get('error', '')}"
            )
    return "\n".join(lines)


def _diff(baseline: list[dict[str, Any]], current: list[dict[str, Any]]) -> str:
    by_id_b = {r["id"]: r["status"] for r in baseline}
    by_id_c = {r["id"]: r["status"] for r in current}
    newly_passing = sorted(
        rid for rid in by_id_c
        if by_id_c[rid] == "pass" and by_id_b.get(rid) == "fail"
    )
    newly_failing = sorted(
        rid for rid in by_id_c
        if by_id_c[rid] == "fail" and by_id_b.get(rid) == "pass"
    )
    new_prompts = sorted(set(by_id_c) - set(by_id_b))
    dropped = sorted(set(by_id_b) - set(by_id_c))

    p_b = sum(1 for s in by_id_b.values() if s == "pass")
    p_c = sum(1 for s in by_id_c.values() if s == "pass")
    delta = p_c - p_b

    lines = [
        f"Baseline: {p_b}/{len(by_id_b)} -> Current: {p_c}/{len(by_id_c)}  "
        f"({'+' if delta >= 0 else ''}{delta})",
    ]
    if newly_passing:
        lines.append(f"  Newly passing ({len(newly_passing)}): {', '.join(newly_passing)}")
    if newly_failing:
        lines.append(f"  Newly failing ({len(newly_failing)}): {', '.join(newly_failing)}")
    if new_prompts:
        lines.append(f"  Added prompts: {', '.join(new_prompts)}")
    if dropped:
        lines.append(f"  Dropped prompts: {', '.join(dropped)}")
    return "\n".join(lines)


def _load_prompts(
    filter_prefix: str | None,
    regression_for: str | None = None,
) -> list[dict[str, Any]]:
    raw = json.loads(_PROMPTS_FILE.read_text(encoding="utf-8"))
    filtered = _filter_prompts(
        raw,
        id_prefix=filter_prefix,
        regression_for=regression_for,
    )
    if not filtered:
        criteria_parts: list[str] = []
        if filter_prefix:
            criteria_parts.append(f"--filter {filter_prefix!r}")
        if regression_for:
            criteria_parts.append(f"--regression-for {regression_for!r}")
        criteria = " and ".join(criteria_parts) or "(no filter)"
        raise SystemExit(f"No prompts matched {criteria}")
    return filtered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--filter", default=None,
                        help="Only run prompts whose id starts with this prefix.")
    parser.add_argument(
        "--regression-for", dest="regression_for", default=None,
        choices=["A", "B", "C", "D", "E"],
        help=(
            "Only run prompts whose `regression_for` array contains the given "
            "letter. Used by Worktree gates to run the regression slice tied "
            "to a specific fix (see plan §9 F-03). ANDs with --filter."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Print the ids that match the filters, then exit without running "
            "the pipeline. Lets a worktree gate verify it picked up the "
            "right prompts before paying for Gemini calls."
        ),
    )
    parser.add_argument("--baseline", default=None,
                        help="Path to a prior runs/*.jsonl file to diff against.")
    parser.add_argument("--out", default=None,
                        help="Path to write JSONL results. Default: runs/<timestamp>.jsonl")
    parser.add_argument(
        "--strict-lvm", action="store_true",
        help=(
            f"Demote a passing prompt to fail when its LVM score is below "
            f"{_STRICT_LVM_THRESHOLD}. Default off so existing baselines stay comparable."
        ),
    )
    args = parser.parse_args()

    # Dry-run path is dep-free (no Gemini, no heavy imports needed at runtime).
    # Resolve filters and exit before touching the pipeline.
    if args.dry_run:
        prompts = _load_prompts(args.filter, args.regression_for)
        for p in prompts:
            tags = p.get("regression_for") or []
            tag_str = f"  [regression_for={','.join(tags)}]" if tags else ""
            print(f"{p['id']}  (domain={p['domain']}, "
                  f"difficulty={p.get('difficulty', 'unknown')}){tag_str}")
        print(f"\n{len(prompts)} prompt(s) matched.")
        return

    heavy_key = os.environ.get("GEMINI_API_KEY_HEAVY")
    if not heavy_key:
        raise SystemExit(
            "Set GEMINI_API_KEY_HEAVY before running the eval. The orchestrator "
            "drives both heavy (gemini-3-flash-preview) and lite (gemini-3.1-flash-"
            "lite-preview) models off this single key."
        )
    import google.generativeai as genai
    genai.configure(api_key=heavy_key)

    _suppress_side_effects()

    prompts = _load_prompts(args.filter, args.regression_for)
    print(f"Running {len(prompts)} prompt(s) with concurrency={args.concurrency}, "
          f"max_retries={args.max_retries}", flush=True)

    t0 = time.monotonic()
    results = asyncio.run(
        _run_all(
            prompts, args.max_retries, args.concurrency,
            strict_lvm=args.strict_lvm,
        )
    )
    total_elapsed = round(time.monotonic() - t0, 1)

    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = pathlib.Path(args.out) if args.out else (
        _RUNS_DIR / f"{int(time.time())}.jsonl"
    )
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    print()
    print(_summarize(results))
    print(f"\nWall-clock elapsed: {total_elapsed}s")
    print(f"Results written to: {out_path}")

    if args.baseline:
        baseline_records = [json.loads(ln) for ln in pathlib.Path(args.baseline).read_text().splitlines() if ln.strip()]
        print()
        print("Diff vs baseline:")
        print(_diff(baseline_records, results))


if __name__ == "__main__":
    main()
