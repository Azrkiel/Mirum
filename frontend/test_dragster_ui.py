"""Main workflow AppTest with real native preview bytes and controlled transport."""
from concurrent.futures import Future
import hashlib
import json
from pathlib import Path
import sys
from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).parent))
import dragster_ui
from backend.dragsters.local import generate_result
from backend.dragsters.spec import DragsterSpec


@pytest.fixture
def accepted(tmp_path):
    spec = DragsterSpec.model_validate_json(dragster_ui.EXAMPLE.read_text())
    result = generate_result(spec, tmp_path)
    files = {fmt:(result/name).read_bytes() for fmt,name in
             dict(spec='spec.json', report='report.json', step='body.step', glb='preview.glb').items()}
    report = json.loads(files['report'])
    report['revision_id'] = 'a'*32
    files['report'] = json.dumps(report).encode()
    return dict(status='done', run_id='a'*32, revision_id='a'*32, spec=spec.model_dump(), report=report,
                digests={fmt:hashlib.sha256(data).hexdigest() for fmt,data in files.items()}), files


def app():
    return AppTest.from_string('from dragster_ui import render_dragster\nrender_dragster("http://unused", lambda: {})', default_timeout=15).run()


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_success_invalid_edit_rejection_and_recovery(monkeypatch, accepted):
    def start(state, *args):
        future = Future(); future.set_result(accepted)
        return state.begin(), future, Event()
    monkeypatch.setattr(dragster_ui, 'start_attempt', start)
    at = app()
    button(at, 'Generate body').click().run()
    assert not at.exception
    state = at.session_state.dragster_state
    assert state.accepted['run_id'] == 'a'*32
    files = state.files.copy()
    at.number_input(key='dragster_param_pocket_center_z_mm').set_value(40.0).run()
    button(at, 'Generate body').click().run()
    assert at.error
    assert at.session_state.dragster_state.files == files
    assert any('differ' in v.value for v in at.info)
    at.number_input(key='dragster_param_pocket_center_z_mm').set_value(accepted[0]['spec']['pocket_center_z_mm']).run()
    button(at, 'Generate body').click().run()
    assert not at.exception and not at.error


def test_cancel_and_late_completion_preserve_previous_result(monkeypatch, accepted):
    future, cancel = Future(), Event()
    def start(state, *args):
        return state.begin(), future, cancel
    monkeypatch.setattr(dragster_ui, 'start_attempt', start)
    at = app()
    state = at.session_state.dragster_state
    state.accept(state.begin(), *accepted)
    button(at, 'Generate body').click().run()
    assert button(at, 'Generate body').disabled
    button(at, 'Cancel generation').click().run()
    assert cancel.is_set()
    future.set_result(accepted)
    at.run()
    assert at.session_state.dragster_state.files == accepted[1]
    assert any('cancelled' in e.value for e in at.error)
    assert not at.exception


def test_json_import_is_atomic_and_first_failure_is_clear():
    at = app()
    initial = at.number_input[0].value
    at.text_area[0].set_value('{"width_mm":46}').run()
    button(at, 'Load parameters').click().run()
    assert at.number_input[0].value == initial
    assert at.error
    assert any('No accepted model' in i.value for i in at.markdown)

    payload = json.loads(dragster_ui.EXAMPLE.read_text())
    payload['width_mm'] = 46.0
    at.text_area[0].set_value(json.dumps(payload)).run()
    button(at, 'Load parameters').click().run()
    assert at.number_input(key='dragster_param_width_mm').value == 46.0
    assert not at.error and not at.exception
