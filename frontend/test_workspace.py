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
    assert app.sidebar.radio[0].value == "cad"
    app.sidebar.radio[0].set_value("dragster").run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "dragster"
    app.button[0].click().run()
    assert app.sidebar.radio[0].value == "dragster"
    assert app.session_state["glb_bytes"] == b"accepted model"
    app.sidebar.radio[0].set_value("glider").run()
    app.sidebar.radio[0].set_value("cad").run()
    assert app.session_state["glb_bytes"] == b"accepted model"


def test_workspace_recovers_from_removed_workflow_selection():
    app = AppTest.from_string('''
from workspace import render_navigation
render_navigation()
''')
    app.session_state["workspace_workflow"] = "retired-workflow"
    app.run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "cad"


def test_cad_workspace_separates_results_and_blocks_blank_generation():
    app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=15).run()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Model", "Analysis", "Parts", "Code"]
    generate = next(button for button in app.button if button.key == "generate_model")
    assert generate.disabled
    app.text_area[0].set_value("A mounting plate, 100 mm long").run()
    assert not next(button for button in app.button if button.key == "generate_model").disabled
