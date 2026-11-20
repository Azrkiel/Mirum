"""Exercise the real UI streaming function without starting the Streamlit page.

Ported behaviors from Text-2-CAD: retain accepted results on errors and distinguish
terminal completion from a broken transport. HTTP responses below are fixtures.
"""
import ast
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

APP = Path(__file__).with_name('app.py')
sys.path.insert(0, str(APP.parent))


class State(dict):
    def __getattr__(self, key): return self[key]
    def __setattr__(self, key, value): self[key] = value


def run_stream(events):
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'stream_pipeline')
    apply = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'apply_route')
    state = State(run_aero=True, run_struct=True, status='Ready', glb_bytes=b'accepted',
        script_code='accepted source', script_version=1, parameters={'old': 1},
        geometry_certificate={'passed': True}, aero_report={'old': True}, struct_report={'old': True},
        last_manifest={'old': True}, last_scripts={'box': 'accepted source'})
    response = Mock(status_code=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_lines.return_value = [e if isinstance(e,str) else 'data: '+json.dumps(e) for e in events]
    requests = SimpleNamespace(post=Mock(return_value=response), ConnectionError=ConnectionError, Timeout=TimeoutError, RequestException=RuntimeError)
    # expander/code: failure details render in a collapsible block.
    st = SimpleNamespace(session_state=state, error=Mock(), expander=MagicMock(), code=Mock(), rerun=Mock())
    ns = dict(st=st, requests=requests, BACKEND_URL='http://fixture', _auth_headers=lambda: {}, json=json, base64=base64,
              _progress_md=lambda steps: str(steps), _step_label=lambda step: step)
    exec(compile(ast.Module(body=[apply, function], type_ignores=[]), str(APP), 'exec'), ns)
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


def test_route_event_switches_workflow_and_sets_notice():
    route = {'step':'route','status':'done','workflow':'regen_chamber','capability_id':'regen_chamber',
             'assurance':'checked_design','reason':'r','detail':'d'}
    state, _, st = run_stream([route])
    assert state.pending_workflow == 'Checked design'
    assert state.pending_v1 == {'capability_id': 'regen_chamber', 'prompt': None}
    assert state.route_notice == 'Checked design: Deterministic rocket nozzle'
    st.rerun.assert_called_once()
    assert state.glb_bytes == b'accepted'


def test_legacy_dragster_route_maps_to_dragster_tab():
    route = {'step':'route','status':'done','workflow':'co2_dragster','detail':'d'}
    state, _, _ = run_stream([route])
    assert state.pending_workflow == 'CO2 dragster'


def test_complete_assurance_is_stored_and_labelled():
    from route_labels import assurance_label
    state, _, _ = run_stream([{'step':'complete','status':'done','glb':base64.b64encode(b'new').decode(),
                               'assurance':'unverified_geometry'}])
    assert state.assurance == 'unverified_geometry'
    assert assurance_label('unverified_geometry') == 'Unverified geometry'
    assert assurance_label('checked_design') == 'Checked design'
    assert assurance_label(None) is None


def test_route_with_backend_draft_hint_queues_the_prompt_for_a_rules_draft():
    route = {'step':'route','status':'done','workflow':'co2_dragster','capability_id':'co2_dragster',
             'assurance':'checked_design','reason':'r','detail':'d','draft':{'payload':{},'issues':[],'provenance':{}}}
    state, _, _ = run_stream([route])
    assert state.pending_workflow == 'Checked design'
    assert state.pending_v1 == {'capability_id': 'co2_dragster', 'prompt': 'box'}


# ---- route first: a checked-capability prompt must not reach /clarify or /plan

def load_route_helpers(post):
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    wanted = {'apply_route', 'fetch_route'}
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {f.name for f in funcs} == wanted
    state = State()
    st = SimpleNamespace(session_state=state, rerun=Mock())
    requests = SimpleNamespace(post=post)
    ns = dict(st=st, requests=requests, BACKEND_URL='http://fixture', _auth_headers=lambda: {})
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(APP), 'exec'), ns)
    return ns, state, st


def reply(payload, status=200):
    return Mock(return_value=Mock(status_code=status, json=Mock(return_value=payload)))


BEAM_EVENT = {'step': 'route', 'status': 'done', 'workflow': 'rectangular_cantilever',
              'capability_id': 'rectangular_cantilever', 'assurance': 'checked_design'}


def test_fetch_route_calls_only_the_provider_free_route_endpoint():
    post = reply({'route': BEAM_EVENT})
    ns, _, _ = load_route_helpers(post)
    assert ns['fetch_route']('a beam') == BEAM_EVENT
    assert post.call_count == 1 and post.call_args.args[0] == 'http://fixture/route'


def test_fetch_route_is_none_only_when_the_router_answers_no_route():
    ns, _, _ = load_route_helpers(reply({'route': None}))
    assert ns['fetch_route']('a prompt') is None


@pytest.mark.parametrize('post', [reply({}, 500), reply({'route': None}, 429), reply({'route': 'x'}), reply({}),
                                  reply(['not', 'a', 'dict']), Mock(side_effect=RuntimeError('down'))])
def test_fetch_route_fails_closed_as_unavailable_on_any_failure(post):
    ns, _, _ = load_route_helpers(post)
    assert ns['fetch_route']('a prompt') == 'unavailable'


def test_an_unmappable_checked_route_never_reaches_clarify_or_plan():
    src = APP.read_text(encoding='utf-8')
    block = src[src.index('if generate and prompt.strip():'):]
    routed = block[block.index('elif routed is not None:'):block.index('else:', block.index('elif routed is not None:'))]
    assert 'apply_route(routed, p)' in routed and 'ambs = None' in routed and 'fetch_clarifications' not in routed


def test_unavailable_route_never_reaches_clarify_or_plan():
    src = APP.read_text(encoding='utf-8')
    block = src[src.index('if generate and prompt.strip():'):]
    unavailable = block[block.index('if routed == "unavailable":'):block.index('elif routed is not None')]
    assert 'ambs = None' in unavailable and 'fetch_clarifications' not in unavailable


def test_apply_route_queues_the_checked_design_view_with_notice():
    ns, state, st = load_route_helpers(reply({}))
    assert ns['apply_route'](BEAM_EVENT, 'a beam') is True
    assert state.pending_workflow == 'Checked design'
    assert state.pending_v1 == {'capability_id': 'rectangular_cantilever', 'prompt': None}
    assert state.route_notice == 'Checked design: Checked beam'
    st.rerun.assert_called_once()


def test_apply_route_ignores_an_event_naming_no_capability():
    ns, state, st = load_route_helpers(reply({}))
    assert ns['apply_route']({'step': 'route', 'capability_id': 'nope'}, 'p') is False
    st.rerun.assert_not_called() and not state


def test_generate_button_routes_before_clarify_and_plan():
    src = APP.read_text(encoding='utf-8')
    block = src[src.index('if generate and prompt.strip():'):]
    assert block.index('fetch_route(') < block.index('fetch_clarifications(') < block.index('fetch_plan(')
