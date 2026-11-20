"""Static checks of the E05 live workflow (.github/workflows/e05-live.yml), mirroring test_b13_step12_workflow.py.

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

WORKFLOW = ROOT / '.github' / 'workflows' / 'e05-live.yml'
B13 = ROOT / '.github' / 'workflows' / 'b13-live.yml'
AI_FLAG = 'MIRUM_ENGINEERING_AI_DRAFTS'


def text(path=WORKFLOW):
    return path.read_text(encoding='utf-8')


def active_lines(path=WORKFLOW):
    return [l for l in text(path).splitlines() if l.strip() and not l.strip().startswith('#')]


def run_step_bodies():
    body = text().split('\n    steps:\n', 1)[1]
    return re.split(r'\n      - ', '\n' + body)


def step(fragment):
    found = [s for s in run_step_bodies() if fragment in s]
    assert len(found) == 1, (fragment, len(found))
    return found[0]


def test_dispatch_only_with_the_three_inputs():
    on = text().split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
    assert re.findall(r'^  (\w+):', on, re.M) == ['workflow_dispatch']
    assert re.findall(r'^      (\w+):', on, re.M) == ['capability', 'run_id', 'authorization']
    for forbidden in ('pull_request', 'pull_request_target', 'push:', 'schedule', 'workflow_run', 'issue_comment'):
        assert forbidden not in '\n'.join(active_lines()), forbidden


def test_capability_input_is_a_closed_choice():
    on = text().split('\non:\n', 1)[1]
    options = re.search(r'capability:.*?options: \[([^\]]+)\]', on, re.S).group(1)
    assert [o.strip() for o in options.split(',')] == ['co2_dragster', 'regen_chamber', 'glider_cem', 'all']


def test_top_level_permissions_are_empty_and_job_has_only_what_it_needs():
    t = text()
    assert re.search(r'^permissions: \{\}\s*$', t, re.M)
    job_perms = re.search(r'^    permissions:\n((?:      .+\n)+)', t, re.M).group(1)
    assert [l.strip() for l in job_perms.splitlines()] == [
        'contents: write   # only to create the atomic reservation tags', 'actions: read']
    assert t.count('permissions:') == 2


def test_job_is_main_only_in_the_secret_environment_with_a_bounded_timeout():
    t = text()
    assert "if: github.ref == 'refs/heads/main'" in t
    assert '    environment: b13-live\n' in t
    assert re.search(r'timeout-minutes: \d+', t)


def test_concurrency_is_shared_with_b13_live_and_never_cancels():
    assert re.search(r'concurrency:\n  group: b13-live\n  cancel-in-progress: false', text())
    assert re.search(r'concurrency:\n  group: b13-live\n', text(B13))


def test_input_validation_is_first_and_reserves_nothing_before_it():
    body = text()
    validate = body.index('Validate inputs')
    assert body.index('      - uses: actions/checkout@v4') > validate
    assert '[[ "$RUN_ID" =~ ^[a-z0-9][a-z0-9-]{5,62}$ ]]' in step('Validate inputs')
    assert '[[ "$AUTHORIZATION" =~ ^[A-Za-z0-9._-]{6,80}$ ]]' in step('Validate inputs')
    assert 'bad capability' in step('Validate inputs')


def test_atomic_tag_reservation_is_one_per_capability_with_its_own_prefix():
    s = step('Validate inputs')
    assert 'e05-${cap}-' in s
    assert '[ "$used" -lt 1 ]' in s
    assert 'refs/tags/e05-${cap}-${RUN_ID}' in s
    # Own prefix: b13-live's diagnostic/qualification counters are untouched.
    assert 'b13-' not in s.replace('b13-live', '')


def test_provider_secret_is_read_in_exactly_one_step_and_only_there():
    assert text().count('secrets.B13_GEMINI_API_KEY') == 1
    assert 'secrets.' in step('KEY: ${{ secrets.B13_GEMINI_API_KEY }}')


def test_pro_and_generic_keys_are_blanked_and_only_heavy_is_written():
    s = step('docker run')
    for blank in ('GEMINI_API_KEY_PRO=', 'GEMINI_API_KEY_LITE=', 'GEMINI_API_KEYS=', 'GEMINI_API_KEY=', 'GOOGLE_API_KEY='):
        assert blank in s, blank
    assert "printf 'GEMINI_API_KEY_HEAVY=%s\\n'" in s


def test_flag_is_never_named_in_the_workflow_it_is_set_inside_the_container_by_the_script():
    # b13-step12 invariant: the flag name appears in no workflow but the step-12 override.
    assert AI_FLAG not in text()
    script = (ROOT / 'scripts' / 'e05_live_eval.py').read_text(encoding='utf-8')
    assert script.count(f"os.environ['{AI_FLAG}'] = '1'") == 1


def test_container_is_hardened_like_b13():
    s = step('docker run')
    for flag in ('--read-only', '--cap-drop ALL', '--security-opt no-new-privileges',
                 '--memory 2g', '--pids-limit 256', '--tmpfs /tmp', '--user "$(id -u):$(id -g)"',
                 '-v "$cred:/run/existing-provider.env:ro"'):
        assert flag in s, flag
    assert '--network' not in s


def test_artifact_upload_is_json_only_and_never_ledger_or_state():
    s = step('Upload safe evidence')
    assert 'evidence/**/*.json' in s
    for forbidden in ('ledger', '.db', 'state', '.md', 'png', '.log'):
        assert forbidden not in s, forbidden
    assert 'if: always()' in s


def test_uses_the_verification_image_and_the_harness_script():
    assert 'Dockerfile.verify' in text()
    assert 'python scripts/e05_live_eval.py' in text()
    assert '--pace-seconds 12' in text()
