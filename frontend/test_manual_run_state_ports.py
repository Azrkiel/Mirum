"""Manual geometry edits must not retain evidence for the previous model."""
import ast
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests


def run_manual(response):
    app = Path(__file__).with_name('app.py')
    fn = next(n for n in ast.parse(app.read_text(encoding='utf-8')).body
              if isinstance(n,ast.FunctionDef) and n.name == 'run_script')
    state = SimpleNamespace(status='Ready',glb_bytes=b'old',script_code='old script',
        geometry_certificate={'passed':True},struct_report={'old':1},aero_report={'old':2},
        aero_plots={'old':3},xfoil_pdf_bytes=b'old pdf',parameters={'old':1},
        last_manifest={'old':1},last_scripts={'old':'old source'},show_plan=True,pending_plan={'old':2})
    st = SimpleNamespace(session_state=state,error=Mock())
    req = SimpleNamespace(post=Mock(return_value=SimpleNamespace(json=lambda:response)),
        ConnectionError=requests.ConnectionError,Timeout=requests.Timeout,RequestException=requests.RequestException)
    ns = dict(st=st,requests=req,base64=base64,BACKEND_URL='http://fixture')
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(app),'exec'),ns)
    ns['run_script']('new runnable script')
    return state


def test_successful_manual_edit_clears_stale_evidence_and_refinement_context():
    state = run_manual({'status':'ok','glb':base64.b64encode(b'new').decode()})
    assert state.glb_bytes == b'new'
    assert state.script_code == 'new runnable script'
    assert state.geometry_certificate is None
    assert state.struct_report is None
    assert state.aero_report is None and state.aero_plots is None
    assert state.xfoil_pdf_bytes is None
    assert state.parameters == {}
    assert state.last_manifest is None and not state.last_scripts
    assert state.pending_plan is None and state.show_plan is False
    assert state.status == 'Ready'


@pytest.mark.parametrize('response',[
    {'status':'error','detail':'fixture fail'},
    {'status':'ok','glb':'not!base64'},
    None, [], 123, 'unexpected scalar',
    {'status':'pending','glb':base64.b64encode(b'new').decode()},
])
def test_failed_manual_edit_retains_previous_accepted_model_and_evidence(response):
    state = run_manual(response)
    assert state.glb_bytes == b'old'
    assert state.geometry_certificate == {'passed':True}
    assert state.struct_report == {'old':1}
    assert state.last_manifest == {'old':1}
    assert state.script_code == 'old script'
    assert state.status == 'Run failed'
