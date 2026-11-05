"""Audit each exact foil in an installed family archive/control state."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'backend'))
from cem.core import canonical_json, digest
from cem.glider.families.installed import freeze, validate
from cem.glider.families.coordinate_polars import coordinates_payload
from cem.glider.families.coordinate_audit import audit, validate as replay_audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--controls', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--reynolds', type=float, nargs='+', default=[30000., 300000.])
    parser.add_argument('--angles', type=float, nargs='+', default=[-4., -2., 0., 2., 4., 6., 8.])
    parser.add_argument('--replay', action='store_true')
    parser.add_argument('--vaccel', type=float, default=None, help='Explicit XFOIL acceleration, 0 through .01; omitted retains native default')
    parser.add_argument('--panel-distribution', type=Path, help='JSON object of explicit PPAR settings')
    args = parser.parse_args()
    settings = json.loads(args.panel_distribution.read_text()) if args.panel_distribution else None
    installed = freeze(json.loads(args.archive.read_text()), json.loads(args.controls.read_text()))
    plane = validate(installed)
    foils = {digest(coordinates_payload(s.airfoil.coordinates)): coordinates_payload(s.airfoil.coordinates)
             for wing in plane.wings for s in wing.xsecs}
    root = args.directory
    if not args.replay and root.exists() and any(root.iterdir()):
        parser.error('Use an empty directory to preserve audit evidence')
    root.mkdir(parents=True, exist_ok=True)
    results = {}
    for key, points in sorted(foils.items()):
        if args.replay:
            result = json.loads((root/key/'coordinate-audit.json').read_text())
            if result['inputs'] != dict(coordinates=points, reynolds=args.reynolds,
                                       angles=args.angles, transition=None, vaccel=args.vaccel, panel_distribution=settings):
                raise ValueError('Installed coordinate audit inputs changed')
            replay_audit(result, root/key)
        else:
            result = audit(points, args.reynolds, args.angles, root/key, vaccel=args.vaccel, panel_distribution=settings)
        results[key] = result
    receipt = dict(installed_sha256=installed['installed_sha256'], foils=results,
                   production_admission=False, verified_gain=False)
    target = root/'installed-audit.json'
    if args.replay:
        if target.read_text() != canonical_json(receipt):
            raise ValueError('Installed coordinate audit differs from reconstruction')
    else:
        target.write_text(canonical_json(receipt))
    print(canonical_json(receipt))
    return 0 if all(r['state'] == 'passed' for r in results.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
