"""Native refinement reuse must preserve valid Assembly-valued subsystems."""
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from schemas import ManifestDiff
from backend.tests.test_review_regressions import (
    ASSEMBLY_SOURCE, main, offline_models,
)


def test_refine_reuses_assembly_result_as_step(offline_models, monkeypatch):
    monkeypatch.setattr(main, 'run_refiner', AsyncMock(return_value=ManifestDiff()))
    with TestClient(main.app) as client:
        response = client.post('/refine',json={
            'original_manifest': offline_models.model_dump(),
            'original_scripts': {'box': ASSEMBLY_SOURCE},
            'refinement_prompt': 'keep existing geometry',
        })
    body = response.json()
    assert body['status'] == 'ok', body
    assert body['glb']
    assert body['updated_scripts']['box'] == ASSEMBLY_SOURCE
    assert 'assembly' in body['script']
