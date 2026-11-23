"""F08 command line interface: ``python -m engineering.program.cli [--url URL | --offline] <command> ...``.

Subcommands and their arguments are generated from the operation registry and its request models
(engineering.program.interface), so the CLI cannot drift from the other transports. A parity test
asserts it.

PURE CLIENT (D3): imports only the stdlib, pydantic, interface and client at import time. The offline
functions (validate/format/plan, the very functions the server runs) are imported lazily and only for
``--offline``. Never the store, sqlite3, a web framework, an HTTP client library, transactions, the
facade, the routes module or a native module (a subprocess test pins this).

Safety (D13): reads only the one file the user names (capped at 64 KiB + 1); the server comes from
--url or MIRUM_API_URL; the access token comes ONLY from MIRUM_ACCESS_TOKEN (there is no token flag) and
is never printed; no X-Mirum-Client-IP; no redirects; JSON responses capped at 4 MiB; ``export`` writes
only <out>/<allowlisted name>, atomically, never over an existing file or through a symlink, after the
bytes match X-Content-SHA256.

Output: the response model as JSON on stdout; errors as the interface envelope JSON on stderr (a
transport failure is {"kind": "transport_error", "code": <fixed code>}).
Exit codes: 0 ok, 1 transport error, 2 invalid/unsupported/rejected, 3 unavailable/busy, 4 conflict/refused.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import typing
import argparse

from . import interface as api
from .client import EXPORT_FILENAMES, ClientError, ProgramClient, TransportError

__all__ = ['main', 'cli_operations', 'cli_name', 'build_parser', 'OFFLINE_OPERATIONS', 'URL_ENV', 'TOKEN_ENV']

URL_ENV = 'MIRUM_API_URL'
TOKEN_ENV = 'MIRUM_ACCESS_TOKEN'
OFFLINE_OPERATIONS = ('validate', 'format', 'plan')


def cli_operations() -> tuple:
    """Registry operations exposed as subcommands (those whose transports include cli)."""
    return tuple(op for op in api.OPERATIONS if 'cli' in op.transports)


def cli_name(spec) -> str:
    return spec.name.replace('_', '-')


class _Usage(Exception):
    pass


class _Exit(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def _parser_class(stdout):
    class _Parser(argparse.ArgumentParser):
        def error(self, message):  # never echo argv (it could hold secrets typed by mistake)
            raise _Usage()

        def exit(self, status=0, message=None):
            raise _Exit(status)

        def _print_message(self, message, file=None):
            if message:
                stdout.write(message)
    return _Parser


def _model_fields(spec):
    return [(n, f) for n, f in spec.input_model.model_fields.items() if n != 'api_version']


def _is_tuple(info) -> bool:
    return typing.get_origin(info.annotation) is tuple


def build_parser(stdout=None) -> argparse.ArgumentParser:
    parser_cls = _parser_class(stdout if stdout is not None else sys.stdout)
    parser = parser_cls(prog='python -m engineering.program.cli', allow_abbrev=False,
                        description='Mirum program interface (one runtime through Python, CLI, HTTP, MCP).')
    parser.add_argument('--url', default=None, help=f'API base URL (default: ${URL_ENV}).')
    parser.add_argument('--offline', action='store_true',
                        help='Run validate/format/plan locally with no network (same code as the server).')
    sub = parser.add_subparsers(dest='command', required=True, parser_class=parser_cls)
    for spec in cli_operations():
        sp = sub.add_parser(cli_name(spec), allow_abbrev=False, help=spec.description)
        for name, info in _model_fields(spec):
            flag = '--' + name.replace('_', '-')
            help_text = info.description
            if _is_tuple(info):
                sp.add_argument(flag, dest=name, nargs='*', default=[], required=info.is_required(),
                                help=help_text)
            elif info.is_required():
                sp.add_argument(name, help=help_text)
            else:
                sp.add_argument(flag, dest=name, default=None, help=help_text)
        if spec.body == 'mel':
            sp.add_argument('file', help='MEL source file (read once, at most 64 KiB).')
        if spec.name == 'export':
            sp.add_argument('--out', dest='out', required=True, help='Existing directory to write into.')
    return parser


# --------------------------------------------------------------------------------------------- helpers

class _Fail(Exception):
    def __init__(self, err):
        super().__init__(err.code)
        self.err = err


def _dump(model) -> str:
    return json.dumps(model.model_dump(mode='json'), sort_keys=True, indent=2) + '\n'


def _read_source(path) -> bytes:
    try:
        if not os.path.isfile(path):
            raise OSError
        with open(path, 'rb') as handle:
            return handle.read(api.MAX_MEL_BODY_BYTES + 1)
    except (OSError, ValueError):
        raise _Fail(api.invalid_request('invalid_field', field='file')) from None


def _refuse_out() -> _Fail:
    return _Fail(api.invalid_request('invalid_field', field='out'))


def _export(client, ns, stdout) -> int:
    if not api.is_export_format(ns.fmt):
        raise _Fail(api.not_found())
    out = ns.out
    if not isinstance(out, str) or not out or '\x00' in out:
        raise _refuse_out()
    if os.path.islink(out) or not os.path.isdir(out):
        raise _refuse_out()
    target = os.path.join(out, EXPORT_FILENAMES[ns.fmt])
    if os.path.lexists(target):
        raise _refuse_out()
    download = client.export(ns.revision_id, ns.fmt)
    if download.filename != EXPORT_FILENAMES[ns.fmt]:
        raise TransportError('filename_mismatch')
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=out, prefix='.mirum-export-', suffix='.tmp')
        with os.fdopen(fd, 'wb') as handle:
            handle.write(download.data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.lexists(target) or os.path.islink(out):
            raise _refuse_out()
        os.replace(tmp, target)
        tmp = None
    except OSError:
        raise _Fail(api.unavailable()) from None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    try:
        meta = api.ExportMetadata(revision_id=download.revision_id, fmt=download.fmt,
                                  filename=download.filename, content_type=download.content_type,
                                  size_bytes=len(download.data), sha256=download.sha256)
    except Exception:  # noqa: BLE001 - fixed code only
        raise TransportError('malformed_response') from None
    stdout.write(_dump(meta))
    return api.EXIT_OK


def _offline(ns, stdout) -> int:
    from . import offline  # lazy: pure language/planning code, still no store or network
    source = _read_source(ns.file)
    func = {'validate': offline.validate_source, 'format': offline.format_source,
            'plan': offline.plan_source}[ns.command]
    try:
        result = func(source, ns.source_id)
    except api.InterfaceFailure as failure:
        raise _Fail(failure.error) from None
    stdout.write(_dump(result))
    return api.EXIT_OK


def _online(ns, environ, transport, stdout) -> int:
    url = ns.url if ns.url else environ.get(URL_ENV)
    if not url:
        raise _Usage()
    token = environ.get(TOKEN_ENV) or None
    client = ProgramClient(url, token=token, transport=transport)
    spec = api.OPERATIONS_BY_NAME[ns.command.replace('-', '_')]
    if spec.name == 'export':
        return _export(client, ns, stdout)
    kwargs = {name: getattr(ns, name) for name, _ in _model_fields(spec)}
    if spec.body == 'mel':
        kwargs['source'] = _read_source(ns.file)
    result = getattr(client, spec.name)(**kwargs)
    stdout.write(_dump(result))
    revision = getattr(result, 'revision', None)
    if revision is not None:
        return api.result_exit_code(revision.result_kind)
    return api.EXIT_OK


def main(argv=None, *, transport=None, environ=None, stdout=None, stderr=None) -> int:
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    environ = environ if environ is not None else os.environ
    argv = list(sys.argv[1:] if argv is None else argv)

    def fail(err) -> int:
        stderr.write(err.model_dump_json() + '\n')
        return api.exit_code(err)

    try:
        ns = build_parser(stdout).parse_args(argv)
        if ns.offline and ns.command not in OFFLINE_OPERATIONS:
            raise _Usage()
        return _offline(ns, stdout) if ns.offline else _online(ns, environ, transport, stdout)
    except _Exit as done:
        return done.status
    except _Usage:
        return fail(api.invalid_request('invalid_field'))
    except _Fail as failure:
        return fail(failure.err)
    except ClientError as failure:
        return fail(failure.error)
    except TransportError as failure:
        stderr.write(json.dumps({'kind': 'transport_error', 'code': failure.code}) + '\n')
        return api.EXIT_TRANSPORT


if __name__ == '__main__':
    raise SystemExit(main())
