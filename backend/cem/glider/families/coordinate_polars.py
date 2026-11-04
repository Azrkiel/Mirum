"""Exact-coordinate XFOIL development polars; missing rows remain missing.

These two-dimensional attached-flow results do not qualify aircraft stall or
interference. Existing mirum-aero-1 NACA/neutral restrictions are unchanged.
"""
import inspect
import json
import math
from pathlib import Path
import tempfile

import numpy as np

from analysis.airfoil import _parse_polar_file
from cem.aero.contracts import Transition
from cem.aero.polars import PolarCoverageError, check_polar_controls
from cem.aero.polar_audit import SEQUENCES
from cem.aero import native as native_runner
from cem.aero.native import run, artifact, resolve, NativeFailure
from cem.core import canonical_json, digest


def coordinates_payload(coordinates):
    points = np.asarray(coordinates)
    if (points.dtype.kind not in 'fi' or points.ndim != 2 or points.shape[1] != 2
            or not 20 <= len(points) <= 500 or not np.isfinite(points).all()
            or points[:, 0].min() < -.2 or points[:, 0].max() > 1.2
            or abs(points[:, 1]).max() > .5 or np.ptp(points[:, 0]) < .9):
        raise ValueError('Unsupported finite normalized airfoil coordinates')
    return points.astype(float).tolist()


def check_vaccel(vaccel):
    # XFOIL's manual recommends reducing acceleration below Re 100,000.
    # None retains the native default; no automatic convergence rescue is used.
    if vaccel is not None and (type(vaccel) not in (int, float) or
            not math.isfinite(vaccel) or not 0 <= vaccel <= .01):
        raise ValueError('Unsupported XFOIL VACCEL (expected 0 through .01 or None)')


def query(coordinates, reynolds, angles, directory, *, transition=None, panels=160, sequence="ascending", vaccel=None):
    check_vaccel(vaccel)
    if sequence not in SEQUENCES:
        raise ValueError('Unknown coordinate initialization sequence')
    points = coordinates_payload(coordinates)
    if (type(reynolds) not in (int, float) or not math.isfinite(reynolds)
            or not 30000 <= reynolds <= 1000000):
        raise ValueError('Unsupported coordinate-polar Reynolds number')
    if (not 3 <= len(angles) <= 81 or any(type(a) not in (int, float) or
            not math.isfinite(a) or not -10 <= a <= 12 for a in angles)
            or any(b <= a for a, b in zip(angles, angles[1:])) or panels not in (160, 240)):
        raise ValueError('Unsupported coordinate-polar angles or panel count')
    t = transition if isinstance(transition, Transition) else Transition.model_validate(transition or {})
    folder = Path(directory)
    if folder.exists() and any(folder.iterdir()):
        raise ValueError('Coordinate polar requires an empty directory')
    folder.mkdir(parents=True, exist_ok=True)
    data = 'mirum_exact_coordinates\n' + ''.join(f'{x:.17g} {y:.17g}\n' for x,y in points)
    (folder/'foil.dat').write_text(data)
    # PANE repanels the supplied shape; no NACA substitute, coordinate
    # normalization toggle or changed transition is used to rescue convergence.
    commands = ['PLOP', 'G F', '', 'LOAD foil.dat', 'PPAR', f'N {panels}', '', '',
        'PANE', 'OPER', f'VISC {reynolds:.12g}', 'MACH 0', 'VPAR',
        f'N {t.n_crit:.12g}', f'XTR {t.top_xtr:.12g} {t.bottom_xtr:.12g}', '',
        'ITER 150', 'ALFA 0', 'PACC', 'polar.out', '']
    if vaccel is not None:
        # Set before the first ALFA, inside VPAR, then leave VPAR normally.
        commands[commands.index('ITER 150'):commands.index('ITER 150')] = [
            'VPAR', f'VACC {vaccel:.17g}', '']
    if sequence in ('ascending', 'ascending_repeat'):
        commands += [f'ALFA {a:.8g}' for a in angles]
    elif sequence == 'zero_centered':
        commands += [f'ALFA {a:.8g}' for a in angles if a >= 0]
        commands += ['INIT'] + [f'ALFA {a:.8g}' for a in reversed(angles) if a < 0]
    else:
        commands += [v for a in angles for v in ('INIT', f'ALFA {a:.8g}')]
    commands += ['PACC', '', 'QUIT']
    try:
        native = run('xfoil', '\n'.join(commands)+'\n', folder, 'polar', ('polar.out',))
    except NativeFailure as exc:
        # Preserve an interrupted invocation separately from scientific query rows.
        evidence = dict(scope='interrupted_coordinate_query', inputs=dict(
            coordinates=points, reynolds=reynolds, angles=list(angles),
            transition=t.model_dump(mode='json'), panels=panels, sequence=sequence,
            vaccel=vaccel), native=exc.evidence, reason=str(exc),
            timeout_s=native_runner.TIMEOUT_S,
            implementation_sha256=digest({name: Path(inspect.getfile(module)).read_text()
                for name, module in [('query', inspect.getmodule(query)), ('native', native_runner)]}),
            foil=artifact(folder/'foil.dat', folder), production_admission=False)
        (folder/'failure.json').write_text(canonical_json(evidence))
        raise NativeFailure(str(exc), evidence) from exc
    native['artifacts'].append(artifact(folder/'foil.dat', folder))
    observed = check_polar_controls((folder/'polar.out').read_text(), None, reynolds, t,
                                    coordinate_name='mirum_exact_coordinates')
    parsed = _parse_polar_file(str(folder/'polar.out'))
    rows = [dict(alpha_deg=a, cl=cl, cd=cd, cm=cm) for a,cl,cd,cm in
            zip(parsed['alpha'],parsed['Cl'],parsed['Cd'],parsed['Cm'])]
    rows.sort(key=lambda row: row['alpha_deg'])
    if (any(not all(math.isfinite(v) for v in row.values()) or row['cd'] <= 0 for row in rows)
            or any(b['alpha_deg'] <= a['alpha_deg'] for a,b in zip(rows,rows[1:]))):
        raise ValueError('Invalid native coordinate polar rows')
    if any(not any(abs(row['alpha_deg']-a) <= .001 for a in angles) for row in rows):
        raise ValueError('Unexpected native polar angle')
    missing = [float(a) for a in angles if not any(abs(row['alpha_deg']-a) <= .001 for row in rows)]
    return dict(scope='exact_coordinate_section_polar_development', coordinates=points,
        coordinates_sha256=digest(points), reynolds=float(reynolds),
        sequence=sequence, vaccel=vaccel, reported_controls=observed, requested_angles_deg=list(angles), transition=t.model_dump(mode='json'), panels=panels,
        state='incomplete' if missing else 'complete', missing_angles_deg=missing,
        rows=rows, native=native, qualified_aircraft_stall=False)


