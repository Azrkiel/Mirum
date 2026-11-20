"""E05 live qualification for the generator AI proposers (co2_dragster, regen_chamber, glider_cem).

Owner-gated: runs only inside the reviewer-gated b13-live job. Never prints credentials, prompts or
replies; evidence JSON holds run identity, corpus hashes, call counts and per-case ids and states only.
Each capability has a hard cap of CALL_CAP provider calls (repair calls included, one unit each).
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / 'backend' / 'tests' / 'fixtures' / 'engineering' / 'proposals'
CALL_CAP = 30
PACE_MAX = 120
CAPABILITIES = ('co2_dragster', 'regen_chamber', 'glider_cem')
# Pinned SHA-256 of each fixed corpus file. A changed corpus is refused before any call.
CORPUS_SHA256 = {
    'co2_dragster': 'fdc429faa9225a677877ae68d0990669a3010a56438560f3541ef3a3a941aa68',
    'regen_chamber': '95b161ecc66b9a8a2475d967e4dc7259bbf4db8df00597d1233d3c3bc3ecc397',
    'glider_cem': 'c4113bb1334fd0951082efe0141120b6615dc05bbed4433afce1310c9a8db3a3',
}
SPEC_PREFIX = 'specification.'
MISSING = object()


class CorpusPinMismatch(RuntimeError):
    """A corpus file differs from its pinned SHA-256."""


class NativeExecutionAttempted(AssertionError):
    """Live proposal code tried to run a subprocess. Counted as an unreviewed execution."""


def load_corpora(corpus_dir=CORPUS_DIR):
    """Return {capability: cases}. Refuses unless every file matches its pin."""
    loaded = {}
    for capability in CAPABILITIES:
        raw = (Path(corpus_dir) / f'{capability}-v1.json').read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != CORPUS_SHA256[capability]:
            raise CorpusPinMismatch(f'{capability} corpus does not match its pinned SHA-256')
        loaded[capability] = json.loads(raw)['cases']
    return loaded


def lookup(payload, path):
    node = payload
    for part in path.split('.'):
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    return node


def _bare(field):
    return field[len(SPEC_PREFIX):] if field.startswith(SPEC_PREFIX) else field


def score_case(case, proposal):
    """Per-case metrics. Values are never copied into the result, only counts and states."""
    payload = proposal.payload if isinstance(proposal.payload, dict) else {}
    expected = case.get('expected_content') or {}
    expected_fields = {_bare(path) for path in expected}
    mismatches = sum(lookup(payload, path) != value for path, value in expected.items())
    origins = getattr(proposal.provenance, 'field_origins', ()) or ()
    unstated_ai = sum(
        1 for origin in origins
        if origin.origin == 'ai_proposed' and _bare(origin.field) not in expected_fields)
    state = proposal.state
    accurate = bool(case['kind'] == 'complete' and state == 'proposed' and state == case['expected_state']
                    and mismatches == 0 and unstated_ai == 0)
    silent_drop = state == 'proposed' and not accurate
    return dict(case_id=case['id'], kind=case['kind'], expected_state=case['expected_state'], state=state,
                state_as_expected=state == case['expected_state'], field_mismatches=mismatches,
                unstated_ai_values=unstated_ai, accurate_reviewable_draft=accurate,
                silent_requirement_drop=silent_drop)


def outcome_label(record):
    if record['accurate_reviewable_draft']:
        return 'complete_accurate'
    if record['silent_requirement_drop']:
        return 'silent_drop'
    if record['state_as_expected'] and record['state'] != 'proposed':
        return 'expected_block'
    return 'unexpected_state'


class CountingBudget:
    """Wraps AiCallBudget semantics: one unit per reserve, never refunded, denials counted."""

    def __init__(self, cap=CALL_CAP):
        from engineering.limits import AiCallBudget
        self._budget = AiCallBudget(cap, 1)
        self.denied = 0

    def reserve(self):
        ok = self._budget.reserve()
        if not ok:
            self.denied += 1
        return ok

    def release(self):
        self._budget.release()

    def has_capacity(self):
        return self._budget.has_capacity()

    def used(self):
        return self._budget.used()


class no_native_execution:
    """Counts and blocks subprocess starts while live proposals run."""

    def __init__(self):
        self.attempts = 0

    def _blocked(self, *args, **kwargs):
        self.attempts += 1
        raise NativeExecutionAttempted('Live proposal cannot start a process')

    def __enter__(self):
        self._saved = (subprocess.Popen, os.system, asyncio.create_subprocess_exec)
        subprocess.Popen = self._blocked
        os.system = self._blocked
        asyncio.create_subprocess_exec = self._blocked
        return self

    def __exit__(self, *exc):
        subprocess.Popen, os.system, asyncio.create_subprocess_exec = self._saved
        return False


def run_capability(capability, cases, proposer, budget, *, pace_seconds=0, sleep=time.sleep):
    """Run every case through one proposer. Stops at the first budget denial, with no retry."""
    records = []
    outcome = 'completed'
    native = no_native_execution()
    with native:
        for index, case in enumerate(cases):
            if index and pace_seconds:
                sleep(pace_seconds)
            denied_before = budget.denied
            try:
                proposal = asyncio.run(proposer.propose(case['prompt']))
            except NativeExecutionAttempted:
                outcome = 'native_execution_attempted'
                break
            except Exception as error:
                outcome = 'provider_error:' + type(error).__name__
                break
            if budget.denied > denied_before:
                outcome = 'budget_exhausted'
                break
            record = score_case(case, proposal)
            record['outcome'] = outcome_label(record)
            records.append(record)
    return dict(
        cases=records,
        calls=budget.used(),
        budget_cap=CALL_CAP,
        budget_exhausted=outcome == 'budget_exhausted',
        unreviewed_executions=native.attempts,
        stop=outcome,
        cases_expected=len(cases),
    )


def gate(result):
    """B13 run-5 rule, applied per capability. Quoted in QUALIFICATION.md, section 'E05 live qualification (planned)'.

    Pass only if the run was not stopped early, every case ran, every complete case is complete-accurate,
    there are zero silent drops, zero unstated AI values and zero unreviewed executions.
    """
    complete = [c for c in result['cases'] if c['kind'] == 'complete']
    ok = (result['stop'] == 'completed'
          and len(result['cases']) == result['cases_expected']
          and len(complete) >= 1
          and all(c['accurate_reviewable_draft'] for c in complete)
          and sum(c['silent_requirement_drop'] for c in result['cases']) == 0
          and sum(c['unstated_ai_values'] for c in result['cases']) == 0
          and result['unreviewed_executions'] == 0)
    return 'passed' if ok else 'blocked'


def build_proposer(capability, budget):
    from engineering import proposal_contract
    return proposal_contract.ai_proposer(capability, enabled=True, budget=budget)


def run(output, capabilities, *, sha, image_id, run_id, authorization, pace_seconds=0,
        corpus_dir=CORPUS_DIR, proposer_factory=build_proposer, budget_factory=CountingBudget):
    if not (re.fullmatch(r'[0-9a-f]{40}', sha) and re.fullmatch(r'sha256:[0-9a-f]{64}', image_id)):
        raise ValueError('An immutable commit and image id are required')
    if not (isinstance(pace_seconds, (int, float)) and 0 <= pace_seconds <= PACE_MAX):
        raise ValueError('pace_seconds out of range')
    corpora = load_corpora(corpus_dir)  # refuses before any call
    evidence = dict(run_id=run_id, authorization=authorization, tested_implementation_sha=sha,
                    image_id=image_id, call_cap_per_capability=CALL_CAP, pace_seconds=pace_seconds,
                    corpus_sha256=dict(CORPUS_SHA256), capabilities={}, qualification='blocked')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for capability in capabilities:
        budget = budget_factory()
        proposer = proposer_factory(capability, budget)
        result = run_capability(capability, [{k: v for k, v in c.items() if k != 'fixture_response'}
                                             for c in corpora[capability]],
                                proposer, budget, pace_seconds=pace_seconds)
        result['gate'] = gate(result)
        evidence['capabilities'][capability] = result
    evidence['qualification'] = ('passed' if all(v['gate'] == 'passed' for v in evidence['capabilities'].values())
                                 and evidence['capabilities'] else 'blocked')
    temporary = output / 'e05-live.json.tmp'
    temporary.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    temporary.replace(output / 'e05-live.json')
    return evidence


def load_credential(path):
    """Read one literal Gemini key into the process environment. Never echoes it."""
    values = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        name, separator, raw = line.strip().removeprefix('export ').partition('=')
        name = name.strip()
        if not separator or name not in ('GEMINI_API_KEY_HEAVY', 'GEMINI_API_KEY'):
            continue
        parts = shlex.split(raw, comments=True, posix=True)
        if len(parts) != 1 or re.fullmatch(r'[A-Za-z0-9_.-]+', parts[0]) is None:
            raise ValueError('Credential must be one literal key')
        values[name] = parts[0]
    key = values.get('GEMINI_API_KEY_HEAVY') or values.get('GEMINI_API_KEY')
    if not key:
        raise ValueError('No usable key in credential file')
    os.environ['GEMINI_API_KEY_HEAVY'] = key
    # glider's legacy client follows GEMINI_API_KEY_PRO; the approved key must be the only one.
    if os.environ.get('GEMINI_API_KEY_PRO'):
        raise ValueError('GEMINI_API_KEY_PRO must be blank in the live container')
    return 'GEMINI_API_KEY_HEAVY'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('capability', choices=CAPABILITIES + ('all',))
    parser.add_argument('sha')
    parser.add_argument('credentials', nargs='?')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--authorization', required=True)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--pace-seconds', type=float, default=0)
    args = parser.parse_args(argv)
    if args.credentials:
        load_credential(args.credentials)
    capabilities = CAPABILITIES if args.capability == 'all' else (args.capability,)
    evidence = run(args.output, capabilities, sha=args.sha, image_id=args.image_id, run_id=args.run_id,
                   authorization=args.authorization, pace_seconds=args.pace_seconds)
    print(json.dumps({k: evidence[k] for k in ('qualification',)}))
    return 0 if evidence['qualification'] == 'passed' else 2


if __name__ == '__main__':
    sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
    sys.exit(main())
