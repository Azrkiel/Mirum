"""Native audits of one exact installed foil/control state, never family admission."""
import inspect
import json
import math
from importlib.metadata import version
from pathlib import Path

from cem.aero.native import NativeFailure
from cem.aero.polar_audit import SEQUENCES
from cem.aero.qualification import _inventory
from cem.core import canonical_json, digest
from . import coordinate_polars as polars

TOLERANCES = dict(cl=.025, cd=.001, cm=.005)


def audit(coordinates, reynolds, angles, directory, *, transition=None, replay=False):
    """Audit all initializations, two meshes and alpha/log-Re cell midpoints.

    Missing or sensitive rows remain failures. Every query has its own retained
    artifacts; replay repeats native queries and compares scientific payloads.
    """
    root = Path(directory)
    if root.exists() and any(root.iterdir()) and not replay:
        raise ValueError('Coordinate audit requires an empty directory')
    points = polars.coordinates_payload(coordinates)
    if (not 2 <= len(reynolds) <= 12 or any(type(r) not in (int, float) or
            not math.isfinite(r) or not 30000 <= r <= 1000000 for r in reynolds)
            or any(b <= a for a, b in zip(reynolds, reynolds[1:]))):
        raise ValueError('Invalid coordinate audit Reynolds grid')
    root.mkdir(parents=True, exist_ok=True)
    report = dict(scope='exact_coordinate_section_audit_development', state='failed',
        coordinates_sha256=digest(points), inputs=dict(coordinates=points,
        reynolds=list(reynolds), angles=list(angles), transition=transition),
        implementation_sha256=digest({name: Path(inspect.getfile(module)).read_text()
            for name, module in [('audit', inspect.getmodule(audit)), ('polars', polars),
                ('metadata', inspect.getmodule(polars.check_polar_controls))]}),
        runtime={name: version(name) for name in ('numpy', 'scipy', 'aerosandbox')},
        tolerances=TOLERANCES.copy(), queries=[], comparisons=[], production_admission=False,
        qualified_aircraft_stall=False, verified_gain=False)

    failures = []

    def reject(reason):
        failures.append(dict(stage=report['stage'], reason=reason))

    def query(tag, re, alpha, panels=160, sequence='ascending'):
        folder = root/tag
        if replay:
            result = json.loads((folder/'query.json').read_text())
            if (result['coordinates'] != points or result['reynolds'] != re or
                    result['requested_angles_deg'] != list(alpha) or result['panels'] != panels
                    or result['sequence'] != sequence or result['transition'] !=
                    polars.Transition.model_validate(transition or {}).model_dump(mode='json')):
                raise ValueError('Coordinate audit query inputs changed')
            polars.validate(result, folder)
        else:
            result = polars.query(points, re, alpha, folder, transition=transition,
                                  panels=panels, sequence=sequence)
            (folder/'query.json').write_text(canonical_json(result))
        report['queries'].append(dict(directory=tag, report=result))
        if result['state'] != 'complete':
            reject(f'Incomplete coordinate audit coverage at Re {re}: '
                   f"missing alpha {result['missing_angles_deg']}")
        return result

    def compare(a, b, left, right, kind):
        # Compare complete independent grids only; do not hide evaluable pairs
        # because a different initialization failed to return its requested rows.
        if a['state'] != 'complete' or b['state'] != 'complete':
            return
        differences = {}
        for key in TOLERANCES:
            x, y = max(zip(a['rows'], b['rows']), key=lambda pair: abs(pair[0][key]-pair[1][key]))
            differences[key] = dict(alpha_deg=x['alpha_deg'], left=x[key], right=y[key],
                                    absolute_error=abs(x[key]-y[key]))
        report['comparisons'].append(dict(kind=kind, reynolds=a['reynolds'],
            left=left, right=right, differences=differences))
        if any(differences[k]['absolute_error'] > TOLERANCES[k] for k in TOLERANCES):
            reject('Coordinate ' + ('initialization sensitivity' if kind == 'initialization'
                                   else 'panel refinement') + ' exceeds tolerance')

    try:
        report['stage'] = 'initialization_and_panel_refinement'
        coarse = []
        for i, re in enumerate(reynolds):
            samples = [query(f're_{i}/{seq}', re, angles, sequence=seq) for seq in SEQUENCES]
            coarse.append(samples[0])
            refined = query(f're_{i}/refined', re, angles, panels=240)
            if all(r['state'] == 'complete' for r in samples[:2]) and samples[0]['rows'] != samples[1]['rows']:
                reject('Coordinate initialization repeat differs')
            for j, a in enumerate(samples):
                for k in range(j+1, len(samples)):
                    compare(a, samples[k], f're_{i}/{SEQUENCES[j]}',
                            f're_{i}/{SEQUENCES[k]}', 'initialization')
            compare(samples[0], refined, f're_{i}/ascending', f're_{i}/refined', 'panel_refinement')
            for result in [*samples, refined]:
                if result['state'] != 'complete':
                    continue
                if any(b['cl'] <= a['cl'] for a, b in zip(result['rows'], result['rows'][1:])):
                    reject('Coordinate audit leaves positive-slope coverage')
        report['stage'] = 'interpolation_coverage'
        bank = None
        try:
            bank = polars.CoordinateBank(coarse)
        except ValueError as exc:
            reject(str(exc))
        midangles = [(a+b)/2 for a, b in zip(angles, angles[1:])]
        # query requires at least three points; retain explicit endpoint angles
        # for narrow grids, without removing any midpoint.
        midangles = sorted(set([angles[0], *midangles, angles[-1]]))
        errors = dict.fromkeys(TOLERANCES, 0.)
        worst = dict.fromkeys(TOLERANCES)
        checked = 0
        for i, (a, b) in enumerate(zip(reynolds, reynolds[1:])):
            result = query(f'midpoint_{i}', math.sqrt(a*b), midangles, panels=240)
            if bank is None or result['state'] != 'complete':
                continue
            checked += 1
            for row in result['rows']:
                predicted = bank.coefficients(digest(points), result['reynolds'], row['alpha_deg'])
                for key, value in zip(TOLERANCES, predicted):
                    error = abs(value-row[key])
                    if worst[key] is None or error > errors[key]:
                        worst[key] = dict(directory=f'midpoint_{i}', reynolds=result['reynolds'],
                            alpha_deg=row['alpha_deg'], predicted=value, observed=row[key], absolute_error=error)
                    errors[key] = max(errors[key], error)
        report['interpolation_queries_checked'] = checked
        report['interpolation_differences'] = errors if checked else None
        report['interpolation_worst'] = worst if checked else None
        if any(errors[k] > TOLERANCES[k] for k in TOLERANCES):
            reject('Coordinate midpoint interpolation exceeds tolerance')
        if failures:
            report.update(stage=failures[0]['stage'], failures=failures,
                          failure=dict(type='ValueError', reason=failures[0]['reason']))
        else:
            report.update(state='passed', stage='completed')
    except (ValueError, OSError) as exc:
        report['failure'] = dict(type=type(exc).__name__, reason=str(exc)[:600])
        if isinstance(exc, NativeFailure):
            report['failure']['native_evidence'] = exc.evidence
    report['artifacts'] = _inventory(root, exclude=('coordinate-audit.json',))
    report['evidence_sha256'] = digest(report)
    if not replay:
        (root/'coordinate-audit.json').write_text(canonical_json(report))
    return report


def validate(report, directory):
    expected = audit(directory=directory, replay=True, **report['inputs'])
    if canonical_json(expected) != canonical_json(report):
        raise ValueError('Coordinate audit differs from native reconstruction')
    return False
