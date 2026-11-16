"""Fixed planar wedge and explicit-axis cylindrical cuts."""
from __future__ import annotations

import math
import cadquery as cq

from .spec import DragsterSpec


def build_body(spec: DragsterSpec) -> cq.Shape:
    L, W, H, N, g = (spec.length_mm, spec.width_mm, spec.height_mm,
                      spec.nose_height_mm, spec.ground_clearance_mm)
    rp, D, zp = spec.pocket_diameter_mm / 2, spec.pocket_depth_mm, spec.pocket_center_z_mm
    ra, za = spec.axle_bore_diameter_mm / 2, spec.axle_z_mm
    points = [(0, -W/2, g), (L, -W/2, g), (L, -W/2, g+H),
              (L/2, -W/2, g+H), (0, -W/2, g+N), (0, -W/2, g)]
    wire = cq.Wire.makePolygon([cq.Vector(*p) for p in points])
    body = cq.Solid.extrudeLinear(wire, [], cq.Vector(0, W, 0))
    pocket = cq.Solid.makeCylinder(rp, D + 1, cq.Vector(L-D, 0, zp), cq.Vector(1, 0, 0))
    body = body.cut(pocket)
    for x in (spec.front_axle_x_mm, spec.rear_axle_x_mm):
        bore = cq.Solid.makeCylinder(ra, W + 2, cq.Vector(x, -W/2-1, za), cq.Vector(0, 1, 0))
        body = body.cut(bore)
    volume = body.Volume()
    if not body.isValid() or len(body.Solids()) != 1 or not math.isfinite(volume) or volume <= 0:
        raise ValueError('Wedge construction did not produce one valid positive-volume solid')
    return body
