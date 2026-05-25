"""Parametric wing geometry — consumes numbers, makes zero engineering decisions."""
from __future__ import annotations
from typing import List, Optional
import numpy as np

try:
    import cadquery as cq
    from ..cad_utils import make_naca_wire
    CQ_AVAILABLE = True
except ImportError:
    CQ_AVAILABLE = False


def make_wing_oml(
    airfoil_pts: np.ndarray,
    chords: List[float],
    y_stations: List[float],
    twists_deg: List[float],
    dihedrals_deg: List[float],
) -> "cq.Workplane":
    """Loft wing OML from pre-computed airfoil + planform arrays."""
    if not CQ_AVAILABLE:
        raise ImportError("cadquery not available")
    wires = []
    for chord, y, twist, dih in zip(chords, y_stations, twists_deg, dihedrals_deg):
        z_offset = y * np.tan(np.radians(dih))
        pts = airfoil_pts * chord
        # Apply twist around quarter-chord
        qc = chord * 0.25
        angle_rad = np.radians(twist)
        pts_rot = pts.copy()
        pts_rot[:, 0] = (
            qc
            + (pts[:, 0] - qc) * np.cos(angle_rad)
            - pts[:, 1] * np.sin(angle_rad)
        )
        pts_rot[:, 1] = (
            (pts[:, 0] - qc) * np.sin(angle_rad)
            + pts[:, 1] * np.cos(angle_rad)
        )
        wire_pts = [
            (float(p[0]), float(y), float(p[1] + z_offset)) for p in pts_rot
        ]
        w = (
            cq.Workplane("XZ")
            .workplane(offset=y)
            .spline(wire_pts)
            .close()
        )
        wires.append(w)
    return cq.Workplane().add(*[w.val() for w in wires]).toPending().loft()


def make_spar(
    chord_frac: float,
    root_y: float,
    tip_y: float,
    root_chord: float,
    tip_chord: float,
    cap_area_root: float,
    cap_area_tip: float,
    web_t: float,
    t_c: float = 0.12,
) -> "cq.Solid":
    """Build a tapered I-beam spar solid between root_y and tip_y."""
    if not CQ_AVAILABLE:
        raise ImportError("cadquery not available")
    h_root = t_c * root_chord
    h_tip = t_c * tip_chord
    x_root = chord_frac * root_chord
    x_tip = chord_frac * tip_chord
    result = (
        cq.Workplane("XY")
        .transformed(offset=(x_root, root_y, 0))
        .rect(web_t, h_root)
        .workplane(offset=tip_y - root_y)
        .transformed(offset=(x_tip - x_root, 0, 0))
        .rect(web_t, h_tip)
        .loft()
    )
    return result.val()


def make_rib(
    y_station: float,
    airfoil_pts: np.ndarray,
    chord: float,
    front_spar_x: float,
    rear_spar_x: float,
    skin_t: float,
    web_t: float,
    t_c: float = 0.12,
    stringer_height: float = 0.015,
    rib_thickness: float = 0.002,
    lightening_hole_r: Optional[float] = None,
) -> "cq.Workplane":
    """Build a rib at y_station with spar cutouts and lightening hole."""
    if not CQ_AVAILABLE:
        raise ImportError("cadquery not available")
    pts = airfoil_pts * chord
    outer_pts = [(float(p[0]), float(p[1])) for p in pts]

    # Offset inner contour toward centroid by skin_t
    cx = float(pts[:, 0].mean())
    cy = float(pts[:, 1].mean())
    scale = 1 - skin_t / (chord * t_c + 1e-9)
    inner_pts = [
        (cx + (x - cx) * scale, cy + (y - cy) * scale)
        for x, y in outer_pts
    ]

    # Extrude rib web from inner contour
    rib = (
        cq.Workplane("XZ")
        .workplane(offset=y_station)
        .spline([(x, 0, z) for x, z in inner_pts])
        .close()
        .extrude(rib_thickness)
    )

    # Boolean-cut spar openings
    for spar_x in [front_spar_x, rear_spar_x]:
        spar_cut = (
            cq.Workplane("XZ")
            .workplane(offset=y_station - rib_thickness)
            .rect(web_t * 2, chord * t_c * 1.2)
            .transformed(offset=(spar_x, 0, 0))
            .extrude(rib_thickness * 3)
        )
        rib = rib.cut(spar_cut)

    # Lightening hole
    if lightening_hole_r is None:
        lightening_hole_r = (rear_spar_x - front_spar_x) * 0.25
    hole_x = (front_spar_x + rear_spar_x) / 2
    hole = (
        cq.Workplane("XZ")
        .workplane(offset=y_station - rib_thickness)
        .circle(lightening_hole_r)
        .transformed(offset=(hole_x, 0, 0))
        .extrude(rib_thickness * 3)
    )
    rib = rib.cut(hole)
    return rib


def make_stringer(
    profile_type: str,
    path_y: np.ndarray,
    x_pos: float,
    z_pos: float,
    t: float = 0.002,
    h: float = 0.020,
) -> "cq.Workplane":
    """Extrude a stringer (L or Z section) along the spanwise path."""
    if not CQ_AVAILABLE:
        raise ImportError("cadquery not available")
    y_start, y_end = float(path_y[0]), float(path_y[-1])
    if profile_type.upper() == "L":
        profile = (
            cq.Workplane("XZ")
            .polyline([(0, 0), (h, 0), (h, t), (t, t), (t, h), (0, h)])
            .close()
        )
    else:  # Z or hat — simplified rectangular web
        profile = cq.Workplane("XZ").rect(t, h)
    stringer = (
        profile
        .transformed(offset=(x_pos, 0, z_pos))
        .workplane()
        .extrude(y_end - y_start)
    )
    return stringer
