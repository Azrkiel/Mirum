"""v2 narrow-waist style builder: the D1 narrow-waist law lofted by the D2 kernel, then pocket and bores.

CadQuery (and ``kernel``, which imports it) is imported lazily inside ``build`` so that the
registry stays importable without CAD (master plan invariant 6).
"""
from __future__ import annotations


def build(spec):
    """Return the narrow-waist body as one valid ``cq.Solid`` (frame: master plan invariant 8)."""
    if getattr(spec, 'style', None) != 'narrow_waist' or getattr(spec, 'schema_version', None) != 2:
        raise ValueError('narrow_waist builder expects a v2 narrow_waist spec')
    from .. import kernel, profiles

    stations = profiles.narrow_waist_stations(spec)
    body = kernel.loft_body(stations, spec.crown_exponent, spec.ground_clearance_mm, spec.length_mm)
    y_extent = max(st.a for st in stations)
    return kernel.cut_pocket_and_bores(
        body, length=spec.length_mm, pocket_diameter=spec.pocket_diameter_mm,
        pocket_depth=spec.pocket_depth_mm, pocket_center_z=spec.pocket_center_z_mm,
        bores=[(spec.front_axle_x_mm, spec.wheels.front_diameter_mm / 2, spec.axle_bore_diameter_mm),
               (spec.rear_axle_x_mm, spec.wheels.rear_diameter_mm / 2, spec.axle_bore_diameter_mm)],
        y_extent=y_extent)
