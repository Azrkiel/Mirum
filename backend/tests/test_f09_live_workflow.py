"""Static checks of the F09 live workflow (.github/workflows/f09-live.yml), mirroring test_e05_live_workflow.py.

Text-based on purpose: nothing here runs Docker, the network or a provider. Repo-level workflow files are
absent from the native gate image, so this module is skipped there.
"""
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if os.environ.get('MIRUM_NATIVE_GATE_IMAGE') == '1':
    pytest.skip('native gate image has no repo-level workflow files', allow_module_level=True)

WORKFLOW = ROOT / '.github' / 'workflows' / 'f09-live.yml'


def text():
    return WORKFLOW.read_text(encoding='utf-8')


def active_lines():
    return [l for l in text().splitlines() if l.strip() and not l.strip().startswith('#')]


def steps():
    body = text().split('\n    steps:\n', 1)[1]
    return [s for s in re.split(r'\n      - ', '\n' + body) if s.strip()]


def step_index(fragment):
    found = [i for i, s in enumerate(steps()) if fragment in s]
    assert len(found) == 1, (fragment, found)
    return found[0]


def test_dispatch_only_with_run_id_and_authorization():
    on = text().split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
    assert re.findall(r'^  (\w+):', on, re.M) == ['workflow_dispatch']
    assert re.findall(r'^      (\w+):', on, re.M) == ['run_id', 'authorization']
    for forbidden in ('pull_request', 'pull_request_target', 'push:', 'schedule', 'workflow_run', 'issue_comment'):
        assert forbidden not in '\n'.join(active_lines()), forbidden


def test_main_only_protected_environment_and_minimal_permissions():
    t = text()
    assert "if: github.ref == 'refs/heads/main'" in t
    assert re.search(r'^    environment: b13-live$', t, re.M)
    assert re.search(r'^permissions: \{\}\s*$', t, re.M)
    job_perms = re.search(r'^    permissions:\n((?:      .+\n)+)', t, re.M).group(1)
    assert [l.strip() for l in job_perms.splitlines()] == [
        'contents: write   # only to create the atomic reservation tag', 'actions: read']
    assert re.search(r'^  group: b13-live$', t, re.M) and 'cancel-in-progress: false' in t


def test_secret_reaches_only_the_live_step_through_a_private_file():
    t = text()
    assert t.count('secrets.') == 1
    live = steps()[step_index('secrets.B13_GEMINI_API_KEY')]
    assert live.startswith('name: Live run')
    assert 'umask 077' in live and 'unset KEY' in live and 'rm -f "$cred"' in live
    assert '-v "$cred:/run/existing-provider.env:ro"' in live
    for blank in ('GEMINI_API_KEY_HEAVY=', 'GEMINI_API_KEY=', 'GOOGLE_API_KEY=', 'GEMINI_API_KEY_PRO='):
        assert '-e %s ' % blank in live
    assert '--mode live' in live and '--max-provider-calls 4' in live


def test_rehearsal_runs_offline_before_the_reservation_and_the_live_run():
    rehearsal = step_index('--mode rehearsal')
    reserve = step_index('git/refs')
    live = step_index('--mode live')
    assert rehearsal < reserve < live
    body = steps()[rehearsal]
    assert '--network none' in body and 'secrets.' not in body and '--credential-file' not in body


def test_reservation_is_atomic_with_cumulative_cap_two():
    body = steps()[step_index('git/refs')]
    assert 'refs/tags/f09-live-${RUN_ID}' in body
    assert 'matching-refs/tags/f09-live-' in body and '-lt 2' in body
    validate = steps()[0]
    assert '^[a-z0-9][a-z0-9-]{5,62}$' in validate and '^[A-Za-z0-9._-]{6,80}$' in validate


def test_containers_are_hardened():
    for fragment in ('--mode rehearsal', '--mode live'):
        body = steps()[step_index(fragment)]
        for flag in ('--read-only', '--cap-drop ALL', '--security-opt no-new-privileges', '--pids-limit 256',
                     '--user "$(id -u):$(id -g)"'):
            assert flag in body, (fragment, flag)


def test_uploads_json_only_and_fails_unless_passed():
    upload = steps()[step_index('upload-artifact')]
    paths = re.findall(r'\$\{\{ runner\.temp \}\}/(\S+)', upload)
    assert paths and all(p.endswith('*.json') for p in paths)
    gate = steps()[-1]
    assert 'f09-live.json' in gate and 'verdict == "passed"' in gate
