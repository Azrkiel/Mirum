from copy import deepcopy

import pytest

from cem.glider.families import coordinate_audit as audit
from cem.glider.families.coordinate_polars import coordinates_payload
import aerosandbox as asb


@pytest.mark.parametrize('failure', [None, 'missing', 'initialization', 'panels', 'midpoint', 'mixed'])
@pytest.mark.parametrize('vaccel', [None, 0.])
def test_audit_checks_actual_coordinate_state_and_retains_failure(tmp_path, monkeypatch, failure, vaccel):
    calls = []
    points = coordinates_payload(asb.Airfoil('naca0012').coordinates)

    def query(coords, re, angles, folder, *, transition, panels, sequence, vaccel):
        assert coords == points
        calls.append((re, list(angles), panels, sequence))
        folder.mkdir(parents=True)
        rows = [dict(alpha_deg=a, cl=.1*a, cd=.01, cm=0.) for a in angles]
        if failure == 'initialization' and sequence == 'reset_each':
            rows[0]['cl'] += .1
        if failure in ('panels', 'mixed') and panels == 240:
            rows[0]['cl'] += .1
        if failure == 'midpoint' and 100000 < re < 200000:
            rows[0]['cl'] += .1
        missing = [angles[1]] if failure == 'missing' or (failure == 'mixed' and sequence == 'reset_each') else []
        rows = [row for row in rows if row['alpha_deg'] not in missing]
        return dict(coordinates=coords, reynolds=re, requested_angles_deg=list(angles),
                    panels=panels, sequence=sequence, vaccel=vaccel, transition=audit.polars.Transition().model_dump(),
                    state='incomplete' if missing else 'complete', missing_angles_deg=missing,
                    coordinates_sha256=audit.digest(coords), rows=rows)

    monkeypatch.setattr(audit.polars, 'query', query)
    monkeypatch.setattr(audit.polars, 'validate', lambda *args: False)
    result = audit.audit(points, [100000., 200000.], [-2., 0., 2.], tmp_path, vaccel=vaccel)
    assert result['inputs']['vaccel'] == vaccel
    assert result['state'] == ('passed' if failure is None else 'failed')
    assert result['production_admission'] is False
    comparisons = result['comparisons']
    if failure == 'mixed':
        # One incomplete initialization must not hide an independently
        # evaluable panel disagreement, or borrow its missing row.
        assert any('panel refinement' in f['reason'] for f in result['failures'])
        assert all(c['left'] != f're_{i}/reset_each' and c['right'] != f're_{i}/reset_each'
                   for c in comparisons for i in range(2))
    if failure in ('panels', 'mixed'):
        panel = next(c for c in comparisons if c['kind'] == 'panel_refinement')
        assert panel['differences']['cl']['alpha_deg'] == -2.
        assert panel['differences']['cl']['absolute_error'] == pytest.approx(.1)
    if failure == 'midpoint':
        worst = result['interpolation_worst']['cl']
        assert worst['alpha_deg'] == -2.
        assert worst['reynolds'] == pytest.approx((100000.*200000.)**.5)
        assert abs(worst['predicted'] - worst['observed']) == pytest.approx(.1)
    if failure == 'missing':
        assert comparisons == []
        assert result['interpolation_differences'] is None
        assert result['interpolation_worst'] is None
    assert audit.validate(result, tmp_path) is False
    if failure is None:
        assert len(calls) == 11
        assert calls[-1][1] == [-2., -1., 1., 2.]
    bad = deepcopy(result)
    bad['state'] = 'promoted'
    with pytest.raises(ValueError, match='reconstruction'):
        audit.validate(bad, tmp_path)
    if comparisons:
        bad = deepcopy(result)
        bad['comparisons'][0]['differences']['cl']['absolute_error'] += .1
        with pytest.raises(ValueError, match='reconstruction'):
            audit.validate(bad, tmp_path)


def test_native_coordinate_audit_replays_without_promoting_qualification(tmp_path):
    points = asb.Airfoil('naca0012').coordinates
    report = audit.audit(points, [900000., 1000000.], [-1., 0., 1.], tmp_path)
    assert len(report['queries']) == 11
    assert report['state'] == 'passed', report.get('failure')
    assert report['interpolation_queries_checked'] == 1
    assert audit.validate(report, tmp_path) is False
    bad = deepcopy(report)
    bad['queries'][-1]['report']['rows'][0]['cl'] += .1
    with pytest.raises(ValueError, match='reconstruction'):
        audit.validate(bad, tmp_path)
