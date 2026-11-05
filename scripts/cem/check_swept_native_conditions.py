"""Independently check selected converged swept conditions against fresh banks.

Every initialization/trial/converged request remains a bank interpolation. The
selected native checks use actual native rounded angles and do not qualify
unqueried strip points, whole-aircraft equilibrium or production admission.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from cem.core import canonical_json, digest
from cem.aero.polar_audit import SEQUENCES
from cem.aero.qualification import _inventory
from cem.glider.families.coordinate_audit import TOLERANCES
from cem.glider.families.coordinate_polars import CoordinateBank, query, validate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audits',type=Path,required=True)
    p.add_argument('--directory',type=Path,required=True)
    args=p.parse_args()
    summaries=json.loads((args.audits/'results.json').read_text())
    reports=[]
    for summary in summaries:
        if not summary['foil'].startswith(('e974','d8e3')):
            continue
        if summary['state']!='passed' or summary['independent_replay']!='matched':
            raise ValueError('Required swept bank has not passed and replayed')
        report=json.loads((args.audits/summary['foil']/'coordinate-audit.json').read_text())
        identity=report.pop('evidence_sha256')
        if digest(report)!=identity or identity!=summary['evidence_sha256']:
            raise ValueError('Swept audit identity changed')
        reports.extend(q['report'] for q in report['queries'] if q['directory'].endswith('/ascending'))
    bank=CoordinateBank(reports)
    traces=json.loads((args.audits/'solver-traces.json').read_text())
    cases=[]
    for t in traces:
        if t['state']!='circulation_root_only' or t['independent_replay']!='matched':
            raise ValueError('Required swept solver report has not replayed')
        # Verify every recorded initialization/Newton/converged bank request.
        for call in t['requests']:
            if 'rejected' in call or list(bank.coefficients(call['foil'],call['reynolds'],call['alpha_deg']))!=call['coefficients']:
                raise ValueError('Swept solver request is uncovered or changed')
        strips=t['result']['strips']
        for foil in sorted({s['foil_sha256'] for s in strips}):
            selected=[s for s in strips if s['foil_sha256']==foil]
            for label,point in [('minimum_Re',min(selected,key=lambda s:s['reynolds'])),
                                ('maximum_Re',max(selected,key=lambda s:s['reynolds']))]:
                cases.append(dict(controls=t['controls'],foil=foil,label=label,reynolds=point['reynolds'],
                                  alpha_deg=point['effective_alpha_deg']))
    args.directory.mkdir(parents=True,exist_ok=False)
    (args.directory/'declaration.json').write_text(canonical_json(dict(cases=cases,
        selection='Per-control-state and exact foil, converged minimum and maximum Re strips. All other points retain bank-interpolation status.',
        all_traced_requests_verified=sum(len(t['requests']) for t in traces),production_admission=False)))
    results=[]
    for i,case in enumerate(cases):
        folder=args.directory/str(i)
        source=next(r for r in reports if r['coordinates_sha256']==case['foil'])
        angles=[case['alpha_deg']-.01,case['alpha_deg'],case['alpha_deg']+.01]
        observations=[]
        for sequence,panels in [*((s,160) for s in SEQUENCES),('ascending',240)]:
            target=folder/f'{sequence}-{panels}'
            report=query(source['coordinates'],case['reynolds'],angles,target,
                         panels=panels,sequence=sequence)
            (target/'query.json').write_text(canonical_json(report))
            validate(report,target)
            observations.append(report)
        complete=all(r['state']=='complete' for r in observations)
        differences={k:0. for k in TOLERANCES}
        if complete:
            for r in observations:
                row=r['rows'][1]
                predicted=bank.coefficients(case['foil'],case['reynolds'],row['alpha_deg'])
                for k,value in zip(TOLERANCES,predicted):
                    differences[k]=max(differences[k],abs(value-row[k]))
            for k in TOLERANCES:
                differences[k]=max(differences[k],max(r['rows'][1][k] for r in observations)-min(r['rows'][1][k] for r in observations))
        repeat_exact=observations[0]['rows']==observations[1]['rows']
        artifacts=_inventory(folder)
        result=dict(case=case,state='passed' if complete and repeat_exact and all(differences[k]<=TOLERANCES[k] for k in TOLERANCES) else 'failed',
                    complete=complete,repeat_exact=repeat_exact,max_bank_or_initialization_panel_error=differences if complete else None,
                    independent_replay='matched',query_reports=observations,artifacts=artifacts,production_admission=False)
        results.append(result)
        (args.directory/'results.json').write_text(canonical_json(results))
        print(i,case['foil'][:10],result['state'],flush=True)


if __name__=='__main__':main()
