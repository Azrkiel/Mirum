"""Pure-Python profile laws for CO2 dragster bodies (stdlib only).

This module is the mathematical definition of every body style. Builders (``kernel``,
``styles``) render it; validation measures STEP files against it. It must never import
CadQuery, OCP, numpy, trimesh or pydantic: the frontend image imports it directly.

Frame (master plan § 5.8): millimetres, X nose -> rear with the nose at X=0, Y transverse
and symmetric about Y=0, Z up. Every height returned here is ``z'``, measured from the flat
body bottom (``z' = z - ground_clearance_mm``), never an absolute Z.

Lofted styles are described by control stations ``Station(x, a, s, c, kind)``: ``a`` is the
half-width, ``s`` the vertical side height and ``c`` the crown height above the side. The
section at X is a flat-bottom D-section: the rectangle ``|y| <= a, 0 <= z' <= s`` plus the
upper half super-ellipse ``(|y|/a)^n + ((z' - s)/c)^n <= 1, z' >= s``. Between consecutive
stations ``a``, ``s`` and ``c`` follow the first station's ``kind``.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math
from typing import Literal, Sequence

Kind = Literal['smoothstep', 'ease_out', 'hold']
KINDS = ('smoothstep', 'ease_out', 'hold')
LOFTED_STYLES = ('teardrop', 'narrow_waist')


@dataclass(frozen=True)
class Station:
    x: float
    a: float
    s: float
    c: float
    kind: Kind = 'smoothstep'   # interpolation from this station to the NEXT one


def _check_stations(stations: Sequence[Station]) -> None:
    if len(stations) < 2:
        raise ValueError('a profile needs at least two stations')
    if stations[0].x != 0:
        raise ValueError('the first station must be at x=0')
    for prev, cur in zip(stations, stations[1:]):
        if not cur.x > prev.x:
            raise ValueError('station x values must be strictly increasing')
    for station in stations:
        if station.kind not in KINDS:
            raise ValueError(f'unknown station kind {station.kind!r}')
        if not all(math.isfinite(v) for v in (station.x, station.a, station.s, station.c)):
            raise ValueError('station values must be finite')


def _blend(kind: str, t: float) -> float:
    if kind == 'smoothstep':
        return 3 * t * t - 2 * t * t * t
    if kind == 'ease_out':
        return math.sin(math.pi * t / 2)
    return 0.0  # hold


def interpolate(stations: Sequence[Station], x: float) -> tuple[float, float, float]:
    """Return ``(a, s, c)`` at ``x``; exact station values at station x's."""
    _check_stations(stations)
    last = stations[-1]
    if not 0 <= x <= last.x:  # also rejects NaN
        raise ValueError(f'x={x} outside the profile [0, {last.x}]')
    if x == last.x:
        return last.a, last.s, last.c
    i = bisect_right([st.x for st in stations], x) - 1
    lo, hi = stations[i], stations[i + 1]
    if x == lo.x:
        return lo.a, lo.s, lo.c
    f = _blend(lo.kind, (x - lo.x) / (hi.x - lo.x))
    return (lo.a + (hi.a - lo.a) * f, lo.s + (hi.s - lo.s) * f, lo.c + (hi.c - lo.c) * f)


def section_area(a: float, s: float, c: float, n: float) -> float:
    """Area of the flat-bottom D-section: rectangle 2as plus super-ellipse half crown."""
    return 2 * a * s + 2 * a * c * math.gamma(1 + 1 / n) ** 2 / math.gamma(1 + 2 / n)


def crown_height_at(a: float, s: float, c: float, n: float, y: float) -> float:
    """Top surface ``z'`` at lateral ``y`` (``|y| <= a``)."""
    u = abs(y)
    if u > a:
        raise ValueError(f'|y|={u} outside the half-width {a}')
    return s + c * max(0.0, 1 - (u / a) ** n) ** (1 / n)


def disk_margin(a: float, s: float, c: float, n: float, yc: float, zc: float, r: float) -> float:
    """Min slack (mm) of the axis-aligned square of half-side ``r`` centred ``(yc, zc')``.

    ``>= 0`` iff the square, hence the inscribed disk of radius ``r``, lies inside the section.
    The top surface decreases with ``|y|``, so the binding crown point is ``|yc| + r``.
    """
    edge = abs(yc) + r
    if edge > a:
        return -math.inf
    return min(a - edge, zc - r, crown_height_at(a, s, c, n, edge) - (zc + r))


