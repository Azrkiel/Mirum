"""Audit four bounded XFOIL initialization sequences; no aircraft admission."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
sys.path.insert(1,str(Path(__file__).resolve().parents[2]))
from cem.aero.polar_audit import run_section_audit,validate_section_audit


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--naca',default='0006')
    p.add_argument('--reynolds',type=float,default=30000.)
    p.add_argument('--replay',action='store_true')
    args=p.parse_args()
    if args.replay:
        report=json.loads((args.directory/'polar-audit.json').read_text())
        validate_section_audit(report,args.directory)
    else:
        report=run_section_audit(args.directory,args.naca,args.reynolds,[i*.5 for i in range(-8,17)])
    print(json.dumps(dict(state=report['state'],production_admissible=report['production_admissible'],
        all_sequences_complete=report['all_sequences_complete'],repeatable=report['repeatable'],
        sequences=[{k:v for k,v in row.items() if k in ('sequence','state','missing_alpha_deg','reason')}
                   for row in report['sequences']],sequence_sensitive_alpha_deg=report['sequence_sensitive_alpha_deg'],
        evidence_sha256=report['evidence_sha256']),indent=2))
    # Diagnostic execution success is not convergence or qualification success.
    return 0 if report['state']=='diagnostic_completed' else 1


if __name__=='__main__': raise SystemExit(main())
