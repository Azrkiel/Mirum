"""Static supply-chain checks for the PicoGK runtime and builder Dockerfiles (stdlib only).

Internal references: `picogk-runtime:26.2` is an image built in the same workflow
(.github/workflows/picogk-runtime.yml) and consumed via `COPY --from`/a named stage.
It is exempt from the digest rule only when it is a local, unqualified tag with no
registry host (no '/'), because it can never be pulled from a registry.
Every other external image must carry @sha256:.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = [ROOT / 'spike-picogk' / 'Dockerfile.runtime', ROOT / 'picogk-builder' / 'Dockerfile']
INTERNAL = {'picogk-runtime:26.2'}


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
    return [r for r in refs if r not in INTERNAL]


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


NETWORK_RUN = re.compile(r'apt-get|git\s+clone|git\s+submodule|dotnet\s+(restore|publish)')
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
                assert 'rm -f /tmp/ca-bundle.crt' in line, f'{p.name}: bundle not removed: {line[:90]}'
                if 'apt-get' in line:
                    assert 'Acquire::https::CaInfo' in line, f'{p.name}: apt ignores the CA: {line[:90]}'
    assert seen >= 4  # apt + git (runtime), dotnet (builder), apt (builder runtime stage)


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
    runs = [l for l in wf.replace(chr(92) + chr(10), ' ').splitlines() if 'docker run ' in l]
    assert len(runs) == 2, runs
    for r in runs:
        for flag in ('--network none', '--memory 3g', '--cap-drop ALL', '--security-opt no-new-privileges', '--read-only', '--user '):
            assert flag in r, f'{flag} missing in {r[:80]}'
