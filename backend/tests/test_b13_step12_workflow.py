"""Static checks of the B13 step-12 live workflow and its compose override (RC1 B13 step 12).

Text-based on purpose: the schema-tests job in ci.yml has only a hashed pytest, no YAML parser. Nothing here
runs Docker, the network or a provider. Like test_picogk_dockerfiles.py, repo-level files are absent from the
native gate image, so this module is skipped there and runs from a full checkout in ci.yml's schema-tests job.
"""
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if os.environ.get('MIRUM_NATIVE_GATE_IMAGE') == '1':
    pytest.skip('native gate image has no repo-level workflow files', allow_module_level=True)

WORKFLOWS = ROOT / '.github' / 'workflows'
WORKFLOW = WORKFLOWS / 'b13-step12.yml'
OVERRIDE = ROOT / 'e2e' / 'compose' / 'b13-step12.override.yml'
LOCK = ROOT / '.github' / 'ci-requirements' / 'b13-step12-playwright.lock'
AI_FLAG = 'MIRUM_ENGINEERING_AI_DRAFTS'
SECRET_NAME = 'B13_GEMINI_API_KEY'


def text(path=WORKFLOW):
    return path.read_text(encoding='utf-8')


def steps():
    body = text().split('\n    steps:\n', 1)[1]
    parts = re.split(r'\n      - ', '\n' + body)
    return [p for p in parts if p.strip()]


def step(fragment):
    found = [s for s in steps() if fragment in s]
    assert len(found) == 1, (fragment, len(found))
    return found[0]


def active_lines(path):
    return [l for l in text(path).splitlines() if l.strip() and not l.strip().startswith('#')]


def test_manual_dispatch_only_with_the_three_inputs():
    on = text().split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
    assert re.findall(r'^  (\w+):', on, re.M) == ['workflow_dispatch']
    assert re.findall(r'^      (\w+):', on, re.M) == ['run_id', 'authorization', 'case_id']
    for forbidden in ('pull_request', 'pull_request_target', 'push:', 'schedule', 'workflow_run', 'issue_comment'):
        assert forbidden not in '\n'.join(active_lines(WORKFLOW)), forbidden


def test_top_level_permissions_are_empty_and_job_has_only_what_it_needs():
    t = text()
    assert re.search(r'^permissions: \{\}\s*$', t, re.M)
    job_perms = re.search(r'^    permissions:\n((?:      .+\n)+)', t, re.M).group(1)
    assert [l.strip() for l in job_perms.splitlines()] == [
        'contents: write   # only to create the atomic reservation tag', 'actions: read']
    assert t.count('permissions:') == 2


def test_job_is_main_only_in_the_secret_environment_with_a_bounded_timeout():
    t = text()
    assert "    if: github.ref == 'refs/heads/main'\n" in t
    assert '    environment: b13-live\n' in t
    assert 'runs-on: ubuntu-latest' in t
    assert int(re.search(r'timeout-minutes: (\d+)', t).group(1)) <= 45
    assert len(re.findall(r'^  \w[\w-]*:\n    if:', t, re.M)) == 1   # one job


def test_concurrency_is_shared_with_b13_live_and_never_cancels():
    t = text()
    assert re.search(r'concurrency:\n  group: b13-live\n  cancel-in-progress: false', t)
    assert re.search(r'concurrency:\n  group: b13-live\n', text(WORKFLOWS / 'b13-live.yml'))


def test_atomic_tag_reservation_with_new_prefix_and_cap_one():
    reserve = step('Validate inputs')
    assert 'prefix=b13-s12; cap=1' in reserve
    assert 'git/matching-refs/tags/${prefix}-' in reserve and 'git/refs' in reserve
    assert 'refs/tags/${prefix}-${RUN_ID}' in reserve
    assert '"$used" -lt "$cap"' in reserve
    assert r'[[ "$RUN_ID" =~ ^[a-z0-9][a-z0-9-]{5,62}$ ]]' in reserve
    assert r'[[ "$AUTHORIZATION" =~ ^[A-Za-z0-9._-]{6,80}$ ]]' in reserve
    # complete cases only: C01-C10
    assert r'^C(0[1-9]|10)$' in reserve
    # other prefixes are untouched, so b13-live's counts are not shared with this one
    assert 'b13-diag' not in reserve and 'b13-qual' not in reserve
    # reservation is the first step, before any build, secret or token exists
    assert steps()[0] == step('Validate inputs')


