"""Transport-fixture UI tests; geometry certification is covered by native tests."""
import hashlib
import json

import pytest
import trimesh

from frontend import test_dragster_ui as scenarios


@pytest.fixture
def downloaded_revision():
    # Real GLB bytes exercise the preview decoder without loading OpenCASCADE.
    spec = json.loads(scenarios.dragster_ui.EXAMPLE.read_text())
    files = {
        'spec': json.dumps(spec).encode(),
        'step': b'ISO-10303-21; transport fixture; END-ISO-10303-21;',
        'glb': trimesh.Scene(trimesh.creation.box(extents=[.2, .03, .04])).export(file_type='glb'),
    }
    digest = lambda data: hashlib.sha256(data).hexdigest()
    report = dict(passed=True, revision_id='a'*32, spec_sha256=digest(files['spec']),
                  step_sha256=digest(files['step']), preview={'glb_sha256': digest(files['glb'])})
    files['report'] = json.dumps(report).encode()
    event = dict(status='done', run_id='a'*32, revision_id='a'*32, spec=spec, report=report,
                 digests={fmt: digest(data) for fmt, data in files.items()})
    return event, files


def test_grouped_dimensions_keep_the_accepted_preview_on_invalid_edits(monkeypatch, downloaded_revision):
    scenarios.test_success_invalid_edit_rejection_and_recovery(monkeypatch, downloaded_revision)


def test_cancel_preserves_all_grouped_preview_downloads(monkeypatch, downloaded_revision):
    scenarios.test_cancel_and_late_completion_preserve_previous_result(monkeypatch, downloaded_revision)
