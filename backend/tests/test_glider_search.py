import asyncio
import copy
import json
import random
from pathlib import Path
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from slowapi import Limiter
from slowapi.util import get_remote_address

from gliders.spec import GliderRequest
from gliders.search import sample, admissible, search
from gliders import api


@pytest.mark.parametrize('changes', [
    {'launch_height_m': float('nan')}, {'evaluations': 10000}, {'objective': 'speed'},
    {'static_margin_target': 0.02, 'static_margin_min': 0.10},
    {'ranges': {'wing': {'span_m': [1.2, 0.8]}}},
    {'ranges': {'wing': {'style': []}}}, {'ranges': {'wing': {'style': ['jet']}}},
    {'ranges': {'tail': {'v_taper': [0.2, 1.5]}}},
    {'ranges': {'wing': {'span_m': [1.2, 1.3]}}, 'max_wingspan_m': 1.0},
    {'ranges': {'wing': {'span_m': [False, 1]}}},
])
def test_invalid_search_space_rejected(changes):
    with pytest.raises(ValidationError):
        GliderRequest(**changes)


def test_refinement_preserves_locked_parameters_and_configuration():
    spec = GliderRequest(ranges={'wing': {'style': ['tapered'], 'span_m': [1.0, 1.0]},
                                'tail': {'kind': ['T'], 'v_taper': [0.5, 0.5]}})
    cfg = spec.configuration()
    rng = random.Random(42)
    parent = sample(rng, cfg, 1)
    for i in range(20):
        gl = sample(rng, cfg, i + 2, parent)
        assert gl.wing.span == 1.0 and gl.wing.style == 'tapered'
        assert gl.tail.kind == 'T' and gl.tail.v_taper == 0.5


def test_balancing_respects_locked_wing_mount():
    from gliders.model.analysis import balance_with_wing_slide
    cfg = GliderRequest(ranges={'fuselage': {'wing_station_in_pod': [0.5, 0.5]}}).configuration()
    gl = sample(random.Random(42), cfg, 1)
    expected = gl.wing_position()[0]
    balance_with_wing_slide(gl, 7, 0.12)
    assert gl.wing_x == pytest.approx(expected)


def test_nonfinite_evidence_never_qualifies():
    from backend.tests.test_glider_model import good_glider
    from gliders.model.config import CONFIG
    gl = good_glider(copy.deepcopy(CONFIG))
    result = admissible(gl, GliderRequest(), {'valid': True, 'L_over_D': float('nan')})
    assert not result['valid']
    assert 'nonfinite' in result['fail_reasons']


def test_actual_search_is_repeatable_and_exports_evaluated_trim(tmp_path):
    # An actual seeded, bounded optimizer run exercises native aero, ranking and exports.
    spec = GliderRequest(evaluations=8, top_k=2, seed=42, ranges={
        'wing': {'style': ['tapered'], 'span_m': [1.08, 1.12], 'root_chord_m': [0.15, 0.15],
                 'taper': [0.6, 0.6], 'dihedral_deg': [7, 7], 'washout_deg': [1.5, 1.5],
                 'incidence_deg': [2, 2], 'sweep_qc_deg': [0, 0], 'naca_camber_pct': [2],
                 'naca_camber_pos': [4], 'naca_thickness_pct': [8]},
        'fuselage': {'style': ['pod_boom'], 'length_m': [0.75, 0.75],
                     'max_diameter_m': [0.07, 0.07], 'nose_frac': [0.08, 0.08],
                     'pod_frac': [0.38, 0.38], 'boom_radius_m': [0.007, 0.007],
                     'wing_station_in_pod': [0.5, 0.5]},
        'tail': {'kind': ['conventional'], 'h_span_m': [0.32, 0.32],
                 'h_root_chord_m': [0.08, 0.08], 'h_taper': [1, 1],
                 'v_height_m': [0.12, 0.12], 'v_root_chord_m': [0.09, 0.09],
                 'v_taper': [0.7, 0.7], 'v_sweep_le_deg': [20, 20], 'airfoil': ['0008']}})
    result = search(spec, tmp_path / 'run')
    assert result['evaluated'] == 8
    assert any(r['phase'] == 'refinement' for r in result['evaluations'])
    assert result['candidates'], result['rejections']
    scores = [c['L_over_D'] for c in result['candidates']]
    assert scores == sorted(scores, reverse=True)
    for c in result['candidates']:
        assert c['glide_distance_m'] == pytest.approx(spec.launch_height_m * c['L_over_D'])
        assert c['mass_kg'] <= spec.max_total_mass_kg
        assert c['static_margin'] >= spec.static_margin_min
        assert c['CL_trim'] <= spec.stall_margin * c['CLmax_est']
        with zipfile.ZipFile(tmp_path / 'run' / f"{c['id']}.zip") as z:
            assert len(z.read('assembly.stl')) > 84
            assert json.loads(z.read('design.json'))['tail_incidence_deg'] == c['tail_incidence_deg']
            assert json.loads(z.read('design.json'))['request'] == spec.model_dump()
            assert z.read('three_view.png').startswith(b'\x89PNG')
    # Deterministic sampling, including elite-guided second half.
    second = search(spec, tmp_path / 'repeat')
    assert [c['parameters'] for c in result['candidates']] == [c['parameters'] for c in second['candidates']]
    assert [c['L_over_D'] for c in second['candidates']] == pytest.approx(scores)


