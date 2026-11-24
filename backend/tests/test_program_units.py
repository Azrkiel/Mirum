"""F02 T1: dimension-checked unit normalization."""
from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from engineering.program.errors import ProgramError
from engineering.program.ir import QuantityValue
from engineering.program.units import CANONICAL_UNIT, normalize

CASES = [
    # (unit, dimension, input, expected canonical value)
    ('mm', 'length', 2.0, 2.0),
    ('cm', 'length', 2.5, 25.0),
    ('m', 'length', 0.5, 500.0),
    ('in', 'length', 2.0, 50.8),
    ('N', 'force', 3.0, 3.0),
    ('kN', 'force', 1.5, 1500.0),
    ('Pa', 'pressure', 2_000_000.0, 2.0),
    ('MPa', 'pressure', 210.0, 210.0),
    ('GPa', 'pressure', 0.2, 200.0),
    ('kg', 'mass', 4.0, 4.0),
    ('g', 'mass', 500.0, 0.5),
    ('s', 'time', 2.0, 2.0),
    ('ms', 'time', 250.0, 0.25),
    ('min', 'time', 2.0, 120.0),
    ('rad', 'angle', 1.5, 1.5),
    ('deg', 'angle', 180.0, math.pi),
    ('1', 'dimensionless', 7.0, 7.0),
]


@pytest.mark.parametrize('unit,dim,value,expected', CASES)
def test_conversion_to_canonical(unit, dim, value, expected):
    q = normalize(value, unit, dim)
    assert isinstance(q, QuantityValue)
    assert q.kind == 'quantity'
    assert q.dimension == dim
    assert q.unit == CANONICAL_UNIT[dim]
    assert q.value == pytest.approx(expected, rel=1e-12)


def test_canonical_units_table():
    assert CANONICAL_UNIT == {
        'length': 'mm', 'force': 'N', 'pressure': 'MPa', 'mass': 'kg',
        'time': 's', 'angle': 'rad', 'dimensionless': '1',
        'temperature': 'K', 'temperature_difference': 'delta_K', 'power': 'W',
        'conductivity': 'W_per_m_K', 'thermal_expansion': 'per_K',
    }


def test_exact_decimal_arithmetic():
    assert normalize(0.1, 'm', 'length').value == 100.0
    assert normalize(25.4, 'mm', 'length').value == 25.4
    assert normalize(1.0, 'in', 'length').value == 25.4


def test_deg_uses_float_pi_over_180():
    assert normalize(90.0, 'deg', 'angle').value == 90.0 * (math.pi / 180)


def test_unknown_unit_ir005():
    with pytest.raises(ProgramError) as ei:
        normalize(1.0, 'furlong', 'length', field='a.b')
    assert ei.value.code == 'IR005'
    assert ei.value.field == 'a.b'


def test_no_mass_to_force():
    with pytest.raises(ProgramError) as ei:
        normalize(1.0, 'kg', 'force')
    assert ei.value.code == 'IR006'


def test_wrong_dimension_ir006():
    with pytest.raises(ProgramError) as ei:
        normalize(1.0, 'N', 'length', field='x')
    assert ei.value.code == 'IR006'
    assert ei.value.field == 'x'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_input_ir007(bad):
    with pytest.raises(ProgramError) as ei:
        normalize(bad, 'mm', 'length')
    assert ei.value.code == 'IR007'


def test_nonfinite_result_ir007():
    with pytest.raises(ProgramError) as ei:
        normalize(1e308, 'm', 'length')
    assert ei.value.code == 'IR007'


def test_negative_zero_becomes_zero():
    for unit, dim in (('mm', 'length'), ('deg', 'angle'), ('kN', 'force')):
        v = normalize(-0.0, unit, dim).value
        assert v == 0.0
        assert math.copysign(1.0, v) == 1.0


def test_ranges_not_checked():
    assert normalize(-5.0, 'mm', 'length').value == -5.0


def test_quantity_value_rejects_noncanonical_unit():
    with pytest.raises(ValidationError) as ei:
        QuantityValue(kind='quantity', dimension='length', value=1.0, unit='m')
    err = ei.value.errors()[0]
    assert isinstance(err['ctx']['error'], ProgramError)
    assert err['ctx']['error'].code == 'IR006'


def test_quantity_value_rejects_nonfinite():
    with pytest.raises(ValidationError):
        QuantityValue(kind='quantity', dimension='length', value=float('nan'), unit='mm')


def test_quantity_value_negative_zero_normalized():
    q = QuantityValue(kind='quantity', dimension='length', value=-0.0, unit='mm')
    assert math.copysign(1.0, q.value) == 1.0
