"""Revision-bound paired orchestration and parent reconstruction from raw evidence."""
import json
from pathlib import Path
import re

import numpy as np

from cem.core import canonical_json, digest
from cem.evidence import file_digest
from cem.glider.physics import atmosphere
from . import CONTRACT_VERSION
from .contracts import installed_geometry, OperatingPoint, Transition
from .native import NativeFailure, resolve, MAX_FILE
from .polars import query, PolarBank
from .performance import equilibrium, numerical_error, gain
from . import avl

SCOPE='Independent rigid attached-flow lifting-line/XFOIL plus slender-body model, cross-checked with AVL; no flight validation.'


def native_references(folder, replay=False,*,family='conventional'):
    """Native numerical references, not mocks or a declaration of installed tools."""
    from .fixtures import rectangular_fixture
    geometry=rectangular_fixture()
    angles=[float(x) for x in range(-2,7)]
    polars=[query(geometry.binding,'0012',100000.,angles,folder/f'xfoil_{n}',panels=n,replay=replay)
            for n in (160,240)]
    p,q=polars
    differences={key:float(max(abs(np.array(getattr(p,key))-getattr(q,key)))) for key in ('cl','cd','cm')}
    if differences['cl']>.025 or differences['cd']>.001 or differences['cm']>.005:
        raise ValueError('Native XFOIL panel convergence failed')
    atzero=p.alpha_deg.index(0.)
    if abs(p.cl[atzero])>.01 or abs(p.cm[atzero])>.005:
        raise ValueError('Native XFOIL symmetric reference failed')
    op=OperatingPoint(velocity_m_s=6.,alpha_deg=4.,density_kg_m3=1.225,viscosity_pa_s=1.8e-5)
    low=avl.analyze(geometry,op,folder/'avl_low',16,8,replay=replay)
    high=avl.analyze(geometry,op,folder/'avl_high',32,12,replay=replay)
    if abs(low['coefficients']['CLtot']-high['coefficients']['CLtot'])>.005:
        raise ValueError('Native AVL rectangular reference failed mesh refinement')
    # Finite-wing analytic reference: approximate rectangular efficiency 0.9.
    expected=2*np.pi*np.radians(4)/(1+2/(5*.9))
    if abs(high['coefficients']['CLtot']/expected-1)>.10:
        raise ValueError('Native AVL finite-wing reference failed')
    if abs(high['coefficients']['Cmtot'])>.005:
        raise ValueError('Native AVL quarter-chord moment reference failed')
    from .qualification import run_reference
    whole=run_reference(folder/'whole_aircraft',replay=replay,case=family)
    if whole['state']!='passed':
        raise ValueError('Native whole-aircraft reference failed: '+whole.get('failure',{}).get('reason','missing evidence'))
    return dict(state='passed',xfoil_panel_differences=differences,
        reference_family=family,
        xfoil=[p.model_dump(mode='json') for p in polars],avl=[low,high],
        whole_aircraft_reference=whole,
        tolerances=dict(xfoil_cl=.025,xfoil_cd=.001,xfoil_cm=.005,avl_cl_mesh=.005,avl_reference_relative=.10))


def bank_for(geometry, initial, req, folder, replay=False):
    air=atmosphere(req.environment); rows=[]
    for naca in sorted({s.naca for s in geometry.surfaces}):
        chords=[c.chord_m for s in geometry.surfaces if s.naca==naca for c in s.sections]
        scale=air['density_kg_m3']*initial['glide_speed_m_s']/air['dynamic_viscosity_pa_s']
        lo=max(30000.,min(chords)*scale*.75)
        hi=min(1000000.,max(chords)*scale*1.4)
        if hi<=lo: raise ValueError('No supported station Reynolds envelope')
        reynolds=np.geomspace(lo,hi,5)
        angles=[float(i)/2 for i in range(-8,17)]
        for re_value in reynolds:
            rows.append(query(geometry.binding,naca,float(re_value),angles,folder,replay=replay))
    return PolarBank(rows,geometry.binding)


def arm(geometry, gl, primary, req, calibration, folder, replay=False):
    bank=bank_for(geometry,primary,req,folder/'polars',replay)
    coarse=equilibrium(geometry,bank,req,calibration,primary,12)
    fine=equilibrium(geometry,bank,req,calibration,primary,24)
    error=numerical_error(coarse,fine,req.objective)
    op=OperatingPoint.model_validate_json(canonical_json(fine['loads']['operating_point']))
    low=avl.analyze(geometry,op,folder/'avl_low',*avl.QUALIFICATION_MESHES[0],replay=replay)
    high=avl.analyze(geometry,op,folder/'avl_high',*avl.QUALIFICATION_MESHES[1],replay=replay)
    stability=avl.admission(low,high,geometry,fine,gl.cfg['limits'])
    return dict(state='passed',binding=geometry.binding.model_dump(mode='json'),
        installed_geometry=geometry.model_dump(mode='json'),
        polar_bank=[p.model_dump(mode='json') for p in bank.polars],
        coarse_performance=coarse,performance=fine,error=error,avl=[low,high],stability=stability)


def inventory(directory):
    result=[]
    if directory.is_symlink(): raise ValueError('Symlink independent evidence directory')
    for p in sorted(directory.rglob('*')):
        if p.is_symlink(): raise ValueError('Symlink independent raw artifact')
        if p.is_file():
            result.append(dict(file=p.relative_to(directory).as_posix(),sha256=file_digest(p,MAX_FILE)))
    if len(result)>300: raise ValueError('Independent artifact count exceeds budget')
    return result


