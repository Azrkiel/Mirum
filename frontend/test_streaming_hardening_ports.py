"""Exercise the real UI streaming function without starting the Streamlit page.

Ported behaviors from Text-2-CAD: retain accepted results on errors and distinguish
terminal completion from a broken transport. HTTP responses below are fixtures.
"""
import ast
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

APP = Path(__file__).with_name('app.py')


class State(dict):
    def __getattr__(self, key): return self[key]
    def __setattr__(self, key, value): self[key] = value


def run_stream(events):
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'stream_pipeline')
    state = State(run_aero=True, run_struct=True, status='Ready', glb_bytes=b'accepted',
        script_code='accepted source', script_version=1, parameters={'old': 1},
        geometry_certificate={'passed': True}, aero_report={'old': True}, struct_report={'old': True},
        last_manifest={'old': True}, last_scripts={'box': 'accepted source'})
    response = Mock(status_code=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_lines.return_value = [e if isinstance(e,str) else 'data: '+json.dumps(e) for e in events]
    requests = SimpleNamespace(post=Mock(return_value=response), ConnectionError=ConnectionError, Timeout=TimeoutError, RequestException=RuntimeError)
    st = SimpleNamespace(session_state=state, error=Mock())
    ns = dict(st=st, requests=requests, BACKEND_URL='http://fixture', _auth_headers=lambda: {}, json=json, base64=base64,
              _progress_md=lambda steps: str(steps), _step_label=lambda step: step)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(APP), 'exec'), ns)
    ns['stream_pipeline']('box', Mock())
    return state, requests, st


def test_structural_opt_in_reaches_backend():
    _, requests, _ = run_stream([{'step':'complete','status':'done','glb':base64.b64encode(b'new').decode()}])
    assert requests.post.call_args.kwargs['json'] == {'prompt':'box','run_aero':True,'run_struct':True}


@pytest.mark.parametrize('events', [
    [{'step':'machinist:box','status':'error','detail':'failed'}, {'step':'complete','status':'error','message':'failed','script':'broken'}],
    [{'step':'complete','status':'error','message':'failed','script':'broken'}],
    [{'step':'planner','status':'running','detail':'planning'}],
    [{'step':'complete','status':'done','glb':''}],
    ['data: {not-json'],
    [None], [[]], [123], [{}],
    [{'step':'complete'}], [{'status':'done'}],
])
def test_failed_or_incomplete_generation_preserves_accepted_result(events):
    state, _, _ = run_stream(events)
    assert state.glb_bytes == b'accepted'
    assert state.script_code == 'accepted source'
    assert state.parameters == {'old': 1}
    assert state.geometry_certificate == {'passed': True}
    assert state.status.startswith('Error')


def test_success_replaces_accepted_result():
    state, _, _ = run_stream([{'step':'complete','status':'done','glb':base64.b64encode(b'new').decode(),
        'script':'new source','manifest':{'assembly_name':'box'},'scripts':{'box':'new source'},
        'geometry_certificate':{'passed':True}}])
    assert state.glb_bytes == b'new'
    assert state.script_code == 'new source'
    assert state.last_manifest == {'assembly_name':'box'}
    assert state.status == 'Ready'


@pytest.mark.parametrize('bad',[
    {}, {'step':'complete'}, {'status':'done'},
    {'step':'','status':'done'}, {'step':'complete','status':''},
    {'step':123,'status':'done'}, {'step':'complete','status':False},
    None, [], 42,
])
def test_malformed_event_after_completion_cannot_publish_partial_result(bad):
    state, _, _ = run_stream([
        {'step':'complete','status':'done','glb':base64.b64encode(b'new').decode(),
         'script':'new source','manifest':{'new':True},'scripts':{'new':'source'}},
        bad,
    ])
    assert state.glb_bytes == b'accepted'
    assert state.script_code == 'accepted source'
    assert state.geometry_certificate == {'passed':True}
    assert state.status.startswith('Error')
