"""Regressions from the phase-1 API/UI review (real CAD, offline model seams).

1. Multi-part aero slices the wing component, not the whole positioned assembly.
2. Single-part Domain F keeps the requested propulsion metadata on the evidence.
3. The inspector's solid budget covers the planner's 50-part manifest limit.
"""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

os.environ.setdefault('GEMINI_API_KEY_HEAVY', 'offline-fixture-never-sent')
import main
from schemas import AssemblyManifest, DesignRequirements, PartDefinition

from backend.tests.test_native_api_hardening_ports import events, offline_models  # noqa: F401
from backend.tests.test_solid_set_acceptance import box, compound, inspector, step

CERT = dict(passed=True, volume_mm3=1000., surface_area_mm2=600., failures=[])
FINAL = ("import cadquery as cq\n"
         "assembly = cq.Assembly(name='plane')\n"
         "assembly.add(cq.Workplane('XY').box(10, 10, 10), name='fuselage')\n"
         "assembly.add(cq.Workplane('XY').box(10, 10, 10).translate((30, 0, 0)), name='wing')\n"
         "assembly.save('output.glb')\n")


def test_multipart_aero_slices_the_wing_component_step(monkeypatch, tmp_path):
    import analysis.airfoil as airfoil

    manifest = AssemblyManifest(assembly_name='plane', parts=[
        PartDefinition(part_id=name, description=name, anchor_tags=['>Z'])
        for name in ('fuselage', 'wing')], mating_rules=[])
    monkeypatch.setattr(main, 'run_rea', AsyncMock(return_value=SimpleNamespace(
        inferred_domain='D', airfoil_spec=SimpleNamespace(naca_code='0012', chord_mm=10, span_mm=10),
        flight_env=None, propulsion_spec=None, material_class=None,
        user_facing_insights=[], geometric_constraints=[])))
    monkeypatch.setattr(main, 'run_planner', AsyncMock(return_value=manifest))
    part_steps = {}

    async def build(part, **kwargs):
        path = tmp_path / f'{part.part_id}.step'
        path.write_bytes(b'per-part fixture')
        part_steps[part.part_id] = str(path)
        return 'fixture per-part source', str(path), dict(CERT)

    monkeypatch.setattr(main, 'run_critic_loop', build)
    monkeypatch.setattr(main, 'run_assembly_critic_loop', AsyncMock(return_value=FINAL))
    sliced = []

    def analyze(step_path, naca_code, span_mm, chord_mm, flight_env):
        sliced.append(step_path)
        return dict(naca_code=naca_code, chord_mm=chord_mm, reynolds=500000, mach=0.0,
                    n_crit=9.0, alpha=[0.0], Cl=[0.0], Cd=[0.01], Cm=[0.0], L_D=[0.0],
                    converged_count=1, stall_alpha=None, Cl_max=0.0)

    monkeypatch.setattr(airfoil, 'analyze_wing_step', Mock(side_effect=analyze))

    async def collect():
        return [json.loads(item[6:]) async for item in main._pipeline('plane', run_aero=True)]

    terminal = asyncio.run(collect())[-1]
    assert terminal['status'] == 'done', terminal
    assert sliced == [part_steps['wing']]


def test_single_part_propulsion_metadata_survives_acceptance(offline_models, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from propulsion.specs import PropulsionSpec

    monkeypatch.setattr(main, 'run_rea', AsyncMock(return_value=DesignRequirements(
        primary_function='nozzle', propulsion_spec=PropulsionSpec(
            propulsion_type='nozzle', throat_diameter_mm=40.0, exit_diameter_mm=130.0))))
    with TestClient(main.app) as client:
        result = events(client.post('/generate', json={'prompt': 'fixture nozzle'}))[-1]
    assert result['status'] == 'done', result
    metadata = result['geometry_certificate']['requested_spec_metadata']
    assert metadata['geometric_expansion_ratio'] == pytest.approx(10.5625)


def test_solid_budget_covers_the_manifest_part_limit(tmp_path):
    import importlib
    module = importlib.import_module('backend.verification.solid_set')
    assert module.MAX_SOLIDS >= 50
    result = inspector()(step(tmp_path, compound(*(box(i * 20) for i in range(50)))))
    assert result['passed'], result
    assert result['solid_count'] == 50
