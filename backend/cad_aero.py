"""Deterministic OML wing builders for Domain D (aerospace).

This module is **sandbox-allowlisted** (`backend/compiler.py` `_ALLOWED_MODULES`):
any LLM-generated CadQuery script may `import cad_aero`. Therefore it MUST import
nothing dangerous — only `cadquery`, `math`, and the sibling allowlisted
`cad_utils`. No `os` / `subprocess` / `open` / `eval` / file IO, ever.

Why this module exists
----------------------
Domain-D geometry was previously authored free-form by the Machinist, so the
same prompt could yield different shapes. These builders are **pure functions of
their keyword arguments** — same args in, identical topology out — so the
Machinist composes a wing by *parameterising* curated builders instead of
emitting raw B-rep code.

Frozen coordinate frame (Hole #2)
---------------------------------
Every builder in this module obeys ONE coordinate convention. No builder takes
its own independent frame. This is what lets parts from different builders mate.

    * X = chordwise   (leading edge → trailing edge, +X aft)
    * Y = spanwise    (root → tip, +Y outboard)
    * Z = thickness   (lower surface → upper surface, +Z up)
    * Origin          = root-section leading edge
    * Dihedral        = vertical offset of a station: z += y * tan(dihedral)
    * Twist (washout) = rotation of a section about its QUARTER-CHORD point
                        (x/c = 0.25), linear from 0 at the root to
                        ``twist_tip_deg`` at the tip. Positive = leading edge up.

Determinism target (Hole #4)
----------------------------
"Same prompt → same topology + part count + mass within epsilon" — NOT byte-equal
STEP. OCC loft / B-spline output is not guaranteed bit-identical across OCC
versions or platforms, so tests assert geometric *invariants*, never file hashes.
"""

from __future__ import annotations

import math

import cadquery as cq

import cad_utils  # sibling allowlisted module — reuse make_naca_wire (OML B-spline)

# Documented, machine-readable form of the frozen frame above (see Hole #2).
_FRAME = {
    "span_axis": "Y",
    "chord_axis": "X",
    "thickness_axis": "Z",
    "origin": "root-section leading edge",
    "dihedral": "z += y * tan(dihedral_deg)",
    "twist_axis": "quarter-chord (x/c=0.25), linear root->tip, +deg = LE up",
}

# Bounding-box sanity envelope: no single dimension of any builder output may
# exceed this. A runaway loft / NaN coordinate trips it loudly.
_MAX_EXTENT_M = 50.0
_MIN_VOLUME_M3 = 1e-9


class CadAeroError(ValueError):
    """A builder produced a missing, invalid, degenerate, or runaway solid."""


def _validate_solid(
    obj,
    name: str,
    *,
    max_extent_m: float = _MAX_EXTENT_M,
    min_vol_m3: float = _MIN_VOLUME_M3,
):
    """Fail loudly unless ``obj`` is a valid, positive-volume, sane-sized solid.

    Mirrors the four geometry-certificate checks (`backend/verification/geometry.py`)
    using the cadquery/OCP API: is-solid + BRepCheck-valid + volume>0 + finite
    bounding box within the envelope. Returns ``obj`` unchanged on success so it
    can be used inline: ``return _validate_solid(wp, "spar")``.
    """
    shape = obj.val() if hasattr(obj, "val") else obj
    if shape is None:
        raise CadAeroError(f"{name}: builder returned None")
    if not shape.isValid():
        raise CadAeroError(f"{name}: invalid B-rep (OCC BRepCheck failed)")
    vol = shape.Volume()
    if vol < min_vol_m3:
        raise CadAeroError(f"{name}: zero/negative volume ({vol:.3e} m^3)")
    bb = shape.BoundingBox()
    extent = max(bb.xlen, bb.ylen, bb.zlen)
    if not math.isfinite(extent) or extent > max_extent_m:
        raise CadAeroError(
            f"{name}: bounding box out of envelope "
            f"({bb.xlen:.3f}, {bb.ylen:.3f}, {bb.zlen:.3f}) m > {max_extent_m} m"
        )
    return obj


