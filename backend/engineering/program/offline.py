"""F08 pure MEL operations shared by the server facade and the CLI --offline path (D3).

validate_source, format_source and plan_source take raw source BYTES and a source_id label, run the
same language entry points the compiler uses (so invalid UTF-8, BOM, NUL and size diagnostics stay
reachable) and return an interface response model or raise InterfaceFailure(InterfaceError). Nothing
here writes, probes, opens a path or calls a provider.

PURE: imports only the stdlib, the language package, planning (offline, no probe), hashing and
interface. Never the store, sqlite3, a web framework, an HTTP client, a native kernel, jobs or
transactions (a subprocess test pins this).
"""
from __future__ import annotations

import hashlib

from engineering.language.diagnostics import MelError, Position, Span, make
from engineering.language.formatter import format_source as _format_source
from engineering.language.semantic import _valid_source_id, compile_source

from . import interface as api
from .planning import PlanRefusal, plan_program

__all__ = ['validate_source', 'format_source', 'plan_source']

_ZERO = Span(Position(0, 1, 1), Position(0, 1, 1))


def _fail(err) -> api.InterfaceFailure:
    return api.InterfaceFailure(err)


def _mel_failure(exc: MelError, source_id) -> api.InterfaceFailure:
    code = exc.diagnostic.code
    # A rejected label is never echoed back in a span.
    return _fail(api.from_mel(exc, source_id=None if code == 'MEL-S016' else source_id))


def _check_input(source, source_id) -> None:
    if type(source) is not bytes:
        raise _fail(api.invalid_request('invalid_field', field='source'))
    if source_id is not None and not _valid_source_id(source_id):
        raise _mel_failure(make('MEL-S016', _ZERO, field_name='source_id'), None)


def _compile(source: bytes, source_id):
    _check_input(source, source_id)
    try:
        return compile_source(source, source_id=source_id)
    except MelError as exc:
        raise _mel_failure(exc, source_id) from None


def validate_source(source: bytes, source_id: str | None = None) -> api.ValidateResult:
    compiled = _compile(source, source_id)
    return api.ValidateResult(
        source_id=source_id, source_sha256=compiled.source_sha256, semantic_sha256=compiled.semantic_sha256,
        language_version=compiled.language_version, compiler_version=compiled.compiler_version)


def format_source(source: bytes, source_id: str | None = None) -> api.FormatResult:
    _check_input(source, source_id)
    try:
        text = _format_source(source)
    except MelError as exc:
        raise _mel_failure(exc, source_id) from None
    encoded = text.encode('utf-8')
    return api.FormatResult(source_id=source_id, formatted_source=text,
                            formatted_sha256=hashlib.sha256(encoded).hexdigest(), unchanged=encoded == source)


def plan_source(source: bytes, source_id: str | None = None) -> api.PlanResult:
    compiled = _compile(source, source_id)
    plan = plan_program(compiled, mode='native')
    if isinstance(plan, PlanRefusal):
        raise _fail(api.from_plan_refusal(plan.outcome, plan.reasons, diagnostic_code=plan.diagnostic_code))
    return api.PlanResult(source_id=source_id, semantic_sha256=compiled.semantic_sha256, mode='native',
                          backend=plan.backend, candidate_count=len(plan.plans))
