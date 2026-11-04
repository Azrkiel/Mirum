from copy import deepcopy

import aerosandbox as asb
import pytest

from cem.core import digest
from cem.glider.families import archive, build
from cem.glider.families.installed import airplane
from cem.glider.families.coordinate_polars import CoordinateBank, query, validate


def fixture_reports():
    """Analytic interpolation fixtures, never aircraft/native qualification."""
    points = asb.Airfoil('naca0012').coordinates.tolist()
    return [dict(state='complete', missing_angles_deg=[], coordinates=points,
        coordinates_sha256=digest(points), reynolds=re, requested_angles_deg=[-2.,0.,2.],
        transition={}, panels=160, rows=[dict(alpha_deg=a,cl=.1*a,cd=.01,cm=0.) for a in (-2.,0.,2.)])
        for re in (100000.,200000.)]


def test_coordinate_bank_rejects_gaps_extrapolation_and_postpeak():
    reports=fixture_reports()
    bank=CoordinateBank(reports)
    key=reports[0]['coordinates_sha256']
    assert bank.coefficients(key,150000.,1.) == pytest.approx((.1,.01,0.))
    for re,alpha in ((99999.,0.),(200001.,0.),(150000.,3.),(float('nan'),0.)):
        with pytest.raises(ValueError,match='coverage'):
            bank.coefficients(key,re,alpha)
    for mutate in ('missing','row','foil','slope'):
        bad=deepcopy(reports)
        if mutate=='missing': bad[0]['missing_angles_deg']=[0.]
        elif mutate=='row': bad[0]['rows'].pop(1)
        elif mutate=='foil': bad[0]['coordinates'][0][1] += .001
        else: bad[0]['rows'][2]['cl'] = -.1
        with pytest.raises(ValueError):
            CoordinateBank(bad).coefficients(key,150000.,1.)


def test_native_exact_coordinate_reference_and_parent_replay(tmp_path):
    points=asb.Airfoil('naca0012').coordinates
    report=query(points,1000000.,[0.,2.,4.],tmp_path/'polar')
    assert report['state']=='complete'
    assert report['rows'][0]['cd'] == pytest.approx(.006,abs=.002)
    assert report['rows'][2]['cl'] == pytest.approx(.44,abs=.05)
    assert validate(report,tmp_path/'polar') is False
    # Replacing scientific values and hashes cannot fabricate a native result.
    bad=deepcopy(report)
    bad['rows'][1]['cl'] += .1
    with pytest.raises(ValueError,match='reconstruction'):
        validate(bad,tmp_path/'polar')
    bad=deepcopy(report);bad['native']['artifacts'].pop()
    with pytest.raises(ValueError,match='artifacts'):
        validate(bad,tmp_path/'polar')
    with pytest.raises(ValueError,match='empty'):
        query(points,1000000.,[0.,2.,4.],tmp_path/'polar')


def test_native_reflex_and_deflected_coordinates_are_not_replaced_by_naca(tmp_path):
    a=build('tailless_swept')
    neutral={c.name:0. for c in a.controls}
    records=[]
    for i,states in enumerate((neutral,dict(neutral,elevon_pitch=3.))):
        foil=airplane(archive(a),states).wings[-1].xsecs[0].airfoil
        report=query(foil.coordinates,200000.,[-2.,0.,2.],tmp_path/str(i))
        assert report['state']=='complete'
        assert 'NACA' not in (tmp_path/str(i)/'polar.in').read_text()
        records.append(report)
    assert records[0]['coordinates_sha256']!=records[1]['coordinates_sha256']
    assert abs(records[0]['rows'][1]['cm']-records[1]['rows'][1]['cm'])>.005


def test_native_missing_rows_are_explicit_and_unusable(tmp_path, monkeypatch):
    from cem.glider.families import coordinate_polars as p
    def fake_native(name,commands,folder,stem,outputs):
        (folder/'polar.out').write_text('Calculated polar for: mirum_exact_coordinates\n Mach = 0.000 Re = 0.100 e 6 Ncrit = 9.000 9.000\n xtrf = 1.000 (top) 1.000 (bottom)\n -------\n -2.0 -.2 .01 .0 .0\n 2.0 .2 .01 .0 .0\n')
        return dict(artifacts=[])
    monkeypatch.setattr(p,'run',fake_native)
    report=p.query(asb.Airfoil('naca0012').coordinates,100000.,[-2.,0.,2.],tmp_path/'missing')
    assert report['state']=='incomplete'
    assert report['missing_angles_deg']==[0.]
    with pytest.raises(ValueError,match='Incomplete'):
        CoordinateBank([report])


@pytest.mark.parametrize('change', ['name', 're', 'mach', 'ncrit', 'trip', 'missing'])
def test_coordinate_native_metadata_rejects_wrong_reported_controls(change):
    from cem.aero.polars import check_polar_controls
    from cem.aero.contracts import Transition
    raw = ('Calculated polar for: mirum_exact_coordinates\n'
           'Mach = 0.000 Re = 0.100 e 6 Ncrit = 9.000 9.000\n'
           'xtrf = 1.000 (top) 1.000 (bottom)\n')
    assert check_polar_controls(raw, None, 100000., Transition(),
                               coordinate_name='mirum_exact_coordinates')['mach'] == 0
    replacements = dict(name=('mirum_exact_coordinates', 'NACA 0012'),
        re=('0.100', '0.200'), mach=('0.000', '0.100'), ncrit=('9.000', '8.000'),
        trip=('1.000 (top)', '0.500 (top)'), missing=('Ncrit', 'absent'))
    a, b = replacements[change]
    with pytest.raises(ValueError, match='operating'):
        check_polar_controls(raw.replace(a, b), None, 100000., Transition(),
                             coordinate_name='mirum_exact_coordinates')


def test_coordinate_coverage_errors_use_shared_solver_domain():
    from cem.aero.polars import PolarCoverageError
    bank = CoordinateBank(fixture_reports())
    for foil, re, alpha in [('absent', 150000., 0.),
                            (fixture_reports()[0]['coordinates_sha256'], 150000., 5.)]:
        with pytest.raises(PolarCoverageError):
            bank.coefficients(foil, re, alpha)


def test_coordinate_domain_drives_damped_solver_and_failure_diagnostics():
    from cem.aero.numerics import bounded_newton, NumericalConvergenceError
    bank = CoordinateBank(fixture_reports())
    key = fixture_reports()[0]['coordinates_sha256']
    def residual(x):
        bank.coefficients(key, 150000., float(x[0]))
        return [x[0]**3-1.]
    result = bounded_newton(residual, [.1])
    assert result['value'] == pytest.approx([1.])
    assert result['rejected_domain_trials'] > 0
    with pytest.raises(NumericalConvergenceError, match='Initial.*coverage'):
        bounded_newton(residual, [3.])
    def no_covered_root(x):
        bank.coefficients(key, 150000., float(x[0]))
        return [x[0]-3.]
    with pytest.raises(NumericalConvergenceError, match='covered'):
        bounded_newton(no_covered_root, [0.])