def _dedupe_pts(pts, tol: float = 1e-7):
    """Drop consecutive duplicate (x, z) points, including the wrap-around dup.

    NACA point lists frequently carry a doubled leading-edge point and a
    first==last closure point; both break wire construction if left in.
    """
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p[:2], out[-1][:2]) > tol:
            out.append(p)
    if len(out) > 1 and math.dist(out[0][:2], out[-1][:2]) < tol:
        out = out[:-1]
    return out


# ===========================================================================
# A1 — trailing-edge cusp recipe
# ===========================================================================
# Three candidate strategies for turning an airfoil contour into a clean
# extruded solid. The raw ``polyline(points).close().extrude(t)`` path produces
# an invalid B-rep at the TE cusp on NACA 4-digit profiles; these are the fixes.
# The empirical 3x3 matrix in test_te_cusp.py selects the winner; the losers stay
# as callable escape-hatch values.
TE_METHODS = ("trim", "loft", "spline")
# Selected empirically (see test_te_cusp.py::test_default_is_the_empirical_winner).
# The B-spline path mirrors the production-proven cad_utils.make_naca_wire.
_DEFAULT_TE_METHOD = "spline"

_TE_TRIM_FRAC = 0.995  # "trim" method: drop contour points past this x/chord


def naca4_points(naca_code: str, chord_m: float = 1.0, n: int = 80):
    """Analytic NACA 4-digit contour as ordered (x, z) points in METERS.

    Mirrors the thickness/camber math of ``cad_utils.make_naca_wire`` (modified
    -0.1036 x^4 term → closed TE) but emits a point LIST in the X-Z plane (X
    chordwise 0→chord, Z thickness), ordered TE→upper→LE→lower→TE. This is the
    shape a builder receives as ``airfoil_points``.
    """
    if len(naca_code) != 4 or not naca_code.isdigit():
        raise CadAeroError(f"naca4_points: '{naca_code}' must be 4 digits")
    m = int(naca_code[0]) / 100.0
    p = int(naca_code[1]) / 10.0
    t = int(naca_code[2:4]) / 100.0

    upper, lower = [], []
    for i in range(n + 1):
        beta = i * math.pi / n
        x = (1.0 - math.cos(beta)) / 2.0
        yt = (t / 0.20) * (
            0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x * x
            + 0.2843 * x * x * x - 0.1036 * x * x * x * x
        )
        if m == 0.0 or p == 0.0:
            yc, theta = 0.0, 0.0
        else:
            if x < p:
                yc = (m / (p * p)) * (2.0 * p * x - x * x)
                dyc = (2.0 * m / (p * p)) * (p - x)
            else:
                yc = (m / ((1.0 - p) ** 2)) * ((1.0 - 2.0 * p) + 2.0 * p * x - x * x)
                dyc = (2.0 * m / ((1.0 - p) ** 2)) * (p - x)
            theta = math.atan(dyc)
        st, ct = math.sin(theta), math.cos(theta)
        upper.append(((x - yt * st) * chord_m, (yc + yt * ct) * chord_m))
        lower.append(((x + yt * st) * chord_m, (yc - yt * ct) * chord_m))

    # TE → LE (upper, reversed) then LE → TE (lower) — a single closed loop.
    contour = list(reversed(upper)) + lower
    return _dedupe_pts(contour)


def _le_index(vecs) -> int:
    """Index of the leading-edge point (minimum chordwise X)."""
    return min(range(len(vecs)), key=lambda i: vecs[i].x)


