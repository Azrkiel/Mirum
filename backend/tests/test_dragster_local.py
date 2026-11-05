"""Native STEP-derived mesh and transactional UI state, with no CAD skips."""
import hashlib
import json
from pathlib import Path

import cadquery as cq
import numpy as np
import pytest

from backend.dragsters import local
from backend.dragsters.spec import DragsterSpec

EXAMPLE = Path(__file__).parents[1] / 'dragsters/examples/wedge.json'


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_step_is_preview_source(tmp_path):
    # A different solid proves the exporter imports STEP instead of rebuilding a wedge.
    step = tmp_path / 'different.step'
    cq.exporters.export(cq.Workplane('XY').box(10, 20, 30).val(), str(step))
    glb = tmp_path / 'different.glb'
    local.export_preview(step, glb)
    mesh = local.preview_mesh(glb.read_bytes())
    assert sorted(mesh.extents) == pytest.approx([0.01, 0.02, 0.03], abs=1e-6)


def test_success_reload_edit_and_provenance(tmp_path):
    state = local.PreviewState()
    assert state.attempt(EXAMPLE.read_text(), tmp_path)
    first = state.accepted
    assert {p.name for p in first.iterdir()} == {'body.step', 'spec.json', 'report.json', 'preview.glb'}
    report = json.loads((first / 'report.json').read_text())
    assert report['passed'] is True
    for name, key in [('spec.json', 'spec_sha256'), ('body.step', 'step_sha256')]:
        assert hashlib.sha256((first / name).read_bytes()).hexdigest() == report[key]
    assert report['preview']['source_step_sha256'] == report['step_sha256']
    assert report['preview']['glb_sha256'] == hashlib.sha256((first / 'preview.glb').read_bytes()).hexdigest()
    mesh = local.preview_mesh((first / 'preview.glb').read_bytes())
    assert sorted(mesh.extents) == pytest.approx([0.032, 0.042, 0.2], abs=1e-5)
    assert np.isfinite(mesh.vertices).all()
    before = snapshot(first)
    assert state.attempt((first / 'spec.json').read_text(), tmp_path)
    second = state.accepted
    assert second != first
    assert json.loads((second / 'report.json').read_text())['volume_mm3'] == pytest.approx(report['volume_mm3'])
    payload = json.loads(EXAMPLE.read_text()) | {'width_mm': 46.0}
    assert state.attempt(json.dumps(payload), tmp_path)
    assert json.loads((state.accepted / 'report.json').read_text())['bounds_mm']['ymax'] == pytest.approx(23)
    assert snapshot(first) == before


@pytest.mark.parametrize('fault', ['json', 'spec', 'build', 'export', 'verification', 'reject', 'preview', 'write', 'rename'])
def test_failed_attempt_preserves_accepted_files_and_state(tmp_path, monkeypatch, fault):
    state = local.PreviewState()
    assert state.attempt(EXAMPLE.read_text(), tmp_path)
    accepted = state.accepted
    before = snapshot(tmp_path)
    payload = EXAMPLE.read_text()
    def fail(*args, **kwargs):
        raise OSError('injected failure')
    if fault == 'json':
        payload = '{'
    elif fault == 'spec':
        payload = json.dumps(json.loads(payload) | {'pocket_center_z_mm': 40.0})
    elif fault == 'build':
        from backend.dragsters import geometry
        monkeypatch.setattr(geometry, 'build_body', fail)
    elif fault == 'export':
        monkeypatch.setattr(cq.exporters, 'export', fail)
    elif fault in ('verification', 'reject'):
        from backend.dragsters import validation
        monkeypatch.setattr(validation, 'verify_step', fail if fault == 'verification' else lambda *a: {'passed':False})
    elif fault == 'preview':
        monkeypatch.setattr(local, 'export_preview', fail)
    elif fault == 'write':
        monkeypatch.setattr(Path, 'write_text', fail)
    else:
        monkeypatch.setattr(Path, 'rename', fail)
    assert not state.attempt(payload, tmp_path)
    assert state.error
    assert state.accepted == accepted
    assert snapshot(tmp_path) == before
    assert list(tmp_path.iterdir()) == [accepted]


def test_bad_preview_is_rejected():
    with pytest.raises(Exception):
        local.preview_mesh(b'not a GLB')
