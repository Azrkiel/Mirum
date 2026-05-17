"""
backend/verification/geometry.py — OCC-based geometry certificate.

Runs structural checks on a compiled STEP file and returns a
GeometryCertificate TypedDict containing measurement values, per-check
pass/fail flags, and a consolidated list of failure reasons. The schema
is additive: legacy keys retained, richer measurements layered on top.

The certificate is advisory — attached to the API "complete" event and
displayed in the frontend cert card; a failing certificate does NOT
block delivery of the GLB model.

Legacy checks (unchanged):
  1. volume_positive       — solid volume > 0
  2. surface_area_positive — surface area > 0
  3. no_degenerate_faces   — every face has area > _MIN_FACE_AREA_MM2
  4. bounding_box_finite   — bounding box extents finite and < _MAX_EXTENT_MM
  5. brep_valid            — BRepCheck_Analyzer reports no errors
  6. is_solid              — shape contains at least one solid sub-shape

Requirements:
  pythonocc-core (OCC) — conda install -c conda-forge pythonocc-core
  Available in the project Docker image. Gracefully degrades in environments
  without OCC (returns a failed cert with an informative failure message).
"""

from __future__ import annotations

import logging
import math
import os
from typing import TypedDict

logger = logging.getLogger("mirum.verification.geometry")

# ---------------------------------------------------------------------------
# Thresholds & material table
# ---------------------------------------------------------------------------

_MIN_FACE_AREA_MM2: float = 1e-6    # faces below this are considered degenerate
_MAX_EXTENT_MM: float = 1e6         # bounding box larger than this is suspect
_DEGEN_EDGE_FLOOR_MM: float = 1e-4  # absolute floor on degenerate-edge threshold

# Covers DesignRequirements.material_class enum (schemas.py:419-426) plus
# aerospace-specific variants Al-6061/Al-7075/Steel-1018/Ti-6Al-4V.
_MATERIAL_DENSITIES_KG_M3: dict[str, float] = {
    "aluminum_6061":   2700.0,
    "aluminum_7075":   2810.0,
    "steel_1018":      7870.0,
    "ti_6al_4v":       4430.0,
    "abs":             1040.0,
    "pla":             1240.0,
    "carbon_fiber":    1600.0,
    "stainless_steel": 7900.0,
    "plastic":         1200.0,
    "brass":           8500.0,
    "ceramic":         3900.0,
    "rubber":          1100.0,
}

_MATERIAL_ALIASES: dict[str, str] = {
    "aluminum": "aluminum_6061",
    "steel":    "steel_1018",
    "titanium": "ti_6al_4v",
    "cf":       "carbon_fiber",
}

# ---------------------------------------------------------------------------
# TypedDict — the geometry certificate
# ---------------------------------------------------------------------------