def _closed_section_wire(points_xz, te_method: str) -> cq.Wire:
    """Build a closed airfoil wire in the X-Z plane (at Y=0) per ``te_method``."""
    pts = _dedupe_pts(points_xz)
    vecs = [cq.Vector(float(x), 0.0, float(z)) for (x, z) in pts]

    if te_method == "spline":
        # Two B-spline edges (TE→LE upper, LE→TE lower) sharing LE+TE endpoints —
        # the production-proven make_naca_wire construction.
        # _dedupe_pts strips the wrap-around TE point, so re-close the loop
        # explicitly: upper.start and lower.end must be the SAME TE vertex or the
        # assembled wire is open and the extruded solid fails BRepCheck.
        loop = list(vecs)
        if math.dist((loop[0].x, loop[0].z), (loop[-1].x, loop[-1].z)) > 1e-9:
            loop.append(loop[0])
        le = _le_index(loop)
        upper_edge = cq.Edge.makeSpline(loop[: le + 1])
        lower_edge = cq.Edge.makeSpline(loop[le:])
        return cq.Wire.assembleEdges([upper_edge, lower_edge])

    if te_method == "trim":
        # Drop the thin cusp sliver past 0.995c; straight close across the TE.
        x_max = max(v.x for v in vecs)
        kept = [v for v in vecs if v.x <= _TE_TRIM_FRAC * x_max]
        return cq.Wire.makePolygon(kept, close=True)

    if te_method == "loft":
        # Full polygon wire; the loft (two of these offset in Y) sidesteps the
        # planar-polygon → solid promotion that trips on the cusp.
        return cq.Wire.makePolygon(vecs, close=True)

    raise CadAeroError(
        f"unknown te_method {te_method!r}; valid: {TE_METHODS}"
    )


def extrude_airfoil_section(
    points_xz,
    thickness_m: float,
    *,
    te_method: str = _DEFAULT_TE_METHOD,
    validate: bool = True,
):
    """Extrude an airfoil contour into a solid of the given spanwise thickness.

    The contour lies in the X-Z plane; extrusion is along +Y (spanwise) by
    ``thickness_m``. ``te_method`` selects the trailing-edge recipe (default:
    the empirically-chosen winner). Returns a ``cq.Workplane``.
    """
    if te_method not in TE_METHODS:
        raise CadAeroError(f"unknown te_method {te_method!r}; valid: {TE_METHODS}")
    if thickness_m <= 0:
        raise CadAeroError(f"thickness_m must be > 0, got {thickness_m}")

    wire = _closed_section_wire(points_xz, te_method)
    span = cq.Vector(0.0, float(thickness_m), 0.0)

    if te_method == "loft":
        w0 = wire
        w1 = wire.translate(span)
        solid = cq.Solid.makeLoft([w0, w1], ruled=True)
    else:
        solid = cq.Solid.extrudeLinear(wire, [], span)

    wp = cq.Workplane("XY").newObject([solid])
    if validate:
        return _validate_solid(wp, f"airfoil_section[{te_method}]")
    return wp


# ===========================================================================
# A2 — the six OML builders
# ===========================================================================
# Shared frame helpers (all builders obey the frozen frame, see _FRAME).
def _chord_at(y: float, span_m: float, root_chord_m: float, tip_chord_m: float) -> float:
    f = 0.0 if span_m == 0 else (y / span_m)
    return root_chord_m + (tip_chord_m - root_chord_m) * f


def _dihedral_z(y: float, dihedral_deg: float) -> float:
    return y * math.tan(math.radians(dihedral_deg))


def _twist_phi(y: float, span_m: float, twist_tip_deg: float) -> float:
    f = 0.0 if span_m == 0 else (y / span_m)
    return math.radians(twist_tip_deg * f)


def _twist_xz(x: float, z: float, xq: float, phi: float):
    """Rotate a chord-plane point about the quarter-chord (x/c=0.25); +phi=LE up."""
    dx, dz = x - xq, z
    c, s = math.cos(phi), math.sin(phi)
    return (xq + dx * c + dz * s, -dx * s + dz * c)


def _surfaces(points_norm):
    """Split a TE→upper→LE→lower→TE contour into (upper, lower) surfaces,
    each a list of (x, z) sorted by ascending x (normalised)."""
    pts = _dedupe_pts(points_norm)
    le = min(range(len(pts)), key=lambda i: pts[i][0])
    upper = sorted(pts[: le + 1], key=lambda p: p[0])
    lower = sorted(pts[le:], key=lambda p: p[0])
    return upper, lower


