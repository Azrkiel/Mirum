"""Complete, refined native section windows; not aircraft or stall qualification."""
import json
import math
from pathlib import Path

import numpy as np

from cem.core import canonical_json,digest
from cem.provenance import provenance
from .contracts import Binding,Transition
from .native import resolve,NativeFailure
from .polars import query,PolarBank,PolarCoverageError
from .polar_audit import run_section_audit
from .qualification import _inventory

SPEC=Path(__file__).resolve().parents[1]/'data/aero-low-re-section-reference-v1.json'


def section_inputs():
    spec=json.loads(SPEC.read_text())
    if spec['format']!='mirum-aero-section-qualification-1':
        raise ValueError('Unsupported section reference format')
    binding=Binding(geometry_sha256=digest(dict(kind='uninstalled_naca4_section',naca=spec['naca'])),
        hardware_sha256=digest(dict(installed_hardware=None,scope=spec['scope'])),
        mission_sha256=digest(spec),process_sha256=digest(dict(surface='ideal smooth numerical section')),
        calibration_sha256=digest(dict(empirical_calibration=None,transition=spec['transition'])),
        implementation_sha256=provenance()['implementation_sha256'])
    return spec,binding


def run_section_reference(directory,*,replay=False):
    spec,binding=section_inputs();root=Path(directory)
    if root.exists() and any(root.iterdir()) and not replay:
        raise ValueError('Use an empty directory to preserve section qualification evidence')
    root.mkdir(parents=True,exist_ok=True)
    report=dict(kind='native_section_qualification',case_id=spec['case_id'],scope=spec['scope'],
        state='failed',input_sha256=digest(spec),binding=binding.model_dump(mode='json'),
        verified_gain=False,aircraft_admission=False,families_enabled=[])
    try:
        _,binary=resolve('xfoil');report['executable_sha256']=binary
        inputs=root/'section-input.json'
        if replay:
            if inputs.read_text()!=canonical_json(spec): raise ValueError('Section reference input changed')
        else: inputs.write_text(canonical_json(spec))
        report['stage']='sampled_grids_and_initialization'
        transition=Transition(**spec['transition']);rows=[];refined=[];audits=[]
        for reynolds in spec['reynolds']:
            tag=f're_{int(reynolds)}'
            rows.append(query(binding,spec['naca'],reynolds,spec['alpha_deg'],root/tag/'coarse',
                              transition,spec['panels'][0],replay=replay))
            refined.append(query(binding,spec['naca'],reynolds,spec['alpha_deg'],root/tag/'refined',
                              transition,spec['panels'][1],replay=replay))
            audit=run_section_audit(root/tag/'initialization',spec['naca'],reynolds,spec['alpha_deg'],transition,replay=replay)
            if (audit['state']!='diagnostic_completed' or not audit['all_sequences_complete']
                    or audit['repeatable'] is not True or audit['sequence_sensitive_alpha_deg']):
                raise ValueError('Incomplete or initialization-sensitive section reference coverage')
            audits.append(audit)
        differences={key:max(float(np.max(abs(np.array(getattr(a,key))-getattr(b,key))))
            for a,b in zip(rows,refined)) for key in ('cl','cd','cm')}
        if any(differences[k]>spec['tolerances'][k] for k in differences):
            raise ValueError('Section reference panel convergence failed')
        if any(min(np.diff(row.cl))<=0 for row in rows+refined):
            raise ValueError('Section reference leaves the positive-slope numerical window')
        report.update(polars=[p.model_dump(mode='json') for p in rows],
            refined_polars=[p.model_dump(mode='json') for p in refined],initialization_audits=audits,
            panel_differences=differences,stage='interpolation_coverage')
        bank=PolarBank(rows,binding);interpolation=[];errors=dict(cl=0.,cd=0.,cm=0.)
        for a,b in zip(spec['reynolds'],spec['reynolds'][1:]):
            reynolds=math.sqrt(a*b)
            native=query(binding,spec['naca'],reynolds,spec['interpolation_alpha_deg'],
                         root/f'interpolation_{int(a)}_{int(b)}',transition,240,replay=replay)
            for i,alpha in enumerate(native.alpha_deg):
                actual=bank.coefficients(spec['naca'],reynolds,alpha)
                for value,key in zip(actual,('cl','cd','cm')):
                    errors[key]=max(errors[key],abs(value-getattr(native,key)[i]))
            interpolation.append(native.model_dump(mode='json'))
        if any(errors[k]>spec['tolerances'][k] for k in errors):
            raise ValueError('Section reference interpolation disagrees with native midpoint queries')
        rejected=[]
        for point in spec['outside_queries']:
            try: bank.coefficients(spec['naca'],point['reynolds'],point['alpha_deg'])
            except PolarCoverageError as exc: rejected.append(dict(point,reason=str(exc)))
            else: raise ValueError('Section reference admitted an out-of-domain query')
        report.update(state='passed',stage='completed',polars=[p.model_dump(mode='json') for p in rows],
            refined_polars=[p.model_dump(mode='json') for p in refined],initialization_audits=audits,
            midpoint_polars=interpolation,panel_differences=differences,interpolation_differences=errors,
            rejected_queries=rejected,qualified_envelope=dict(naca=spec['naca'],mach=0.,
                reynolds=[spec['reynolds'][0],spec['reynolds'][-1]],sampled_reynolds=spec['reynolds'],
                alpha_deg=[spec['alpha_deg'][0],spec['alpha_deg'][-1]],transition=spec['transition'],
                limitations=['Numerical XFOIL section window, not physical validation or a stall boundary.',
                    'No installed flow, downwash, family or manufacturing admission.',
                    'Wider failed polars remain failed; no row splicing or production fallback.']))
    except (ValueError,OSError) as exc:
        report['failure']=dict(type=type(exc).__name__,reason=str(exc)[:600])
        if isinstance(exc,NativeFailure): report['failure']['native_evidence']=exc.evidence
    report['artifacts']=_inventory(root,exclude=('section-qualification.json',))
    report['evidence_sha256']=digest(report)
    if not replay: (root/'section-qualification.json').write_text(canonical_json(report))
    return report


def validate_section_reference(report,directory):
    if report.get('state') not in ('passed','failed'): raise ValueError('Missing section qualification state')
    actual=run_section_reference(directory,replay=True)
    if canonical_json(actual)!=canonical_json(report):
        raise ValueError('Section qualification differs from reconstructed native evidence')
