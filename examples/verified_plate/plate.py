"""Bounded nominal mounting-plate demonstrator, not engineering certification.

Millimetres. Fixed XYZ datum, bottom at Z=0. Sharp rectangular plate with four
identical Z through-bores. This module is NOT wired into Mirum's live pipeline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_SOLID
from OCP.TopoDS import TopoDS_Iterator


def _has_loose_topology(wrapped):
    """Only compound wrappers and complete solids may occur at the body level."""
    if wrapped.IsNull():
        return True
    if wrapped.ShapeType() == TopAbs_SOLID:
        return False
    if wrapped.ShapeType() != TopAbs_COMPOUND:
        return True
    children = TopoDS_Iterator(wrapped)
    count = 0
    while children.More():
        count += 1
        if _has_loose_topology(children.Value()):
            return True
        children.Next()
    return count == 0


@dataclass(frozen=True)
class PlateSpec:
    length_mm: float = 80.0
    width_mm: float = 50.0
    thickness_mm: float = 6.0
    hole_diameter_mm: float = 6.0
    pitch_x_mm: float = 60.0
    pitch_y_mm: float = 30.0
    dimension_tolerance_mm: float = 0.01
    kernel_tolerance_mm: float = 0.001

    def __post_init__(self):
        for name, value in asdict(self).items():
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive real number")
        if max(self.length_mm, self.width_mm, self.thickness_mm) > 10000:
            raise ValueError("Demonstrator supports dimensions up to 10000 mm")
        if self.dimension_tolerance_mm > min(0.1, self.thickness_mm / 20,
                                             self.hole_diameter_mm / 20):
            raise ValueError("Dimensional tolerance too coarse for this feature scale")
        if self.kernel_tolerance_mm > self.dimension_tolerance_mm / 10:
            raise ValueError("Kernel tolerance must be <= one tenth of dimensional tolerance")
        margin = 4 * self.dimension_tolerance_mm
        if (self.pitch_x_mm <= self.hole_diameter_mm + margin
                or self.pitch_y_mm <= self.hole_diameter_mm + margin
                or self.length_mm - self.pitch_x_mm - self.hole_diameter_mm <= margin
                or self.width_mm - self.pitch_y_mm - self.hole_diameter_mm <= margin):
            raise ValueError("Hole pattern overlaps or has insufficient edge ligament")

    def hole_centers(self):
        return [(x * self.pitch_x_mm / 2, y * self.pitch_y_mm / 2)
                for x in (-1, 1) for y in (-1, 1)]


def build_plate(spec: PlateSpec) -> cq.Shape:
    """Trusted deterministic feature construction, no generated Python."""
    return (cq.Workplane("XY")
            .box(spec.length_mm, spec.width_mm, spec.thickness_mm,
                 centered=(True, True, False))
            .faces(">Z").workplane()
            .pushPoints(spec.hole_centers()).hole(spec.hole_diameter_mm).val())


def _bounds(shape):
    box = Bnd_Box()
    # Do not let tessellation caches or padded boxes determine dimensions.
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    return list(box.Get())


def validate_plate(shape: cq.Shape, spec: PlateSpec) -> dict:
    """Measure B-rep surfaces, not source text or planner assertions."""
    report = {"accepted": False, "scope": "bounded_nominal_four_hole_plate",
              "units": "mm", "spec": asdict(spec), "issues": [],
              "solid_count": 0, "hole_count": 0}
    issues = report["issues"]
    try:
        if _has_loose_topology(shape.wrapped):
            issues.append("Loose/unsupported topology outside solid bodies")
        solids = shape.Solids()
        report["solid_count"] = len(solids)
        if len(solids) != 1:
            issues.append("Expected exactly one solid")
        if not BRepCheck_Analyzer(shape.wrapped, True).IsValid():
            issues.append("Invalid B-rep")
        if not solids:
            return report
        if not all(shell.Closed() for shell in solids[0].Shells()):
            issues.append("Open solid shell")
        volume = shape.Volume()
        report["volume_mm3"] = volume
        if not math.isfinite(volume) or volume <= 0:
            issues.append("Nonpositive/nonfinite volume")
        tolerance = max(BRep_Tool.Tolerance_s(item.wrapped)
                        for item in shape.Vertices() + shape.Edges() + shape.Faces())
        report["max_topology_tolerance_mm"] = tolerance
        if not math.isfinite(tolerance) or tolerance > spec.kernel_tolerance_mm:
            issues.append("Kernel topology tolerance exceeds budget")
        bounds = _bounds(shape)
        report["bounds_mm"] = bounds
        dimensions = [bounds[i + 3] - bounds[i] for i in range(3)]
        report["measured_dimensions_mm"] = dict(zip(("length", "width", "thickness"), dimensions))
        if any(not math.isfinite(value) or abs(value - target) > spec.dimension_tolerance_mm
               for value, target in zip(dimensions, (spec.length_mm, spec.width_mm, spec.thickness_mm))):
            issues.append("Measured size exceeds dimensional tolerance")
        expected_bounds = [-spec.length_mm / 2, -spec.width_mm / 2, 0,
                           spec.length_mm / 2, spec.width_mm / 2, spec.thickness_mm]
        if any(not math.isfinite(v) or abs(v - e) > spec.dimension_tolerance_mm
               for v, e in zip(bounds, expected_bounds)):
            issues.append("Outside dimensions/datum do not match")
        cylinders = []
        planar_count = 0
        expected_planes = [(0, -spec.length_mm / 2), (0, spec.length_mm / 2),
                           (1, -spec.width_mm / 2), (1, spec.width_mm / 2),
                           (2, 0.0), (2, spec.thickness_mm)]
        for face in shape.Faces():
            surface = BRepAdaptor_Surface(face.wrapped, True)
            kind = surface.GetType()
            if kind == GeomAbs_Cylinder:
                cylinder = surface.Cylinder()
                axis = cylinder.Axis().Direction()
                if math.hypot(axis.X(), axis.Y()) > 1e-9:
                    issues.append("Hole axis is not parallel to Z")
                face_bounds = _bounds(face)
                if (abs(face_bounds[2]) > spec.dimension_tolerance_mm
                        or abs(face_bounds[5] - spec.thickness_mm) > spec.dimension_tolerance_mm):
                    issues.append("Hole does not span the full plate thickness")
                cylinders.append({"x_mm": cylinder.Location().X(),
                                  "y_mm": cylinder.Location().Y(),
                                  "diameter_mm": 2 * cylinder.Radius()})
            elif kind == GeomAbs_Plane:
                planar_count += 1
                plane = surface.Plane()
                normal = plane.Axis().Direction()
                direction = [normal.X(), normal.Y(), normal.Z()]
                position = [plane.Location().X(), plane.Location().Y(), plane.Location().Z()]
                matches = [p for p in expected_planes
                           if abs(abs(direction[p[0]]) - 1) <= 1e-12
                           and all(abs(direction[k]) <= 1e-9 for k in range(3) if k != p[0])
                           and abs(position[p[0]] - p[1]) <= spec.dimension_tolerance_mm]
                if len(matches) != 1:
                    issues.append("Exterior plane is not the requested rectangular datum plane")
                else:
                    expected_planes.remove(matches[0])
            else:
                issues.append("Unsupported surface in bounded plate")
        report["hole_count"] = len(cylinders)
        report["measured_holes"] = cylinders
        if planar_count != 6 or len(cylinders) != 4:
            issues.append("Expected six planes and four cylindrical through-holes")
        remaining = list(spec.hole_centers())
        for cylinder in cylinders:
            matches = [p for p in remaining if math.hypot(
                cylinder["x_mm"] - p[0], cylinder["y_mm"] - p[1])
                <= spec.dimension_tolerance_mm]
            if not matches:
                issues.append("Hole location/count does not match")
            else:
                remaining.remove(matches[0])
            if abs(cylinder["diameter_mm"] - spec.hole_diameter_mm) > spec.dimension_tolerance_mm:
                issues.append("Hole diameter does not match")
        if remaining:
            issues.append("Missing requested holes")
        report["accepted"] = not issues
    except Exception as exc:
        issues.append(f"Measurement failed: {type(exc).__name__}: {exc}")
    return report



def export_verified_plate(spec: PlateSpec, output_dir) -> dict:
    """Single-owner fresh-directory demo export. Failed runs publish no STEP.

    This is not a concurrent artifact store or a hostile STEP-file sandbox.
    Reimport and source/export comparison use the same OCCT kernel.
    """
    import hashlib
    import json
    from pathlib import Path
    import tempfile
    import OCP
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone

    shape = build_plate(spec)
    source_report = validate_plate(shape, spec)
    if not source_report["accepted"]:
        raise ValueError(f"Source validation failed: {source_report['issues']}")
    output = Path(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    # Stage beside the absent destination on the same filesystem. Only the
    # complete directory is published, so handled I/O failure cannot expose
    # STEP without its matching evidence. This is not power-loss durability.
    with tempfile.TemporaryDirectory(prefix=".plate-stage-", dir=output.parent) as stage:
        staged_step = Path(stage) / "plate.step"
        cq.exporters.export(shape, str(staged_step), exportType="STEP")
        reader = STEPControl_Reader()
        if reader.ReadFile(str(staged_step)) != IFSelect_RetDone or reader.TransferRoots() <= 0:
            raise ValueError("STEP round-trip read/transfer failed")
        imported = cq.Shape.cast(reader.OneShape())
        exported_report = validate_plate(imported, spec)
        if not exported_report["accepted"]:
            raise ValueError(f"STEP round-trip validation failed: {exported_report['issues']}")
        # Compare actual source/exported solids, not just their bounding boxes.
        difference = shape.cut(imported).Volume() + imported.cut(shape).Volume()
        budget = max(1e-6, source_report["volume_mm3"] * 1e-9)
        volume_delta = abs(source_report["volume_mm3"] - exported_report["volume_mm3"])
        if (not math.isfinite(difference) or difference < 0 or difference > budget
                or not math.isfinite(volume_delta) or volume_delta > budget):
            raise ValueError(f"STEP round-trip geometry changed: Boolean={difference}, volume={volume_delta} mm3")
        # Boolean operations may absorb differences near OCCT tolerance. Compare
        # independently measured feature parameters too. The accepted source,
        # not inflated imported tolerances, sets this comparison floor.
        feature_budget = max(1e-8, 2 * source_report["max_topology_tolerance_mm"])
        source_features = sorted((h["x_mm"], h["y_mm"], h["diameter_mm"])
                                 for h in source_report["measured_holes"])
        exported_features = sorted((h["x_mm"], h["y_mm"], h["diameter_mm"])
                                   for h in exported_report["measured_holes"])
        deltas = [abs(a-b) for a, b in zip(source_report["bounds_mm"], exported_report["bounds_mm"])]
        deltas += [abs(a-b) for sa, sb in zip(source_features, exported_features) for a, b in zip(sa, sb)]
        max_feature_delta = max(deltas)
        if not math.isfinite(max_feature_delta) or max_feature_delta > feature_budget:
            raise ValueError(f"STEP round-trip measured features changed: {max_feature_delta} mm")
        spec_bytes = json.dumps(asdict(spec), sort_keys=True, allow_nan=False).encode()
        report = {"scope": "bounded_nominal_four_hole_plate", "engineering_certified": False,
                  "units": "mm", "source_validation": source_report,
                  "step_validation": exported_report,
                  "symmetric_difference_mm3": difference,
                  "absolute_volume_change_mm3": volume_delta,
                  "max_roundtrip_feature_change_mm": max_feature_delta,
                  "roundtrip_feature_budget_mm": feature_budget,
                  "roundtrip_volume_budget_mm3": budget,
                  "step_sha256": hashlib.sha256(staged_step.read_bytes()).hexdigest(),
                  "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
                  "validator_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "versions": {"cadquery": cq.__version__, "ocp": OCP.__version__},
                  "step_path": str(output / "plate.step"),
                  "evidence_path": str(output / "validation.json"),
                  "limitations": ["No standards compliance or engineering sign-off",
                                  "No material/process/load/strength/inspection verification",
                                  "Same-kernel reimport, not independent-kernel proof",
                                  "Fixed sharp-edged plate family only; not a generic validator",
                                  "Uncertainties are not a manufacturing GD&T model"]}
        # A fresh directory prevents stale success evidence from a prior run.
        staged_report = Path(stage) / "validation.json"
        staged_report.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        Path(stage).replace(output)
    return report


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", help="New output directory; existing directories are rejected")
    args = parser.parse_args()
    print(json.dumps(export_verified_plate(PlateSpec(), args.output_dir), indent=2))