def _interp_z(surf, x: float) -> float:
    """Linear-interpolate the z of a surface (sorted by x) at chord fraction x."""
    if x <= surf[0][0]:
        return surf[0][1]
    if x >= surf[-1][0]:
        return surf[-1][1]
    for i in range(1, len(surf)):
        x0, z0 = surf[i - 1]
        x1, z1 = surf[i]
        if x0 <= x <= x1:
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            return z0 + (z1 - z0) * t
    return surf[-1][1]


def _oml_section_wire(points_norm, chord_m, y, z0, phi, te_method="spline") -> cq.Wire:
    """Closed airfoil OML wire at spanwise station ``y``: normalised points scaled
    to ``chord_m``, twisted about the quarter-chord by ``phi`` rad, dihedral-offset
    to ``z0``. Built as two B-spline edges (the proven cusp-clean construction)."""
    xq = 0.25 * chord_m
    vecs = []
    for (xn, zn) in _dedupe_pts(points_norm):
        x, z = xn * chord_m, zn * chord_m
        xr, zr = _twist_xz(x, z, xq, phi)
        vecs.append(cq.Vector(xr, y, z0 + zr))
    if math.dist((vecs[0].x, vecs[0].z), (vecs[-1].x, vecs[-1].z)) > 1e-9:
        vecs.append(vecs[0])
    le = _le_index(vecs)
    upper = cq.Edge.makeSpline(vecs[: le + 1])
    lower = cq.Edge.makeSpline(vecs[le:])
    return cq.Wire.assembleEdges([upper, lower])


def _wp(solid):
    return cq.Workplane("XY").newObject([solid])


def make_spar(
    *,
    airfoil_points,
    span_m: float,
    root_chord_m: float,
    tip_chord_m: float,
    x_frac: float,
    web_thickness_m: float,
    twist_tip_deg: float = 0.0,
    dihedral_deg: float = 0.0,
    n_stations: int = 7,
) -> cq.Workplane:
    """Shear-web spar at chord fraction ``x_frac``, root→tip, filling the local
    OML section height. Rectangular web sections lofted across ``n_stations``."""
    upper, lower = _surfaces(airfoil_points)
    wires = []
    for i in range(n_stations):
        y = span_m * i / (n_stations - 1)
        chord = _chord_at(y, span_m, root_chord_m, tip_chord_m)
        z0 = _dihedral_z(y, dihedral_deg)
        phi = _twist_phi(y, span_m, twist_tip_deg)
        xc = x_frac * chord
        z_up = _interp_z(upper, x_frac) * chord
        z_lo = _interp_z(lower, x_frac) * chord
        xq = 0.25 * chord
        corners = []
        for (cx, cz) in (
            (xc - web_thickness_m / 2, z_lo),
            (xc + web_thickness_m / 2, z_lo),
            (xc + web_thickness_m / 2, z_up),
            (xc - web_thickness_m / 2, z_up),
        ):
            xr, zr = _twist_xz(cx, cz, xq, phi)
            corners.append(cq.Vector(xr, y, z0 + zr))
        wires.append(cq.Wire.makePolygon(corners, close=True))
    solid = cq.Solid.makeLoft(wires, ruled=False)
    return _validate_solid(_wp(solid), f"spar@{x_frac:.2f}")


