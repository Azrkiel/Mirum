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


def generate_result_v2(spec, output_root: Path) -> Path:
    """Publish v2 evidence only after independent STEP checks and preview succeed."""
    import cadquery as cq
    from .spec_v2 import canonical_spec_v2, parse_v2
    from .styles import resolve_build
    from .validation_v2 import verify_step_v2

    spec = parse_v2(canonical_spec_v2(spec))
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.dragster-preview-stage-', dir=root))
    try:
        step = stage / 'body.step'
        cq.exporters.export(resolve_build(spec.style)(spec), str(step), exportType='STEP')
        report = verify_step_v2(step, spec)
        if report.get('passed') is not True:
            raise ValueError('STEP verification did not pass')
        export_preview(step, stage / 'preview.glb')
        report['preview'] = {
            'source': 'verified_reimported_step',
            'source_step_sha256': report['step_sha256'],
            'glb_sha256': hashlib.sha256((stage / 'preview.glb').read_bytes()).hexdigest(),
            'linear_tolerance_mm': 0.05, 'angular_tolerance_rad': 0.1, 'units': 'm',
        }
        (stage / 'spec.json').write_text(canonical_spec_v2(spec), encoding='utf-8')
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
            from .spec_v2 import parse_any
            spec = parse_any(payload)
            result = (generate_result if spec.schema_version == 1 else generate_result_v2)(spec, root)
        except Exception as exc:
            self.error = str(exc)
            return False
        self.accepted = result
        self.error = None
        return True


def render_preview(data: bytes, *, envelopes=None) -> None:
    """Offline orbit viewer shared by standalone and integrated parameter forms."""
    import streamlit as st
    import plotly.graph_objects as go
    mesh = preview_mesh(data)
    # The saved GLB is metres/Y-up; show its decoded mesh in CAD mm/Z-up.
    vertices = mesh.vertices * 1000
    x, y, z = vertices[:, 0], -vertices[:, 2], vertices[:, 1]
    i, j, k = mesh.faces.T
    figure = go.Figure(go.Mesh3d(x=x, y=y, z=z, i=i, j=j, k=k,
                               color='lightsteelblue', flatshading=True))
    if envelopes:
        import math
        for envelope in envelopes:
            cx, cy, cz = envelope['center_mm']
            radius, width, count = envelope['diameter_mm'] / 2, envelope['width_mm'], 40
            vertices = [(cx + radius * math.cos(2 * math.pi * t / count),
                         cy + sign * width / 2,
                         cz + radius * math.sin(2 * math.pi * t / count))
                        for sign in (-1, 1) for t in range(count)]
            faces = []
            for t in range(count):
                nxt = (t + 1) % count
                faces.extend([(t, nxt, count + t), (nxt, count + nxt, count + t)])
            for t in range(1, count - 1):
                faces.extend([(0, t + 1, t), (count, count + t, count + t + 1)])
            ex, ey, ez = zip(*vertices)
            ei, ej, ek = zip(*faces)
            figure.add_trace(go.Mesh3d(x=ex, y=ey, z=ez, i=ei, j=ej, k=ek,
                color='slategray', opacity=0.25, showlegend=True,
                name=f"{envelope['axle']} {envelope['side']} envelope — not in STEP"))
    span = max(mesh.extents)
    figure.update_layout(
        scene=dict(aspectmode='manual',
                   aspectratio=dict(x=mesh.extents[0] / span,
                                    y=mesh.extents[2] / span,
                                    z=mesh.extents[1] / span),
                   camera=dict(eye=dict(x=1.1, y=1.1, z=0.8)),
                   xaxis_title='X (mm)', yaxis_title='Y (mm)', zaxis_title='Z (mm)'),
        margin=dict(l=0, r=0, t=0, b=0), height=500)
    st.plotly_chart(figure, use_container_width=True)