class GeometryCertificate(TypedDict):
    """Full geometry certification result for a compiled STEP file.

    Note: `mass_g`, `material`, `part_count`, `per_part` are NOT fields of
    `GeometryCertificate`. They are added to the response-payload dict by
    `backend/main.py` (single-part: post-call augmentation; multi-part:
    aggregate construction). Callers using `certify_geometry()` directly
    will not see those keys.
    """

    passed: bool
    """True when all checks pass."""

    volume_mm3: float | None
    """Solid volume in cubic millimetres, or None if measurement failed."""

    surface_area_mm2: float | None
    """Surface area in square millimetres, or None if measurement failed."""

    bounding_box: dict[str, float] | None
    """{'x_mm': float, 'y_mm': float, 'z_mm': float} extents, or None."""

    checks: dict[str, bool]
    """Per-check pass/fail flags, keyed by check name."""

    failures: list[str]
    """Human-readable failure messages (empty list when passed=True)."""

    manifold: bool
    """`manifold: bool` — True iff `BRepCheck_Analyzer.IsValid(shape)` AND no
    per-subshape `BRepCheck_Result.Status()` errors. This is BRep topological
    validity + subshape integrity; it is NOT a strict 2-manifoldness test
    (which would require walking edges and counting adjacent faces).
    Rename to `brep_topology_valid` is a follow-up consideration.
    """

    manifold_failures: list[dict]
    """List of {'subshape_type': str, 'status': str} entries from BRepCheck."""

    volume: float | None
    """Solid volume in cubic metres (SI), or None if measurement failed."""

    surface_area: float | None
    """Surface area in square metres (SI), or None if measurement failed."""

    bounding_box_corners: dict | None
    """{'min': [x,y,z], 'max': [x,y,z]} in mm, or None on failure."""

    center_of_mass: dict | None
    """{'x': float, 'y': float, 'z': float} in mm, or None on failure."""

    principal_moments_of_inertia: dict | None
    """{'moments': [I1,I2,I3], 'axes': [[ax,ay,az]x3]} sorted desc by moment."""

    mass_properties: dict | None
    """{'material': str, 'density': float kg/m3, 'mass': float kg} or None."""

    zero_thickness_features: list[dict]
    """{'subshape_type': 'FACE', 'index': int, 'area_mm2': float} entries."""

    degenerate_edges: list[dict]
    """{'subshape_type': 'EDGE', 'index': int, 'length_mm': float} entries."""

    checks_passed: bool
    """Alias for `passed` — True when every per-check flag is True."""

    warnings: list[str]
    """Non-fatal advisories collected during certification."""

    errors: list[str]
    """Fatal/internal errors collected during certification."""


# ---------------------------------------------------------------------------
# OCC imports (lazy-guarded)
# ---------------------------------------------------------------------------

def _import_occ():
    """Import OCC modules; raise ImportError if not installed."""
    from OCC.Core.STEPControl import STEPControl_Reader  # type: ignore[import]
    from OCC.Core.BRep import BRep_Builder  # type: ignore[import]
    from OCC.Core.BRepGProp import (  # type: ignore[import]
        brepgprop_VolumeProperties,
        brepgprop_SurfaceProperties,
        brepgprop_LinearProperties,
    )
    from OCC.Core.GProp import GProp_GProps  # type: ignore[import]
    from OCC.Core.BRepBndLib import brepbndlib_Add  # type: ignore[import]
    from OCC.Core.Bnd import Bnd_Box  # type: ignore[import]
    from OCC.Core.BRepCheck import (  # type: ignore[import]
        BRepCheck_Analyzer,
        BRepCheck_Status,
        BRepCheck_ListIteratorOfListOfStatus,
    )
    from OCC.Core.TopExp import TopExp_Explorer  # type: ignore[import]
    from OCC.Core.TopAbs import (  # type: ignore[import]
        TopAbs_FACE, TopAbs_SOLID, TopAbs_EDGE, TopAbs_SHELL, TopAbs_VERTEX,
    )
    from OCC.Core.IFSelect import IFSelect_RetDone  # type: ignore[import]
    return {
        "STEPControl_Reader": STEPControl_Reader,
        "BRep_Builder": BRep_Builder,
        "brepgprop_VolumeProperties": brepgprop_VolumeProperties,
        "brepgprop_SurfaceProperties": brepgprop_SurfaceProperties,
        "brepgprop_LinearProperties": brepgprop_LinearProperties,
        "GProp_GProps": GProp_GProps,
        "brepbndlib_Add": brepbndlib_Add,
        "Bnd_Box": Bnd_Box,
        "BRepCheck_Analyzer": BRepCheck_Analyzer,
        "BRepCheck_Status": BRepCheck_Status,
        "BRepCheck_ListIteratorOfListOfStatus": BRepCheck_ListIteratorOfListOfStatus,
        "TopExp_Explorer": TopExp_Explorer,
        "TopAbs_FACE": TopAbs_FACE,
        "TopAbs_SOLID": TopAbs_SOLID,
        "TopAbs_EDGE": TopAbs_EDGE,
        "TopAbs_SHELL": TopAbs_SHELL,
        "TopAbs_VERTEX": TopAbs_VERTEX,
        "IFSelect_RetDone": IFSelect_RetDone,
    }


