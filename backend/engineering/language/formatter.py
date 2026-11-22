"""MEL v0.1 canonical formatter (F04).

Style: header line, `design <name> {`, one assignment per line indented two spaces as `name = value;`,
lists inline as `[a, b]`, a quantity as its number text exactly as written plus one space and the
unit, strings re-escaped as ASCII JSON (every non-ASCII, control and DEL character becomes \\uXXXX,
so the output always lexes), LF line endings, one trailing newline. Comments are dropped: the
formatted text is a new source with its own source hash; its syntax tree equals the original's.
"""
from __future__ import annotations

import json

from .parser import parse
from .syntax import ListValue, Program, Quantity, String, Value


def _value(value: Value) -> str:
    if isinstance(value, Quantity):
        return value.number if value.unit is None else f'{value.number} {value.unit}'
    if isinstance(value, String):
        return json.dumps(value.value, ensure_ascii=True)
    if isinstance(value, ListValue):
        return '[' + ', '.join(_value(item) for item in value.items) + ']'
    raise TypeError('unknown MEL value')


def format_program(program: Program) -> str:
    lines = [f'language mirum {json.dumps(program.version, ensure_ascii=True)};',
             f'design {program.design} {{']
    lines += [f'  {a.name} = {_value(a.value)};' for a in program.assignments]
    lines.append('}')
    return '\n'.join(lines) + '\n'


def format_source(source: bytes | str) -> str:
    """Parse then format (raises the first MelError of the source)."""
    return format_program(parse(source))
