from copy import deepcopy

import pytest

from cem.glider.families import coordinate_audit as audit
from cem.glider.families.coordinate_polars import coordinates_payload
import aerosandbox as asb


@pytest.mark.parametrize('failure', [None, 'missing', 'initialization', 'panels', 'midpoint'])
def test_audit_checks_actual_coordinate_state_and_retains_failure(tmp_path, monkeypatch, failure):
    calls = []
    points = coordinates_payload(asb.Airfoil('naca0012').coordinates)

    def query(coords, re, angles, folder, *, transition, panels, sequence):
        assert coords == points
        calls.append((re, list(angles), panels, sequence))
        folder.mkdir(parents=True)
        rows = [dict(alpha_deg=a, cl=.1*a, cd=.01, cm=0.) for a in angles]
        if failure == 'initialization' and sequence == 'reset_each':
            rows[0]['cl'] += .1
        if failure == 'panels' and panels == 240:
            rows[0]['cl'] += .1
        if failure == 'midpoint' and 100000 < re < 200000:
            rows[0]['cl'] += .1
        missing = [angles[1]] if failure == 'missing' else []
        return dict(coordinates=coords, reynolds=re, requested_angles_deg=list(angles),
                    panels=panels, sequence=sequence, transition=audit.polars.Transition().model_dump(),
                    state='incomplete' if missing else 'complete', missing_angles_deg=missing,
                    coordinates_sha256=audit.digest(coords), rows=rows)

    monkeypatch.setattr(audit.polars, 'query', query)
    monkeypatch.setattr(audit.polars, 'validate', lambda *args: False)
    result = audit.audit(points, [100000., 200000.], [-2., 0., 2.], tmp_path)
    assert result['state'] == ('passed' if failure is None else 'failed')
    assert result['production_admission'] is False
    assert audit.validate(result, tmp_path) is False
    if failure is None:
        assert len(calls) == 11
        assert calls[-1][1] == [-2., -1., 1., 2.]
    bad = deepcopy(result)
    bad['state'] = 'promoted'
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
