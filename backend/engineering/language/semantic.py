"""MEL v0.1 semantic compiler: source -> ProgramIR (F05, LANGUAGE.md compiler stages 1-4).

Stages: (1) decode and parse; (2) pick the field schema by exact capability@version; (3) normalize,
type and envelope check every field; (4) canonical IR, built through the reviewed brief validation
and the F02 bridge, with a fail closed round trip (K32) before any hash is taken. Source text is
never evaluated and no field has a default; unknown fields fail.

Failures raise ``MelError`` with one MEL-S diagnostic (see ``diagnostics.SEMANTIC``). A cooperative
deadline (``deadline_s``, at most 2 seconds) is checked at stage boundaries and once per assignment
and per list element; it fails with MEL-S018 (category 'resource').
"""
from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass

from engineering.program.versions import MEL_COMPILER_VERSION, MEL_LANGUAGE_VERSION
from vendor.mirum_kernel.structural import BoundaryError, MODEL, resolve_region

from .diagnostics import MelError, Position, Span, make, snippet
from .parser import parse
from .syntax import ListValue, Program, Quantity, String

CAPABILITY_ID = 'rectangular_cantilever'
CAPABILITY_VERSION = '1.0.0'
MAX_DEADLINE_S = 2.0
_DESIGN_NAME = re.compile('[a-z][a-z0-9_]{0,63}')  # ProgramIR Identifier
_SOURCE_SEGMENT = re.compile('[A-Za-z0-9._-]+')

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
_REGION_FIELDS = {
    'load_region': ('string', ('requirements', 'load_region'), None),
    'support_region': ('string', ('requirements', 'support_region'), None),
}
# Frozen identity translation (LANGUAGE.md, F01): any other value is an error, never a default.
SOURCE_KINDS = frozenset({'illustrative', 'user_supplied', 'documented'})
# Requirement id (F02 bridge) -> MEL field, for spans.
REQUIREMENT_FIELDS = {
    'beam.length': 'length', 'beam.width': 'width', 'beam.dimensional_tolerance': 'dimensional_tolerance',
    'load.tip_force': 'tip_force', 'limit.max_tip_deflection': 'max_tip_deflection',
    'material.youngs_modulus': 'youngs_modulus', 'material.allowable_stress': 'allowable_stress',
    'material.reference': 'material_reference', 'material.source_kind': 'material_source_kind',
    'model.assumptions': 'assumptions', 'load.region': 'load_region', 'support.region': 'support_region',
}


@dataclass(frozen=True)
class CapabilitySchema:
    fields: dict
    backends: tuple[str, ...]
    exports: tuple[str, ...]  # the exact export list the bridge accepts
    max_candidates: int
    unique_candidates: bool = True


# Exact capability and version select the reviewed bridge.
_BAY_FIELDS = {
    **{name: FIELDS[name] for name in ('capability', 'capability_version', 'backend',
                                      'numerical_policy', 'resource_policy', 'exports')},
    'length': ('quantity', ('specification', 'length_mm'), 'length'),
    'width': ('quantity', ('specification', 'width_mm'), 'length'),
    'module_count': ('integer', ('specification', 'module_count'), None),
    'preferred_angle': ('quantity', ('specification', 'preferred_angle_rad'), 'angle'),
    'supplier': ('string', ('specification', 'supplier'), None),
}

_REGEN_FIELDS = {
    **{name: FIELDS[name] for name in ('capability', 'capability_version', 'backend',
                                      'numerical_policy', 'resource_policy', 'exports')},
    'preset': ('string', ('specification', 'preset'), None),
}

_EDITABLE_PLATE_FIELDS = {
    **{name: FIELDS[name] for name in ('capability', 'capability_version', 'backend',
                                      'numerical_policy', 'resource_policy', 'exports')},
    'width': ('quantity', ('specification', 'width_mm'), 'length'),
    'height': ('quantity', ('specification', 'height_mm'), 'length'),
    'thickness': ('quantity', ('specification', 'thickness_mm'), 'length'),
    'intent': ('string', ('specification', 'intent'), None),
    'datum_ref': ('string', ('specification', 'datum_ref'), None),
}

