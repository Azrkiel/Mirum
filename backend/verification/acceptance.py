"""Shared, fail-closed structural acceptance for a single delivered revision.

Trusted-local Linux/OCP deployment only. Not a hostile-code sandbox, geometric
intent/GD&T verifier, or engineering certification. Native work stays in an
owned bounded subprocess. GLB is rendered from the measured STEP, not a second
execution of generated source.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


class AcceptanceError(RuntimeError):
    """An artifact must not be delivered as structurally accepted."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def accept_script(source: str, *, output_variable: str = 'auto', execute=None) -> dict:
    """Execute once, capture STEP, inspect/reimport/render, then return bytes.

    Temporary artifacts are owned until capture/validation/rendering and cleanup
    finish. Callers publish returned bytes only after this function succeeds.
    ``execute`` is the existing AST-gated compiler seam, not a bypass.
    """
    if output_variable not in {'auto', 'result', 'assembly'}:
        raise AcceptanceError('Unsupported native output variable')
    if not isinstance(source, str) or not source.strip() or len(source) > 500000:
        raise AcceptanceError('Source is empty or exceeds the acceptance budget')
    if __package__.startswith('backend.'):
        from ..compiler import execute_cad_script, _WORK_DIR
        from ..process_runner import ProcessRunner
    else:
        from compiler import execute_cad_script, _WORK_DIR
        from process_runner import ProcessRunner
    execute = execute or execute_cad_script
    _WORK_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='accept-', dir=_WORK_DIR) as temporary:
        stage = Path(temporary)
        step_path = stage / 'model.step'
        glb_path = stage / 'model.glb'
        report_path = stage / 'report.json'
        script = source.replace('output.glb', str(stage / 'discard.glb'))
        if output_variable == 'auto':
            select = 'try:\n    _mirum_capture = assembly\nexcept NameError:\n    _mirum_capture = result\n'
        else:
            select = f'_mirum_capture = {output_variable}\n'
        script += ('\n\nimport cadquery as _mirum_cq\n' + select +
                   'if isinstance(_mirum_capture, _mirum_cq.Assembly):\n'
                   f'    _mirum_capture.save({str(step_path)!r}, exportType="STEP")\n'
                   'else:\n'
                   f'    _mirum_cq.exporters.export(_mirum_capture, {str(step_path)!r}, exportType="STEP")\n')
        outcome = await execute(script)
        if not isinstance(outcome, dict) or outcome.get('status') != 'success':
            raise AcceptanceError('CAD source execution failed')
        if not step_path.is_file() or step_path.stat().st_size <= 0:
            raise AcceptanceError('CAD execution produced no STEP geometry')
        # No provider keys or full inherited environment reach the native worker.
        env = {name: os.environ[name] for name in ('PATH', 'HOME', 'LANG', 'SYSTEMROOT',
               'WINDIR', 'USERPROFILE', 'TEMP', 'TMP') if name in os.environ}
        env.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
                   PYTHONDONTWRITEBYTECODE='1')
        runner = ProcessRunner(timeout=30, output_limit=1048576, memory_mb=1536)
        try:
            native = await runner.run([sys.executable,
                str(Path(__file__).with_name('acceptance_worker.py')),
                str(step_path), str(glb_path), str(report_path)], cwd=str(stage), env=env)
        finally:
            await runner.close()
        if native.get('code') or native.get('returncode') != 0:
            raise AcceptanceError('Native acceptance worker failed or exceeded its budget')
        try:
            certificate = json.loads(report_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise AcceptanceError('Native acceptance evidence is unavailable') from None
        if not isinstance(certificate, dict) or certificate.get('passed') is not True:
            raise AcceptanceError('Structural geometry validation failed')
        if step_path.stat().st_size > 500 * 1024 * 1024 or glb_path.stat().st_size > 200 * 1024 * 1024:
            raise AcceptanceError('Accepted artifact exceeds the delivery size budget')
        step_data, glb_data = step_path.read_bytes(), glb_path.read_bytes()
        hashes = certificate.get('artifacts', {})
        if (hashes.get('step_sha256') != _digest(step_data)
                or hashes.get('glb_sha256') != _digest(glb_data)):
            raise AcceptanceError('Artifact bytes changed after native acceptance')
        certificate['artifacts']['source_sha256'] = _digest(source.encode('utf-8'))
        certificate['engineering_certified'] = False
        return {'glb': glb_data, 'step': step_data, 'geometry_certificate': certificate}
