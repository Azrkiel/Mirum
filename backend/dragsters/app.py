"""Run from repository root: python -m streamlit run backend/dragsters/app.py."""
from pathlib import Path
import json
import os

import streamlit as st

from backend.dragsters.local import PreviewState, render_preview
from backend.dragsters.spec import DragsterSpec, canonical_spec
from backend.dragsters.spec_v2 import parse_any, canonical_spec_v2
from backend.dragsters.styles import REGISTRY, load_preset


EXAMPLE = Path(__file__).with_name('examples') / 'wedge.json'


def load_editor(payload: str, *, set_choice=True) -> None:
    # Validate the complete strict contract before changing any widget values.
    spec = parse_any(payload)
    st.session_state.preview_draft = spec.model_dump(mode='json')
    st.session_state.preview_style = spec.style
    if set_choice:
        st.session_state.preview_choice = spec.style
    for name, value in spec.model_dump().items():
        if isinstance(value, dict):
            for child, number in value.items():
                st.session_state[f'param_{name}.{child}'] = number
        else:
            st.session_state[f'param_{name}'] = value


def main() -> None:
    st.set_page_config(page_title='Local wedge prototype', layout='wide')
    st.title('Deterministic dragster — local preview')
    st.caption('Demonstration geometry in mm. No manufacturing or cartridge-fit certification.')
    if 'preview_state' not in st.session_state:
        st.session_state.preview_state = PreviewState()
        load_editor(EXAMPLE.read_text(encoding='utf-8'))
    state = st.session_state.preview_state
    root = Path(os.environ.get('MIRUM_DRAGSTER_OUTPUT_ROOT', 'output/dragster-previews'))

    with st.expander('Load strict versioned JSON'):
        upload = st.file_uploader('Parameters JSON', type=['json'])
        pasted = st.text_area('Or paste complete parameters JSON')
        if st.button('Load parameters'):
            try:
                if upload is None and not pasted:
                    raise ValueError('Choose a JSON file or paste complete JSON first')
                load_editor(upload.getvalue().decode('utf-8') if upload is not None else pasted)
                state.error = None
            except Exception as exc:
                state.error = str(exc)

    selected = st.radio('Body style', list(REGISTRY), key='preview_choice',
                        format_func=lambda style: REGISTRY[style].label, horizontal=True)
    if selected != st.session_state.preview_style:
        load_editor(canonical_spec_v2(load_preset(selected)), set_choice=False)
    st.subheader('Edited parameters')
    st.caption('Edits are a draft. The preview and accepted downloads below belong to the last successful result. Generate to apply edits.')
    st.caption('Shape heights are above the bottom; pocket Z is above ground. v2 axle Z is half the wheel diameter.')
    import copy
    draft = copy.deepcopy(st.session_state.preview_draft)
    # Widgets outside a batching form let draft/accepted status update on every edit.
    # The explicit Generate button is the sole submission/build action.
    columns = st.columns(3)
    for index, (name, value) in enumerate(draft.items()):
        if name in ('schema_version', 'style', 'units') or value is None:
            continue
        with columns[index % 3]:
            if isinstance(value, dict):
                for child, number in value.items():
                    key = f'{name}.{child}'
                    value[child] = (st.text_input(key, key=f'param_{key}') if isinstance(number, str) else
                                    st.number_input(key, key=f'param_{key}', step=0.5, format='%.3f'))
            else:
                draft[name] = st.number_input(name, key=f'param_{name}', step=0.5, format='%.3f')
    payload = json.dumps(draft, sort_keys=True, separators=(',', ':'), allow_nan=False)
    try:
        valid_draft = parse_any(payload)
        canonical = canonical_spec if valid_draft.schema_version == 1 else canonical_spec_v2
        st.download_button('Save edited parameters JSON', canonical(valid_draft), 'edited-spec.json', 'application/json')
    except ValueError as exc:
        st.warning(f'Edited parameters are invalid: {exc}')
    if st.button('Generate accepted preview', type='primary'):
        with st.spinner('Building, verifying STEP, and generating preview…'):
            state.attempt(payload, root)
    if state.error:
        st.error(state.error)
    if state.accepted is None:
        st.info('No accepted result yet.')
        return

    accepted = state.accepted
    accepted_json = (accepted / 'spec.json').read_text(encoding='utf-8')
    changed = payload != accepted_json
    st.subheader('Last accepted result')
    st.info('Edited parameters differ from displayed result.' if changed else 'Edited parameters match displayed result.')
    st.code(str(accepted))
    with st.expander('Parameters belonging to displayed result'):
        st.json(json.loads(accepted_json))
    render_preview((accepted / 'preview.glb').read_bytes())
    for label, filename, mime in [('STEP', 'body.step', 'application/step'),
                                  ('accepted parameters', 'spec.json', 'application/json'),
                                  ('report', 'report.json', 'application/json'),
                                  ('GLB', 'preview.glb', 'model/gltf-binary')]:
        st.download_button(f'Download {label}', (accepted / filename).read_bytes(), filename, mime)


if __name__ == '__main__':
    main()
