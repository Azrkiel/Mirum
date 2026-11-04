"""Reproducible native whole-aircraft references and unstable countercases.

This checks separately declared rigid numerical benchmarks. It enables no new
family, manufacturing claim, flight validation or performance-gain claim.
"""
import json
from pathlib import Path

import numpy as np

from cem.core import canonical_json, digest
from cem.evidence import file_digest
from cem.provenance import provenance
from cem.glider.requirements import GliderMissionRequirements
from . import avl
from .contracts import InstalledGeometry, OperatingPoint, Transition
from .lifting import solve
from .body import increments
from .native import MAX_FILE, NativeFailure, resolve
from .performance import equilibrium, numerical_error
from .polars import PolarBank, query

SPEC=Path(__file__).resolve().parents[1]/'data/aero-conventional-reference-v1.json'
REFERENCE_FILES=dict(conventional=SPEC.name,t_tail='aero-t-tail-reference-v1.json',
                     v_tail='aero-v-tail-reference-v1.json')


def reference_inputs(*, unstable=False,case='conventional'):
    if case not in REFERENCE_FILES:
        raise ValueError('Unsupported native reference case')
    path=SPEC if case=='conventional' else SPEC.parent/REFERENCE_FILES[case]
    spec=json.loads(path.read_text())
    if spec['format']!='mirum-aero-qualification-1':
        raise ValueError('Unsupported native reference format')
    if spec['geometry']['family']!=case:
        raise ValueError('Native reference case/family identity mismatch')
    req=GliderMissionRequirements.model_validate(spec['requirements'])
    calibration=spec['calibration'];geometry=json.loads(canonical_json(spec['geometry']))
    hardware=json.loads(canonical_json(spec['hardware']))
    if unstable:
        geometry['reference']['cg_m']=spec['rejected_case']['cg_m']
        hardware['cg_m']=spec['rejected_case']['cg_m']
    for field in ('mass_kg','cg_m','inertia_kg_m2'):
        if hardware[field]!=geometry['reference'][field]:
            raise ValueError('Reference hardware/mass properties differ from geometry')
    binding=dict(geometry_sha256=digest(geometry),hardware_sha256=digest(hardware),mission_sha256=digest(req),
        process_sha256=digest(dict(process=req.process,construction=req.construction,scope=spec['scope'])),
        calibration_sha256=digest(calibration),implementation_sha256=provenance()['implementation_sha256'])
    installed=InstalledGeometry.model_validate_json(canonical_json(dict(geometry,binding=binding)))
    return spec,installed,req,calibration


def _bank(spec,geometry,folder,panels=160,replay=False):
    grid=spec['polar_grid'];transition=Transition(**grid['transition'])
    rows=[query(geometry.binding,grid['naca'],reynolds,grid['alpha_deg'],folder,
        transition,panels,replay=replay) for reynolds in grid['reynolds']]
    return PolarBank(rows,geometry.binding)


def _inventory(root,exclude=('qualification.json',)):
    if root.is_symlink(): raise ValueError('Symlink qualification directory')
    result=[]
    for path in sorted(root.rglob('*')):
        if path.is_symlink(): raise ValueError('Symlink native qualification evidence')
        if path.is_file() and path.relative_to(root).as_posix() not in exclude:
            result.append(dict(file=path.relative_to(root).as_posix(),sha256=file_digest(path,MAX_FILE)))
    if len(result)>150: raise ValueError('Native reference exceeds artifact budget')
    return result


