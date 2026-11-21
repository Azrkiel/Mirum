"""Deterministic grounding check for AI-proposed values (E05 fix loop, D3).

A value the model proposes may be labelled ``ai_proposed`` only if the prompt states it.
Pure stdlib; no provider, no I/O. Conservative by design: a value derived from a stated one
(a diameter from a radius, a sum, a ratio) is NOT grounded.

Numbers: an ``ai_proposed`` numeric value ``v`` for a field whose canonical unit is ``unit`` in
``dimension`` is grounded iff some number token ``t`` in the prompt and some unit ``u`` of the same
dimension in ``UNIT_TO_SI`` give ``c = t * UNIT_TO_SI[dimension][u] / UNIT_TO_SI[dimension][unit]``
with ``|c - v| <= REL_TOL * |v|`` (for ``v == 0``: grounded iff some ``t == 0``). The unit word need
not appear next to the number (no unit parsing); the factor set is fixed and explicit.

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


def number_tokens(prompt: str) -> tuple[float, ...]:
    """Every ``NUMBER`` token in ``prompt`` as a float, in order (duplicates kept)."""
    return tuple(float(match.group()) for match in NUMBER.finditer(prompt))


def is_grounded_number(value: float, prompt: str, dimension: str, unit: str) -> bool:
    """The number rule above. ``bool`` values are never grounded; an unknown dimension or unit raises ``KeyError``."""
    factors = UNIT_TO_SI[dimension]
    target = factors[unit]
    if isinstance(value, bool):
        return False
    tokens = number_tokens(prompt)
    if value == 0:
        return any(token == 0 for token in tokens)
    limit = REL_TOL * abs(value)
    return any(abs(token * factor / target - value) <= limit
               for token in tokens for factor in factors.values())


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
