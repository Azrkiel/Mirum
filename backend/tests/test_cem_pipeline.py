import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random

import numpy as np
import pytest

pytest.importorskip('trimesh')
pytest.importorskip('aerosandbox')

from cem.core import canonical_json
from cem.glider.requirements import GliderMissionRequirements as Mission
from cem.glider.calibration import initial
from cem.glider.model import configuration, as_sized, evaluate_candidate
from cem.glider.geometry import export_bundle, measure_part
from cem.glider.closure import verify
from cem.glider.uncertainty import uncertainty
from cem.glider.physics import launch_state
from cem.glider.service import build_design
from gliders.design_spec import preset, placement


def example():
    req = Mission.model_validate(dict(launch={'method':'drop'}, process='sheet_foam', reference_target=1200.,
        constraints={'payload':{'mass_kg':.06,'diameter_m':.045,'length_m':.06}}, discovery={'budget_evals':8}))
    request,cfg = configuration(req, initial(),'sheet_foam')
    gl = preset(request).build(); gl.cfg=cfg
    gl.wing.root_chord, gl.wing.taper, gl.wing.span = .18, .75, 1.25
    gl.tail.h_root_chord, gl.tail.h_span = .10, .30
    gl.tail.v_root_chord, gl.tail.v_height, gl.tail.v_taper = .11,.12,.9
    return req,request,as_sized(gl)


@pytest.fixture(scope='module')
def evaluated():
    req,request,gl = example()
    result = evaluate_candidate(gl,request,req,initial(),'sheet_foam')
    assert result['valid'],result['fail_reasons']
    return req,request,gl,result


def test_closed_export_has_positive_mass_and_artifact_identity(tmp_path,evaluated):
    req,request,gl,result=evaluated
    bundle=export_bundle(gl,tmp_path,'0123456789ab')
    for item in bundle['parts']:
        path=tmp_path/item['file']
        measured=measure_part(path,item['material'])
        assert measured['mass_kg']>0 and measured['volume_m3']>0
        assert np.linalg.eigvalsh(measured['inertia_kg_m2']).min()>0
        assert measured['sha256']==item['sha256']
        assert b'0123456789ab' in path.read_bytes()[:80]
    assert bundle['manufacturing_ready'] is False


def test_twenty_percent_mass_error_fails_closure(tmp_path,evaluated):
    req,request,gl,result=evaluated
    bundle=export_bundle(gl,tmp_path,'0123456789ab')
    wrong=copy.deepcopy(result);wrong['mass_kg']*=1.20
    report=verify(gl,wrong,bundle,tmp_path,request,req,initial(),'sheet_foam')
    assert report['state']=='failed'
    assert any('mass' in failure for failure in report['failures'])


def test_mutated_mesh_cannot_reuse_previous_report(tmp_path,evaluated):
    req,request,gl,result=evaluated
    bundle=export_bundle(gl,tmp_path,'0123456789ab')
    path=tmp_path/bundle['parts'][0]['file']
    data=bytearray(path.read_bytes());data[2]=65;path.write_bytes(data)
    report=verify(gl,result,bundle,tmp_path,request,req,initial(),'sheet_foam')
    assert report['state']=='failed'


def test_seeded_bands_include_launch_and_build_uncertainty(evaluated):
    req,request,gl,result=evaluated
    args=(req,result,initial(),'sheet_foam',launch_state(req.launch,initial()))
    a,b=uncertainty(*args),uncertainty(*args)
    assert canonical_json(a)==canonical_json(b)
    for band in a['bands'].values():
        assert 0<=band['p10']<=band['p50']<=band['p90']
        assert band['p10']<band['p90']


def test_design_json_and_stl_are_repeatable_and_never_claim_manufacturing_acceptance(tmp_path,evaluated):
    req,request,gl,result=evaluated
    elite=dict(elite_id='1234567890abcdef',process='sheet_foam',result=result,placement=placement(gl),
        parameters={'wing':asdict(gl.wing),'fuselage':asdict(gl.fuse),'tail':asdict(gl.tail)})
    a=build_design(req,initial(),elite,tmp_path/'a')
    b=build_design(req,initial(),elite,tmp_path/'b')
    assert (tmp_path/'a/design.json').read_bytes()==(tmp_path/'b/design.json').read_bytes()
    for path in (tmp_path/'a').glob('*.stl'):
        assert path.read_bytes()==(tmp_path/'b'/path.name).read_bytes()
    assert a['accepted_for_manufacture'] is False and a['engineering_certified'] is False
    assert a['closure']['manufacturing_ready'] is False
    # This fixture retains the original straight stock rod under raised,
    # swept wing panels. Aerodynamic screening is not installed-spar evidence.
    assert a['status'] == 'rejected'
    assert a['closure']['spar_enclosure']['state'] == 'failed'
    assert any('spar' in reason for reason in a['closure']['failures'])
    assert (tmp_path/'a/performance_report.json').exists()
    from cem.evidence import validate
    from cem.glider.service import identity
    from gliders.search import finite_json
    payload=dict(kind='design', requirements=req.model_dump(mode='json'), calibration=initial(), elite=finite_json(elite))
    # Parent validation receives decoded JSON, not NumPy scalars from in-process FEA.
    validate(json.loads(canonical_json(a)),payload,identity(req,initial()),tmp_path/'a')
    bad=copy.deepcopy(a);bad['uncertainty']=None;bad['status']='screened'
    with pytest.raises((ValueError,TypeError,KeyError)):
        validate(bad,payload,identity(req,initial()),tmp_path/'a')


def test_tailwind_speed_to_fly_uses_ground_objective(evaluated):
    req,request,gl,result=evaluated
    # Same mass/layout, new environment, independently re-trimmed by actual solver.
    wind_req=Mission.model_validate(dict(req.model_dump(),environment={'kind':'wind','wind_m_s':3.}))
    _,cfg=configuration(wind_req,initial(),'sheet_foam')
    windy=copy.deepcopy(gl);windy.cfg=cfg
    wind=evaluate_candidate(windy,request,wind_req,initial(),'sheet_foam')
    assert wind['valid'],wind['fail_reasons']
    assert wind['trajectory']['distance_m']>result['trajectory']['distance_m']
    assert wind['glide_speed_m_s']<=result['glide_speed_m_s']


def test_duration_objective_remains_valid_in_a_strong_headwind(evaluated):
    req,request,gl,result=evaluated
    values=[]
    for env in ({'kind':'still_air'},{'kind':'wind','wind_m_s':-10.}):
        mission=Mission.model_validate(dict(req.model_dump(),objective='duration_s',environment=env))
        limits,cfg=configuration(mission,initial(),'sheet_foam')
        current=copy.deepcopy(gl);current.cfg=cfg
        value=evaluate_candidate(current,limits,mission,initial(),'sheet_foam')
        assert value['valid'],value['fail_reasons']
        values.append(value['trajectory']['duration_s'])
    assert values[0] == pytest.approx(values[1],rel=1e-6)
