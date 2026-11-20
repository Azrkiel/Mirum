"""Check subprocess environment filtering without PicoGK."""
import importlib.util
import os
from pathlib import Path

import pytest

from engineering.regen_cli import minimal_env, run_builder


def test_minimal_env_filters_secrets_and_preserves_runtime_settings(monkeypatch):
    excluded = (
        'GEMINI_API_KEY_HEAVY', 'GITHUB_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'MY_PASSWORD',
        'DOTNET_AuthMode', 'DOTNET_Credential', 'UNRELATED_SETTING',
    )
    for name in excluded:
        monkeypatch.setenv(name, 'planted')
    monkeypatch.setenv('PATH', '/custom/bin')
    monkeypatch.setenv('REGEN_GCC_LIB', '/custom/gcc')
    monkeypatch.setenv('HOME', '/custom/home')
    monkeypatch.setenv('TMPDIR', '/custom/tmp')
    monkeypatch.setenv('DOTNET_GCHEAPHARDLIMIT', '0x10000000')
    monkeypatch.setenv('OPENBLAS_NUM_THREADS', '8')
    monkeypatch.setenv('OMP_NUM_THREADS', '8')

    builder = Path('/x/builder')
    env = minimal_env(builder)

    assert {name.upper() for name in env}.isdisjoint(name.upper() for name in excluded)
    assert env['PATH'] == '/custom/bin'
    assert env['LD_LIBRARY_PATH'] == f'/custom/gcc:{builder.parent}'
    assert env['HOME'] == '/custom/home'
    assert env['TMPDIR'] == '/custom/tmp'
    assert env['DOTNET_GCHEAPHARDLIMIT'] == '0x10000000'
    assert env['OPENBLAS_NUM_THREADS'] == env['OMP_NUM_THREADS'] == '2'


@pytest.mark.skipif(os.name == 'nt', reason='requires a POSIX shell')
def test_run_builder_does_not_inherit_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'planted')
    builder = tmp_path / 'builder'
    builder.write_text(
        '#!/bin/sh\n'
        'set -e\n'
        'command -p env > "$2.env"\n'
        'printf "%084d" 0 > "$2"\n'
        'printf \'{"tris": 1}\\n\'\n',
        encoding='utf-8',
    )
    builder.chmod(0o755)
    spec_path = tmp_path / 'spec.json'
    spec_path.write_text('{}', encoding='utf-8')
    stl_path = tmp_path / 'raw.stl'

    assert run_builder(builder, spec_path, stl_path) == ({'tris': 1}, None)
    dumped_env = (tmp_path / 'raw.stl.env').read_bytes().splitlines()
    assert b'GEMINI_API_KEY_HEAVY' not in {line.partition(b'=')[0] for line in dumped_env}


def test_native_evidence_child_env_filters_secrets(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'planted')
    monkeypatch.setenv('DOTNET_AccessToken', 'planted')
    monkeypatch.setenv('DOTNET_GCHEAPHARDLIMIT', '0x10000000')
    monkeypatch.setenv('PYTHONPATH', '/custom/backend')
    script_path = Path(__file__).resolve().parents[2] / 'scripts' / 'regen_native_evidence.py'
    spec = importlib.util.spec_from_file_location('regen_native_evidence', script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env = module.child_env()

    assert 'GEMINI_API_KEY_HEAVY' not in env
    assert 'DOTNET_ACCESSTOKEN' not in {name.upper() for name in env}
    assert env['DOTNET_GCHEAPHARDLIMIT'] == '0x10000000'
    assert env['PYTHONPATH'] == '/custom/backend'
    assert env['OPENBLAS_NUM_THREADS'] == env['OMP_NUM_THREADS'] == '2'
