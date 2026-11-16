"""F1 regression: the dragster worker env caps glibc malloc arenas.

glibc reserves address space per malloc arena and sizes the arena count by CPU count. On many-core
hosts (16 CPUs locally) lofted builds reach VmPeak ~2.1 GiB, past the worker's 1536 MiB RLIMIT_AS,
and fail with TIMEOUT / rc -9 / -11. ``MALLOC_ARENA_MAX=2`` bounds it (~1.25 GiB) without raising
the ProcessRunner limits (master plan invariant 7).
"""
import json
from pathlib import Path
import sys

import pytest

from backend.dragsters import service
from backend.dragsters.spec import DragsterSpec

REPO = Path(__file__).parents[2]
EXAMPLE = Path(__file__).parents[1] / 'dragsters/examples/wedge.json'

# Same budget the service uses; never raise it (master plan invariant 7).
TIMEOUT_S, MEMORY_MB = 30, 1536

LOFTED_RUN = '''
import json, sys
from pathlib import Path
import cadquery as cq
from backend.dragsters.styles import resolve_build, load_preset
from backend.dragsters.validation_v2 import verify_step_v2
from backend.dragsters.local import export_preview
style, out = sys.argv[1], Path(sys.argv[2])
spec = load_preset(style)
step = out / f'{style}.step'
cq.exporters.export(resolve_build(style)(spec), str(step), exportType='STEP')
report = verify_step_v2(step, spec)
export_preview(step, out / f'{style}.glb')
status = dict(l.split(':', 1) for l in open('/proc/self/status') if l.startswith(('VmHWM', 'VmPeak')))
print(json.dumps({'passed': report['passed'], 'VmPeak': status['VmPeak'].strip()}))
'''


def test_worker_env_caps_malloc_arenas_and_threads():
    env = service.worker_env()
    assert env['MALLOC_ARENA_MAX'] == '2'
    assert env['OMP_NUM_THREADS'] == '1' and env['OPENBLAS_NUM_THREADS'] == '1'


def test_worker_env_carries_no_secrets(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'secret')
    monkeypatch.setenv('GEMINI_API_KEY_PRO', 'secret')
    assert not any(key.startswith('GEMINI') for key in service.worker_env())


@pytest.mark.asyncio
async def test_generate_passes_worker_env_to_runner():
    seen = {}

    class RecordingRunner:
        async def run(self, command, *, input=b'', cwd=None, env=None):
            seen['env'] = env
            return dict(code='TIMEOUT', returncode=-9, stdout='', stderr='')

    spec = DragsterSpec.model_validate_json(EXAMPLE.read_text())
    with pytest.raises(service.GenerationError, match='TIMEOUT'):
        await service.generate(spec, runner=RecordingRunner())
    assert seen['env'] == service.worker_env()


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='RLIMIT_AS is enforced on Linux only')
@pytest.mark.asyncio
@pytest.mark.parametrize('style', ['teardrop', 'narrow_waist'])
async def test_lofted_preset_fits_worker_memory_limit(style, tmp_path):
    runner = service.ProcessRunner(timeout=TIMEOUT_S, memory_mb=MEMORY_MB, output_limit=1048576)
    try:
        result = await runner.run([sys.executable, '-W', 'ignore', '-c', LOFTED_RUN, style, str(tmp_path)],
                                  cwd=str(REPO), env=service.worker_env())
    finally:
        await runner.close()
    assert result.get('code') is None and result['returncode'] == 0, (
        result.get('code'), result['returncode'], result['stderr'][-1500:])
    assert json.loads(result['stdout'].strip().splitlines()[-1])['passed'] is True
