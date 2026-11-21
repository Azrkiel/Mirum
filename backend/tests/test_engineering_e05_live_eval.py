"""Offline tests for scripts/e05_live_eval.py: fake providers only, no network, no key."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / 'e05_live_eval.py'
CORPUS_DIR = ROOT / 'backend' / 'tests' / 'fixtures' / 'engineering' / 'proposals'
SHA = 'a' * 40
IMAGE = 'sha256:' + 'b' * 64
SENTINEL = 'SENTINEL-RAW-REPLY-7731'


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


class FixedProposer:
    def __init__(self, proposals):
        self.proposals = list(proposals)

    async def propose(self, prompt, base_spec=None):
        return self.proposals.pop(0)


def test_cap_stops_at_30_with_always_repair_provider():
    """Real regen AiProposer: a provider that always returns unparseable text needs 2 units per case."""
    from engineering.proposer import ProviderReply, ProviderSettings
    from engineering.regen_proposer import AiProposer

    class AlwaysRepair:
        available = True
        calls = 0

        def settings(self):
            return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture-model',
                                    temperature=0, max_tokens=1024)

        async def generate(self, prompt, schema, settings):
            self.calls += 1
            return ProviderReply(text='not json')

    provider = AlwaysRepair()
    corpus = json.loads((CORPUS_DIR / 'regen_chamber-v1.json').read_text(encoding='utf-8'))['cases']
    prompt = next(c['prompt'] for c in corpus if c['id'] == 'R01')
    cases = [case(f'X{i:02d}', prompt=prompt, expected={'specification.chamber_length_mm': 44.0})
             for i in range(20)]
    budget = e05.CountingBudget(e05.CALL_CAP)
    result = e05.run_capability('regen_chamber', cases, AiProposer(budget, provider), budget)
    assert provider.calls == e05.CALL_CAP == 30
    assert budget.used() == 30
    assert result['budget_exhausted'] is True
    assert result['stop'] == 'budget_exhausted'
    assert len(result['cases']) == 15  # 15 cases x 2 units exactly fill the cap; case 16 is denied
    assert result['calls'] == 30
    assert e05.gate(result) == 'blocked'


def test_corpus_tamper_is_refused(tmp_path):
    for name in e05.CORPUS_FILES.values():
        shutil.copy(CORPUS_DIR / name, tmp_path / name)
    with open(tmp_path / 'regen_chamber-v1.json', 'a', encoding='utf-8') as handle:
        handle.write(' ')
    with pytest.raises(e05.CorpusPinMismatch):
        e05.load_corpora(tmp_path)


def test_real_corpora_match_their_pins():
    loaded = e05.load_corpora(CORPUS_DIR)
    assert set(loaded) == set(e05.CAPABILITIES)
    assert all(loaded[c] for c in loaded)


def test_silent_drop_on_changed_value_and_unstated_ai_value_is_scored():
    stated = case('S1', expected={'specification.length_mm': 210.0})
    changed = proposal(payload={'specification': {'length_mm': 200.0}},
                       origins=[dict(field='specification.length_mm', origin='ai_proposed', reference='r')])
    record = e05.score_case(stated, changed)
    assert record['field_mismatches'] == 1
    assert record['silent_requirement_drop'] is True
    assert record['accurate_reviewable_draft'] is False

    unstated = case('S2', expected={'specification.length_mm': 210.0})
    extra = proposal(payload={'specification': {'length_mm': 210.0, 'width_mm': 42.0}},
                     origins=[dict(field='specification.width_mm', origin='ai_proposed', reference='r')])
    record = e05.score_case(unstated, extra)
    assert record['unstated_ai_values'] == 1
    assert record['accurate_reviewable_draft'] is False


def test_correct_complete_case_is_accurate_and_expected_block_is_not_silent():
    good = case('G1', expected={'specification.length_mm': 210.0})
    record = e05.score_case(good, proposal(payload={'specification': {'length_mm': 210.0}},
                                           origins=[dict(field='specification.length_mm', origin='ai_proposed', reference='r')]))
    assert record['accurate_reviewable_draft'] is True
    assert e05.outcome_label(record) == 'complete_accurate'

    ambiguous = case('A1', kind='ambiguous', expected_state='clarification_required')
    record = e05.score_case(ambiguous, proposal(state='clarification_required'))
    assert record['silent_requirement_drop'] is False
    assert e05.outcome_label(record) == 'expected_block'


def test_gate_needs_every_complete_case_accurate_and_zero_silent_drops():
    def result(records, unreviewed=0):
        return dict(cases=records, cases_expected=len(records), stop='completed', unreviewed_executions=unreviewed)

    ok = [dict(kind='complete', accurate_reviewable_draft=True, silent_requirement_drop=False, unstated_ai_values=0),
          dict(kind='unsupported', accurate_reviewable_draft=False, silent_requirement_drop=False, unstated_ai_values=0)]
    assert e05.gate(result(ok)) == 'passed'
    assert e05.gate(result(ok, unreviewed=1)) == 'blocked'
    bad_complete = [dict(ok[0], accurate_reviewable_draft=False)] + ok[1:]
    assert e05.gate(result(bad_complete)) == 'blocked'
    silent = ok + [dict(kind='unsupported', accurate_reviewable_draft=False, silent_requirement_drop=True,
                        unstated_ai_values=0)]
    assert e05.gate(dict(result(silent), cases_expected=3)) == 'blocked'


def test_native_execution_is_counted_and_blocked():
    def spawns(prompt):
        subprocess.Popen(['echo', 'x'])

    class Spawning:
        async def propose(self, prompt, base_spec=None):
            spawns(prompt)

    budget = e05.CountingBudget(e05.CALL_CAP)
    result = e05.run_capability('glider_cem', [case('N1')], Spawning(), budget)
    assert result['unreviewed_executions'] == 1
    assert result['stop'] == 'native_execution_attempted'
    assert e05.gate(result) == 'blocked'


def test_evidence_contains_ids_states_and_counts_only(tmp_path):
    prompt_text = 'request ' + SENTINEL

    def factory(capability, budget):
        p = proposal(state='clarification_required', payload={'specification': {}})
        p.reasons = (SENTINEL,)
        return FixedProposer([p] * 13)

    def fixed_budget():
        return e05.CountingBudget(e05.CALL_CAP)

    corpus_dir = tmp_path / 'corpus'
    corpus_dir.mkdir()
    for name in e05.CORPUS_FILES.values():
        shutil.copy(CORPUS_DIR / name, corpus_dir / name)
    cases_in = json.loads((corpus_dir / 'regen_chamber-v1.json').read_text(encoding='utf-8'))
    for c in cases_in['cases']:
        c['prompt'] = prompt_text
    (corpus_dir / 'regen_chamber-v1.json').write_text(json.dumps(cases_in), encoding='utf-8')
    # Re-pin the modified corpus so the run proceeds; the pin itself is tested separately.
    import hashlib
    e05.CORPUS_SHA256['regen_chamber'] = hashlib.sha256((corpus_dir / 'regen_chamber-v1.json').read_bytes()).hexdigest()
    try:
        evidence = e05.run(tmp_path / 'out', ('regen_chamber',), sha=SHA, image_id=IMAGE, run_id='run-test-01',
                           authorization='owner-test', corpus_dir=corpus_dir,
                           proposer_factory=factory, budget_factory=fixed_budget)
    finally:
        e05.CORPUS_SHA256['regen_chamber'] = '95b161ecc66b9a8a2475d967e4dc7259bbf4db8df00597d1233d3c3bc3ecc397'
    text = (tmp_path / 'out' / 'e05-live.json').read_text(encoding='utf-8')
    assert SENTINEL not in text
    assert 'request' not in text
    assert evidence['capabilities']['regen_chamber']['calls'] == 0
    for key in ('run_id', 'authorization', 'tested_implementation_sha', 'image_id', 'corpus_sha256'):
        assert key in evidence


def test_credential_refuses_a_set_pro_key(tmp_path, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY_HEAVY', raising=False)
    monkeypatch.setenv('GEMINI_API_KEY_PRO', 'set')
    cred = tmp_path / 'provider.env'
    cred.write_text('GEMINI_API_KEY_HEAVY=abc123\n', encoding='utf-8')
    with pytest.raises(ValueError):
        e05.load_credential(cred)


def test_budget_is_per_capability_and_cannot_be_refunded():
    budget = e05.CountingBudget(2)
    assert budget.reserve()
    budget.release()  # in flight is released after each call; the call's unit stays spent
    assert budget.reserve()
    budget.release()
    assert budget.reserve() is False
    assert budget.denied == 1
    assert budget.used() == 2


def test_harness_has_no_legacy_client_and_main_never_imports_google_generativeai(tmp_path, monkeypatch):
    """The glider uses the engineering provider (D5); the harness must not configure the legacy
    google.generativeai global client any more."""
    import sys
    assert not hasattr(e05, 'configure_legacy_client')
    imported = []
    real_import = __import__

    def spy(name, *args, **kwargs):
        if name.startswith('google.generativeai'):
            imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr('builtins.__import__', spy)
    monkeypatch.delitem(sys.modules, 'google.generativeai', raising=False)
    monkeypatch.setenv('MIRUM_ENGINEERING_AI_DRAFTS', '0')  # main() sets it; restored on teardown
    monkeypatch.setattr(e05, 'load_credential', lambda path: 'GEMINI_API_KEY_HEAVY')
    monkeypatch.setattr(e05, 'run', lambda *a, **k: dict(qualification='passed'))
    code = e05.main([str(tmp_path / 'out'), 'glider_cem', 'a' * 40, str(tmp_path / 'cred'),
                     '--run-id', 'r1', '--authorization', 'owner-test', '--image-id', 'img', '--round', '2'])
    assert code == 0 and imported == []


def test_evidence_is_on_disk_for_finished_capabilities_when_a_later_one_crashes(tmp_path):
    """#110 review M3: spend for capability 1 must be recorded even if capability 2 fails."""
    def factory(capability, budget):
        if capability == 'regen_chamber':
            raise RuntimeError('provider construction failed SECRET-ISH-DETAIL')
        p = proposal(state='clarification_required', payload={'specification': {}})
        p.reasons = ('x',)
        return FixedProposer([p] * 40)

    with pytest.raises(RuntimeError):
        e05.run(tmp_path / 'out', ('co2_dragster', 'regen_chamber'), sha=SHA, image_id=IMAGE,
                run_id='run-test-02', authorization='owner-test', proposer_factory=factory)
    text = (tmp_path / 'out' / 'e05-live.json').read_text(encoding='utf-8')
    evidence = json.loads(text)
    assert 'co2_dragster' in evidence['capabilities'] and 'gate' in evidence['capabilities']['co2_dragster']
    assert evidence['capabilities']['regen_chamber'] == dict(gate='blocked', error='RuntimeError', calls_used=0)
    assert evidence['qualification'] == 'blocked'
    assert 'SECRET-ISH-DETAIL' not in text
