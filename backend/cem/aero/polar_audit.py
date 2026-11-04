"""Bounded section convergence/initialization diagnostics, never aircraft admission."""
from pathlib import Path

from cem.core import canonical_json,digest
from cem.evidence import number
from cem.provenance import provenance
from .contracts import Transition
from .native import run,resolve,artifact,NativeFailure
from .polars import script,check_polar_controls
from .qualification import _inventory

SEQUENCES=('ascending','ascending_repeat','zero_centered','reset_each')


def command(naca,reynolds,angles,transition,sequence):
    original=script(naca,reynolds,angles,transition,'polar.out')
    prefix=original.split('PACC\n',1)[0]+'PACC\npolar.out\n\n'
    if sequence in ('ascending','ascending_repeat'):
        return original
    if sequence=='zero_centered':
        commands=[f'ALFA {a:.8g}' for a in angles if a>=0]
        commands+=['INIT']+[f'ALFA {a:.8g}' for a in reversed(angles) if a<0]
    elif sequence=='reset_each':
        commands=[value for angle in angles for value in ('INIT',f'ALFA {angle:.8g}')]
    else:
        raise ValueError('Unknown polar initialization diagnostic')
    return prefix+'\n'.join(commands)+'\nPACC\n\nQUIT\n'


def run_section_audit(directory,naca,reynolds,angles,transition=None,*,replay=False):
    from analysis.airfoil import _parse_polar_file
    root=Path(directory);transition=transition or Transition()
    # Validate all controls before creating any output or invoking a solver.
    command(naca,reynolds,angles,transition,'ascending')
    if root.exists() and any(root.iterdir()) and not replay:
        raise ValueError('Use an empty directory to preserve previous polar evidence')
    root.mkdir(parents=True,exist_ok=True)
    controls=dict(naca=naca,reynolds=float(reynolds),alpha_deg=list(angles),transition=transition.model_dump(mode='json'))
    report=dict(kind='native_section_convergence_diagnostic',scope='Uninstalled section initialization audit; no aircraft, stall or family admission.',
        controls=controls,implementation_sha256=provenance()['implementation_sha256'],
        verified_gain=False,production_admissible=False,sequences=[])
    values={}
    for sequence in SEQUENCES:
        folder=root/sequence;commands=command(naca,reynolds,angles,transition,sequence)
        try:
            if replay:
                if (folder/'audit.in').read_text()!=commands:
                    raise ValueError('Polar diagnostic commands differ from requested controls')
                _,binary=resolve('xfoil')
                evidence=dict(solver='xfoil',executable_sha256=binary,state='completed',returncode=0,
                    artifacts=[artifact(folder/name,folder) for name in ('audit.in','audit.log','polar.out')])
            else:
                evidence=run('xfoil',commands,folder,'audit',('polar.out',))
            data=_parse_polar_file(str(folder/'polar.out'))
            check_polar_controls((folder/'polar.out').read_text(),naca,reynolds,transition)
            for column in data.values():
                for value in column: number(value)
            mapped={}
            for i,alpha in enumerate(data['alpha']):
                match=[a for a in angles if abs(a-alpha)<=.001]
                if len(match)!=1 or match[0] in mapped:
                    raise ValueError('Duplicate or unexpected native diagnostic angle')
                if data['Cd'][i]<=0: raise ValueError('Nonpositive diagnostic profile drag')
                mapped[match[0]]={k:data[k][i] for k in ('Cl','Cd','Cm')}
            missing=[a for a in angles if a not in mapped]
            report['sequences'].append(dict(sequence=sequence,state='partial' if missing else 'complete',
                missing_alpha_deg=missing,rows=len(mapped),polar=data,native=evidence))
            values[sequence]=mapped
        except (ValueError,OSError) as exc:
            if replay: raise
            entry=dict(sequence=sequence,state='failed',reason=str(exc)[:500])
            if isinstance(exc,NativeFailure): entry['native']=exc.evidence
            report['sequences'].append(entry)
    report['all_sequences_complete']=all(s['state']=='complete' for s in report['sequences'])
    report['state']='diagnostic_completed' if all(s['state'] in ('complete','partial') for s in report['sequences']) else 'diagnostic_failed'
    a,b=values.get('ascending'),values.get('ascending_repeat')
    report['repeatable']=(a==b) if a is not None and b is not None else None
    spreads={};sensitive=[]
    thresholds=dict(Cl=.025,Cd=.001,Cm=.005)
    for alpha in angles:
        rows=[v[alpha] for v in values.values() if alpha in v]
        if len(rows)<2: continue
        spread={k:max(r[k] for r in rows)-min(r[k] for r in rows) for k in thresholds}
        spreads[str(alpha)]=spread
        if any(spread[k]>thresholds[k] for k in thresholds): sensitive.append(alpha)
    report.update(sequence_spread=spreads,sequence_sensitive_alpha_deg=sensitive,
                  diagnostic_disagreement_thresholds=thresholds)
    report['artifacts']=_inventory(root,exclude=('polar-audit.json',))
    report['evidence_sha256']=digest(report)
    if not replay: (root/'polar-audit.json').write_text(canonical_json(report))
    return report


def validate_section_audit(report,directory):
    c=report['controls']
    actual=run_section_audit(directory,c['naca'],c['reynolds'],c['alpha_deg'],Transition(**c['transition']),replay=True)
    if canonical_json(actual)!=canonical_json(report):
        raise ValueError('Section diagnostic differs from reconstructed native evidence')
