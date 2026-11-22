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


def names():
    return [s.split(chr(10), 1)[0] for s in steps()]


def test_input_validation_is_first_and_reserves_nothing():
    validate = step('Validate inputs')
    assert steps()[0] == validate
    assert r'[[ "$RUN_ID" =~ ^[a-z0-9][a-z0-9-]{5,62}$ ]]' in validate
    assert r'[[ "$AUTHORIZATION" =~ ^[A-Za-z0-9._-]{6,80}$ ]]' in validate
    assert r'^C(0[1-9]|10)$' in validate          # complete cases only: C01-C10
    assert 'git/refs' not in validate and 'GH_TOKEN' not in validate


def test_atomic_tag_reservation_with_new_prefix_and_cap_one():
    reserve = step('Enforce the total budget')
    assert 'prefix=b13-s12; cap=1' in reserve
    assert 'git/matching-refs/tags/${prefix}-' in reserve and 'git/refs' in reserve
    assert 'refs/tags/${prefix}-${RUN_ID}' in reserve
    assert '"$used" -lt "$cap"' in reserve
    # other prefixes are untouched, so b13-live's counts are not shared with this one
    assert 'b13-diag' not in reserve and 'b13-qual' not in reserve


def test_reservation_comes_after_build_stack_and_preflight_and_right_before_the_provider_step():
    order = names()
    reserve = next(i for i, n in enumerate(order) if 'Enforce the total budget' in n)
    live = next(i for i, n in enumerate(order) if 'Take one live proposal' in n)
    assert reserve == live - 1                      # nothing between the reservation and the first call
    assert text().index('uses: ./.github/actions/build-release-images') < text().index('git/refs')
    for earlier in ('Start the release stack', 'Provider-free preflight', 'Run-local access token', 'Driver unit tests'):
        assert next(i for i, n in enumerate(order) if earlier in n) < reserve, earlier
    # no other step creates a ref
    assert text().count('git/refs') == 1


def test_preflight_spends_nothing_and_has_no_key():
    pre = step('Provider-free preflight')
    assert '--preflight-only' in pre and 'GEMINI' not in pre and 'secrets.' not in pre
    assert 'b13-step12-evidence' in pre
    assert '--preflight-only' not in step('Take one live proposal')


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
    # Every file of any type at the repo root (docker-compose*.yml, compose*.yml, .env*, fly.toml...),
    # every */fly.toml, and the e2e, CI, deploy and scripts trees. Matched on raw bytes (UTF-8 and
    # UTF-16), so a re-encoded or renamed file cannot slip past; a commented-out line does not set it.
    # Markdown only documents the flag, and e2e Python tests only assert on it against e2e stacks.
    def sets_flag(data):
        if any(AI_FLAG.encode(enc) in data for enc in ('utf-16-le', 'utf-16-be')):
            return True
        if AI_FLAG.encode() not in data:
            return False
        return any(AI_FLAG in line and not line.lstrip().startswith('#')
                   for line in data.decode('utf-8', 'replace').splitlines())

    files = [p for p in ROOT.iterdir() if p.is_file()] + list(ROOT.glob('*/fly.toml'))
    for tree in ('e2e', '.github', 'deploy', 'scripts'):
        files += [p for p in (ROOT / tree).rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts and not (tree == 'e2e' and p.suffix == '.py')]
    holders, env_refs = set(), set()
    for path in files:
        if path.suffix == '.md':
            continue
        data = path.read_bytes()
        rel = path.relative_to(ROOT).as_posix()
        if sets_flag(data):
            holders.add(rel)
        if b'e05-ai-offline.env' in data and rel != 'e2e/compose/e05-ai-offline.env':
            env_refs.add(rel)
    # The B13 step-12 override and the E05 offline e2e env file (no egress, placeholder key); the E05 live
    # qualification script may set it in-process inside its owner-gated container (Wave E P5). Prod: the
    # backend Fly config (owner yes 2026-11-22, runbook 2026-11-21-ai-flag-enable.md section 5); its whole
    # [env], caps included, is pinned by e2e/test_fly_release.py.
    required = {'e2e/compose/b13-step12.override.yml', 'e2e/compose/e05-ai-offline.env',
                'deploy/fly/backend.fly.toml'}
    assert required <= holders <= required | {'scripts/e05_live_eval.py'}, sorted(holders)
    # That env file is loaded only by its own e2e override, never by a release/prod compose or Fly config.
    assert env_refs == {'e2e/compose/e05-ai-offline.override.yml'}, sorted(env_refs)
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
    # M1: the proposer reads only GEMINI_API_KEY_HEAVY, so the dragster interpreter's own cap is zeroed
    assert re.search(r'^      MIRUM_DRAGSTER_INTERPRET_DAILY_CAP: "0"$', t, re.M)
    assert len(re.findall(r'^      GEMINI\w*:', t, re.M)) == 1


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


# ---------------------------------------------------------------- the provider-free rehearsal workflow

REHEARSAL = WORKFLOWS / 'b13-step12-rehearsal.yml'
REHEARSAL_OVERRIDE = ROOT / 'e2e' / 'compose' / 'b13-step12-rehearsal.override.yml'


def test_rehearsal_has_no_secret_no_environment_no_flag_and_read_only_permissions():
    t = text(REHEARSAL)
    live = chr(10).join(active_lines(REHEARSAL))
    for forbidden in ('secrets.', 'environment:', AI_FLAG, 'GEMINI', 'B13_', 'contents: write', 'workflow_run',
                      'pull_request_target', 'git/refs'):
        assert forbidden not in live, forbidden
    assert re.search(r'^permissions: \{\}\s*$', t, re.M)
    assert t.count('permissions:') == 2 and 'contents: read' in t
    assert 'GEMINI' not in text(REHEARSAL_OVERRIDE) and AI_FLAG not in text(REHEARSAL_OVERRIDE)
    assert '-f compose.release.yml -f e2e/compose/b13-step12-rehearsal.override.yml' in t
    assert 'b13-step12.override.yml' not in live.replace('e2e/compose/b13-step12.override.yml"', '')


def test_rehearsal_triggers_and_paths():
    on = text(REHEARSAL).split(chr(10) + 'on:' + chr(10), 1)[1].split(chr(10) + 'permissions:', 1)[0]
    assert re.findall(r'^  (\w+):', on, re.M) == ['pull_request', 'workflow_dispatch']
    for path in ('scripts/b13_step12_ui.py', '.github/ci-requirements/b13-step12-playwright.lock',
                 'e2e/compose/b13-step12.override.yml', '.github/workflows/b13-step12.yml',
                 'frontend/engineering_ui.py'):
        assert '- "%s"' % path in on, path


def test_rehearsal_runs_the_driver_in_rehearsal_mode_and_uploads_json_and_png_only():
    t = text(REHEARSAL)
    assert '--rehearsal' in t and 'scripts/b13_step12_ui.py' in t and '--tb=short' in t
    assert 'MIRUM_ACCESS_TOKEN=$token' in t and '::add-mask::' in t
    assert '/**/*.json' in t and '/**/*.png' in t
    for banned in ('sqlite', '.zip', '.step', '.glb', 'downloads/**'):
        assert banned not in t.split('Upload evidence', 1)[1].split('Tear down', 1)[0], banned
    assert '$COMPOSE_CMD down -v --remove-orphans' in t
    for uses in re.findall(r'uses: (\S+)', t):
        assert uses.startswith('./') or re.fullmatch(r'[\w./-]+@[0-9a-f]{40}', uses), uses