def validate(report, directory):
    """Check retained bytes and independently repeat the coordinate query."""
    folder = Path(directory)
    if report.get('scope') != 'exact_coordinate_section_polar_development':
        raise ValueError('Unknown coordinate polar scope')
    if resolve('xfoil')[1] != report['native']['executable_sha256']:
        raise ValueError('Coordinate polar executable identity changed')
    native = report['native']
    if (native.get('solver') != 'xfoil' or native.get('state') != 'completed'
            or native.get('returncode') != 0 or len(native['artifacts']) != 4
            or {a['file'] for a in native['artifacts']} != {'foil.dat','polar.in','polar.log','polar.out'}):
        raise ValueError('Incomplete coordinate polar native artifacts')
    for entry in report['native']['artifacts']:
        name = entry['file']
        if name not in ('foil.dat','polar.in','polar.log','polar.out'):
            raise ValueError('Unexpected coordinate polar artifact')
        path = folder/name
        if path.is_symlink() or artifact(path,folder) != entry:
            raise ValueError('Coordinate polar artifact differs from retained identity')
    with tempfile.TemporaryDirectory(prefix='mirum-coordinate-polar-') as tmp:
        expected = query(report['coordinates'], report['reynolds'], report['requested_angles_deg'], tmp,
            transition=report['transition'], panels=report['panels'], sequence=report['sequence'], vaccel=report['vaccel'])
    # Bind declared numerical controls to the retained deterministic commands,
    # even when two settings happen to yield identical rounded polar rows.
    for name in ('polar.in', 'foil.dat'):
        identity = lambda r: next(a for a in r['native']['artifacts'] if a['file'] == name)
        if identity(expected) != identity(report):
            raise ValueError('Coordinate polar inputs differ from native reconstruction')
    # Native log timing text can vary. Compare scientific payloads exactly;
    # the retained raw logs must separately match their recorded hashes above.
    scientific = lambda r: {k:v for k,v in r.items() if k != 'native'}
    if canonical_json(scientific(expected)) != canonical_json(scientific(report)):
        raise ValueError('Coordinate polar differs from native reconstruction')
    return False