def test_provider_secret_is_read_in_exactly_one_step_and_only_there():
    t = text()
    assert t.count('secrets.') == 1
    assert 'secrets.' + SECRET_NAME in step('Start the release stack')
    start = step('Start the release stack')
    assert re.search(r'env:\n(?:          #.*\n)*          GEMINI_API_KEY_HEAVY: \$\{\{ secrets\.' + SECRET_NAME + r' \}\}\n', start)
    # never echoed, never in argv, never written
    for line in active_lines(WORKFLOW):
        if 'GEMINI_API_KEY_HEAVY' in line:
            assert 'printf' not in line and '>>' not in line and ' -e ' not in line, line
            assert not re.search(r'echo[^;]*\$\{?GEMINI_API_KEY_HEAVY', line), line
    assert 'GEMINI' not in step('Take one live proposal') and 'GEMINI' not in step('Compose logs on failure')
    assert '--env' not in t and 'env-file' not in t and 'GITHUB_ENV' in step('Run-local access token')
    assert 'GEMINI' not in step('Run-local access token')


def test_access_token_is_run_local_and_masked():
    token = step('Run-local access token')
    assert 'openssl rand -hex 32' in token and '::add-mask::' in token
    assert 'secrets.' not in token
    assert 'MIRUM_ACCESS_TOKEN' not in step('Take one live proposal').split('run:')[1]   # never in argv


def test_images_come_from_the_release_action_and_actions_are_pinned():
    assert './.github/actions/build-release-images' in text()
    for uses in re.findall(r'uses: (\S+)', text()):
        assert uses.startswith('./') or re.fullmatch(r'[\w./-]+@[0-9a-f]{40}', uses), uses
    assert 'docker build' not in text() and 'docker pull' not in text()


def test_stack_is_the_release_compose_plus_the_override_in_a_disposable_project():
    t = text()
    assert ('COMPOSE_CMD: docker compose -p b13s12-${{ github.run_id }}-${{ github.run_attempt }} '
            '-f compose.release.yml -f e2e/compose/b13-step12.override.yml') in t
    assert '$COMPOSE_CMD up -d --wait' in step('Start the release stack')
    teardown = step('Tear down')
    assert '$COMPOSE_CMD down -v --remove-orphans' in teardown and 'if: always()' in teardown
    assert 'docker-compose.yml' not in t


def test_unit_tests_run_with_short_tracebacks_before_the_token_or_key_exist():
    names = [s.split('\n', 1)[0] for s in steps()]
    unit = next(i for i, n in enumerate(names) if 'Driver unit tests' in n)
    assert unit < next(i for i, n in enumerate(names) if 'Run-local access token' in n)
    assert unit < next(i for i, n in enumerate(names) if 'Start the release stack' in n)
    assert '--tb=short' in step('Driver unit tests')
    for s in steps():
        for line in s.splitlines():
            if 'pytest' in line and 'pip install' not in line and 'pytest.lock' not in line:
                assert '--tb=short' in line, line


def test_failure_logs_redact_the_token_and_key_shapes():
    logs = step('Compose logs on failure')
    assert 'if: failure()' in logs
    assert 'MIRUM_ACCESS_TOKEN' in logs and '[redacted-token]' in logs and 'AIza' in logs and '[redacted-key]' in logs
    assert logs.index('logs --no-color') < logs.index('sed -E')   # logs are always piped through the redaction


def test_only_json_and_png_are_uploaded_never_ledger_or_packages():
    upload = step('Upload safe evidence')
    assert 'if: always()' in upload
    lines = upload.splitlines()
    start = lines.index('          path: |') + 1
    paths = []
    for line in lines[start:]:
        if not line.startswith('            '):
            break
        paths.append(line.strip())
    assert paths == ['${{ runner.temp }}/b13-step12-evidence/**/*.json', '${{ runner.temp }}/b13-step12-evidence/**/*.png']
    for banned in ('sqlite', '.log', '.zip', '.step', '.glb', '.env', 'downloads', 'state'):
        assert banned not in ''.join(paths)
    assert text().count('upload-artifact@') == 1


