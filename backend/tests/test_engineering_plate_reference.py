"""Tests for the independent plate references R1 (Navier), R2 (finite difference), R4 (strip limits).

Coefficients: w D / (q b^4) and M / (q b^2), b = short side, nu = 0.3.

Label note: with a along X (long) and b along Y (short), the large bending
moment of a long plate has its curvature in the short direction, so it is
`My = -D (w_yy + nu w_xx)`. Timoshenko's tables use x along the short side, so
their `Mx` column is our `My` and vice versa.
"""

from __future__ import annotations

import functools
import math
import subprocess
import sys
from pathlib import Path

import pytest

from engineering.plate_reference import (
    FD_GRID_MULTIPLIERS,
    NAVIER_RELATIVE_STOP,
    NAVIER_TERM_CAP,
    STRIP_LIMIT_RELATIVE,
    check_strip_limit,
    fd_plate,
    navier_ss,
    strip_limits,
)

NU = 0.3
E = 200000.0
B = 100.0
H = 2.0
Q = 0.01
D = E * H**3 / (12.0 * (1.0 - NU**2))


def _wc(w: float, b: float = B) -> float:
    return w * D / (Q * b**4)


def _mc(m: float, b: float = B) -> float:
    return m / (Q * b**2)


def _within(value: float, ref: float, decimals: int, rel: float = 0.005) -> bool:
    return abs(value - ref) <= rel * abs(ref) + 0.5 * 10.0 ** (-decimals)


@functools.lru_cache(maxsize=None)
def _r1(ratio: float) -> dict:
    return navier_ss(ratio * B, B, H, Q, E, NU, return_fields=True)


@functools.lru_cache(maxsize=None)
def _r2(ratio: float, edge: str) -> dict:
    return fd_plate(ratio * B, B, H, Q, E, NU, edge)


def test_constants():
    assert NAVIER_RELATIVE_STOP == 1e-10
    assert NAVIER_TERM_CAP == 401
    assert FD_GRID_MULTIPLIERS == (1, 2, 4)
    assert STRIP_LIMIT_RELATIVE == 0.02


# ---------------------------------------------------------------- R1


def test_r1_square_ss_book_values():
    r = _r1(1.0)
    assert _within(_wc(r["w_max"]), 0.00406, 5)
    assert _within(_mc(r["mx_centre"]), 0.0479, 4)
    assert _within(_mc(r["my_centre"]), 0.0479, 4)
    assert r["converged"] or r["terms_used"] == NAVIER_TERM_CAP
    assert r["terms_used"] <= NAVIER_TERM_CAP
    assert isinstance(r["converged"], bool)
    assert r["last_relative_change"] >= 0.0
    # corner force R = 2 |Mxy|, about 0.065 q a^2 for the square plate
    assert _within(r["corner_force"] / (Q * B**2), 0.065, 3)


def test_r1_square_ss_von_mises_location_consistent():
    r = _r1(1.0)
    f = r["fields"]
    ix, iy = r["vm_max_index"]
    sx = 6.0 * f["Mx"][ix][iy] / H**2
    sy = 6.0 * f["My"][ix][iy] / H**2
    t = 6.0 * f["Mxy"][ix][iy] / H**2
    vm_here = math.sqrt(sx * sx - sx * sy + sy * sy + 3.0 * t * t)
    assert vm_here == pytest.approx(r["vm_max"], rel=1e-12)
    flat = [
        math.sqrt(
            (6 * f["Mx"][i][j] / H**2) ** 2
            - (6 * f["Mx"][i][j] / H**2) * (6 * f["My"][i][j] / H**2)
            + (6 * f["My"][i][j] / H**2) ** 2
            + 3 * (6 * f["Mxy"][i][j] / H**2) ** 2
        )
        for i in range(len(f["Mx"]))
        for j in range(len(f["Mx"][0]))
    ]
    assert max(flat) == pytest.approx(r["vm_max"], rel=1e-12)
    loc = r["vm_max_location"]
    assert loc["x"] == pytest.approx(r["grid_x"][ix])
    assert loc["y"] == pytest.approx(r["grid_y"][iy])
    assert r["vm_max_kind"] in {"centre", "corner", "other"}
    # square SS plate: twisting at the corner beats the centre
    assert r["vm_max_kind"] == "corner"
    assert r["vm_max"] * H**2 / (Q * B**2) == pytest.approx(math.sqrt(3.0) * 6 * 0.0325, rel=0.02)


def test_r1_grid_includes_edges_and_corners():
    r = _r1(2.0)
    assert r["grid_x"][0] == 0.0 and r["grid_y"][0] == 0.0
    assert r["grid_x"][-1] == pytest.approx(2.0 * B / 2.0)
    assert r["grid_y"][-1] == pytest.approx(B / 2.0)
    assert len(r["grid_x"]) >= 101 and len(r["grid_y"]) >= 101
    assert r["mxy_max_location"]["x"] == 0.0 and r["mxy_max_location"]["y"] == 0.0


