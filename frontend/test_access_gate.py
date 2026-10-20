"""The UI forwards the visitor's access token on every backend call."""
import ast
from pathlib import Path

APP = Path(__file__).with_name('app.py')


def test_auth_headers_use_the_session_token():
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_auth_headers')

    class State(dict):
        get = dict.get

    class St:
        session_state = State()

    ns = {'st': St}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP), 'exec'), ns)
    assert ns['_auth_headers']() == {}
    St.session_state['access_token'] = 'secret'
    assert ns['_auth_headers']() == {'X-Mirum-Access-Token': 'secret'}


def test_every_backend_call_sends_auth_headers():
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr in {'post', 'get'}
             and isinstance(n.func.value, ast.Name) and n.func.value.id in {'requests', '_req'}]
    assert len(calls) >= 8
    for call in calls:
        keywords = {k.arg for k in call.keywords}
        assert 'headers' in keywords, ast.unparse(call)[:120]
    assert 'http://backend:8000/refine' not in APP.read_text(encoding='utf-8')
