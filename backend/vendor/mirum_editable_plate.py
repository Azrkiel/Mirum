"""Version 1 of the rectangular four-bore editable plate worker."""

from argparse import ArgumentParser
from collections import Counter
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from io import BytesIO
from itertools import product
import json
from math import dist, isclose, isfinite, pi, tau
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from typing import Literal

PART_ID = "plate"
FRAME_ID = "plate.datum"
LEFT_ID = "plate.side.left"
RIGHT_ID = "plate.side.right"
PATTERN_ID = "plate.bores"
INTENTS = ("edge-driven", "datum-driven")
LINEAR_TOL = 1e-6  # mm
AXIS_TOL = 1e-8  # unit-vector component
VOLUME_TOL = 1e-5  # mm³
DIAMETER = 6.0
Y_OFFSET = 15.0
EDGE_MARGIN = 10.0
DATUM_OFFSET = 30.0
MAX_LENGTH = 5000.0


class PlateError(ValueError):
    def __init__(self, code: str, field: str, message: str):
        super().__init__(message)
        self.diagnostic = {"code": code, "field": field, "message": message}


def _length(value, field):
    if type(value) not in (int, float) or not 0 < value <= MAX_LENGTH or not isfinite(value):
        raise PlateError("invalid_dimension", field, f"{field} must be positive and at most {MAX_LENGTH:g} mm")


@dataclass(frozen=True)
class Plate:
    width_mm: float
    height_mm: float
    thickness_mm: float
    intent: Literal["edge-driven", "datum-driven"]
    datum_ref: str
    units: Literal["mm"]

    def __post_init__(self):
        if self.units != "mm":
            raise PlateError("invalid_units", "units", "the worker accepts canonical millimetres")
        if self.datum_ref != FRAME_ID:
            raise PlateError("dangling_reference", "datum_ref", f"expected authored datum {FRAME_ID}")
        if self.intent not in INTENTS:
            raise PlateError("invalid_intent", "intent", "expected edge-driven or datum-driven hole placement")
        for field in ("width_mm", "height_mm", "thickness_mm"):
            _length(getattr(self, field), field)
        radius = DIAMETER / 2
        x = self.half_pitch
        clearance = 2 * LINEAR_TOL
        if x <= radius + clearance or self.width_mm / 2 - x <= radius + clearance:
            raise PlateError("invalid_dimension", "width_mm", "bores must be separate and inside both side datums")
        if self.height_mm / 2 - Y_OFFSET <= radius + clearance:
            raise PlateError("invalid_dimension", "height_mm", "bores must be inside both ends")
        if self.thickness_mm <= clearance:
            raise PlateError("invalid_dimension", "thickness_mm", "thickness is below measurement resolution")

    @property
    def half_pitch(self):
        return self.width_mm / 2 - EDGE_MARGIN if self.intent == "edge-driven" else DATUM_OFFSET

    @property
    def centers(self):
        return {
            f"{PATTERN_ID}.{column}.{row}": (x * self.half_pitch, y * Y_OFFSET)
            for (column, x), (row, y) in product((("left", -1), ("right", 1)), (("lower", -1), ("upper", 1)))
        }


@dataclass(frozen=True)
class WidthEdit:
    target: str
    width_mm: float
    units: Literal["mm"]

    def __post_init__(self):
        if self.target != PART_ID:
            raise PlateError("dangling_reference", "target", f"expected authored part {PART_ID}")
        if self.units != "mm":
            raise PlateError("invalid_units", "units", "the worker accepts canonical millimetres")
        _length(self.width_mm, "width_mm")


def apply_width_edit(plate: Plate, edit: WidthEdit) -> Plate:
    return replace(plate, width_mm=edit.width_mm)


def build_step(plate: Plate) -> bytes:
    """Build from the authored frame. The cutters require no topological face selection."""
    import cadquery as cq

    body = cq.Workplane("XY").box(plate.width_mm, plate.height_mm, plate.thickness_mm,
                                   centered=(True, True, False))
    cutters = (cq.Workplane("XY", origin=(0, 0, -1)).pushPoints(list(plate.centers.values()))
               .circle(DIAMETER / 2).extrude(plate.thickness_mm + 2))
    solid = body.cut(cutters).val()
    if len(solid.Solids()) != 1 or not solid.isValid():
        raise PlateError("invalid_solid", "step", "the rebuild did not produce one valid solid")
    with TemporaryDirectory() as directory:
        destination = Path(directory) / "plate.step"
        cq.exporters.export(solid, str(destination))
        data = destination.read_bytes()
    # Replace the wall clock and OCCT exchange counter with stable STEP metadata.
    data, count = re.subn(rb"(FILE_NAME\('[^']*',')[^']+(')", rb"\g<1>2000-01-01T00:00:00\2", data, count=1)
    data, labels = re.subn(rb"'Open CASCADE STEP translator [0-9.]+ [0-9]+'", b"'plate'", data)
    if count != 1 or labels != 2:
        raise PlateError("step_export_failed", "step", "STEP metadata could not be normalized")
    return data