_BREPCHECK_STATUS_NAMES: dict[int, str] | None = None
_BREPCHECK_KNOWN_NAMES = (
    "BRepCheck_NoError", "BRepCheck_InvalidPointOnCurve", "BRepCheck_InvalidPointOnCurveOnSurface",
    "BRepCheck_InvalidPointOnSurface", "BRepCheck_No3DCurve", "BRepCheck_Multiple3DCurve",
    "BRepCheck_Invalid3DCurve", "BRepCheck_NoCurveOnSurface", "BRepCheck_InvalidCurveOnSurface",
    "BRepCheck_InvalidCurveOnClosedSurface", "BRepCheck_InvalidSameRangeFlag",
    "BRepCheck_InvalidSameParameterFlag", "BRepCheck_InvalidDegeneratedFlag",
    "BRepCheck_FreeEdge", "BRepCheck_InvalidMultiConnexity",
    "BRepCheck_InvalidRange", "BRepCheck_EmptyWire", "BRepCheck_RedundantEdge",
    "BRepCheck_SelfIntersectingWire", "BRepCheck_NoSurface", "BRepCheck_InvalidWire",
    "BRepCheck_RedundantWire", "BRepCheck_IntersectingWires", "BRepCheck_InvalidImbricationOfWires",
    "BRepCheck_EmptyShell", "BRepCheck_RedundantFace", "BRepCheck_UnorientableShape",
    "BRepCheck_NotClosed", "BRepCheck_NotConnected", "BRepCheck_SubshapeNotInShape",
    "BRepCheck_BadOrientation", "BRepCheck_BadOrientationOfSubshape",
    "BRepCheck_InvalidPolygonOnTriangulation", "BRepCheck_InvalidToleranceValue",
    "BRepCheck_EnforcedReversedNature", "BRepCheck_CheckFail",
)


def _brepcheck_status_name(occ, value: int) -> str:
    """Resolve a BRepCheck_Status int to its symbolic name (cached)."""
    global _BREPCHECK_STATUS_NAMES
    if _BREPCHECK_STATUS_NAMES is None:
        mod = occ["BRepCheck_Status"]
        cache: dict[int, str] = {}
        for name in _BREPCHECK_KNOWN_NAMES:
            const = getattr(mod, name, None)
            if const is None:
                continue
            try:
                cache[int(const)] = name
            except Exception:
                continue
        _BREPCHECK_STATUS_NAMES = cache
    return _BREPCHECK_STATUS_NAMES.get(value, f"BRepCheck_Status({value})")


# ---------------------------------------------------------------------------
# Internal helpers — failed-cert + load
# ---------------------------------------------------------------------------

def _failed_cert(reason: str) -> GeometryCertificate:
    """Return a minimal failed certificate for error-path returns."""
    return GeometryCertificate(
        passed=False,
        volume_mm3=None,
        surface_area_mm2=None,
        bounding_box=None,
        checks={
            "volume_positive": False,
            "surface_area_positive": False,
            "no_degenerate_faces": False,
            "bounding_box_finite": False,
            "brep_valid": False,
            "is_solid": False,
        },
        failures=[reason],
        manifold=False,
        manifold_failures=[],
        volume=None,
        surface_area=None,
        bounding_box_corners=None,
        center_of_mass=None,
        principal_moments_of_inertia=None,
        mass_properties=None,
        zero_thickness_features=[],
        degenerate_edges=[],
        checks_passed=False,
        warnings=[],
        errors=[reason],
    )


def _load_step(occ, path: str):
    """Load a STEP file and return the top-level OCC shape."""
    reader = occ["STEPControl_Reader"]()
    status = reader.ReadFile(path)
    if status != occ["IFSelect_RetDone"]:
        raise ValueError(f"STEPControl_Reader.ReadFile() failed for {path!r}")
    reader.TransferRoots()
    return reader.OneShape()


