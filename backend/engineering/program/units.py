"""Dimension-checked unit normalization (F02, D3).

Canonical units: length mm, force N, pressure MPa (modulus shares it), mass kg,
time s, angle rad, dimensionless '1'. Factors are Decimal strings; deg uses the
float factor math.pi / 180 (computed as float(math.pi) / 180). No mass->force
conversion exists.
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Literal

from .errors import ProgramError

Dimension = Literal['length', 'force', 'pressure', 'mass', 'time', 'angle', 'dimensionless']

CANONICAL_UNIT: dict[str, str] = {
    'length': 'mm', 'force': 'N', 'pressure': 'MPa', 'mass': 'kg',
    'time': 's', 'angle': 'rad', 'dimensionless': '1',
}

# unit -> (dimension, factor to canonical as Decimal string)
UNITS: dict[str, tuple[str, str]] = {
    'mm': ('length', '1'), 'cm': ('length', '10'), 'm': ('length', '1000'), 'in': ('length', '25.4'),
    'N': ('force', '1'), 'kN': ('force', '1000'),
    'Pa': ('pressure', '0.000001'), 'MPa': ('pressure', '1'), 'GPa': ('pressure', '1000'),
    'kg': ('mass', '1'), 'g': ('mass', '0.001'),
    's': ('time', '1'), 'ms': ('time', '0.001'), 'min': ('time', '60'),
    'rad': ('angle', '1'), 'deg': ('angle', 'pi/180'),
    '1': ('dimensionless', '1'),
}

_DEG_FACTOR = float(math.pi) / 180


def normalize(value: float, unit: str, expected: Dimension, *, field: str | None = None, span=None):
    from .ir import QuantityValue

    if unit not in UNITS:
        raise ProgramError('IR005', f'unknown unit {unit!r}', field, span)
    dim, factor = UNITS[unit]
    if dim != expected:
        raise ProgramError('IR006', f'unit {unit!r} is a {dim} unit, expected {expected}', field, span)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProgramError('IR010', 'quantity value must be a number', field, span)
    value = float(value)
    if not math.isfinite(value):
        raise ProgramError('IR007', 'quantity value is not finite', field, span)
    if unit == 'deg':
        result = value * _DEG_FACTOR
    else:
        result = float(Decimal(str(value)) * Decimal(factor))
    if not math.isfinite(result):
        raise ProgramError('IR007', 'normalized quantity is not finite', field, span)
    if result == 0:
        result = 0.0
    return QuantityValue(kind='quantity', dimension=expected, value=result, unit=CANONICAL_UNIT[expected])
