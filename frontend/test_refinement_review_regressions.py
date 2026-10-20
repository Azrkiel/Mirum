"""Execute real generation/refinement UI bodies without Streamlit page startup."""
import ast
import base64
import json
import sys
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests as real_requests

APP = Path(__file__).with_name('app.py')


class State(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


def generated_ui(monkeypatch):
    """Populate the old accepted model through the actual /generate consumer."""
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    stream = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'stream_pipeline')
    apply = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and any(
        isinstance(c, ast.Constant) and c.value == 'Apply Refinement' for c in ast.walk(n.test)))
    state = State(run_aero=True, run_struct=True, status='Ready', script_version=0)
    st = SimpleNamespace(session_state=state, error=Mock(), warning=Mock(), success=Mock(),
                         spinner=lambda *a: nullcontext(), button=Mock(return_value=True), rerun=Mock())
    old = dict(step='complete', status='done', glb=base64.b64encode(b'old geometry').decode(),
               script='old runnable script', manifest={'assembly_name': 'old wing'},
               scripts={'wing': 'old part'}, parameters={'chord': 10},
               geometry_certificate={'passed': True, 'volume_mm3': 600},
               aero_report={'Cl': [0.1]}, struct_report={'passed': True})
    response = Mock(status_code=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_lines.return_value = ['data: ' + json.dumps(old)]
    requests = SimpleNamespace(post=Mock(return_value=response), ConnectionError=ConnectionError,
                               Timeout=TimeoutError, RequestException=real_requests.RequestException)
    monkeypatch.setitem(sys.modules, 'requests', requests)
    ns = dict(st=st, requests=requests, BACKEND_URL='http://fixture', _auth_headers=lambda: {}, base64=base64, json=json,
              _progress_md=str, _step_label=str, refinement_text='double the chord')
    exec(compile(ast.Module(body=[stream], type_ignores=[]), str(APP), 'exec'), ns)
    ns['stream_pipeline']('old wing', Mock())
    state.aero_plots = {'cl_alpha': 'old plot'}
    state.xfoil_pdf_bytes = b'old PDF'
    return state, requests, st, ns, apply


def apply_refinement(monkeypatch, response):
    state, requests, st, ns, apply = generated_ui(monkeypatch)
    before = State(deepcopy(dict(state)))
    if isinstance(response, Exception):
        requests.post.side_effect = response
    else:
        requests.post.return_value = Mock(json=Mock(return_value=response))
    exec(compile(ast.Module(body=[apply], type_ignores=[]), str(APP), 'exec'), ns)
    return state, before, requests, st


@pytest.mark.parametrize('script', ['new runnable script', None])
def test_changed_refinement_discards_old_certificate_analysis_and_editor_state(monkeypatch, script):
    result = dict(status='ok', glb=base64.b64encode(b'changed geometry').decode(),
                  updated_manifest={'assembly_name': 'new wing'}, updated_scripts={'wing': 'new part'},
                  regenerated_parts=['wing'], reused_parts=[])
    if script is not None:
        result['script'] = script
    state, before, requests, st = apply_refinement(monkeypatch, result)
    assert before.geometry_certificate['passed'] is True
    assert state.glb_bytes == b'changed geometry'
    assert state.geometry_certificate is None
    assert state.struct_report is None
    assert state.aero_report is None
    assert state.aero_plots is None
    assert state.xfoil_pdf_bytes is None
    assert state.parameters == {}
    assert state.script_code == (script or '')
    assert state.script_version == before.script_version + 1
    assert state.last_manifest == result['updated_manifest']
    assert state.last_scripts == result['updated_scripts']
    assert requests.post.call_args.kwargs['json']['original_manifest'] == before.last_manifest
    st.rerun.assert_called_once()
    st.error.assert_not_called()


@pytest.mark.parametrize('response', [
    {'status': 'error', 'detail': 'compile failed'},
    ConnectionError('disconnected'),
    {'status': 'ok', 'glb': '', 'updated_manifest': {'assembly_name': 'rejected'}},
    {'status': 'ok', 'glb': '!!!!', 'updated_scripts': {'wing': 'rejected'}},
])
def test_failed_refinement_preserves_entire_accepted_result(monkeypatch, response):
    state, before, _, st = apply_refinement(monkeypatch, response)
    assert state == before
    st.rerun.assert_not_called()
    st.success.assert_not_called()