def test_driver_step_gets_evidence_and_download_dirs_apart():
    driver = step('Take one live proposal')
    assert 'scripts/b13_step12_ui.py' in driver and '--case-id "$CASE_ID"' in driver
    assert 'b13-step12-evidence' in driver and 'b13-step12-downloads' in driver
    assert 'b13-step12-downloads' not in step('Upload safe evidence')


def test_driver_tooling_comes_from_hashed_locks():
    install = step('Install the driver tooling')
    for line in install.splitlines():
        if 'pip install' in line:
            assert '--require-hashes' in line and '--only-binary=:all:' in line and '--no-deps' in line, line
    assert 'b13-step12-playwright.lock' in install and 'playwright install' not in text()
    lock = text(LOCK)
    pins = re.findall(r'^([A-Za-z0-9_.-]+)==\S+ \\$', lock, re.M)
    assert {'playwright', 'pyee', 'typing_extensions', 'greenlet'} <= set(pins)
    assert lock.count('--hash=sha256:') >= len(pins)
    for block in re.split(r'\n(?=[A-Za-z])', lock):
        if '==' in block and not block.startswith('#'):
            assert re.search(r'--hash=sha256:[0-9a-f]{64}', block), block


# ---------------------------------------------------------------- the override and the AI flag

def test_ai_flag_is_only_in_the_step12_override():
    assert AI_FLAG in text(OVERRIDE)
    assert re.search(r'^      ' + AI_FLAG + r': "1"$', text(OVERRIDE), re.M)
    holders = []
    for path in list(ROOT.glob('compose*.yml')) + list((ROOT / 'e2e').rglob('*.yml')) + list(WORKFLOWS.glob('*.yml')) \
            + list((ROOT / '.github' / 'actions').rglob('*.yml')):
        if AI_FLAG in path.read_text(encoding='utf-8'):
            holders.append(path.relative_to(ROOT).as_posix())
    assert holders == ['e2e/compose/b13-step12.override.yml'], holders
    assert AI_FLAG not in text(ROOT / 'compose.release.yml')


def test_override_puts_flag_and_key_in_the_backend_only():
    t = text(OVERRIDE)
    assert re.findall(r'^  (\w+):$', t, re.M) == ['backend']          # services: backend only
    assert 'frontend' not in '\n'.join(active_lines(OVERRIDE))
    # pass-through: no value is stored in the file
    assert re.search(r'^      GEMINI_API_KEY_HEAVY:\s*$', t, re.M)
    assert not re.search(r'GEMINI\w*:\s*\S', '\n'.join(active_lines(OVERRIDE)).replace('MIRUM', ''))
    assert 'env_file' not in t and 'ports:' not in t and 'volumes:' not in t
    # backend budget is capped at the owner's total and one call in flight
    assert re.search(r'MIRUM_ENGINEERING_AI_DAILY_CALLS: "3"', t)
    assert re.search(r'MIRUM_ENGINEERING_AI_CONCURRENCY: "1"', t)


def test_override_does_not_loosen_the_release_compose():
    t = '\n'.join(active_lines(OVERRIDE))
    for loosened in ('read_only', 'cap_add', 'privileged', 'security_opt', 'mem_limit', 'pids_limit',
                     'tmpfs', 'MIRUM_DEPLOYMENT', 'MIRUM_ACCESS_TOKEN', 'user:'):
        assert loosened not in t, loosened


def test_release_compose_and_other_workflows_keep_the_flag_off():
    for path in WORKFLOWS.glob('*.yml'):
        assert AI_FLAG not in text(path), path.name
    assert 'GEMINI' not in text(ROOT / 'compose.release.yml')


def test_dispatch_command_has_no_other_trigger_from_b13_live():
    live = text(WORKFLOWS / 'b13-live.yml')
    assert 'b13-s12' not in live            # b13-live's reservation counts are untouched
    assert 'prefix=b13-diag' in live and 'prefix=b13-qual' in live
