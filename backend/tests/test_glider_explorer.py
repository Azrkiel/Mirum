import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import ValidationError

from gliders.design_spec import DesignSpec, preset, from_candidate
from gliders.preview import preview, metadata
from gliders import api

ROOT = Path(__file__).resolve().parents[2]


def test_revision_validation_and_independent_instances():
    spec = preset()
    altered = spec.model_dump()
    altered['parameters']['wing']['dihedral_deg'] = 15
    with pytest.raises(ValidationError):
        DesignSpec.model_validate(altered)
    altered = spec.model_dump()
    altered['parameters']['tail']['vt_panel_span'] = .15
    with pytest.raises(ValidationError):
        DesignSpec.model_validate(altered)
    one, two = spec.build(), spec.build()
    one.wing_x = .2
    one.wing.span = 1.2
    assert two.wing_x is None and two.wing.span == 1.1
    assert spec.digest() == preset().digest()
    altered = spec.model_dump()
    altered['request']['launch_height_m'] = 20
    assert DesignSpec.model_validate(altered).digest() != spec.digest()


def test_preview_does_not_evaluate_or_export(monkeypatch):
    import gliders.model.analysis as analysis
    import gliders.model.export as export
    def forbidden(*args, **kwargs):
        raise AssertionError('Heavy path called')
    monkeypatch.setattr(analysis, 'evaluate', forbidden)
    monkeypatch.setattr(export, 'export_stls', forbidden)
    spec = preset()
    first = preview(spec)
    assert first == preview(spec)
    assert first['input_revision'] == spec.digest()
    assert all(len(p['vertices']) < 10000 for p in first['geometry']['parts'])


@pytest.mark.parametrize('wing', ['tapered', 'elliptical', 'polyhedral'])
@pytest.mark.parametrize('body', ['pod_boom', 'torpedo', 'slab'])
@pytest.mark.parametrize('tail', ['conventional', 'T', 'V'])
def test_browser_geometry_matches_canonical_meshes(wing, body, tail):
    from gliders.spec import GliderRequest
    spec = preset(GliderRequest(ranges={'wing': {'style': [wing]}, 'fuselage': {'style': [body]}, 'tail': {'kind': [tail]}}))
    data = spec.model_dump()
    data['parameters']['wing']['naca'] = '2412'
    data['parameters']['wing']['sweep_qc_deg'] = 7
    data['parameters']['wing']['washout_deg'] = 2.4
    data['placement']['tail_incidence_deg'] = -3
    spec = DesignSpec.model_validate(data)
    actual = preview(spec)['geometry']
    script = "import {meshes} from './frontend/components/glider_explorer/src/model.js';let s='';for await(const c of process.stdin)s+=c;const d=JSON.parse(s);console.log(JSON.stringify(meshes(d.design,d.airfoils)));"
    result = subprocess.run(['node', '--input-type=module', '-e', script], input=json.dumps({'design': spec.model_dump(), 'airfoils': metadata(spec)['airfoils']}), capture_output=True, text=True, cwd=ROOT, check=True)
    browser = json.loads(result.stdout)
    for local, canonical in zip(browser['parts'], actual['parts']):
        assert local['name'] == canonical['name']
        assert local['triangles'] == canonical['triangles']
        assert np.asarray(local['vertices']) == pytest.approx(np.asarray(canonical['vertices']), abs=1e-9)
    assert np.asarray(browser['stations']) == pytest.approx(np.asarray(actual['stations']), abs=1e-12)


def test_candidate_placement_is_preserved():
    spec = preset()
    p = spec.model_dump()['parameters']
    for family in p:
        p[family]['id'] = 'candidate'
    candidate = dict(parameters=p, request=spec.request.model_dump(), wing_le_x_m=.18,
                     payload_x_m=.16, ballast_kg=.005, tail_incidence_deg=-2)
    restored = from_candidate(candidate)
    assert restored.build().wing_position()[0] == .18
    assert restored.build().tail_incidence_deg == -2
    assert restored.build().payload_x == .16


def test_preview_and_evaluation_api_share_bounded_scheduler(monkeypatch, tmp_path):
    import asyncio
    manager = api.Jobs()
    monkeypatch.setattr(api, 'jobs', manager)
    monkeypatch.setenv('MIRUM_WORK_DIR', str(tmp_path))
    async def hold(key, spec):
        await asyncio.sleep(100)
    monkeypatch.setattr(manager, 'run', hold)
    app = FastAPI()
    api.attach_glider_routes(app, Limiter(key_func=get_remote_address, enabled=False))
    with TestClient(app) as client:
        design = client.get('/gliders/metadata').json()['preset']
        assert client.post('/gliders/preview', json=design).status_code == 200
        job = client.post('/gliders/evaluations', json=design)
        assert job.status_code == 202
        key = job.json()['evaluation_id']
        assert client.post('/gliders/search', json={}).status_code == 429
        assert client.post('/gliders/evaluations', json=design).status_code == 429
        assert client.post('/gliders/preview', json=design).status_code == 200
        assert client.get(f'/gliders/evaluations/{key}/export').status_code == 409
        assert client.get('/gliders/evaluations/unknown').status_code == 404
        client.portal.call(manager.close)
