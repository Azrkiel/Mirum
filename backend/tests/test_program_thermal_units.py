from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.language.diagnostics import MelError
from engineering.language.semantic import compile_source
from engineering.program.errors import ProgramError
from engineering.program.ir import QuantityValue
from engineering.program.units import normalize


SOURCE = (Path(__file__).parents[1] / 'engineering/fixtures/thermal/bar.mir').read_bytes()


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


def test_thermal_mel_constraints_and_canonical_meaning():
    nominal = compile_source(SOURCE)
    equivalent = compile_source(SOURCE.replace(b'20 degC', b'293.15 K').replace(
        b'1 delta_degC', b'1 delta_K').replace(b'0.10 m', b'100 mm').replace(b'2 W;', b'0.002 kW;'))
    assert nominal.semantic_sha256 == equivalent.semantic_sha256
    assert nominal.brief_sha256 == equivalent.brief_sha256
    assert nominal.source_sha256 != equivalent.source_sha256


@pytest.mark.parametrize(('old', 'new', 'code'), [
    (b'bar.cold_end', b'bar.ends', 'MEL-S022'),
    (b'bar.cold_end', b'bar.missing', 'MEL-S023'),
    (b'conduction.temperature', b'conduction.missing', 'MEL-S023'),
    (b'conduction.temperature', b'conduction.fields', 'MEL-S022'),
    (b'cold_fixed_hot_free', b'both_ends_fixed', 'MEL-S015'),
    (b'20 degC', b'-300 degC', 'MEL-S007'),
    (b'20 degC', b'20 delta_K', 'MEL-S005'),
])
def test_thermal_mel_reports_invalid_witnesses_and_units(old, new, code):
    with pytest.raises(MelError) as error:
        compile_source(SOURCE.replace(old, new))
    assert error.value.diagnostic.code == code
