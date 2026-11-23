"""F08 MCP server: ``python -m engineering.program.mcp`` (stdio, newline-delimited JSON-RPC 2.0).

Stdlib only, no MCP SDK. An HTTP client of the engineering API (D3): every tool call goes through
engineering.program.client.ProgramClient. The server holds no store, no service and no key.

Tools are exactly the registry operations whose transports include mcp (D12): validate, format, plan,
propose, status, inspect, history, diff, run, cancel. create, review, restore, rebuild and export are not
exposed. inputSchema comes from interface.mcp_input_schema (the same request models the CLI uses). No
argument is a path, URL or directory: MEL source is a string argument, sent as raw bytes.

propose (D12): reads the program head through the client, sends X-Mirum-Require-Requirements = the
head's fixed_requirements_sha256, and never calls stage without a head. The server never supplies review
data and never reviews, so a proposal always lands as a staged revision that needs a human review.

Safety: the server URL comes from MIRUM_API_URL and the token only from MIRUM_ACCESS_TOKEN; the token is
never part of any output. Responses and stderr lines are fixed or come from interface models, never
exception text, paths or tracebacks. Input lines are read with a hard cap (MAX_MCP_MESSAGE_BYTES) and
parsed with a depth cap; an oversize line is answered with an error and the rest of it is discarded
without being buffered.

PURE CLIENT: imports only the stdlib, pydantic (through interface), interface and client.
"""
from __future__ import annotations

import json
import sys

from pydantic import ValidationError

from . import interface as api
from .client import ClientError, ProgramClient, TransportError

__all__ = ['McpServer', 'serve', 'main', 'SERVER_NAME', 'SUPPORTED_PROTOCOL_VERSIONS', 'URL_ENV', 'TOKEN_ENV',
           'tool_names']

URL_ENV = 'MIRUM_API_URL'
TOKEN_ENV = 'MIRUM_ACCESS_TOKEN'
SERVER_NAME = 'mirum-program'
SERVER_VERSION = '0.1'
SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_MESSAGES = {
    PARSE_ERROR: 'Parse error',
    INVALID_REQUEST: 'Invalid request',
    METHOD_NOT_FOUND: 'Method not found',
    INVALID_PARAMS: 'Invalid params',
    INTERNAL_ERROR: 'Internal error',
}

_MEL_TOOLS = frozenset({'validate', 'format', 'plan'})


def tool_names() -> tuple[str, ...]:
    return tuple(api.MCP_TOOL_NAMES)


def _rpc_error(code: int, request_id=None, message: str | None = None) -> dict:
    return {'jsonrpc': '2.0', 'id': request_id,
            'error': {'code': code, 'message': message or _MESSAGES[code]}}


def _valid_id(value) -> bool:
    return (type(value) is str and len(value) <= 256) or (type(value) is int and abs(value) <= 2 ** 53)


def _dump(model) -> dict:
    return json.loads(model.model_dump_json())


def _transport_envelope(code: str) -> dict:
    return {'kind': 'transport_error', 'code': code}


