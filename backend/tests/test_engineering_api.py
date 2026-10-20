import base64
import hashlib
import pytest
from fastapi.testclient import TestClient
from backend.tests.test_native_api_hardening_ports import main, offline_models
from backend.tests.test_engineering_spec import spec_data


def test_engineering_api_uses_no_llm_and_returns_measured_bound_evidence(offline_models):
    with TestClient(main.app) as client:
        response = client.post('/engineering/generate', json={'specification': spec_data()})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'accepted', result
    assert result['engineering_report']['physics']['state'] == 'passed'
    assert result['engineering_report']['standards']['state'] == 'not_evaluated'
    assert hashlib.sha256(base64.b64decode(result['step'])).hexdigest() == result['engineering_report']['step_sha256']
    assert base64.b64decode(result['glb'])[:4] == b'glTF'


@pytest.mark.parametrize('changes', [{'tip_force_n': None}, {'units': 'inch'}, {'height_mm': 50.0}])
def test_engineering_api_rejects_unsupported_requirements(offline_models, changes):
    with TestClient(main.app) as client:
        response = client.post('/engineering/generate', json={'specification': spec_data(**changes)})
    assert response.status_code == 422


def test_engineering_api_returns_no_artifacts_when_required_physics_fails(offline_models):
    with TestClient(main.app) as client:
        result = client.post('/engineering/generate', json={'specification': spec_data(allowable_stress_mpa=1.0)}).json()
    assert result['status'] == 'rejected', result
    assert not result.get('glb') and not result.get('step')
    assert result['engineering_report']['checks']['stress_allowable']['state'] == 'failed'
