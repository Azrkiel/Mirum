"""Exercise workflow navigation as a real Streamlit session."""
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).parent))


def test_workflow_selection_survives_rerun_without_erasing_accepted_model():
    app = AppTest.from_string('''
import streamlit as st
from workspace import apply_workspace, render_navigation
apply_workspace()
st.session_state.setdefault("glb_bytes", b"accepted model")
workflow = render_navigation()
st.write(workflow)
st.button("Rerun controls")
''').run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "Text to CAD"
    app.sidebar.radio[0].set_value("CO2 dragster").run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "CO2 dragster"
    app.button[0].click().run()
    assert app.sidebar.radio[0].value == "CO2 dragster"
    assert app.session_state["glb_bytes"] == b"accepted model"
    app.sidebar.radio[0].set_value("Passive glider distance search").run()
    app.sidebar.radio[0].set_value("Text to CAD").run()
    assert app.session_state["glb_bytes"] == b"accepted model"


def test_workspace_recovers_from_removed_workflow_selection():
    app = AppTest.from_string('''
from workspace import render_navigation
render_navigation()
''')
    app.session_state["workflow"] = "retired-workflow"
    app.run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "Text to CAD"


def test_cad_workspace_separates_results_and_blocks_blank_generation():
    app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=15).run()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Model", "Analysis", "Parts", "Code"]
    generate = next(button for button in app.button if button.key == "generate_model")
    assert generate.disabled
    app.text_area[0].set_value("A mounting plate, 100 mm long").run()
    assert not next(button for button in app.button if button.key == "generate_model").disabled


def test_all_six_workflows_are_offered_and_route_target_is_honoured():
    from workspace import WORKFLOWS
    assert list(WORKFLOWS) == ['Text to CAD', 'CO2 dragster', 'Passive glider distance search',
                               'Physics-based glider CEM', 'Deterministic rocket nozzle', 'Checked beam']
    app = AppTest.from_string('from workspace import render_navigation\nrender_navigation()')
    app.session_state["workflow"] = "Deterministic rocket nozzle"  # what a route event sets
    app.run()
    assert not app.exception
    assert app.sidebar.radio(key="workflow").value == "Deterministic rocket nozzle"


def test_checked_workflow_query_param_selects_checked_beam_initially():
    app = AppTest.from_string('from workspace import render_navigation\nrender_navigation()')
    app.query_params["checked_workflow"] = "1"
    app.run()
    assert app.sidebar.radio(key="workflow").value == "Checked beam"


def _app():
    return AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=15)


def test_route_notice_and_tab_switch_render_under_workspace_layout():
    app = _app()
    app.session_state["pending_workflow"] = "Deterministic rocket nozzle"
    app.session_state["route_notice"] = "Checked design: Deterministic rocket nozzle"
    app.run()
    assert not app.exception
    assert app.sidebar.radio(key="workflow").value == "Deterministic rocket nozzle"
    assert any("Checked design: Deterministic rocket nozzle" in i.value for i in app.info)
    app.run()  # the notice is one-shot
    assert not any("Checked design:" in i.value for i in app.info)


def test_assurance_label_renders_beside_the_model_under_workspace_layout():
    for value, label in [("checked_design", "Checked design"), ("unverified_geometry", "Unverified geometry")]:
        app = _app()
        app.session_state["glb_bytes"] = b"glTF fixture"
        app.session_state["assurance"] = value
        app.run()
        assert not app.exception
        assert any(f"**{label}**" == m.value for m in app.markdown), value
    app = _app()
    app.session_state["glb_bytes"] = b"glTF fixture"
    app.run()
    assert not app.exception
    assert not any(m.value in ("**Checked design**", "**Unverified geometry**") for m in app.markdown)
    assert any("Unverified preview" in c.value for c in app.caption)
