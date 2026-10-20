"""Acceptance must govern edited-source delivery, not just process success."""
import base64
import hashlib

from fastapi.testclient import TestClient
from backend.tests.test_native_api_hardening_ports import main, offline_models


BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10,20,3)\n"
LOOSE = ("import cadquery as cq\n"
         "result = cq.Compound.makeCompound([cq.Workplane('XY').box(10,20,3).val(), "
         "cq.Face.makePlane(5,5,cq.Vector(40,0,0))])\n")


def script(source):
    return source + "cq.Assembly(result).save('output.glb')\n"


def test_run_rejects_loose_topology_and_binds_native_delivery(offline_models):
    with TestClient(main.app) as client:
        invalid = client.post('/run', json={'script': script(LOOSE)}).json()
        assert invalid['status'] == 'error', 'Execution success must not publish loose topology'
        assert not invalid.get('glb')
        valid = client.post('/run', json={'script': script(BOX)}).json()
    assert valid['status'] == 'ok', valid
    cert = valid['geometry_certificate']
    assert cert['passed'] is True
    assert cert['scope'] == 'structural_solid_set_v1'
    assert cert['engineering_certified'] is False
    assert cert['artifacts']['glb_sha256'] == hashlib.sha256(base64.b64decode(valid['glb'])).hexdigest()


import pytest
from unittest.mock import AsyncMock
from backend.tests.test_native_api_hardening_ports import agents, events
from schemas import ManifestDiff


@pytest.mark.parametrize('route', ['generate', 'refine', 'export'])
def test_all_delivery_routes_reject_loose_faces_and_return_measured_evidence(offline_models, monkeypatch, route):
    monkeypatch.setattr(main, 'run_refiner', AsyncMock(return_value=ManifestDiff()))
    def invoke(client, source):
        if route == 'generate':
            agents.run_machinist.return_value = source
            return events(client.post('/generate', json={'prompt': 'fixture structural acceptance'}))[-1]
        if route == 'refine':
            return client.post('/refine', json={
                'original_manifest': offline_models.model_dump(mode='json'),
                'original_scripts': {'box': source}, 'refinement_prompt': 'keep unchanged',
            }).json()
        return client.post('/export/parasolid', json={'script': source}).json()
    with TestClient(main.app) as client:
        invalid = invoke(client, LOOSE)
        assert invalid['status'] == 'error', (route, invalid)
        assert not invalid.get('glb') and not invalid.get('data_b64')
        valid = invoke(client, BOX)
    assert valid['status'] in {'ok', 'done'}, (route, valid)
    cert = valid['geometry_certificate']
    assert cert['passed'] is True
    assert cert['scope'] == 'structural_solid_set_v1'
    assert cert['solid_count'] == 1
    assert cert['volume_mm3'] == pytest.approx(600)
    assert cert['engineering_certified'] is False
    if route == 'export' and valid['format'] == 'step':
        data = base64.b64decode(valid['data_b64'])
        assert cert['artifacts']['step_sha256'] == hashlib.sha256(data).hexdigest()
    elif route != 'export':
        assert cert['artifacts']['glb_sha256'] == hashlib.sha256(base64.b64decode(valid['glb'])).hexdigest()


def test_generate_source_digest_matches_returned_runnable_source(offline_models):
    with TestClient(main.app) as client:
        result = events(client.post('/generate', json={'prompt': 'fixture box'}))[-1]
    assert result['status'] == 'done', result
    assert result['geometry_certificate']['artifacts']['source_sha256'] == hashlib.sha256(
        result['script'].encode()).hexdigest()
