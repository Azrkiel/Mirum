"""Pure-Python profile laws for the lofted dragster styles; stdlib only, no CAD."""
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.dragsters.profiles import (
    Station, crown_height_at, disk_margin, half_width_at, interpolate, law_volume,
    max_half_width_over, min_half_width_over, min_side_height_over, narrow_waist_stations,
    section_area, side_height_at, stations_for, teardrop_stations, top_at,
)

REPO = Path(__file__).parents[2]

TEARDROP = SimpleNamespace(
    style='teardrop', length_mm=220.0, nose_width_mm=16.0, nose_height_mm=14.0,
    max_width_mm=56.0, max_height_mm=50.0, max_section_x_frac=0.35,
    tail_width_mm=44.0, tail_height_mm=46.0, side_height_frac=0.5, crown_exponent=3.0,
)
NARROW = SimpleNamespace(
    style='narrow_waist', length_mm=230.0, nose_width_mm=14.0, nose_height_mm=12.0,
    front_section_x_frac=0.15, front_width_mm=40.0, waist_x_frac=0.45, waist_width_mm=22.0,
    body_height_mm=26.0, housing_width_mm=44.0, housing_height_mm=46.0, housing_length_mm=80.0,
    side_height_frac=0.7, crown_exponent=3.0,
)
WEDGE = SimpleNamespace(style='wedge', length_mm=200.0, width_mm=42.0, height_mm=32.0, nose_height_mm=12.0)

THREE = [Station(0, 10, 5, 4, 'ease_out'), Station(50, 20, 15, 6, 'smoothstep'), Station(120, 14, 9, 3, 'hold'),
         Station(160, 30, 20, 10)]


def approx_station(actual, expected):
    assert actual.kind == expected.kind
    assert (actual.x, actual.a, actual.s, actual.c) == pytest.approx((expected.x, expected.a, expected.s, expected.c),
                                                                     abs=1e-12)


# --- interpolate ---------------------------------------------------------------------------

def test_interpolate_hits_station_values_exactly():
    for station in THREE:
        assert interpolate(THREE, station.x) == (station.a, station.s, station.c)


@pytest.mark.parametrize('kind', ['smoothstep', 'ease_out'])
def test_interpolate_monotone_between_stations(kind):
    stations = [Station(0, 10, 5, 4, kind), Station(100, 30, 2, 9, kind)]
    xs = [100 * i / 400 for i in range(401)]
    values = [interpolate(stations, x) for x in xs]
    for (a0, s0, c0), (a1, s1, c1) in zip(values, values[1:]):
        assert a1 >= a0 and s1 <= s0 and c1 >= c0


def test_interpolate_formulas():
    ease = [Station(0, 0, 0, 0, 'ease_out'), Station(10, 10, 20, 30)]
    smooth = [Station(0, 0, 0, 0, 'smoothstep'), Station(10, 10, 20, 30)]
    for t in (0.1, 0.25, 0.5, 0.9):
        f = math.sin(math.pi * t / 2)
        assert interpolate(ease, 10 * t) == pytest.approx((10 * f, 20 * f, 30 * f), abs=1e-12)
        f = 3 * t * t - 2 * t ** 3
        assert interpolate(smooth, 10 * t) == pytest.approx((10 * f, 20 * f, 30 * f), abs=1e-12)


def test_hold_is_constant_until_next_station():
    for x in (120, 121.5, 140, 159.999):
        assert interpolate(THREE, x) == (14, 9, 3)
    assert interpolate(THREE, 160) == (30, 20, 10)


@pytest.mark.parametrize('x', [-1e-9, -5, 160.0001, 1e9, float('nan')])
def test_interpolate_out_of_range_raises(x):
    with pytest.raises(ValueError):
        interpolate(THREE, x)


@pytest.mark.parametrize('stations', [
    [],
    [Station(0, 1, 1, 1)],
    [Station(1, 1, 1, 1), Station(5, 1, 1, 1)],
    [Station(0, 1, 1, 1), Station(5, 1, 1, 1), Station(5, 2, 2, 2)],
    [Station(0, 1, 1, 1), Station(5, 1, 1, 1), Station(4, 2, 2, 2)],
    [Station(0, 1, 1, 1, 'linear'), Station(5, 1, 1, 1)],
])
def test_invalid_station_lists_raise(stations):
    with pytest.raises(ValueError):
        interpolate(stations, 0)


