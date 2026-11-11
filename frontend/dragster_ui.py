"""Provider-free, revision-aware editor for all deterministic dragster styles."""
import copy
import json
from concurrent.futures import Future
from threading import Event, Thread
from pathlib import Path

import requests
import streamlit as st

from backend.dragsters.spec import canonical_spec
from backend.dragsters.spec_v2 import parse_any, canonical_spec_v2, upgrade_v1
from backend.dragsters.styles import REGISTRY, load_preset
from backend.dragsters.hardware import wheel_envelopes
from backend.dragsters.local import render_preview
from dragster_state import DragsterState
from dragster_fields import GROUPS, field_info

_ROOT = Path(__file__).resolve().parent
EXAMPLE = (_ROOT / 'backend/dragsters/examples/wedge.json' if (_ROOT / 'backend').is_dir()
           else _ROOT.parent / 'backend/dragsters/examples/wedge.json')


def canonical(spec):
    return (canonical_spec if spec.schema_version == 1 else canonical_spec_v2)(spec)


def leaves(data, prefix=''):
    for key, value in data.items():
        path = prefix + key
        if isinstance(value, dict):
            yield from leaves(value, path + '.')
        elif value is not None and key not in ('schema_version', 'style', 'units'):
            yield path, value


def load_editor(payload):
    """Validate/upgrade atomically before changing draft or widget state."""
    spec = parse_any(payload)
    provenance = None
    if spec.schema_version == 1:
        try:
            spec, provenance = upgrade_v1(spec)
        except ValueError:
            st.session_state.dragster_legacy_payload = payload
            st.session_state.dragster_import_info = 'This v1 file cannot be upgraded; this v1 file still generates as v1.'
            return
    draft = spec.model_dump(mode='json')
    st.session_state.dragster_draft = draft
    st.session_state.dragster_loaded = json.dumps(draft, sort_keys=True)
    st.session_state.dragster_style = spec.style
    st.session_state.dragster_stock = draft['stock'] is not None
    st.session_state.dragster_material = draft['material'] is not None
    for path, value in leaves(draft):
        st.session_state['dragster_param_' + path] = value
    st.session_state.dragster_legacy_payload = None
    st.session_state.dragster_import_info = (
        'Upgraded v1 parameters. wheels.width_mm: preset default 6 mm; '
        'wheels.side_clearance_mm: preset default 1.5 mm. Provenance: ' + json.dumps(provenance, sort_keys=True)
        if provenance else None)


