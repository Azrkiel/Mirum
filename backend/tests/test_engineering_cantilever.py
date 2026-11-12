"""Real adapter/native equivalence; injected failures are labelled seams."""
import asyncio
import hashlib

import pytest

from backend.tests.test_engineering_contracts import finalized


def test_adapter_registered_and_reference_matches_legacy():
    from engineering.capabilities import get_adapter, CAPABILITY
    from engineering.service import generate_verified
    adapter = get_adapter(CAPABILITY.id, CAPABILITY.version)
    candidates = adapter.candidates(finalized())
    assert [c.specification.height_mm for c in candidates] == [5, 10, 15]
    for candidate in candidates:
        result = asyncio.run(adapter.evaluate(candidate))
        legacy = asyncio.run(generate_verified(candidate.specification))
        assert result['outcome'].value == legacy['status']
        assert result['report']['measurements'] == pytest.approx(legacy['engineering_report']['measurements'])
        assert result['report']['step_sha256'] == result['step_identity'].sha256
        assert result['report']['implementation_sha256']['verify.py']
        assert len(result['checks']) == 20
        for check in result['checks']:
            assert check.brief_sha256 == candidate.brief_sha256
            assert check.candidate_sha256 == candidate.candidate_sha256
        if legacy['status'] == 'accepted':
            assert hashlib.sha256(result['artifacts']['model.step']).hexdigest() == result['step_identity'].sha256
            assert all(c.measurements and c.limits for c in result['checks'])
        else:
            assert result['artifacts'] == {}


def test_adapter_unavailable_is_not_rejection(monkeypatch):
    from engineering.cantilever import CantileverAdapter
    from engineering.service import EngineeringUnavailable
    import engineering.service as service
    async def missing(_):
        raise EngineeringUnavailable('injected unavailable solver seam')
    monkeypatch.setattr(service, 'generate_verified', missing)
    adapter = CantileverAdapter()
    with pytest.raises(EngineeringUnavailable):
        asyncio.run(adapter.evaluate(adapter.candidates(finalized())[0]))


def test_adapter_revalidates_forged_candidate_before_native_work(monkeypatch):
    from engineering.cantilever import CantileverAdapter
    from unittest.mock import AsyncMock
    import engineering.service as service
    adapter = CantileverAdapter()
    candidate = adapter.candidates(finalized())[0]
    forged = candidate.model_copy(update={'specification': candidate.specification.model_copy(update={'height_mm': 12.0})})
    native = AsyncMock(side_effect=AssertionError('native work must not start'))
    monkeypatch.setattr(service, 'generate_verified', native)
    with pytest.raises(ValueError):
        asyncio.run(adapter.evaluate(forged))
    native.assert_not_called()


def test_changed_root_section_is_valid_brep_but_rejected(tmp_path):
    import cadquery as cq
    from engineering.verify import verify_step
    from engineering.contracts import CandidateSpec
    candidate = CandidateSpec.from_brief(finalized(), 15.0)
    prism = cq.Workplane('XY').box(200, 10, 15, centered=(False, False, False))
    notch = cq.Workplane('XY').box(2, 2, 15, centered=(False, False, False))
    path = tmp_path / 'changed-root.step'
    cq.exporters.export(prism.cut(notch), str(path))
    report = verify_step(str(path), candidate.specification)
    assert report['native_geometry']['passed'] is True
    assert report['passed'] is False
    assert report['physics']['state'] == 'not_evaluated'
    assert report['step_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
