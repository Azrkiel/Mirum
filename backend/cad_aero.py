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