def test_r1_ratio_two_book_values():
    r = _r1(2.0)
    assert _within(_wc(r["w_max"]), 0.01013, 5)
    # short-direction moment (our My) is the large one, see module docstring
    assert _within(_mc(r["my_centre"]), 0.1017, 4)
    assert _within(_mc(r["mx_centre"]), 0.0464, 4)


# ---------------------------------------------------------------- R2 SS vs R1


@pytest.mark.parametrize("ratio", [1.0, 1.5, 2.0, 5.0])
def test_r2_ss_matches_r1(ratio):
    r1 = _r1(ratio)
    r2 = _r2(ratio, "simply_supported")["extrapolated"]
    assert r2["w_centre"] == pytest.approx(r1["w_max"], rel=1e-3)
    assert r2["mx_centre"] == pytest.approx(r1["mx_centre"], rel=5e-3)
    assert r2["my_centre"] == pytest.approx(r1["my_centre"], rel=5e-3)
    assert r2["vm_max"] == pytest.approx(r1["vm_max"], rel=1e-2)


def test_r2_ss_matches_r1_ratio_1p2():
    r1 = _r1(1.2)
    r2 = _r2(1.2, "simply_supported")["extrapolated"]
    assert r2["w_centre"] == pytest.approx(r1["w_max"], rel=1e-3)
    assert r2["vm_max"] == pytest.approx(r1["vm_max"], rel=1e-2)


def test_r2_structure_and_unknown_cap():
    r = _r2(1.0, "simply_supported")
    assert set(r) >= {"extrapolated", "raw", "grids", "unknowns"}
    for name, vals in r["raw"].items():
        assert len(vals) == 3
        assert all(isinstance(v, float) for v in vals), name
    assert max(r["unknowns"]) <= 6000
    assert [g["multiplier"] for g in r["grids"]] == list(FD_GRID_MULTIPLIERS)
    with pytest.raises(ValueError):
        fd_plate(B, B, H, Q, E, NU, "simply_supported", n=40)


# ---------------------------------------------------------------- R2 CL vs books

CL_TABLE_35 = {
    # ratio: (w, long-edge moment magnitude, centre moment short direction, centre moment long direction)
    1.0: (0.00126, 0.0513, 0.0231, 0.0231),
    1.2: (0.00172, 0.0639, 0.0299, 0.0228),
    1.5: (0.00220, 0.0757, 0.0368, 0.0203),
    2.0: (0.00254, 0.0829, 0.0412, 0.0158),
}


@pytest.mark.parametrize("ratio", sorted(CL_TABLE_35))
def test_r2_cl_vs_timoshenko_table_35(ratio):
    w_ref, medge_ref, mshort_ref, mlong_ref = CL_TABLE_35[ratio]
    r = _r2(ratio, "clamped")["extrapolated"]
    assert _within(_wc(r["w_centre"]), w_ref, 5)
    assert _within(abs(_mc(r["edge_moment_long_edge"])), medge_ref, 4)
    assert r["edge_moment_long_edge"] < 0.0


@pytest.mark.parametrize("ratio", [1.2, 1.5, 2.0])
def test_r2_cl_centre_moments_vs_timoshenko_table_35(ratio):
    _, _, mshort_ref, mlong_ref = CL_TABLE_35[ratio]
    r = _r2(ratio, "clamped")["extrapolated"]
    # Timoshenko Mx (curvature along the short side) is our My
    assert _within(_mc(r["my_centre"]), mshort_ref, 4)
    assert _within(_mc(r["mx_centre"]), mlong_ref, 4)


def test_r2_cl_square_centre_moment_records_table_35_discrepancy():
    """Table 35 prints 0.0231 for the clamped square centre moment (Evans 1939).

    R2 converges (n = 16, 24, 32, 48 and Richardson) to 0.02290, 0.9 % lower, beyond the
    contract's R3 band (0.5 % + half a printed digit). The discrepancy is asserted, not hidden,
    and is reported to the lead as a contract question; no xfail (it would count as a skip).
    """
    r = _r2(1.0, "clamped")["extrapolated"]
    for name in ("mx_centre", "my_centre"):
        coefficient = _mc(r[name])
        assert 0.02285 <= coefficient <= 0.02295, (name, coefficient)
        assert 0.005 < 1.0 - coefficient / 0.0231 < 0.012, (name, coefficient)


