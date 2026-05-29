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