def verify_pair(gliders, primary_arms, req, calibration, process, directory):
    root=directory/'independent';root.mkdir(parents=True,exist_ok=True)
    geometries=[installed_geometry(gl,req,calibration,process) for gl in gliders]
    if len(geometries)!=2 or geometries[0].family!=geometries[1].family:
        raise ValueError('Paired independent reference family mismatch')
    report=dict(contract_version=CONTRACT_VERSION,scope=SCOPE,objective=req.objective,
        transition=Transition().model_dump(mode='json'),bindings=[g.binding.model_dump(mode='json') for g in geometries],
        executables={},references=dict(state='not_evaluated'),arms=[])
    try:
        for name in ('xfoil','avl'):
            _,sha=resolve(name);report['executables'][name]=sha
        if not all(a['result'].get('valid') for a in primary_arms):
            raise ValueError('Both frozen-hardware primary arms must pass admission')
        report['references']=native_references(root/'references',family=geometries[0].family)
    except (ValueError,OSError) as exc:
        report['references']=dict(state='failed',reason=str(exc)[:400])
        if isinstance(exc,NativeFailure): report['references']['native_failure']=exc.evidence
    for g,gl,primary,label in zip(geometries,gliders,primary_arms,('baseline','shaped')):
        if report['references']['state']!='passed':
            report['arms'].append(dict(state='not_evaluated',binding=g.binding.model_dump(mode='json'),
                reason='Native references or paired primary admission did not pass'))
            continue
        try:
            report['arms'].append(arm(g,gl,primary['result'],req,calibration,root/label))
        except (ValueError,OSError) as exc:
            failure=dict(state='failed',binding=g.binding.model_dump(mode='json'),reason=str(exc)[:400])
            if isinstance(exc,NativeFailure): failure['native_failure']=exc.evidence
            report['arms'].append(failure)
    report.update(gain(report['arms']))
    report['artifacts']=inventory(root)
    report['evidence_sha256']=digest({k:v for k,v in report.items() if k!='evidence_sha256'})
    return report


def validate(report, gliders, primary_arms, req, calibration, process, directory, requested):
    """Recompute all successful claims from bounded native raw files in the parent.

    Failed evidence cannot authorize a gain. Its identity and artifact manifest
    are still checked; no executable is rerun during admission.
    """
    if not isinstance(report,dict):
        raise ValueError('Missing independent performance evidence')
    if not requested:
        if report!={'state':'not_requested','verified_gain':False}:
            raise ValueError('Unexpected independent performance claim')
        return False
    geometry=[installed_geometry(gl,req,calibration,process) for gl in gliders]
    if (report.get('contract_version')!=CONTRACT_VERSION or report.get('objective')!=req.objective
            or report.get('scope')!=SCOPE or report.get('transition')!=Transition().model_dump(mode='json')
            or report.get('bindings')!=[g.binding.model_dump(mode='json') for g in geometry]
            or len(report.get('arms',[]))!=2):
        raise ValueError('Independent geometry/mission/process/calibration/implementation binding mismatch')
    # Reject nonfinite summaries even when a failed arm would suppress a gain.
    canonical_json(report)
    if report.get('evidence_sha256')!=digest({k:v for k,v in report.items() if k!='evidence_sha256'}):
        raise ValueError('Independent evidence digest mismatch')
    root=directory/'independent'
    if report.get('artifacts')!=inventory(root):
        raise ValueError('Independent raw artifact inventory changed')
    actual={}
    for name in ('xfoil','avl'):
        try: _,sha=resolve(name);actual[name]=sha
        except NativeFailure: continue
    # A missing first tool can stop resolution before the second; available
    # recorded tools must still exactly match the current binary identity.
    if any(actual.get(k)!=v for k,v in report.get('executables',{}).items()):
        raise ValueError('Independent native executable changed')
    refs=report['references']
    if refs.get('state')=='passed':
        if report.get('executables')!=actual or len(actual)!=2:
            raise ValueError('Missing independent native identities')
        expected=native_references(root/'references',replay=True,family=geometry[0].family)
        if canonical_json(refs)!=canonical_json(expected):
            raise ValueError('Native reference assessment differs from raw evidence')
    elif refs.get('state')!='failed':
        raise ValueError('Invalid native reference admission')
    for g,gl,primary,label,item in zip(geometry,gliders,primary_arms,('baseline','shaped'),report['arms']):
        if item.get('binding')!=g.binding.model_dump(mode='json'):
            raise ValueError('Independent arm revision mismatch')
        if item.get('state')=='passed':
            if refs.get('state')!='passed' or not all(a['result']['valid'] for a in primary_arms):
                raise ValueError('Independent gain lacks paired/native admission')
            expected=arm(g,gl,primary['result'],req,calibration,root/label,replay=True)
            if canonical_json(item)!=canonical_json(expected):
                raise ValueError('Independent performance differs from reconstructed raw evidence')
        elif item.get('state') not in ('not_evaluated','failed'):
            raise ValueError('Invalid independent performance state')
    summary=gain(report['arms'])
    if any(report.get(k)!=v for k,v in summary.items()):
        raise ValueError('Independent numerical gain arithmetic mismatch')
    return summary['verified_gain']
