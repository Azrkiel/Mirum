"""Shape-only controls must survive evaluation and report validation."""
import copy
from dataclasses import asdict

import pytest

from cem.core import digest
from cem.glider.calibration import initial
from cem.glider.requirements import GliderMissionRequirements as Mission
from gliders.design_spec import placement


def shaped_example():
    from backend.tests.test_cem_pipeline import example
    from cem.glider.shapes import ShapedWing
    req, request, gl = example()
    value = req.model_dump(mode='json')
    value['discovery']['wing_shape'] = 'multi_station'
    req = Mission.model_validate(value)
    gl.wing = ShapedWing(**asdict(gl.wing), chord_factors=(1., 1.02, .99, 1.01, 1.))
    return req, request, gl


def elite_for(gl):
    value = dict(process='sheet_foam', parameters=dict(wing=asdict(gl.wing),
                 fuselage=asdict(gl.fuse), tail=asdict(gl.tail)), placement=placement(gl))
    return dict(value, elite_id=digest(value)[:16])


def test_pair_changes_only_factors_and_uses_identical_stations():
    from cem.glider.comparison import comparison_pair
    req, _, gl = shaped_example()
    elite = elite_for(gl)
    before = copy.deepcopy(elite)
    a, b = comparison_pair(req, initial(), elite)
    assert elite == before
    for key in ('fuse', 'tail', 'cfg', 'ballast_kg', 'tail_incidence_deg', 'payload_x', 'wing_x'):
        assert getattr(a, key) == getattr(b, key)
    assert a.wing.chord_factors == (1.,)*5
    assert b.wing.chord_factors == gl.wing.chord_factors
    aa, bb = asdict(a.wing), asdict(b.wing)
    aa.pop('chord_factors'); bb.pop('chord_factors')
    assert aa == bb
    assert [s.y for s in a.wing.surface().sections] == [s.y for s in b.wing.surface().sections]
    assert a.cfg is not b.cfg
    assert a.spar()['diameter_m'] == b.spar()['diameter_m']
    assert a.spar()['mass_kg'] == b.spar()['mass_kg']


def test_pair_rejects_stale_or_template_elite():
    from cem.glider.comparison import comparison_pair
    req, _, gl = shaped_example()
    elite = elite_for(gl)
    elite['parameters']['tail']['kind'] = 'V'
    with pytest.raises(ValueError, match='revision'):
        comparison_pair(req, initial(), elite)
    gl.wing = __import__('cem.glider.shapes', fromlist=['wing_from_parameters']).wing_from_parameters(
        {k:v for k,v in asdict(gl.wing).items() if k != 'chord_factors'})
    with pytest.raises(ValueError, match='shape'):
        comparison_pair(req, initial(), elite_for(gl))


def test_real_fixed_geometry_evaluation_never_rebalances_or_retrims_hardware(monkeypatch):
    from cem.glider.model import evaluate_candidate
    from gliders.model import analysis
    req, request, gl = shaped_example()
    result = evaluate_candidate(gl, request, req, initial(), 'sheet_foam')
    assert result['valid'], result['fail_reasons']
    original = placement(gl)
    def forbidden(*args, **kwargs):
        raise AssertionError('Frozen comparison must not move hardware')
    monkeypatch.setattr(analysis, 'balance_with_wing_slide', forbidden)
    monkeypatch.setattr(analysis, 'trim', forbidden)
    monkeypatch.setattr(gl, 'balance', forbidden)
    result = evaluate_candidate(gl, request, req, initial(), 'sheet_foam', fixed_geometry=True)
    assert result['valid'], result['fail_reasons']
    assert placement(gl) == original
    assert abs(result['Cm_trim']) < .01
    assert result['fixed_geometry'] is True


def test_failed_arm_has_no_gain_and_recheck_is_explicit(tmp_path, monkeypatch):
    from cem.glider import comparison
    req, _, gl = shaped_example()
    count = []
    def failed(gl, *args, **kw):
        count.append(kw)
        return dict(valid=False, fail_reasons='cannot trim fixed hardware')
    monkeypatch.setattr(comparison, 'evaluate_candidate', failed)
    report = comparison.compare_shape(req, initial(), elite_for(gl), tmp_path)
    assert len(count) == 2 and all(x['fixed_geometry'] for x in count)
    assert report['objective_delta'] is None and report['verified_gain'] is False
    assert report['independent_recheck']['state'] == 'not_requested'
    assert report['status'] == 'inconclusive'


