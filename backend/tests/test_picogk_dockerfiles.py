"""Static supply-chain checks for the PicoGK runtime and builder Dockerfiles (stdlib only).

Internal references: `picogk-runtime:26.2` is an image built in the same workflow
(.github/workflows/picogk-runtime.yml) and consumed via `COPY --from`/a named stage.
It is exempt from the digest rule only when it is a local, unqualified tag with no
registry host (no '/'), because it can never be pulled from a registry.
Every other external image must carry @sha256:.
"""
import os
import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = [ROOT / 'spike-picogk' / 'Dockerfile.runtime', ROOT / 'picogk-builder' / 'Dockerfile']
INTERNAL = {'picogk-runtime:26.2'}

# The native gate image (Dockerfile.verify) ships backend/ only, so repo-level files are absent there.
# ci.yml sets MIRUM_NATIVE_GATE_IMAGE=1 on exactly those docker runs. Anywhere else, missing files FAIL
# (deleting .github/workflows/picogk-runtime.yml must not silently disable these checks); the
# schema-tests job in ci.yml runs this module from a full checkout.
if os.environ.get('MIRUM_NATIVE_GATE_IMAGE') == '1':
    pytest.skip('native gate image has no repo-level Docker/workflow files', allow_module_level=True)


def _logical_lines(path):
    out, buf = [], ''
    for raw in path.read_text(encoding='utf-8').splitlines():
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        if s.endswith(chr(92)):
            buf += s[:-1] + ' '
            continue
        out.append(buf + s)
        buf = ''
    return out


def _external_refs(path):
    stages, refs = set(), []
    for line in _logical_lines(path):
        m = re.match(r'FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?', line, re.I)
        if m:
            if m.group(1) not in stages:
                refs.append(m.group(1))
            if m.group(2):
                stages.add(m.group(2))
            continue
        m = re.match(r'COPY\s+.*?--from=(\S+)', line, re.I)
        if m and m.group(1) not in stages:
            refs.append(m.group(1))
    return [r for r in refs if r not in INTERNAL and r != 'scratch']  # scratch is the empty base, not an image


def test_dockerfiles_exist():
    for p in DOCKERFILES:
        assert p.is_file(), p


def test_external_images_are_digest_pinned():
    for p in DOCKERFILES:
        for ref in _external_refs(p):
            assert '@sha256:' in ref, f'{p.name}: {ref} is not pinned by digest'
            assert re.search(r'@sha256:[0-9a-f]{64}$', ref), f'{p.name}: bad digest in {ref}'


def test_internal_runtime_tag_is_local_only():
    for ref in INTERNAL:
        assert '/' not in ref and '@' not in ref


def test_ca_is_never_copied_unconditionally():
    for p in DOCKERFILES:
        for line in _logical_lines(p):
            if re.match(r'(COPY|ADD)\b', line, re.I):
                assert 'avast-ca' not in line and '.pem' not in line, f'{p.name}: {line}'


def test_ca_comes_only_from_optional_secret_mount():
    for p in DOCKERFILES:
        text = p.read_text(encoding='utf-8')
        if 'extra_ca' in text:
            assert 'type=secret,id=extra_ca,required=false' in text.replace(' ', ''), p.name


def test_tls_verification_never_disabled():
    bad = [r'--insecure', r'curl\s+(?:\S+\s+)*-k\b', r'--no-check-certificate', r'sslverify\s*=\s*false',
           r'http\.sslverify', r'GIT_SSL_NO_VERIFY', r'NODE_TLS_REJECT_UNAUTHORIZED', r'--trusted-host',
           r'allow-insecure-repositories', r'AllowUnauthenticated', r'--allow-unauthenticated',
           r'DOTNET_SYSTEM_NET_HTTP_USESOCKETSHTTPHANDLER', r'check-certificate\s*=\s*off']
    for p in DOCKERFILES:
        text = p.read_text(encoding='utf-8')
        for pat in bad:
            assert not re.search(pat, text, re.I), f'{p.name}: matches {pat}'


NETWORK_RUN = re.compile(r'apt-get|git\s+clone|git\s+submodule|dotnet\s+(restore|publish)|\bcurl\b|\bwget\b')
BUILDER = ROOT / 'picogk-builder'
WORKFLOW = ROOT / '.github' / 'workflows' / 'picogk-runtime.yml'


