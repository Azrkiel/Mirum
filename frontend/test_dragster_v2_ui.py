"""Three-style draft editing and evidence display without provider transport."""
from concurrent.futures import Future
import json
from pathlib import Path
import sys
from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).parent))
import dragster_ui
from backend.dragsters.styles import REGISTRY, load_preset


def app():
    return AppTest.from_string('from dragster_ui import render_dragster\nrender_dragster("http://unused", lambda: {})').run()


def button(at, label):
    return next(b for b in at.button if b.label == label)


@pytest.mark.parametrize('style', ['teardrop', 'narrow_waist'])
def test_clean_style_switch_loads_preset(style):
    at = app()
    at.radio[0].set_value(style).run()
    assert not at.exception
    assert at.session_state.dragster_draft['style'] == style
    assert at.session_state.dragster_draft == load_preset(style).model_dump()


def test_edited_style_switch_needs_explicit_discard():
    at = app()
    next(n for n in at.number_input if n.label == 'width_mm').set_value(46.0).run()
    at.radio[0].set_value('teardrop').run()
    assert at.session_state.dragster_draft['style'] == 'wedge'
    assert at.session_state.dragster_draft['width_mm'] == 46.0
    button(at, 'Discard edits and load preset').click().run()
    assert at.session_state.dragster_draft == load_preset('teardrop').model_dump()
    assert not at.exception


def test_invalid_draft_disables_generate():
    at = app()
    next(n for n in at.number_input if n.label == 'pocket_center_z_mm').set_value(60.0).run()
    assert button(at, 'Generate accepted wedge').disabled
    assert any('pocket_center_z_mm' in e.value for e in at.error)
    assert at.session_state.dragster_state.accepted is None


def test_v1_load_upgrades_and_displays_default_provenance():
    at = app()
    at.text_area[0].set_value(dragster_ui.EXAMPLE.read_text()).run()
    button(at, 'Load wedge parameters').click().run()
    assert at.session_state.dragster_draft['schema_version'] == 2
    assert at.session_state.dragster_draft['wheels']['width_mm'] == 6.0
    assert any('wheels.width_mm' in item.value and '6 mm' in item.value for item in at.info)
    assert not at.exception


def test_v1_upgrade_failure_sends_original_json(monkeypatch):
    def unsupported(spec):
        raise ValueError('test v1 upgrade failure')
    monkeypatch.setattr(dragster_ui, 'upgrade_v1', unsupported)
    sent = []
    def start(state, payload, *args):
        sent.append(payload)
        return state.begin(), Future(), Event()
    monkeypatch.setattr(dragster_ui, 'start_attempt', start)
    at = app()
    raw = dragster_ui.EXAMPLE.read_text()
    at.text_area[0].set_value(raw).run()
    button(at, 'Load wedge parameters').click().run()
    assert any('this v1 file still generates as v1' in item.value for item in at.info)
    button(at, 'Generate as v1').click().run()
    assert sent == [raw]
    assert not at.exception


def test_optional_stock_material_groups():
    at = app()
    next(c for c in at.checkbox if c.label == 'Use stock block').check().run()
    next(c for c in at.checkbox if c.label == 'Supply material density').check().run()
    assert at.session_state.dragster_draft['stock'] is not None
    assert at.session_state.dragster_draft['material'] is not None
    next(c for c in at.checkbox if c.label == 'Use stock block').uncheck().run()
    assert at.session_state.dragster_draft['stock'] is None


def test_wheel_envelopes_add_four_labelled_traces(monkeypatch, tmp_path):
    from backend.dragsters import local
    spec = load_preset('wedge')
    result = local.generate_result_v2(spec, tmp_path)
    figures = []
    monkeypatch.setattr(dragster_ui.st, 'plotly_chart', lambda figure, **kwargs: figures.append(figure))
    dragster_ui.render_preview((result / 'preview.glb').read_bytes(), envelopes=dragster_ui.wheel_envelopes(spec))
    assert len(figures[0].data) == 5
    assert all('envelope — not in STEP' in trace.name for trace in figures[0].data[1:])


def test_v2_evidence_panel_and_failed_attempt_retains_acceptance(monkeypatch, tmp_path):
    import hashlib
    from backend.dragsters import local
    spec = load_preset('wedge')
    directory = local.generate_result_v2(spec, tmp_path)
    files = {fmt: (directory / name).read_bytes() for fmt, name in
             dict(spec='spec.json', report='report.json', step='body.step', glb='preview.glb').items()}
    report = json.loads(files['report']) | {'revision_id': 'a' * 32}
    files['report'] = json.dumps(report).encode()
    accepted = dict(status='done', revision_id='a' * 32, run_id='a' * 32,
                    spec=spec.model_dump(), report=report,
                    digests={fmt: hashlib.sha256(data).hexdigest() for fmt, data in files.items()})
    at = app()
    state = at.session_state.dragster_state
    state.accept(state.begin(), accepted, files)
    at.run()
    assert any('no density supplied' in m.value for m in at.markdown)
    assert at.dataframe
    assert any('Wheelbase' in m.label for m in at.metric)
    next(c for c in at.checkbox if c.label == 'Show wheel envelopes').check().run()
    assert not at.exception
    def failed(state, *args):
        future = Future()
        future.set_exception(ValueError('forced failure'))
        return state.begin(), future, Event()
    monkeypatch.setattr(dragster_ui, 'start_attempt', failed)
    button(at, 'Generate accepted wedge').click().run()
    assert at.session_state.dragster_state.files == files
    assert any('forced failure' in item.value for item in at.error)


def test_standalone_style_selector_loads_v2_without_generating(tmp_path, monkeypatch):
    monkeypatch.setenv('MIRUM_DRAGSTER_OUTPUT_ROOT', str(tmp_path))
    path = Path(__file__).parents[1] / 'backend/dragsters/app.py'
    at = AppTest.from_file(str(path)).run()
    at.radio[0].set_value('teardrop').run()
    assert not at.exception
    assert at.session_state.preview_style == 'teardrop'
    assert not list(tmp_path.iterdir())
