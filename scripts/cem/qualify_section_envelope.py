"""Fresh declared section banks and covered swept solver requests, never admission.

Requires a declaration containing exact foils, installed archives and required
banks. Run native audit/replay sequentially; never admit a failed/aborted audit.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from cem.core import canonical_json, digest
from cem.glider.families.coordinate_audit import audit, validate
from cem.glider.families.coordinate_polars import CoordinateBank
from cem.glider.families.coupled_aero import solve, replay


def qualify(declaration, directory, settings=None, vaccel=None):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    # Declaration is retained before any native experiment.
    (root/'declaration.json').write_text(canonical_json(dict(
        envelope=declaration, low_re_panel_distribution=settings, low_re_vaccel=vaccel)))
    results, passed = [], []
    for bank in declaration['required_banks']:
        key = bank['foil']
        low_re = key.startswith(('e9426ae7ef', 'f925aa8eb7'))
        folder = root/key
        result = dict(foil=key, domain=bank, panel_distribution=settings if low_re else None)
        try:
            report = audit(declaration['foils'][key], bank['reynolds'], bank['angles'], folder,
                           panel_distribution=result['panel_distribution'], vaccel=vaccel if low_re else None)
            result.update(state=report['state'], failure=report.get('failure'),
                failures=report.get('failures'), evidence_sha256=report['evidence_sha256'],
                interpolation_worst=report.get('interpolation_worst'), queries=len(report['queries']))
            try:
                assert validate(report, folder) is False
                result['independent_replay'] = 'matched'
            except (ValueError, OSError) as exc:
                result['independent_replay'] = dict(state='unmatched', reason=str(exc))
            if report['state']=='passed' and result['independent_replay']=='matched':
                passed.extend(q['report'] for q in report['queries'] if q['directory'].endswith('/ascending'))
        except (ValueError, OSError) as exc:
            result.update(state='aborted', reason=str(exc), independent_replay='unavailable')
        results.append(result)
        (root/'results.json').write_text(canonical_json(results))
        print(key[:10], result['state'], result['independent_replay'], flush=True)
    if not all(any(r['foil']==key and r['state']=='passed' and r['independent_replay']=='matched'
                   for r in results) for key in declaration['foils'] if key.startswith(('e974','d8e3'))):
        return results
    bank = CoordinateBank(passed)
    traces = []
    for state in declaration['states'][:2]:
        trace = dict(installed_sha256=state['installed']['installed_sha256'], controls=state['controls'],
                     requests=[], production_admission=False)

        class TraceBank:
            # Delegate to the actual bank. Standard solve/replay is performed
            # separately so instrumentation cannot masquerade as model source.
            reports = bank.reports

            def coefficients(self, foil, re, alpha):
                entry = dict(foil=foil, reynolds=re, alpha_deg=alpha)
                trace['requests'].append(entry)
                try:
                    value = bank.coefficients(foil, re, alpha)
                except ValueError as exc:
                    entry['rejected'] = str(exc)
                    raise
                entry['coefficients'] = value
                return value

        try:
            instrumented = solve(state['archive'], state['controls'], TraceBank(),
                                 **declaration['swept_solver_conditions'])
            standard = solve(state['archive'], state['controls'], bank,
                             **declaration['swept_solver_conditions'])
            replay(standard, bank)
            # Instrumentation fingerprint differs by design; physical results
            # and all conditions must be identical to the uninstrumented solve.
            scientific = lambda r: {k:v for k,v in r.items() if k!='implementation_sha256'}
            if canonical_json(scientific(instrumented)) != canonical_json(scientific(standard)):
                raise ValueError('Instrumented and standard solver payloads differ')
            trace.update(state='circulation_root_only', result=standard, independent_replay='matched')
        except ValueError as exc:
            trace.update(state='failed', reason=str(exc))
        traces.append(trace)
        (root/'solver-traces.json').write_text(canonical_json(traces))
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--declaration', type=Path, required=True)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--settings', type=Path)
    p.add_argument('--vaccel', type=float)
    args = p.parse_args()
    qualify(json.loads(args.declaration.read_text()), args.directory,
            json.loads(args.settings.read_text()) if args.settings else None, args.vaccel)


if __name__ == '__main__':
    main()
