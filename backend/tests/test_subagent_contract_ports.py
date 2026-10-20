"""Exercise real wrappers with the current three-value critic contract."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agents
import compiler
from schemas import AssemblyManifest, PartDefinition
from subagents.machinist import MachinistSubagent
from subagents.messages import SubagentContext
from subagents.orchestrator import Orchestrator, PipelineState


@pytest.fixture
def part():
    return PartDefinition(part_id='box', description='box', anchor_tags=['>Z'])


def test_machinist_wrapper_preserves_geometry_certificate(monkeypatch, part):
    cert = {'passed': True, 'volume_mm3': 1000.0}
    monkeypatch.setattr(agents, 'run_critic_loop', AsyncMock(return_value=('result = 1', '/tmp/box.step', cert)))
    ctx = SubagentContext(extra={'part_def': part})
    msg = asyncio.run(MachinistSubagent().run(ctx))
    assert msg.is_ok, msg.metadata
    assert msg.payload['step_path'] == '/tmp/box.step'
    assert msg.payload['geometry_certificate'] == cert


@pytest.mark.parametrize('count', [1, 2])
def test_orchestrator_machine_stage_accepts_three_value_critic(monkeypatch, part, tmp_path, count):
    parts = [part] if count == 1 else [part, PartDefinition(part_id='cap', description='cap', anchor_tags=['<Z'])]
    manifest = AssemblyManifest(assembly_name='boxes', parts=parts, mating_rules=[])
    monkeypatch.setattr(agents, 'run_critic_loop', AsyncMock(return_value=('result = 1', '/tmp/box.step', {'passed': True})))
    monkeypatch.setattr(agents, 'run_single_part_export', AsyncMock(return_value='result = 1'))
    monkeypatch.setattr(compiler, 'execute_cad_script', AsyncMock(return_value={'status': 'success', 'output': ''}))
    orch = Orchestrator(SubagentContext(prompt='boxes'), work_dir=tmp_path)
    orch._manifest = manifest
    orch._output_filename = str(tmp_path/'result.glb')
    async def run():
        return [event async for event in orch._stage_machine()]
    events = asyncio.run(run())
    assert orch.state != PipelineState.FAILED, events
    assert not any('"status": "error"' in event for event in events), events
    assert len([m for m in orch.ctx.history if m.agent_type == 'machinist' and m.is_ok]) == count