def _near(actual, expected, tolerance=LINEAR_TOL):
    return isclose(actual, expected, rel_tol=0, abs_tol=tolerance)


def _read_solid(step):
    import cadquery as cq
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TColStd import TColStd_SequenceOfAsciiString

    reader = STEPControl_Reader()
    if reader.ReadStream("plate.step", BytesIO(step)) != IFSelect_RetDone:
        raise PlateError("step_unreadable", "step", "could not parse the delivered STEP")
    units, angle, solid_angle = (TColStd_SequenceOfAsciiString() for _ in range(3))
    reader.FileUnits(units, angle, solid_angle)
    if {units.Value(i).ToCString() for i in range(1, units.Length() + 1)} != {"millimetre"}:
        raise PlateError("invalid_units", "step", "the delivered STEP must declare millimetres")
    reader.SetSystemLengthUnit(1.0)
    if reader.NbRootsForTransfer() > 1:
        raise PlateError("ambiguous_datum", "step", "multiple STEP roots could supply the plate datum")
    if reader.TransferRoots() != 1 or reader.OneShape().IsNull():
        raise PlateError("step_unreadable", "step", "could not transfer the delivered STEP root")
    shape = cq.Shape.cast(reader.OneShape())
    if len(shape.Solids()) > 1:
        raise PlateError("ambiguous_datum", "step", "multiple solids could supply the plate datum")
    if len(shape.Solids()) != 1 or not shape.isValid():
        raise PlateError("invalid_solid", "step", "the delivered STEP must contain one valid solid")
    solid = shape.Solids()[0]
    if len(shape.Faces()) != len(solid.Faces()):
        raise PlateError("unexpected_geometry", "step", "the delivered root contains faces outside the plate solid")
    return solid