def test_no_feasible_design_is_reported_without_geometry(tmp_path):
    result = search(GliderRequest(evaluations=8), tmp_path,
                    evaluator=lambda gl: {'id': gl.id, 'valid': False, 'fail_reasons': 'fixture rejection'})
    assert result['candidates'] == []
    assert result['rejections'] == {'fixture rejection': 8}
    assert not list(tmp_path.glob('*.zip'))


def test_api_job_states_busy_validation_and_download_membership(monkeypatch, tmp_path):
    manager = api.Jobs()
    monkeypatch.setattr(api, 'jobs', manager)
    monkeypatch.setenv('MIRUM_WORK_DIR', str(tmp_path))

    async def hold(key, spec):
        await asyncio.sleep(100)

    monkeypatch.setattr(manager, 'run', hold)
    app = FastAPI()
    api.attach_glider_routes(app, Limiter(key_func=get_remote_address, enabled=False))
    with TestClient(app) as client:
        assert client.post('/gliders/search', json={'evaluations': 1000}).status_code == 422
        response = client.post('/gliders/search', json={'evaluations': 8})
        assert response.status_code == 202
        key = response.json()['job_id']
        assert client.get(f'/gliders/search/{key}').json()['status'] == 'running'
        assert client.post('/gliders/search', json={}).status_code == 429
        assert client.get('/gliders/search/missing').status_code == 404
        assert client.get(f'/gliders/search/{key}/geometry/fake').status_code == 409
        manager.jobs[key].update(status='completed', result={'candidates': [{'id': 'candidate'}]})
        (manager.jobs[key]['directory'] / 'candidate.zip').write_bytes(b'zip-fixture')
        assert client.get(f'/gliders/search/{key}/geometry/other').status_code == 404
        assert client.get(f'/gliders/search/{key}/geometry/candidate').content == b'zip-fixture'
        client.portal.call(manager.close)


@pytest.mark.asyncio
async def test_worker_failure_is_terminal_and_receives_no_credentials(monkeypatch, tmp_path):
    manager = api.Jobs()
    manager.jobs['fixture'] = {'status': 'running', 'directory': tmp_path}
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'must-not-reach-worker')

    async def timeout(self, command, **kwargs):
        assert 'GEMINI_API_KEY_HEAVY' not in kwargs['env']
        assert 'HTTPS_PROXY' not in kwargs['env']
        return {'code': 'TIMEOUT', 'returncode': -9, 'stderr': '', 'stdout': ''}

    monkeypatch.setattr(api.ProcessRunner, 'run', timeout)
    await manager.run('fixture', GliderRequest())
    assert manager.jobs['fixture']['status'] == 'failed'
    assert 'result' not in manager.jobs['fixture']
