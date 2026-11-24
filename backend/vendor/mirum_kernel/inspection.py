"""Measure the delivered bores and packaging from a fresh XDE read."""

from collections import Counter, defaultdict
from math import dist, isclose, tau
from pathlib import Path

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.gp import gp_Pnt

from .design import ALIGNMENT_ROUNDOFF_MM, GEOMETRY_ROUNDOFF_MM, Design, KernelError, read_json, tray_hole_id
from .exchange import BOM, MANIFEST, READER, STEP, bom_record, bounds, file_record, read_step, tool_versions


def inspect_delivery(design: Design, directory: Path) -> dict:
    """Check the package's input and artifact bindings, then measure its exact STEP bytes."""
    directory = Path(directory)
    manifest = read_json((directory / MANIFEST).read_bytes(), MANIFEST)
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1 or manifest.get("units") != "mm"
            or manifest.get("assembly_id") != design.id):
        raise KernelError("manifest: invalid assembly, schema version or units")
    versions = manifest.get("tool_versions")
    if (not isinstance(manifest.get("writer"), str) or not manifest["writer"]
            or not isinstance(versions, dict)
            or any(not isinstance(versions.get(name), str) or not versions[name]
                   for name in ("python", "cadquery", "cadquery-ocp"))):
        raise KernelError("manifest: missing writer or tool versions")
    inputs = manifest.get("inputs")
    if (inputs != design.inputs
            or ("rotation_override_deg" in inputs and type(inputs["rotation_override_deg"]) is not int)):
        raise KernelError("manifest: input digests or rotation do not match the supplied design")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or artifacts.keys() != {STEP, BOM}:
        raise KernelError("manifest: expected STEP and BOM artifact records")
    step, bom = ((directory / name).read_bytes() for name in (STEP, BOM))
    for name, data in ((STEP, step), (BOM, bom)):
        ref = artifacts[name]
        if ref != file_record(data) or type(ref["bytes"]) is not int:
            raise KernelError(f"manifest: size or sha256 mismatch for {name}")
    inventory = read_json(bom, BOM)
    if (inventory != bom_record(design) or type(inventory["schema_version"]) is not int
            or any(type(item["quantity"]) is not int for item in inventory["items"])):
        raise KernelError("BOM: definition revisions or occurrence quantities differ from the supplied design")
    return inspect_step(design, step)


def _bores(solid, diameter):
    """Extract local XY axes of inward, vertical, full-height cylindrical faces."""
    _, _, floor, _, _, top = bounds(solid)
    centers = []
    for face in solid.Faces():
        if face.geomType() != "CYLINDER":
            continue
        surface = BRepAdaptor_Surface(face.wrapped)
        cylinder = surface.Cylinder()
        if not (
            _near(cylinder.Radius(), diameter / 2)
            and _near(abs(cylinder.Axis().Direction().Z()), 1)
            and _near(surface.LastUParameter() - surface.FirstUParameter(), tau)
        ):
            continue
        _, _, start, _, _, end = bounds(face)
        if not (_near(start, floor) and _near(end, top)):
            continue
        axis = cylinder.Location()
        point = face.positionAt(0.5, 0.5)
        normal = face.normalAt(point)
        if (point.x - axis.X()) * normal.x + (point.y - axis.Y()) * normal.y < 0:
            centers.append((axis.X(), axis.Y()))
    return centers


def _near(left, right):
    return isclose(left, right, rel_tol=0, abs_tol=GEOMETRY_ROUNDOFF_MM)


def _bind_holes(part, anchors, design, diagnostics):
    """Bind each authored ID only when it and its measured candidate are mutually unique."""
    if part is None:
        return {}
    transform = part.location.Transformation()
    if not all(_near(transform.Value(row, 3), target) for row, target in ((1, 0), (2, 0), (3, 1))):
        diagnostics.append({"code": "mounting_axes_not_in_assembly_z", "occurrence_id": part.id})
        return {}
    measured = _bores(part.solid, design.diameter)
    candidates = {
        name: [i for i, point in enumerate(measured) if dist(anchor, point) <= design.lookup_radius]
        for name, anchor in anchors
    }
    claims = Counter(i for indices in candidates.values() for i in indices)
    bound = {}
    for name, indices in candidates.items():
        if len(indices) != 1 or claims[indices[0]] != 1:
            diagnostics.append({
                "code": "hole_missing" if not indices else "hole_ambiguous",
                "occurrence_id": part.id,
                "hole_id": name,
                "candidate_count": len(indices),
            })
            continue
        x, y = measured[indices[0]]
        world = gp_Pnt(x, y, 0).Transformed(transform)
        bound[name] = (world.X(), world.Y())
    if len(claims) != len(measured):
        diagnostics.append({"code": "unexpected_bores", "occurrence_id": part.id,
                            "count": len(measured) - len(claims)})
    return bound


