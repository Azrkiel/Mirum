"""Real CAD/API paths with deterministic LLM fixtures, never provider calls.

Run in Linux with CadQuery/OCP; model seams are fixtures, but compilation,
STEP/GLB export, certificate checks, SSE and HTTP handlers are real.
"""
import base64
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GEMINI_API_KEY_HEAVY', 'offline-fixture-never-sent')

import agents
import main
from fastapi.testclient import TestClient
from schemas import AssemblyManifest, DesignRequirements, PartDefinition

BOX_SOURCE = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 3)\n"


@pytest.fixture
def offline_models(monkeypatch):
    manifest = AssemblyManifest(assembly_name='box', parts=[PartDefinition(
        part_id='box', description='10 x 20 x 3 mm box', anchor_tags=['>Z'])], mating_rules=[])
    monkeypatch.setattr(main, 'run_rea', AsyncMock(return_value=DesignRequirements(primary_function='box')))
    monkeypatch.setattr(main, 'run_planner', AsyncMock(return_value=manifest))
    monkeypatch.setattr(agents, 'classify_part', AsyncMock(return_value={'domain':'A','reasoning':'fixture','key_params':''}))
    monkeypatch.setattr(agents, 'run_machinist', AsyncMock(return_value=BOX_SOURCE))
    monkeypatch.setattr(agents, '_score_and_log', AsyncMock())
    monkeypatch.setattr(main.limiter, 'enabled', False)
    # Any accidental SDK generation must fail rather than reach a provider.
    monkeypatch.setattr(agents.genai, 'GenerativeModel', lambda *a, **k: pytest.fail('Unexpected live model call'))
    return manifest


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_generate_exports_real_geometry_and_refinement_context(offline_models):
    with TestClient(main.app) as client:
        result = events(client.post('/generate', json={'prompt':'fixture box'}))[-1]
    assert result['status'] == 'done', result
    assert base64.b64decode(result['glb'])[:4] == b'glTF'
    assert result['geometry_certificate']['passed'] is True
    assert result['geometry_certificate']['volume_mm3'] == pytest.approx(600)
    assert result['manifest'] == offline_models.model_dump(mode='json')
    assert result['scripts'] == {'box':BOX_SOURCE}


def test_failed_geometry_certificate_never_delivers_success(offline_models, monkeypatch):
    from process_runner import ProcessRunner
    original_run = ProcessRunner.run
    observed = []
    async def fail_native(self, command, **kwargs):
        if len(command) > 1 and Path(command[1]).name == 'acceptance_worker.py':
            observed.append(True)
            Path(command[-1]).write_text(json.dumps({'passed': False, 'checks': {},
                'failures': ['fixture required check failed']}), encoding='utf-8')
            return {'code': None, 'returncode': 0, 'stdout': '', 'stderr': ''}
        return await original_run(self, command, **kwargs)
    # Native acceptance now runs in its own bounded process; inject failure at
    # that actual boundary, rather than monkeypatching the obsolete parent helper.
    monkeypatch.setattr(ProcessRunner, 'run', fail_native)
    with TestClient(main.app) as client:
        result = events(client.post('/generate', json={'prompt':'fixture box'}))[-1]
    assert observed, 'The required native acceptance boundary was not reached'
    assert result['status'] == 'error', result
    assert not result.get('glb')


def test_run_script_exports_real_glb_and_rejects_banned_code(offline_models):
    script = BOX_SOURCE + "cq.exporters.export(result, 'output.glb')\n"
    # GLB requires an Assembly in CadQuery's exporter; use its public save API.
    script = BOX_SOURCE + "assembly = cq.Assembly(result)\nassembly.save('output.glb')\n"
    with TestClient(main.app) as client:
        valid = client.post('/run', json={'script':script}).json()
        invalid = client.post('/run', json={'script':'fn = exec\nfn(\"print(1)\")'}).json()
    assert valid['status'] == 'ok', valid
    assert base64.b64decode(valid['glb'])[:4] == b'glTF'
    assert invalid['status'] == 'error'


def test_refine_reuses_exported_source_without_another_machinist(offline_models, monkeypatch):
    from schemas import ManifestDiff
    monkeypatch.setattr(main, 'run_refiner', AsyncMock(return_value=ManifestDiff()))
    with TestClient(main.app) as client:
        result = client.post('/refine', json={
            'original_manifest':offline_models.model_dump(mode='json'),
            'original_scripts':{'box':BOX_SOURCE},
            'refinement_prompt':'keep unchanged',
        }).json()
    assert result['status'] == 'ok', result
    assert base64.b64decode(result['glb'])[:4] == b'glTF'
    assert result['reused_parts'] == ['box']
    assert result['regenerated_parts'] == []
    agents.run_machinist.assert_not_awaited()


def test_native_constraint_assembly_runs_real_solver(tmp_path):
    import asyncio
    import cadquery as cq
    from schemas import MateType, MatingRule
    paths = {}
    for name in ['base','cap']:
        path = tmp_path / (name+'.step')
        cq.exporters.export(cq.Workplane('XY').box(10, 20, 3), str(path))
        paths[name] = str(path)
    manifest = AssemblyManifest(assembly_name='pair', parts=[
        PartDefinition(part_id='base',description='base',anchor_tags=['>Z']),
        PartDefinition(part_id='cap',description='cap',anchor_tags=['<Z']),
    ], mating_rules=[MatingRule(source_part_id='base',target_part_id='cap',source_anchor='>Z',
        target_anchor='<Z',mate_type=MateType.FASTENED,clearance=0)])
    script = asyncio.run(agents.run_assembly_critic_loop(manifest, paths))
    assert "solve_assembly_checked(assembly)" in script
