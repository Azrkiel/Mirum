"""Fresh native section audits feeding the actual-family aircraft-physics ledger.

Reconstructs the archived tailless-swept neutral and +3-degree pitch-channel
installed states, declares the previously passed bounded grids *before* any
native work, and runs each whole coordinate audit plus an independent replay
with the current executables. Only complete passed/replayed audits supply a
bank. The ledger then reports which aircraft contributions remain unsupported;
no equilibrium is attempted while any required contribution is missing.

Run sequentially, with no competing native job. The 20-second invocation,
1 MiB file and 150-artifact audit caps and all tolerances are unchanged.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from cem.core import canonical_json, digest
from cem.glider.families.coordinate_audit import audit, validate as replay_audit
from cem.glider.families.coordinate_polars import CoordinateBank
from cem.glider.families.installed import freeze, validate
from cem.glider.families.physics import assessment, mass as massprops

GRIDS = dict(
    deflected=dict(reynolds=[49000., 56000., 64000.],
                   angles=[2.5, 2.875, 3.25, 3.625, 4., 4.375, 4.75, 5.125, 5.5]),
    neutral=dict(reynolds=[49000., 74000.], angles=[3., 4.5, 6.]))
OPERATING_POINT = dict(speed_m_s=6., alpha_deg=4., beta_deg=0., pitch_deg=0., bank_deg=0.)


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
    deflected = set(foils[1]) - set(foils[0])
    neutral = set(foils[1]) & set(foils[0])
    if len(deflected) != 1 or len(neutral) != 1:
        raise ValueError('Expected one deflected and one neutral swept foil')
    (deflected,), (neutral,) = deflected, neutral
    target = states[1]
    declaration = dict(archive=target['archive'], controls=target['controls'],
        installed_sha256=installed[1]['installed_sha256'], foils=dict(deflected=deflected, neutral=neutral),
        grids=GRIDS, operating_point=OPERATING_POINT, spanwise=4, production_admission=False)
    (root / 'declaration.json').write_text(canonical_json(declaration))
    audits, reports = {}, []
    for name, foil in (('deflected', deflected), ('neutral', neutral)):
        points = foils[1][foil]
        folder = root / name
        report = audit(points, GRIDS[name]['reynolds'], GRIDS[name]['angles'], folder)
        replay = replay_audit(report, folder)
        audits[name] = dict(state=report['state'], replay='matched' if replay is False else 'differs',
            failure=report.get('failure'), interpolation_worst=report.get('interpolation_worst'),
            query_count=len(report['queries']), evidence_sha256=report['evidence_sha256'])
        print(name, report['state'], 'replay matched', flush=True)
        if report['state'] == 'passed':
            reports += [q['report'] for q in report['queries'] if q['directory'].endswith('/ascending')]
    (root / 'audits.json').write_text(canonical_json(audits))
    bank = bank_evidence = None
    if all(a['state'] == 'passed' and a['replay'] == 'matched' for a in audits.values()):
        bank = CoordinateBank(reports)
        bank_evidence = dict(state='passed_and_replayed', replay='matched',
            report_sha256=[digest(r) for r in reports],
            audit_evidence_sha256=[a['evidence_sha256'] for a in audits.values()])
    result = assessment.assess(target['archive'], controls=target['controls'],
        operating_point=OPERATING_POINT, bank=bank, bank_evidence=bank_evidence, spanwise=4)
    compact = dict(assessment.summary(result), audits=audits,
        lifting=next((c for c in result.get('contributions', []) if c['name'] == 'lifting_surfaces'), None),
        mass_properties={k: result['mass_properties'][k] for k in (
            'mass_kg', 'cg_m', 'inertia_cg_kg_m2', 'products_of_inertia_kg_m2', 'principal_kg_m2',
            'omitted_self_inertia', 'state', 'mass_properties_sha256')},
        screening_binding=massprops.family_screening_properties(
            __import__('cem.glider.families', fromlist=['reconstruct']).reconstruct(target['archive']))['binding_sha256'])
    (root / 'assessment.json').write_text(canonical_json(result))
    (root / 'summary.json').write_text(canonical_json(compact))
    print(json.dumps(dict(state=compact['state'], unsupported=[u['name'] for u in compact['unsupported'] or []]),
                     indent=1), flush=True)


if __name__ == '__main__':
    main()
