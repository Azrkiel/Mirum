"""Multi-part delivery requires explicit passing certificates for every unique part.

Model/part/assembly seams are fixtures; final GLB compilation and pipeline/SSE
handling are real. These tests target certificate aggregation, not mate quality.
"""
import asyncio
import json
import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault('GEMINI_API_KEY_HEAVY', 'offline-fixture-never-sent')
import main
from pydantic import ValidationError
from schemas import AssemblyManifest, DesignRequirements, MatingRule, PartDefinition

CERT = dict(passed=True, volume_mm3=1., surface_area_mm2=6., failures=[])
FINAL = "import cadquery as cq\nassembly = cq.Assembly(cq.Workplane('XY').box(1,1,1))\nassembly.save('output.glb')\n"


@pytest.mark.parametrize('variant', ['valid', 'false', 'missing', 'truthy', 'unavailable', 'duplicate'])
def test_multipart_required_certificates_are_strict_and_complete(monkeypatch, variant):
    if variant == 'duplicate':
        # Duplicate ids are rejected at the typed manifest boundary, before
        # any part is built or certificates are aggregated.
        with pytest.raises(ValidationError, match='Duplicate part_id'):
            AssemblyManifest(assembly_name='pair',parts=[
                PartDefinition(part_id='base',description='base',anchor_tags=['>Z']) for _ in range(2)
            ],mating_rules=[])
        return
    names = ['base','cap']
    manifest = AssemblyManifest(assembly_name='pair',parts=[
        PartDefinition(part_id=name,description=name,anchor_tags=['>Z']) for name in names
    ],mating_rules=[MatingRule(source_part_id='base',target_part_id='cap',source_anchor='>Z',target_anchor='>Z',translation='0, 0, 1',clearance=0)])
    monkeypatch.setattr(main,'run_rea',AsyncMock(return_value=DesignRequirements(
        primary_function='pair',user_facing_insights=['pair'],geometric_constraints=[])))
    monkeypatch.setattr(main,'run_planner',AsyncMock(return_value=manifest))
    async def build(part, **kwargs):
        cert = dict(CERT)
        if part.part_id == 'cap':
            if variant == 'false': cert['passed'] = False
            if variant == 'truthy': cert['passed'] = 'true'
            if variant in ['missing','unavailable']: cert = None
        return "fixture per-part source", '/tmp/unused-'+part.part_id+'.step', cert
    monkeypatch.setattr(main,'run_critic_loop',build)
    monkeypatch.setattr(main,'run_assembly_critic_loop',AsyncMock(return_value=FINAL))
    if variant == 'unavailable':
        import verification.geometry as geometry
        monkeypatch.delattr(geometry, 'certify_geometry')
    async def collect():
        return [json.loads(item[6:]) async for item in main._pipeline('pair')]
    terminal = asyncio.run(collect())[-1]
    if variant == 'valid':
        assert terminal['status'] == 'done', terminal
        assert terminal['geometry_certificate']['passed'] is True
        assert terminal['geometry_certificate']['part_count'] == 2
    else:
        assert terminal['status'] == 'error', terminal
        assert 'glb' not in terminal
