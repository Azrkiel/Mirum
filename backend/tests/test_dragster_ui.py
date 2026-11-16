"""Streamlit AppTest exercises real native builds through the UI buttons."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / 'dragsters/app.py'


def button(app, label):
    return next(b for b in app.button if b.label == label)


def test_ui_success_invalid_edit_preserves_preview(tmp_path, monkeypatch):
    monkeypatch.setenv('MIRUM_DRAGSTER_OUTPUT_ROOT', str(tmp_path))
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.exception
    assert not list(tmp_path.iterdir())
    button(app, 'Generate accepted preview').click().run()
    assert not app.exception and not app.error
    accepted = app.session_state.preview_state.accepted
    before = {p.name: p.read_bytes() for p in accepted.iterdir()}
    assert len(app.get('plotly_chart')) == 1
    app.number_input(key='param_pocket_center_z_mm').set_value(40.0).run()
    assert any('differ' in item.value for item in app.info)
    button(app, 'Generate accepted preview').click().run()
    assert not app.exception
    assert any('pocket_center_z_mm' in item.value for item in app.error)
    assert app.session_state.preview_state.accepted == accepted
    assert {p.name: p.read_bytes() for p in accepted.iterdir()} == before
    assert list(tmp_path.iterdir()) == [accepted]
    assert len(app.get('plotly_chart')) == 1
    assert len(app.get('download_button')) == 4
    app.number_input(key='param_pocket_center_z_mm').set_value(23.0).run()
    button(app, 'Generate accepted preview').click().run()
    assert not app.exception and not app.error
    assert app.session_state.preview_state.accepted != accepted


def test_ui_load_strict_json_is_atomic(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('MIRUM_DRAGSTER_OUTPUT_ROOT', str(tmp_path))
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    payload = json.loads((APP.parent / 'examples/wedge.json').read_text()) | {'width_mm': 46.0}
    app.text_area[0].set_value(json.dumps(payload)).run()
    button(app, 'Load parameters').click().run()
    assert not app.exception and not app.error
    assert app.number_input(key='param_width_mm').value == 46.0
    button(app, 'Generate accepted preview').click().run()
    accepted = app.session_state.preview_state.accepted
    for bad in ['{', json.dumps(payload | {'schema_version':True}),
                json.dumps(payload | {'unexpected': 1}),
                json.dumps(payload | {'pocket_center_z_mm': 40.0})]:
        app.text_area[0].set_value(bad).run()
        button(app, 'Load parameters').click().run()
        assert not app.exception and app.error
        assert app.number_input(key='param_width_mm').value == 46.0
        assert app.number_input(key='param_pocket_center_z_mm').value == 23.0
        assert app.session_state.preview_state.accepted == accepted
        assert len(app.get('plotly_chart')) == 1
