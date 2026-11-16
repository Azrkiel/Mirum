"""Independent aerodynamic contracts, conservation and adversarial admission."""
import copy
import math

import numpy as np
import pytest

from cem.core import digest
from cem.aero.contracts import InstalledGeometry, OperatingPoint, StructuralLoadCase
from cem.aero.fixtures import rectangular_fixture, analytic_bank


def op(alpha=4., speed=6.):
    return OperatingPoint(velocity_m_s=speed,alpha_deg=alpha,density_kg_m3=1.225,viscosity_pa_s=1.8e-5)


def test_contract_roundtrip_and_stale_geometry_rejected():
    g=rectangular_fixture()
    assert InstalledGeometry.model_validate_json(g.model_dump_json())==g
    raw=g.model_dump(mode='json'); raw['reference']['cg_m'][0]+=.01
    with pytest.raises(ValueError,match='revision'):
        InstalledGeometry.model_validate_json(__import__('json').dumps(raw))
    with pytest.raises(ValueError): op(float('nan'))


def test_polar_no_extrapolation_or_context_reuse():
    from cem.aero.polars import PolarBank
    g=rectangular_fixture();bank=analytic_bank(g)
    assert bank.coefficients('0012',100000.,2.)[0]==pytest.approx(2*math.pi*math.radians(2))
    for re,alpha in [(29999.,2.),(1000001.,2.),(50000.,13.),(float('nan'),2.)]:
        with pytest.raises(ValueError): bank.coefficients('0012',re,alpha)
    other=g.binding.model_copy(update={'mission_sha256':'a'*64})
    with pytest.raises(ValueError,match='Stale'): PolarBank(bank.polars,other)


def test_lifting_line_analytic_reference_refinement_and_load_closure():
    from cem.aero.lifting import solve
    g=rectangular_fixture();bank=analytic_bank(g);results=[]
    for resolution in (8,16,32):
        loads=solve(g,op(),bank,resolution);results.append(loads)
        q=.5*1.225*6**2;alpha=math.radians(4.)
        cl=(loads.force_n[2]*math.cos(alpha)-loads.force_n[0]*math.sin(alpha))/(q*.2)
        # Finite AR reduces lift from 2*pi*alpha; rectangular reference e~0.9.
        theory=2*math.pi*alpha/(1+2/(5*.9))
        assert cl==pytest.approx(theory,rel=.10)
        assert loads.force_closure_n<1e-12 and loads.moment_closure_nm<1e-12
        assert loads.induced_drag_n>0 and loads.profile_drag_n>0
        assert abs(loads.force_n[1])<1e-10
        assert abs(loads.moment_cg_nm[0])<1e-10
        assert all(s.effective_alpha_deg<4. for s in loads.strips)
        assert loads.circulation_residual<1e-6
    delta1=abs(results[1].force_n[2]-results[0].force_n[2])
    delta2=abs(results[2].force_n[2]-results[1].force_n[2])
    assert delta2<delta1
    case=StructuralLoadCase(name='fixture-only',binding=g.binding,aerodynamic_loads_sha256=digest(results[-1]),
                            load_factor=1.,loads=results[-1])
    assert case.loads==results[-1]
    with pytest.raises(ValueError):
        StructuralLoadCase(name='stale',binding=g.binding,aerodynamic_loads_sha256='0'*64,load_factor=1.,loads=results[-1])


def test_body_does_not_call_primary_aero_and_enforces_applicability(monkeypatch):
    from cem.aero.body import increments
    from cem.aero.contracts import BodyStation
    from gliders.model import analysis
    monkeypatch.setattr(analysis,'_aero',lambda *a,**k:pytest.fail('Primary aero reused'))
    g=rectangular_fixture()
    body=tuple(BodyStation(x_m=x,width_m=d,height_m=d,exponent=2.) for x,d in
               ((0.,.0002),(.1,.05),(.4,.05),(.5,.002)))
    # Trusted fixture construction: create the correct new geometry binding.
    raw=g.model_copy(update={'body':body})
    raw=raw.model_copy(update={'binding':g.binding.model_copy(update={'geometry_sha256':digest(raw.geometry_payload())})})
    value=increments(raw,op())
    assert value.drag_n>0 and value.drag_error_bound_n>0
    assert value.moment_cg_nm[1]>0
    refined=[]
    for a,b in zip(raw.body,raw.body[1:]):
        refined.extend((a,BodyStation(x_m=.5*(a.x_m+b.x_m),width_m=.5*(a.width_m+b.width_m),
            height_m=.5*(a.height_m+b.height_m),exponent=2.)))
    refined.append(raw.body[-1])
    fine=raw.model_copy(update={'body':tuple(refined)})
    fine=fine.model_copy(update={'binding':fine.binding.model_copy(update={'geometry_sha256':digest(fine.geometry_payload())})})
    check=increments(fine,op())
    assert check.force_n==pytest.approx(value.force_n,abs=1e-12)
    assert check.moment_cg_nm==pytest.approx(value.moment_cg_nm,abs=1e-12)
    with pytest.raises(ValueError,match='applicability'): increments(raw,op(9.))