def make_rib(
    *,
    airfoil_points,
    chord_m: float,
    y_station_m: float,
    thickness_m: float,
    front_spar_x_frac: float,
    rear_spar_x_frac: float,
    n_lightening_holes: int = 3,
    twist_deg: float = 0.0,
    use_oml_outline: bool = True,
    te_method: str = _DEFAULT_TE_METHOD,
) -> cq.Workplane:
    """True-OML rib at spanwise ``y_station_m``, ``thickness_m`` thick in Y.

    Lightening holes are auto-spaced between the spar fractions and the radius is
    clamped to the local section height so they can never intersect the spar caps,
    each other, or the OML (Hole #5)."""
    phi = math.radians(twist_deg)
    w0 = _oml_section_wire(airfoil_points, chord_m, y_station_m, 0.0, phi, te_method)
    w1 = _oml_section_wire(
        airfoil_points, chord_m, y_station_m + thickness_m, 0.0, phi, te_method
    )
    rib = cq.Solid.makeLoft([w0, w1], ruled=True)
    wp = _wp(rib)

    n = max(0, int(n_lightening_holes))
    if n > 0:
        upper, lower = _surfaces(airfoil_points)
        x_lo = front_spar_x_frac
        x_hi = rear_spar_x_frac
        # Auto-space hole centres strictly between the spars.
        for k in range(n):
            xf = x_lo + (x_hi - x_lo) * (k + 1) / (n + 1)
            xc = xf * chord_m
            z_up = _interp_z(upper, xf) * chord_m
            z_lo = _interp_z(lower, xf) * chord_m
            zc = 0.5 * (z_up + z_lo)
            sec_h = z_up - z_lo
            # spacing between adjacent hole centres along x
            dx = (x_hi - x_lo) * chord_m / (n + 1)
            # clamp: fit within section height AND not overrun the x-spacing
            r = min(0.30 * sec_h, 0.40 * dx)
            if r <= 1e-4:
                continue
            y_mid = y_station_m + thickness_m / 2.0
            hole = (
                cq.Workplane("XZ")
                .center(xc, zc)
                .circle(r)
                # extrude symmetric (both=True) so the cylinder straddles the rib
                # regardless of the XZ-plane normal sign; over-extrude guarantees
                # a clean cut-through.
                .extrude(thickness_m * 2, both=True)
                .translate(cq.Vector(0, y_mid, 0))
            )
            wp = wp.cut(hole)
    return _validate_solid(wp, f"rib@y{y_station_m:.3f}")


def make_stringer(
    *,
    span_m: float,
    x_frac: float,
    z_frac: float,
    root_chord_m: float,
    tip_chord_m: float,
    airfoil_points=None,
    section: str = "L",
    leg_mm: float = 12.0,
    thickness_mm: float = 1.5,
    twist_tip_deg: float = 0.0,
    dihedral_deg: float = 0.0,
) -> cq.Workplane:
    """Stringer (L/Z/T/hat cap) running spanwise along the (x_frac) chord line at
    the OML surface selected by ``z_frac`` (+1 upper cap, -1 lower cap).

    If ``airfoil_points`` is given, the cap base is seated on the ACTUAL local OML
    surface (essential for a cambered airfoil whose lower surface is not at a
    symmetric height) and the web grows inward into the section, so the stringer
    truly attaches to the skin/ribs rather than floating off the surface."""
    if section not in ("L", "Z", "T", "hat"):
        raise CadAeroError(f"unknown stringer section {section!r}")
    leg = leg_mm / 1000.0
    th = thickness_mm / 1000.0
    sign = 1.0 if z_frac >= 0 else -1.0
    surf = None
    if airfoil_points is not None:
        u, low = _surfaces(airfoil_points)
        surf = u if z_frac >= 0 else low

    def _profile_2d():
        # Local (a, b) profile, origin at the attachment corner; b grows away
        # from the skin (sign-flipped for a lower cap).
        if section == "L":
            return [(0, 0), (leg, 0), (leg, th), (th, th), (th, leg), (0, leg)]
        if section == "T":
            return [(-leg / 2, 0), (leg / 2, 0), (leg / 2, th), (th / 2, th),
                    (th / 2, leg), (-th / 2, leg), (-th / 2, th), (-leg / 2, th)]
        if section == "Z":
            return [(0, 0), (leg, 0), (leg, th), (th, th), (th, leg),
                    (leg, leg), (leg, leg + th), (0, leg + th)]
        # hat
        return [(0, 0), (leg, 0), (leg, th), (leg - th, th), (leg - th, leg),
                (th, leg), (th, th), (0, th)]

    prof = _profile_2d()
    wires = []
    for y in (0.0, span_m):
        chord = _chord_at(y, span_m, root_chord_m, tip_chord_m)
        z0 = _dihedral_z(y, dihedral_deg)
        phi = _twist_phi(y, span_m, twist_tip_deg)
        xc = x_frac * chord
        # Seat the cap on the real OML surface when available; else a sane nominal.
        if surf is not None:
            z_surf = _interp_z(surf, x_frac) * chord
        else:
            z_surf = sign * 0.06 * chord
        xq = 0.25 * chord
        ring = []
        for (a, b) in prof:
            px = xc + a
            pz = z_surf - sign * b  # web grows INWARD (into the section)
            xr, zr = _twist_xz(px, pz, xq, phi)
            ring.append(cq.Vector(xr, y, z0 + zr))
        wires.append(cq.Wire.makePolygon(ring, close=True))
    solid = cq.Solid.makeLoft(wires, ruled=True)
    return _validate_solid(_wp(solid), f"stringer[{section}]@{x_frac:.2f}")


