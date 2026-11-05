"""Bounded deterministic native generation and mandatory accepted publication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

if __package__.startswith('backend.'):
    from .. import artifacts
    from ..process_runner import ProcessRunner
    from ..compiler import _WORK_DIR
else:
    import artifacts
    from process_runner import ProcessRunner
    from compiler import _WORK_DIR
from .spec import DragsterSpec, canonical_spec


class GenerationError(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


async def generate(spec: DragsterSpec, *, runner=None) -> dict:
    """Retain scratch ownership until worker cleanup and publication settle."""
    spec = DragsterSpec.model_validate_json(canonical_spec(spec))
    own_runner = runner is None
    runner = runner or ProcessRunner(timeout=30, memory_mb=1536, output_limit=1048576)
    _WORK_DIR.mkdir(parents=True, exist_ok=True)
    run_id = ''
    try:
        with tempfile.TemporaryDirectory(prefix='dragster-', dir=_WORK_DIR) as temporary:
            stage = Path(temporary)
            env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'SYSTEMROOT',
                   'WINDIR', 'TEMP', 'TMP') if key in os.environ}
            env.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
            native = await runner.run([sys.executable, str(Path(__file__).with_name('worker.py')),
                                      str(stage)], input=canonical_spec(spec).encode(), cwd=str(stage), env=env)
            if native.get('code') or native.get('returncode') != 0:
                reason = native.get('code') or native.get('stderr', '').strip()[-1200:] or 'native generation failed'
                raise GenerationError(f'Dragster worker: {reason}')
            results = list(stage.glob('dragster-*'))
            if len(results) != 1:
                raise GenerationError('Native worker produced no unique verified result')
            result = results[0]
            files = dict(spec='spec.json', report='report.json', step='body.step', glb='preview.glb')
            for name in files.values():
                path = result / name
                if not path.is_file() or not 0 < path.stat().st_size <= artifacts._MAX_ARTIFACT_BYTES:
                    raise GenerationError('Required artifact missing or exceeds delivery budget')
            data = {fmt: (result / name).read_bytes() for fmt, name in files.items()}
            report = json.loads(data['report'])
            if (report.get('passed') is not True or data['spec'] != canonical_spec(spec).encode()
                    or report.get('spec_sha256') != digest(data['spec'])
                    or report.get('step_sha256') != digest(data['step'])
                    or report.get('preview', {}).get('source_step_sha256') != digest(data['step'])
                    or report.get('preview', {}).get('glb_sha256') != digest(data['glb'])):
                raise GenerationError('Native artifact evidence mismatch')
            # Store under a private name, then rename atomically to the run id.
            # Download paths cannot resolve staging names. No partial accepted run.
            artifacts.sweep()
            run_id = artifacts.new_run_id()
            report['revision_id'] = run_id
            report['coordinate_frame'] = {'cad': 'mm; X nose to rear, Y transverse, Z up',
                                          'preview': 'metres; glTF Y up'}
            data['report'] = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
            try:
                artifacts.publish_required(run_id, data)
            except Exception as exc:
                raise GenerationError(f"Required artifact publication failed: {exc}") from exc
            return dict(run_id=run_id, revision_id=run_id, spec=spec.model_dump(), report=report,
                        downloads={fmt: True for fmt in files},
                        digests={fmt: digest(value) for fmt, value in data.items()})
    finally:
        if own_runner:
            await runner.close()
