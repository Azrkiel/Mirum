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
from workspace import empty_state, section_header


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
    if 'dragster_state' not in st.session_state:
        st.session_state.dragster_state = DragsterState()
        load_editor(EXAMPLE.read_text())
    state = st.session_state.dragster_state
    draft = dict(schema_version=1, style='wedge', units='mm')
    controls, output = st.columns([.4, .6], gap='large')
    with controls:
        # Load before the number widgets are instantiated: Streamlit forbids
        # mutating a widget's session value after it has rendered in this run.
        with st.expander('Import parameters'):
            upload = st.file_uploader('Parameters JSON', type=['json'])
            pasted = st.text_area('Paste parameters JSON')
            if st.button('Load parameters'):
                try:
                    load_editor(upload.getvalue().decode('utf-8') if upload is not None else pasted)
                    state.error = None
                    st.rerun()
                except (ValueError, UnicodeError) as exc:
                    state.error = str(exc)
        with st.container(border=True):
            section_header('Dimensions', 'Millimetres. Generate to apply draft edits.')
            groups = {
                'Body': [('length_mm', 'Length'), ('width_mm', 'Width'),
                         ('height_mm', 'Rear height'), ('nose_height_mm', 'Nose height'),
                         ('ground_clearance_mm', 'Ground clearance'), ('min_wall_mm', 'Minimum wall')],
                'Axles': [('front_axle_x_mm', 'Front position'), ('rear_axle_x_mm', 'Rear position'),
                          ('axle_z_mm', 'Axle height'), ('axle_bore_diameter_mm', 'Bore diameter')],
                'Cartridge pocket': [('pocket_diameter_mm', 'Diameter'), ('pocket_depth_mm', 'Depth'),
                                     ('pocket_center_z_mm', 'Centre height')],
            }
            for group, fields in groups.items():
                with st.expander(group, expanded=group == 'Body'):
                    if group == 'Axles':
                        st.caption('Positions from the nose; height above ground.')
                    elif group == 'Cartridge pocket':
                        st.caption('Centre height above ground. External holder cavity only.')
                    columns = st.columns(2)
                    for index, (name, label) in enumerate(fields):
                        with columns[index % 2]:
                            draft[name] = st.number_input(label + ' (mm)', key='dragster_param_' + name,
                                                          step=.5, format='%.3f')
            payload = json.dumps(draft, sort_keys=True, separators=(',', ':'), allow_nan=False)
            try:
                spec = DragsterSpec.model_validate_json(payload)
            except ValueError as exc:
                spec = None
                st.warning(f'Invalid dimensions: {exc}')
            job = st.session_state.get('dragster_job')
            if st.button('Generate body', type='primary', use_container_width=True,
                         disabled=job is not None) and job is None:
                try:
                    DragsterSpec.model_validate_json(payload)
                    st.session_state.dragster_job = start_attempt(state, payload, backend, headers())
                    st.rerun()
                except ValueError as exc:
                    state.fail(state.begin(), str(exc))
            poll_attempt(state)
            if state.error:
                st.error(state.error)
            if spec is not None:
                st.download_button('Save draft JSON', canonical_spec(spec), 'edited-spec.json', 'application/json')
        st.caption('Demonstration geometry. No manufacturing or cartridge-fit certification.')
    with output:
        with st.container(border=True):
            section_header('Design output')
            model, evidence = st.tabs(['Model', 'Verification'])
            with model:
                if state.accepted is None:
                    empty_state('No accepted model', 'Set the dimensions and select Generate body.')
                else:
                    accepted = state.accepted
                    st.info('Draft matches the displayed model.' if draft == accepted['spec'] else
                            'Draft edits differ from the displayed model. Generate to update it.')
                    render_preview(state.files['glb'])
                    columns = st.columns(4)
                    for column, fmt, label, name, mime in zip(columns,
                            ['step', 'glb', 'spec', 'report'],
                            ['STEP', 'GLB', 'Parameters', 'Report'],
                            ['body.step', 'preview.glb', 'spec.json', 'report.json'],
                            ['application/step', 'model/gltf-binary', 'application/json', 'application/json']):
                        with column:
                            st.download_button(label, state.files[fmt], name, mime, use_container_width=True)
                    st.caption('Accepted revision: ' + accepted['revision_id'])
                    st.caption('Downloads are retained in this session. Save parameters before reloading; server copies expire.')
            with evidence:
                if state.accepted is None:
                    st.caption('STEP checks appear after a successful generation.')
                else:
                    st.success('Required STEP checks passed. Preview and downloads match this revision.')
                    with st.expander('Accepted parameters'):
                        st.json(state.accepted['spec'])
                    with st.expander('Verification report', expanded=True):
                        st.json(state.accepted['report'])


@st.fragment(run_every=.5)
def poll_attempt(state):
    job = st.session_state.get('dragster_job')
    if job is not None:
        attempt, future, cancelled = job
        if st.button('Cancel generation'):
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
        st.info('Generating geometry and checking STEP…')