def test_every_network_run_has_optional_ca_handling():
    seen = 0
    for p in DOCKERFILES:
        for line in _logical_lines(p):
            if re.match(r'RUN\b', line, re.I) and NETWORK_RUN.search(line):
                seen += 1
                flat = line.replace(' ', '')
                assert '--mount=type=secret,id=extra_ca,required=false' in flat, f'{p.name}: {line[:90]}'
                assert 'SSL_CERT_FILE' in line and '/tmp/ca-bundle.crt' in line, f'{p.name}: {line[:90]}'
                assert re.search(r'rm\s+-r?f\s[^;&|]*/tmp/ca-bundle\.crt', line), f'{p.name}: bundle not removed: {line[:90]}'
                if 'apt-get' in line:
                    assert 'Acquire::https::CaInfo' in line, f'{p.name}: apt ignores the CA: {line[:90]}'
    assert seen == 4  # runtime: apt, git clone/submodules, Boost curl fetch; builder: dotnet restore+publish. Update when adding one.


def test_nuget_restore_is_locked_and_sources_are_pinned():
    csproj = (BUILDER / 'builder.csproj').read_text(encoding='utf-8')
    assert '<RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>' in csproj
    lock = BUILDER / 'packages.lock.json'
    assert lock.is_file(), 'commit picogk-builder/packages.lock.json'
    import json
    deps = json.loads(lock.read_text(encoding='utf-8'))['dependencies']
    assert any('PicoGK' in pkgs for pkgs in deps.values())
    cfg = (BUILDER / 'nuget.config').read_text(encoding='utf-8')
    assert '<clear />' in cfg and cfg.count('<add ') == 1
    assert 'https://api.nuget.org/v3/index.json' in cfg
    text = (BUILDER / 'Dockerfile').read_text(encoding='utf-8')
    assert 'dotnet restore' in text and '--locked-mode' in text and '--no-restore' in text
    assert 'packages.lock.json' in text and 'nuget.config' in text
    ignore = (BUILDER / 'Dockerfile.dockerignore').read_text(encoding='utf-8')
    assert '!picogk-builder/packages.lock.json' in ignore and '!picogk-builder/nuget.config' in ignore


def test_workflow_pip_installs_require_hashes_and_containers_are_hardened():
    wf = WORKFLOW.read_text(encoding='utf-8')
    reqs = (BUILDER / 'requirements-test.txt').read_text(encoding='utf-8')
    entries = [l for l in reqs.splitlines() if l.strip() and not l.startswith('#')]
    assert entries and all('--hash=sha256:' in l for l in entries)
    for m in re.finditer(r'^\s*python -m pip install.*$', wf, re.M):
        assert '--require-hashes' in m.group(0) and '-r picogk-builder/requirements-test.txt' in m.group(0), m.group(0)
    runs = [shlex.split(l) for l in wf.replace(chr(92) + chr(10), ' ').splitlines() if 'docker run ' in l]
    assert len(runs) == 2, runs
    for argv in runs:
        opts = argv[argv.index('run') + 1:argv.index('mirum-backend:release')]
        # Each required flag exactly once, with its value.
        for flag, value in (('--network', 'none'), ('--cap-drop', 'ALL'), ('--security-opt', 'no-new-privileges'),
                            ('--memory', '3g'), ('--user', '$(id -u):$(id -g)')):
            assert opts.count(flag) == 1, (flag, opts)
            assert opts[opts.index(flag) + 1] == value, (flag, opts)
        assert opts.count('--read-only') == 1, opts
        # Nothing that widens the sandbox.
        joined = ' '.join(opts)
        for bad in ('--privileged', '--cap-add', '--pid host', '--pid=host', '--ipc host', '--ipc=host',
                    '--network host', '--network=host', '--net host', '--net=host', 'seccomp=unconfined',
                    'apparmor=unconfined', '--device', '/var/run/docker.sock'):
            assert bad not in joined, (bad, opts)
        assert opts.count('--security-opt') == 1  # a second --security-opt could carry seccomp=unconfined


def test_ci_wires_the_skip_marker_and_the_always_run_job():
    ci = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
    shard_runs = [l for l in ci.splitlines() if 'docker run' in l and 'ci_shard.py' in l]
    assert len(shard_runs) == 2
    assert all('MIRUM_NATIVE_GATE_IMAGE=1' in l for l in shard_runs)
    assert 'backend/tests/test_picogk_dockerfiles.py' in ci  # schema-tests job runs it on a full checkout


def test_workflow_requires_all_native_tests_to_pass():
    wf = WORKFLOW.read_text(encoding='utf-8')
    assert 'junit.xml' in wf and 'passed < 4' in wf