@pytest.mark.parametrize("ratio,w_exact", [(1.0, 0.00126725), (1.2, 0.00172833), (2.0, 0.00253625)])
def test_r2_cl_vs_imrak_exact_within_r3_band(ratio, w_exact):
    """Imrak 2007's 'exact' column sits 0.15-0.2 % above the converged value; R2 matches the
    Taylor and Govindjee column of the same table to 0.1 % (next test). Imrak is a secondary
    R3 source, so it is checked at the contract's R3 tolerance (0.5 %), and the measured offset
    (R2 below Imrak by more than 0.1 %) is asserted."""
    r = _r2(ratio, "clamped")["extrapolated"]
    relative = _wc(r["w_centre"]) / w_exact - 1.0
    assert -0.005 <= relative < -0.001, relative


@pytest.mark.parametrize("ratio,w_ref", [(1.0, 0.00126532), (1.2, 0.00172487), (2.0, 0.00253297)])
def test_r2_cl_vs_taylor_govindjee(ratio, w_ref):
    r = _r2(ratio, "clamped")["extrapolated"]
    assert _wc(r["w_centre"]) == pytest.approx(w_ref, rel=1e-3)


# ---------------------------------------------------------------- R4


def test_r4_strip_limits_formulas():
    s = strip_limits(B, H, Q, E, NU)
    assert s["simply_supported"]["w"] == pytest.approx(5 * Q * B**4 / (384 * D))
    assert s["clamped"]["w"] == pytest.approx(Q * B**4 / (384 * D))
    assert s["clamped"]["edge_moment"] == pytest.approx(Q * B**2 / 12.0)


def test_r4_check_strip_limit():
    ok = check_strip_limit(1.019, 1.0)
    assert ok["passed"] and ok["relative_error"] == pytest.approx(0.019)
    bad = check_strip_limit(1.021, 1.0)
    assert not bad["passed"]
    with pytest.raises(ValueError):
        check_strip_limit(1.0, 0.0)
    with pytest.raises(ValueError):
        check_strip_limit(float("nan"), 1.0)


def test_r4_references_at_ratio_five():
    s = strip_limits(B, H, Q, E, NU)
    r1 = _r1(5.0)
    ss = _r2(5.0, "simply_supported")["extrapolated"]
    cl = _r2(5.0, "clamped")["extrapolated"]
    assert check_strip_limit(r1["w_max"], s["simply_supported"]["w"])["passed"]
    assert check_strip_limit(ss["w_centre"], s["simply_supported"]["w"])["passed"]
    assert check_strip_limit(cl["w_centre"], s["clamped"]["w"])["passed"]
    assert check_strip_limit(abs(cl["edge_moment_long_edge"]), s["clamped"]["edge_moment"])["passed"]


# ---------------------------------------------------------------- validation / determinism


@pytest.mark.parametrize(
    "kwargs",
    [
        {"a": 50.0},  # a < b
        {"nu": 0.0},
        {"nu": 0.5},
        {"nu": -0.1},
        {"nu": 0.7},
        {"a": float("nan")},
        {"b": float("inf")},
        {"h": 0.0},
        {"q": -1.0},
        {"E": 0.0},
        {"E": float("nan")},
    ],
)
def test_validation_rejects(kwargs):
    base = {"a": 100.0, "b": 100.0, "h": H, "q": Q, "E": E, "nu": NU}
    base.update(kwargs)
    with pytest.raises(ValueError):
        navier_ss(**base)
    with pytest.raises(ValueError):
        fd_plate(**base, edge_condition="clamped", n=2)
    if set(kwargs) <= {"b", "h", "q", "E", "nu"}:
        with pytest.raises(ValueError):
            strip_limits(base["b"], base["h"], base["q"], base["E"], base["nu"])


def test_unsupported_edge_condition():
    with pytest.raises(ValueError):
        fd_plate(100.0, 100.0, H, Q, E, NU, "free", n=2)


def test_determinism():
    assert navier_ss(150.0, 100.0, H, Q, E, NU) == navier_ss(150.0, 100.0, H, Q, E, NU)
    one = fd_plate(150.0, 100.0, H, Q, E, NU, "clamped", n=3)
    two = fd_plate(150.0, 100.0, H, Q, E, NU, "clamped", n=3)
    assert one == two


def test_results_are_plain_json():
    import json

    json.dumps(navier_ss(100.0, 100.0, H, Q, E, NU))
    json.dumps(fd_plate(100.0, 100.0, H, Q, E, NU, "simply_supported", n=3))
    json.dumps(strip_limits(B, H, Q, E, NU))


# ---------------------------------------------------------------- independence


def test_import_loads_no_heavy_modules():
    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys; sys.path.insert(0, 'backend'); "
        "import engineering.plate_reference; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('cadquery', 'OCP', 'scipy')); "
        "print(','.join(bad)); "
        "assert 'engineering.plate_fea' not in sys.modules"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""
