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