def run_reference(directory, *, replay=False,case='conventional'):
    spec,geometry,req,calibration=reference_inputs(case=case)
    root=Path(directory)
    if root.exists() and any(root.iterdir()) and not replay:
        raise ValueError('Use an empty directory to preserve previous qualification evidence')
    root.mkdir(parents=True,exist_ok=True)
    report=dict(kind='native_aerodynamic_qualification',case_id=spec['case_id'],scope=spec['scope'],
        input_sha256=digest(spec),binding=geometry.binding.model_dump(mode='json'),
        installed_geometry=geometry.model_dump(mode='json'),requirements=req.model_dump(mode='json'),
        calibration=calibration,limits=spec['limits'],state='failed',executables={},verified_gain=False,
        engineering_certified=False,accepted_for_manufacture=False,families_enabled=[])
    try:
        for name in ('xfoil','avl'):
            _,binary=resolve(name);report['executables'][name]=binary
        source=root/'reference-input.json'
        if replay:
            if source.read_text()!=canonical_json(spec):
                raise ValueError('Native reference input changed')
        else:
            source.write_text(canonical_json(spec))
        bank=_bank(spec,geometry,root/'polars',160,replay)
        refined=_bank(spec,geometry,root/'polars_refined',240,replay)
        differences={}
        for key in ('cl','cd','cm'):
            differences[key]=max(float(np.max(abs(np.array(getattr(a,key))-getattr(b,key))))
                for a,b in zip(bank.polars,refined.polars))
        if differences['cl']>.025 or differences['cd']>.001 or differences['cm']>.005:
            raise ValueError('Whole-aircraft reference polar panel convergence failed')
        report['polar_convergence']=differences
        report['polars']=[p.model_dump(mode='json') for p in bank.polars]
        report['refined_polars']=[p.model_dump(mode='json') for p in refined.polars]
        before=canonical_json(geometry)
        coarse=equilibrium(geometry,bank,req,calibration,spec['initial'],12)
        fine=equilibrium(geometry,bank,req,calibration,spec['initial'],24)
        if canonical_json(geometry)!=before:
            raise ValueError('Qualification equilibrium changed frozen installed geometry')
        report.update(coarse_performance=coarse,performance=fine,error=numerical_error(coarse,fine,req.objective))
        op=OperatingPoint.model_validate_json(canonical_json(fine['loads']['operating_point']))
        native=[avl.analyze(geometry,op,root/name,*mesh,replay=replay)
            for name,mesh in zip(('avl_low','avl_high'),avl.QUALIFICATION_MESHES)]
        report['avl']=native
        report['stability']=avl.admission(*native,geometry,fine,spec['limits'])

        # Isolate the CG stability effect at the positive case's operating point.
        # No zero-moment trim or unstable equilibrium is claimed for this case.
        _,negative,_,_=reference_inputs(unstable=True,case=case)
        negative_bank=_bank(spec,negative,root/'unstable/polars',160,replay)
        loads=solve(negative,op,negative_bank,24)
        body=increments(negative,op)
        negative_native=[avl.analyze(negative,op,root/'unstable'/name,*mesh,replay=replay)
            for name,mesh in zip(('avl_low','avl_high'),avl.QUALIFICATION_MESHES)]
        at_point=dict(alpha_deg=op.alpha_deg,glide_angle_deg=fine['glide_angle_deg'],
                      loads=loads.model_dump(mode='json'))
        try:
            avl.admission(*negative_native,negative,at_point,spec['limits'])
        except ValueError as exc:
            if spec['rejected_case']['expected_failure'] not in str(exc):
                raise ValueError('Native negative case failed for an unexpected reason: '+str(exc)) from exc
            rejection=str(exc)
        else:
            raise ValueError('Native unstable reference was incorrectly admitted')
        report['unstable_case']=dict(case_id=spec['rejected_case']['case_id'],state='rejected_as_expected',
            scope='Fixed operating-point pitch instability; no trim or flight equilibrium claimed.',
            installed_geometry=negative.model_dump(mode='json'),loads=loads.model_dump(mode='json'),
            body=body.model_dump(mode='json'),avl=negative_native,reason=rejection)
        report['qualified_envelope']=dict(case_id=spec['case_id'],geometry_sha256=geometry.binding.geometry_sha256,
            naca=spec['polar_grid']['naca'],reynolds=spec['polar_grid']['reynolds'],
            alpha_deg=[spec['polar_grid']['alpha_deg'][0],spec['polar_grid']['alpha_deg'][-1]],
            transition=spec['polar_grid']['transition'],rigid_only=True,
            limitations=['Specific benchmark, not an entire family or geometry envelope.',
                'No junction/stall/flexible-wing/physical-material qualification.',
                'Body and error allowances remain engineering models, not flight calibration.'])
        report['state']='passed'
    except (ValueError,OSError) as exc:
        if replay: raise
        report['failure']=dict(type=type(exc).__name__,reason=str(exc)[:600])
        if isinstance(exc,NativeFailure): report['failure']['native_evidence']=exc.evidence
    report['artifacts']=_inventory(root)
    report['evidence_sha256']=digest(report)
    if not replay:
        (root/'qualification.json').write_text(canonical_json(report))
    return report


def validate_reference(report,directory):
    if report.get('state')!='passed':
        raise ValueError('Missing successful native whole-aircraft qualification')
    family=report.get('installed_geometry',{}).get('family')
    actual=run_reference(directory,replay=True,case=family)
    if canonical_json(report)!=canonical_json(actual):
        raise ValueError('Native qualification differs from reconstructed raw evidence')