class McpServer:
    """JSON-RPC 2.0 dispatcher. ``handle`` takes a decoded message dict or raw bytes/str and returns the
    response dict, or None when no reply is due (notifications, stray responses)."""

    def __init__(self, client, *, max_message_bytes: int = api.MAX_MCP_MESSAGE_BYTES):
        self._client = client
        self._max = max_message_bytes
        self._tools = {d['name']: d for d in api.mcp_tool_descriptors()}

    # ------------------------------------------------------------------------------------ dispatch

    def handle(self, message):
        if isinstance(message, str):
            try:
                message = message.encode('utf-8')
            except UnicodeEncodeError:
                return _rpc_error(PARSE_ERROR)
        if isinstance(message, (bytes, bytearray)):
            try:
                message = api.bounded_json_loads(bytes(message), max_bytes=self._max, max_depth=api.MAX_JSON_DEPTH)
            except api.BoundsError as exc:
                if exc.code == 'malformed_json':
                    return _rpc_error(PARSE_ERROR)
                return _rpc_error(INVALID_REQUEST, message='Message too large or too deep')
        if not isinstance(message, dict):  # includes batches: not supported, a single message per line
            return _rpc_error(INVALID_REQUEST)
        if 'method' not in message:
            return None if ('result' in message or 'error' in message) else _rpc_error(INVALID_REQUEST)
        method = message.get('method')
        has_id = 'id' in message
        request_id = message.get('id')
        if message.get('jsonrpc') != '2.0' or type(method) is not str or (has_id and not _valid_id(request_id)):
            return _rpc_error(INVALID_REQUEST, request_id if has_id and _valid_id(request_id) else None)
        params = message.get('params')
        if params is not None and not isinstance(params, dict):
            return _rpc_error(INVALID_PARAMS, request_id) if has_id else None
        if not has_id:  # a notification never gets a reply
            return None
        params = params or {}
        if method == 'initialize':
            return self._initialize(request_id, params)
        if method == 'ping':
            return {'jsonrpc': '2.0', 'id': request_id, 'result': {}}
        if method == 'tools/list':
            return {'jsonrpc': '2.0', 'id': request_id, 'result': {'tools': self._tool_list()}}
        if method == 'tools/call':
            return self._call(request_id, params)
        return _rpc_error(METHOD_NOT_FOUND, request_id)

    def _initialize(self, request_id, params):
        wanted = params.get('protocolVersion')
        version = wanted if wanted in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        return {'jsonrpc': '2.0', 'id': request_id, 'result': {
            'protocolVersion': version, 'capabilities': {'tools': {}},
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION}}}

    def _tool_list(self) -> list:
        out = []
        for name in api.MCP_TOOL_NAMES:
            tool = dict(self._tools[name])
            tool['annotations'] = {'readOnlyHint': not api.OPERATIONS_BY_NAME[name].mutating}
            out.append(tool)
        return out

    # ---------------------------------------------------------------------------------- tools/call

    def _call(self, request_id, params):
        name = params.get('name')
        arguments = params.get('arguments', {})
        if type(name) is not str or name not in self._tools or not isinstance(arguments, dict):
            return _rpc_error(INVALID_PARAMS, request_id)
        payload, is_error = self._run_tool(name, arguments)
        text = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return {'jsonrpc': '2.0', 'id': request_id, 'result': {
            'content': [{'type': 'text', 'text': text}], 'isError': is_error, 'structuredContent': payload}}

    def _run_tool(self, name: str, arguments: dict):
        try:
            return self._dispatch(name, dict(arguments))
        except api.InterfaceFailure as exc:
            return _dump(exc.error), True
        except ClientError as exc:
            return _dump(exc.error), True
        except TransportError as exc:
            return _transport_envelope(exc.code), True
        except Exception:  # noqa: BLE001 - fixed envelope, never exception text
            return _transport_envelope('internal_error'), True

    def _dispatch(self, name: str, arguments: dict):
        spec = api.OPERATIONS_BY_NAME[name]
        source = None
        if name in _MEL_TOOLS or name == 'propose':
            source = self._source(arguments.pop('source', None))
        try:
            request = api.parse_model(spec.input_model, arguments)
        except ValidationError as exc:
            raise api.InterfaceFailure(api.from_validation_error(exc)) from None
        client = self._client
        if name == 'validate':
            result = client.validate(source, request.source_id)
        elif name == 'format':
            result = client.format(source, request.source_id)
        elif name == 'plan':
            result = client.plan(source, request.source_id)
        elif name == 'propose':
            result = self._propose(request, source)
        elif name == 'status':
            result = client.status(request.revision_id)
        elif name == 'inspect':
            result = client.inspect(request.revision_id)
        elif name == 'history':
            result = client.history(request.program_id)
        elif name == 'diff':
            result = client.diff(request.revision_id, request.other_revision_id)
        elif name == 'run':
            result = client.run(request.revision_id)
        else:  # cancel
            result = client.cancel(request.revision_id)
        return _dump(result), False

    @staticmethod
    def _source(value) -> bytes:
        if type(value) is not str:
            raise api.InterfaceFailure(api.invalid_request('invalid_field', field='source'))
        try:
            data = value.encode('utf-8')
        except UnicodeEncodeError:
            raise api.InterfaceFailure(api.invalid_request('invalid_field', field='source')) from None
        if len(data) > api.MAX_MEL_BODY_BYTES:
            raise api.InterfaceFailure(api.too_large('body_too_large'))
        return data

    def _propose(self, request, source: bytes):
        """Stage with the requirements guard read from the program head (D12). No head -> no stage call."""
        history = self._client.history(request.program_id)
        head_id = history.head_revision_id
        if head_id is None:
            raise api.InterfaceFailure(api.error('requirements_changed', field=None))
        head = next((r for r in history.revisions if r.revision_id == head_id), None)
        if head is None:
            head = self._client.status(head_id).revision
        return self._client.propose(request.program_id, source, request.source_id, request.expected_parent,
                                    require_requirements_sha256=head.fixed_requirements_sha256)


# ---------------------------------------------------------------------------------------- stdio loop

def _read_line(stdin, cap: int):
    """One line without its newline, or None at EOF. Returns (data, oversize). Never buffers more than
    cap + 1 bytes: the rest of an oversize line is read and dropped in cap-sized chunks."""
    line = stdin.readline(cap + 1)
    if not line:
        return None, False
    if len(line) > cap and not line.endswith(b'\n'):
        while True:
            rest = stdin.readline(cap + 1)
            if not rest or rest.endswith(b'\n'):
                break
        return b'', True
    return line.rstrip(b'\r\n'), False


def _write(stdout, response: dict) -> None:
    stdout.write((json.dumps(response, separators=(',', ':')) + '\n').encode('ascii'))
    stdout.flush()


def _log(stderr, line: str) -> None:
    try:
        stderr.write(line + '\n')
        stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def serve(server: McpServer, stdin, stdout, stderr=None) -> None:
    """Run the loop over binary streams until EOF."""
    cap = api.MAX_MCP_MESSAGE_BYTES
    if stderr is not None:
        _log(stderr, 'mirum-mcp: started')
    while True:
        data, oversize = _read_line(stdin, cap)
        if data is None:
            break
        if oversize:
            if stderr is not None:
                _log(stderr, 'mirum-mcp: oversize message discarded')
            _write(stdout, _rpc_error(INVALID_REQUEST, message='Message too large'))
            continue
        if not data.strip():
            continue
        response = server.handle(data)
        if response is not None:
            _write(stdout, response)
    if stderr is not None:
        _log(stderr, 'mirum-mcp: stopped')


def main(argv=None, *, environ=None, stdin=None, stdout=None, stderr=None, transport=None) -> int:
    import os
    environ = os.environ if environ is None else environ
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    url = environ.get(URL_ENV)
    if not url:
        _log(stderr, 'mirum-mcp: MIRUM_API_URL is not set')
        return 1
    try:
        client = ProgramClient(url, token=environ.get(TOKEN_ENV) or None, transport=transport)
    except TransportError as exc:
        _log(stderr, 'mirum-mcp: invalid configuration (' + exc.code + ')')
        return 1
    serve(McpServer(client), stdin, stdout, stderr)
    return 0


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())