def test_shared_structural_fixture_has_analytic_reactions_and_rejects_missing_support_parts():
    from cem.aero.fixtures import structural_fixture
    from cem.aero.contracts import InstalledStructure
    geometry,case,structure=structural_fixture()
    right=[s for s in case.loads.strips if s.center_m[1]>0]
    assert sum(s.force_n[2] for s in right)==pytest.approx(.5)
    assert sum(s.moment_cg_nm[0] for s in right)==pytest.approx(.125)
    raw=structure.model_dump_json()
    assert InstalledStructure.model_validate_json(raw)==structure
    value=__import__('json').loads(raw);value['materials']=value['materials'][:1]
    with pytest.raises(ValueError,match='missing installed material'):
        InstalledStructure.model_validate_json(__import__('json').dumps(value))


def test_gain_requires_two_arms_and_strict_error_exceedance():
    from cem.aero.performance import gain
    def arm(value,error=2.):
        return dict(state='passed',performance={'objective_value':value},error={'absolute_bound':error})
    assert not gain([arm(100.),arm(104.)])['verified_gain']
    assert gain([arm(100.),arm(104.01)])['verified_gain']
    assert not gain([arm(100.),dict(state='failed')])['verified_gain']
    assert not gain([dict(state='diagnostics_passed'),arm(110.)])['verified_gain']
    with pytest.raises(ValueError): gain([arm(float('nan')),arm(110.)])


def test_fixed_hardware_equilibrium_closes_inclined_glide_forces():
    from cem.aero.contracts import BodyStation
    from cem.aero.performance import equilibrium
    from cem.glider.requirements import GliderMissionRequirements
    from cem.glider.calibration import initial
    g=rectangular_fixture()
    wing=g.surfaces[0].model_copy(update={'sections':tuple(
        s.model_copy(update={'incidence_deg':2.}) for s in g.surfaces[0].sections)})
    body=tuple(BodyStation(x_m=x,width_m=d,height_m=d,exponent=2.) for x,d in
               ((0.,.0002),(.1,.05),(.4,.05),(.5,.002)))
    g=g.model_copy(update={'surfaces':(wing,),'body':body})
    g=g.model_copy(update={'binding':g.binding.model_copy(update={'geometry_sha256':digest(g.geometry_payload())})})
    before=g.model_dump_json();bank=analytic_bank(g)
    req=GliderMissionRequirements.model_validate({'launch':{'method':'drop'}})
    result=equilibrium(g,bank,req,initial(),dict(glide_speed_m_s=7.,alpha_trim_deg=.1,glide_angle_deg=5.))
    assert g.model_dump_json()==before
    assert max(abs(x) for x in result['equilibrium_residual'])<1e-4
    gamma=math.radians(result['glide_angle_deg']);weight=g.reference.mass_kg*9.81
    assert result['lift_n']==pytest.approx(weight*math.cos(gamma),rel=1e-4)
    assert result['drag_n']==pytest.approx(weight*math.sin(gamma),rel=1e-4)
    assert abs(result['moment_nm'])<1e-5
    assert result['trajectory']['distance_m']>0


def test_native_sigfpe_is_runtime_failure_and_keeps_input_log(tmp_path,monkeypatch):
    import sys
    from cem.aero.native import run,NativeFailure
    tool=tmp_path/'xfoil-crash'
    tool.write_text('#!'+sys.executable+'\nimport os, signal\nos.kill(os.getpid(), signal.SIGFPE)\n')
    tool.chmod(0o755);monkeypatch.setenv('MIRUM_XFOIL_BIN',str(tool))
    with pytest.raises(NativeFailure) as caught:
        run('xfoil','NACA 2412\n',tmp_path/'run','case',('polar.out',))
    evidence=caught.value.evidence
    assert evidence['state']=='runtime_sigfpe'
    assert {a['file'] for a in evidence['artifacts']}=={'case.in','case.log'}
    assert len(evidence['executable_sha256'])==64


def test_independent_admission_rescreens_mass_constraints():
    from cem.aero.performance import equilibrium
    from cem.glider.requirements import GliderMissionRequirements
    from cem.glider.calibration import initial
    geometry=rectangular_fixture()
    req=GliderMissionRequirements.model_validate({'launch':{'method':'drop'},'constraints':{'max_mass_kg':.05}})
    with pytest.raises(ValueError,match='mass/dimension'):
        equilibrium(geometry,analytic_bank(geometry),req,initial(),{})


