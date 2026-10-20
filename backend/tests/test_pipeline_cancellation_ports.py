"""Pipeline ownership regressions: finishing the SSE owner drains siblings."""
import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path
from unittest.mock import AsyncMock

os.environ.setdefault('GEMINI_API_KEY_HEAVY','offline-fixture-never-sent')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
from agents import ScriptError
from schemas import AssemblyManifest, DesignRequirements, MatingRule, PartDefinition


def _configure(monkeypatch):
    manifest = AssemblyManifest(assembly_name='pair',parts=[
        PartDefinition(part_id=name,description=name,anchor_tags=['>Z']) for name in ['base','cap']
    ],mating_rules=[MatingRule(source_part_id='base',target_part_id='cap',source_anchor='>Z',target_anchor='>Z',translation='0, 0, 1',clearance=0)])
    monkeypatch.setattr(main,'run_rea',AsyncMock(return_value=DesignRequirements(
        primary_function='pair',user_facing_insights=['pair'],geometric_constraints=[])))
    monkeypatch.setattr(main,'run_planner',AsyncMock(return_value=manifest))


def test_pipeline_cancellation_drains_both_inflight_part_builds(monkeypatch):
    _configure(monkeypatch)
    async def scenario():
        active=set()
        both_started=asyncio.Event()
        async def build(part,**kwargs):
            active.add(part.part_id)
            if len(active)==2:
                both_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active.remove(part.part_id)
        monkeypatch.setattr(main,'run_critic_loop',build)
        stream=main._pipeline('pair')
        async def consume():
            async for _ in stream:
                pass
        owner=asyncio.create_task(consume())
        await asyncio.wait_for(both_started.wait(),5)
        owner.cancel()
        with suppress(asyncio.CancelledError):
            await owner
        await stream.aclose()
        assert not active, f'unowned part builds survived stream cancellation: {active}'
    asyncio.run(scenario())


def test_failed_part_drains_sibling_before_terminal_error(monkeypatch):
    _configure(monkeypatch)
    async def scenario():
        active=set()
        sibling_started=asyncio.Event()
        async def build(part,**kwargs):
            if part.part_id=='base':
                await sibling_started.wait()
                raise ScriptError('fixture part rejected')
            active.add(part.part_id)
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active.remove(part.part_id)
        monkeypatch.setattr(main,'run_critic_loop',build)
        result=[item async for item in main._pipeline('pair')]
        assert any('error' in item for item in result)
        assert not active, f'unowned sibling survived terminal error: {active}'
    asyncio.run(scenario())


def test_second_disconnect_cancellation_cannot_interrupt_sibling_drain(monkeypatch):
    _configure(monkeypatch)
    async def scenario():
        entered=set()
        cleaning=set()
        cleaned=set()
        both_started=asyncio.Event()
        both_cleaning=asyncio.Event()
        release_cleanup=asyncio.Event()
        async def build(part,**kwargs):
            entered.add(part.part_id)
            if len(entered)==2:
                both_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.add(part.part_id)
                if len(cleaning)==2:
                    both_cleaning.set()
                await release_cleanup.wait()
                cleaned.add(part.part_id)
        monkeypatch.setattr(main,'run_critic_loop',build)
        async def consume():
            async for _ in main._pipeline('pair'):
                pass
        owner=asyncio.create_task(consume())
        await asyncio.wait_for(both_started.wait(),5)
        owner.cancel()
        await asyncio.wait_for(both_cleaning.wait(),5)
        owner.cancel()
        # A second cancellation must not propagate through the join to children
        # already doing cleanup, or let the stream owner abandon that cleanup.
        for _ in range(5):
            await asyncio.sleep(0)
        abandoned=owner.done()
        release_cleanup.set()
        with suppress(asyncio.CancelledError):
            await owner
        assert not abandoned, 'SSE owner abandoned cleanup after repeated cancellation'
        assert cleaned==entered
    asyncio.run(scenario())
