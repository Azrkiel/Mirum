"""D2 crown probe: spline vs polyline crown, and OCC volume integration methods.

Why the kernel's crown is a polyline. Run inside the verify image, one case per process
(the narrow-waist spline/smooth case hangs in volume integration, so use a timeout):

    timeout 120 python docs/plans/co2-dragster/evidence/tools/d2_crown_probe.py <case> <crown> <ruled>

    case  = const | teardrop | narrow_waist      crown = spline | polyline      ruled = 0 | 1

Prints the loft volume error vs the law for: the default ``Shape.Volume()`` (what STEP
verification uses), adaptive integration (``computeMass(tol=1e-6)``) and a fine mesh volume.
17 crown points, 5 mm step (the spike's initial settings).
"""
import math
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(REPO), str(REPO / 'backend')]

import cadquery as cq  # noqa: E402
import numpy as np  # noqa: E402

from backend.dragsters.kernel import section_xs  # noqa: E402
from backend.dragsters.profiles import Station, crown_height_at, interpolate, law_volume, stations_for  # noqa: E402
from backend.dragsters.styles import load_preset  # noqa: E402


def wire(x, a, s, c, n, g, m, crown):
    pts = []
    for k in range(m):
        y = a if k == 0 else (-a if k == m - 1 else a * math.cos(math.pi * k / (m - 1)))
        pts.append(cq.Vector(x, y, g + crown_height_at(a, s, c, n, y)))
    edges = [cq.Edge.makeLine(cq.Vector(x, -a, g), cq.Vector(x, a, g)),
             cq.Edge.makeLine(cq.Vector(x, a, g), cq.Vector(x, a, g + s))]
    if crown == 'spline':
        edges.append(cq.Edge.makeSpline(pts, tangents=[cq.Vector(0, 0, 1), cq.Vector(0, 0, -1)]))
    else:
        edges += [cq.Edge.makeLine(p, q) for p, q in zip(pts, pts[1:])]
    edges.append(cq.Edge.makeLine(cq.Vector(x, -a, g + s), cq.Vector(x, -a, g)))
    return cq.Wire.assembleEdges(edges)


def mesh_volume(shape, tol=0.01):
    vs, ts = shape.tessellate(tol, 0.05)
    v, t = np.array([p.toTuple() for p in vs]), np.array(ts)
    return abs(np.einsum('ij,ij->i', v[t[:, 0]], np.cross(v[t[:, 1]], v[t[:, 2]])).sum() / 6)


def main():
    case, crown, ruled = sys.argv[1], sys.argv[2], sys.argv[3] == '1'
    if case == 'const':
        stations, n, g = [Station(0, 18.0, 12.0, 9.0, 'hold'), Station(150.0, 18.0, 12.0, 9.0)], 3.0, 3.0
    else:
        spec = load_preset(case)
        stations, n, g = stations_for(spec), spec.crown_exponent, spec.ground_clearance_mm
    t0 = time.perf_counter()
    loft = cq.Solid.makeLoft([wire(x, *interpolate(stations, x), n, g, 17, crown)
                              for x in section_xs(stations, 5.0)], ruled)
    print(f'{case} {crown} ruled={ruled} valid={loft.isValid()} loft {time.perf_counter() - t0:.2f}s', flush=True)
    law = law_volume(stations, n)
    for name, fn in (('default', lambda: loft.Volume()), ('mesh', lambda: mesh_volume(loft)),
                     ('adaptive', lambda: cq.Shape.computeMass(loft, 1e-6))):
        t0 = time.perf_counter()
        v = fn()
        print(f'  {name:8s} {(v - law) / law:+.4%} ({time.perf_counter() - t0:.2f}s)', flush=True)


if __name__ == '__main__':
    main()
