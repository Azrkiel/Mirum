"""Fresh exact-foil trial windows and covered solver traces; no admission.

Uses the fixed installed archives in the historical window receipt, never its
polar rows. Run sequentially with no other native workload. Output must be new.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from cem.core import canonical_json, digest
from cem.glider.families.installed import freeze, validate
from cem.glider.families.coordinate_polars import coordinates_payload, CoordinateBank
from cem.glider.families.coordinate_audit import audit, validate as replay
from cem.glider.families.coupled_aero import solve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    root = args.directory
    root.mkdir(parents=True, exist_ok=False)
    receipt = json.loads((ROOT/'docs/testing/cem-coordinate-window-results.json').read_text())
    foils = {}
    historical = {}
    states = []
    for state in receipt['installed_states']:
        installed = freeze(state['archive'], state['controls'])
        states.append(dict(**state, current_installed=installed))
        current_foils = set()
        for wing in validate(installed).wings:
            for section in wing.xsecs:
                points = coordinates_payload(section.airfoil.coordinates)
                key = digest(points)
                foils[key] = points
                current_foils.add(key)
        if len(current_foils) == 1:
            historical[next(iter(current_foils))] = state['foils'][0]
        else:
            old_remaining = set(state['foils']) - set(historical.values())
            new_remaining = current_foils - set(historical)
            if not old_remaining and not new_remaining:
                continue
            if len(old_remaining) != 1 or len(new_remaining) != 1:
                raise ValueError('Ambiguous installed foil correspondence')
            historical[new_remaining.pop()] = old_remaining.pop()
    (root/'installed-states.json').write_text(canonical_json(states))
    # Declared fresh cells include the two first rejected neutral-foil requests.
    # Other foil windows reproduce the previous initialization experiment.
    bounds = {
        'e974ec3cea': ([49000., 74000.], [3., 4.5, 6.]),
        'd8e3b2bda8': ([49000., 64000.], [4., 4.75, 5.5]),
        'e9426ae7ef': ([33000., 73000.], [4., 6., 8.]),
        'f925aa8eb7': ([33000., 61000.], [5., 6.5, 8.]),
    }
    results = []
    passed = []
    for foil, points in sorted(foils.items()):
        previous_foil = historical[foil]
        re, angles = bounds[previous_foil[:10]]
        for vaccel in ([None] if previous_foil[:10] in ('e974ec3cea', 'd8e3b2bda8') else [None, 0.]):
            directory = root/(foil+('-default' if vaccel is None else '-zero'))
            report = audit(points, re, angles, directory, vaccel=vaccel)
            result = dict(foil=foil, historical_foil=previous_foil, directory=str(directory), reynolds=re, angles=angles,
                          vaccel=vaccel, state=report['state'], failure=report.get('failure'),
                          evidence_sha256=report['evidence_sha256'])
            try:
                assert replay(report, directory) is False
                result['replay'] = 'matched'
            except (ValueError, OSError) as exc:
                result['replay'] = dict(state='unmatched', reason=str(exc))
            results.append(result)
            if report['state'] == 'passed' and result['replay'] == 'matched':
                passed.extend(q['report'] for q in report['queries']
                              if q['directory'].endswith('/ascending'))
            (root/'audits.json').write_text(canonical_json(results))
            print(foil[:10], vaccel, result['state'], result['replay'], flush=True)
    traces = []
    for state in states[:2]:
        class TracedBank(CoordinateBank):
            def coefficients(self, foil, re, alpha):
                call = dict(foil=foil, reynolds=re, alpha_deg=alpha)
                trace['requests'].append(call)
                try:
                    value = super().coefficients(foil, re, alpha)
                except ValueError as exc:
                    call['rejected'] = str(exc)
                    raise
                call['coefficients'] = value
                return value
        trace = dict(installed_sha256=state['current_installed']['installed_sha256'], controls=state['controls'],
                     requests=[], production_admission=False)
        try:
            trace['result'] = solve(state['archive'], state['controls'], TracedBank(passed))
            trace['state'] = 'circulation_solution_only'
        except ValueError as exc:
            trace.update(state='failed', reason=str(exc))
        traces.append(trace)
    (root/'solver-trials.json').write_text(canonical_json(traces))


if __name__ == '__main__':
    main()
