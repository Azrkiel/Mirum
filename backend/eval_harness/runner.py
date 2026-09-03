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
import re
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

# Filter and reporting helpers live in dep-free siblings so the test suite
# (and any future tooling) can call them without pulling in google-generativeai.
from eval_harness._prompts_loader import filter_prompts as _filter_prompts  # noqa: E402
from eval_harness._md_report import render_markdown_report as _render_md  # noqa: E402


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


# ---------------------------------------------------------------------------
# Domain D verification: NACA profile + XFOIL convergence
# ---------------------------------------------------------------------------

# Matches the profile actually requested of the deterministic helpers:
#   make_naca_wire("2412", ...) / make_naca_airfoil('0012', ...)
#   naca_code="2412"  /  naca4_points('0012')
_NACA_CALL_RE = re.compile(
    r"""(?:naca_code\s*=\s*|make_naca\w*\s*\(\s*|naca4_points\s*\(\s*)"""
    r"""['"](\d{4})['"]""",
    re.IGNORECASE | re.VERBOSE,
)


def _naca_codes_in(code: str) -> set[str]:
    """Return every NACA 4-digit code the script actually asks for.

    Deliberately anchored to the call sites rather than a bare 4-digit scan:
    '0012' also appears as a plain dimension in plenty of scripts.
    """
    return set(_NACA_CALL_RE.findall(code or ""))


def _check_naca(record: dict[str, Any], prompt: dict[str, Any], code: str) -> None:
    """Fail the record when the script built a different airfoil than requested.

    Without this a clean NACA 0024 solid scores identically to the requested
    0012 — the geometry certificate only checks structural validity, never
    whether the profile matches the brief.
    """
    expected = str(prompt.get("expected_naca_code") or "").strip()
    if not expected:
        return
    found = _naca_codes_in(code)
    if expected in found:
        return
    record["status"] = "fail"
    if found:
        record["error"] = (
            f"NACA_MISMATCH: expected {expected}, script uses {', '.join(sorted(found))}"
        )
    else:
        record["error"] = (
            f"NACA_NOT_FOUND: expected {expected}; script references no NACA "
            f"profile via cad_utils/cad_aero helpers"
        )


async def _check_aero(
    record: dict[str, Any],
    prompt: dict[str, Any],
    step_path: str,
) -> None:
    """Run an XFOIL polar on the GENERATED geometry and gate on convergence.

    Only invoked under --with-aero for prompts carrying min_converged_alpha.

    Critically, analyze_wing_step falls back to the *analytical* NACA
    definition when the OCC mid-span slice fails, which would silently measure
    XFOIL against a textbook airfoil instead of the model's output. That
    fallback sets dat_path=None, so we treat it as a failure: for a geometry
    eval, "XFOIL could not read your solid" is a real negative result.
    """
    threshold = prompt.get("min_converged_alpha")
    if threshold is None:
        return
    if record["status"] != "pass":
        return
    if not step_path or not os.path.exists(step_path):
        record["status"] = "fail"
        record["error"] = "AERO_NO_STEP: no STEP file was produced to analyse"
        return

    import sys as _sys
    _repo_root = pathlib.Path(__file__).resolve().parent.parent.parent
    if str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))

    try:
        from analysis.airfoil import analyze_wing_step
    except ImportError as exc:
        record["aero_skipped"] = f"analysis package unavailable: {exc}"
        return

    # span/chord are not part of the prompt schema; fall back to the measured
    # envelope. span sets the mid-span cut plane, chord only scales Reynolds.
    span_mm = float(prompt.get("span_mm") or 0.0)
    chord_mm = float(prompt.get("chord_mm") or 0.0)
    if not span_mm or not chord_mm:
        extents = sorted(record.get("bbox_extents") or [], reverse=True)
        if len(extents) >= 2:
            span_mm = span_mm or extents[0]
            chord_mm = chord_mm or extents[1]
    if not span_mm or not chord_mm:
        record["aero_skipped"] = "span/chord unknown (no bbox measured)"
        return

    try:
        polar = await asyncio.to_thread(
            analyze_wing_step,
            step_path,
            str(prompt.get("expected_naca_code") or ""),
            span_mm,
            chord_mm,
            {},
        )
    except Exception as exc:
        record["status"] = "fail"
        record["error"] = f"AERO_FAILED: {type(exc).__name__}: {exc}"[:280]
        return

    converged = int(polar.get("converged_count") or 0)
    record["converged_count"] = converged
    record["aero_from_geometry"] = polar.get("dat_path") is not None

    if not record["aero_from_geometry"]:
        record["status"] = "fail"
        record["error"] = (
            "AERO_ANALYTICAL_FALLBACK: the OCC mid-span slice failed, so XFOIL "
            "ran on the textbook NACA profile rather than the generated solid"
        )
        return
    if converged < int(threshold):
        record["status"] = "fail"
        record["error"] = (
            f"AERO_UNDER_CONVERGED: {converged} converged alpha points "
            f"< required {threshold}"
        )


