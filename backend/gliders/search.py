"""Seeded exploration followed by bounded refinement of the leading candidates."""
import copy
import math
import random
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import json
import zipfile

from .spec import GliderRequest
from .model.components import WingParams, FuselageParams, TailParams
from .model.assembly import Glider
from .model.analysis import evaluate
from .model import export
from .model.config import CONFIG

ASSUMPTIONS = ('Small passive fixed-surface glider; still air; sea-level standard atmosphere; '
               'steady trimmed glide from the stated launch height. Distance = height × L/D. '
               'AeroSandbox/NeuralFoil screening predictions, not flight validation. '
               'Mass uses supplied areal densities. Structural strength, launch loads, gusts, '
               'wing flex and landing transients are not evaluated. STL meshes are not STEP solids.')


def finite_json(value):
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items() if k != 'traceback'}
    if isinstance(value, (list, tuple)):
        return [finite_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def sample(rng, cfg, index, parent=None, radius=0.2):
    space = copy.deepcopy(cfg['ranges'])
    if parent:
        for family, part in zip(('wing', 'fuselage', 'tail'), (parent.wing, parent.fuse, parent.tail)):
            for key, interval in space[family].items():
                attr = key.removesuffix('_m')
                if family == 'fuselage' and key == 'max_diameter_m':
                    attr = 'max_diameter'
                if isinstance(interval, (list, tuple)):
                    # Numeric intervals are identified from the original model, not choices.
                    if isinstance(CONFIG['ranges'][family][key], tuple) and hasattr(part, attr):
                        center = getattr(part, attr)
                        lo, hi = interval
                        if lo <= center <= hi:
                            width = (hi - lo) * radius
                            space[family][key] = (max(lo, center - width), min(hi, center + width))
                    elif hasattr(part, attr) and getattr(part, attr) in interval and rng.random() < 0.8:
                        space[family][key] = [getattr(part, attr)]
    return Glider(WingParams.random(rng, space['wing'], index),
                  FuselageParams.random(rng, space['fuselage'], index),
                  TailParams.random(rng, space['tail'], index), cfg)


def admissible(glider, spec, result):
    """Enforce user caps and fail closed on nonfinite objective/constraint evidence."""
    reasons = [s for s in result.get('fail_reasons', '').split('; ') if s]
    if result.get('valid'):
        required = ('L_over_D', 'mass_kg', 'glide_speed_m_s', 'static_margin',
                    'CL_trim', 'CLmax_est', 'Cm_trim', 'Cma', 'Clb', 'Cnb',
                    'dutch_roll_zeta', 'dutch_roll_real', 'roll_real', 'spiral_real')
        if any(not isinstance(result.get(k), (int, float)) or not math.isfinite(result[k]) for k in required):
            reasons.append('nonfinite or missing analysis evidence')
        elif (result['L_over_D'] <= 0 or result['mass_kg'] <= 0
              or result['mass_kg'] > spec.max_total_mass_kg
              or not 0 < result['glide_speed_m_s'] <= spec.max_flight_speed_m_s
              or result['static_margin'] < spec.static_margin_min
              or result['CL_trim'] > spec.stall_margin * result['CLmax_est']):
            reasons.append('designer constraint violated')
        # Measure the full export envelope, including tail and swept/twisted surfaces.
        import numpy as np
        points, _ = glider.airplane().mesh_body(method='tri')
        bounds = np.ptp(points, axis=0)
        if not np.isfinite(bounds).all():
            reasons.append('nonfinite geometry envelope')
        elif bounds[0] > spec.max_length_m + 1e-9 or bounds[1] > spec.max_wingspan_m + 1e-9:
            reasons.append('assembled geometry exceeds maximum dimensions')
        result['overall_length_m'], result['overall_span_m'] = float(bounds[0]), float(bounds[1])
    result.update(valid=bool(result.get('valid')) and not reasons, fail_reasons='; '.join(reasons))
    return result


def search(spec: GliderRequest, output: Path, progress=lambda value: None, evaluator=evaluate):
    cfg, rng = spec.configuration(), random.Random(spec.seed)
    archive, feasible, records = [], [], []
    explore = spec.evaluations // 2
    for i in range(spec.evaluations):
        # Keep exploration in the second half to avoid collapsing onto one family.
        parent = None
        if i >= explore and archive and (i - explore) % 4 != 0:
            pool = feasible[:5] or sorted(archive, key=lambda x: len(x[0].get('fail_reasons', '').split(';')))[:5]
            parent = rng.choice(pool)[1]
        gl = sample(rng, cfg, i + 1, parent, radius=max(0.05, 0.25 * (1 - i / spec.evaluations)))
        result = admissible(gl, spec, evaluator(gl))
        result['phase'] = 'refinement' if parent else 'exploration'
        archive.append((result, gl))
        records.append(finite_json(result))
        if result['valid']:
            feasible.append((result, gl))
            feasible.sort(key=lambda pair: (-pair[0]['L_over_D'], pair[0]['id']))
        progress({'evaluated': i + 1, 'budget': spec.evaluations, 'feasible': len(feasible)})
    candidates = []
    output.mkdir(parents=True, exist_ok=True)
    for rank, (result, gl) in enumerate(feasible[:spec.top_k], 1):
        directory = output / result['id']
        directory.mkdir()
        # The evaluator mutated this exact instance's balance and trim. Never rebuild defaults.
        export.export_stls(gl, str(directory))
        export.export_templates(gl, str(directory))
        export.three_view(gl, result, str(directory / 'three_view.png'))
        candidate = finite_json(dict(result, rank=rank,
                                    parameters={'wing': asdict(gl.wing), 'fuselage': asdict(gl.fuse),
                                                'tail': asdict(gl.tail)},
                                    request=spec.model_dump(), assumptions=ASSUMPTIONS))
        (directory / 'design.json').write_text(json.dumps(candidate, allow_nan=False))
        (directory / 'assumptions.txt').write_text(ASSUMPTIONS)
        with zipfile.ZipFile(output / f'{gl.id}.zip', 'w', zipfile.ZIP_DEFLATED) as z:
            for path in sorted(directory.iterdir()):
                z.write(path, path.name)
        candidates.append(candidate)
    report = {'status': 'completed', 'objective': 'maximum_trimmed_lift_to_drag',
              'request': spec.model_dump(), 'assumptions': ASSUMPTIONS,
              'solver': {'aerosandbox': __import__('aerosandbox').__version__,
                         'search': 'seeded exploration + elite local resampling'},
              'evaluated': len(records), 'feasible': len(feasible), 'candidates': candidates,
              'rejections': dict(Counter(reason for r in records if not r['valid']
                                        for reason in r['fail_reasons'].split('; ') if reason)),
              'evaluations': records}
    (output / 'result.json').write_text(json.dumps(report, allow_nan=False))
    return report