# ---------------------------------------------------------------------------
# Measurement helpers — legacy
# ---------------------------------------------------------------------------

def _measure_volume(occ, shape, errors: list[str]) -> float | None:
    """Return volume in mm3, or None on failure (appends to errors)."""
    try:
        props = occ["GProp_GProps"]()
        occ["brepgprop_VolumeProperties"](shape, props)
        v = props.Mass()
        return v if math.isfinite(v) else None
    except Exception as exc:
        errors.append(f"volume measurement: {exc}")
        return None


def _measure_surface_area(occ, shape, errors: list[str]) -> float | None:
    """Return surface area in mm2, or None on failure (appends to errors)."""
    try:
        props = occ["GProp_GProps"]()
        occ["brepgprop_SurfaceProperties"](shape, props)
        a = props.Mass()
        return a if math.isfinite(a) else None
    except Exception as exc:
        errors.append(f"surface-area measurement: {exc}")
        return None


def _measure_bounding_box(occ, shape, errors: list[str]) -> dict[str, float] | None:
    """Return {'x_mm', 'y_mm', 'z_mm'} extents, or None on failure (appends to errors)."""
    try:
        bb = occ["Bnd_Box"]()
        occ["brepbndlib_Add"](shape, bb)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return {"x_mm": abs(xmax - xmin), "y_mm": abs(ymax - ymin), "z_mm": abs(zmax - zmin)}
    except Exception as exc:
        errors.append(f"bounding-box measurement: {exc}")
        return None


# ---------------------------------------------------------------------------
# Measurement helpers — additive (rich schema)
# ---------------------------------------------------------------------------

def _measure_bounding_box_corners(occ, shape, errors: list[str]) -> dict | None:
    """Return {'min':[x,y,z], 'max':[x,y,z]} in mm, or None on failure (appends to errors)."""
    try:
        bb = occ["Bnd_Box"]()
        occ["brepbndlib_Add"](shape, bb)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return {"min": [xmin, ymin, zmin], "max": [xmax, ymax, zmax]}
    except Exception as exc:
        errors.append(f"bounding-box corners measurement: {exc}")
        return None


def _measure_center_of_mass(occ, shape, errors: list[str]) -> dict | None:
    """Return {'x','y','z'} in mm of the volume centroid, or None on failure (appends to errors)."""
    try:
        props = occ["GProp_GProps"]()
        occ["brepgprop_VolumeProperties"](shape, props)
        p = props.CentreOfMass()
        return {"x": p.X(), "y": p.Y(), "z": p.Z()}
    except Exception as exc:
        errors.append(f"center-of-mass measurement: {exc}")
        return None


def _measure_principal_moments(occ, shape, errors: list[str]) -> dict | None:
    """Return {'moments':[I1,I2,I3], 'axes':[[ax,ay,az]x3]} sorted desc, or None (appends to errors)."""
    try:
        props = occ["GProp_GProps"]()
        occ["brepgprop_VolumeProperties"](shape, props)
        pp = props.PrincipalProperties()
        I1, I2, I3 = pp.Moments()
        a1 = pp.FirstAxisOfInertia()
        a2 = pp.SecondAxisOfInertia()
        a3 = pp.ThirdAxisOfInertia()
        pairs = sorted(
            [(I1, [a1.X(), a1.Y(), a1.Z()]),
             (I2, [a2.X(), a2.Y(), a2.Z()]),
             (I3, [a3.X(), a3.Y(), a3.Z()])],
            key=lambda p: p[0], reverse=True,
        )
        return {"moments": [p[0] for p in pairs], "axes": [p[1] for p in pairs]}
    except Exception as exc:
        errors.append(f"principal moments measurement: {exc}")
        return None


