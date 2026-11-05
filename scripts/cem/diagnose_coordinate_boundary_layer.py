"""Retained, repeated BL diagnostics for complete coordinate initialization sweeps.

Instrument the standard query commands with DUMP after each requested ALFA.
These reports are diagnostics, never qualified polar banks. Run sequentially.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
import numpy as np
from cem.core import canonical_json, digest
from cem.aero.native import artifact, NativeFailure
from cem.glider.families import coordinate_polars as polars

MANUAL_SHA256 = 'c36482e92f6cab424e965ccb48d0d81ebc030204ebf44a4205b366b9d7db3bea'


def gradients(path, panels, row):
    data = np.loadtxt(path, comments='#', usecols=range(8))[:panels]
    # XFOIL BLDUMP writes HK in column 8 (labelled H), verified in xoper.f.
    # Split at the leading edge and orient both sides downstream. Exclude wake.
    leading = int(np.argmin(data[:, 1]))
    results = []
    for side, points, transition in [('top', data[:leading+1][::-1], row[5]),
                                      ('bottom', data[leading:], row[6])]:
        eligible = [(a, b) for a, b in zip(points, points[1:])
                    if a[1] >= transition and b[1] > a[1] and b[1] <= 1.]
        violations = [dict(x0=float(a[1]), x1=float(b[1]), hk_drop=float(a[7]-b[7]),
                           ue_change=float(abs(abs(a[3])-abs(b[3])))) for a, b in eligible
                      if a[7]-b[7] > 1 or abs(abs(a[3])-abs(b[3])) > .05]
        results.append(dict(side=side, transition_x=float(transition), eligible_pairs=len(eligible),
            max_hk_drop=max((float(a[7]-b[7]) for a,b in eligible), default=None),
            max_ue_change=max((float(abs(abs(a[3])-abs(b[3]))) for a,b in eligible), default=None),
            violations=violations))
    return results


def diagnose(points, reynolds, angles, directory, *, panels, sequence, settings, vaccel=None):
    original = polars.run
    dumps = []

    def instrument(name, script, folder, stem, outputs):
        lines = []
        recording = False
        for line in script.splitlines():
            lines.append(line)
            if line == 'PACC':
                recording = not recording
            if recording and line.startswith('ALFA '):
                name = f'bl_{len(dumps)}.dat'
                dumps.append((float(line.split()[1]), name))
                lines.append('DUMP '+name)
        return original('xfoil', '\n'.join(lines)+'\n', folder, stem,
                        (*outputs, *(name for _, name in dumps)))

    polars.run = instrument
    folder = Path(directory)
    try:
        query = polars.query(points, reynolds, angles, folder, panels=panels,
                             sequence=sequence, panel_distribution=settings, vaccel=vaccel)
    except NativeFailure as exc:
        return dict(state='interrupted', evidence=exc.evidence, production_admission=False)
    finally:
        polars.run = original
    raw = np.loadtxt(folder/'polar.out', skiprows=12, ndmin=2)
    results = []
    for alpha, name in dumps:
        matches = [r for r in raw if abs(r[0]-alpha) <= .001]
        results.append(dict(alpha=alpha, file=name, gradients=gradients(folder/name, panels, matches[0])
                            if len(matches) == 1 else None, converged_polar_row=len(matches)==1))
    return dict(scope='instrumented_coordinate_bl_diagnostic_not_bank', query=query, dumps=results,
                state=query['state'], instrumentation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                production_admission=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--declaration', type=Path, required=True)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--manual', type=Path, required=True)
    p.add_argument('--xoper', type=Path, required=True)
    p.add_argument('--settings', type=Path)
    p.add_argument('--vaccel', type=float)
    args = p.parse_args()
    if hashlib.sha256(args.manual.read_bytes()).hexdigest() != MANUAL_SHA256:
        raise ValueError('Manual checksum mismatch')
    declaration = json.loads(args.declaration.read_text())
    settings = json.loads(args.settings.read_text()) if args.settings else None
    polars.check_panel_distribution(settings)
    args.directory.mkdir(parents=True, exist_ok=False)
    inputs = dict(declaration=declaration, settings=settings, vaccel=args.vaccel, manual_sha256=MANUAL_SHA256,
                  xoper_sha256=hashlib.sha256(args.xoper.read_bytes()).hexdigest())
    (args.directory/'declaration.json').write_text(canonical_json(inputs))
    results = []
    for key, points in declaration['foils'].items():
        if key[:10] not in ('e9426ae7ef', 'f925aa8eb7'):
            continue
        angles = [4.,6.,8.] if key.startswith('e942') else [5.,6.5,8.]
        for panels in (160,240):
            for sequence in ('ascending','zero_centered','reset_each'):
                case = dict(panels=panels, sequence=sequence, settings=settings, vaccel=args.vaccel)
                folder = args.directory/f'{key[:10]}-{panels}-{sequence}'
                a = diagnose(points, 33000., angles, folder/'first', **case)
                b = diagnose(points, 33000., angles, folder/'repeat', **case)
                # Raw logs can contain timing variation; deterministic input,
                # foil, polar and every BL dump must repeat exactly.
                entries = lambda r: {x['file']:x['sha256'] for x in r.get('query',{}).get('native',{}).get('artifacts',[]) if x['file']!='polar.log'}
                scientific = lambda r: {k:v for k,v in r.items() if k!='query'}
                repeat = a['state']!='interrupted' and entries(a)==entries(b) and scientific(a)==scientific(b)
                result = dict(foil=key, case=case, report=a, independent_repeat='matched' if repeat else 'unmatched')
                results.append(result)
                (args.directory/'results.json').write_text(canonical_json(results))
                print(key[:10], panels, sequence, a['state'], result['independent_repeat'], flush=True)


if __name__ == '__main__':
    main()