def test_real_pair_report_is_revision_bound_and_fails_closed_on_tampering(tmp_path):
    from cem.glider.comparison import compare_shape, validate_comparison
    from cem.glider.model import evaluate_candidate
    req, request, gl = shaped_example()
    result = evaluate_candidate(gl, request, req, initial(), 'sheet_foam')
    assert result['valid'], result['fail_reasons']
    elite = elite_for(gl)
    payload = dict(kind='comparison', requirements=req.model_dump(mode='json'), calibration=initial(),
                   elite=elite, independent_recheck=False)
    report = compare_shape(req, initial(), elite, tmp_path)
    validate_comparison(report, payload, tmp_path)
    assert report['verified_gain'] is False
    assert report['baseline']['parameters']['wing']['chord_factors'] == [1.]*5
    if report['status'] == 'screened':
        assert report['objective_delta'] == pytest.approx(
            report['shaped']['result']['objective_value']-report['baseline']['result']['objective_value'])
    for field, value in [('verified_gain', True), ('objective_delta', 9999.)]:
        bad = copy.deepcopy(report); bad[field] = value
        with pytest.raises(ValueError): validate_comparison(bad, payload, tmp_path)
    bad = copy.deepcopy(report)
    bad['baseline']['placement']['ballast_kg'] += .01
    with pytest.raises(ValueError): validate_comparison(bad, payload, tmp_path)


def test_rechecks_missing_binaries_are_explicit_and_do_not_fallback(tmp_path, monkeypatch):
    from cem.glider import rechecks
    req, _, gl = shaped_example()
    monkeypatch.setenv('MIRUM_XFOIL_BIN', '/missing/xfoil')
    monkeypatch.setenv('MIRUM_AVL_BIN', '/missing/avl')
    result = dict(valid=True, alpha_trim_deg=4., glide_speed_m_s=6.)
    arms = [dict(revision_sha256='a'*64, result=result)]*2
    report = rechecks.recheck_pair((gl, gl), arms, req, tmp_path)
    assert report['state'] == 'incomplete'
    assert all(arm['xfoil']['state'] == 'unavailable' for arm in report['arms'])
    assert all(arm['avl']['state'] == 'unavailable' for arm in report['arms'])


def test_avl_geometry_contains_all_surfaces_camber_and_cg_reference():
    from cem.glider.rechecks import avl_geometry
    req, _, gl = shaped_example()
    text = avl_geometry(gl)
    assert text.count('SURFACE\n') == 3
    assert text.count('SECTION\n') == sum(len(s.sections) for s in [gl.wing_surface(), *gl.tail_surfaces()])
    assert text.count('YDUPLICATE\n') == 2
    assert text.count('NACA\n') == text.count('SECTION\n')
    assert gl.wing.naca in text
    # Reference follows the actual mass CG, not the global origin.
    assert ' '.join(format(float(x), '.12g') for x in gl.mass_summary()['cg']) in text


@pytest.mark.parametrize('mode', ['missing', 'nonfinite', 'unstable'])
def test_avl_missing_or_bad_derivatives_fail_closed(tmp_path, mode):
    from cem.glider.rechecks import assess_avl
    _, _, gl = shaped_example()
    fields = dict(CLtot=.6, CDind=.02, Cmtot=0., e=.8, CLa=5., Cma=-.5,
                  CYb=-.4, Clb=-.1, Cnb=.1, CYp=0., Clp=-.5, Cnp=-.02,
                  CYr=.2, Clr=.1, Cnr=-.2, Cmq=-4.)
    if mode == 'missing': del fields['Cmq']
    if mode == 'nonfinite': fields['CLtot'] = float('nan')
    if mode == 'unstable': fields['Cma'] = .5
    raw = '\n'.join(f'{k} = {v}' for k,v in fields.items())
    with pytest.raises(ValueError):
        assess_avl(raw, gl, dict(CL_trim=.6, glide_speed_m_s=6., glide_angle_deg=4.))


