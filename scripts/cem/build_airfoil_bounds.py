"""Rebuild the screening envelope offline. No provider calls, no fabricated XFOIL evidence.

Run with the pinned glider environment. XFOIL cross-validation is an explicit
remaining qualification step; the resulting table cannot prove global limits.
"""
import json
from pathlib import Path
import aerosandbox as asb
import neuralfoil
import numpy as np

AIRFOILS = ['sd7037', 'ag24', 'ag25', 's1223', 'naca2408', 'naca2412']


def build():
    rows = []
    for reynolds in [30000, 50000, 100000, 200000, 500000]:
        ld, sink_metric, evidence = 0., 0., []
        for name in AIRFOILS:
            af = asb.Airfoil(name).to_kulfan_airfoil()
            polar = af.get_aero_from_neuralfoil(alpha=np.linspace(-2, 14, 65), Re=reynolds, model_size='large')
            cl, cd = np.asarray(polar['CL']), np.asarray(polar['CD'])
            confidence = np.asarray(polar['analysis_confidence'])
            ok = (cl > .1) & (cd > 0) & (confidence >= .8) & np.isfinite(cl) & np.isfinite(cd)
            if not ok.any():
                continue
            # Optimistic e=1 and largest supported AR=14, no fuselage/tail drag.
            total_cd = cd / 1.25 + cl**2 / (np.pi * 14.)
            l, s = float(np.max(cl[ok] / total_cd[ok])), float(np.max(cl[ok] ** 1.5 / total_cd[ok]))
            ld, sink_metric = max(ld, l), max(sink_metric, s)
            evidence.append(dict(airfoil=name, ld=l, cl_1_5_over_cd=s))
        if not evidence:
            raise RuntimeError('No supported polar samples')
        rows.append(dict(reynolds=reynolds, ld_upper=ld,
                         cl_1_5_over_cd_upper=sink_metric, samples=evidence))
    return dict(version='neuralfoil-envelope-1', source=dict(aerosandbox=asb.__version__,
                neuralfoil=neuralfoil.__version__, model_size='large', alpha_deg=[-2, 14, 65],
                confidence_floor=.8, airfoils=AIRFOILS, xfoil='not_cross_validated',
                max_aspect_ratio=14., oswald_upper=1., profile_drag_divisor=1.25),
                scope='Optimistic sampled-model envelope, not a universal physical impossibility proof', rows=rows)


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[2] / 'backend/cem/data/airfoil_bounds.json'
    path.write_text(json.dumps(build(), sort_keys=True, indent=2, allow_nan=False) + '\n')
