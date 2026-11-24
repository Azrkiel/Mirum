"""Dimension-checked unit normalization (F02, D3).

Canonical units: length mm, force N, pressure MPa (modulus shares it), mass kg,
time s, angle rad, absolute K, interval delta_K, thermal SI properties, and
dimensionless '1'. Decimal factors preserve equivalent authored quantities.
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Literal

from .errors import ProgramError

THERMAL_UNIT_CONTRACT = 'mirum_thermal_units_v1'
Dimension = Literal['length', 'force', 'pressure', 'mass', 'time', 'angle', 'dimensionless',
                    'temperature', 'temperature_difference', 'power', 'conductivity', 'thermal_expansion']

CANONICAL_UNIT: dict[str, str] = {
    'length': 'mm', 'force': 'N', 'pressure': 'MPa', 'mass': 'kg',
    'time': 's', 'angle': 'rad', 'dimensionless': '1',
    'temperature': 'K', 'temperature_difference': 'delta_K', 'power': 'W',
    'conductivity': 'W_per_m_K', 'thermal_expansion': 'per_K',
}

# unit -> (dimension, factor to canonical as Decimal string)
UNITS: dict[str, tuple[str, str]] = {
    'mm': ('length', '1'), 'cm': ('length', '10'), 'm': ('length', '1000'), 'in': ('length', '25.4'),
    'um': ('length', '0.001'),
    'N': ('force', '1'), 'kN': ('force', '1000'),
    'Pa': ('pressure', '0.000001'), 'MPa': ('pressure', '1'), 'GPa': ('pressure', '1000'),
    'kg': ('mass', '1'), 'g': ('mass', '0.001'),
    's': ('time', '1'), 'ms': ('time', '0.001'), 'min': ('time', '60'),
    'rad': ('angle', '1'), 'deg': ('angle', 'pi/180'),
    '1': ('dimensionless', '1'),
    'K': ('temperature', '1'), 'degC': ('temperature', '1'),
    'delta_K': ('temperature_difference', '1'), 'delta_degC': ('temperature_difference', '1'),
    'W': ('power', '1'), 'kW': ('power', '1000'),
    'W_per_m_K': ('conductivity', '1'), 'per_K': ('thermal_expansion', '1'),
    'um_per_m_K': ('thermal_expansion', '0.000001'),
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
        offset = Decimal('273.15') if unit == 'degC' else Decimal(0)
        result = float(Decimal(str(value)) * Decimal(factor) + offset)
    if not math.isfinite(result):
        raise ProgramError('IR007', 'normalized quantity is not finite', field, span)
    if dim == 'temperature' and result < 0:
        raise ProgramError('IR007', 'absolute temperature is below absolute zero', field, span)
    if result == 0:
        result = 0.0
    return QuantityValue(kind='quantity', dimension=expected, value=result, unit=CANONICAL_UNIT[expected])
