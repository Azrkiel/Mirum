"""Lofted-body CAD kernel: profile-law sections, loft, pocket and bore cuts (CadQuery).

The profile law itself (stations, interpolation, crown height) lives in ``profiles``; this
module only renders it. Validation must never import this module (master plan invariant 5).
Frame: mm, X nose -> rear (nose at X=0), Y symmetric about 0, Z up, body bottom at ``ground``.
"""
from __future__ import annotations

import math
from typing import Sequence

import cadquery as cq

from .profiles import Station, crown_height_at, interpolate
from .tolerances import CROWN_POINTS, LOFT_RULED, STATION_STEP_MM


def _one_valid_solid(shape: cq.Shape, what: str) -> cq.Solid:
    solids = shape.Solids()
    if len(solids) != 1:
        raise ValueError(f'{what} produced {len(solids)} solids, expected exactly one')
    solid = solids[0]
    volume = solid.Volume()
    if not (shape.isValid() and solid.isValid()) or not math.isfinite(volume) or volume <= 0:
        raise ValueError(f'{what} did not produce one valid positive-volume solid')
    return solid


def section_wire(x: float, a: float, s: float, c: float, n: float, ground: float,
                 crown_points: int = CROWN_POINTS) -> cq.Wire:
    """Closed flat-bottom D-section in the plane X=x.

    Edges, always in this order and starting at ``(x, -a, ground)``: bottom line, right side
    line, ``crown_points - 1`` crown chords from ``y=+a`` to ``y=-a`` through law points
    (``z = ground + crown_height_at(...)``, cosine spacing in y so points cluster near the
    vertical ends), left side line. Every section of a law therefore has ``crown_points + 2``
    edges and the same start vertex. ``crown_points`` must be odd so the apex ``y=0`` is a
    vertex (exact top bound).

    The crown is a polyline, not a spline, by measurement (D2 spike): OCC's default volume
    integration is off by up to 4 % on lofts of spline crowns and its adaptive integration
    hangs on the narrow-waist preset, while polyline lofts integrate consistently.
    """
    values = (x, a, s, c, n, ground)
    if not all(math.isfinite(v) for v in values):
        raise ValueError('section values must be finite')
    if not (a > 0 and s > 0 and c > 0 and n > 0):
        raise ValueError(f'section needs a, s, c, n > 0 (got a={a}, s={s}, c={c}, n={n})')
    if crown_points < 3 or crown_points % 2 == 0:
        raise ValueError('crown_points must be an odd integer >= 3')
    top = ground + s
    crown = []
    for k in range(crown_points):
        if k == 0:
            y = a
        elif k == crown_points - 1:
            y = -a
        elif 2 * k == crown_points - 1:
            y = 0.0
        else:
            y = a * math.cos(math.pi * k / (crown_points - 1))
        crown.append(cq.Vector(x, y, ground + crown_height_at(a, s, c, n, y)))
    edges = [
        cq.Edge.makeLine(cq.Vector(x, -a, ground), cq.Vector(x, a, ground)),
        cq.Edge.makeLine(cq.Vector(x, a, ground), cq.Vector(x, a, top)),
        *(cq.Edge.makeLine(p, q) for p, q in zip(crown, crown[1:])),
        cq.Edge.makeLine(cq.Vector(x, -a, top), cq.Vector(x, -a, ground)),
    ]
    wire = cq.Wire.assembleEdges(edges)
    if not wire.IsClosed():
        raise ValueError('section wire is not closed')
    return wire


def section_xs(stations: Sequence[Station], step_mm: float = STATION_STEP_MM) -> list[float]:
    """Section positions: every station x plus a uniform grid inside each station interval.

    Each interval ``[x_i, x_i+1]`` gets ``ceil(len / step_mm)`` equal slabs, so spacing is
    ``<= step_mm`` everywhere and no sliver slab appears next to a station.
    """
    if not (math.isfinite(step_mm) and step_mm > 0):
        raise ValueError('step_mm must be positive')
    xs = [stations[0].x]
    for lo, hi in zip(stations, stations[1:]):
        span = hi.x - lo.x
        if not span > 0:
            raise ValueError('station x values must be strictly increasing')
        count = max(1, math.ceil(span / step_mm - 1e-9))
        xs.extend(lo.x + span * i / count for i in range(1, count))
        xs.append(hi.x)
    return xs


def loft_body(stations: Sequence[Station], n: float, ground: float, length: float, *,
              step_mm: float = STATION_STEP_MM, ruled: bool = LOFT_RULED,
              crown_points: int = CROWN_POINTS) -> cq.Solid:
    """Loft the profile law into one closed solid from X=0 to X=length."""
    if not stations or stations[0].x != 0 or abs(stations[-1].x - length) > 1e-9:
        raise ValueError(f'stations must span [0, {length}]')
    wires = [section_wire(x, *interpolate(stations, x), n, ground, crown_points)
             for x in section_xs(stations, step_mm)]
    try:
        loft = cq.Solid.makeLoft(wires, ruled)
    except Exception as exc:  # OCC StdFail / Standard_Failure
        raise ValueError(f'loft failed: {exc}') from exc
    return _one_valid_solid(loft, 'loft')


def cut_pocket_and_bores(body: cq.Shape, *, length: float, pocket_diameter: float,
                         pocket_depth: float, pocket_center_z: float,
                         bores: Sequence[tuple[float, float, float]], y_extent: float) -> cq.Solid:
    """Cut the rear-open cartridge pocket and each axle bore ``(x, z, diameter)`` along Y.

    All tools go into one boolean (same result as v1's sequential cuts, about half the time on
    ~800-face lofts; D2 spike). The kernel does not judge walls or containment (D5 does): a bore
    outside the body is a volume no-op. The result must still be exactly one valid solid.
    """
    L, D = length, pocket_depth
    pocket = cq.Solid.makeCylinder(pocket_diameter / 2, D + 1, cq.Vector(L - D, 0, pocket_center_z),
                                   cq.Vector(1, 0, 0))
    tools = [pocket] + [cq.Solid.makeCylinder(d / 2, 2 * y_extent + 2, cq.Vector(x, -y_extent - 1, z),
                                              cq.Vector(0, 1, 0)) for x, z, d in bores]
    return _one_valid_solid(body.cut(*tools), 'pocket/bore cut')
