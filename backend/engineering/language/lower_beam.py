"""Beam-only lowering of a MEL v0.1 program to ProgramIR (F04, provisional until F05).

The parsed program is translated field by field into the same payload the typed form sends
(``engineering_brief_v1``), validated by the existing brief validation (``BriefService.validate``),
and turned into ProgramIR by the F02 beam bridge (``program_from_brief_content``). Language and
form input therefore meet at one reviewed ``BriefContent`` before any hash is taken (K32).
Source spans are attached afterwards; they are not part of the semantic hash.

Semantic failures keep the F02 IR codes (IR002 unknown field, IR005 unknown unit, IR006 unit
mismatch, IR010 invalid or missing value); F05 owns the final MEL-S code table. No field has a
default.
"""
from __future__ import annotations

import json
import math
import re

from .diagnostics import MelError, Span, make, snippet
from .parser import parse
from .syntax import Assignment, ListValue, Program, Quantity, String

CAPABILITY_ID = 'rectangular_cantilever'
CAPABILITY_VERSION = '1.0.0'
_DESIGN_NAME = re.compile('[a-z][a-z0-9_]{0,63}')  # ProgramIR Identifier

# MEL field -> (kind, legacy payload path, dimension for quantities). Order = payload order.
FIELDS: dict[str, tuple[str, tuple[str, ...], str | None]] = {
    'capability': ('string', ('capability', 'id'), None),
    'capability_version': ('string', ('capability', 'version'), None),
    'backend': ('string', (), None),
    'numerical_policy': ('string', ('capability', 'numerical_policy_version'), None),
    'resource_policy': ('string', ('capability', 'resource_policy_version'), None),
    'length': ('quantity', ('requirements', 'length_mm'), 'length'),
    'width': ('quantity', ('requirements', 'width_mm'), 'length'),
    'dimensional_tolerance': ('quantity', ('requirements', 'dimensional_tolerance_mm'), 'length'),
    'tip_force': ('quantity', ('requirements', 'tip_force_n'), 'force'),
    'max_tip_deflection': ('quantity', ('requirements', 'max_tip_deflection_mm'), 'length'),
    'youngs_modulus': ('quantity', ('requirements', 'material', 'youngs_modulus_mpa'), 'pressure'),
    'allowable_stress': ('quantity', ('requirements', 'material', 'allowable_stress_mpa'), 'pressure'),
    'material_reference': ('string', ('requirements', 'material', 'reference'), None),
    'material_source_kind': ('string', ('requirements', 'material', 'source_kind'), None),
    'assumptions': ('string', ('requirements', 'assumptions'), None),
    'mode': ('string', ('mode',), None),
    'candidate_heights': ('quantity_list', ('candidate_heights_mm',), 'length'),
    'exports': ('string_list', (), None),
}
# Frozen identity translation (LANGUAGE.md, F01): any other value is an error, never a default.
SOURCE_KINDS = frozenset({'illustrative', 'user_supplied', 'documented'})
# Requirement id (F02 bridge) -> MEL field, for spans.
REQUIREMENT_FIELDS = {
    'beam.length': 'length', 'beam.width': 'width', 'beam.dimensional_tolerance': 'dimensional_tolerance',
    'load.tip_force': 'tip_force', 'limit.max_tip_deflection': 'max_tip_deflection',
    'material.youngs_modulus': 'youngs_modulus', 'material.allowable_stress': 'allowable_stress',
    'material.reference': 'material_reference', 'material.source_kind': 'material_source_kind',
    'model.assumptions': 'assumptions',
}
# Legacy payload path -> MEL field, for issues reported by the existing brief validation.
_PATH_FIELDS = {'.'.join(path): name for name, (_, path, _) in FIELDS.items() if path}


def _error(code: str, message: str, span: Span, field_name: str) -> MelError:
    return make(code, span, message, field_name=field_name)


def _quantity(name: str, value, dimension: str, span: Span) -> float:
    from engineering.program.errors import ProgramError
    from engineering.program.units import normalize
    if not isinstance(value, Quantity):
        raise _error('IR010', f'{name} must be a number with a {dimension} unit', span, name)
    if value.unit is None:
        raise _error('IR006', f'{name} needs a {dimension} unit', value.span, name)
    number = float(value.number)
    if not math.isfinite(number):  # the lexer already rejects this (MEL-L012)
        raise _error('IR007', f'{name} is not finite', value.span, name)
    try:
        return normalize(number, value.unit, dimension, field=name).value
    except ProgramError as exc:
        raise _error(exc.code, f'{name}: {exc.message}', value.span, name) from None


def _string(name: str, value, span: Span) -> str:
    if not isinstance(value, String):
        raise _error('IR010', f'{name} must be a string', span, name)
    return value.value