def make_wing_skin_panel(
    *,
    airfoil_points,
    span_m: float,
    root_chord_m: float,
    tip_chord_m: float,
    upper: bool,
    thickness_root_m: float,
    thickness_tip_m: float,
    front_spar_x_frac: float,
    rear_spar_x_frac: float,
    twist_tip_deg: float = 0.0,
    dihedral_deg: float = 0.0,
    n_stations: int = 11,
) -> cq.Workplane:
    """Curved OML skin panel (upper suction or lower) between the spars, with a
    root→tip thickness taper. Each station section follows the airfoil arc; the
    inner surface is the arc offset inward by the local skin thickness."""
    surf_u, surf_l = _surfaces(airfoil_points)
    surf = surf_u if upper else surf_l
    sign = 1.0 if upper else -1.0
    # sample the arc between the spar fractions
    n_arc = 16
    xs = [
        front_spar_x_frac + (rear_spar_x_frac - front_spar_x_frac) * j / (n_arc - 1)
        for j in range(n_arc)
    ]
    wires = []
    for i in range(n_stations):
        y = span_m * i / (n_stations - 1)
        chord = _chord_at(y, span_m, root_chord_m, tip_chord_m)
        z0 = _dihedral_z(y, dihedral_deg)
        phi = _twist_phi(y, span_m, twist_tip_deg)
        t = thickness_root_m + (thickness_tip_m - thickness_root_m) * (
            0.0 if span_m == 0 else y / span_m
        )
        xq = 0.25 * chord
        outer = [(xf * chord, _interp_z(surf, xf) * chord) for xf in xs]
        inner = [(x, z - sign * t) for (x, z) in outer]
        ring2d = outer + list(reversed(inner))
        ring = []
        for (x, z) in ring2d:
            xr, zr = _twist_xz(x, z, xq, phi)
            ring.append(cq.Vector(xr, y, z0 + zr))
        wires.append(cq.Wire.makePolygon(ring, close=True))
    solid = cq.Solid.makeLoft(wires, ruled=True)
    return _validate_solid(_wp(solid), f"skin[{'upper' if upper else 'lower'}]")


def make_root_fitting(
    *,
    root_chord_m: float,
    thickness_to_chord: float,
    front_spar_x_frac: float,
    rear_spar_x_frac: float,
    fitting_thickness_m: float,
    bolt_pattern=None,
) -> cq.Workplane:
    """Solid root fitting spanning the spar bay at y=0, sized to the section box,
    ``fitting_thickness_m`` thick in Y. Optional ``bolt_pattern`` = list of
    (x_frac, z_frac) through-holes."""
    x0 = front_spar_x_frac * root_chord_m
    x1 = rear_spar_x_frac * root_chord_m
    h = thickness_to_chord * root_chord_m
    box = (
        cq.Workplane("XZ")
        .center((x0 + x1) / 2.0, 0.0)
        .rect(x1 - x0, h)
        .extrude(fitting_thickness_m)
    )
    # Normalize the spanwise position to Y in [0, fitting_thickness] regardless of
    # the XZ-plane normal sign, so the fitting overlaps the root rib + spars rather
    # than sitting just inboard of them (zero-volume abutment).
    box = box.translate(cq.Vector(0, -box.val().BoundingBox().ymin, 0))
    wp = box
    if bolt_pattern:
        y_mid = fitting_thickness_m / 2.0
        r = min(0.15 * (x1 - x0), 0.20 * h) / max(len(bolt_pattern), 1)
        r = max(min(r, 0.25 * h), 1e-3)
        for (xf, zf) in bolt_pattern:
            xc = xf * root_chord_m
            zc = zf * (h / 2.0)
            hole = (
                cq.Workplane("XZ")
                .center(xc, zc)
                .circle(r)
                .extrude(fitting_thickness_m * 2, both=True)
                .translate(cq.Vector(0, y_mid, 0))
            )
            wp = wp.cut(hole)
    return _validate_solid(wp, "root_fitting")


