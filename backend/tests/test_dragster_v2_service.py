"""v2 native transactions, strict worker input and unchanged v1 delivery."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from backend import artifacts
from backend.dragsters import local, service
from backend.dragsters.spec import DragsterSpec
from backend.dragsters.spec_v2 import canonical_spec_v2
from backend.dragsters.styles import REGISTRY, load_preset


@pytest.mark.parametrize('style', REGISTRY)
def test_local_v2_transaction(style, tmp_path):
    spec = load_preset(style)
    result = local.generate_result_v2(spec, tmp_path)
    assert {p.name for p in result.iterdir()} == {'body.step', 'preview.glb', 'spec.json', 'report.json'}
    assert (result / 'spec.json').read_text() == canonical_spec_v2(spec)
    report = json.loads((result / 'report.json').read_bytes())
    assert report['schema_version'] == 2 and report['passed'] is True
    assert report['generator_version'] == REGISTRY[style].generator_version
    assert report['preview']['glb_sha256'] == hashlib.sha256((result / 'preview.glb').read_bytes()).hexdigest()
    assert not list(tmp_path.glob('.dragster-*'))


@pytest.mark.parametrize('failure', ['verification', 'preview'])
def test_v2_failed_transaction_removes_stage(failure, monkeypatch, tmp_path):
    from backend.dragsters import validation_v2
    def fail(*args, **kwargs):
        raise ValueError('forced ' + failure)
    monkeypatch.setattr(validation_v2 if failure == 'verification' else local,
                        'verify_step_v2' if failure == 'verification' else 'export_preview', fail)
    with pytest.raises(ValueError, match='forced'):
        local.generate_result_v2(load_preset('wedge'), tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_v2_real_runner_keeps_env_limits_and_detects_tampering(monkeypatch, tmp_path):
    monkeypatch.setattr(artifacts, '_RUNS_DIR', tmp_path / 'runs')
    original = service.ProcessRunner.run
    async def tamper(self, command, **kwargs):
        assert self.memory_mb == 1536 and self.timeout == 30
        assert kwargs['env']['MALLOC_ARENA_MAX'] == '2'
        assert not any('KEY' in key or 'TOKEN' in key for key in kwargs['env'])
        result = await original(self, command, **kwargs)
        assert result['returncode'] == 0, result
        next(Path(command[-1]).glob('dragster-*/preview.glb')).write_bytes(b'tampered')
        return result
    monkeypatch.setattr(service.ProcessRunner, 'run', tamper)
    with pytest.raises(service.GenerationError, match='evidence mismatch'):
        await service.generate(load_preset('wedge'))
    assert not list(artifacts._RUNS_DIR.glob('*'))


@pytest.mark.parametrize('payload', [b' ' * 16385, b'{"schema_version":2,"style":"arbitrary.module"}', b'[]'])
def test_worker_rejects_oversized_or_untrusted_input(payload, tmp_path):
    worker = Path(service.__file__).with_name('worker.py')
    result = subprocess.run([sys.executable, str(worker), str(tmp_path)], input=payload,
                            capture_output=True, timeout=10, env=service.worker_env())
    assert result.returncode == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('style', REGISTRY)
def test_cli_accepts_v2(style, tmp_path):
    from backend.dragsters.cli import main
    source = tmp_path / 'input.json'
    source.write_text(canonical_spec_v2(load_preset(style)))
    root = tmp_path / 'output'
    assert main(['--spec', str(source), '--output-root', str(root)]) == 0
    result, = root.iterdir()
    report = json.loads((result / 'report.json').read_text())
    assert report['schema_version'] == 2 and report['passed']
