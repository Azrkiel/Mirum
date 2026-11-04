from copy import deepcopy

import numpy as np
import pytest

from cem.core import digest
from cem.glider.families import archive,build
from cem.glider.families.installed import freeze,validate
from cem.glider.families.coordinate_polars import CoordinateBank,query,coordinates_payload
from cem.glider.families import coupled_aero as coupled


def fixture_bank(record,controls):
    """Analytical section fixtures check conservation, not physical validity."""
    foils={digest(coordinates_payload(s.airfoil.coordinates)):coordinates_payload(s.airfoil.coordinates)
        for wing in validate(freeze(record,controls)).wings for s in wing.xsecs}
    reports=[]
    for key,points in foils.items():
        for re in (30000.,1000000.):
            angles=[float(v) for v in range(-10,13,2)]
            reports.append(dict(state='complete',missing_angles_deg=[],coordinates=points,
                coordinates_sha256=key,reynolds=re,requested_angles_deg=angles,
                transition={},panels=160,rows=[dict(alpha_deg=a,cl=.1*a,cd=.01,cm=-.01) for a in angles]))
    return CoordinateBank(reports)


@pytest.mark.parametrize('family',['tailless_swept','canard','tandem'])
def test_coupled_fixture_closure_and_source_resolved_interactions(family):
    a=build(family);record=archive(a);controls={c.name:0. for c in a.controls}
    bank=fixture_bank(record,controls)
    report=coupled.solve(record,controls,bank,spanwise=2)
    assert report['whole_aircraft_drag_n'] is None
    assert report['body_drag_n'] is None
    assert report['production_admission'] is False
    assert report['qualified_aircraft_stall'] is False
    assert report['lifting_surface_drag_n']==pytest.approx(report['induced_drag_n']+report['profile_drag_n'])
    assert np.sum([s['force_n'] for s in report['strips']],axis=0)==pytest.approx(report['force_n'])
    assert np.sum([s['moment_cg_nm'] for s in report['strips']],axis=0)==pytest.approx(report['moment_cg_nm'])
    names={s.name for s in a.surfaces}
    for strip in report['strips']:
        assert set(strip['interaction_sources'])==names
        assert np.sum([v['induced_velocity_m_s'] for v in strip['interaction_sources'].values()],axis=0)==pytest.approx(strip['induced_velocity_m_s'])
    if len(names)>1:
        for source in names:
            targets=[s for s in report['strips'] if s['surface']!=source]
            assert max(np.linalg.norm(s['interaction_sources'][source]['induced_velocity_m_s']) for s in targets)>1e-5
    assert coupled.replay(report,bank) is False
    stability=coupled.static_derivatives(record,controls,bank,spanwise=2)
    assert stability['cl_alpha_per_rad']>0
    assert stability['static_margin']==pytest.approx(-stability['cm_alpha_per_rad']/stability['cl_alpha_per_rad'])
    assert stability['dynamic_stability_qualified'] is False
    assert stability['lower']['controls']==stability['upper']['controls']==controls
    assert stability['upper']['conditions']['initial_circulation_m2_s']==[
        s['circulation_m2_s'] for s in stability['lower']['strips']]
    bad=deepcopy(report);bad['qualified_aircraft_stall']=True
    with pytest.raises(ValueError,match='reconstruction'):
        coupled.replay(bad,bank)


def test_actual_coordinate_polars_drive_native_coupled_swept_case(tmp_path):
    a=build('tailless_swept',dict(wing=dict(root_chord_m=.24,taper=1.,incidence_deg=0.,
        washout_deg=0.,dihedral_deg=0.,sweep_deg=25.,airfoil='0012')))
    record=archive(a);controls={c.name:0. for c in a.controls}
    foil=validate(freeze(record,controls)).wings[0].xsecs[0].airfoil
    reports=[query(foil.coordinates,re,[-2.,0.,2.,4.,6.],tmp_path/str(i))
             for i,re in enumerate((100000.,300000.))]
    assert all(r['state']=='complete' for r in reports)
    bank=CoordinateBank(reports)
    coarse=coupled.solve(record,controls,bank,speed_m_s=10.,alpha_deg=3.,spanwise=2)
    fine=coupled.solve(record,controls,bank,speed_m_s=10.,alpha_deg=3.,spanwise=4)
    assert fine['profile_drag_n']>0
    assert fine['induced_drag_n']>0
    assert coupled.replay(fine,bank) is False
    assert coarse['force_n'][2]==pytest.approx(fine['force_n'][2],rel=.15)


def test_uncovered_local_conditions_reject_instead_of_extrapolating():
    a=build('canard');record=archive(a);controls={c.name:0. for c in a.controls}
    bank=fixture_bank(record,controls)
    with pytest.raises(ValueError,match='coverage'):
        coupled.solve(record,controls,bank,speed_m_s=1.,spanwise=2)


def test_native_canard_adjacent_alpha_continuation_stays_inside_real_polar_coverage(tmp_path):
    a=build('canard');record=archive(a);controls={c.name:0. for c in a.controls}
    foils={digest(coordinates_payload(s.airfoil.coordinates)):s.airfoil.coordinates
        for wing in validate(freeze(record,controls)).wings for s in wing.xsecs}
    reports=[]
    for i,coords in enumerate(foils.values()):
        reports.extend(query(coords,re,[-4.,-2.,0.,2.,4.,6.,8.],tmp_path/f'{i}-{j}')
            for j,re in enumerate((30000.,300000.)))
    bank=CoordinateBank(reports)
    with pytest.raises(ValueError,match='angle.*coverage'):
        coupled.solve(record,controls,bank,alpha_deg=4.25,spanwise=2)
    result=coupled.static_derivatives(record,controls,bank,alpha_deg=4.,spanwise=2)
    assert result['cl_alpha_per_rad']>0
    assert coupled.replay(result['upper'],bank) is False