def _bounds(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    return box.Get()


def _measure_bore(face, limits):
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    surface = BRepAdaptor_Surface(face.wrapped)
    cylinder = surface.Cylinder()
    point, axis = cylinder.Location(), cylinder.Axis().Direction()
    direction = (axis.X(), axis.Y(), axis.Z())
    sign = 1 if axis.Z() >= 0 else -1
    unit_axis = [sign * value + 0.0 for value in direction]
    center = (point.X(), point.Y())
    z_min, z_max = _bounds(face)[2::3]
    sample = face.positionAt(0.5, 0.5)
    normal = face.normalAt(sample)
    inward = (sample.x - center[0]) * normal.x + (sample.y - center[1]) * normal.y < 0
    vertical = dist(unit_axis, (0, 0, 1)) <= AXIS_TOL
    complete = _near(surface.LastUParameter() - surface.FirstUParameter(), tau, AXIS_TOL)
    if vertical:
        # Report the axis intersection with the authored XY datum (z=0).
        center = tuple(coordinate - point.Z() * direction[i] / axis.Z()
                       for i, coordinate in enumerate(center))
    return {"center_mm": list(center), "axis": unit_axis, "diameter_mm": 2 * cylinder.Radius(),
            "z_range_mm": [z_min, z_max], "through": inward and vertical and complete
            and _near(z_min, limits[2]) and _near(z_max, limits[5])}


def _measure(plate, solid, report, add):
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    limits = _bounds(solid)
    expected = (-plate.width_mm / 2, -plate.height_mm / 2, 0,
                plate.width_mm / 2, plate.height_mm / 2, plate.thickness_mm)
    report["envelope_mm"] = [upper - lower for lower, upper in zip(limits[:3], limits[3:])]
    report["bounds_mm"] = list(limits)
    if any(not _near(a, b) for a, b in zip(limits, expected)):
        add("envelope_mismatch", FRAME_ID, "delivered bounds differ from the reviewed bounds")

    bores, planes = [], Counter()
    for face in solid.Faces():
        if face.geomType() == "CYLINDER":
            bores.append(_measure_bore(face, limits))
        elif face.geomType() == "PLANE":
            plane = BRepAdaptor_Surface(face.wrapped).Plane()
            normal = plane.Axis().Direction().Coord()
            location = plane.Location().Coord()
            for i, position in enumerate(expected):
                axis = i % 3
                basis = tuple(float(j == axis) for j in range(3))
                if dist(tuple(abs(value) for value in normal), basis) <= AXIS_TOL and _near(location[axis], position):
                    planes[i] += 1
        else:
            add("unexpected_geometry", PART_ID, "the plate has a non-planar, non-cylindrical face")
    plane_count = sum(face.geomType() == "PLANE" for face in solid.Faces())
    if any(count > 1 for count in planes.values()):
        add("ambiguous_datum", FRAME_ID, "multiple planar faces match an authored boundary plane")
    if plane_count != 6 or set(planes) != set(range(6)):
        add("datum_mismatch", FRAME_ID, "expected the six authored boundary planes")

    bores.sort(key=lambda bore: (bore["center_mm"], bore["diameter_mm"], bore["z_range_mm"]))
    report["bores"] = bores
    candidates = {name: [index for index, bore in enumerate(bores) if dist(center, bore["center_mm"]) <= LINEAR_TOL]
                  for name, center in plate.centers.items()}
    claims = Counter(index for indices in candidates.values() for index in indices)
    for name, indices in candidates.items():
        if not indices:
            add("feature_missing", name, "no bore axis intersects the authored center within tolerance")
        elif len(indices) != 1 or claims[indices[0]] != 1:
            add("ambiguous_feature", name, "matching requires one unique bore per authored center")
        else:
            bore = bores[indices[0]]
            bore["id"] = name
            if not bore["through"] or not _near(bore["diameter_mm"], DIAMETER):
                add("bore_mismatch", name, "expected a vertical inward through-bore of reviewed diameter")
    if len(bores) != 4 or len(claims) != len(bores):
        add("unexpected_bores", PATTERN_ID, "expected four uniquely matched cylindrical bores")
    if bores:
        x_values = [bore["center_mm"][0] for bore in bores]
        report["pitch_x_mm"] = max(x_values) - min(x_values)
        report["side_margins_mm"] = [min(x_values) - limits[0], limits[3] - max(x_values)]
    volume = solid.Volume()
    report["volume_mm3"] = volume
    expected_volume = (plate.width_mm * plate.height_mm - 4 * pi * (DIAMETER / 2)**2) * plate.thickness_mm
    if not _near(volume, expected_volume, VOLUME_TOL):
        add("volume_mismatch", PART_ID, "delivered solid volume differs from the reviewed plate")


def inspect_step(plate: Plate, step: bytes) -> dict:
    """Measure delivered bytes in the authored lower-center XY frame using a fresh OCCT reader."""
    from importlib.metadata import version
    from platform import python_version

    from OCP.Standard import Standard_Failure

    report = {
        "schema": "editable_plate_measurement_v1",
        "part_id": PART_ID,
        "datum": {"id": FRAME_ID, "origin_mm": [0, 0, 0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                  "side_ids": [LEFT_ID, RIGHT_ID]},
        "pattern": {"id": PATTERN_ID, "intent": plate.intent, "y_reference": plate.datum_ref,
                    "x_references": [LEFT_ID, RIGHT_ID] if plate.intent == "edge-driven" else [plate.datum_ref]},
        "units": "mm",
        "tolerances": {"length_mm": LINEAR_TOL, "axis_component": AXIS_TOL, "volume_mm3": VOLUME_TOL},
        "reader": "OCCT STEPControl_Reader",
        "versions": {"python": python_version(), "cadquery": version("cadquery"), "cadquery-ocp": version("cadquery-ocp")},
        "step": {"sha256": sha256(step).hexdigest(), "bytes": len(step)},
        "specification": asdict(plate),
        "diagnostics": [],
    }

    def add(code, field, message):
        report["diagnostics"].append({"code": code, "field": field, "message": message})

    try:
        _measure(plate, _read_solid(step), report, add)
    except PlateError as error:
        report["diagnostics"].append(error.diagnostic)
    except (Standard_Failure, RuntimeError, ValueError):
        add("step_unreadable", "step", "OCCT could not measure the delivered STEP")
    report["outcome"] = "fail" if report["diagnostics"] else "pass"
    return report


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("plate", type=Path)
    parser.add_argument("step", type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--edit", type=Path, help="Apply this version 1 width edit before building")
    actions.add_argument("--inspect", action="store_true", help="Measure the supplied STEP without building")
    args = parser.parse_args(argv)
    try:
        plate = Plate(**json.loads(args.plate.read_bytes()))
        if args.edit:
            plate = apply_width_edit(plate, WidthEdit(**json.loads(args.edit.read_bytes())))
        step = args.step.read_bytes() if args.inspect else build_step(plate)
        report = inspect_step(plate, step)
        if not args.inspect and report["outcome"] == "pass":
            args.step.write_bytes(step)
    except PlateError as error:
        report = {"outcome": "fail", "diagnostics": [error.diagnostic]}
    except (TypeError, ValueError, OSError, RuntimeError):
        report = {"outcome": "fail", "diagnostics": [
            {"code": "worker_error", "field": "input", "message": "could not decode input or complete CAD operation"}]}
    json.dump(report, sys.stdout, allow_nan=False, sort_keys=True, indent=2)
    print()
    return int(report["outcome"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
