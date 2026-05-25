"""ISA atmosphere validation tests."""
import math
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.aero.isa import isa, AtmState

def _close(a, b, tol=0.01):
    return abs(a - b) / max(abs(b), 1e-12) < tol

def test_sea_level():
    s = isa(0.0)
    assert _close(s.rho, 1.225, 0.005), f"rho={s.rho}"
    assert _close(s.T, 288.15, 0.001), f"T={s.T}"
    assert _close(s.P, 101325.0, 0.001), f"P={s.P}"
    assert _close(s.a, 340.3, 0.005), f"a={s.a}"
    print(f"Sea level: rho={s.rho:.4f}, T={s.T:.2f}, a={s.a:.2f} OK")

def test_tropopause():
    s = isa(11000.0)
    assert _close(s.T, 216.65, 0.001), f"T={s.T}"
    assert _close(s.P, 22632.0, 0.01), f"P={s.P}"
    print(f"Tropopause: T={s.T:.2f}K, P={s.P:.1f}Pa OK")

def test_stratosphere():
    s = isa(15000.0)
    assert s.T == 216.65, f"T={s.T} (stratosphere should be isothermal)"
    assert s.P < 22632.0, f"P should decrease above tropopause"
    print(f"Stratosphere: T={s.T:.2f}K, P={s.P:.1f}Pa OK")

def test_viscosity_positive():
    for h in [0, 5000, 11000, 15000]:
        s = isa(h)
        assert s.mu > 0 and s.nu > 0, f"Negative viscosity at h={h}"
    print("Viscosity positive at all altitudes OK")

def test_invalid_altitude():
    try:
        isa(-100)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    try:
        isa(25000)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("Invalid altitude rejection OK")

if __name__ == "__main__":
    test_sea_level()
    test_tropopause()
    test_stratosphere()
    test_viscosity_positive()
    test_invalid_altitude()
    print("All ISA tests passed.")
