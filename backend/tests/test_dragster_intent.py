"""Deterministic routing boundaries; no provider or CAD dependencies."""
import pytest
from backend.dragsters.intent import detect


@pytest.mark.parametrize('prompt,expected', [
    ('CO2 dragster body', True), ('co₂ car for tech class', True),
    ('carbon dioxide powered racer', True), ('balsa dragster wedge', True),
    ('co 2 vehicle', True), ('CO2-powered dragster with aerodynamic profile', True),
    ('co2 powered mini balsa racecar', True), ('race car powered by co2', True),
    ('a dragster', True), ('co2 derby car', True), ('co2 car cartridge pocket', True),
    ('CO2 narrow waist racer', True), ('a vehicle using CO₂', True),
    ('CO2 cartridge dragster body', True), ('tear drop co2 car', True),
    ('design a pinewood derby car', False), ('derby car', False),
    ('a full size top fuel dragster chassis', False), ('CO2 cartridge holder', False),
    ('rocket nozzle', False), ('race car spoiler', False), ('co2 canister', False),
    ('full-size co2 dragster', False), ('life-size dragster', False),
    ('1:1 dragster', False), ('real car co2 vehicle', False), ('NHRA dragster', False),
    ('funny car dragster', False), ('co2 pinewood car', False), ('glider wing', False),
    ('co2 powered mini balsa classroom racecar', False), ('carburetor co2 cylinder', False),
    ('', False), ('dragsteroid model', False), ('fullsize model car', False),
])
def test_intent_table(prompt, expected):
    result = detect(prompt)
    assert result.is_dragster is expected
    assert result.reason


def test_old_strategy_keywords_are_narrowed():
    from backend.strategies import _KEYWORD_TRIGGERS
    assert _KEYWORD_TRIGGERS['rule_co2_dragster'] == [
        'co2 dragster','co₂ dragster','co2 car','carbon dioxide dragster']
