"""Parametric wing geometry consumer — Module 8 (CadQuery)."""
from __future__ import annotations
import math
from typing import Sequence

try:
    import cadquery as cq
    _CQ_AVAILABLE = True
except ImportError:
    _CQ_AVAILABLE = False
    cq = None  # type: ignore


def _require_cq():
    if not _CQ_AVAILABLE:
        raise ImportError("cadquery is required for wing_geometry. Install it in the cadenv conda environment.")


def make_wing_oml(
    airfoil_pts: list[tuple[float, float]],   # normalised profile (chord=1)
    chords: list[float],                        # chord at each spanwise station (m)
    y_stations: list[float],                    # spanwise y positions (m)
    twists_deg: list[float],                    # geometric twist at each station (deg)
    dihedrals_deg: list[float],                 # dihedral at each station (deg)
) -> "cq.Workplane":
    """
    Loft a wing OML from airfoil cross-sections.
    All lists must have the same length.
    """
    _require_cq()
    if not (len(chords) == len(y_stations) == len(twists_deg) == len(dihedrals_deg)):
        raise ValueError("All input lists must have the same length.")

    sections = []
    for c, y, twist, dih in zip(chords, y_stations, twists_deg, dihedrals_deg):
        # Scale airfoil to chord
        scaled = [(x * c, z * c) for x, z in airfoil_pts]
        # Apply twist (rotate about LE, positive = nose up)
        tw_rad = math.radians(twist)
        rotated = [
            (x * math.cos(tw_rad) - z * math.sin(tw_rad),
             x * math.sin(tw_rad) + z * math.cos(tw_rad))
            for x, z in scaled
        ]
        # Build 3D wire at spanwise station y (xz plane, translated in y)
        dih_rad = math.radians(dih)
        z_offset = y * math.tan(dih_rad)
        pts_3d = [(x, y, z + z_offset) for x, z in rotated]
        wire = (
            cq.Workplane("XZ")
            .moveTo(pts_3d[0][0], pts_3d[0][2])
        )
        for px, py, pz in pts_3d[1:]:
            wire = wire.lineTo(px, pz)
        wire = wire.close()
        section = wire.val().translate((0, y, z_offset))
        sections.append(section)

    # Loft through all sections
    result = cq.Workplane("XY")
    for sec in sections:
        result = result.add(sec)
    return result.shell(0)  # placeholder — full loft requires cq.Solid.makeLoft


def make_spar(
    chord_frac: float,          # x/c location of spar (e.g. 0.25 or 0.65)
    root_y: float,              # root y station m
    tip_y: float,               # tip y station m
    root_chord: float,          # m
    tip_chord: float,           # m
    airfoil_t_c: float,         # thickness/chord
    spar_t: float,              # web thickness m
    cap_area: float,            # boom area m² (each cap)
    cap_width: float | None = None,  # if None, derived from cap_area assuming square
) -> "cq.Solid":
    """Box spar web from root to tip."""
    _require_cq()
    cap_w = cap_width or math.sqrt(cap_area)
    cap_h = cap_area / cap_w

    root_h = airfoil_t_c * root_chord
    tip_h = airfoil_t_c * tip_chord
    root_x = chord_frac * root_chord
    tip_x = chord_frac * tip_chord

    # Build as swept I-section placeholder — simple box for now
    root_face = (
        cq.Workplane("XZ")
        .rect(spar_t, root_h)
        .extrude(0)
        .translate((root_x, root_y, 0))
    )
    tip_face = (
        cq.Workplane("XZ")
        .rect(spar_t, tip_h)
        .extrude(0)
        .translate((tip_x, tip_y, 0))
    )
    # Loft web
    spar = cq.Workplane("XY").add(root_face.val()).add(tip_face.val())
    return spar.val()


def make_rib(
    y_station: float,
    airfoil_pts: list[tuple[float, float]],
    chord: float,
    front_spar_x: float,        # absolute x from LE (m)
    rear_spar_x: float,
    skin_t: float,
    web_t: float,
    flange_w: float = 0.015,    # flange width m
    lightening_hole_r: float | None = None,  # if None, auto-sized
    num_lightening: int = 3,
) -> "cq.Workplane":
    """
    Rib at y_station. Offset the airfoil contour inward by skin_t,
    cut spar openings, add flanges, cut mouse holes, add lightening holes.
    """
    _require_cq()
    # Scale airfoil to chord
    scaled = [(x * chord, z * chord) for x, z in airfoil_pts]
    # Inner offset (shrink by skin_t) — approximate by scaling
    inner_scale = (chord - 2 * skin_t) / chord
    inner = [(x * inner_scale, z * inner_scale) for x, z in airfoil_pts]

    # Build outer contour wire
    outer_wire = cq.Workplane("XZ").polyline([(x, z) for x, z in scaled]).close()
    inner_wire = cq.Workplane("XZ").polyline([(x, z) for x, z in inner]).close()
    rib_face = outer_wire.extrude(web_t).cut(inner_wire.extrude(web_t))

    # Spar cutouts (rectangles through full height)
    front_cut = (
        cq.Workplane("XZ")
        .rect(flange_w * 2, chord * 0.15)
        .extrude(web_t)
        .translate((front_spar_x, 0, 0))
    )
    rear_cut = (
        cq.Workplane("XZ")
        .rect(flange_w * 2, chord * 0.15)
        .extrude(web_t)
        .translate((rear_spar_x, 0, 0))
    )
    rib_face = rib_face.cut(front_cut).cut(rear_cut)

    # Lightening holes
    if lightening_hole_r is None:
        lightening_hole_r = (rear_spar_x - front_spar_x) / (3 * num_lightening)

    box_width = rear_spar_x - front_spar_x
    for i in range(num_lightening):
        hx = front_spar_x + box_width * (i + 1) / (num_lightening + 1)
        hole = (
            cq.Workplane("XZ")
            .circle(lightening_hole_r)
            .extrude(web_t)
            .translate((hx, 0, 0))
        )
        rib_face = rib_face.cut(hole)

    return rib_face.translate((0, y_station, 0))


def make_stringer(
    profile_type: str,          # "L", "J", "Z", "hat"
    path_y: tuple[float, float],  # (root_y, tip_y)
    x_pos: float,               # chordwise x position m
    z_pos: float,               # vertical z position m
    t: float,                   # thickness m
    h: float,                   # height m
) -> "cq.Workplane":
    """Simple angle/hat stringer extruded along span."""
    _require_cq()
    root_y, tip_y = path_y
    length = tip_y - root_y

    if profile_type.upper() == "L":
        profile = (
            cq.Workplane("XZ")
            .polyline([(0, 0), (h, 0), (h, t), (t, t), (t, h), (0, h), (0, 0)])
            .close()
        )
    elif profile_type.upper() in ("J", "Z"):
        profile = (
            cq.Workplane("XZ")
            .polyline([(0, 0), (h * 0.5, 0), (h * 0.5, t),
                       (t, t), (t, h - t), (h * 0.5 + t, h - t),
                       (h * 0.5 + t, h), (0, h), (0, 0)])
            .close()
        )
    else:  # hat
        w2 = h * 0.4
        profile = (
            cq.Workplane("XZ")
            .polyline([(0, 0), (h, 0), (h + w2, t), (h + w2, t + h * 0.5),
                       (h, t + h * 0.5), (t, t + h * 0.5), (0, t + h * 0.5),
                       (-w2, t), (-w2, 0), (0, 0)])
            .close()
        )
    return (
        profile.extrude(length)
        .translate((x_pos, root_y, z_pos))
    )