CAPABILITY_SCHEMAS = {
    ('editable_plate', '1.0.0'): CapabilitySchema(
        fields=_EDITABLE_PLATE_FIELDS, backends=('occt',), exports=('step',), max_candidates=1),
    ('regen_chamber', '1.0.0'): CapabilitySchema(
        fields=_REGEN_FIELDS, backends=('picogk',), exports=('stl',), max_candidates=1),
    ('equipment_bay', '1.0.0'): CapabilitySchema(
        fields=_BAY_FIELDS, backends=('occt',), exports=('step',), max_candidates=1),
    (CAPABILITY_ID, CAPABILITY_VERSION): CapabilitySchema(
        fields=FIELDS, backends=('occt',), exports=('step', 'glb'), max_candidates=4),
}
# Legacy payload path -> MEL field, for issues reported by the reviewed brief validation.
_PATH_FIELDS = {'.'.join(path): name for name, (_, path, _) in FIELDS.items() if path}


@dataclass(frozen=True)
class CompiledProgram:
    ir: object  # ProgramIR
    brief_content: object  # BriefContent, or None when the capability has none
    source_sha256: str
    semantic_sha256: str
    brief_sha256: str | None
    spans: tuple  # ((field_name, Span), ...) in source order, then ('design', block span)
    language_version: str = MEL_LANGUAGE_VERSION
    compiler_version: str = MEL_COMPILER_VERSION
    diagnostics: tuple = ()


_ZERO = Span(Position(0, 1, 1), Position(0, 1, 1))


def _valid_source_id(value) -> bool:
    if not isinstance(value, str) or not 0 < len(value) <= 128:
        return False
    return all(_SOURCE_SEGMENT.fullmatch(p) and p not in ('.', '..') for p in value.split('/'))


class _Deadline:
    def __init__(self, clock, limit: float):
        self.clock, self.limit = clock, limit
        self.start = clock()

    def check(self, span: Span, field_name: str | None = None) -> None:
        if self.clock() - self.start > self.limit:
            raise make('MEL-S018', span, f'compile deadline of {self.limit:g} s exceeded', field_name=field_name)


def _error(code: str, message: str, span: Span, field_name: str) -> MelError:
    return make(code, span, message, field_name=field_name)


def _quantity(name: str, value, dimension: str, span: Span) -> float:
    from engineering.program.errors import ProgramError
    from engineering.program.units import normalize
    if not isinstance(value, Quantity):
        raise _error('MEL-S008', f'{name} must be a number with a {dimension} unit', span, name)
    if value.unit is None:
        raise _error('MEL-S006', f'{name} needs a {dimension} unit', value.span, name)
    number = float(value.number)
    if not math.isfinite(number):  # the lexer already rejects this (MEL-L012)
        raise _error('MEL-S007', f'{name} is not finite', value.span, name)
    try:
        return normalize(number, value.unit, dimension, field=name).value
    except ProgramError as exc:
        code = {'IR005': 'MEL-S004', 'IR006': 'MEL-S005', 'IR007': 'MEL-S007'}.get(exc.code, 'MEL-S019')
        raise _error(code, f'{name}: {exc.message}', value.span, name) from None


def _string(name: str, value, span: Span) -> str:
    if not isinstance(value, String):
        raise _error('MEL-S008', f'{name} must be a string', span, name)
    # Code points written as escapes are still refused when they are controls (Cc), invisible format
    # characters (Cf, incl. every bidi control) or line separators (Zl, Zp).
    for ch in value.value:
        if unicodedata.category(ch) in ('Cc', 'Cf', 'Zl', 'Zp'):
            raise _error('MEL-S009', f'{name} contains a control or invisible format character '
                         f'(U+{ord(ch):04X})', value.span, name)
    return value.value


