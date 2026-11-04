"""Qualify/replay the pinned narrow low-Re section window; no aircraft admission."""
import argparse,json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
sys.path.insert(1,str(Path(__file__).resolve().parents[2]))
from cem.aero.section_qualification import run_section_reference,validate_section_reference


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True);p.add_argument('--replay',action='store_true')
    args=p.parse_args()
    if args.replay:
        report=json.loads((args.directory/'section-qualification.json').read_text())
        validate_section_reference(report,args.directory)
    else: report=run_section_reference(args.directory)
    summary={k:report[k] for k in ('case_id','state','aircraft_admission','verified_gain','evidence_sha256')}
    if report['state']=='passed':
        summary.update(panel_differences=report['panel_differences'],interpolation_differences=report['interpolation_differences'])
    else: summary['failure']=report.get('failure')
    print(json.dumps(summary,indent=2))
    return 0 if report['state']=='passed' else 1


if __name__=='__main__': raise SystemExit(main())