def law_volume(stations: Sequence[Station], n: float, samples: int = 4001) -> float:
    """Composite Simpson integral of ``section_area`` over ``[0, last.x]`` (``samples`` odd)."""
    if samples < 3 or samples % 2 == 0:
        raise ValueError('samples must be an odd integer >= 3')
    _check_stations(stations)
    length = stations[-1].x
    h = length / (samples - 1)
    total = 0.0
    for i in range(samples):
        x = length if i == samples - 1 else i * h
        weight = 1 if i in (0, samples - 1) else (4 if i % 2 else 2)
        total += weight * section_area(*interpolate(stations, x), n)
    return total * h / 3


def _split(h: float, frac: float) -> tuple[float, float]:
    s = frac * h
    return s, h - s


def teardrop_stations(spec) -> list[Station]:
    L, frac = spec.length_mm, spec.side_height_frac
    sections = [
        (0.0, spec.nose_width_mm, spec.nose_height_mm, 'ease_out'),
        (spec.max_section_x_frac * L, spec.max_width_mm, spec.max_height_mm, 'smoothstep'),
        (L, spec.tail_width_mm, spec.tail_height_mm, 'smoothstep'),
    ]
    return [Station(x, w / 2, *_split(h, frac), kind) for x, w, h, kind in sections]


def narrow_waist_stations(spec) -> list[Station]:
    L, frac = spec.length_mm, spec.side_height_frac
    sections = [
        (0.0, spec.nose_width_mm, spec.nose_height_mm, 'ease_out'),
        (spec.front_section_x_frac * L, spec.front_width_mm, spec.body_height_mm, 'smoothstep'),
        (spec.waist_x_frac * L, spec.waist_width_mm, spec.body_height_mm, 'smoothstep'),
        (L - spec.housing_length_mm, spec.housing_width_mm, spec.housing_height_mm, 'hold'),
        (L, spec.housing_width_mm, spec.housing_height_mm, 'smoothstep'),
    ]
    return [Station(x, w / 2, *_split(h, frac), kind) for x, w, h, kind in sections]


def stations_for(spec) -> list[Station]:
    """Control stations of a lofted style (teardrop / narrow_waist)."""
    if spec.style == 'teardrop':
        return teardrop_stations(spec)
    if spec.style == 'narrow_waist':
        return narrow_waist_stations(spec)
    raise ValueError(f'style {spec.style!r} has no lofted profile')


def _check_x(spec, x: float) -> None:
    if not 0 <= x <= spec.length_mm:
        raise ValueError(f'x={x} outside the body [0, {spec.length_mm}]')


def _wedge_top(spec, x: float) -> float:
    N, H, L = spec.nose_height_mm, spec.height_mm, spec.length_mm
    return N + (H - N) * min(2 * x / L, 1)


def _section(spec, x: float) -> tuple[float, float, float]:
    """``(a, s, c)`` at x for any style; the wedge is a flat-topped section (c=0)."""
    if spec.style == 'wedge':
        _check_x(spec, x)
        return spec.width_mm / 2, _wedge_top(spec, x), 0.0
    if spec.style in LOFTED_STYLES:
        return interpolate(stations_for(spec), x)
    raise ValueError(f'unknown style {spec.style!r}')


def half_width_at(spec, x: float) -> float:
    return _section(spec, x)[0]


def side_height_at(spec, x: float) -> float:
    """Lowest top ``z'`` across Y at x (the side height; the whole flat top for the wedge)."""
    return _section(spec, x)[1]


def top_at(spec, x: float, y: float) -> float:
    """Top surface ``z'`` (above the body bottom) at ``(x, y)``."""
    a, s, c = _section(spec, x)
    if spec.style == 'wedge':
        if abs(y) > a:
            raise ValueError(f'|y|={abs(y)} outside the half-width {a}')
        return s
    return crown_height_at(a, s, c, spec.crown_exponent, y)


def sample_xs(x0: float, x1: float, step: float = 0.25) -> list[float]:
    """Evenly spaced samples over ``[x0, x1]``, spacing <= step, both endpoints exact."""
    if not step > 0:
        raise ValueError('step must be positive')
    if not x0 <= x1:
        raise ValueError(f'empty interval [{x0}, {x1}]')
    count = max(1, math.ceil((x1 - x0) / step))
    xs = [x0 + (x1 - x0) * i / count for i in range(count + 1)]
    xs[-1] = x1
    return xs


def max_half_width_over(spec, x0: float, x1: float, step: float = 0.25) -> float:
    return max(half_width_at(spec, x) for x in sample_xs(x0, x1, step))


def min_half_width_over(spec, x0: float, x1: float, step: float = 0.25) -> float:
    return min(half_width_at(spec, x) for x in sample_xs(x0, x1, step))


def min_side_height_over(spec, x0: float, x1: float, step: float = 0.25) -> float:
    return min(side_height_at(spec, x) for x in sample_xs(x0, x1, step))
