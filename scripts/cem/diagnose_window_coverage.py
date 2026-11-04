"""Compare historical sampled windows with zero-circulation solver inputs.

This is a necessary initialization-domain check, not a polar replay or a flight
solution. Induced flow, Newton trial states and converged states require their
own coverage. No coefficients are synthesized and no polar rows are combined.
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

import aerosandbox as asb
import numpy as np

from cem.glider.families.coupled_aero import panels
from cem.glider.families.installed import freeze


def diagnose(state, windows, *, speed_m_s=6., alpha_deg=4., altitude_m=0., spanwise=4):
    if not (1 <= speed_m_s <= 15 and -10 <= alpha_deg <= 12 and 0 <= altitude_m <= 5000):
        raise ValueError('Unsupported coupled operating point')
    installed = freeze(state['archive'], state['controls'])
    if installed['installed_sha256'] != state['installed_sha256']:
        raise ValueError('Historical installed-state identity changed')
    air = asb.Atmosphere(altitude=altitude_m)
    rho_over_mu = float(air.density() / air.dynamic_viscosity())
    alpha = math.radians(alpha_deg)
    wind = np.array([math.cos(alpha), 0., math.sin(alpha)])
    strips = []
    for panel in panels(installed, spanwise):
        perpendicular = wind - np.dot(wind, panel['span']) * panel['span']
        re_per_speed = float(rho_over_mu * np.linalg.norm(perpendicular) * panel['chord'])
        angle = math.degrees(math.atan2(float(perpendicular @ panel['normal']),
                                       float(perpendicular @ panel['chord_direction'])))
        candidates = [w for w in windows if w['state'] == 'passed' and
                      w['foil_sha256'] == panel['foil']]
        checks = [dict(reynolds_grid=w['reynolds'], angles_deg=w['angles_deg'],
                       reynolds_in_bounds=w['reynolds'][0] <= re_per_speed * speed_m_s <= w['reynolds'][-1],
                       alpha_in_bounds=w['angles_deg'][0] <= angle <= w['angles_deg'][-1],
                       speed_interval_m_s=[w['reynolds'][0] / re_per_speed,
                                           w['reynolds'][-1] / re_per_speed]) for w in candidates]
        strips.append(dict(surface=panel['surface'], center_m=panel['center'].tolist(),
                           foil_sha256=panel['foil'], chord_m=panel['chord'],
                           reynolds=float(re_per_speed * speed_m_s), alpha_deg=angle,
                           historical_window_checks=checks,
                           inside_historical_bounds=any(c['reynolds_in_bounds'] and
                                                        c['alpha_in_bounds'] for c in checks)))
    # A single rectangular historical window per strip permits an exact
    # intersection. Multiple disjoint windows need a union-of-intervals check;
    # do not bridge them by taking their outer bounds.
    common = None
    if all(len(s['historical_window_checks']) == 1 for s in strips):
        intervals = [s['historical_window_checks'][0]['speed_interval_m_s'] for s in strips]
        lower = max(i[0] for i in intervals)
        upper = min(i[1] for i in intervals)
        common = dict(lower_m_s=lower, upper_m_s=upper, empty=lower > upper,
                      scope='Reynolds_only_at_declared_alpha_zero_circulation')
    return dict(scope='historical_window_initialization_domain_diagnostic',
                family=state['family'], controls=state['controls'],
                installed_sha256=installed['installed_sha256'],
                conditions=dict(speed_m_s=speed_m_s, alpha_deg=alpha_deg,
                                altitude_m=altitude_m, spanwise=spanwise,
                                circulation='zero_initialization_only'),
                strips=strips, outside_count=sum(not s['inside_historical_bounds'] for s in strips),
                common_reynolds_speed_interval=common,
                historical_evidence_replayed=False, converged_flight_coverage='unestablished',
                production_admission=False, verified_gain=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--speed', type=float, default=6.)
    parser.add_argument('--alpha', type=float, default=4.)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    results = [diagnose(s, receipt['windows'], speed_m_s=args.speed, alpha_deg=args.alpha)
               for s in receipt['installed_states']]
    serialized = json.dumps(results, indent=2, allow_nan=False) + '\n'
    with args.output.open('x') as stream:
        stream.write(serialized)
    for result in results:
        print(result['family'], result['controls'],
              f"{result['outside_count']}/{len(result['strips'])} initial strips outside historical bounds")


if __name__ == '__main__':
    main()