# --- section area / crown / disk margin ----------------------------------------------------

def test_section_area_half_ellipse_and_rectangle():
    a, s, c = 21.0, 13.0, 9.0
    assert section_area(a, s, c, 2) == pytest.approx(2 * a * s + math.pi * a * c / 2, abs=1e-9)
    assert section_area(a, s, 0, 3) == pytest.approx(2 * a * s, abs=1e-12)
    # n -> large approaches a full rectangle crown
    assert section_area(a, s, c, 4) < 2 * a * (s + c)
    assert section_area(a, s, c, 4) > section_area(a, s, c, 3) > section_area(a, s, c, 2)


def test_crown_height():
    assert crown_height_at(10, 5, 4, 3, 0) == pytest.approx(9)
    assert crown_height_at(10, 5, 4, 3, 10) == pytest.approx(5)
    assert crown_height_at(10, 5, 4, 3, -10) == pytest.approx(5)
    y = 6.0
    assert crown_height_at(10, 5, 4, 2, y) == pytest.approx(5 + 4 * math.sqrt(1 - 0.36))
    assert crown_height_at(10, 5, 4, 3, y) == crown_height_at(10, 5, 4, 3, -y)
    with pytest.raises(ValueError):
        crown_height_at(10, 5, 4, 3, 10.0001)


def test_disk_margin_cases():
    a, s, c, n = 20.0, 20.0, 10.0, 2.0
    # comfortably inside: side 20-(0+5)=15, bottom 10-5=5, crown at y=5 minus 15
    expected = min(15, 5, crown_height_at(a, s, c, n, 5) - 15)
    assert disk_margin(a, s, c, n, 0, 10, 5) == pytest.approx(expected)
    assert disk_margin(a, s, c, n, 0, 10, 5) > 0
    # touching the bottom exactly
    assert disk_margin(a, s, c, n, 0, 5, 5) == pytest.approx(0, abs=1e-12)
    # touching the side exactly
    assert disk_margin(a, s, c, n, 15, 10, 5) == pytest.approx(0, abs=1e-12)
    # poking through the crown
    assert disk_margin(a, s, c, n, 0, 26, 5) < 0
    # below the bottom
    assert disk_margin(a, s, c, n, 0, 3, 5) < 0
    # beyond the side
    assert disk_margin(a, s, c, n, 16, 10, 5) == -math.inf
    assert disk_margin(a, s, c, n, -16, 10, 5) == -math.inf


# --- law volume ----------------------------------------------------------------------------

def test_law_volume_constant_section():
    stations = [Station(0, 12, 8, 5, 'hold'), Station(180, 12, 8, 5)]
    expected = section_area(12, 8, 5, 3) * 180
    assert law_volume(stations, 3) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize('kind,mean_f', [('smoothstep', 0.5), ('ease_out', 2 / math.pi)])
def test_law_volume_ramp_matches_analytic(kind, mean_f):
    # rectangular section (c=0), only a varies: V = 2 s L (a0 + (a1 - a0) * mean(f))
    stations = [Station(0, 5, 10, 0, kind), Station(200, 25, 10, 0)]
    expected = 2 * 10 * 200 * (5 + 20 * mean_f)
    assert law_volume(stations, 3) == pytest.approx(expected, rel=1e-6)


def test_law_volume_requires_odd_samples():
    stations = [Station(0, 5, 10, 0), Station(200, 25, 10, 0)]
    for samples in (4000, 1, 2):
        with pytest.raises(ValueError):
            law_volume(stations, 3, samples=samples)


# --- style station lists -------------------------------------------------------------------

