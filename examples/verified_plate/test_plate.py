"""Native tests: no mocks, no model calls, no missing-kernel skips."""
from dataclasses import replace
import importlib.util
from pathlib import Path

import cadquery as cq
import pytest


def load_plate():
    path = Path(__file__).with_name("plate.py")
    assert path.is_file(), "Missing deterministic plate builder and acceptance gate"
    spec = importlib.util.spec_from_file_location("verified_plate", path)
    import sys
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_correct_native_plate_and_wrong_box():
    plate = load_plate()
    spec = plate.PlateSpec()
    shape = plate.build_plate(spec)
    result = plate.validate_plate(shape, spec)
    assert result["accepted"], result
    assert result["solid_count"] == 1
    assert result["hole_count"] == 4
    assert result["max_topology_tolerance_mm"] <= spec.kernel_tolerance_mm
    # A valid B-rep with the same outside dimensions is NOT the requested part.
    box = cq.Workplane("XY").box(spec.length_mm, spec.width_mm, spec.thickness_mm,
                                  centered=(True, True, False)).val()
    assert box.isValid()
    rejected = plate.validate_plate(box, spec)
    assert not rejected["accepted"]
    assert any("hole" in issue.lower() for issue in rejected["issues"])


def test_loose_topology_is_not_hidden_by_a_valid_solid():
    plate = load_plate()
    spec = plate.PlateSpec()
    shape = plate.build_plate(spec)
    loose_edge = cq.Edge.makeLine(cq.Vector(0, 0, 2), cq.Vector(1, 0, 2))
    compound = cq.Compound.makeCompound([shape, loose_edge])
    assert compound.isValid()
    result = plate.validate_plate(compound, spec)
    assert not result["accepted"], "A valid solid must not hide loose edges"
    assert any("loose" in issue.lower() for issue in result["issues"])


@pytest.mark.parametrize("changes", [
    {"length_mm": float("nan")}, {"width_mm": float("inf")},
    {"thickness_mm": 0}, {"hole_diameter_mm": -1}, {"pitch_x_mm": True},
    {"pitch_y_mm": "30"}, {"length_mm": 20000},
    {"pitch_x_mm": 78}, {"pitch_y_mm": 5},
    {"dimension_tolerance_mm": 0}, {"dimension_tolerance_mm": 10},
    {"kernel_tolerance_mm": 0.1}, {"kernel_tolerance_mm": float("nan")},
])
def test_invalid_spec_is_rejected_before_native_construction(changes):
    plate = load_plate()
    with pytest.raises(ValueError):
        plate.PlateSpec(**changes)


def test_tilted_bores_are_not_misreported_as_z_through_holes():
    plate = load_plate()
    spec = plate.PlateSpec()
    shape = cq.Workplane("XY").box(80, 50, 6, centered=(True, True, False)).val()
    for x, y in spec.hole_centers():
        cutter = cq.Solid.makeCylinder(3, 30, cq.Vector(x, y, -10), cq.Vector(0.1, 0, 1))
        shape = shape.cut(cutter)
    assert shape.isValid()
    assert len(shape.Faces()) == 10
    result = plate.validate_plate(shape, spec)
    assert not result["accepted"], "Cylinder origin alone does not prove bore axis/depth"


@pytest.mark.parametrize("mutation", ["blind", "shifted", "oversized", "missing", "extra_body",
                                       "shell", "wrong_thickness", "high_kernel_tolerance"])
def test_native_wrong_geometry_is_rejected(mutation):
    plate = load_plate()
    spec = plate.PlateSpec()
    if mutation in {"blind", "shifted", "oversized", "missing"}:
        points = spec.hole_centers()
        if mutation == "shifted":
            points[0] = (points[0][0] + 0.1, points[0][1])
        if mutation == "missing":
            points = points[:-1]
        work = (cq.Workplane("XY").box(80, 50, 6, centered=(True, True, False))
                .faces(">Z").workplane().pushPoints(points))
        shape = work.hole(6.1 if mutation == "oversized" else 6,
                          depth=3 if mutation == "blind" else None).val()
    else:
        shape = plate.build_plate(spec)
        if mutation == "extra_body":
            shape = cq.Compound.makeCompound([shape, shape.translate((0, 0, 10))])
        elif mutation == "shell":
            shape = cq.Shell.makeShell(shape.Faces()[:-1])
        elif mutation == "wrong_thickness":
            shape = plate.build_plate(replace(spec, thickness_mm=6.1))
        elif mutation == "high_kernel_tolerance":
            from OCP.BRep import BRep_Builder
            BRep_Builder().UpdateVertex(shape.Vertices()[0].wrapped, 0.002)
    result = plate.validate_plate(shape, spec)
    assert not result["accepted"], (mutation, result)


