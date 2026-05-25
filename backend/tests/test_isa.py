"""Validate ISA implementation against ICAO tabulated values."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from aero.isa import isa
import math


def test_sea_level():
    s = isa(0)
    assert abs(s.T - 288.15) < 0.01
    assert abs(s.P - 101325) < 1
    assert abs(s.rho - 1.2250) < 0.001
    assert abs(s.a - 340.29) < 0.1


def test_tropopause():
    s = isa(11000)
    assert abs(s.T - 216.65) < 0.01
    assert abs(s.P - 22632) < 10
    assert abs(s.rho - 0.3639) < 0.001
    assert abs(s.a - 295.07) < 0.1


def test_stratosphere():
    s = isa(15000)
    assert abs(s.T - 216.65) < 0.01


def test_viscosity():
    s = isa(0)
    assert abs(s.mu - 1.789e-5) < 1e-7


if __name__ == "__main__":
    for fn in [test_sea_level, test_tropopause, test_stratosphere, test_viscosity]:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