def test_native_timeout_and_provider_environment_are_bounded(tmp_path,monkeypatch):
    import sys
    from cem.aero import native
    tool=tmp_path/'environment-check'
    tool.write_text('#!'+sys.executable+'''\nimport os,time
assert 'GEMINI_API_KEY_HEAVY' not in os.environ
print('no provider credential',flush=True)
time.sleep(5)
''')
    tool.chmod(0o755);monkeypatch.setenv('MIRUM_AVL_BIN',str(tool))
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY','test-value');monkeypatch.setattr(native,'TIMEOUT_S',.1)
    with pytest.raises(native.NativeFailure) as caught: native.run('avl','',tmp_path/'run','case')
    assert caught.value.evidence['state']=='timeout'
    assert 'no provider credential' in (tmp_path/'run/case.log').read_text()


def test_parent_rejects_fabricated_gain_stale_context_and_changed_raw(tmp_path,monkeypatch):
    from cem.aero.verification import verify_pair,validate
    from backend.tests.test_cem_comparison import shaped_example,elite_for
    from cem.glider.comparison import comparison_pair
    from cem.glider.calibration import initial
    req,_,gl=shaped_example();calibration=initial();pair=comparison_pair(req,calibration,elite_for(gl))
    monkeypatch.setenv('MIRUM_XFOIL_BIN','/missing/xfoil');monkeypatch.setenv('MIRUM_AVL_BIN','/missing/avl')
    arms=[dict(result=dict(valid=False))]*2
    value=verify_pair(pair,arms,req,calibration,'sheet_foam',tmp_path)
    assert value['state']=='inconclusive' and not value['verified_gain']
    assert not validate(value,pair,arms,req,calibration,'sheet_foam',tmp_path,True)
    bad=copy.deepcopy(value);bad['verified_gain']=True
    bad['evidence_sha256']=digest({k:v for k,v in bad.items() if k!='evidence_sha256'})
    with pytest.raises(ValueError,match='arithmetic'): validate(bad,pair,arms,req,calibration,'sheet_foam',tmp_path,True)
    bad=copy.deepcopy(value);bad['bindings'][0]['mission_sha256']='a'*64
    with pytest.raises(ValueError,match='binding'): validate(bad,pair,arms,req,calibration,'sheet_foam',tmp_path,True)
    (tmp_path/'independent/injected.out').write_text('changed')
    with pytest.raises(ValueError,match='inventory'): validate(value,pair,arms,req,calibration,'sheet_foam',tmp_path,True)


def test_stability_diagnostics_cannot_skip_spiral_rejection(monkeypatch):
    from cem.aero import avl
    from cem.aero.contracts import BodyStation
    from cem.glider.calibration import initial
    from backend.tests.test_cem_comparison import shaped_example,elite_for
    from cem.glider.comparison import comparison_pair
    from cem.aero.contracts import installed_geometry
    req,_,gl=shaped_example();gl=comparison_pair(req,initial(),elite_for(gl))[1]
    geometry=installed_geometry(gl,req,initial(),'sheet_foam');ref=geometry.reference;point=op()
    q=.5*point.density_kg_m3*point.velocity_m_s**2
    fields=dict(CLtot=.6,CDind=.02,Cmtot=0.,e=.9)
    derivatives=dict(CLa=5.,Cma=-.7,CYb=-.4,Clb=-.1,Cnb=.1,CYp=0.,Clp=-.5,Cnp=-.02,
                     CYr=.2,Clr=.1,Cnr=-.2,Cmq=-4.)
    native=dict(coefficients=fields,derivatives=derivatives)
    alpha=math.radians(point.alpha_deg)
    lift=q*ref.area_m2*.6
    performance=dict(alpha_deg=point.alpha_deg,glide_angle_deg=4.,loads=dict(
        operating_point=point.model_dump(mode='json'),force_n=[-lift*math.sin(alpha),0.,lift*math.cos(alpha)],
        moment_cg_nm=[0.,0.,0.],induced_drag_n=q*ref.area_m2*.02))
    def modes(*args):
        return dict(roll_real=-1.,dutch_roll_real=-.2,dutch_roll_zeta=.3,spiral_time_to_double_s=100.)
    monkeypatch.setattr(avl,'lateral_modes',modes)
    assert avl.admission(native,native,geometry,performance,gl.cfg['limits'])['state']=='passed'
    def unstable(*args): return dict(modes(),spiral_time_to_double_s=.1)
    monkeypatch.setattr(avl,'lateral_modes',unstable)
    with pytest.raises(ValueError,match='spiral divergence'):
        avl.admission(native,native,geometry,performance,gl.cfg['limits'])


def test_native_reference_cases_and_parent_raw_reparse(tmp_path):
    from cem.aero.native import resolve,NativeFailure
    try:
        resolve('xfoil');resolve('avl')
    except NativeFailure:
        pytest.skip('Native XFOIL and AVL executables not selected')
    from cem.aero.verification import native_references
    from cem.core import canonical_json
    result=native_references(tmp_path)
    assert result['state']=='passed'
    assert canonical_json(native_references(tmp_path,replay=True))==canonical_json(result)
    commands=next((tmp_path/'xfoil_160').glob('*.in'))
    commands.write_text(commands.read_text().replace('N 160','N 240'))
    with pytest.raises(ValueError,match='commands differ'):
        native_references(tmp_path,replay=True)
