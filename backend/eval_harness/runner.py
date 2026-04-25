"""Machinist reliability eval harness.

Runs a curated set of part-level prompts through ``run_critic_loop`` and
records pass/fail + elapsed time per prompt. Skips the Planner: each prompt
is fed directly as a ``PartDefinition``, which isolates Machinist quality
from upstream planner variance.

Usage (from inside the backend conda env, with GEMINI_API_KEY_HEAVY and GEMINI_API_KEY_LITE set):

    python -m eval_harness.runner                       # full eval, latest results
    python -m eval_harness.runner --concurrency 4
    python -m eval_harness.runner --filter A_           # only prompts whose id starts with A_
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

import google.generativeai as genai

from agents import run_critic_loop  # noqa: E402
import agents as _agents_mod        # noqa: E402
from schemas import PartDefinition  # noqa: E402


_PROMPTS_FILE = _HERE / "prompts.json"
# Default to a writable location. /app is bind-mounted read-only inside the
# backend container, so we can't write under _HERE there. Override via
# MIRUM_EVAL_RUNS_DIR if you want runs alongside the source tree.
_RUNS_DIR = pathlib.Path(
    os.environ.get("MIRUM_EVAL_RUNS_DIR", "/tmp/mirum/eval_runs")
)


def _suppress_side_effects() -> None:
    """Disable retrieval-index updates and LVM scoring during eval.

    Eval prompts shouldn't pollute the few-shot retrieval index — that would
    make retrieval-augmented retrieval circular and make production users see
    eval text in their prompts. Likewise, the fire-and-forget LVM scorer adds
    a per-success Gemini call we don't need.
    """
    _agents_mod.add_to_index = lambda **kwargs: None  # type: ignore[assignment]
    _agents_mod._score_and_log = lambda *a, **kw: asyncio.sleep(0)  # type: ignore[assignment]


async def _run_one(prompt: dict[str, Any], max_retries: int) -> dict[str, Any]:
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
    record["elapsed_s"] = round(time.monotonic() - t0, 2)
    return record


async def _run_all(
    prompts: list[dict[str, Any]],
    max_retries: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(p: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            print(f"[run]  {p['id']}", flush=True)
            r = await _run_one(p, max_retries)
            mark = "PASS" if r["status"] == "pass" else "FAIL"
            err = f"  ({r.get('error', '')[:80]})" if r["status"] == "fail" else ""
            print(f"[{mark}] {p['id']}  {r['elapsed_s']}s{err}", flush=True)
            return r

    return await asyncio.gather(*[_bounded(p) for p in prompts])


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

    lines = [
        f"Total: {passed}/{total} passed ({100 * passed / total:.0f}%)"
        if total else "Total: 0",
    ]
    for domain in sorted(by_domain):
        rs = by_domain[domain]
        p = sum(1 for r in rs if r["status"] == "pass")
        lines.append(f"  Domain {domain}: {p}/{len(rs)} ({100 * p / len(rs):.0f}%)")
    lines.append(f"Elapsed p50: {p50}s | p95: {p95}s")

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
        f"Baseline: {p_b}/{len(by_id_b)} → Current: {p_c}/{len(by_id_c)}  "
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


def _load_prompts(filter_prefix: str | None) -> list[dict[str, Any]]:
    raw = json.loads(_PROMPTS_FILE.read_text(encoding="utf-8"))
    if filter_prefix:
        raw = [p for p in raw if p["id"].startswith(filter_prefix)]
    if not raw:
        raise SystemExit(f"No prompts matched filter {filter_prefix!r}")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--filter", default=None,
                        help="Only run prompts whose id starts with this prefix.")
    parser.add_argument("--baseline", default=None,
                        help="Path to a prior runs/*.jsonl file to diff against.")
    parser.add_argument("--out", default=None,
                        help="Path to write JSONL results. Default: runs/<timestamp>.jsonl")
    args = parser.parse_args()

    heavy_key = os.environ.get("GEMINI_API_KEY_HEAVY")
    lite_key = os.environ.get("GEMINI_API_KEY_LITE")
    if not heavy_key or not lite_key:
        raise SystemExit(
            "Set GEMINI_API_KEY_HEAVY and GEMINI_API_KEY_LITE before running the eval. "
            "The orchestrator uses both: heavy for the Machinist, lite for the classifier."
        )
    # Default global config is heavy; lite-tier call sites swap on demand.
    genai.configure(api_key=heavy_key)

    _suppress_side_effects()

    prompts = _load_prompts(args.filter)
    print(f"Running {len(prompts)} prompt(s) with concurrency={args.concurrency}, "
          f"max_retries={args.max_retries}", flush=True)

    t0 = time.monotonic()
    results = asyncio.run(_run_all(prompts, args.max_retries, args.concurrency))
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