def _resolve_material(name: str | None) -> tuple[str | None, float | None]:
    """Resolve a material label (with aliases) to (canonical, density_kg_m3)."""
    if name is None:
        return None, None
    key = name.lower().strip().replace("-", "_").replace(" ", "_")
    if not key:
        return None, None
    key = _MATERIAL_ALIASES.get(key, key)
    density = _MATERIAL_DENSITIES_KG_M3.get(key)
    if density is None:
        return None, None
    return key, density


def _compute_mass_properties(
    material: str | None, volume_mm3: float | None, warnings: list[str],
) -> dict | None:
    """Return {'material','density','mass'} or None; append a warning if unknown."""
    if material is None:
        return None
    canonical, density = _resolve_material(material)
    if canonical is None or density is None:
        warnings.append(f"Unknown material {material!r}; mass_properties skipped")
        return None
    if volume_mm3 is None or volume_mm3 <= 0.0:
        return None
    mass_kg = density * volume_mm3 * 1e-9
    return {"material": canonical, "density": density, "mass": mass_kg}


def _find_zero_thickness_features(
    occ, shape, tol_mm: float, errors: list[str],
) -> list[dict]:
    """Return faces with area below tol_mm**2. Collect then iterate (directive 5)."""
    faces = []
    try:
        explorer = occ["TopExp_Explorer"](shape, occ["TopAbs_FACE"])
        while explorer.More():
            faces.append(explorer.Current())
            explorer.Next()
    except Exception as exc:
        errors.append(f"zero-thickness scan: explorer: {exc}")
        return []
    threshold_area = tol_mm * tol_mm
    out: list[dict] = []
    for i, face in enumerate(faces):
        try:
            props = occ["GProp_GProps"]()
            occ["brepgprop_SurfaceProperties"](face, props)
            area = props.Mass()
            if area < threshold_area:
                out.append({"subshape_type": "FACE", "index": i, "area_mm2": area})
        except Exception as exc:
            errors.append(f"zero-thickness scan: face {i}: {exc}")
    return out


def _find_degenerate_edges(
    occ, shape, max_extent_mm: float, errors: list[str],
) -> list[dict]:
    """Return edges shorter than max(_DEGEN_EDGE_FLOOR_MM, 1e-7*extent). Collect first."""
    edges = []
    try:
        explorer = occ["TopExp_Explorer"](shape, occ["TopAbs_EDGE"])
        while explorer.More():
            edges.append(explorer.Current())
            explorer.Next()
    except Exception as exc:
        errors.append(f"degenerate-edge scan: explorer: {exc}")
        return []
    threshold = max(_DEGEN_EDGE_FLOOR_MM, 1e-7 * max_extent_mm)
    out: list[dict] = []
    for i, edge in enumerate(edges):
        try:
            props = occ["GProp_GProps"]()
            occ["brepgprop_LinearProperties"](edge, props)
            length = props.Mass()
            if length < threshold:
                out.append({"subshape_type": "EDGE", "index": i, "length_mm": length})
        except Exception as exc:
            errors.append(f"degenerate-edge scan: edge {i}: {exc}")
    return out


