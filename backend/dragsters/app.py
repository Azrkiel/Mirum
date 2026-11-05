"""Run from repository root: python -m streamlit run backend/dragsters/app.py."""
from pathlib import Path
import json
import os

import streamlit as st

from backend.dragsters.local import PreviewState, preview_mesh
from backend.dragsters.spec import DragsterSpec, canonical_spec


EXAMPLE = Path(__file__).with_name('examples') / 'wedge.json'


def load_editor(payload: str) -> None:
    # Validate the complete strict contract before changing any widget values.
    spec = DragsterSpec.model_validate_json(payload)
    for name, value in spec.model_dump().items():
        st.session_state[f'param_{name}'] = value


def main() -> None:
    st.set_page_config(page_title='Local wedge prototype', layout='wide')
    st.title('Deterministic wedge — local preview')
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

    st.subheader('Edited parameters')
    st.caption('Edits are a draft. The preview and accepted downloads below belong to the last successful result. Generate to apply edits.')
    st.caption('schema_version=1 · style=wedge · units=mm. Heights are above the bottom; axle/pocket Z is above ground.')
    draft = dict(schema_version=1, style='wedge', units='mm')
    # Widgets outside a batching form let draft/accepted status update on every edit.
    # The explicit Generate button is the sole submission/build action.
    columns = st.columns(3)
    for index, name in enumerate(DragsterSpec.model_fields):
        if name in draft:
            continue
        with columns[index % 3]:
            draft[name] = st.number_input(name, key=f'param_{name}', step=0.5, format='%.3f')
    payload = json.dumps(draft, sort_keys=True, separators=(',', ':'), allow_nan=False)
    try:
        valid_draft = DragsterSpec.model_validate_json(payload)
        st.download_button('Save edited parameters JSON', canonical_spec(valid_draft), 'edited-spec.json', 'application/json')
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
    import plotly.graph_objects as go
    mesh = preview_mesh((accepted / 'preview.glb').read_bytes())
    # The saved GLB is metres/Y-up; show its decoded mesh in CAD mm/Z-up.
    vertices = mesh.vertices * 1000
    x, y, z = vertices[:, 0], -vertices[:, 2], vertices[:, 1]
    i, j, k = mesh.faces.T
    figure = go.Figure(go.Mesh3d(x=x, y=y, z=z, i=i, j=j, k=k,
                               color='lightsteelblue', flatshading=True))
    span = max(mesh.extents)
    figure.update_layout(
        scene=dict(aspectmode='manual',
                   aspectratio=dict(x=mesh.extents[0] / span,
                                    y=mesh.extents[2] / span,
                                    z=mesh.extents[1] / span),
                   camera=dict(eye=dict(x=1.1, y=1.1, z=0.8)),
                   xaxis_title='X (mm)', yaxis_title='Y (mm)', zaxis_title='Z (mm)'),
        margin=dict(l=0, r=0, t=0, b=0), height=500)
    st.plotly_chart(figure, use_container_width=True)
    for label, filename, mime in [('STEP', 'body.step', 'application/step'),
                                  ('accepted parameters', 'spec.json', 'application/json'),
                                  ('report', 'report.json', 'application/json'),
                                  ('GLB', 'preview.glb', 'model/gltf-binary')]:
        st.download_button(f'Download {label}', (accepted / filename).read_bytes(), filename, mime)


if __name__ == '__main__':
    main()
