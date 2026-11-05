"""Provider-free wedge form in the main application."""
import json
from concurrent.futures import Future
from threading import Event, Thread
from pathlib import Path

import requests
import streamlit as st

from backend.dragsters.spec import DragsterSpec, canonical_spec
from backend.dragsters.local import render_preview
from dragster_state import DragsterState


_ROOT = Path(__file__).resolve().parent
EXAMPLE = (_ROOT / 'backend/dragsters/examples/wedge.json' if (_ROOT / 'backend').is_dir()
           else _ROOT.parent / 'backend/dragsters/examples/wedge.json')


def load_editor(payload):
    spec = DragsterSpec.model_validate_json(payload)
    for key, value in spec.model_dump().items():
        st.session_state['dragster_param_' + key] = value


def fetch_result(payload, backend, auth, cancelled):
    """Runs off the Streamlit thread; closing SSE cancels its owned worker."""
    spec = DragsterSpec.model_validate_json(payload)
    complete = None
    with requests.post(backend + '/dragsters/generate', data=canonical_spec(spec),
                       headers={**auth, 'Content-Type': 'application/json'},
                       stream=True, timeout=(10, 40)) as response:
        response.raise_for_status()
        for line in response.iter_lines(chunk_size=1):
            if cancelled.is_set():
                raise ValueError('Attempt cancelled; previous accepted result retained')
            if line.startswith(b'data: '):
                event = json.loads(line[6:])
                if event.get('step') == 'complete':
                    complete = event
    if not complete or complete.get('status') != 'done':
        raise ValueError((complete or {}).get('message', 'Generation interrupted; previous result retained'))
    files = {}
    for fmt in ('spec', 'report', 'step', 'glb'):
        if cancelled.is_set():
            raise ValueError('Attempt cancelled; previous accepted result retained')
        response = requests.get(f"{backend}/download/{complete['run_id']}/{fmt}", headers=auth, timeout=15)
        if response.status_code == 404:
            raise ValueError('Accepted artifacts expired before download. Regenerate from the saved parameters.')
        response.raise_for_status()
        files[fmt] = response.content
    return complete, files


def start_attempt(state, payload, backend, auth):
    attempt, future, cancelled = state.begin(), Future(), Event()
    def work():
        try:
            future.set_result(fetch_result(payload, backend, auth, cancelled))
        except Exception as exc:
            future.set_exception(exc)
    Thread(target=work, daemon=True, name='dragster-download').start()
    return attempt, future, cancelled


def render_dragster(backend, headers):
    st.title('CO2 dragster — deterministic wedge')
    st.caption('Version 1 demonstration geometry in mm. No provider key required. No manufacturing or cartridge-fit certification.')
    if 'dragster_state' not in st.session_state:
        st.session_state.dragster_state = DragsterState()
        load_editor(EXAMPLE.read_text())
    state = st.session_state.dragster_state
    with st.expander('Load strict versioned JSON'):
        upload = st.file_uploader('Wedge parameters JSON', type=['json'])
        pasted = st.text_area('Or paste complete wedge parameters JSON')
        if st.button('Load wedge parameters'):
            try:
                load_editor(upload.getvalue().decode('utf-8') if upload is not None else pasted)
                state.error = None
            except (ValueError, UnicodeError) as exc:
                state.error = str(exc)
    st.subheader('Edited parameters')
    st.caption('Edits are a draft. Generate to replace the accepted revision. Heights are above the bottom; axle/pocket Z is above ground.')
    draft = dict(schema_version=1, style='wedge', units='mm')
    columns = st.columns(3)
    for index, name in enumerate(DragsterSpec.model_fields):
        if name not in draft:
            with columns[index % 3]:
                draft[name] = st.number_input(name, key='dragster_param_' + name, step=.5, format='%.3f')
    payload = json.dumps(draft, sort_keys=True, separators=(',', ':'), allow_nan=False)
    try:
        spec = DragsterSpec.model_validate_json(payload)
        st.download_button('Save edited parameters JSON', canonical_spec(spec), 'edited-spec.json', 'application/json')
    except ValueError as exc:
        st.warning(f'Edited parameters are invalid: {exc}')
    job = st.session_state.get('dragster_job')
    if st.button('Generate accepted wedge', type='primary', disabled=job is not None) and job is None:
        try:
            DragsterSpec.model_validate_json(payload)
            st.session_state.dragster_job = start_attempt(state, payload, backend, headers())
            st.rerun()
        except ValueError as exc:
            state.fail(state.begin(), str(exc))
    poll_attempt(state)
    if state.error:
        st.error(state.error)
    if state.accepted is None:
        st.info('No accepted result yet. Correct the parameters and generate again.')
        return
    accepted = state.accepted
    st.subheader('Last accepted result')
    st.info('Edited parameters match displayed result.' if draft == accepted['spec'] else
            'Edited parameters differ from displayed result. Preview and downloads retain the accepted revision.')
    st.caption('Accepted revision: ' + accepted['revision_id'])
    with st.expander('Parameters belonging to displayed result'):
        st.json(accepted['spec'])
    render_preview(state.files['glb'])
    st.success('Required STEP checks passed; STEP-derived preview and all four artifacts verified.')
    with st.expander('Accepted verification report'):
        st.json(accepted['report'])
    # Downloads use the exact bytes fetched and verified together. The server
    # buffer may expire; this session retains its accepted snapshot.
    st.caption('Downloads below are verified copies retained in this session. Server copies expire (default 6 hours, or earlier under storage caps). Reloading loses this session; save parameters to regenerate.')
    for fmt, label, name, mime in [('step', 'STEP', 'body.step', 'application/step'),
                                  ('glb', 'GLB', 'preview.glb', 'model/gltf-binary'),
                                  ('spec', 'accepted parameters', 'spec.json', 'application/json'),
                                  ('report', 'report', 'report.json', 'application/json')]:
        st.download_button('Download ' + label, state.files[fmt], name, mime)


@st.fragment(run_every=.5)
def poll_attempt(state):
    job = st.session_state.get('dragster_job')
    if job is not None:
        attempt, future, cancelled = job
        if st.button('Cancel wedge attempt'):
            cancelled.set()
            state.fail(attempt, 'Attempt cancelled; previous accepted result retained')
            # Invalidate the attempt token so even an already completed response
            # cannot replace the previous revision after the user cancels.
            state.attempt = ''
            st.session_state.dragster_job = None
            st.rerun()
        if future.done():
            try:
                event, files = future.result()
                state.accept(attempt, event, files)
            except Exception as exc:
                state.fail(attempt, str(exc))
            st.session_state.dragster_job = None
            st.rerun()
        st.info('Building, verifying STEP, generating preview, and publishing artifacts…')
