"""D3 range audit: rejection-sample valid TeardropV2 specs (seed 20261109), build each, 20 s cap."""
import json, math, random, signal, sys, time, tempfile
from pathlib import Path
sys.path[:0] = ['/workspace']
from backend.dragsters.spec_v2 import TeardropV2, parse_v2
from backend.dragsters.styles import load_preset

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
MODE = sys.argv[2] if len(sys.argv) > 2 else 'both'   # sample | both
base = json.loads(load_preset('teardrop').model_dump_json())

def bounds(model, name):
    f = model.model_fields[name]
    lo = hi = None
    for m in f.metadata:
        lo = getattr(m, 'ge', lo)
        hi = getattr(m, 'le', hi)
    return lo, hi

COMMON = {  # D1 table (common block)
    'length_mm': (160, 300), 'ground_clearance_mm': (1, 10), 'front_axle_x_mm': (10, 290),
    'rear_axle_x_mm': (10, 290), 'axle_bore_diameter_mm': (2, 5), 'pocket_diameter_mm': (12, 24),
    'pocket_depth_mm': (30, 80), 'pocket_center_z_mm': (10, 60), 'min_wall_mm': (2, 5)}
WHEELS = {'front_diameter_mm': (16, 45), 'rear_diameter_mm': (16, 45), 'width_mm': (2, 12), 'side_clearance_mm': (0.5, 5)}
STYLE = {n: bounds(TeardropV2, n) for n in (
    'nose_width_mm', 'nose_height_mm', 'max_width_mm', 'max_height_mm', 'max_section_x_frac',
    'tail_width_mm', 'tail_height_mm', 'side_height_frac', 'crown_exponent')}

def sample(rng):
    d = json.loads(json.dumps(base))
    for k, (lo, hi) in COMMON.items():
        d[k] = round(rng.uniform(lo, hi), 1)
    for k, (lo, hi) in WHEELS.items():
        d['wheels'][k] = round(rng.uniform(lo, hi), 1)
    for k, (lo, hi) in STYLE.items():
        d[k] = round(rng.uniform(lo, hi), 2 if 'frac' in k or 'exp' in k else 1)
    return d

rng = random.Random(20261109)
specs, tries = [], 0
t0 = time.time()
while len(specs) < N:
    tries += 1
    d = sample(rng)
    try:
        specs.append(parse_v2(json.dumps(d)))
    except Exception:
        pass
print(json.dumps({'sampled': len(specs), 'tries': tries, 'accept_rate': len(specs) / tries,
                  'sample_s': time.time() - t0}), flush=True)
Path('/tmp/audit_specs.json').write_text(json.dumps([json.loads(s.model_dump_json()) for s in specs]))
if MODE == 'sample':
    sys.exit(0)

import cadquery as cq
from backend.dragsters import profiles
from backend.dragsters.styles.teardrop import build
from backend.dragsters.tolerances import LOFT_BOUNDS_TOL_MM, LOFT_VOLUME_REL_TOL, VOID_TOLERANCE_MM3
sys.path.insert(0, '/workspace')
from backend.tests.test_dragster_style_teardrop import _law_bounds, _expected_volume, _void_residuals

class Cap(Exception):
    pass
def on_alarm(*a):
    raise Cap()
signal.signal(signal.SIGALRM, on_alarm)

fails, times = [], []
work = Path(tempfile.mkdtemp())
for i, spec in enumerate(specs):
    t = time.time()
    err = None
    try:
        signal.alarm(20)
        body = build(spec)
        p = work / 'a.step'
        cq.exporters.export(body, str(p), exportType='STEP')
        back = cq.importers.importStep(str(p)).val()
        assert len(back.Solids()) == 1 and back.isValid(), 'step round trip invalid'
        bb = back.BoundingBox()
        got = (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)
        be = max(abs(a - b) for a, b in zip(got, _law_bounds(spec)))
        assert be <= LOFT_BOUNDS_TOL_MM, f'bounds err {be}'
        exp = _expected_volume(spec)
        ve = abs(back.Volume() - exp) / exp
        assert ve <= LOFT_VOLUME_REL_TOL, f'volume err {ve}'
        res = _void_residuals(back, spec)
        assert all(v <= VOID_TOLERANCE_MM3 for v in res.values()), f'void {res}'
    except Cap:
        err = 'TIMEOUT 20s'
    except BaseException as e:
        err = f'{type(e).__name__}: {e}'
    finally:
        signal.alarm(0)
    dt = time.time() - t
    times.append(dt)
    print(json.dumps({'i': i, 'ok': err is None, 'err': err, 's': round(dt, 2), 'vol_err': locals().get('ve'), 'bounds_err': locals().get('be')}), flush=True)
    if err:
        fails.append({'i': i, 'err': err, 'spec': json.loads(spec.model_dump_json())})
print(json.dumps({'done': True, 'n': len(specs), 'failures': len(fails), 'max_s': max(times)}))
Path('/scratch/../tmp/audit_fails.json').write_text(json.dumps(fails)) if False else None
print('FAILS', json.dumps(fails))
