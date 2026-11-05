"""Declare fresh deflected-foil midpoint grids and native rounding checks.

Run sequentially, with no competing native jobs. Failed audits never supply a
bank. This driver does not grant aircraft qualification or production admission.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from cem.core import canonical_json, digest
from cem.glider.families.installed import freeze, validate
from cem.glider.families.coordinate_polars import CoordinateBank, query, validate as replay_query
from cem.glider.families.coordinate_audit import audit, validate as replay_audit
from cem.aero.polars import PolarCoverageError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    receipt = json.loads((ROOT / 'docs/testing/cem-coordinate-window-results.json').read_text())
    states = receipt['installed_states'][:2]
    installed = [freeze(s['archive'], s['controls']) for s in states]
    foils = [{digest(s.airfoil.coordinates.tolist()): s.airfoil.coordinates.tolist()
              for wing in validate(state).wings for s in wing.xsecs} for state in installed]
    changed = set(foils[1]) - set(foils[0])
    if len(changed) != 1:
        raise ValueError('Expected exactly one deflected swept foil')
    foil = changed.pop()
    points = foils[1][foil]
    # Declare all grids before starting native work; refinement is a new audit,
    # not a splice, extension or promotion of a failed coarse grid.
    declarations = [
        dict(name='coarse', reynolds=[49000., 64000.], angles=[2.5, 4., 5.5]),
        dict(name='refined', reynolds=[49000., 56000., 64000.],
             angles=[2.5, 3.25, 4., 4.75, 5.5]),
    ]
    rounding = dict(reynolds=[49000., 64000.], angles=[3.02376103536733, 4., 5.5])
    declaration = dict(installed=installed, foil_sha256=foil, grids=declarations,
                       rounding=rounding, vaccel=None, transition={},
                       production_admission=False)
    (root / 'declaration.json').write_text(canonical_json(declaration))
    results = []
    for grid in declarations:
        folder = root / grid['name']
        report = audit(points, grid['reynolds'], grid['angles'], folder)
        assert replay_audit(report, folder) is False
        results.append(dict(name=grid['name'], state=report['state'], replay='matched',
                            failure=report.get('failure'),
                            interpolation_worst=report.get('interpolation_worst'),
                            evidence_sha256=report['evidence_sha256']))
        (root / 'audits.json').write_text(canonical_json(results))
        print(grid['name'], report['state'], 'replay matched', flush=True)
    reports = []
    for i, re in enumerate(rounding['reynolds']):
        folder = root / f'rounding_{i}'
        report = query(points, re, rounding['angles'], folder)
        (folder / 'query.json').write_text(canonical_json(report))
        assert replay_query(report, folder) is False
        reports.append(report)
    bank = CoordinateBank(reports)
    try:
        bank.coefficients(foil, 56000., rounding['angles'][0])
    except PolarCoverageError as exc:
        outcome = dict(state='rejected', reason=str(exc))
    else:
        raise AssertionError('Unrounded lower endpoint was admitted beyond native samples')
    (root / 'rounding.json').write_text(canonical_json(dict(
        requested_angles=rounding['angles'], native_angles=[r['alpha_deg'] for r in reports[0]['rows']],
        requested_endpoint=outcome, replay='matched', production_admission=False)))
    print('Unrounded native endpoint rejected; no coverage expansion', flush=True)


if __name__ == '__main__':
    main()
