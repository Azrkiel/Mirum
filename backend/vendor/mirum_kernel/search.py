"""Exhaustively evaluate the two uniform orientations supported by the bay fixture."""

from math import dist

from .design import (
    ALIGNMENT_ROUNDOFF_MM,
    GEOMETRY_ROUNDOFF_MM,
    ORIENTATIONS_DEG,
    Design,
    parse_design,
    tray_hole_id,
)


def _candidate(design: Design) -> dict:
    definitions = {part.key: part for part in design.definitions}
    tray = definitions[design.tray.definition]
    size = tray.size[:2]
    clearance = [limit - span for limit, span in zip(design.bay_size, size)]
    fits = min(clearance) >= -GEOMETRY_ROUNDOFF_MM
    tray_holes = dict(tray.holes)
    failing_holes = []
    for occurrence in sorted(design.modules, key=lambda module: module.id):
        for name, local in definitions[occurrence.definition].holes:
            tray_id = tray_hole_id(occurrence.id, name)
            module_xy = occurrence.to_assembly_xy(local)
            tray_xy = tray_holes[tray_id]
            offset = dist(module_xy, tray_xy)
            if offset > design.alignment_tolerance + ALIGNMENT_ROUNDOFF_MM:
                failing_holes.append({
                    "occurrence_id": occurrence.id,
                    "module_hole_id": name,
                    "tray_hole_id": tray_id,
                    "module_xy_mm": module_xy,
                    "tray_xy_mm": tray_xy,
                    "offset_mm": offset,
                })
    return {
        "rotation_deg": design.modules[0].rotation_deg,
        "predicted_envelope_mm": size,
        "clearance_mm": clearance,
        "packaging_fits": fits,
        "alignment_tolerance_mm": design.alignment_tolerance,
        "failed_mounting_holes": failing_holes,
        "feasible": fits and not failing_holes,
    }


def search_layout(data: bytes, *, substitution: bytes | None = None) -> dict:
    """Predict both candidates from one input snapshot. The authored angle has priority."""
    original = parse_design(data, substitution=substitution)
    preferred = original.modules[0].rotation_deg
    (other,) = (angle for angle in ORIENTATIONS_DEG if angle != preferred)
    alternate = parse_design(data, substitution=substitution, rotation_deg=other)
    candidates = [_candidate(design) for design in (original, alternate)]
    selection = next((item["rotation_deg"] for item in candidates if item["feasible"]), None)
    return {
        "schema_version": 1,
        "assembly_id": original.id,
        "inputs": original.inputs,
        "units": "mm",
        "module_count": len(original.modules),
        "bay_allowance_mm": original.bay_size,
        "scope": "two fully occupied rows with uniform 0 or 90 degree orientation",
        "source": "analytic prediction from the design",
        "outcome": "infeasible" if selection is None else "feasible",
        "selected_rotation_deg": selection,
        "candidates": candidates,
    }
