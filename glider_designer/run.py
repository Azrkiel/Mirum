"""
Generate wings, fuselages and tails, assemble every combination, keep the
flyable ones, rank them and export the best.

    python run.py                       # uses config.py
    python run.py --wings 6 --fuselages 4 --tails 5 --workers 4 --top 5
    python run.py --objective duration  # rank by time aloft instead of distance

Outputs go to ./results (or --out).
"""
from __future__ import annotations

import argparse
import copy
import csv
import itertools
import json
import math
import os
import random
import shutil
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CONFIG
from components import WingParams, FuselageParams, TailParams, params_dict
from assembly import Glider
from analysis import evaluate
import export


def make_parts(cfg):
    rng = random.Random(cfg["seed"])
    R, S = cfg["ranges"], cfg["sampling"]
    wings = [WingParams.random(rng, R["wing"], i + 1) for i in range(S["n_wings"])]
    fuses = [FuselageParams.random(rng, R["fuselage"], i + 1) for i in range(S["n_fuselages"])]
    tails = [TailParams.random(rng, R["tail"], i + 1) for i in range(S["n_tails"])]
    return wings, fuses, tails


def _job(args):
    w, f, t, cfg = args
    return evaluate(Glider(w, f, t, cfg))


def write_csv(path, rows, first_cols):
    keys = list(first_cols)
    for r in rows:
        for k in r:
            if k not in keys and k != "traceback":
                keys.append(k)
    with open(path, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow({k: (f"{v:.5g}" if isinstance(v, float) else v) for k, v in r.items()})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wings", type=int)
    ap.add_argument("--fuselages", type=int)
    ap.add_argument("--tails", type=int)
    ap.add_argument("--max-combos", type=int)
    ap.add_argument("--objective", choices=["distance", "duration"])
    ap.add_argument("--top", type=int)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", default="results")
    ap.add_argument("--export-only", action="store_true",
                    help="skip evaluation; re-export the best designs from an existing --out folder")
    a = ap.parse_args()

    cfg = copy.deepcopy(CONFIG)
    if a.wings: cfg["sampling"]["n_wings"] = a.wings
    if a.fuselages: cfg["sampling"]["n_fuselages"] = a.fuselages
    if a.tails: cfg["sampling"]["n_tails"] = a.tails
    if a.max_combos: cfg["sampling"]["max_combinations"] = a.max_combos
    if a.objective: cfg["mission"]["objective"] = a.objective
    if a.top: cfg["export"]["top_k"] = a.top
    if a.seed is not None: cfg["seed"] = a.seed

    out = a.out
    os.makedirs(out, exist_ok=True)
    if a.export_only:
        valid, parts = load_results(out)
        export_best(valid, parts, cfg, out)
        return

    # ---- 1. generate parts ----
    wings, fuses, tails = make_parts(cfg)
    write_csv(os.path.join(out, "parts_wings.csv"), [params_dict(p) for p in wings], ["id"])
    write_csv(os.path.join(out, "parts_fuselages.csv"), [params_dict(p) for p in fuses], ["id"])
    write_csv(os.path.join(out, "parts_tails.csv"), [params_dict(p) for p in tails], ["id"])

    combos = list(itertools.product(wings, fuses, tails))
    cap = cfg["sampling"]["max_combinations"]
    if cap and cap < len(combos):
        combos = random.Random(cfg["seed"]).sample(combos, cap)
    print(f"{len(wings)} wings x {len(fuses)} fuselages x {len(tails)} tails -> "
          f"evaluating {len(combos)} gliders on {a.workers} worker(s)...")

    # ---- 2. evaluate ----
    t0 = time.time()
    jobs = [(w, f, t, cfg) for w, f, t in combos]
    results = []
    with Pool(a.workers) as pool:
        for i, r in enumerate(pool.imap_unordered(_job, jobs, chunksize=2), 1):
            results.append(r)
            if i % 25 == 0 or i == len(jobs):
                n_ok = sum(x["valid"] for x in results)
                print(f"  {i}/{len(jobs)} done, {n_ok} valid, {time.time() - t0:.0f} s")

    valid = sorted([r for r in results if r["valid"]], key=lambda r: -r["score"])
    for i, r in enumerate(valid, 1):
        r["rank"] = i
    first = ["rank", "id", "valid", "score", "L_over_D", "sink_rate_m_s", "glide_speed_m_s", "mass_kg",
             "ballast_kg", "cg_x_m", "tail_incidence_deg", "static_margin", "fail_reasons"]
    write_csv(os.path.join(out, "all_designs.csv"),
              valid + [r for r in results if not r["valid"]], first)
    write_csv(os.path.join(out, "ranked_valid_designs.csv"), valid, first)

    # ---- 3. summary ----
    lines = [f"Objective: {cfg['mission']['objective']}   evaluated: {len(results)}   "
             f"valid: {len(valid)} ({100 * len(valid) / max(1, len(results)):.0f}%)", ""]
    reasons = Counter(x.strip() for r in results if not r["valid"] for x in r["fail_reasons"].split(";") if x.strip())
    lines.append("Why designs were rejected (one design can fail several checks):")
    lines += [f"  {n:5d}  {k}" for k, n in reasons.most_common()]
    for key, label in [("tail_kind", "tail type"), ("wing_style", "wing style"), ("fuselage_style", "fuselage style")]:
        groups = defaultdict(list)
        for r in results:
            groups[r[key]].append(r)
        lines += ["", f"By {label}:  pass rate | best score | median score of valid"]
        for k, rs in sorted(groups.items()):
            ok = [r["score"] for r in rs if r["valid"]]
            med = sorted(ok)[len(ok) // 2] if ok else float("nan")
            lines.append(f"  {k:14s} {100 * len(ok) / len(rs):5.0f}%   "
                         f"{max(ok) if ok else float('nan'):8.2f}   {med:8.2f}")
    for parts, label in [(wings, "wing"), (fuses, "fuselage"), (tails, "tail")]:
        col = {"wing": "wing", "fuselage": "fuselage", "tail": "tail"}[label]
        lines += ["", f"Most successful {label}s (appearances among valid designs):"]
        cnt = Counter(r[col] for r in valid)
        lines += [f"  {pid}: {n}" for pid, n in cnt.most_common(5)]
    lines += ["", "Top 10:"]
    for r in valid[:10]:
        lines.append(f"  #{r['rank']:<3d} {r['id']:14s} {r['wing_style']:10s} {r['fuselage_style']:9s} "
                     f"{r['tail_kind']:12s} L/D {r['L_over_D']:5.1f}  sink {r['sink_rate_m_s']:.2f} m/s  "
                     f"speed {r['glide_speed_m_s']:.1f} m/s  mass {r['mass_kg'] * 1000:.0f} g")
    summary = "\n".join(lines)
    with open(os.path.join(out, "summary.txt"), "w") as fh:
        fh.write(summary + "\n")
    print("\n" + summary)

    # ---- 4. export the best ----
    export_best(valid, wings + fuses + tails, cfg, out)
    print(f"\nDone in {time.time() - t0:.0f} s. Results in {os.path.abspath(out)}")


def export_best(valid, parts, cfg, out):
    """STL / drawings / templates / spec sheets for the best designs + gallery."""
    by_id = {p.id: p for p in parts}
    top_dir = os.path.join(out, "top_designs")
    shutil.rmtree(top_dir, ignore_errors=True)   # don't mix with an older run
    os.makedirs(top_dir, exist_ok=True)
    n_need = max(cfg["export"]["top_k"], cfg["export"]["gallery_size"])
    cap_w = cfg["export"].get("max_designs_per_wing")
    picked, per_wing = [], Counter()
    for r in valid:
        if cap_w and per_wing[r["wing"]] >= cap_w:
            continue
        per_wing[r["wing"]] += 1
        picked.append(r)
        if len(picked) >= n_need:
            break
    gallery_items = []
    for pick_no, r in enumerate(picked, 1):
        gl = Glider(by_id[r["wing"]], by_id[r["fuselage"]], by_id[r["tail"]], cfg)
        gl.ballast_kg, gl.tail_incidence_deg = r["ballast_kg"], r["tail_incidence_deg"]
        gl.wing_x = r["wing_le_x_m"]
        if cfg["mission"]["payload_mass_kg"] > 0:
            gl.payload_x = r["payload_x_m"]
        if pick_no <= cfg["export"]["gallery_size"]:
            gallery_items.append((r["rank"], gl, r))
        if pick_no > cfg["export"]["top_k"]:
            continue
        d = os.path.join(top_dir, f"rank{r['rank']:02d}_{r['id']}")
        os.makedirs(d, exist_ok=True)
        export.export_stls(gl, d)
        export.three_view(gl, r, os.path.join(d, "three_view.png"))
        export.export_templates(gl, d)
        export.spec_sheet(gl, r, r["rank"], os.path.join(d, "README.md"))
        with open(os.path.join(d, "design.json"), "w") as fh:
            json.dump({"wing": params_dict(gl.wing), "fuselage": params_dict(gl.fuse),
                       "tail": params_dict(gl.tail), "results": {k: v for k, v in r.items() if k != "traceback"}},
                      fh, indent=2, default=float)
        print(f"  exported #{r['rank']} {r['id']} -> {d}")
    export.gallery(gallery_items, os.path.join(out, "top_designs_gallery.png"))


def _num(v):
    try:
        return int(v) if v.lstrip("-").isdigit() else float(v)
    except (ValueError, AttributeError):
        return {"True": True, "False": False}.get(v, v)


def load_results(out):
    """Re-load a finished run (for --export-only)."""
    def rows(name):
        with open(os.path.join(out, name)) as fh:
            return [{k: _num(v) for k, v in r.items()} for r in csv.DictReader(fh)]
    parts = [WingParams(**{k: (str(v) if k in ("id", "style", "naca") else v) for k, v in r.items()})
             for r in rows("parts_wings.csv")]
    for r in parts:
        r.naca = r.naca.zfill(4)
    parts += [FuselageParams(**{k: (str(v) if k in ("id", "style") else v) for k, v in r.items()})
              for r in rows("parts_fuselages.csv")]
    parts += [TailParams(**{k: (str(v).zfill(4) if k == "airfoil" else (str(v) if k in ("id", "kind") else v))
                            for k, v in r.items()}) for r in rows("parts_tails.csv")]
    return rows("ranked_valid_designs.csv"), parts


if __name__ == "__main__":
    main()