def _find_occurrences(design, parts, diagnostics):
    by_name = defaultdict(list)
    for part in parts:
        by_name[part.id].append(part)
    found = {}
    expected_ids = {occurrence.id for occurrence in design.occurrences}
    for name in sorted(set(by_name) - expected_ids, key=lambda value: value or ""):
        diagnostics.append({"code": "occurrence_unexpected", "occurrence_id": name})
    for occurrence in design.occurrences:
        matches = by_name[occurrence.id]
        if len(matches) != 1:
            diagnostics.append({"code": "occurrence_ambiguous" if matches else "occurrence_missing",
                                "occurrence_id": occurrence.id, "candidate_count": len(matches)})
            continue
        part = matches[0]
        if part.definition != occurrence.definition:
            diagnostics.append({
                "code": "definition_revision_mismatch", "occurrence_id": occurrence.id,
                "expected": "@".join(occurrence.definition), "delivered": part.definition_name,
            })
        actual = part.location.Transformation()
        expected = cq.Location(occurrence.origin, (0, 0, 1), occurrence.rotation_deg).wrapped.Transformation()
        actual_matrix = [[actual.Value(row, col) for col in (1, 2, 3, 4)] for row in (1, 2, 3)]
        expected_matrix = [[expected.Value(row, col) for col in (1, 2, 3, 4)] for row in (1, 2, 3)]
        if any(not _near(a, b) for a_row, b_row in zip(actual_matrix, expected_matrix)
               for a, b in zip(a_row, b_row)):
            diagnostics.append({
                "code": "occurrence_placement_mismatch", "occurrence_id": occurrence.id,
                "expected_matrix": expected_matrix, "delivered_matrix": actual_matrix,
            })
        found[occurrence.id] = part
    return found


def _xy(point):
    return None if point is None else [round(value, 8) + 0.0 for value in point]


def _envelope(design, parts, complete):
    result = {"allowance_mm": list(design.bay_size), "measured_mm": None, "outcome": "unresolved"}
    if parts and complete:
        boxes = [bounds(part.solid.moved(cq.Location(part.location))) for part in parts]
        size = tuple(max(box[axis + 3] for box in boxes) - min(box[axis] for box in boxes)
                     for axis in (0, 1))
        result.update(measured_mm=_xy(size),
                      outcome="pass" if all(a <= b or _near(a, b)
                                            for a, b in zip(size, design.bay_size)) else "fail")
    return result


def inspect_step(design: Design, step: bytes) -> dict:
    """Inspect exact STEP bytes against already validated, independently supplied expectations."""
    parts, diagnostics = read_step(step, design.id)
    envelope = _envelope(design, parts, complete=not diagnostics)
    found = _find_occurrences(design, parts, diagnostics)
    tray = next(definition for definition in design.definitions if definition.key == design.tray.definition)
    tray_axes = _bind_holes(found.get(design.tray.id), tray.holes, design, diagnostics)

    rows = []
    for module in sorted(design.modules, key=lambda occurrence: occurrence.id):
        part = found.get(module.id)
        module_axes = _bind_holes(part, design.module_holes, design, diagnostics)
        delivered_id, delivered_revision = part.definition if part and part.definition else (None, None)
        for name, _ in design.module_holes:
            tray_id = tray_hole_id(module.id, name)
            module_xy, tray_xy = module_axes.get(name), tray_axes.get(tray_id)
            offset = dist(module_xy, tray_xy) if module_xy is not None and tray_xy is not None else None
            if offset is None:
                outcome = "unresolved"
            elif offset <= design.alignment_tolerance + ALIGNMENT_ROUNDOFF_MM:
                outcome = "pass"
            else:
                outcome = "fail"
            rows.append({
                "occurrence_id": module.id,
                "definition_id": delivered_id,
                "revision": delivered_revision,
                "expected_definition_id": module.definition[0],
                "expected_revision": module.definition[1],
                "module_hole_id": name,
                "tray_hole_id": tray_id,
                "module_xy_mm": _xy(module_xy),
                "tray_xy_mm": _xy(tray_xy),
                "offset_mm": None if offset is None else round(offset, 8),
                "tolerance_mm": design.alignment_tolerance,
                "outcome": outcome,
            })
    counts = Counter(row["outcome"] for row in rows)
    passed = not diagnostics and envelope["outcome"] == "pass" and counts["pass"] == len(rows)
    return {
        "schema_version": 1,
        "assembly_id": design.id,
        "tray_occurrence_id": design.tray.id,
        "inputs": design.inputs,
        "step": file_record(step),
        "reader": READER,
        "tool_versions": tool_versions(),
        "units": "mm",
        "frame": "assembly XY, projected along +Z",
        "alignment_criterion": "digital fixture criterion",
        "outcome": "pass" if passed else "fail",
        "envelope": envelope,
        "mounting": {
            "counts": {outcome: counts[outcome] for outcome in ("pass", "fail", "unresolved")},
            "correspondences": rows,
        },
        "diagnostics": diagnostics,
    }