def fetch_result(payload, backend, auth, cancelled):
    """Runs off the Streamlit thread; legacy fallback sends the original JSON."""
    parse_any(payload)
    complete = None
    with requests.post(backend + '/dragsters/generate', data=payload,
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


def render_editor():
    draft = copy.deepcopy(st.session_state.dragster_draft)
    for group in GROUPS:
        with st.expander(group, expanded=group in GROUPS[:3]):
            if group == 'Stock block':
                enabled = st.checkbox('Use stock block', key='dragster_stock')
                draft['stock'] = (draft['stock'] or dict(length_mm=320.0, width_mm=80.0, height_mm=80.0)) if enabled else None
            if group == 'Material':
                enabled = st.checkbox('Supply material density', key='dragster_material')
                draft['material'] = (draft['material'] or dict(name='wood', density_g_cm3=0.2)) if enabled else None
            columns = st.columns(3)
            fields = [(path, value) for path, value in leaves(draft) if field_info(path).group == group]
            for index, (path, value) in enumerate(fields):
                info = field_info(path)
                key = 'dragster_param_' + path
                if key not in st.session_state:
                    st.session_state[key] = value
                with columns[index % 3]:
                    if isinstance(value, str):
                        value = st.text_input(info.label, key=key, help=info.help)
                    else:
                        value = st.number_input(info.label, key=key, help=info.help,
                            step=0.01 if info.unit != 'mm' else 0.5, format='%.3f')
                target = draft
                parts = path.split('.')
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
    st.session_state.dragster_draft = draft
    return draft


def render_evidence(report):
    checks = report.get('checks', {})
    st.dataframe([{'id': name, 'pass/fail': 'pass' if check['passed'] else 'fail',
                   'measured': json.dumps({k: v for k, v in check.items() if k not in ('passed', 'threshold')}, sort_keys=True),
                   'threshold': json.dumps(check.get('threshold', 'see measured evidence'))}
                  for name, check in checks.items()], use_container_width=True, hide_index=True)
    hardware = report.get('hardware')
    if hardware:
        columns = st.columns(3)
        for col, label, key in zip(columns, ('Wheelbase (mm)', 'Front track (mm)', 'Rear track (mm)'),
                                  ('wheelbase_mm', 'track_width_front_mm', 'track_width_rear_mm')):
            col.metric(label, f"{hardware[key]:.2f}")
        st.write('Stock margins (mm):', hardware['stock_margin_mm'] if hardware['stock_margin_mm'] is not None else 'No stock block supplied')
    mass = report.get('mass')
    if mass:
        st.markdown('Body mass: — (no density supplied)' if mass['value_g'] is None else
                    f"Body mass: {mass['value_g']:.2f} g (body only)")


def render_dragster(backend, headers):
    st.title('CO2 dragster — deterministic bodies')
    st.caption('Demonstration geometry in mm. No provider key required. No manufacturing, cartridge-fit, competition or performance certification.')
    if 'dragster_state' not in st.session_state:
        st.session_state.dragster_state = DragsterState()
        load_editor(canonical_spec_v2(load_preset('wedge')))
    pending = st.session_state.pop('dragster_pending_preset', None)
    if pending:
        load_editor(pending)
    state = st.session_state.dragster_state
    with st.expander('Load strict versioned JSON'):
        upload = st.file_uploader('Dragster parameters JSON', type=['json'])
        pasted = st.text_area('Or paste complete wedge parameters JSON')
        if st.button('Load wedge parameters'):
            try:
                load_editor(upload.getvalue().decode('utf-8') if upload is not None else pasted)
                state.error = None
            except (ValueError, UnicodeError) as exc:
                state.error = str(exc)
    selected = st.radio('Body style', list(REGISTRY), format_func=lambda name: REGISTRY[name].label,
                        key='dragster_style', horizontal=True)
    current = st.session_state.dragster_draft
    if selected != current['style']:
        dirty = json.dumps(current, sort_keys=True) != st.session_state.dragster_loaded
        if not dirty or st.button('Discard edits and load preset'):
            st.session_state.dragster_pending_preset = canonical_spec_v2(load_preset(selected))
            st.rerun()
        st.warning('The draft has unsaved edits. Discard them explicitly to switch styles.')
    info = st.session_state.get('dragster_import_info')
    if info:
        st.info(info)
    st.subheader('Edited parameters')
    st.caption('Edits are a draft. X: nose to rear; Y: transverse; Z: up. Shape heights are above body bottom. Axle Z = wheel diameter/2; ground (wheel contact) = Z=0. Pocket Z is above ground.')
    draft = render_editor()
    payload = json.dumps(draft, sort_keys=True, separators=(',', ':'), allow_nan=False)
    valid = True
    try:
        spec = parse_any(payload)
        st.download_button('Save edited parameters JSON', canonical(spec), 'edited-spec.json', 'application/json')
    except ValueError as exc:
        valid = False
        errors = exc.errors() if hasattr(exc, 'errors') else None
        message = (f"{'.'.join(map(str, errors[0]['loc'])) or 'spec'}: {errors[0]['msg']}" if errors else str(exc))
        st.error('Edited parameters are invalid: ' + message)
    job = st.session_state.get('dragster_job')
    if st.button('Generate accepted ' + draft['style'].replace('_', ' '), type='primary', disabled=job is not None or not valid) and job is None and valid:
        st.session_state.dragster_job = start_attempt(state, payload, backend, headers())
        st.rerun()
    legacy = st.session_state.get('dragster_legacy_payload')
    if legacy and st.button('Generate as v1', disabled=job is not None):
        st.session_state.dragster_job = start_attempt(state, legacy, backend, headers())
        st.rerun()
    poll_attempt(state)
    if state.error:
        st.error(state.error)
    if state.accepted is None:
        st.info('No accepted result yet. Correct the parameters and generate again.')
        return
    accepted = state.accepted
    st.subheader('Last accepted result')
    matches = valid and canonical(parse_any(payload)) == canonical(parse_any(json.dumps(accepted['spec'])))
    st.info('Edited parameters match displayed result.' if matches else
            'Edited parameters differ from displayed result. Preview and downloads retain the accepted revision.')
    st.caption('Accepted revision: ' + accepted['revision_id'])
    with st.expander('Parameters belonging to displayed result'):
        st.json(accepted['spec'])
    envelopes = None
    if accepted['spec']['schema_version'] == 2 and st.checkbox('Show wheel envelopes'):
        envelopes = wheel_envelopes(parse_any(json.dumps(accepted['spec'])))
        st.caption('Wheel envelope — not in STEP. Display/check envelopes only.')
    render_preview(state.files['glb'], envelopes=envelopes)
    st.success('Required STEP checks passed; STEP-derived preview and all four artifacts verified.')
    render_evidence(accepted['report'])
    with st.expander('Accepted verification report'):
        st.json(accepted['report'])
    st.caption('Downloads are verified copies retained in this session. Server copies expire (default 6 hours, or earlier under storage caps). Save parameters to regenerate after reloading.')
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