def test_small_bore_diameter_deviation_within_explicit_budget_is_accepted():
    plate = load_plate()
    spec = plate.PlateSpec()
    shape = plate.build_plate(replace(spec, hole_diameter_mm=6.005))
    assert plate.validate_plate(shape, spec)["accepted"]


def test_step_export_is_reimported_revalidated_and_hash_bound(tmp_path):
    import hashlib
    import json
    plate = load_plate()
    exporter = getattr(plate, "export_verified_plate", None)
    assert callable(exporter), "Missing verified STEP export path"
    result = exporter(plate.PlateSpec(), tmp_path / "run")
    step_path = Path(result["step_path"])
    assert step_path.is_file()
    assert result["source_validation"]["accepted"]
    assert result["step_validation"]["accepted"]
    assert result["step_sha256"] == hashlib.sha256(step_path.read_bytes()).hexdigest()
    assert json.loads(Path(result["evidence_path"]).read_text())["step_sha256"] == result["step_sha256"]
    assert result["symmetric_difference_mm3"] <= result["roundtrip_volume_budget_mm3"]


def test_changed_but_still_in_tolerance_export_is_not_the_accepted_source(tmp_path, monkeypatch):
    plate = load_plate()
    exporter = getattr(plate, "export_verified_plate", None)
    assert callable(exporter), "Missing verified STEP export path"
    original_export = cq.exporters.export
    wrong_shape = plate.build_plate(replace(plate.PlateSpec(), hole_diameter_mm=6.005))
    assert plate.validate_plate(wrong_shape, plate.PlateSpec())["accepted"]
    def export_other_geometry(shape, filename, *args, **kwargs):
        return original_export(wrong_shape, filename, *args, **kwargs)
    monkeypatch.setattr(cq.exporters, "export", export_other_geometry)
    with pytest.raises(ValueError, match="round.trip"):
        exporter(plate.PlateSpec(), tmp_path / "run")
    assert not (tmp_path / "run" / "plate.step").exists()
    assert not (tmp_path / "run" / "validation.json").exists()


def test_trapezoid_with_matching_bbox_and_holes_is_not_a_rectangular_plate():
    plate = load_plate()
    spec = plate.PlateSpec()
    shape = (cq.Workplane("XY").polyline([(-40, -25), (40, -25), (39, 25), (-39, 25)])
             .close().extrude(6).faces(">Z").workplane(origin=(0, 0, 6))
             .pushPoints(spec.hole_centers()).hole(6).val())
    assert shape.isValid()
    assert len(shape.Faces()) == 10
    result = plate.validate_plate(shape, spec)
    assert not result["accepted"], "BBox and feature counts do not prove the requested rectangle"


def test_size_tolerance_is_not_doubled_by_checking_each_endpoint_only():
    plate = load_plate()
    spec = plate.PlateSpec()
    shape = plate.build_plate(replace(spec, length_mm=80.015))
    result = plate.validate_plate(shape, spec)
    assert not result["accepted"], "80.015 exceeds a +/-0.01 length tolerance"


def test_near_kernel_difference_cannot_hide_behind_zero_boolean_volume(tmp_path, monkeypatch):
    plate = load_plate()
    spec = plate.PlateSpec()
    original_export = cq.exporters.export
    altered = plate.build_plate(replace(spec, hole_diameter_mm=6.0000004))
    source = plate.build_plate(spec)
    assert abs(altered.Volume() - source.Volume()) > source.Volume() * 1e-9
    def export_altered(shape, filename, *args, **kwargs):
        return original_export(altered, filename, *args, **kwargs)
    monkeypatch.setattr(cq.exporters, "export", export_altered)
    with pytest.raises(ValueError, match="round.trip"):
        plate.export_verified_plate(spec, tmp_path / "run")


def test_publication_failure_leaves_no_step_or_partial_acceptance(tmp_path, monkeypatch):
    plate = load_plate()
    original_replace = Path.replace
    calls = []
    def fail_publication(path, target):
        calls.append(str(target))
        # Fail the final directory publication, or the old design's second
        # per-file publication. In neither design may a failed run leave STEP.
        if path.is_dir() or len(calls) == 2:
            raise OSError("Injected publication failure")
        return original_replace(path, target)
    monkeypatch.setattr(Path, "replace", fail_publication)
    with pytest.raises(OSError, match="Injected"):
        plate.export_verified_plate(plate.PlateSpec(), tmp_path / "run")
    assert not (tmp_path / "run").exists(), "Failed publication left partial accepted artifacts"
