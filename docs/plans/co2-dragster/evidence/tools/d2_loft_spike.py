"""D2 loft spike: measure the lofted-body kernel across presets, boundary cases and settings.

Run inside the verify image (real OpenCASCADE), one container at a time, e.g. from the repo root:

    MSYS_NO_PATHCONV=1 docker run --rm --name d2-spike --memory 3g --network none \
      --tmpfs /tmp:rw,exec,size=1g,mode=1777 mirum-verify:<tag> \
      python docs/plans/co2-dragster/evidence/tools/d2_loft_spike.py > spike.jsonl

(the verify image does not copy ``docs/``; mount it, or the file, read-only).

Modes:
    --check              validate every case spec against the D1 rules (no CAD)
    (default)            full matrix; one fresh interpreter per build, JSON line per build
    --diag               F1: VmPeak/RSS of v1 wedge vs lofted presets, with/without RLIMIT_AS
                         1536 MiB and MALLOC_ARENA_MAX=2 (kernel defaults from tolerances.py)
    --one '<json>'       internal: measure one build in this process
    --table FILE.jsonl   print the Markdown tables for the evidence doc

Each build runs in a fresh ``python`` child so that peak RSS (``VmHWM``) and peak virtual size
(``VmPeak``) from ``/proc/self/status`` belong to that build alone (master invariant 7, D0 F1).
Per build: loft (+ STEP round trip of the uncut loft for volume/bounds vs the law), then the
worker-equivalent path: pocket/bore cuts -> STEP export -> reimport (validity) -> STEP-derived
GLB via ``local.export_preview``. "build s" = loft + cuts + STEP export; "GLB s" = export_preview.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(REPO), str(REPO / 'backend')]

CHILD_TIMEOUT_S = 120
RULED = (False, True)
STEPS = (10.0, 7.5, 5.0)
CROWNS = (17, 33)

# name -> (style, changes from the preset, nudges of *other* fields with the reason)
CASES: dict[str, tuple[str, dict, dict]] = {
    'td-preset': ('teardrop', {}, {}),
    'td-n2.0': ('teardrop', {'crown_exponent': 2.0}, {}),
    'td-n4.0': ('teardrop', {'crown_exponent': 4.0}, {}),
    'td-side0.3': ('teardrop', {'side_height_frac': 0.3}, {
        'wheels.front_diameter_mm': (16.0, 'front bore must stay below the lower side height'),
        'wheels.rear_diameter_mm': (26.0, 'rear bore must stay below the lower side height')}),
    'td-side0.8': ('teardrop', {'side_height_frac': 0.8}, {}),
    'td-nose8-max64': ('teardrop', {'nose_width_mm': 8.0, 'max_width_mm': 64.0}, {}),
    'td-tail=max': ('teardrop', {'tail_width_mm': 56.0}, {}),
    'td-maxx0.25': ('teardrop', {'max_section_x_frac': 0.25}, {}),
    'td-maxx0.6': ('teardrop', {'max_section_x_frac': 0.6}, {
        'wheels.front_diameter_mm': (28.0, 'front bore must stay below the slower-rising side height')}),
    'nw-preset': ('narrow_waist', {}, {}),
    'nw-waist14-housing64': ('narrow_waist', {'waist_width_mm': 14.0, 'housing_width_mm': 64.0}, {}),
    'nw-housingh60-body18': ('narrow_waist', {'housing_height_mm': 60.0, 'body_height_mm': 18.0}, {
        'wheels.front_diameter_mm': (24.0, 'front bore must stay below the 18 mm body side height')}),
    'nw-housingl110': ('narrow_waist', {'housing_length_mm': 110.0}, {}),
    'nw-front0.30-waist0.35': ('narrow_waist', {'front_section_x_frac': 0.30, 'waist_x_frac': 0.35}, {}),
}


def _merge(data: dict, changes: dict) -> dict:
    out = json.loads(json.dumps(data))
    for key, value in changes.items():
        if key.startswith('wheels.'):
            out['wheels'][key.split('.', 1)[1]] = value
        else:
            out[key] = value
    return out


def case_spec(name: str):
    from backend.dragsters.spec_v2 import parse_v2
    from backend.dragsters.styles import REGISTRY
    style, changes, nudges = CASES[name]
    preset = REPO / 'backend/dragsters' / REGISTRY[style].preset_path
    data = _merge(json.loads(preset.read_text(encoding='utf-8')), changes)
    data = _merge(data, {k: v for k, (v, _why) in nudges.items()})
    return parse_v2(json.dumps(data))


def _status() -> dict:
    out = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in ('VmPeak', 'VmHWM', 'Threads'):
            out[key] = int(value.split()[0])
    return out


def law_bounds(stations, ground):
    from backend.dragsters.profiles import interpolate, sample_xs
    xs = sample_xs(0.0, stations[-1].x, 0.25) + [st.x for st in stations]
    secs = [interpolate(stations, x) for x in xs]
    a_max = max(a for a, _, _ in secs)
    top = max(s + c for _, s, c in secs)
    return (0.0, -a_max, ground, stations[-1].x, a_max, ground + top)


def measure_one(cfg: dict) -> dict:
    import cadquery as cq
    from backend.dragsters import kernel
    from backend.dragsters.local import export_preview
    from backend.dragsters.profiles import law_volume, stations_for

    out = dict(cfg)
    work = Path(tempfile.mkdtemp(prefix='d2spike-'))
    if cfg['case'] == 'wedge-v1':   # F1 yardstick: v1 builder through the same path
        from backend.dragsters.geometry import build_body
        from backend.dragsters.spec import DragsterSpec
        spec = DragsterSpec.model_validate_json(
            (REPO / 'backend/dragsters/examples/wedge.json').read_text(encoding='utf-8'))
        t0 = time.perf_counter()
        body = build_body(spec)
        step = work / 'body.step'
        cq.exporters.export(body, str(step), exportType='STEP')
        out['build_s'] = time.perf_counter() - t0
    else:
        spec = case_spec(cfg['case'])
        stations = stations_for(spec)
        n, g, L = spec.crown_exponent, spec.ground_clearance_mm, spec.length_mm
        t0 = time.perf_counter()
        loft = kernel.loft_body(stations, n, g, L, step_mm=cfg['step'], ruled=cfg['ruled'],
                                crown_points=cfg['crown'])
        t_loft = time.perf_counter() - t0
        # Uncut loft -> STEP -> reimport: what verification would measure against the law.
        raw = work / 'loft.step'
        cq.exporters.export(loft, str(raw), exportType='STEP')
        back = cq.importers.importStep(str(raw)).val()
        law = law_volume(stations, n)
        out['loft_rt_valid'] = len(back.Solids()) == 1 and back.isValid()
        out['vol_rel_err'] = (back.Volume() - law) / law
        bb = back.BoundingBox()
        want = law_bounds(stations, g)
        got = (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)
        out['bounds_err_mm'] = max(abs(a - b) for a, b in zip(got, want))
        out['sections'] = len(kernel.section_xs(stations, cfg['step']))
        # Worker-equivalent path.
        t1 = time.perf_counter()
        y_extent = max(st.a for st in stations)
        bores = [(spec.front_axle_x_mm, spec.wheels.front_diameter_mm / 2, spec.axle_bore_diameter_mm),
                 (spec.rear_axle_x_mm, spec.wheels.rear_diameter_mm / 2, spec.axle_bore_diameter_mm)]
        body = kernel.cut_pocket_and_bores(
            loft, length=L, pocket_diameter=spec.pocket_diameter_mm, pocket_depth=spec.pocket_depth_mm,
            pocket_center_z=spec.pocket_center_z_mm, bores=bores, y_extent=y_extent)
        step = work / 'body.step'
        cq.exporters.export(body, str(step), exportType='STEP')
        out['build_s'] = t_loft + time.perf_counter() - t1
    reimported = cq.importers.importStep(str(step)).val()
    out['valid'] = len(reimported.Solids()) == 1 and reimported.isValid()
    t2 = time.perf_counter()
    export_preview(step, work / 'preview.glb')
    out['glb_s'] = time.perf_counter() - t2
    out['total_s'] = out['build_s'] + out['glb_s']
    out['glb_kb'] = (work / 'preview.glb').stat().st_size / 1024
    status = _status()
    out['rss_mib'] = status['VmHWM'] / 1024
    out['vmpeak_mib'] = status['VmPeak'] / 1024
    out['threads'] = status.get('Threads')
    return out


def configs():
    yield {'case': 'wedge-v1', 'ruled': None, 'step': None, 'crown': None}
    for case in CASES:
        for ruled in RULED:
            for step in STEPS:
                for crown in CROWNS:
                    yield {'case': case, 'ruled': ruled, 'step': step, 'crown': crown}


def run_matrix(only: str | None) -> None:
    env = dict(os.environ, OMP_NUM_THREADS='1')   # worker env (service.py)
    for cfg in configs():
        if only and not cfg['case'].startswith(only):
            continue
        t0 = time.perf_counter()
        try:
            proc = subprocess.run([sys.executable, '-W', 'ignore', __file__, '--one', json.dumps(cfg)],
                                  capture_output=True, text=True, timeout=CHILD_TIMEOUT_S, env=env)
            lines = [ln for ln in proc.stdout.splitlines() if ln.startswith('{')]
            if proc.returncode == 0 and lines:
                result = json.loads(lines[-1])
            else:
                result = dict(cfg, error=f'rc={proc.returncode}: {proc.stderr.strip()[-300:]}')
        except subprocess.TimeoutExpired:
            result = dict(cfg, error=f'timeout {CHILD_TIMEOUT_S}s')
        result['wall_s'] = time.perf_counter() - t0
        print(json.dumps(result), flush=True)


def diag() -> None:
    """F1: peak virtual size vs the worker's 1536 MiB RLIMIT_AS, with/without an arena cap.

    Uses the kernel defaults from ``tolerances``. Limited runs go through the same trusted
    wrapper as ProcessRunner (``backend/_memory_entry.py``); MALLOC_ARENA_MAX is diagnostic only.
    """
    from backend.dragsters import tolerances as tol
    entry = REPO / 'backend' / '_memory_entry.py'
    cases = ('wedge-v1', 'td-preset', 'nw-preset')
    for limit_mib, repeats in ((None, 2), (1536, 3)):
        for arena in (None, '2'):
            for case in cases:
                for rep in range(repeats):
                    cfg = {'case': case, 'ruled': None if case == 'wedge-v1' else tol.LOFT_RULED,
                           'step': None if case == 'wedge-v1' else tol.STATION_STEP_MM,
                           'crown': None if case == 'wedge-v1' else tol.CROWN_POINTS}
                    env = dict(os.environ, OMP_NUM_THREADS='1')
                    if arena:
                        env['MALLOC_ARENA_MAX'] = arena
                    cmd = [sys.executable, '-W', 'ignore', __file__, '--one', json.dumps(cfg)]
                    if limit_mib:
                        cmd = [sys.executable, str(entry), str(limit_mib * 1024 * 1024)] + cmd
                    t0 = time.perf_counter()
                    out = {'case': case, 'limit_mib': limit_mib, 'arena_max': arena, 'rep': rep}
                    try:
                        proc = subprocess.run(cmd, capture_output=True, text=True,
                                              timeout=CHILD_TIMEOUT_S, env=env)
                        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith('{')]
                        out['rc'] = proc.returncode
                        if proc.returncode == 0 and lines:
                            r = json.loads(lines[-1])
                            out.update({k: r[k] for k in ('total_s', 'rss_mib', 'vmpeak_mib', 'threads')})
                        else:
                            out['stderr'] = proc.stderr.strip()[-200:]
                    except subprocess.TimeoutExpired:
                        out['rc'] = 'timeout'
                    out['wall_s'] = time.perf_counter() - t0
                    print(json.dumps(out), flush=True)


def check() -> int:
    bad = 0
    for name in CASES:
        try:
            case_spec(name)
            print(f'OK   {name}')
        except Exception as exc:  # report every D1 rule failure
            bad += 1
            print(f'FAIL {name}: {exc}')
    return bad


def _fmt_cfg(r):
    return f"{'ruled' if r['ruled'] else 'smooth'} / {r['step']:g} / {r['crown']}"


def table(path: Path) -> None:
    rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.startswith('{')]
    print('| case | ruled/smooth, step mm, crown pts | sections | valid (loft RT, body RT) | vol err % | bounds err mm | build s | GLB s | total s | RSS MiB | VmPeak MiB |')
    print('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for r in rows:
        if 'error' in r:
            cfg = 'v1 builder' if r['ruled'] is None else _fmt_cfg(r)
            print(f"| {r['case']} | {cfg} | | **ERROR** {r['error'][:80]} | | | | | | | |")
            continue
        if r['ruled'] is None:
            print(f"| {r['case']} | v1 builder | | {r['valid']} | | | {r['build_s']:.2f} | {r['glb_s']:.2f} | "
                  f"{r['total_s']:.2f} | {r['rss_mib']:.0f} | {r['vmpeak_mib']:.0f} |")
            continue
        print(f"| {r['case']} | {_fmt_cfg(r)} | {r['sections']} | {r['loft_rt_valid']}, {r['valid']} | "
              f"{100 * r['vol_rel_err']:+.3f} | {r['bounds_err_mm']:.4f} | {r['build_s']:.2f} | "
              f"{r['glb_s']:.2f} | {r['total_s']:.2f} | {r['rss_mib']:.0f} | {r['vmpeak_mib']:.0f} |")
    print()
    print('Per configuration over all lofted cases:')
    print()
    print('| ruled/smooth, step, crown | all valid | worst |vol err| % | worst bounds mm | median total s | max total s | max RSS MiB | max VmPeak MiB |')
    print('| --- | --- | --- | --- | --- | --- | --- | --- |')
    groups: dict[tuple, list] = {}
    for r in rows:
        if r['ruled'] is not None:
            groups.setdefault((r['ruled'], r['step'], r['crown']), []).append(r)
    for key, rs in groups.items():
        ok = [r for r in rs if 'error' not in r]
        valid = len(ok) == len(rs) and all(r['valid'] and r['loft_rt_valid'] for r in ok)
        if not ok:
            print(f'| {_fmt_cfg(rs[0])} | False | | | | | | |')
            continue
        tot = [r['total_s'] for r in ok]
        print(f"| {_fmt_cfg(rs[0])} | {valid} ({len(ok)}/{len(rs)}) | "
              f"{100 * max(abs(r['vol_rel_err']) for r in ok):.3f} | {max(r['bounds_err_mm'] for r in ok):.4f} | "
              f"{statistics.median(tot):.2f} | {max(tot):.2f} | {max(r['rss_mib'] for r in ok):.0f} | "
              f"{max(r['vmpeak_mib'] for r in ok):.0f} |")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--diag', action='store_true')
    parser.add_argument('--one')
    parser.add_argument('--only')
    parser.add_argument('--table', type=Path)
    args = parser.parse_args()
    if args.check:
        return 1 if check() else 0
    if args.diag:
        diag()
        return 0
    if args.table:
        table(args.table)
        return 0
    if args.one:
        print(json.dumps(measure_one(json.loads(args.one))), flush=True)
        return 0
    run_matrix(args.only)
    return 0


if __name__ == '__main__':
    sys.exit(main())
