"""E05 grounding contract for numeric and choice values extracted from prompts."""
import math

import pytest

from engineering.grounding import (ChoiceField, NumberField, is_grounded_choice,
                                   is_grounded_number, number_tokens, ungrounded_fields)


SYNONYMS = {
    'hand_toss': ('hand toss', 'hand-toss', 'hand launched'),
    'tow': ('tow launch',),
}


def test_number_tokens_keep_order_and_duplicates():
    assert number_tokens('14 m, 0.025, -3, 1.5e3, .5, CO2, 14 m') == (
        14.0, 0.025, -3.0, 1500.0, 0.5, 2.0, 14.0,
    )


@pytest.mark.parametrize(('prompt', 'value', 'dimension', 'unit'), [
    ('1.2 m', 1200.0, 'length', 'mm'),
    ('1.2 m', 1.2, 'length', 'm'),
    ('2 in', 50.8, 'length', 'mm'),
    ('6 m/s', 6.0, 'velocity', 'm/s'),
    ('6 m/s', 21.6, 'velocity', 'km/h'),
    ('0.06 kg', 60.0, 'mass', 'g'),
    ('90 deg', math.pi / 2, 'angle', 'rad'),
    ('1 bar', 100.0, 'pressure', 'kPa'),
    ('2 kN', 2000.0, 'force', 'N'),
    ('2 min', 120.0, 'time', 's'),
    ('Use 32', 32, 'dimensionless', '1'),
])
def test_number_grounded_by_a_same_dimension_factor(prompt, value, dimension, unit):
    assert is_grounded_number(value, prompt, dimension, unit)


def test_relative_tolerance_is_inclusive_and_relative_to_value():
    assert is_grounded_number(200.0, '201', 'dimensionless', '1')
    assert not is_grounded_number(200.0, '201.2', 'dimensionless', '1')
    assert not is_grounded_number(200.0, '201.001', 'dimensionless', '1')


def test_zero_requires_a_zero_token():
    assert is_grounded_number(0.0, 'Set the length to 0 mm.', 'length', 'mm')
    assert not is_grounded_number(0.0, 'Use 0.00001 mm.', 'length', 'mm')
    assert not is_grounded_number(0.0, 'No length stated.', 'length', 'mm')


@pytest.mark.parametrize('value', [True, False])
def test_boolean_is_never_a_grounded_number(value):
    assert not is_grounded_number(value, '0 and 1', 'dimensionless', '1')


@pytest.mark.parametrize(('dimension', 'unit'), [('unknown', 'mm'), ('length', 'unknown')])
def test_unknown_dimension_or_unit_raises_key_error(dimension, unit):
    with pytest.raises(KeyError):
        is_grounded_number(1.0, '1', dimension, unit)


@pytest.mark.parametrize(('prompt', 'value'), [('radius of 25 mm', 50.0), ('3 mm and 4 mm', 7.0)])
def test_derived_number_is_not_grounded(prompt, value):
    assert not is_grounded_number(value, prompt, 'length', 'mm')


@pytest.mark.parametrize('prompt', ['A Hand-Toss glider', 'A hand__toss glider', 'A hand  launched glider'])
def test_choice_normalizes_synonyms(prompt):
    assert is_grounded_choice('hand_toss', prompt, SYNONYMS)


@pytest.mark.parametrize('prompt', ['A handtossing glider', 'A hand-tossing glider', 'A prehand-toss glider'])
def test_choice_requires_word_boundaries(prompt):
    assert not is_grounded_choice('hand_toss', prompt, SYNONYMS)


def test_choice_requires_an_explicit_synonym_mapping():
    assert not is_grounded_choice('hand_toss', 'A Hand-Toss glider', {'tow': ('tow launch',)})


def test_choice_list_requires_every_element_to_be_grounded():
    choices = ['hand_toss', 'tow']
    assert is_grounded_choice(choices, 'Try hand toss and tow-launch.', SYNONYMS)
    assert not is_grounded_choice(choices, 'Try hand toss.', SYNONYMS)
    assert not is_grounded_choice([], 'Try hand toss.', SYNONYMS)


@pytest.mark.parametrize('value', [None, False, True])
def test_none_and_booleans_are_never_grounded_choices(value):
    assert not is_grounded_choice(value, 'None, false or true', SYNONYMS)


def test_ungrounded_fields_are_sorted_skip_none_and_include_unknown_names():
    values = {
        'z_unknown': 14.0,
        'mode': 'hand_toss',
        'length': 14.0,
        'unknown_unset': None,
        'a_wrong_length': 11.0,
        'known_unset': None,
        'b_wrong_mode': 'tow',
    }
    length = NumberField('length', 'mm')
    mode = ChoiceField(SYNONYMS)
    kinds = {'mode': mode, 'length': length, 'a_wrong_length': length,
             'known_unset': length, 'b_wrong_mode': mode}

    assert ungrounded_fields(values, 'A 14 mm Hand-Toss glider', kinds) == (
        'a_wrong_length', 'b_wrong_mode', 'z_unknown',
    )


