"""D4 timing: fresh interpreter per build (OMP_NUM_THREADS=1): build -> STEP -> reimport -> GLB; VmHWM/VmPeak.
usage: timing.py [reps_preset] ; env MALLOC_ARENA_MAX respected."""
import json, os, statistics, subprocess, sys, tempfile, time
from pathlib import Path

ME = __file__

def status():
    out = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        k, _, v = line.partition(':')
        if k in ('VmPeak', 'VmHWM'):
            out[k] = int(v.split()[0]) / 1024
    return out

def one(case):
    sys.path[:0] = ['/workspace']
    import cadquery as cq
    from backend.dragsters.local import export_preview
    from backend.dragsters.styles import load_preset
    from backend.dragsters.styles.narrow_waist import build
    from backend.tests.test_dragster_style_narrow_waist import BOUNDARY, _variant, _void_residuals, _law_bounds
    spec = load_preset('narrow_waist') if case == 'preset' else _variant(**BOUNDARY[case])
    work = Path(tempfile.mkdtemp())
    t0 = time.perf_counter()
    body = build(spec)
    step = work / 'b.step'
    cq.exporters.export(body, str(step), exportType='STEP')
    t_build = time.perf_counter() - t0
    t1 = time.perf_counter()
    back = cq.importers.importStep(str(step)).val()
    ok = len(back.Solids()) == 1 and back.isValid()
    res = _void_residuals(back, spec)
    ok = ok and all(v <= 0.001 for v in res.values())
    t_verify = time.perf_counter() - t1
    t2 = time.perf_counter()
    export_preview(step, work / 'p.glb')
    t_glb = time.perf_counter() - t2
    st = status()
    print(json.dumps({'case': case, 'ok': ok, 'build_s': t_build, 'verify_s': t_verify, 'glb_s': t_glb,
                      'total_s': t_build + t_verify + t_glb, 'rss_mib': st['VmHWM'], 'vmpeak_mib': st['VmPeak']}))

if len(sys.argv) > 2 and sys.argv[1] == '--one':
    one(sys.argv[2]); sys.exit(0)

sys.path[:0] = ['/workspace']
from backend.tests.test_dragster_style_narrow_waist import BOUNDARY
reps = int(sys.argv[1]) if len(sys.argv) > 1 else 10
env = dict(os.environ, OMP_NUM_THREADS='1')
rows = []
for case in ['preset'] * reps + list(BOUNDARY):
    p = subprocess.run([sys.executable, '-W', 'ignore', ME, '--one', case], capture_output=True, text=True, env=env, timeout=120)
    lines = [l for l in p.stdout.splitlines() if l.startswith('{')]
    r = json.loads(lines[-1]) if lines else {'case': case, 'error': p.stderr[-200:]}
    rows.append(r); print(json.dumps(r), flush=True)
pre = [r for r in rows if r['case'] == 'preset' and 'error' not in r]
for key in ('build_s', 'verify_s', 'glb_s', 'total_s', 'rss_mib', 'vmpeak_mib'):
    v = [r[key] for r in pre]
    print(f'preset {key}: median {statistics.median(v):.2f} max {max(v):.2f} min {min(v):.2f}')
allr = [r for r in rows if 'error' not in r]
print('ALL max total_s', max(r['total_s'] for r in allr), 'max rss', max(r['rss_mib'] for r in allr), 'max vmpeak', max(r['vmpeak_mib'] for r in allr), 'all ok', all(r['ok'] for r in allr), 'errors', len(rows) - len(allr))