def _collect_manifold_failures(occ, shape, errors: list[str]) -> list[dict]:
    """Walk SOLID/SHELL/FACE/EDGE/VERTEX, return non-NoError BRepCheck statuses."""
    analyzer = occ["BRepCheck_Analyzer"](shape)
    out: list[dict] = []
    subshape_types = [
        ("SOLID",  occ["TopAbs_SOLID"]),
        ("SHELL",  occ["TopAbs_SHELL"]),
        ("FACE",   occ["TopAbs_FACE"]),
        ("EDGE",   occ["TopAbs_EDGE"]),
        ("VERTEX", occ["TopAbs_VERTEX"]),
    ]
    for label, typ in subshape_types:
        subs = []
        try:
            expl = occ["TopExp_Explorer"](shape, typ)
            while expl.More():
                subs.append(expl.Current())
                expl.Next()
        except Exception as exc:
            errors.append(f"manifold walk: {label} explorer: {exc}")
            continue
        for sub in subs:
            try:
                result = analyzer.Result(sub)
                if result is None:
                    continue
                status_list = result.Status()
                try:
                    it = occ["BRepCheck_ListIteratorOfListOfStatus"](status_list)
                    while it.More():
                        code = int(it.Value())
                        if code != 0:  # skip BRepCheck_NoError
                            out.append({
                                "subshape_type": label,
                                "status": _brepcheck_status_name(occ, code),
                            })
                        it.Next()
                except Exception:
                    # Fallback: emit just the first status (older pythonocc-core builds)
                    try:
                        first = int(status_list.First())
                        if first != 0:
                            out.append({
                                "subshape_type": label,
                                "status": _brepcheck_status_name(occ, first),
                            })
                    except Exception as inner:
                        errors.append(f"manifold walk: {label}: status iter: {inner}")
            except Exception as exc:
                errors.append(f"manifold walk: {label}: {exc}")
    return out


# ---------------------------------------------------------------------------
# Legacy check helpers
# ---------------------------------------------------------------------------

def _check_degenerate_faces(occ, shape, errors: list[str]) -> tuple[bool, list[str]]:
    """Return (pass, failures). Fails if any face has area < _MIN_FACE_AREA_MM2 (appends to errors)."""
    failures: list[str] = []
    try:
        faces = []
        explorer = occ["TopExp_Explorer"](shape, occ["TopAbs_FACE"])
        while explorer.More():
            faces.append(explorer.Current())
            explorer.Next()
        degenerate_count = 0
        for i, face in enumerate(faces):
            try:
                props = occ["GProp_GProps"]()
                occ["brepgprop_SurfaceProperties"](face, props)
                if props.Mass() < _MIN_FACE_AREA_MM2:
                    degenerate_count += 1
            except Exception as exc:
                errors.append(f"degenerate-face scan: face {i}: {exc}")
        if degenerate_count > 0:
            failures.append(
                f"{degenerate_count}/{len(faces)} face(s) have area < {_MIN_FACE_AREA_MM2} mm2 "
                "(degenerate faces detected)"
            )
        return len(failures) == 0, failures
    except Exception as exc:
        errors.append(f"degenerate-face scan: {exc}")
        return False, [f"Face degeneracy check error: {exc}"]


def _check_brep_valid(occ, shape) -> tuple[bool, list[str]]:
    """Run BRepCheck_Analyzer and return (pass, failures)."""
    try:
        analyzer = occ["BRepCheck_Analyzer"](shape)
        if analyzer.IsValid():
            return True, []
        return False, ["BRepCheck_Analyzer reports invalid BRep topology"]
    except Exception as exc:
        return False, [f"BRep validity check error: {exc}"]