def _set(payload: dict, path: tuple[str, ...], value) -> None:
    node = payload
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _resolve_schema(program: Program, guard: _Deadline) -> CapabilitySchema:
    values = []
    for name in ('capability', 'capability_version'):
        a = program.get(name)
        if a is None:
            raise _error('MEL-S003', f'Missing required field {name!r} (no defaults)', program.design_span, name)
        values.append(_string(name, a.value, a.span))
    capability, version = values
    schema = CAPABILITY_SCHEMAS.get((capability, version))
    if schema is not None and schema.fields is _REGEN_FIELDS and program.get('preset') is None:
        raise _error('MEL-S011', 'PicoGK MEL requires the registered release preset',
                     program.get('capability').span, 'capability')
    if schema is None:
        known = {c for c, _ in CAPABILITY_SCHEMAS}
        name = 'capability_version' if capability in known else 'capability'
        raise _error('MEL-S011', f'unsupported capability {snippet(capability)!r} version {snippet(version)!r}; '
                     'see the MEL capability table', program.get(name).span, name)
    return schema


def _normalize(program: Program, schema: CapabilitySchema, guard: _Deadline) -> dict:
    fields = schema.fields
    if fields is FIELDS:
        assumptions = program.get('assumptions')
        if assumptions is not None and isinstance(assumptions.value, String) and assumptions.value.value == MODEL:
            fields = {**fields, **_REGION_FIELDS}
    for a in program.assignments:
        guard.check(a.span, a.name)
        if a.name not in fields:
            raise _error('MEL-S002', f'Unknown field {snippet(a.name)!r} for this capability',
                         a.name_span, a.name)
    missing = [name for name in fields if program.get(name) is None]
    if missing:
        # LANGUAGE.md (F01): a missing material_source_kind is MEL-S001, like any invalid value.
        if missing[0] in _REGION_FIELDS:
            raise _error('MEL-S023', f'boundary_missing: {missing[0]} is required',
                         program.design_span, missing[0])
        code = 'MEL-S001' if missing[0] == 'material_source_kind' else 'MEL-S003'
        raise _error(code, f'Missing required field {missing[0]!r} (no defaults)', program.design_span,
                     missing[0])
    if not _DESIGN_NAME.fullmatch(program.design):
        raise _error('MEL-S010', 'design name must be lower case: a letter, then letters, digits or _ '
                     '(at most 64)', program.design_span, 'design')
    payload: dict = {'schema_version': 'engineering_brief_v1'}
    for name, (kind, path, dimension) in fields.items():
        a = program.get(name)
        guard.check(a.span, name)
        value = a.value
        if kind == 'string':
            if name == 'material_source_kind' and not isinstance(value, String):
                raise _error('MEL-S001', 'material_source_kind must be a string literal', a.span, name)
            converted = _string(name, value, a.span)
            if name == 'material_source_kind' and converted not in SOURCE_KINDS:
                raise _error('MEL-S001', 'material_source_kind must be "illustrative", "user_supplied" or '
                             '"documented"', value.span, name)
            if name == 'backend' and converted not in schema.backends:
                raise _error('MEL-S012', f'backend {snippet(converted)!r} is not supported for this capability',
                             value.span, name)
        elif kind == 'integer':
            if not isinstance(value, Quantity) or value.unit is not None or not float(value.number).is_integer():
                raise _error('MEL-S008', f'{name} must be a whole number', a.span, name)
            converted = int(float(value.number))
        elif kind == 'quantity':
            converted = _quantity(name, value, dimension, a.span)
        elif kind == 'quantity_list':
            if not isinstance(value, ListValue):
                raise _error('MEL-S008', f'{name} must be a list', a.span, name)
            converted = []
            for item in value.items:
                guard.check(item.span, name)
                converted.append(_quantity(name, item, dimension, item.span))
            if not 1 <= len(converted) <= schema.max_candidates:
                raise _error('MEL-S014', f'{name} needs 1 to {schema.max_candidates} values', value.span, name)
            if schema.unique_candidates and len(set(converted)) != len(converted):
                raise _error('MEL-S014', f'{name} values must be unique', value.span, name)
        else:  # string_list
            if not isinstance(value, ListValue):
                raise _error('MEL-S008', f'{name} must be a list of strings', a.span, name)
            converted = []
            for item in value.items:
                guard.check(item.span, name)
                converted.append(_string(name, item, item.span))
            if name == 'exports' and tuple(converted) != schema.exports:
                raise _error('MEL-S013', f'exports must be exactly {list(schema.exports)}', value.span, name)
        if name == 'backend':
            payload.setdefault('__backend', converted)
        elif name == 'exports':
            payload.setdefault('__exports', tuple(converted))
        else:
            _set(payload, path, converted)
    if schema.fields in (_BAY_FIELDS, _REGEN_FIELDS, _EDITABLE_PLATE_FIELDS):
        payload['schema_version'] = 'engineering_generator_brief_v1'
    else:
        payload['requirements']['units'] = 'mm_N_MPa'
    return payload