def test_runtime_builds_a_sha256_verified_boost_of_at_least_1_82():
    text = (ROOT / 'spike-picogk' / 'Dockerfile.runtime').read_text(encoding='utf-8')
    ver = re.search(r'^ARG BOOST_VERSION=(\d+)\.(\d+)\.(\d+)$', text, re.M)
    assert ver and (int(ver.group(1)), int(ver.group(2))) >= (1, 82)
    assert re.search(r'^ARG BOOST_SHA256=[0-9a-f]{64}$', text, re.M)
    flat = ' '.join(_logical_lines(ROOT / 'spike-picogk' / 'Dockerfile.runtime'))
    # Verified before it is extracted, over HTTPS only, into a private prefix OpenVDB is pointed at.
    assert flat.index('sha256sum -c') < flat.index('tar -xzf')
    assert 'https://archives.boost.io/' in flat and 'http://' not in flat
    assert '--prefix=/opt/boost' in flat and '-DBOOST_ROOT=/opt/boost' in flat
    assert '-DBoost_NO_SYSTEM_PATHS=ON' in flat
    assert 'libboost' not in flat.split('apt-get')[-1].split('/picogk/apt-packages.json')[0]


def test_bake_builds_from_the_local_checkout_not_a_git_context():
    # bake-action defaults to a git context, which ignores .dockerignore and would ship backend/tests.
    for rel in ('.github/workflows/picogk-runtime.yml', '.github/actions/build-release-images/action.yml'):
        text = (ROOT / rel).read_text(encoding='utf-8')
        n = len(re.findall(r'uses: docker/bake-action@', text))
        assert n and len(re.findall(r'^\s+source: \.\s', text, re.M)) == n, rel


# ---- N1c-3b: the native gate image carries the pinned mirum-native binary (release images stay unchanged) ----
def _native_pin():
    import sys
    sys.path.insert(0, str(ROOT / 'backend'))
    from engineering.program.adapters import native_pin
    return native_pin


def _verify_lines():
    return _logical_lines(ROOT / 'Dockerfile.verify')


def test_verify_image_copies_the_kernel_by_full_digest_never_a_tag():
    pin = _native_pin()
    copies = [l for l in _verify_lines() if re.match(r'COPY\s+.*--from=ghcr\.io/azrkiel/mirum-native', l, re.I)]
    assert len(copies) == 1, copies
    m = re.match(r'COPY\s+--from=(\S+)\s+(\S+)\s+(\S+)$', copies[0])
    assert m, copies[0]
    ref, src, dst = m.groups()
    assert '@sha256:' in ref and re.fullmatch(r'ghcr\.io/azrkiel/mirum-native@sha256:[0-9a-f]{64}', ref), ref
    assert ref == pin.IMAGE
    assert src == dst == pin.BINARY_PATH


def test_native_sha_file_matches_the_pin_and_the_installed_path():
    pin = _native_pin()
    text = (ROOT / 'native-kernel' / 'mirum-native.sha256').read_text(encoding='utf-8').strip().splitlines()
    assert len(text) == 1
    m = re.fullmatch(r'([0-9a-f]{64})  (/\S+)', text[0])
    assert m, text
    assert m.group(1) == pin.BINARY_SHA256 and m.group(2) == pin.BINARY_PATH


def test_verify_image_checks_the_hash_and_locks_down_the_binary():
    pin = _native_pin()
    lines = _verify_lines()
    idx = [i for i, l in enumerate(lines) if '--from=ghcr.io/azrkiel/mirum-native' in l][0]
    sha_file = '/workspace/native-kernel/mirum-native.sha256'
    # the sha file must already be in the image when it is checked
    copy_kernel_dir = [i for i, l in enumerate(lines) if l.startswith('COPY native-kernel/')]
    assert copy_kernel_dir and copy_kernel_dir[0] < idx + 2
    run = lines[idx + 1]
    assert run.startswith('RUN ') and f'sha256sum -c {sha_file}' in run
    assert 'chown root:root' in run and 'chmod 0755' in run and pin.BINARY_PATH in run
    assert 'chmod -R' not in run
    # nothing after the check may rewrite the binary tree
    for later in lines[idx + 2:]:
        assert '/opt/mirum-native' not in later


def test_ci_gate_runs_require_the_binary_and_kernel_env():
    ci = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
    shard_runs = [l for l in ci.splitlines() if 'docker run' in l and 'ci_shard.py' in l]
    assert len(shard_runs) == 2
    for l in shard_runs:
        for env in ('MIRUM_NATIVE_GATE_IMAGE=1', 'MIRUM_NATIVE_REQUIRE_BINARY=1', 'MIRUM_NATIVE_KERNEL=1',
                    'MIRUM_NATIVE_BIN=/opt/mirum-native/bin/mirum-native'):
            assert f'-e {env}' in l, (env, l)


def test_release_images_do_not_carry_the_native_binary():
    for rel in ('Dockerfile', 'backend/Dockerfile', 'frontend/Dockerfile'):
        p = ROOT / rel
        if p.is_file():
            assert 'mirum-native' not in p.read_text(encoding='utf-8'), rel
