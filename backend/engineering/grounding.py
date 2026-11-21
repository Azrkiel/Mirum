"""Deterministic grounding check for AI-proposed values (E05 fix loop, D3).

A value the model proposes may be labelled ``ai_proposed`` only if the prompt states it.
Pure stdlib; no provider, no I/O. Conservative by design: a value derived from a stated one
(a diameter from a radius, a sum, a ratio) is NOT grounded.

Numbers: the prompt is parsed into (number, unit) pairs. A ``NUMBER`` token followed (after at most
one space or hyphen) by a unit alias from ``UNIT_ALIASES`` is a united token; the alias must end at a
word boundary (``mmHg`` and ``masts`` are not units, so the number before them is bare). An
``ai_proposed`` value ``v`` for a field whose canonical unit is ``unit`` in ``dimension`` is grounded
iff some token fits: a united token of the SAME dimension converts with its own unit's factor only,
``c = t * UNIT_TO_SI[dimension][u] / UNIT_TO_SI[dimension][unit]``; a bare token (no recognized unit of
any dimension) must equal ``v`` directly in the field's canonical unit; a token whose unit is of a
DIFFERENT dimension never grounds the field. A match is ``|c - v| <= REL_TOL * |v|``. Dimensionless
fields ground from bare tokens only. Zero: ``v == 0`` is grounded iff some usable token is exactly
zero (tokens are parsed with ``Decimal``, so ``1e-400``, which underflows to 0.0, is not zero). Tokens
whose float is inf, or underflows from a nonzero value, are ignored. Symbol aliases are
case-sensitive (``mm`` vs ``M``); word aliases are not.

Number tokens: ``NUMBER`` matches in the prompt (optional sign, digits, optional decimal part,
optional exponent, e.g. ``-3``, ``0.025``, ``1.5e3``, ``.5``). A digit glued to letters still counts
(``CO2`` yields ``2``); the extra tokens can only ground, never invent, a value the prompt shows.

Choices (enums and text): a value is grounded iff one of its fixed synonyms occurs in the prompt,
case-insensitive, at word boundaries, after both are normalized (lowercase; every run of
``-``, ``_`` or whitespace becomes one space). A list value is grounded iff it is non-empty and every
element is.
Booleans and ``None`` are never grounded (``None`` means unset; callers skip it).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any, Mapping

REL_TOL = 0.005
NUMBER = re.compile(r'[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?')

# Factor of each unit to the dimension's SI unit. The PR body quotes this table.
UNIT_TO_SI: dict[str, dict[str, float]] = {
    'length': {'m': 1.0, 'mm': 1e-3, 'cm': 1e-2, 'km': 1e3, 'in': 0.0254, 'ft': 0.3048},
    'pressure': {'Pa': 1.0, 'kPa': 1e3, 'MPa': 1e6, 'bar': 1e5, 'psi': 6894.757293168, 'atm': 101325.0},
    'force': {'N': 1.0, 'kN': 1e3, 'lbf': 4.4482216152605},
    'mass': {'kg': 1.0, 'g': 1e-3, 'lb': 0.45359237, 'oz': 0.028349523125},
    'time': {'s': 1.0, 'ms': 1e-3, 'min': 60.0, 'h': 3600.0},
    'angle': {'rad': 1.0, 'deg': 0.017453292519943295},
    'velocity': {'m/s': 1.0, 'km/h': 1.0 / 3.6, 'mph': 0.44704, 'ft/s': 0.3048, 'kn': 0.514444444444},
    # Temperatures: Celsius only (no offset conversions). Fahrenheit is its own dimension so a
    # Fahrenheit number never grounds a Celsius field and is never read as a bare number.
    'temperature': {'C': 1.0},
    'temperature_f': {'F': 1.0},
    'dimensionless': {'1': 1.0},
}


@dataclass(frozen=True)
class NumberField:
    dimension: str    # a key of UNIT_TO_SI
    unit: str         # the field's canonical unit, a key of UNIT_TO_SI[dimension]


@dataclass(frozen=True)
class ChoiceField:
    synonyms: Mapping[str, tuple[str, ...]]   # value -> fixed synonyms (the value itself is not implied)


def _normalize(text: str) -> str:
    return re.sub(r'[-_\s]+', ' ', text.lower()).strip()


# alias -> (dimension, unit key). Symbols are case-sensitive (m vs M, mm vs Mm); words are not.
_SYMBOLS: dict[str, tuple[str, str]] = {
    'mm': ('length', 'mm'), 'cm': ('length', 'cm'), 'm': ('length', 'm'), 'km': ('length', 'km'),
    'Pa': ('pressure', 'Pa'), 'kPa': ('pressure', 'kPa'), 'MPa': ('pressure', 'MPa'),
    'N': ('force', 'N'), 'kN': ('force', 'kN'),
    'kg': ('mass', 'kg'), 'g': ('mass', 'g'),
    's': ('time', 's'), 'ms': ('time', 'ms'), 'h': ('time', 'h'),
    'm/s': ('velocity', 'm/s'), 'km/h': ('velocity', 'km/h'),
    'C': ('temperature', 'C'), 'F': ('temperature_f', 'F'),
}
_WORDS: dict[str, tuple[str, str]] = {}
for _dim, _unit, _names in (
    ('length', 'mm', ('millimeter', 'millimeters', 'millimetre', 'millimetres')),
    ('length', 'cm', ('centimeter', 'centimeters', 'centimetre', 'centimetres')),
    ('length', 'm', ('meter', 'meters', 'metre', 'metres')),
    ('length', 'km', ('kilometer', 'kilometers', 'kilometre', 'kilometres')),
    ('length', 'in', ('in', 'inch', 'inches')),
    ('length', 'ft', ('ft', 'foot', 'feet')),
    ('pressure', 'Pa', ('pascal', 'pascals')),
    ('pressure', 'bar', ('bar',)),
    ('pressure', 'psi', ('psi',)),
    ('pressure', 'atm', ('atm',)),
    ('force', 'N', ('newton', 'newtons')),
    ('force', 'lbf', ('lbf',)),
    ('mass', 'kg', ('kilogram', 'kilograms')),
    ('mass', 'g', ('gram', 'grams')),
    ('mass', 'lb', ('lb', 'lbs', 'pound', 'pounds')),
    ('mass', 'oz', ('oz', 'ounce', 'ounces')),
    ('time', 's', ('sec', 'second', 'seconds')),
    ('time', 'min', ('min', 'minute', 'minutes')),
    ('time', 'h', ('hr', 'hour', 'hours')),
    ('angle', 'deg', ('deg', 'degree', 'degrees')),
    ('angle', 'rad', ('rad', 'radian', 'radians')),
    ('velocity', 'km/h', ('kph',)),
    ('velocity', 'mph', ('mph',)),
    ('velocity', 'ft/s', ('ft/s',)),
    ('velocity', 'kn', ('kn', 'knot', 'knots')),
    ('temperature', 'C', ('celsius', 'degc', 'deg c', 'degree c', 'degrees c', 'degree celsius',
                          'degrees celsius')),
    ('temperature_f', 'F', ('fahrenheit', 'degf', 'deg f', 'degree f', 'degrees f', 'degree fahrenheit',
                            'degrees fahrenheit')),
):
    for _name in _names:
        _WORDS[_name] = (_dim, _unit)
# Mark symbols (not word characters): matched without a trailing word boundary.
_PUNCT: dict[str, tuple[str, str]] = {'"': ('length', 'in'), "'": ('length', 'ft'), '°': ('angle', 'deg'),
                                      '°C': ('temperature', 'C'), '°F': ('temperature_f', 'F')}
UNIT_ALIASES: dict[str, tuple[str, str]] = {**_SYMBOLS, **_WORDS, **_PUNCT}
assert all(unit in UNIT_TO_SI[dim] for dim, unit in UNIT_ALIASES.values())
_ALIASES = tuple(sorted(
    [(a, r, True) for a, r in _SYMBOLS.items()] + [(a, r, False) for a, r in _WORDS.items()]
    + [(a, r, True) for a, r in _PUNCT.items()], key=lambda item: -len(item[0])))


def _unit_after(prompt: str, end: int) -> tuple[str, str] | None:
    """The (dimension, unit) of the alias right after a number ending at ``end``, or ``None``."""
    pos = end
    if pos < len(prompt) and prompt[pos] in ' -':
        pos += 1
    for alias, ref, case_sensitive in _ALIASES:
        chunk = prompt[pos:pos + len(alias)]
        if (chunk if case_sensitive else chunk.lower()) != alias:
            continue
        if alias in _PUNCT:
            return ref
        following = prompt[pos + len(alias):pos + len(alias) + 1]
        if not (following.isalnum() or following == '_'):
            return ref
    return None


def _parse(prompt: str) -> tuple[tuple[float, tuple[str, str] | None], ...]:
    out = []
    for match in NUMBER.finditer(prompt):
        try:
            exact = Decimal(match.group())
            value = float(exact)
        except (InvalidOperation, OverflowError, ValueError):
            continue
        if math.isinf(value) or math.isnan(value) or (value == 0 and exact != 0):
            continue
        out.append((value, _unit_after(prompt, match.end())))
    return tuple(out)


def number_tokens(prompt: str) -> tuple[float, ...]:
    """Every usable ``NUMBER`` token in ``prompt`` as a float, in order (duplicates kept)."""
    return tuple(value for value, _ in _parse(prompt))


def is_grounded_number(value: float, prompt: str, dimension: str, unit: str) -> bool:
    """The number rule above. ``bool`` values are never grounded; an unknown dimension or unit raises ``KeyError``."""
    factors = UNIT_TO_SI[dimension]
    target = factors[unit]
    if isinstance(value, bool):
        return False
    limit = REL_TOL * abs(value)
    for token, ref in _parse(prompt):
        if ref is None:
            candidate = token
        elif ref[0] == dimension and dimension != 'dimensionless':
            candidate = token * factors[ref[1]] / target
        else:
            continue
        if value == 0:
            if token == 0:
                return True
        elif abs(candidate - value) <= limit:
            return True
    return False


def is_grounded_choice(value: Any, prompt: str, synonyms: Mapping[str, tuple[str, ...]]) -> bool:
    """The choice rule above. A value missing from ``synonyms`` is not grounded. Lists: every element."""
    if isinstance(value, (list, tuple)):
        return bool(value) and all(is_grounded_choice(item, prompt, synonyms) for item in value)
    if not isinstance(value, str) or value not in synonyms:
        return False
    text = _normalize(prompt)
    for synonym in synonyms[value]:
        words = _normalize(synonym)
        if words and re.search(r'(?<!\w)' + re.escape(words) + r'(?!\w)', text):
            return True
    return False


def ungrounded_fields(values: Mapping[str, Any], prompt: str,
                      kinds: Mapping[str, NumberField | ChoiceField]) -> tuple[str, ...]:
    """Names in ``values`` (sorted) whose non-``None`` value is not grounded in ``prompt``.

    A name with no entry in ``kinds`` is ungrounded (conservative). ``None`` values are skipped.
    """
    bad = []
    for name, value in values.items():
        if value is None:
            continue
        kind = kinds.get(name)
        if isinstance(kind, NumberField):
            grounded = (isinstance(value, (int, float)) and
                        is_grounded_number(value, prompt, kind.dimension, kind.unit))
        elif isinstance(kind, ChoiceField):
            grounded = is_grounded_choice(value, prompt, kind.synonyms)
        else:
            grounded = False
        if not grounded:
            bad.append(name)
    return tuple(sorted(bad))
