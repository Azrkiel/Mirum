"""Exact-coordinate XFOIL development polars; missing rows remain missing.

These two-dimensional attached-flow results do not qualify aircraft stall or
interference. Existing mirum-aero-1 NACA/neutral restrictions are unchanged.
"""
import math
from pathlib import Path
import tempfile

import numpy as np

from analysis.airfoil import _parse_polar_file
from cem.aero.contracts import Transition
from cem.aero.native import run, artifact, resolve
from cem.core import canonical_json, digest


def coordinates_payload(coordinates):
    points = np.asarray(coordinates)
    if (points.dtype.kind not in 'fi' or points.ndim != 2 or points.shape[1] != 2
            or not 20 <= len(points) <= 500 or not np.isfinite(points).all()
            or points[:, 0].min() < -.2 or points[:, 0].max() > 1.2
            or abs(points[:, 1]).max() > .5 or np.ptp(points[:, 0]) < .9):
        raise ValueError('Unsupported finite normalized airfoil coordinates')
    return points.astype(float).tolist()


def query(coordinates, reynolds, angles, directory, *, transition=None, panels=160):
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
    commands += [f'ALFA {a:.8g}' for a in angles]
    commands += ['PACC', '', 'QUIT']
    native = run('xfoil', '\n'.join(commands)+'\n', folder, 'polar', ('polar.out',))
    native['artifacts'].append(artifact(folder/'foil.dat', folder))
    parsed = _parse_polar_file(str(folder/'polar.out'))
    rows = [dict(alpha_deg=a, cl=cl, cd=cd, cm=cm) for a,cl,cd,cm in
            zip(parsed['alpha'],parsed['Cl'],parsed['Cd'],parsed['Cm'])]
    if (any(not all(math.isfinite(v) for v in row.values()) or row['cd'] <= 0 for row in rows)
            or any(b['alpha_deg'] <= a['alpha_deg'] for a,b in zip(rows,rows[1:]))):
        raise ValueError('Invalid native coordinate polar rows')
    if any(not any(abs(row['alpha_deg']-a) <= .001 for a in angles) for row in rows):
        raise ValueError('Unexpected native polar angle')
    missing = [float(a) for a in angles if not any(abs(row['alpha_deg']-a) <= .001 for row in rows)]
    return dict(scope='exact_coordinate_section_polar_development', coordinates=points,
        coordinates_sha256=digest(points), reynolds=float(reynolds),
        requested_angles_deg=list(angles), transition=t.model_dump(mode='json'), panels=panels,
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
            transition=report['transition'], panels=report['panels'])
    # Native log timing text can vary. Compare scientific payloads exactly;
    # the retained raw logs must separately match their recorded hashes above.
    scientific = lambda r: {k:v for k,v in r.items() if k != 'native'}
    if canonical_json(scientific(expected)) != canonical_json(scientific(report)):
        raise ValueError('Coordinate polar differs from native reconstruction')
    return False


class CoordinateBank:
    """Complete alpha/log-Re cells only; reject extrapolation and post-peak cells."""
    def __init__(self, reports):
        from copy import deepcopy
        reports = deepcopy(list(reports))
        self.reports = reports
        self.groups = {}
        for r in reports:
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
            if any((r['requested_angles_deg'],r['transition'],r['panels']) !=
                   (group[0]['requested_angles_deg'],group[0]['transition'],group[0]['panels']) for r in group):
                raise ValueError('Inconsistent coordinate polar grid')

    def coefficients(self, foil, reynolds, alpha):
        group = self.groups.get(foil, ())
        if (not math.isfinite(reynolds) or not math.isfinite(alpha) or not group
                or not group[0]['reynolds'] <= reynolds <= group[-1]['reynolds']):
            raise ValueError('Local Reynolds number outside coordinate polar coverage')
        angles = group[0]['requested_angles_deg']
        if not angles[0] <= alpha <= angles[-1]:
            raise ValueError('Local angle outside coordinate polar coverage')
        i = max(0,min(len(group)-2,int(np.searchsorted([r['reynolds'] for r in group],reynolds))-1))
        a,b = group[i:i+2]
        j = max(0,min(len(angles)-2,int(np.searchsorted(angles,alpha))-1))
        if any(r['rows'][j+1]['cl'] <= r['rows'][j]['cl'] for r in (a,b)):
            raise ValueError('Coordinate polar query outside attached positive-slope coverage')
        w = math.log(reynolds/a['reynolds']) / math.log(b['reynolds']/a['reynolds'])
        return tuple(float((1-w)*np.interp(alpha,angles,[row[k] for row in a['rows']]) +
                           w*np.interp(alpha,angles,[row[k] for row in b['rows']])) for k in ('cl','cd','cm'))