def validate_failure(report, directory):
    """Reconstruct a failure with fresh native work; never return admission."""
    folder = Path(directory)
    native = report['native']
    if (report.get('scope') != 'interrupted_coordinate_query'
            or native.get('solver') != 'xfoil'
            or native.get('state') not in ('timeout', 'runtime_sigfpe', 'runtime_failure', 'missing_output')
            or resolve('xfoil')[1] != native.get('executable_sha256')):
        raise ValueError('Unsupported or changed interrupted executable evidence')
    entries = [report['foil'], *native['artifacts']]
    names = [entry['file'] for entry in entries]
    if (len(names) != len(set(names)) or not {'foil.dat', 'polar.in', 'polar.log'} <= set(names)
            or not set(names) <= {'foil.dat', 'polar.in', 'polar.log', 'polar.out'}):
        raise ValueError('Missing or unexpected interrupted artifacts')
    for entry in entries:
        path = folder/entry['file']
        if path.is_symlink() or artifact(path, folder) != entry:
            raise ValueError('Interrupted artifact identity changed')
    with tempfile.TemporaryDirectory(prefix='mirum-interrupted-query-') as tmp:
        try:
            query(directory=tmp, **report['inputs'])
        except NativeFailure as exc:
            if canonical_json(exc.evidence) != canonical_json(report):
                raise ValueError('Interrupted query differs from native reconstruction') from exc
        else:
            raise ValueError('Interrupted query did not reproduce failure')
    return False


class CoordinateBank:
    """Complete alpha/log-Re cells only; reject extrapolation and post-peak cells."""
    def __init__(self, reports):
        from copy import deepcopy
        reports = deepcopy(list(reports))
        self.reports = reports
        self.groups = {}
        for r in reports:
            check_vaccel(r.get('vaccel'))
            if (r['state'] != 'complete' or r['missing_angles_deg'] or
                    digest(coordinates_payload(r['coordinates'])) != r['coordinates_sha256']):
                raise ValueError('Incomplete or altered coordinate polar')
            rows = r['rows']
            angles = r['requested_angles_deg']
            if (not 3 <= len(angles) <= 81 or any(not math.isfinite(a) or not -10 <= a <= 12 for a in angles)
                    or any(b <= a for a,b in zip(angles,angles[1:]))
                    or not math.isfinite(r['reynolds']) or not 30000 <= r['reynolds'] <= 1000000):
                raise ValueError('Invalid coordinate polar grid')
            if (len(rows) != len(r['requested_angles_deg']) or any(
                    abs(row['alpha_deg']-a) > .001 for row,a in zip(rows,r['requested_angles_deg']))
                    or any(not all(math.isfinite(v) for v in row.values()) or row['cd'] <= 0 for row in rows)):
                raise ValueError('Incomplete or invalid coordinate polar rows')
            self.groups.setdefault(r['coordinates_sha256'], []).append(r)
        for group in self.groups.values():
            group.sort(key=lambda r:r['reynolds'])
            if len(group) < 2 or len({r['reynolds'] for r in group}) != len(group):
                raise ValueError('Missing or duplicate coordinate Reynolds coverage')
            if any((r['requested_angles_deg'],r['transition'],r['panels'],r.get('vaccel')) !=
                   (group[0]['requested_angles_deg'],group[0]['transition'],group[0]['panels'],group[0].get('vaccel')) for r in group):
                raise ValueError('Inconsistent coordinate polar grid')

    def coefficients(self, foil, reynolds, alpha):
        group = self.groups.get(foil, ())
        if (not math.isfinite(reynolds) or not math.isfinite(alpha) or not group
                or not group[0]['reynolds'] <= reynolds <= group[-1]['reynolds']):
            raise PolarCoverageError('Local Reynolds number outside coordinate polar coverage')
        angles = group[0]['requested_angles_deg']
        if not angles[0] <= alpha <= angles[-1]:
            raise PolarCoverageError('Local angle outside coordinate polar coverage')
        i = max(0,min(len(group)-2,int(np.searchsorted([r['reynolds'] for r in group],reynolds))-1))
        a,b = group[i:i+2]
        j = max(0,min(len(angles)-2,int(np.searchsorted(angles,alpha))-1))
        if any(r['rows'][j+1]['cl'] <= r['rows'][j]['cl'] for r in (a,b)):
            raise PolarCoverageError('Coordinate polar query outside attached positive-slope coverage')
        w = math.log(reynolds/a['reynolds']) / math.log(b['reynolds']/a['reynolds'])
        return tuple(float((1-w)*np.interp(alpha,angles,[row[k] for row in a['rows']]) +
                           w*np.interp(alpha,angles,[row[k] for row in b['rows']])) for k in ('cl','cd','cm'))