def _set(payload: dict, path: tuple[str, ...], value) -> None:
    node = payload
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _payload(program: Program) -> tuple[dict, str, tuple[str, ...]]:
    known = set(FIELDS)
    for a in program.assignments:
        if a.name not in known:
            raise _error('IR002', f'Unknown field {snippet(a.name)!r} for {CAPABILITY_ID} {CAPABILITY_VERSION}',
                         a.name_span, a.name)
    missing = [name for name in FIELDS if program.get(name) is None]
    if missing:
        raise _error('IR010', f'Missing required field {missing[0]!r} (no defaults)', program.design_span,
                     missing[0])
    if not _DESIGN_NAME.fullmatch(program.design):
        raise _error('IR010', 'design name must be lower case: a letter, then letters, digits or _ '
                     '(at most 64)', program.design_span, 'design')
    for name, expected in (('capability', CAPABILITY_ID), ('capability_version', CAPABILITY_VERSION)):
        a = program.get(name)
        if not (isinstance(a.value, String) and a.value.value == expected):
            raise _error('IR010', f'this lowering supports only {CAPABILITY_ID} {CAPABILITY_VERSION}; '
                         f'{name} must be "{expected}"', a.span, name)
    payload: dict = {'schema_version': 'engineering_brief_v1'}
    backend, exports = 'occt', ()
    for name, (kind, path, dimension) in FIELDS.items():
        a: Assignment = program.get(name)
        value = a.value
        if kind == 'string':
            converted = _string(name, value, a.span)
            if name == 'material_source_kind' and converted not in SOURCE_KINDS:
                raise _error('IR010', 'material_source_kind must be "illustrative", "user_supplied" or '
                             '"documented"', value.span, name)
        elif kind == 'quantity':
            converted = _quantity(name, value, dimension, a.span)
        elif kind == 'quantity_list':
            if not isinstance(value, ListValue):
                raise _error('IR010', f'{name} must be a list', a.span, name)
            converted = [_quantity(name, item, dimension, item.span) for item in value.items]
        else:  # string_list
            if not isinstance(value, ListValue):
                raise _error('IR010', f'{name} must be a list of strings', a.span, name)
            converted = [_string(name, item, item.span) for item in value.items]
        if name == 'backend':
            backend = converted
        elif name == 'exports':
            exports = tuple(converted)
        else:
            _set(payload, path, converted)
    payload['requirements']['units'] = 'mm_N_MPa'
    return payload, backend, exports


def _span_dict(span: Span, source_id: str | None) -> dict:
    out = {'line': span.start.line, 'column': span.start.column, 'end_line': span.end.line,
           'end_column': span.end.column}
    if source_id is not None:
        out['source_id'] = source_id
    return out


def lower_program(program: Program, source: bytes, *, source_id: str | None = None):
    """Lower a parsed beam program. ``source`` is the exact bytes the program was parsed from."""
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.program.beam_bridge import brief_content_from_program, program_from_brief_content
    from engineering.program.errors import ProgramError
    from engineering.program.hashing import source_sha256
    from engineering.program.ir import ProgramProvenance, program_from_dict
    from pydantic import ValidationError

    payload, backend, exports = _payload(program)
    content, issues = BriefService(MemoryBriefRepository()).validate(payload)
    if content is None or issues:
        issue = issues[0] if issues else None
        name = _PATH_FIELDS.get(issue.field if issue else '', None)
        a = program.get(name) if name else None
        raise _error('IR010', f'Brief validation: {issue.reason if issue else "invalid brief"}',
                     a.span if a else program.design_span, name or (issue.field if issue else 'brief'))
    try:
        provenance = ProgramProvenance(kind='language', source_sha256=source_sha256(source),
                                       source_id=source_id)
        ir = program_from_brief_content(content, provenance=provenance, design=program.design,
                                        backend=backend, exports=exports)
        # K32: back through the bridge; it rejects (never drops) what the reviewed brief cannot
        # carry, e.g. a backend other than occt or exports other than step and glb.
        brief_content_from_program(ir, design=program.design)
        data = json.loads(ir.model_dump_json())
        for requirement in data['requirements']['requirements']:
            a = program.get(REQUIREMENT_FIELDS[requirement['id']])
            requirement['span'] = _span_dict(a.span, source_id)
        heights = program.get('candidate_heights')
        data['candidates']['parameters'][0]['span'] = _span_dict(heights.span, source_id)
        data['span'] = _span_dict(program.block_span, source_id)
        return program_from_dict(data)
    except ProgramError as exc:
        name = {'design': 'design', 'execution.backend': 'backend'}.get(exc.field or '', None)
        if name is None and exc.field and exc.field.startswith('execution.exports'):
            name = 'exports'
        a = program.get(name) if name and name != 'design' else None
        span = a.span if a else program.design_span
        raise _error(exc.code, exc.message, span, name or exc.field or 'program') from None
    except ValidationError as exc:  # defensive: any record the bridge builds directly
        first = exc.errors(include_input=False, include_url=False)[0]
        raise _error('IR010', f'invalid program: {first.get("msg", "invalid value")[:200]}',
                     program.design_span, 'program') from None


def compile_beam(source: bytes | str, *, source_id: str | None = None):
    """Parse and lower MEL source (bytes or str) for the beam capability."""
    from .lexer import decode
    decode(source)  # size, BOM and UTF-8 checks before any encoding below
    data = source.encode('utf-8') if isinstance(source, str) else bytes(source)
    return lower_program(parse(data), data, source_id=source_id)
