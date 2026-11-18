#!/usr/bin/env python3
"""Deterministic test sharding and completeness check for the native CI gate.

Stdlib only. Subcommands:

  collect --out FILE
      Collect the offline gate's node IDs (same pytest args as Dockerfile.verify's
      CMD) and write them, one per line.
  run --shard N --total M --out DIR
      Collect, pick this shard's test FILES, run them, and write
      DIR/shard-N.json = {"shard", "exit_code", "ids", "counts"} where "ids" are the
      node IDs pytest actually executed (including skips).
  check --all FILE REPORT.json...
      Fail unless every shard exited 0 and the union of executed IDs equals the
      collected set exactly (nothing lost, nothing duplicated, nothing extra).

Sharding is by whole file (so module/class fixtures stay together): files are
sorted by weight (HINTS, else collected test count) and greedily placed on the
lightest shard (LPT). The result depends only on the collected set.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Keep in sync with Dockerfile.verify CMD (the gate's pytest invocation).
BASE_ARGS = ["-q", "-p", "no:cacheprovider", "--tb=short",
             "--ignore-glob=backend/tests/test_regen_*.py"]

# Optional per-file weights (seconds-ish) measured from CI junit output.
# Files not listed fall back to their collected test count.
HINTS: dict = {}

_ID_RE = re.compile(r"^\S+\.py::.+$")


def file_of(node_id):
    return node_id.split("::", 1)[0]


def parse_collect(text):
    return [ln.rstrip("\r") for ln in text.splitlines() if _ID_RE.match(ln.rstrip("\r"))]


def assign(ids, total, hints=None):
    """Return `total` lists of node IDs; each file lands wholly in one shard."""
    hints = HINTS if hints is None else hints
    by_file = {}
    for i in ids:
        by_file.setdefault(file_of(i), []).append(i)
    weights = {f: float(hints.get(f, len(v))) for f, v in by_file.items()}
    shards = [[] for _ in range(total)]
    loads = [0.0] * total
    for f in sorted(by_file, key=lambda f: (-weights[f], f)):
        n = min(range(total), key=lambda k: (loads[k], k))
        shards[n].extend(sorted(by_file[f]))
        loads[n] += weights[f]
    return shards


def shard_files(ids, shard, total, hints=None):
    return sorted({file_of(i) for i in assign(ids, total, hints)[shard]})


def check(all_ids, runs):
    """Return a list of problems (empty means complete and green)."""
    problems = []
    if not all_ids:
        problems.append("collected set is empty")
    seen = {}
    for n, r in enumerate(runs):
        if r is None:
            problems.append(f"shard {n}: no report (shard did not finish)")
            continue
        if r.get("exit_code") != 0:
            problems.append(f"shard {n}: pytest exit code {r.get('exit_code')}")
        for i in r.get("ids", []):
            seen.setdefault(i, []).append(n)
    want = set(all_ids)
    missing = sorted(want - set(seen))
    extra = sorted(set(seen) - want)
    dups = sorted(i for i, s in seen.items() if len(s) > 1)
    if missing:
        problems.append(f"{len(missing)} collected test(s) missing from shards, e.g. {missing[:5]}")
    if extra:
        problems.append(f"{len(extra)} unexpected test(s) ran, e.g. {extra[:5]}")
    if dups:
        problems.append(f"{len(dups)} duplicate test(s) ran in several shards, e.g. {dups[:5]}")
    return problems


def _collect():
    p = subprocess.run([sys.executable, "-m", "pytest", *BASE_ARGS, "--collect-only"],
                       capture_output=True, text=True)
    ids = parse_collect(p.stdout)
    if p.returncode != 0 or not ids:
        sys.stderr.write(p.stdout[-4000:] + p.stderr[-4000:])
        raise SystemExit(f"collection failed (exit {p.returncode}, {len(ids)} ids)")
    return ids


class _Recorder:
    def __init__(self):
        self.outcomes = {}

    def pytest_runtest_logreport(self, report):
        if report.when == "setup":
            self.outcomes[report.nodeid] = report.outcome  # skips are reported here
        elif report.when == "call":
            self.outcomes[report.nodeid] = report.outcome
        elif report.outcome == "failed":  # teardown error
            self.outcomes[report.nodeid] = "failed"


def _run(shard, total, out):
    import pytest

    ids = _collect()
    files = shard_files(ids, shard, total)
    mine = {i for i in assign(ids, total)[shard]}
    print(f"[ci_shard] shard {shard}/{total}: {len(files)} files, {len(mine)} of {len(ids)} tests",
          flush=True)
    rec = _Recorder()
    code = int(pytest.main([*BASE_ARGS, *files], plugins=[rec]))
    counts = {}
    for o in rec.outcomes.values():
        counts[o] = counts.get(o, 0) + 1
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"shard-{shard}.json").write_text(
        json.dumps({"shard": shard, "exit_code": code, "ids": sorted(rec.outcomes),
                    "counts": counts}), encoding="utf-8")
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--shard", type=int, required=True)
    r.add_argument("--total", type=int, required=True)
    r.add_argument("--out", required=True)
    k = sub.add_parser("check")
    k.add_argument("--all", required=True)
    k.add_argument("reports", nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "collect":
        Path(a.out).write_text("\n".join(_collect()) + "\n", encoding="utf-8")
        return 0
    if a.cmd == "run":
        return _run(a.shard, a.total, a.out)
    all_ids = [ln for ln in Path(a.all).read_text(encoding="utf-8").splitlines() if ln]
    runs = []
    for p in a.reports:
        runs.append(json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).is_file() else None)
    problems = check(all_ids, runs)
    totals = {}
    for r_ in runs:
        for o, n in ((r_ or {}).get("counts") or {}).items():
            totals[o] = totals.get(o, 0) + n
    print(f"collected={len(all_ids)} executed_counts={totals}")
    for p in problems:
        print(f"FAIL: {p}")
    if not problems:
        print("OK: shards cover the collected set exactly")
    return 1 if problems else 0


if __name__ == "__main__":
    # Running as a script puts scripts/ on sys.path[0]; keep it from shadowing test imports.
    here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != here]
    raise SystemExit(main())