def make_control_surface(
    *,
    parent_airfoil_points,
    chord_frac: float,
    span_m: float,
    root_chord_m: float,
    tip_chord_m: float,
    hinge_x_frac: float,
    deflection_deg: float = 0.0,
    te_method: str = _DEFAULT_TE_METHOD,
) -> cq.Workplane:
    """Control surface (aileron/flap/elevator) = the aft OML region from
    ``hinge_x_frac`` to the trailing edge, lofted over ``span_m`` and rotated about
    the hinge line by ``deflection_deg``. Returns ONLY the flap solid; the parent
    cove/trim is out of scope v1 (Hole #3)."""
    upper, lower = _surfaces(parent_airfoil_points)

    def _aft_ring(chord):
        # perimeter: upper hinge→TE, then lower TE→hinge (close() draws hinge line)
        xs_aft = [x for (x, _) in upper if x >= hinge_x_frac]
        if not xs_aft or xs_aft[0] > hinge_x_frac:
            xs_aft = [hinge_x_frac] + xs_aft
        ring = []
        for xf in xs_aft:
            ring.append((xf * chord, _interp_z(upper, xf) * chord))
        for xf in reversed(xs_aft):
            ring.append((xf * chord, _interp_z(lower, xf) * chord))
        return ring

    wires = []
    for y in (0.0, span_m):
        chord = _chord_at(y, span_m, root_chord_m, tip_chord_m)
        ring = [cq.Vector(x, y, z) for (x, z) in _aft_ring(chord)]
        wires.append(cq.Wire.makePolygon(ring, close=True))
    solid = cq.Solid.makeLoft(wires, ruled=True)

    if abs(deflection_deg) > 1e-9:
        hinge_x = hinge_x_frac * root_chord_m
        solid = solid.rotate(
            cq.Vector(hinge_x, 0, 0), cq.Vector(hinge_x, 1, 0), deflection_deg
        )
    return _validate_solid(_wp(solid), "control_surface")


# ===========================================================================
# A3/A4 — canonical assembly + reference artifact
# ===========================================================================
# The frozen NACA-2412-class semi-span wing. Building it from the A2 builders
# yields the NEW OML reference artifact (output/wing_assembly_oml_reference.json)
# that replaces the retired 67.6 kg box-beam number (Hole #1, #9).
CANONICAL_WING = {
    "naca": "2412",
    "span_m": 5.3642,            # semi-span
    "root_chord_m": 1.7881,
    "tip_chord_m": 0.8940,
    "front_spar_x_frac": 0.25,
    "rear_spar_x_frac": 0.65,
    "thickness_to_chord": 0.12,
    "twist_tip_deg": -3.0,
    "dihedral_deg": 2.0,
    "n_ribs": 7,
    "web_thickness_m": 0.0008,
    "skin_thickness_m": 0.0012,
    "rib_thickness_m": 0.003,
}
_AL2024T3_DENSITY_KGM3 = 2780.0
# 2 spars + 7 ribs + 2 skins + 4 stringers + 1 root fitting.
EXPECTED_PART_COUNT = 16


