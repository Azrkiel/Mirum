"""Local preview transactions; independent of the CLI and application services."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from .spec import DragsterSpec, canonical_spec


def export_preview(step: Path, destination: Path) -> None:
    """Tessellate a fresh import of the verified STEP, never the builder shape."""
    import cadquery as cq

    body = cq.importers.importStep(str(step)).val()
    # OCCT retains numeric CAD coordinates; glTF specifies metres.
    cq.Assembly(body.scale(0.001)).save(str(destination), exportType='GLTF', tolerance=0.00005,
                           angularTolerance=0.1)
    # Reject missing, corrupt, or empty mesh exports before publishing anything.
    preview_mesh(destination.read_bytes())


def preview_mesh(data: bytes):
    """Decode the actual GLB for the local, offline Plotly viewer."""
    import io
    import numpy as np
    import trimesh

    scene = trimesh.load(io.BytesIO(data), file_type='glb', force='scene')
    mesh = scene.dump(concatenate=True)
    if (not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces)
            or not np.isfinite(mesh.vertices).all()):
        raise ValueError('Preview GLB has no finite triangle mesh')
    return mesh


def generate_result(spec: DragsterSpec, output_root: Path) -> Path:
    """Publish a fresh directory only after STEP and preview both succeed."""
    import cadquery as cq
    from .geometry import build_body
    from .validation import verify_step

    spec = DragsterSpec.model_validate_json(canonical_spec(spec))
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.dragster-preview-stage-', dir=root))
    try:
        step = stage / 'body.step'
        cq.exporters.export(build_body(spec), str(step), exportType='STEP')
        report = verify_step(step, spec)
        if report.get('passed') is not True:
            raise ValueError('STEP verification did not pass')
        export_preview(step, stage / 'preview.glb')
        report['preview'] = {
            'source': 'verified_reimported_step',
            'source_step_sha256': report['step_sha256'],
            'glb_sha256': hashlib.sha256((stage / 'preview.glb').read_bytes()).hexdigest(),
            'linear_tolerance_mm': 0.05, 'angular_tolerance_rad': 0.1,
            'units': 'm',
        }
        (stage / 'spec.json').write_text(canonical_spec(spec), encoding='utf-8')
        (stage / 'report.json').write_text(json.dumps(report, sort_keys=True, indent=2,
                                                    allow_nan=False) + '\n', encoding='utf-8')
        destination = root / f'dragster-{uuid.uuid4().hex}'
        if destination.exists():
            raise FileExistsError('Fresh result name is already occupied')
        stage.rename(destination)
        return destination
    finally:
        if stage.exists():
            shutil.rmtree(stage)


@dataclass
class PreviewState:
    """Only successful transactions replace accepted state."""
    accepted: Path | None = None
    error: str | None = None

    def attempt(self, payload: str, root: Path) -> bool:
        try:
            spec = DragsterSpec.model_validate_json(payload)
            result = generate_result(spec, root)
        except Exception as exc:
            self.error = str(exc)
            return False
        self.accepted = result
        self.error = None
        return True