def test_unit_binds_to_its_own_factor_only():
    assert not is_grounded_number(2000.0, 'span 2 m', 'length', 'm')
    assert is_grounded_number(2.0, 'span 2 m', 'length', 'm')
    assert is_grounded_number(2000.0, 'span 2 m', 'length', 'mm')
    assert is_grounded_number(304.8, '1 ft', 'length', 'mm')
    assert not is_grounded_number(1.0, '1 ft', 'length', 'm')
    assert is_grounded_number(40.0, '40 mm', 'length', 'mm')
    assert is_grounded_number(4.0, '40 mm', 'length', 'cm')
    assert not is_grounded_number(50.8, 'Make it 2 long', 'length', 'mm')


def test_bare_number_grounds_only_in_canonical_unit():
    assert is_grounded_number(300, 'use 300 channels', 'dimensionless', '1')
    assert is_grounded_number(300.0, 'use 300', 'length', 'mm')
    assert not is_grounded_number(0.3, 'use 300', 'length', 'm')


def test_other_dimension_unit_does_not_ground():
    assert not is_grounded_number(5.0, '5 kg', 'length', 'm')
    assert not is_grounded_number(5.0, '5 kg', 'dimensionless', '1')
    assert not is_grounded_number(5.0, '5 m', 'dimensionless', '1')


def test_unit_must_end_at_a_word_boundary():
    # mmHg / masts are not units: the number stays bare, so no unit conversion applies
    for prompt, number in (('5 mmHg', 5.0), ('2 masts', 2.0)):
        assert not is_grounded_number(number * 1000, prompt, 'length', 'mm')
        assert not is_grounded_number(number * 10, prompt, 'length', 'mm')
        assert not is_grounded_number(number / 1000, prompt, 'length', 'm')
        assert not is_grounded_number(number * 25.4, prompt, 'length', 'mm')


def test_unit_aliases_and_case_rules():
    assert is_grounded_number(25.4, '1 inch', 'length', 'mm')
    assert is_grounded_number(25.4, '1" wide', 'length', 'mm')
    assert is_grounded_number(304.8, "1' tall", 'length', 'mm')
    assert is_grounded_number(2000.0, '2 Meters', 'length', 'mm')
    assert is_grounded_number(1000.0, '1 m', 'length', 'mm')
    assert not is_grounded_number(1e9, '1 M', 'length', 'mm')
    assert is_grounded_number(1e6, '1 MPa', 'pressure', 'Pa')
    assert is_grounded_number(math.pi, '180 deg', 'angle', 'rad')
    assert is_grounded_number(math.pi, '180°', 'angle', 'rad')
    assert is_grounded_number(3.6, '1 m/s', 'velocity', 'km/h')
    assert is_grounded_number(40.0, '40-mm bore', 'length', 'mm')


def test_underflow_does_not_ground_zero():
    assert not is_grounded_number(0.0, 'a 1e-400 gap', 'length', 'mm')
    assert not is_grounded_number(0.0, 'a 1e-400 mm gap', 'length', 'mm')
    assert number_tokens('1e-400 and 1e999 and 3') == (3.0,)
    assert is_grounded_number(0.0, '0 mm', 'length', 'mm')
    assert is_grounded_number(0.0, '0.0e5', 'dimensionless', '1')


@pytest.mark.parametrize('prompt', ['ambient 20 °C', 'ambient 20 C at sea level'.replace(' C', ' degC'),
                                    'about 20 degrees Celsius', 'at 20 celsius', 'air at 20'])
def test_celsius_temperature_grounds_a_celsius_field(prompt):
    assert is_grounded_number(20.0, prompt, 'temperature', 'C')


@pytest.mark.parametrize('prompt', ['ambient 68 °F', 'at 68 degrees Fahrenheit', 'turn it 20 degrees',
                                    'heading 20°'])
def test_fahrenheit_or_angle_never_grounds_celsius(prompt):
    assert not is_grounded_number(20.0, prompt, 'temperature', 'C')
    assert not is_grounded_number(68.0, prompt, 'temperature', 'C')


def test_glider_temperature_field_is_celsius():
    from engineering import glider_extraction
    kinds = {k: v for k, v in vars(glider_extraction).items() if isinstance(v, dict) and 'temperature_c' in v
             and isinstance(v['temperature_c'], NumberField)}
    assert kinds and all(v['temperature_c'] == NumberField('temperature', 'C') for v in kinds.values())
