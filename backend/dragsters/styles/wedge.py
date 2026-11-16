"""v2 wedge style builder: the v1 planar wedge with per-axle bore heights.

CadQuery (and ``kernel``, which imports it) is imported lazily inside ``build`` so that the
registry stays importable without CAD (master plan invariant 6).
"""
from __future__ import annotations


def build(spec):
    """Return the wedge body as one valid ``cq.Solid`` (frame: master plan invariant 8).

    Same outline as v1 ``geometry.build_body`` (constructed here, not imported), then the shared
    kernel cuts: pocket, and bores at ``z = front_d/2`` and ``z = rear_d/2``.
    """
    if getattr(spec, 'style', None) != 'wedge' or getattr(spec, 'schema_version', None) != 2:
        raise ValueError('wedge builder expects a v2 wedge spec')
    import cadquery as cq

    from ..kernel import cut_pocket_and_bores

    L, W, H, N, g = (spec.length_mm, spec.width_mm, spec.height_mm,
                     spec.nose_height_mm, spec.ground_clearance_mm)
    points = [(0, -W / 2, g), (L, -W / 2, g), (L, -W / 2, g + H),
              (L / 2, -W / 2, g + H), (0, -W / 2, g + N), (0, -W / 2, g)]
    wire = cq.Wire.makePolygon([cq.Vector(*p) for p in points])
    body = cq.Solid.extrudeLinear(wire, [], cq.Vector(0, W, 0))
    bore_d = spec.axle_bore_diameter_mm
    bores = [(spec.front_axle_x_mm, spec.wheels.front_diameter_mm / 2, bore_d),
             (spec.rear_axle_x_mm, spec.wheels.rear_diameter_mm / 2, bore_d)]
    return cut_pocket_and_bores(body, length=L, pocket_diameter=spec.pocket_diameter_mm,
                                pocket_depth=spec.pocket_depth_mm,
                                pocket_center_z=spec.pocket_center_z_mm, bores=bores, y_extent=W / 2)