def test_teardrop_preset_stations():
    stations = teardrop_stations(TEARDROP)
    expected = [Station(0, 8, 7, 7, 'ease_out'), Station(77, 28, 25, 25, 'smoothstep'),
                Station(220, 22, 23, 23, 'smoothstep')]
    assert len(stations) == 3
    for actual, want in zip(stations, expected):
        approx_station(actual, want)
    assert stations[-1].x == 220.0


def test_narrow_waist_preset_stations():
    stations = narrow_waist_stations(NARROW)
    expected = [Station(0, 7, 8.4, 3.6, 'ease_out'), Station(34.5, 20, 18.2, 7.8, 'smoothstep'),
                Station(103.5, 11, 18.2, 7.8, 'smoothstep'), Station(150, 22, 32.2, 13.8, 'hold'),
                Station(230, 22, 32.2, 13.8, 'smoothstep')]
    assert len(stations) == 5
    for actual, want in zip(stations, expected):
        approx_station(actual, want)
    assert stations[-1].x == 230.0


def test_stations_for_dispatch():
    assert stations_for(TEARDROP) == teardrop_stations(TEARDROP)
    assert stations_for(NARROW) == narrow_waist_stations(NARROW)
    with pytest.raises(ValueError):
        stations_for(WEDGE)


# --- dispatching helpers -------------------------------------------------------------------

def test_wedge_law_helpers():
    assert half_width_at(WEDGE, 0) == 21
    assert half_width_at(WEDGE, 200) == 21
    assert side_height_at(WEDGE, 0) == pytest.approx(12)
    assert side_height_at(WEDGE, 50) == pytest.approx(12 + 20 * 0.5)
    assert side_height_at(WEDGE, 100) == pytest.approx(32)
    assert side_height_at(WEDGE, 180) == pytest.approx(32)
    assert top_at(WEDGE, 50, 0) == top_at(WEDGE, 50, 21) == pytest.approx(22)
    with pytest.raises(ValueError):
        top_at(WEDGE, 50, 21.01)
    with pytest.raises(ValueError):
        half_width_at(WEDGE, 200.01)


def test_lofted_law_helpers():
    for spec in (TEARDROP, NARROW):
        stations = stations_for(spec)
        for x in (0, 33.3, 77, 120.25, spec.length_mm):
            a, s, c = interpolate(stations, x)
            assert half_width_at(spec, x) == a
            assert side_height_at(spec, x) == s
            assert top_at(spec, x, 0) == pytest.approx(s + c)
            assert top_at(spec, x, a) == pytest.approx(s)
            assert top_at(spec, x, -a / 2) == pytest.approx(crown_height_at(a, s, c, spec.crown_exponent, a / 2))
    with pytest.raises(ValueError):
        half_width_at(SimpleNamespace(style='blob', length_mm=200), 10)


def test_sampled_extrema_over_interval():
    # teardrop half-width peaks at the max station (x=77, a=28)
    assert max_half_width_over(TEARDROP, 60, 90) == pytest.approx(28)
    assert max_half_width_over(TEARDROP, 0, 0) == pytest.approx(8)
    # endpoints are always included even when the step does not divide the interval
    assert max_half_width_over(TEARDROP, 10, 10.3, step=0.25) == pytest.approx(half_width_at(TEARDROP, 10.3))
    assert min_half_width_over(NARROW, 90, 120) == pytest.approx(11)
    assert min_side_height_over(NARROW, 0, 10) == pytest.approx(8.4)
    assert min_side_height_over(WEDGE, 0, 200) == pytest.approx(12)
    with pytest.raises(ValueError):
        max_half_width_over(TEARDROP, 10, 5)
    with pytest.raises(ValueError):
        max_half_width_over(TEARDROP, 0, 10, step=0)


# --- import hygiene ------------------------------------------------------------------------

def test_profiles_import_is_stdlib_only():
    code = (
        "import sys\n"
        "for name in ('cadquery', 'OCP', 'numpy', 'trimesh', 'pydantic'):\n"
        "    sys.modules[name] = None\n"
        "import backend.dragsters.profiles as p\n"
        "assert p.law_volume([p.Station(0, 1, 1, 1), p.Station(10, 1, 1, 1)], 3) > 0\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, '-c', code], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'
