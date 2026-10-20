"""/refine must keep reused-part STEP files inside _WORK_DIR.

part_id comes from the client's original_manifest, so a '../' or quote in it
must not change where the re-export script writes. Model seams are fixtures;
the script is executed for real.
"""
import ast
import glob
import os
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GEMINI_API_KEY_HEAVY', 'offline-fixture-never-sent')

import main
from fastapi.testclient import TestClient
from schemas import AssemblyManifest, ManifestDiff, PartDefinition

BOX_SOURCE = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 3)\n"
HOSTILE_ID = "../../../../tmp/escape'part"


def test_refine_reuse_step_cannot_escape_work_dir(monkeypatch):
    manifest = AssemblyManifest(assembly_name='box', parts=[PartDefinition(
        part_id='box', description='box', anchor_tags=['>Z'])], mating_rules=[])
    payload = manifest.model_dump(mode='json')
    payload['parts'][0]['part_id'] = HOSTILE_ID
    monkeypatch.setattr(main, 'run_refiner', AsyncMock(return_value=ManifestDiff()))
    monkeypatch.setattr(main.limiter, 'enabled', False)
    original_execute, scripts = main.execute_cad_script, []

    async def recording_execute(script):
        scripts.append(script)
        return await original_execute(script)

    monkeypatch.setattr(main, 'execute_cad_script', recording_execute)
    with TestClient(main.app) as client:
        client.post('/refine', json={'original_manifest': payload,
                                     'original_scripts': {HOSTILE_ID: BOX_SOURCE},
                                     'refinement_prompt': 'keep unchanged'})
    reuse = [s for s in scripts if s.startswith(BOX_SOURCE.rstrip())]
    assert reuse, scripts
    work_dir = Path(main._WORK_DIR).resolve()
    for script in reuse:
        targets = re.findall(r"""export\(result, (r?['"].*?\.step['"])\)""", script)
        assert targets, script
        for literal in targets:
            assert Path(ast.literal_eval(literal)).resolve().parent == work_dir, literal
    assert not glob.glob('/tmp/escape*')