def _span_dict(span: Span, source_id: str | None) -> dict:
    out = {'line': span.start.line, 'column': span.start.column, 'end_line': span.end.line,
           'end_column': span.end.column}
    if source_id is not None:
        out['source_id'] = source_id
    return out


def _issue_field(issue_field: str) -> str | None:
    if issue_field in _PATH_FIELDS:
        return _PATH_FIELDS[issue_field]
    if issue_field.split('.')[0] in ('candidate_heights_mm', 'height_mm'):  # per-height envelope rule
        return 'candidate_heights'
    return None


def _compile_program(program: Program, data: bytes, source_id: str | None, guard: _Deadline) -> CompiledProgram:
    """Stages 2-4 for an already parsed program. ``data`` is the exact source bytes."""
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.program.beam_bridge import brief_content_from_program, program_from_brief_content
    from engineering.program.errors import ProgramError
    from engineering.program.hashing import semantic_sha256, source_sha256
    from engineering.program.ir import ProgramProvenance, SourceSpan, program_from_dict
    from pydantic import ValidationError

    guard.check(program.design_span, 'design')
    schema = _resolve_schema(program, guard)
    guard.check(program.design_span, 'design')
    payload = _normalize(program, schema, guard)
    backend = payload.pop('__backend')
    exports = payload.pop('__exports')

    if schema.fields is _REGEN_FIELDS:
        from engineering.program.regen_bridge import PRESET, capability_reference, preset_content, program_for
        if payload['capability'] != capability_reference().model_dump():
            raise _error('MEL-S015', 'PicoGK capability or policy version mismatch', program.design_span, 'capability')
        preset = program.get('preset')
        if payload['specification']['preset'] != PRESET:
            raise _error('MEL-S015', 'use the registered PicoGK release reference', preset.span, 'preset')
        content = preset_content()
        source_hash = source_sha256(data)
        provenance = ProgramProvenance(kind='language', source_sha256=source_hash, source_id=source_id)
        ir = program_for(content, program.design, provenance,
                         preset_span=SourceSpan(**_span_dict(preset.span, source_id)),
                         source_span=SourceSpan(**_span_dict(program.block_span, source_id)))
        guard.check(program.design_span, 'design')
        spans = tuple((a.name, a.span) for a in program.assignments) + (('design', program.block_span),)
        return CompiledProgram(ir=ir, brief_content=content, source_sha256=source_hash,
                               semantic_sha256=semantic_sha256(ir), brief_sha256=content.brief_sha256,
                               spans=spans)

    if schema.fields is _BAY_FIELDS or schema.fields is _EDITABLE_PLATE_FIELDS:
        if schema.fields is _BAY_FIELDS:
            from engineering.program import bay_bridge as bridge
            label = 'equipment-bay'
        else:
            from engineering.program import editable_plate_bridge as bridge
            label = 'editable plate'
        if payload['capability'] != bridge.capability_reference().model_dump():
            raise _error('MEL-S015', f'{label} capability or policy version mismatch',
                         program.design_span, 'capability')
        try:
            content = bridge.content_for(payload['specification'])
        except ValidationError as exc:
            issue = exc.errors(include_input=False, include_url=False)[0]
            lookup = {path[-1]: name for name, (_, path, _) in schema.fields.items() if path}
            field = issue.get('ctx', {}).get('field')
            name = lookup.get(field or (issue['loc'][0] if issue['loc'] else 'preferred_angle'))
            code = {'dangling_reference': 'MEL-S020', 'invalid_dimension': 'MEL-S021'}.get(issue['type'], 'MEL-S015')
            a = program.get(name) if name else None
            raise _error(code, issue['msg'][:200], a.span if a else program.design_span,
                         name or 'program') from None
        source_hash = source_sha256(data)
        provenance = ProgramProvenance(kind='language', source_sha256=source_hash, source_id=source_id)
        bindings = {path[-1]: SourceSpan(**_span_dict(program.get(name).span, source_id))
                    for name, (_, path, _) in schema.fields.items() if path[:1] == ('specification',)}
        ir = bridge.program_for(content, program.design, provenance, bindings,
                         SourceSpan(**_span_dict(program.block_span, source_id)))
        if bridge.content_from_program(ir) != content:
            raise _error('MEL-S017', f'{label} brief did not round trip', program.design_span, 'program')
        guard.check(program.design_span, 'design')
        spans = tuple((a.name, a.span) for a in program.assignments) + (('design', program.block_span),)
        return CompiledProgram(ir=ir, brief_content=content, source_sha256=source_hash,
                               semantic_sha256=semantic_sha256(ir), brief_sha256=content.brief_sha256,
                               spans=spans)

    requirements = payload['requirements']
    if requirements.get('assumptions') == MODEL:
        for role in ('load', 'support'):
            name = role + '_region'
            try:
                resolve_region(role, requirements[name])
            except BoundaryError as exc:
                code = 'MEL-S022' if exc.diagnostic['code'] == 'boundary_ambiguous' else 'MEL-S023'
                raise _error(code, str(exc)[:200], program.get(name).span, name) from None
    content, issues = BriefService(MemoryBriefRepository()).validate(payload)
    guard.check(program.design_span, 'design')
    if content is None or issues:
        issue = issues[0] if issues else None
        name = _issue_field(issue.field) if issue else None
        a = program.get(name) if name else None
        code = {'candidate_heights': 'MEL-S014', 'material_source_kind': 'MEL-S001'}.get(name, 'MEL-S015')
        raise _error(code, f'Brief validation: {issue.reason if issue else "invalid brief"}',
                     a.span if a else program.design_span, name or (issue.field if issue else 'brief'))
    try:
        # K32: the language path meets the typed form path at one reviewed BriefContent. The round
        # trip must reproduce it exactly before any hash is taken. A placeholder provenance keeps
        # the source hash out of this check; the real one is set below.
        placeholder = ProgramProvenance(kind='language', source_sha256='0' * 64, source_id=source_id)
        ir = program_from_brief_content(content, provenance=placeholder, design=program.design,
                                        backend=backend, exports=exports)
        back = brief_content_from_program(ir, design=program.design)
        if back != content or back.brief_sha256 != content.brief_sha256:
            raise _error('MEL-S017', 'the reviewed round trip did not reproduce the validated brief',
                         program.design_span, 'program')
        guard.check(program.design_span, 'design')  # security review LOW-1 (PR #138): after the bridge
        data_ir = json.loads(ir.model_dump_json())
        data_ir['provenance']['source_sha256'] = source_sha256(data)
        for requirement in data_ir['requirements']['requirements']:
            mapped = REQUIREMENT_FIELDS.get(requirement['id'])
            if mapped is None:  # fail closed if the bridge ever emits an unmapped requirement
                raise _error('MEL-S019', 'the beam bridge produced an unmapped requirement',
                             program.design_span, 'program')
            requirement['span'] = _span_dict(program.get(mapped).span, source_id)
        data_ir['candidates']['parameters'][0]['span'] = _span_dict(program.get('candidate_heights').span,
                                                                    source_id)
        data_ir['span'] = _span_dict(program.block_span, source_id)
        final = program_from_dict(data_ir)
    except ProgramError as exc:
        name = {'design': 'design', 'execution.backend': 'backend'}.get(exc.field or '', None)
        if name is None and exc.field and exc.field.startswith('execution.exports'):
            name = 'exports'
        a = program.get(name) if name and name != 'design' else None
        raise _error('MEL-S019', exc.message, a.span if a else program.design_span,
                     name or exc.field or 'program') from None
    except ValidationError as exc:  # defensive: any record the bridge builds directly
        first = exc.errors(include_input=False, include_url=False)[0]
        raise _error('MEL-S019', f'invalid program: {first.get("msg", "invalid value")[:200]}',
                     program.design_span, 'program') from None
    guard.check(program.design_span, 'design')
    spans = tuple((a.name, a.span) for a in program.assignments) + (('design', program.block_span),)
    return CompiledProgram(ir=final, brief_content=content, source_sha256=data_ir['provenance']['source_sha256'],
                           semantic_sha256=semantic_sha256(final), brief_sha256=content.brief_sha256,
                           spans=spans)