def _count_matched_features(
    expected: list[str], missing: list[str] | None
) -> tuple[int, int] | None:
    """Reconcile the judge's free-text 'missing' list against expected_features.

    The previous arithmetic (len(expected) - len(missing)) ran over two
    unrelated sets: the judge invents its own phrasing, so the difference
    could over- or under-count without bound. Here an expected feature counts
    as missing only when a judge entry actually corresponds to it.
    """
    if not expected:
        return None
    if not isinstance(missing, list):
        return None
    norm_missing = [str(m).strip().lower() for m in missing if str(m).strip()]
    matched = 0
    for feat in expected:
        f = str(feat).strip().lower()
        if not any(m in f or f in m for m in norm_missing):
            matched += 1
    return matched, len(expected)


async def _run_one(
    prompt: dict[str, Any],
    max_retries: int,
    strict_lvm: bool = False,
    with_aero: bool = False,
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
    _step: str = ""
    try:
        # run_critic_loop returns (code, step_path, geometry_cert); the harness
        # only needs the code. (Pre-existing main bug: this unpacked 2 of 3,
        # raising "too many values to unpack" for every prompt — see main.py
        # callers which already unpack 3.)
        # Keep the STEP only when an XFOIL polar has to read it back.
        _need_step = bool(with_aero and prompt.get("min_converged_alpha"))
        code, _step, _report = await run_critic_loop(
            part, max_retries=max_retries, save_step=_need_step
        )
        record["status"] = "pass"
        record["code_length"] = len(code)
        # Surface the measured envelope so --with-aero can derive span/chord,
        # and so a report reader can see what was actually built.
        _cert = getattr(_report, "geometry_cert", None)
        _bbox = (_cert or {}).get("bounding_box") if isinstance(_cert, dict) else None
        if _bbox:
            record["bbox_extents"] = [
                _bbox.get("x_mm"), _bbox.get("y_mm"), _bbox.get("z_mm")
            ]
        # The delivered geometry compiled but failed a verification check;
        # the harness must not report that as a clean pass.
        if getattr(_report, "degraded", False):
            record["degraded"] = True

        # Domain D: did it build the airfoil that was actually asked for?
        _check_naca(record, prompt, code)
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

    if with_aero:
        try:
            await _check_aero(record, prompt, _step or "")
        finally:
            if _step:
                try:
                    os.remove(_step)
                except OSError:
                    pass

    record["elapsed_s"] = round(time.monotonic() - t0, 2)
    return record


async def _run_all(
    prompts: list[dict[str, Any]],
    max_retries: int,
    concurrency: int,
    strict_lvm: bool = False,
    with_aero: bool = False,
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(p: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            print(f"[run]  {p['id']}", flush=True)
            r = await _run_one(p, max_retries, strict_lvm=strict_lvm,
                               with_aero=with_aero)
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--filter", default=None,
                        help="Only run prompts whose id starts with this prefix.")
    parser.add_argument(
        "--regression-for", dest="regression_for", default=None,
        choices=["A", "B", "C", "D", "E", "F", "G"],
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
        "--report-md", dest="report_md", default=None,
        help=(
            "Optional path to write a markdown table summarising the run "
            "(columns: id, domain, difficulty, status, lvm, features matched, "
            "time_ms, regression_for, notes; sorted by domain then id for "
            "diffability). Used by the worktree merge gate (plan §9 F-04)."
        ),
    )
    parser.add_argument(
        "--with-aero", dest="with_aero", action="store_true",
        help=(
            "For prompts carrying `min_converged_alpha`, export the generated "
            "solid and run an XFOIL polar on it, failing the prompt if too few "
            "alpha points converge — or if XFOIL had to fall back to the "
            "analytical NACA profile because the solid could not be sliced. "
            "Off by default: needs xfoil on PATH, is much slower, and would "
            "otherwise silently change the default corpus baseline."
        ),
    )
    parser.add_argument(
        "--strict-lvm", action="store_true",
        help=(
            f"Demote a passing prompt to fail when its LVM score is below "
            f"{_STRICT_LVM_THRESHOLD}. Default off so existing baselines stay comparable."
        ),
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()

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
            with_aero=args.with_aero,
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

    if args.report_md:
        report_path = pathlib.Path(args.report_md)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_render_md(results, prompts), encoding="utf-8")
        print(f"Markdown report written to: {report_path}")

    if args.baseline:
        baseline_records = [json.loads(ln) for ln in pathlib.Path(args.baseline).read_text().splitlines() if ln.strip()]
        print()
        print("Diff vs baseline:")
        print(_diff(baseline_records, results))


if __name__ == "__main__":
    main()
