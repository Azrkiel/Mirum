import math
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.test_cem_api import app_for
from cem.glider import calibration, discovery
from cem.glider.requirements import GliderMissionRequirements


@pytest.fixture
def flight_test(monkeypatch, tmp_path):
    base = calibration.initial()
    report = {
        'output_id': 'a' * 12,
        'status': 'screened',
        'requirements': {'launch': {'method': 'drop'}, 'environment': {'kind': 'still_air'}},
        'closure': {'reanalysis': {
            'CL_trim': 0.48,
            'CD_trim': 0.05,
            'cd_profile': 0.02,
            'glide_speed_m_s': 5.0,
        }},
        'calibration': base,
        'calibration_version': calibration.version(base),
        'elite_id': 'b' * 16,
        'design_revision_sha256': 'c' * 64,
        'cem_version': '1.0.0',
    }
    payload = {
        'design_job_id': 'd' * 32,
        'output_id': report['output_id'],
        'measured': {
            'launch_method': 'drop',
            'launch_height_m': 10.0,
            'distance_m': 80.0,
            'duration_s': math.hypot(80.0, 10.0) / 5.0,
        },
    }

    def completed(job_id, kind):
        assert job_id == payload['design_job_id']
        assert kind == 'design'
        return {'result': report}

    monkeypatch.setattr(calibration, 'root', lambda: tmp_path)
    with TestClient(app_for(SimpleNamespace(completed=completed), monkeypatch)) as client:
        yield client, report, payload


def test_flight_test_saves_new_calibration_version(flight_test):
    client, report, payload = flight_test
    response = client.post('/cem/glider/flight-test', json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    new_version = body['calibration_version']
    parent_version = report['calibration_version']
    assert re.fullmatch(r'[a-f0-9]{64}', new_version)
    assert new_version != parent_version
    assert body['parent_calibration_version'] == parent_version
    assert body['coefficients_updated'] == ['cd0_factor']
    assert calibration.load(new_version)['parent'] == parent_version


@pytest.mark.parametrize(
    ('status', 'output_id'), [('rejected', 'a' * 12), ('screened', 'e' * 12)],
)
def test_flight_test_requires_matching_screened_design(flight_test, status, output_id):
    client, report, payload = flight_test
    report['status'] = status
    payload['output_id'] = output_id

    response = client.post('/cem/glider/flight-test', json=payload)

    assert response.status_code == 409, response.text


@pytest.mark.parametrize(
    ('measured_launch', 'design_launch', 'environment', 'detail'),
    [
        ('catapult', 'drop', 'still_air', 'Profile drag is not identifiable'),
        ('drop', 'catapult', 'still_air', 'Profile drag is not identifiable'),
        ('drop', 'drop', 'wind', 'Calibration requires a still-air flight log'),
    ],
)
def test_flight_test_requires_still_air_drop(
    flight_test, measured_launch, design_launch, environment, detail,
):
    client, report, payload = flight_test
    payload['measured']['launch_method'] = measured_launch
    report['requirements']['launch']['method'] = design_launch
    report['requirements']['environment']['kind'] = environment

    response = client.post('/cem/glider/flight-test', json=payload)

    assert response.status_code == 422, response.text
    assert response.json()['detail'].startswith(detail)


def test_drag_update_changes_calibration_version_deterministically():
    base_version = calibration.version(calibration.initial())
    first = calibration.update_drag(calibration.initial(), [1.5])
    second = calibration.update_drag(calibration.initial(), [1.5])

    assert calibration.version(first) != base_version
    assert calibration.version(first) == calibration.version(second)


def test_discovery_reports_unsupported_family_and_searches_supported_family(monkeypatch):
    evaluated_tails = set()

    def evaluate(glider, *args):
        evaluated_tails.add(glider.tail.kind)
        return {'valid': False, 'fail_reasons': 'Synthetic candidate rejection'}

    monkeypatch.setattr(discovery, 'evaluate_candidate', evaluate)
    requirements = GliderMissionRequirements(
        launch={'method': 'drop'},
        process='balsa',
        discovery={'families': ['conventional', 'box'], 'budget_evals': 8},
    )

    result = discovery.discover(requirements, calibration.initial())

    assert result['status'] == 'completed'
    assert result['capabilities']['unavailable_families'] == ['box']
    assert evaluated_tails == {'conventional'}
