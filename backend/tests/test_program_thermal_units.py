import pytest
from pydantic import ValidationError

from engineering.program.errors import ProgramError
from engineering.program.ir import QuantityValue
from engineering.program.units import normalize


def test_thermal_units_separate_absolute_and_interval_temperatures():
    assert normalize(0, 'degC', 'temperature') == normalize(273.15, 'K', 'temperature')
    assert normalize(1, 'delta_degC', 'temperature_difference') == normalize(1, 'delta_K', 'temperature_difference')
    assert normalize(5, 'um', 'length') == normalize(0.005, 'mm', 'length')
    assert normalize(0.002, 'kW', 'power') == normalize(2, 'W', 'power')
    with pytest.raises(ProgramError, match='temperature_difference'):
        normalize(1, 'K', 'temperature_difference')
    with pytest.raises(ProgramError, match='absolute zero'):
        normalize(-273.16, 'degC', 'temperature')
    with pytest.raises(ValidationError, match='absolute zero'):
        QuantityValue(kind='quantity', dimension='temperature', value=-1.0, unit='K')
    with pytest.raises(ProgramError, match='expected conductivity'):
        normalize(1, 'W', 'conductivity')
