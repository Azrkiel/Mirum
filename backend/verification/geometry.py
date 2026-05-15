"""
backend/verification/geometry.py — OCC-based geometry certificate.

Runs 6 structural checks on a compiled STEP file and returns a
GeometryCertificate TypedDict containing measurement values, per-check
pass/fail flags, and a consolidated list of failure reasons.

The certificate is advisory — it is attached to the API "complete" event
and displayed in the frontend cert card, but a failing certificate does NOT
block delivery of the GLB model.

Checks:
  1. volume_positive     — solid volume > 0 (catches degenerate/empty solids)
  2. surface_area_positive — surface area > 0 (catches zero-area shells)
  3. no_degenerate_faces  — every face has area > _MIN_FACE_AREA_MM2
  4. bounding_box_finite  — bounding box extents are finite and < _MAX_EXTENT_MM
  5. brep_valid          — BRepCheck_Analyzer reports no errors
  6. is_solid            — shape contains at least one solid sub-shape

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
# Thresholds
# ---------------------------------------------------------------------------

_MIN_FACE_AREA_MM2: float = 1e-6    # faces below this are considered degenerate
_MAX_EXTENT_MM: float = 1e6         # bounding box larger than this is suspect

# ---------------------------------------------------------------------------
# TypedDict — the geometry certificate
# ---------------------------------------------------------------------------


class GeometryCertificate(TypedDict):
    """Full geometry certification result for a compiled STEP file."""

    passed: bool
    """True when all 6 checks pass."""

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


# ---------------------------------------------------------------------------
# OCC imports (lazy-guarded)
# ---------------------------------------------------------------------------

def _import_occ():
    """Import OCC modules; raise ImportError if not installed."""
    from OCC.Core.STEPControl import STEPControl_Reader  # type: ignore[import]
    from OCC.Core.BRep import BRep_Builder  # type: ignore[import]
    from OCC.Core.BRepGProp import brepgprop_VolumeProperties, brepgprop_SurfaceProperties  # type: ignore[import]
    from OCC.Core.GProp import GProp_GProps  # type: ignore[import]
    from OCC.Core.BRepBndLib import brepbndlib_Add  # type: ignore[import]
    from OCC.Core.Bnd import Bnd_Box  # type: ignore[import]
    from OCC.Core.BRepCheck import BRepCheck_Analyzer  # type: ignore[import]
    from OCC.Core.TopExp import TopExp_Explorer  # type: ignore[import]
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID  # type: ignore[import]
    from OCC.Core.IFSelect import IFSelect_RetDone  # type: ignore[import]
    return {
        "STEPControl_Reader": STEPControl_Reader,
        "BRep_Builder": BRep_Builder,
        "brepgprop_VolumeProperties": brepgprop_VolumeProperties,
        "brepgprop_SurfaceProperties": brepgprop_SurfaceProperties,
        "GProp_GProps": GProp_GProps,
        "brepbndlib_Add": brepbndlib_Add,
        "Bnd_Box": Bnd_Box,
        "BRepCheck_Analyzer": BRepCheck_Analyzer,
        "TopExp_Explorer": TopExp_Explorer,
        "TopAbs_FACE": TopAbs_FACE,
        "TopAbs_SOLID": TopAbs_SOLID,
        "IFSelect_RetDone": IFSelect_RetDone,
    }


# ---------------------------------------------------------------------------
# Internal helpers
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
    )


def _load_step(occ, path: str):
    """Load a STEP file and return the top-level OCC shape."""
    reader = occ["STEPControl_Reader"]()
    status = reader.ReadFile(path)
    if status != occ["IFSelect_RetDone"]:
        raise ValueError(f"STEPControl_Reader.ReadFile() failed for {path!r}")
    reader.TransferRoots()
    return reader.OneShape()


def _measure_volume(occ, shape) -> float | None:
    """Return volume in mm³, or None on failure."""
    try:
        props = occ["GProp_GProps"]()
        occ["brepgprop_VolumeProperties"](shape, props)
        v = props.Mass()
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _measure_surface_area(occ, shape) -> float | None:
    """Return surface area in mm², or None on failure."""
    try:
        props = occ["GProp_GProps"]()
        occ["brepgprop_SurfaceProperties"](shape, props)
        a = props.Mass()
        return a if math.isfinite(a) else None
    except Exception:
        return None


def _measure_bounding_box(occ, shape) -> dict[str, float] | None:
    """Return {'x_mm', 'y_mm', 'z_mm'} extents, or None on failure."""
    try:
        bb = occ["Bnd_Box"]()
        occ["brepbndlib_Add"](shape, bb)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return {
            "x_mm": abs(xmax - xmin),
            "y_mm": abs(ymax - ymin),
            "z_mm": abs(zmax - zmin),
        }
    except Exception:
        return None


def _check_degenerate_faces(occ, shape) -> tuple[bool, list[str]]:
    """Return (pass, failures). Fails if any face has area < _MIN_FACE_AREA_MM2."""
    failures: list[str] = []
    try:
        explorer = occ["TopExp_Explorer"](shape, occ["TopAbs_FACE"])
        face_count = 0
        degenerate_count = 0
        while explorer.More():
            face = explorer.Current()
            try:
                props = occ["GProp_GProps"]()
                occ["brepgprop_SurfaceProperties"](face, props)
                area = props.Mass()
                if area < _MIN_FACE_AREA_MM2:
                    degenerate_count += 1
            except Exception:
                pass  # Skip unmeasurable face; don't count as degenerate
            face_count += 1
            explorer.Next()
        if degenerate_count > 0:
            failures.append(
                f"{degenerate_count}/{face_count} face(s) have area < {_MIN_FACE_AREA_MM2} mm² "
                "(degenerate faces detected)"
            )
        return len(failures) == 0, failures
    except Exception as exc:
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
        has_solid = explorer.More()
        if not has_solid:
            return False, ["Shape contains no solid sub-shapes (open shell or empty compound)"]
        return True, []
    except Exception as exc:
        return False, [f"Solid check error: {exc}"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def certify_geometry(step_path: str) -> GeometryCertificate:
    """Run all 6 OCC geometry checks on a STEP file.

    Args:
        step_path: Absolute or relative path to a .step file.

    Returns:
        GeometryCertificate — always returns a value, never raises.
        On OCC import failure or file error, returns a failed certificate.
    """
    # --- Guard: file must exist ---
    if not os.path.isfile(step_path):
        return _failed_cert(f"STEP file not found: {step_path!r}")

    # --- Guard: OCC must be available ---
    try:
        occ = _import_occ()
    except (ImportError, ModuleNotFoundError) as exc:
        return _failed_cert(
            f"OCC not available ({exc}). Install with: "
            "conda install -c conda-forge pythonocc-core"
        )

    # --- Load shape ---
    try:
        shape = _load_step(occ, step_path)
    except Exception as exc:
        return _failed_cert(f"STEP load error: {exc}")

    # --- Run all 6 checks ---
    checks: dict[str, bool] = {}
    failures: list[str] = []

    # 1. Volume
    volume = _measure_volume(occ, shape)
    volume_pass = volume is not None and volume > 0.0
    checks["volume_positive"] = volume_pass
    if not volume_pass:
        failures.append(
            f"Volume is {'None' if volume is None else f'{volume:.4f} mm³'} "
            "(expected > 0)"
        )

    # 2. Surface area
    surface_area = _measure_surface_area(occ, shape)
    sa_pass = surface_area is not None and surface_area > 0.0
    checks["surface_area_positive"] = sa_pass
    if not sa_pass:
        failures.append(
            f"Surface area is {'None' if surface_area is None else f'{surface_area:.4f} mm²'} "
            "(expected > 0)"
        )

    # 3. Degenerate faces
    no_degen_pass, degen_failures = _check_degenerate_faces(occ, shape)
    checks["no_degenerate_faces"] = no_degen_pass
    failures.extend(degen_failures)

    # 4. Bounding box finite
    bb = _measure_bounding_box(occ, shape)
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

    # 5. BRep validity
    brep_pass, brep_failures = _check_brep_valid(occ, shape)
    checks["brep_valid"] = brep_pass
    failures.extend(brep_failures)

    # 6. Is solid
    solid_pass, solid_failures = _check_is_solid(occ, shape)
    checks["is_solid"] = solid_pass
    failures.extend(solid_failures)

    all_passed = all(checks.values())

    return GeometryCertificate(
        passed=all_passed,
        volume_mm3=volume,
        surface_area_mm2=surface_area,
        bounding_box=bb,
        checks=checks,
        failures=failures,
    )
