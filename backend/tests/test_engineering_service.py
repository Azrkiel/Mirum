import importlib.util
import asyncio
import hashlib
import pytest
from backend.tests.test_engineering_spec import spec_data


def test_service_delivers_geometry_and_physics_bound_to_one_spec_and_artifact():
    assert importlib.util.find_spec('engineering.service') is not None, 'Deterministic engineering service is missing'
    from engineering.spec import CantileverSpec, specification_digest
    from engineering.service import generate_verified
    spec = CantileverSpec.model_validate(spec_data())
    delivery = asyncio.run(generate_verified(spec))
    assert delivery['status'] == 'accepted', delivery
    assert delivery['engineering_report']['passed'] is True
    assert delivery['engineering_report']['spec_sha256'] == specification_digest(spec)
    assert delivery['engineering_report']['step_sha256'] == hashlib.sha256(delivery['step']).hexdigest()
    assert delivery['geometry_certificate']['artifacts']['source_sha256'] == hashlib.sha256(delivery['script'].encode()).hexdigest()
    assert delivery['glb'][:4] == b'glTF'


def test_solver_feedback_selects_only_an_explicitly_approved_height():
    from engineering.spec import CantileverSpec
    import engineering.service as service
    assert hasattr(service, 'generate_with_feedback'), 'Bounded solver feedback is missing'
    spec = CantileverSpec.model_validate(spec_data(height_mm=5.0, tip_force_n=50.0, max_tip_deflection_mm=1.0))
    result = asyncio.run(service.generate_with_feedback(spec, [5.0, 10.0, 15.0]))
    assert result['status'] == 'accepted', result
    assert result['specification']['height_mm'] == 15.0
    assert [attempt['status'] for attempt in result['attempts']] == ['rejected', 'rejected', 'accepted']
    assert result['attempts'][0]['failures']
    assert result['engineering_report']['approved_request_sha256']


@pytest.mark.parametrize('kind', ['missing', 'malformed', 'stale_spec', 'false_pass', 'timeout'])
def test_unavailable_or_forged_worker_evidence_never_delivers_artifacts(monkeypatch, kind):
    import json
    from pathlib import Path
    from process_runner import ProcessRunner
    from engineering.service import generate_verified, EngineeringUnavailable
    from engineering.spec import CantileverSpec, specification_digest
    spec = CantileverSpec.model_validate(spec_data())
    original = ProcessRunner.run
    async def break_evidence(self, command, **kwargs):
        if Path(command[1]).name != 'worker.py':
            return await original(self, command, **kwargs)
        path = Path(command[-1])
        if kind == 'timeout':
            return dict(code='TIMEOUT', returncode=-9)
        if kind == 'malformed':
            path.write_text('[]')
        elif kind == 'stale_spec':
            path.write_text(json.dumps({'passed': True, 'spec_sha256': 'stale'}))
        elif kind == 'false_pass':
            path.write_text(json.dumps({'passed': True, 'spec_sha256': specification_digest(spec)}))
        return dict(code=None, returncode=0)
    monkeypatch.setattr(ProcessRunner, 'run', break_evidence)
    with pytest.raises(EngineeringUnavailable):
        asyncio.run(generate_verified(spec))
    from compiler import _WORK_DIR
    assert not list(_WORK_DIR.glob('engineering-*'))


@pytest.mark.parametrize('heights', [[5.0]*2, [1.0]*5, [float('nan')], [50.0], ['10'], [True]])
def test_search_space_is_validated_before_any_native_work(monkeypatch, heights):
    from engineering.spec import CantileverSpec
    import engineering.service as service
    from unittest.mock import AsyncMock
    call = AsyncMock(side_effect=AssertionError('No native work may start'))
    monkeypatch.setattr(service, 'generate_verified', call)
    with pytest.raises(ValueError):
        asyncio.run(service.generate_with_feedback(CantileverSpec.model_validate(spec_data()), heights))
    call.assert_not_called()

