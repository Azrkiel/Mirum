"""Server-side HTTP bridge: browser messages contain physical inputs, never tokens."""
import base64
import uuid
import requests
import streamlit as st
from components.glider_explorer import explorer


def render_explorer(backend, headers):
    if 'explorer_bootstrap' not in st.session_state:
        try:
            r = requests.get(f'{backend}/gliders/metadata', headers=headers(), timeout=15)
            r.raise_for_status()
            st.session_state.explorer_bootstrap = r.json()
        except (requests.RequestException, ValueError) as exc:
            st.error(f'Could not load explorer controls: {exc}')
            return
    bootstrap = st.session_state.explorer_bootstrap
    if 'explorer_epoch' not in st.session_state:
        st.session_state.explorer_epoch = uuid.uuid4().hex
    event = explorer(design=bootstrap['preset'], metadata=bootstrap,
                     epoch=st.session_state.explorer_epoch,
                     reply=st.session_state.get('explorer_reply'), key='glider-explorer-viewer', default=None)
    if not event or event == st.session_state.get('explorer_last_event'):
        return
    st.session_state.explorer_last_event = event
    if event.get('epoch') != st.session_state.explorer_epoch:
        return
    reply = {'sequence': event['sequence'], 'epoch': event['epoch'], 'nonce': event['nonce']}
    try:
        action = event['action']
        if action in ('preview', 'evaluate'):
            endpoint = 'preview' if action == 'preview' else 'evaluations'
            r = requests.post(f'{backend}/gliders/{endpoint}', json=event['design'], headers=headers(), timeout=15)
            if r.status_code == 429:
                raise ValueError('Solver busy. Your edited geometry remains usable; retry recalculation shortly.')
            r.raise_for_status()
            data = r.json()
            if action == 'preview':
                reply['preview'] = data
            else:
                st.session_state.explorer_evaluation = dict(id=data['evaluation_id'],
                    sequence=event['sequence'], revision=data['input_revision'])
                reply['evaluation_id'] = data['evaluation_id']
        elif action in ('poll', 'export'):
            evaluation = st.session_state.get('explorer_evaluation')
            if not evaluation or evaluation['sequence'] != event['sequence']:
                return
            url = f"{backend}/gliders/evaluations/{evaluation['id']}"
            r = requests.get(url + ('/export' if action == 'export' else ''), headers=headers(), timeout=30)
            r.raise_for_status()
            if action == 'export':
                reply['download'] = base64.b64encode(r.content).decode()
            else:
                data = r.json()
                if data['input_revision'] != evaluation['revision']:
                    raise ValueError('Evaluation revision mismatch; recalculate this design.')
                if data['status'] == 'completed':
                    reply['result'] = data['result']
                elif data['status'] == 'failed':
                    raise ValueError(data['detail'])
                else:
                    reply['evaluation_id'] = evaluation['id']
    except (requests.RequestException, ValueError, KeyError) as exc:
        reply['error'] = f'Explorer request failed: {exc}'
    st.session_state.explorer_reply = reply
    st.rerun()
