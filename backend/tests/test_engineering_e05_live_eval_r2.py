"""Offline regression tests for E05 family refusals and public evidence reason codes."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from engineering import family_gate
from engineering.capabilities import GENERATORS

try:
    from backend.tests.test_native_api_hardening_ports import offline_models  # noqa: F401
except ImportError:
    pass


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / 'e05_live_eval.py'
CORPUS_DIR = ROOT / 'backend' / 'tests' / 'fixtures' / 'engineering' / 'proposals'
SHA = 'a' * 40
IMAGE = 'sha256:' + 'b' * 64
CANARY = 'CANARY-e05fix-7f3a'
# rectangular_plate (E03) has no AI path and no E05 corpus.
GENERATOR_IDS = tuple(descriptor.id for descriptor in GENERATORS if descriptor.id != 'rectangular_plate')
FAMILY_PROMPTS = {
    'co2_dragster': ('Design a wedge body for a CO2 dragster.',
                     'Make a teardrop CO2 race car.'),
    'regen_chamber': ('Draw a regeneratively cooled rocket chamber.',
                      'Model a thrust chamber with cooling channels.'),
    'glider_cem': ('Design a hand-launched glider for distance.',
                   'Make a passive glider for maximum flight time.'),
    'rectangular_cantilever': ('Size a rectangular cantilever beam.',
                               'Check the tip load on a rectangular cantilever beam.'),
}
AMBIGUOUS = ('Design a CO2 dragster and a hand-launched glider for distance.',
             'Size a rectangular cantilever beam and model a thrust chamber with cooling channels.')
NO_MATCH = ('Design a coffee mug.', 'Make a bookshelf.')


def other_family_prompts(requested_id):
    return [prompt for family, prompts in FAMILY_PROMPTS.items()
            if family != requested_id for prompt in prompts]


REFUSALS = [
    (capability, prompt, state, reason, reason_id)
    for capability in GENERATOR_IDS
    for prompts, state, reason, reason_id in (
        (other_family_prompts(capability), 'unsupported', family_gate.OTHER_FAMILY, 'unsupported.family'),
        (AMBIGUOUS, 'clarification_required', family_gate.AMBIGUOUS, 'clarification.ambiguous'),
    )
    for prompt in prompts
]


def load_script():
    spec = importlib.util.spec_from_file_location('e05_live_eval', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e05 = load_script()


def case(cid, kind='complete', expected_state='proposed', expected=None, prompt='p'):
    return dict(id=cid, kind=kind, expected_state=expected_state, prompt=prompt,
                expected_content=expected if expected is not None else {})


def proposal(state='proposed', payload=None, origins=()):
    return SimpleNamespace(state=state, payload=payload if payload is not None else {}, issues=(), reasons=(),
                           provenance=SimpleNamespace(field_origins=[SimpleNamespace(**o) for o in origins]))


def fake_budget():
    budget = Mock(spec=e05.CountingBudget, denied=0)
    budget.used.return_value = 0
    budget.has_capacity.return_value = True
    return budget


def assert_reason(record, expected):
    reason = record['reason']
    assert reason == expected
    if record['state'] == 'proposed':
        assert reason is None
    else:
        assert reason in e05.REASON_IDS or e05.EXCEPTION_REASON.fullmatch(reason)


@pytest.mark.parametrize(('capability', 'prompt', 'state', 'reason', 'reason_id'), REFUSALS)
def test_family_gate_refuses(capability, prompt, state, reason, reason_id):
    assert family_gate.family_gate(prompt, capability) == family_gate.GateRefusal(state, reason, reason_id)


@pytest.mark.parametrize('capability', GENERATOR_IDS)
def test_family_gate_accepts_own_family_and_no_match(capability):
    cases = e05.load_corpora(CORPUS_DIR)[capability]
    for item in cases:
        if item['expected_route'] == capability:
            assert family_gate.family_gate(item['prompt'], capability) is None, item['id']
    for prompt in NO_MATCH:
        assert family_gate.family_gate(prompt, capability) is None


@pytest.mark.parametrize('capability', GENERATOR_IDS)
def test_harness_gates_real_corpus_before_proposer(capability):
    cases = e05.load_corpora(CORPUS_DIR)[capability]
    spy = AsyncMock(return_value=proposal())
    budget = fake_budget()
    result = e05.run_capability(capability, cases, SimpleNamespace(propose=spy), budget)
    assert result['stop'] == 'completed'
    records = result['cases']
    assert [record['case_id'] for record in records] == [item['id'] for item in cases]
    expected_calls = []
    for item, record in zip(cases, records, strict=True):
        if item['expected_route'] in (None, capability):
            expected_calls.append(call(item['prompt']))
            assert record['state'] == 'proposed', item['id']
            assert_reason(record, None)
        else:
            assert record['state'] == 'unsupported', item['id']
            assert_reason(record, 'unsupported.family')
    assert spy.await_args_list == expected_calls
    budget.reserve.assert_not_called()
    if capability == 'co2_dragster':
        states = {record['case_id']: record['state'] for record in records}
        assert all(states[cid] == 'unsupported' for cid in ('D07', 'D08', 'D09'))


def test_harness_scores_the_shared_gate_refusal(monkeypatch):
    item = case('gate', expected={'specification.length_mm': 1}, expected_state='clarification_required')
    refusal = family_gate.GateRefusal('clarification_required', family_gate.AMBIGUOUS, 'clarification.ambiguous')
    gate = Mock(return_value=refusal)
    monkeypatch.setattr(family_gate, 'family_gate', gate)
    harness = load_script()
    spy = AsyncMock(return_value=proposal())
    budget = fake_budget()
    result = harness.run_capability('glider_cem', [item], SimpleNamespace(propose=spy), budget)
    gate.assert_called_once_with(item['prompt'], 'glider_cem')
    spy.assert_not_called()
    budget.reserve.assert_not_called()
    record, = result['cases']
    assert record['state'] == 'clarification_required'
    assert_reason(record, 'clarification.ambiguous')
    assert record['field_mismatches'] == 1
    assert record['unstated_ai_values'] == 0
    assert record['outcome'] == 'expected_block'


MISSING_ID = object()


@pytest.mark.parametrize(('state', 'reason_id', 'expected'), [
    ('proposed', 'unsupported.family', None),
    ('unavailable', MISSING_ID, 'unavailable.unclassified'),
    ('unavailable', 'unavailable.timeout', 'unavailable.timeout'),
    ('clarification_required', '', 'clarification.unclassified'),
    ('clarification_required', 'Please specify a dimension', 'clarification.unclassified'),
    ('unsupported', 'unsupported.invented', 'unsupported.unclassified'),
    ('unsupported', 'unsupported.feature', 'unsupported.feature'),
    ('cancelled', None, 'unavailable.unclassified'),
])
def test_harness_maps_reason_id(state, reason_id, expected):
    reply = proposal(state=state)
    if reason_id is not MISSING_ID:
        reply.reason_id = reason_id
    spy = AsyncMock(return_value=reply)
    result = e05.run_capability('co2_dragster', [case('reason')], SimpleNamespace(propose=spy), fake_budget())
    record, = result['cases']
    assert record['state'] == state
    assert_reason(record, expected)


def run_dragster_evidence(tmp_path, spy):
    evidence = e05.run(tmp_path, ('co2_dragster',), sha=SHA, image_id=IMAGE, run_id='test-r2',
                       authorization='owner-test', corpus_dir=CORPUS_DIR, budget_factory=fake_budget,
                       proposer_factory=lambda capability, budget: SimpleNamespace(propose=spy))
    text = (tmp_path / 'e05-live.json').read_text(encoding='utf-8')
    assert CANARY not in text
    assert CANARY not in json.dumps(evidence)
    saved = json.loads(text)
    assert saved == evidence
    return saved['capabilities']['co2_dragster']


@pytest.mark.parametrize(('error', 'stop'), [
    (RuntimeError, 'provider_error:RuntimeError'),
    (e05.NativeExecutionAttempted, 'native_execution_attempted'),
])
def test_exception_records_failing_case_and_omits_message(tmp_path, error, stop):
    first = e05.load_corpora(CORPUS_DIR)['co2_dragster'][0]
    spy = AsyncMock(side_effect=error(CANARY))
    result = run_dragster_evidence(tmp_path, spy)
    spy.assert_awaited_once_with(first['prompt'])
    assert result['stop'] == stop
    record, = result['cases']
    expected = dict(case_id=first['id'], kind=first['kind'], expected_state=first['expected_state'],
                    state='error', state_as_expected=False, field_mismatches=0, unstated_ai_values=0,
                    accurate_reviewable_draft=False, silent_requirement_drop=False, outcome='exception')
    assert {key: record[key] for key in expected} == expected
    assert_reason(record, f'exception:{error.__name__}')


def test_unavailable_evidence_omits_model_and_issue_text(tmp_path):
    reply = proposal(state='unavailable')
    reply.reason_id = CANARY
    reply.reasons = (CANARY,)
    reply.issues = (SimpleNamespace(field='request', state='unavailable', reason=CANARY),)
    result = run_dragster_evidence(tmp_path, AsyncMock(return_value=reply))
    cases = e05.load_corpora(CORPUS_DIR)['co2_dragster']
    assert result['stop'] == 'completed'
    for item, record in zip(cases, result['cases'], strict=True):
        assert record['case_id'] == item['id']
        if item['expected_route'] == 'co2_dragster':
            assert record['state'] == 'unavailable'
            assert_reason(record, 'unavailable.unclassified')
        else:
            assert record['state'] == 'unsupported'
            assert_reason(record, 'unsupported.family')


def test_api_uses_shared_family_gate():
    source = (ROOT / 'backend' / 'engineering' / 'api_v1.py').read_text(encoding='utf-8')
    assert 'family_gate(' in source
    assert 'routed.reason.startswith' not in source


@pytest.fixture
def ai_client(request, tmp_path, monkeypatch):
    TestClient = pytest.importorskip('fastapi.testclient').TestClient
    from backend.tests.test_native_api_hardening_ports import main
    request.getfixturevalue('offline_models')
    import config

    monkeypatch.setattr(config, 'ENGINEERING_AI_DRAFTS', True)
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'engineering'))
    with TestClient(main.app) as client:
        main.app.state.limiter.reset()
        yield client


@pytest.mark.parametrize(('capability', 'prompt', 'state', 'reason', 'reason_id'), REFUSALS)
def test_api_and_harness_refuse_before_ai(ai_client, monkeypatch, capability, prompt, state, reason, reason_id):
    from engineering import proposal_contract

    ai_factory = Mock(side_effect=AssertionError('Family gate reached the AI path'))
    monkeypatch.setattr(proposal_contract, 'ai_proposer', ai_factory)
    response = ai_client.post('/engineering/v1/drafts', json=dict(
        capability=capability, mode='ai', prompt=prompt, input={}))
    ai_factory.assert_not_called()
    assert response.status_code == 201, response.text
    spy = AsyncMock(return_value=proposal())
    result = e05.run_capability(capability, [case('parity', prompt=prompt)], SimpleNamespace(propose=spy), fake_budget())
    spy.assert_not_called()
    record, = result['cases']
    assert record['state'] == state
    assert_reason(record, reason_id)
    issues = [issue for issue in response.json()['issues'] if issue['field'] == 'request']
    assert (record['state'], reason) in [(issue['state'], issue['reason']) for issue in issues]


# --- D8: owner-authorized round 2, caps 18/19/18 --------------------------------------------------
ROUND2 = {'co2_dragster': 18, 'regen_chamber': 19, 'glider_cem': 18}


def test_round2_caps_are_exact():
    assert e05.ROUND2_CAPS == ROUND2


@pytest.mark.parametrize('extra', [[], ['--round', '1'], ['--round', '3']])
def test_main_refuses_without_round_2_before_any_load(extra, monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError('must not load or build anything')

    monkeypatch.setenv('MIRUM_ENGINEERING_AI_DRAFTS', '0')
    monkeypatch.setattr(e05, 'load_corpora', boom)
    monkeypatch.setattr(e05, 'build_proposer', boom)
    monkeypatch.setattr(e05, 'load_credential', boom)
    with pytest.raises(SystemExit) as exit_info:
        e05.main([str(tmp_path / 'out'), 'glider_cem', SHA, str(tmp_path / 'cred'),
                  '--run-id', 'r1', '--authorization', 'owner-test', '--image-id', IMAGE, *extra])
    assert exit_info.value.code == 2


@pytest.mark.parametrize('bad', [1, 3, 0, True, '2', None])
def test_run_refuses_other_rounds_before_any_call(bad, monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError('must not load or call anything')

    monkeypatch.setattr(e05, 'load_corpora', boom)
    with pytest.raises(ValueError):
        e05.run(tmp_path, ('co2_dragster',), sha=SHA, image_id=IMAGE, run_id='test-r2', authorization='owner-test',
                proposer_factory=boom, round_number=bad)
    assert not (tmp_path / 'e05-live.json').exists()


class ReservingProposer:
    def __init__(self, budget):
        self.budget = budget
        self.reserved = 0

    async def propose(self, prompt, base_spec=None):
        for _ in range(2):  # a call plus a repair, like the real proposers; in flight is released each time
            if self.budget.reserve():
                self.reserved += 1
                self.budget.release()
        return proposal(state='clarification_required', payload={'specification': {}})


@pytest.mark.parametrize('capability', sorted(ROUND2))
def test_default_budget_is_the_round2_cap_and_stops_exactly_there(capability, monkeypatch, tmp_path):
    seen = []
    real = e05.CountingBudget

    def spy_budget(*args, **kwargs):
        budget = real(*args, **kwargs)
        seen.append((args, kwargs, budget))
        return budget

    proposers = []

    def factory(cap, budget):
        proposer = ReservingProposer(budget)
        proposers.append(proposer)
        return proposer

    monkeypatch.setattr(e05, 'CountingBudget', spy_budget)
    monkeypatch.setattr(e05.family_gate if hasattr(e05, 'family_gate') else family_gate, 'family_gate',
                        lambda prompt, cap: None)
    evidence = e05.run(tmp_path, (capability,), sha=SHA, image_id=IMAGE, run_id='test-r2',
                       authorization='owner-20261120-e05r2', corpus_dir=CORPUS_DIR,
                       proposer_factory=factory, pace_seconds=0)
    assert [args for args, kwargs, _ in seen] == [(ROUND2[capability],)]
    result = evidence['capabilities'][capability]
    assert result['budget_cap'] == ROUND2[capability]
    assert result['calls'] == proposers[0].reserved == ROUND2[capability]
    assert result['stop'] == 'budget_exhausted' and result['budget_exhausted'] is True
    assert evidence['round'] == 2
    assert evidence['call_caps'] == {capability: ROUND2[capability]}
    assert evidence['authorization'] == 'owner-20261120-e05r2'
    assert 'call_cap_per_capability' not in evidence
    assert json.loads((tmp_path / 'e05-live.json').read_text(encoding='utf-8')) == evidence


# T9 (E05 fix loop) corpus self-check: a fake provider that answers each case with its own fixture_response
# (the corpus's expected model reply) drives the REAL proposers through the harness. Every complete case in the
# pinned corpora (v2 where present) must stay proposed and accurate after grounding (D3/D4/D5), so the fixes
# cannot break the run-1 passes. Offline: no key, no network.
class _PromptKeyedProvider:
    """GeminiProposalProvider stand-in: replies with the fixture of the case whose prompt is in the request."""
    available = True

    def __init__(self, cases):
        self._replies = {}
        for item in cases:
            fixture = item['fixture_response']
            replies = fixture if isinstance(fixture, list) else [fixture]
            self._replies[json.dumps(item['prompt'], ensure_ascii=False)] = [
                r if isinstance(r, str) else json.dumps(r) for r in replies]

    def settings(self):
        from engineering.proposer import ProviderSettings
        return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture-model',
                                temperature=0, max_tokens=1024)

    async def generate(self, prompt, schema, settings):
        from engineering.proposer import ProviderReply
        key = max((k for k in self._replies if k in prompt), key=len)   # the request embeds the JSON prompt
        return ProviderReply(text=self._replies[key].pop(0))


def _self_check_proposer(capability, cases, budget):
    import copy
    if capability == 'co2_dragster':
        from engineering import dragster_proposer
        by_prompt = {item['prompt']: item['fixture_response'] for item in cases}

        async def interpret(prompt, base_spec=None):
            return copy.deepcopy(by_prompt[prompt])
        return dragster_proposer.AiProposer(budget, interpret)
    if capability == 'regen_chamber':
        from engineering import regen_proposer
        return regen_proposer.AiProposer(budget, _PromptKeyedProvider(cases))
    from engineering import glider_proposer
    return glider_proposer.AiProposer(budget, _PromptKeyedProvider(cases))


@pytest.mark.parametrize('capability', e05.CAPABILITIES)
def test_t9_corpus_self_check_complete_cases_stay_accurate(capability):
    cases = e05.load_corpora(CORPUS_DIR)[capability]   # the pinned files the live run will use
    budget = e05.CountingBudget(e05.ROUND2_CAPS[capability])
    result = e05.run_capability(capability, cases, _self_check_proposer(capability, cases, budget), budget,
                                budget_cap=e05.ROUND2_CAPS[capability])
    assert result['stop'] == 'completed'
    assert result['calls'] <= e05.ROUND2_CAPS[capability]
    complete = [r for r in result['cases'] if r['kind'] == 'complete']
    assert len(complete) >= 3
    for record in complete:
        assert record['state'] == 'proposed', record['case_id']
        assert record['accurate_reviewable_draft'], record['case_id']
        assert record['unstated_ai_values'] == 0, record['case_id']
    assert sum(r['silent_requirement_drop'] for r in result['cases']) == 0
    for record in result['cases']:
        assert record['reason'] is None if record['state'] == 'proposed' else record['reason'] in e05.REASON_IDS
    # With every case answered by its own fixture reply, the whole capability passes the unchanged gate.
    assert all(r['unstated_ai_values'] == 0 for r in result['cases']), \
        [r['case_id'] for r in result['cases'] if r['unstated_ai_values']]
    assert e05.gate(result) != 'blocked'


def test_t9_harness_loads_glider_v2_and_keeps_v1_pins():
    assert e05.CORPUS_FILES == {'co2_dragster': 'co2_dragster-v1.json', 'regen_chamber': 'regen_chamber-v2.json',
                                'glider_cem': 'glider_cem-v2.json'}
    import hashlib
    for capability, name in e05.CORPUS_FILES.items():
        assert hashlib.sha256((CORPUS_DIR / name).read_bytes()).hexdigest() == e05.CORPUS_SHA256[capability]
    # Run 1 cited these v1 files by SHA; they must never change.
    assert hashlib.sha256((CORPUS_DIR / 'glider_cem-v1.json').read_bytes()).hexdigest() == \
        'c4113bb1334fd0951082efe0141120b6615dc05bbed4433afce1310c9a8db3a3'
    assert hashlib.sha256((CORPUS_DIR / 'regen_chamber-v1.json').read_bytes()).hexdigest() ==         '95b161ecc66b9a8a2475d967e4dc7259bbf4db8df00597d1233d3c3bc3ecc397'


def test_regen_v2_differs_from_v1_only_in_r05_r13_expected_content():
    v1 = json.loads((CORPUS_DIR / 'regen_chamber-v1.json').read_text(encoding='utf-8'))
    v2 = json.loads((CORPUS_DIR / 'regen_chamber-v2.json').read_text(encoding='utf-8'))
    assert v2.pop('changes_from_v1') and v2.pop('version') == 'e05-regen_chamber-v2'
    v1.pop('version')
    cases1 = {c['id']: c for c in v1.pop('cases')}
    cases2 = {c['id']: c for c in v2.pop('cases')}
    assert v1 == v2 and cases1.keys() == cases2.keys()
    for case_id, case in cases1.items():
        other = dict(cases2[case_id])
        if case_id == 'R05':
            assert other.pop('expected_content') == {'specification.throat_radius_mm': 40.0}
            assert '40 mm' in case['prompt']
            case = {k: v for k, v in case.items() if k != 'expected_content'}
        elif case_id == 'R13':
            assert other.pop('expected_content') == {'specification.channel_count': 300}
            assert '300 coolant channels' in case['prompt']
            case = {k: v for k, v in case.items() if k != 'expected_content'}
        assert case == other, case_id


@pytest.mark.parametrize('where', ['run', 'load_credential'])
def test_main_prints_only_the_class_name_when_run_raises(where, monkeypatch, tmp_path, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError(CANARY)

    monkeypatch.setenv('MIRUM_ENGINEERING_AI_DRAFTS', '0')
    monkeypatch.setattr(e05, 'load_credential', lambda path: None)
    monkeypatch.setattr(e05, where, boom)
    code = e05.main([str(tmp_path / 'out'), 'glider_cem', SHA, str(tmp_path / 'cred'),
                     '--run-id', 'r1', '--authorization', 'owner-test', '--image-id', IMAGE,
                     '--round', '2'])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.strip() == 'e05 harness failed: RuntimeError'
    assert CANARY not in captured.err and CANARY not in captured.out
    assert 'Traceback' not in captured.err
