"""One ledger call equals one HTTP request (B13 step 12 security review, M2).

The proposer's AiCallBudget and the audit ledger count one call per GeminiProposalProvider.generate(). That
count is only the real provider spend if the SDK sends exactly one HTTP request per generate(), i.e. its own
retry loop (HttpRetryOptions) is off. This points the REAL proposer, with the REAL google-genai client, at a
local stub HTTP server through the SDK's documented GOOGLE_GEMINI_BASE_URL variable and counts inbound
requests. Nothing here reaches the network beyond 127.0.0.1 and no real key is used.

Skipped when google-genai is not importable (host venvs); runs in the native shards where it is installed.
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip('google.genai')


class _Stub:
    def __init__(self, status):
        self.status, self.requests = status, []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.rfile.read(length)
                stub.requests.append(self.path)
                body = json.dumps({'error': {'code': stub.status, 'message': 'stub refusal',
                                             'status': 'UNAVAILABLE' if stub.status == 503 else 'RESOURCE_EXHAUSTED'}}).encode()
                self.send_response(stub.status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d/' % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def stub_env(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)


@pytest.mark.parametrize('status', [503, 429])
def test_one_generate_call_is_exactly_one_http_request(status, monkeypatch, stub_env):
    from engineering import proposer as module
    with _Stub(status) as stub:
        monkeypatch.setenv('GOOGLE_GEMINI_BASE_URL', stub.url)
        provider = module.GeminiProposalProvider()
        with pytest.raises(Exception) as caught:
            asyncio.run(provider.generate('fixture prompt', module.provider_schema(), provider.settings()))
        assert getattr(caught.value, 'code', status) == status
        assert len(stub.requests) == 1, stub.requests


def test_two_generate_calls_are_two_http_requests(monkeypatch, stub_env):
    from engineering import proposer as module
    with _Stub(503) as stub:
        monkeypatch.setenv('GOOGLE_GEMINI_BASE_URL', stub.url)
        provider = module.GeminiProposalProvider()
        for _ in range(2):
            with pytest.raises(Exception):
                asyncio.run(provider.generate('fixture prompt', module.provider_schema(), provider.settings()))
        assert len(stub.requests) == 2


def test_the_stub_would_see_sdk_retries_if_they_were_on(monkeypatch, stub_env):
    """Control: with attempts=2 the same client and stub record two requests, so the count above is meaningful."""
    from google import genai
    from google.genai import types
    with _Stub(503) as stub:
        monkeypatch.setenv('GOOGLE_GEMINI_BASE_URL', stub.url)
        options = types.HttpOptions(timeout=5000, retry_options=types.HttpRetryOptions(
            attempts=2, initial_delay=0.01, max_delay=0.02))
        client = genai.Client(api_key='offline-placeholder', vertexai=False, http_options=options)

        async def call():
            try:
                await client.aio.models.generate_content(model='fixture-model', contents='x')
            finally:
                await client.aio.aclose()

        with pytest.raises(Exception):
            asyncio.run(call())
        assert len(stub.requests) == 2