def _check_deadline_arg(deadline_s) -> float:
    if (isinstance(deadline_s, bool) or not isinstance(deadline_s, (int, float))
            or not math.isfinite(deadline_s) or deadline_s <= 0 or deadline_s > MAX_DEADLINE_S):
        raise ValueError(f'deadline_s must be a finite number in (0, {MAX_DEADLINE_S}]')
    return float(deadline_s)


def compile_source(source: bytes | str, *, source_id: str | None = None, deadline_s: float = MAX_DEADLINE_S,
                   clock=time.monotonic) -> CompiledProgram:
    """Compile MEL v0.1 source (bytes or str) to ProgramIR. Raises MelError with one diagnostic.

    ``source_id`` is a label recorded in provenance and spans, never opened as a path: relative
    segments of [A-Za-z0-9._-], at most 128 characters, no '.' or '..' segment (else MEL-S016).
    ``deadline_s`` (0 < deadline_s <= 2.0) bounds the compile cooperatively (MEL-S018); anything
    else is caller misuse (ValueError)."""
    limit = _check_deadline_arg(deadline_s)
    guard = _Deadline(clock, limit)
    if source_id is not None and not _valid_source_id(source_id):
        raise _error('MEL-S016', 'source_id must be a relative label of [A-Za-z0-9._-] segments, at most '
                     '128 characters', _ZERO, 'source_id')
    from .lexer import decode
    decode(source)  # size, BOM and UTF-8 checks before any encoding below
    data = source.encode('utf-8') if isinstance(source, str) else bytes(source)
    program = parse(data)
    guard.check(program.design_span, 'design')  # security review LOW-1 (PR #138): parse counts too
    return _compile_program(program, data, source_id, guard)


def lower_program(program: Program, source: bytes, *, source_id: str | None = None):
    """ProgramIR for an already parsed program; ``source`` is the exact bytes it was parsed from."""
    if source_id is not None and not _valid_source_id(source_id):
        raise _error('MEL-S016', 'source_id must be a relative label of [A-Za-z0-9._-] segments, at most '
                     '128 characters', _ZERO, 'source_id')
    return _compile_program(program, bytes(source), source_id, _Deadline(time.monotonic, MAX_DEADLINE_S)).ir
