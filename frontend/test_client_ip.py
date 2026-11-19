"""Visitor address as seen by the platform proxy, forwarded to the backend."""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import client_ip  # noqa: E402

APP = Path(__file__).with_name('app.py')


def _ctx(headers):
    return patch.object(client_ip.st, 'context', SimpleNamespace(headers=headers), create=True)


def test_prefers_fly_client_ip():
    with _ctx({'Fly-Client-IP': '203.0.113.7', 'X-Forwarded-For': '198.51.100.1'}):
        assert client_ip.visitor_ip() == '203.0.113.7'


def test_headers_are_case_insensitive_for_plain_dicts():
    with _ctx({'fly-client-ip': '203.0.113.7'}):
        assert client_ip.visitor_ip() == '203.0.113.7'


def test_falls_back_to_first_forwarded_hop():
    with _ctx({'X-Forwarded-For': ' 198.51.100.1 , 10.0.0.2'}):
        assert client_ip.visitor_ip() == '198.51.100.1'


def test_invalid_fly_header_falls_back_to_forwarded():
    with _ctx({'Fly-Client-IP': 'nope', 'X-Forwarded-For': '198.51.100.1'}):
        assert client_ip.visitor_ip() == '198.51.100.1'


def test_ipv6_normalised():
    with _ctx({'Fly-Client-IP': '2001:0DB8::0001'}):
        assert client_ip.visitor_ip() == '2001:db8::1'


def test_invalid_returns_none():
    with _ctx({'Fly-Client-IP': 'garbage', 'X-Forwarded-For': 'also, bad'}):
        assert client_ip.visitor_ip() is None
    with _ctx({'Fly-Client-IP': 'fe80::1%eth0'}):
        assert client_ip.visitor_ip() is None
    with _ctx({'X-Forwarded-For': 'x' * 1000}):
        assert client_ip.visitor_ip() is None


def test_absent_returns_none():
    with _ctx({}):
        assert client_ip.visitor_ip() is None


def test_context_unavailable_returns_none():
    class Broken:
        @property
        def headers(self):
            raise RuntimeError('no script run context')

    with patch.object(client_ip.st, 'context', Broken(), create=True):
        assert client_ip.visitor_ip() is None


def test_client_headers_adds_header_only_when_known():
    with _ctx({'Fly-Client-IP': '203.0.113.7'}):
        assert client_ip.client_ip_headers() == {'X-Mirum-Client-IP': '203.0.113.7'}
    with _ctx({}):
        assert client_ip.client_ip_headers() == {}


def test_auth_headers_include_client_ip():
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_auth_headers')

    class St:
        session_state = {}

    ns = {'st': St, 'client_ip_headers': lambda: {'X-Mirum-Client-IP': '203.0.113.7'}}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP), 'exec'), ns)
    assert ns['_auth_headers']() == {'X-Mirum-Client-IP': '203.0.113.7'}
    St.session_state['access_token'] = 't'
    assert ns['_auth_headers']() == {'X-Mirum-Access-Token': 't', 'X-Mirum-Client-IP': '203.0.113.7'}


def test_access_check_call_also_sends_client_ip():
    src = APP.read_text(encoding='utf-8')
    assert '"X-Mirum-Access-Token": entered, **client_ip_headers()' in src