def canonical_wing_parts() -> dict:
    """Build the canonical semi-span wing as an ordered {name: cq.Workplane}.

    Multi-Y builders (spar/skin/stringer) bake in dihedral + twist; the
    single-station ribs are positioned at the composition layer (dihedral
    Z-offset + local twist) so they meet the dihedral-shifted spars."""
    p = CANONICAL_WING
    af = naca4_points(p["naca"], 1.0)
    span, root, tip = p["span_m"], p["root_chord_m"], p["tip_chord_m"]
    front, rear = p["front_spar_x_frac"], p["rear_spar_x_frac"]
    twist, dih = p["twist_tip_deg"], p["dihedral_deg"]
    parts: dict = {}

    parts["front_spar"] = make_spar(
        airfoil_points=af, span_m=span, root_chord_m=root, tip_chord_m=tip,
        x_frac=front, web_thickness_m=p["web_thickness_m"],
        twist_tip_deg=twist, dihedral_deg=dih,
    )
    parts["rear_spar"] = make_spar(
        airfoil_points=af, span_m=span, root_chord_m=root, tip_chord_m=tip,
        x_frac=rear, web_thickness_m=p["web_thickness_m"],
        twist_tip_deg=twist, dihedral_deg=dih,
    )
    for i in range(p["n_ribs"]):
        y = span * i / (p["n_ribs"] - 1)
        # Keep the outermost rib inside the spar loft [0, span] so it overlaps
        # (not merely abuts) the spar — a thickness past the tip means zero-volume
        # contact and a "floating" tip rib.
        y = min(y, span - p["rib_thickness_m"])
        chord = _chord_at(y, span, root, tip)
        rib = make_rib(
            airfoil_points=af, chord_m=chord, y_station_m=y,
            thickness_m=p["rib_thickness_m"], front_spar_x_frac=front,
            rear_spar_x_frac=rear, n_lightening_holes=3,
            twist_deg=twist * (0.0 if span == 0 else y / span),
        )
        z0 = _dihedral_z(y, dih)
        if abs(z0) > 1e-12:
            rib = rib.translate(cq.Vector(0, 0, z0))
        parts[f"rib_{i:02d}"] = rib

    for upper in (True, False):
        parts["skin_upper" if upper else "skin_lower"] = make_wing_skin_panel(
            airfoil_points=af, span_m=span, root_chord_m=root, tip_chord_m=tip,
            upper=upper, thickness_root_m=p["skin_thickness_m"],
            thickness_tip_m=p["skin_thickness_m"], front_spar_x_frac=front,
            rear_spar_x_frac=rear, twist_tip_deg=twist, dihedral_deg=dih,
        )

    for x_frac, tag in ((front, "front"), (rear, "rear")):
        for z_frac, cap in ((1.0, "upper"), (-1.0, "lower")):
            parts[f"stringer_{tag}_{cap}"] = make_stringer(
                span_m=span, x_frac=x_frac, z_frac=z_frac, root_chord_m=root,
                tip_chord_m=tip, airfoil_points=af, section="L",
                twist_tip_deg=twist, dihedral_deg=dih,
            )

    parts["root_fitting"] = make_root_fitting(
        root_chord_m=root, thickness_to_chord=p["thickness_to_chord"],
        front_spar_x_frac=front, rear_spar_x_frac=rear,
        fitting_thickness_m=p["web_thickness_m"] * 3,
    )
    return parts


def wing_mass_kg(parts: dict, density_kgm3: float = _AL2024T3_DENSITY_KGM3) -> float:
    """Total structural mass [kg] = sum(part volume_m3) * density."""
    return sum(wp.val().Volume() for wp in parts.values()) * density_kgm3


def wing_reference_report(parts: dict) -> dict:
    """Serializable reference artifact: per-part volume/mass/bbox + totals."""
    rows = {}
    for name, wp in parts.items():
        s = wp.val()
        bb = s.BoundingBox()
        rows[name] = {
            "volume_m3": s.Volume(),
            "mass_kg": s.Volume() * _AL2024T3_DENSITY_KGM3,
            "bbox_m": [bb.xlen, bb.ylen, bb.zlen],
        }
    return {
        "params": CANONICAL_WING,
        "density_kgm3": _AL2024T3_DENSITY_KGM3,
        "part_count": len(parts),
        "total_mass_kg": wing_mass_kg(parts),
        "parts": rows,
        "note": (
            "OML reference artifact (Hole #1/#9). Replaces the retired 67.6 kg "
            "box-beam Raymer estimate. Determinism target: same part count + mass "
            "within epsilon, NOT byte-equal STEP (Hole #4)."
        ),
    }
