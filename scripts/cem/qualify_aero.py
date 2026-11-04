"""Run or replay the pinned native whole-aircraft reference without a provider."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
sys.path.insert(1,str(Path(__file__).resolve().parents[2]))
from cem.aero.qualification import run_reference, validate_reference


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--replay',action='store_true')
    args=parser.parse_args()
    if args.replay:
        report=json.loads((args.directory/'qualification.json').read_text())
        validate_reference(report,args.directory)
    else:
        report=run_reference(args.directory)
    summary={k:report[k] for k in ('case_id','state','verified_gain','evidence_sha256')}
    if report['state']=='passed':
        summary.update(objective_m=report['performance']['objective_value'],
            static_margin=report['stability']['static_margin'],unstable_case=report['unstable_case']['state'])
    else:
        summary['failure']=report.get('failure')
    print(json.dumps(summary,indent=2))
    return 0 if report['state']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
