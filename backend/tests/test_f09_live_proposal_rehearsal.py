"""F09 L3: the AI proposal route harness in rehearsal mode (provider-free), plus its refusal rules.

The refusal tests are pure and run on every host. The rehearsal run needs the real kernel, so it is a
native shard test: skipped when cadquery, fastapi or httpx is absent, and off posix (same guard as
test_program_interface_native.py). It runs scripts/f09_live_proposal.py in a subprocess with the typed
proposer fixture injected at the provider interface, then checks the evidence JSON.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / 'f09_live_proposal.py'
F09 = Path(__file__).parent / 'fixtures' / 'program' / 'f09'
FIXTURE_CALLS = 3
SHA = 'a' * 40
IMAGE = 'sha256:' + 'b' * 64


def load_harness():
    spec = importlib.util.spec_from_file_location('f09_live_proposal_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence_of(directory):
    return json.loads((Path(directory) / 'f09-live.json').read_text(encoding='utf-8'))


def argv(tmp_path, *extra):
    return ['--evidence-dir', str(tmp_path / 'evidence'), '--state-dir', str(tmp_path / 'state'), *extra]


# ------------------------------------------------------------------------------------ pure refusals

def test_rehearsal_refuses_a_credential_file_without_any_call(tmp_path):
    credential = tmp_path / 'provider.env'
    credential.write_text('GEMINI_API_KEY_HEAVY=not-a-real-key\n', encoding='utf-8')
    code = load_harness().main(['--mode', 'rehearsal', '--credential-file', str(credential), *argv(tmp_path)])
    assert code == 2
    evidence = evidence_of(tmp_path / 'evidence')
    assert evidence['verdict'] == 'blocked' and 'credential' in evidence['blocker']
    assert evidence['provider_calls'] == 0 and evidence['steps'] == []
    assert not (tmp_path / 'state').exists()
    assert 'not-a-real-key' not in json.dumps(evidence)


@pytest.mark.parametrize('credential', [None, 'missing.env'])
def test_live_refuses_a_missing_credential_file_without_any_call(tmp_path, credential):
    extra = ['--credential-file', str(tmp_path / credential)] if credential else []
    code = load_harness().main(['--mode', 'live', '--run-id', 'run-1', '--authorization', 'auth-1',
                                '--source-sha', SHA, '--image-id', IMAGE, *extra, *argv(tmp_path)])
    assert code == 2
    evidence = evidence_of(tmp_path / 'evidence')
    assert evidence['verdict'] == 'blocked' and 'credential file' in evidence['blocker']
    assert evidence['provider_calls'] == 0 and evidence['steps'] == []
    assert not (tmp_path / 'state').exists()


def test_live_requires_a_distinct_authorized_identity(tmp_path):
    credential = tmp_path / 'provider.env'
    credential.write_text('GEMINI_API_KEY_HEAVY=not-a-real-key\n', encoding='utf-8')
    code = load_harness().main(['--mode', 'live', '--credential-file', str(credential), *argv(tmp_path)])
    assert code == 2 and 'authorized run identity' in evidence_of(tmp_path / 'evidence')['blocker']


@pytest.mark.parametrize('cap', ['0', '7'])
def test_call_cap_has_a_hard_ceiling(tmp_path, cap):
    code = load_harness().main(['--mode', 'rehearsal', '--max-provider-calls', cap, *argv(tmp_path)])
    assert code == 2 and 'max-provider-calls' in evidence_of(tmp_path / 'evidence')['blocker']


def test_a_used_state_dir_and_existing_evidence_are_never_reused(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'engineering').mkdir()
    harness = load_harness()
    assert harness.main(['--mode', 'rehearsal', *argv(tmp_path)]) == 2
    assert 'never reused' in evidence_of(tmp_path / 'evidence')['blocker']
    before = (tmp_path / 'evidence' / 'f09-live.json').read_bytes()
    assert harness.main(['--mode', 'rehearsal', *argv(tmp_path)]) == 2
    assert (tmp_path / 'evidence' / 'f09-live.json').read_bytes() == before


def test_fixture_replies_are_typed_proposals_with_the_three_cases():
    from engineering.proposer import ProposalResponse
    replies = {name: ProposalResponse.model_validate_json((F09 / name).read_text(encoding='utf-8'))
               for name in ('reply_equivalent.json', 'reply_length_201.json', 'reply_candidate_20.json')}
    assert len(replies) == FIXTURE_CALLS
    assert all(reply.state == 'proposed' for reply in replies.values())
    assert replies['reply_length_201.json'].fields.requirements.length_mm == 201.0
    assert replies['reply_candidate_20.json'].fields.candidate_heights_mm == (20.0,)
    assert replies['reply_equivalent.json'].fields.requirements.length_mm == 200.0


# ------------------------------------------------------------------------------- native rehearsal run

def test_rehearsal_run_passes_every_step_with_no_network(tmp_path):
    if os.name != 'posix':
        pytest.skip('ProcessRunner needs posix process groups')
    pytest.importorskip('cadquery')
    pytest.importorskip('fastapi')
    pytest.importorskip('httpx')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'backend')]))
    done = subprocess.run([sys.executable, str(SCRIPT), '--mode', 'rehearsal', *argv(tmp_path),
                           '--run-id', 'f09-rehearsal-test'], cwd=str(ROOT), env=env, capture_output=True,
                          text=True, timeout=3000)
    evidence = evidence_of(tmp_path / 'evidence')
    assert done.returncode == 0, (done.stdout[-400:], evidence)
    assert evidence['mode'] == 'rehearsal' and evidence['verdict'] == 'passed'
    assert [s['step'] for s in evidence['steps']] == ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']
    assert all(step['passed'] for step in evidence['steps']), evidence['steps']
    # zero network, and the ledger agrees with the fixture about every provider call
    assert evidence['network_attempts'] == 0
    assert evidence['provider_calls'] == evidence['fixture_calls'] == FIXTURE_CALLS
    assert evidence['fixture_calls_match_ledger'] is True
    assert [s['provider_calls'] for s in evidence['steps']] == [0, 0, 0, 1, 1, 1]
    by = {s['step']: s for s in evidence['steps']}
    assert by['S3']['selected_height_mm'] == 15 and by['S3']['attempt_outcomes'] == ['rejected', 'rejected', 'accepted']
    assert by['S4']['draft_provenance_kind'] == 'ai' and by['S4']['source_hashes_distinct'] is True
    assert by['S5']['status'] == 'exercised' and by['S5']['error_code'] == 'requirements_changed'
    assert by['S5']['revisions_before'] == by['S5']['revisions_after']
    for step in ('S4', 'S5', 'S6'):
        link = by[step]['link']
        assert link['linked'] and link['draft_input_mode'] == 'ai' and link['ledger_calls_for_proposal'] == 1
        assert link['ai_proposed_origin_count'] == link['origin_count'] > 0
        assert len(link['prompt_sha256']) == 64 and len(link['converted_source_sha256']) == 64
    assert by['S6']['link']['revision_source_equals_converted'] is True
    assert by['S6']['source_id'] == 'typed-ai-proposal.mir' and by['S6']['selected_height_mm'] == 20
    assert by['S6']['run_before_review_left_staged'] is True and by['S6']['new_head'] is True
    # no raw reply text, prompt text or key in the evidence
    text = (tmp_path / 'evidence' / 'f09-live.json').read_text(encoding='utf-8')
    for name in ('reply_equivalent.json', 'reply_length_201.json', 'reply_candidate_20.json'):
        raw = (F09 / name).read_text(encoding='utf-8')
        assert raw not in text
        for marker in ('"questions"', '"unsupported_features"', '"contradictions"'):
            assert marker not in text
    harness = load_harness()
    for prompt in harness.PROMPTS.values():
        assert prompt[:60] not in text
    assert 'offline-fixture-never-sent' not in text


def test_later_prompts_restate_every_fact_with_exactly_one_change():
    # Live run live-20261124-a: a prompt that left the material reference implicit drew a clarification.
    harness = load_harness()
    base = harness.PROMPTS['equivalent']
    reference = 'Illustrative user-provided properties; not certified stock values'
    for key, (old, new) in {'length_201': ('200 mm long', '201 mm long'),
                            'candidate_20': ('candidate heights 5 mm, 10 mm and 15 mm',
                                             'a single candidate height of 20 mm')}.items():
        prompt = harness.PROMPTS[key]
        assert reference in prompt and 'as before' not in prompt
        assert prompt == base.replace(old, new) and prompt != base


def test_step_failure_details_never_carry_issue_reasons():
    harness = load_harness()
    secret_like = 'model text that must not reach evidence'
    issues = [{'field': 'request', 'state': 'clarification_required', 'reason': secret_like}]
    failure = harness.StepFailure('proposal_not_ready', issues=harness._safe_issues(issues))
    assert failure.details == {'reason': 'proposal_not_ready',
                               'issues': [{'field': 'request', 'state': 'clarification_required'}]}
    assert secret_like not in json.dumps(failure.details)