def _check_is_solid(occ, shape) -> tuple[bool, list[str]]:
    """Return True if shape contains at least one solid sub-shape."""
    try:
        explorer = occ["TopExp_Explorer"](shape, occ["TopAbs_SOLID"])
        if not explorer.More():
            return False, ["Shape contains no solid sub-shapes (open shell or empty compound)"]
        return True, []
    except Exception as exc:
        return False, [f"Solid check error: {exc}"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def certify_geometry(
    step_path: str,
    *,
    material: str | None = None,
    zero_thickness_tolerance_mm: float = 0.1,
) -> GeometryCertificate:
    """Run OCC geometry checks on a STEP file.

    Args:
        step_path: Absolute or relative path to a .step file.
        material: Optional material label (canonical or alias) for mass calc.
        zero_thickness_tolerance_mm: Faces below tol**2 mm2 reported as
            zero-thickness features.

    Returns:
        GeometryCertificate — always returns a value, never raises.
        On OCC import failure or file error, returns a failed certificate.
    """
    warnings: list[str] = []
    errors: list[str] = []

    if not os.path.isfile(step_path):
        return _failed_cert(f"STEP file not found: {step_path!r}")

    try:
        occ = _import_occ()
    except (ImportError, ModuleNotFoundError) as exc:
        return _failed_cert(
            f"OCC not available ({exc}). Install with: "
            "conda install -c conda-forge pythonocc-core"
        )

    try:
        shape = _load_step(occ, step_path)
    except Exception as exc:
        return _failed_cert(f"STEP load error: {exc}")

    if shape is None or shape.IsNull():
        return _failed_cert("STEP transferred zero roots; loaded shape is null/None")

    checks: dict[str, bool] = {}
    failures: list[str] = []

    volume = _measure_volume(occ, shape, errors)
    volume_pass = volume is not None and volume > 0.0
    checks["volume_positive"] = volume_pass
    if not volume_pass:
        failures.append(
            f"Volume is {'None' if volume is None else f'{volume:.4f} mm3'} (expected > 0)"
        )

    surface_area = _measure_surface_area(occ, shape, errors)
    sa_pass = surface_area is not None and surface_area > 0.0
    checks["surface_area_positive"] = sa_pass
    if not sa_pass:
        failures.append(
            f"Surface area is {'None' if surface_area is None else f'{surface_area:.4f} mm2'} "
            "(expected > 0)"
        )

    no_degen_pass, degen_failures = _check_degenerate_faces(occ, shape, errors)
    checks["no_degenerate_faces"] = no_degen_pass
    failures.extend(degen_failures)

    bb = _measure_bounding_box(occ, shape, errors)
    bb_pass = (
        bb is not None
        and all(math.isfinite(v) for v in bb.values())
        and all(v < _MAX_EXTENT_MM for v in bb.values())
    )
    checks["bounding_box_finite"] = bb_pass
    if not bb_pass:
        failures.append(
            f"Bounding box is {'None' if bb is None else bb} "
            f"(expected finite extents < {_MAX_EXTENT_MM} mm)"
        )

    brep_pass, brep_failures = _check_brep_valid(occ, shape)
    checks["brep_valid"] = brep_pass
    failures.extend(brep_failures)

    solid_pass, solid_failures = _check_is_solid(occ, shape)
    checks["is_solid"] = solid_pass
    failures.extend(solid_failures)

    all_passed = all(checks.values())

    volume_m3 = (volume * 1e-9) if volume is not None else None
    surface_area_m2 = (surface_area * 1e-6) if surface_area is not None else None
    bbox_corners = _measure_bounding_box_corners(occ, shape, errors)
    com = _measure_center_of_mass(occ, shape, errors)
    moments = _measure_principal_moments(occ, shape, errors)
    mass_props = _compute_mass_properties(material, volume, warnings)
    max_extent = max(bb.values(), default=1.0) if bb is not None else 1.0
    ztf = _find_zero_thickness_features(occ, shape, zero_thickness_tolerance_mm, errors)
    degen = _find_degenerate_edges(occ, shape, max_extent, errors)
    try:
        manifold_failures = _collect_manifold_failures(occ, shape, errors)
    except Exception as exc:
        errors.append(f"manifold walk: {exc}")
        manifold_failures = []
    manifold = (len(manifold_failures) == 0) and brep_pass

    logger.info(
        "certify_geometry: path=%r passed=%s manifold=%s warnings=%d errors=%d",
        step_path, all_passed, manifold, len(warnings), len(errors),
    )

    return GeometryCertificate(
        passed=all_passed,
        volume_mm3=volume,
        surface_area_mm2=surface_area,
        bounding_box=bb,
        checks=checks,
        failures=failures,
        manifold=manifold,
        manifold_failures=manifold_failures,
        volume=volume_m3,
        surface_area=surface_area_m2,
        bounding_box_corners=bbox_corners,
        center_of_mass=com,
        principal_moments_of_inertia=moments,
        mass_properties=mass_props,
        zero_thickness_features=ztf,
        degenerate_edges=degen,
        checks_passed=all_passed,
        warnings=warnings,
        errors=errors,
    )