@pytest.fixture
def solver_doubles(tmp_path, monkeypatch):
    """Actual subprocess test doubles; never qualification evidence."""
    from pathlib import Path
    import sys
    xfoil = tmp_path/'xfoil-double'
    source = (Path(__file__).resolve().parents[2]/'analysis/tests/mock_xfoil.py').read_text()
    xfoil.write_text('#!'+sys.executable+'\n'+source.split('\n', 1)[1])
    xfoil.chmod(0o755)
    avl = tmp_path/'avl-double'
    avl.write_text('#!'+sys.executable+'''\nimport sys, pathlib
s=sys.stdin.read()
name=next(x.split()[1] for x in s.splitlines() if x.startswith('ST '))
pathlib.Path(name).write_text('CLtot = 0.6 CDind = 0.02 Cmtot = 0 e = 0.8 CLa = 5 Cma = -0.5 CYb = -0.4 Clb = -0.1 Cnb = 0.1 CYp = 0 Clp = -0.5 Cnp = -0.02 CYr = 0.2 Clr = 0.1 Cnr = -0.2 Cmq = -4')
''')
    avl.chmod(0o755)
    monkeypatch.setenv('MIRUM_XFOIL_BIN', str(xfoil))
    monkeypatch.setenv('MIRUM_AVL_BIN', str(avl))
    return xfoil, avl


def test_executable_rechecks_preserve_and_validate_raw_station_evidence(tmp_path, solver_doubles):
    from cem.glider.rechecks import recheck_pair, validate_rechecks
    req, _, gl = shaped_example()
    result = dict(valid=True, alpha_trim_deg=4., glide_speed_m_s=6., CL_trim=.6, glide_angle_deg=4.)
    arms = [dict(revision_sha256='a'*64, result=result)]*2
    report = recheck_pair((gl, gl), arms, req, tmp_path)
    assert all(a['xfoil']['state'] == 'completed' for a in report['arms'])
    assert all(a['avl']['state'] == 'passed' for a in report['arms'])
    assert report['state'] == 'completed'
    validate_rechecks(report, arms, True, tmp_path, (gl, gl), req)
    command = tmp_path/'rechecks/baseline_avl.in'
    command.write_text(command.read_text().replace('A A 4', 'A A 9'))
    from cem.evidence import file_digest
    item = next(a for a in report['arms'][0]['avl']['artifacts'] if a['file'].endswith('.in'))
    item['sha256'] = file_digest(command)
    with pytest.raises(ValueError, match='operating point'):
        validate_rechecks(report, arms, True, tmp_path, (gl, gl), req)
    # Every wing and tail station is covered, not merely a root Reynolds polar.
    expected = sum(len(s.sections) for s in [gl.wing_surface(), *gl.tail_surfaces()])
    assert len(report['arms'][0]['xfoil']['stations']) == expected
    artifact = tmp_path/report['arms'][0]['xfoil']['artifacts'][1]['file']
    artifact.write_text(artifact.read_text()+'tampered')
    with pytest.raises(ValueError, match='artifact changed'):
        validate_rechecks(report, arms, True, tmp_path, (gl, gl), req)


def test_missing_xfoil_angles_are_rejected(tmp_path):
    from cem.glider.rechecks import assess_xfoil
    raw = tmp_path/'polar.out'
    raw.write_text('alpha CL CD CDp Cm\n------\n3 .5 .01 0 -.02\n4 .6 .01 0 -.02\n')
    with pytest.raises(ValueError, match='every requested'):
        assess_xfoil(raw, dict(alpha_deg=4., naca='2408', reynolds=60000.))


def test_native_process_timeout_is_bounded(tmp_path, monkeypatch):
    import subprocess
    import sys
    from cem.glider import rechecks
    tool = tmp_path/'sleep-solver'
    tool.write_text('#!'+sys.executable+'\nimport time\ntime.sleep(10)\n')
    tool.chmod(0o755)
    monkeypatch.setenv('MIRUM_AVL_BIN', str(tool))
    monkeypatch.setattr(rechecks, 'TIMEOUT_S', .05)
    with pytest.raises(subprocess.TimeoutExpired):
        rechecks.run_solver('avl', '', tmp_path)


def test_fixed_rod_loads_are_rescreened_without_resizing(monkeypatch):
    from cem.glider import model
    from cem.glider.comparison import comparison_pair
    req, request, gl = shaped_example()
    baseline, _ = comparison_pair(req, initial(), elite_for(gl))
    baseline.frozen_spar['stress_pa'] = baseline.frozen_spar['allowable_with_sf_pa']*2
    original_diameter = baseline.spar()['diameter_m']
    monkeypatch.setattr(model, 'evaluate', lambda *a, **kw: dict(valid=True, fail_reasons=''))
    result = model.evaluate_candidate(baseline, request, req, initial(), 'sheet_foam', fixed_geometry=True)
    assert result['valid'] is False
    assert 'fixed source spar' in result['fail_reasons']
    assert result['spar']['diameter_m'] == original_diameter
