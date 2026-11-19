"""U7b: /generate routes registered-capability prompts before any LLM step."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GEMINI_API_KEY_HEAVY', 'offline-fixture-never-sent')

import main
from fastapi.testclient import TestClient
from dragsters.intent import detect
from schemas import AssemblyManifest, PartDefinition

BACKEND = Path(__file__).resolve().parents[1]
PROMPTS = json.loads((BACKEND / 'eval_harness' / 'prompts.json').read_text(encoding='utf-8'))

CASES = [
    ('co2_dragster', 'Design a CO2 dragster with a low wedge body'),
    ('regen_chamber', 'Design a regeneratively cooled rocket thrust chamber with cooling channels'),
    ('glider_cem', 'Design a hand-launched glider that maximizes distance'),
    ('rectangular_cantilever', 'Size a rectangular cantilever beam for a tip force with an allowable stress'),
]
PLAIN = 'A smooth ergonomic shell for a computer mouse, roughly 120mm long'


def events(client, prompt):
    response = client.post('/generate', json={'prompt': prompt})
    assert response.status_code == 200
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.limiter, 'enabled', False)
    monkeypatch.delenv('MIRUM_ACCESS_TOKEN', raising=False)
    with TestClient(main.app) as c:
        yield c


def forbid(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('LLM stage reached for a routed prompt')
    for name in ('run_rea', 'run_planner', 'run_critic_loop', 'run_assembly_critic_loop'):
        if hasattr(main, name):
            monkeypatch.setattr(main, name, boom)


@pytest.mark.parametrize('capability,prompt', CASES)
def test_routed_prompt_emits_one_route_event_and_stops(client, monkeypatch, capability, prompt):
    forbid(monkeypatch)
    got = events(client, prompt)
    assert len(got) == 1
    event = got[0]
    assert event['step'] == 'route' and event['status'] == 'done'
    assert event['capability_id'] == capability
    assert event['workflow'] == capability  # co2_dragster is also the legacy value
    assert event['assurance'] == 'checked_design'
    assert event['reason'] and event['detail']
    assert 'draft' not in event or isinstance(event['draft'], dict)


def test_legacy_dragster_event_keys_unchanged(client, monkeypatch):
    forbid(monkeypatch)
    event = events(client, CASES[0][1])[0]
    assert event['workflow'] == 'co2_dragster'
    assert event['detail'] == 'Routed to the deterministic CO2 dragster workflow'
    assert {'step', 'status', 'workflow', 'detail'} <= set(event)


def test_draft_only_when_rules_proposer_installed(client, monkeypatch):
    forbid(monkeypatch)
    import engineering.proposer as proposer

    class Rules:
        def propose(self, prompt):
            return {'capability': {'id': 'x'}}, (), None
    monkeypatch.setattr(proposer, 'rules_proposer', lambda cid: Rules())
    event = events(client, CASES[1][1])[0]
    assert event['draft']['payload'] == {'capability': {'id': 'x'}}

    def none(cid):
        from engineering.capabilities import UnsupportedCapability
        raise UnsupportedCapability('no rules proposer')
    monkeypatch.setattr(proposer, 'rules_proposer', none)
    assert 'draft' not in events(client, CASES[1][1])[0]


def test_unrouted_prompt_reaches_pipeline_and_complete_carries_assurance(client, monkeypatch):
    seen = []
    manifest = AssemblyManifest(assembly_name='shell', parts=[
        PartDefinition(part_id='shell', description='mouse shell', anchor_tags=['>Z'])], mating_rules=[])

    async def rea(prompt):
        seen.append('rea')
        return None

    async def planner(prompt, requirements=None):
        seen.append('planner')
        return manifest

    async def critic(*a, **k):
        seen.append('machinist')
        raise main.ScriptError('fixture failure', 'broken_script')
    monkeypatch.setattr(main, 'run_rea', rea)
    monkeypatch.setattr(main, 'run_planner', planner)
    monkeypatch.setattr(main, 'run_critic_loop', critic)
    got = events(client, PLAIN)
    assert seen == ['rea', 'planner', 'machinist']
    assert not any(e['step'] == 'route' for e in got)
    complete = [e for e in got if e['step'] == 'complete']
    assert complete and all(e['assurance'] == 'unverified_geometry' for e in complete)


def test_eval_prompts_route_as_before():
    from engineering.router import route
    for item in PROMPTS:
        prompt = item['description']
        decision = route(prompt)
        assert (decision.capability_id == 'co2_dragster') == detect(prompt).is_dragster
        assert decision.capability_id is None, prompt
