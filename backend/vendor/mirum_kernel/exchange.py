"""Build analytic parts and exchange a named, single-level STEP assembly with XDE."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
from platform import python_version
from tempfile import TemporaryDirectory

import cadquery as cq
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.Standard import Standard_Failure
from OCP.TCollection import TCollection_ExtendedString
from OCP.TColStd import TColStd_SequenceOfAsciiString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

from .design import DefinitionKey, Design, KernelError

STEP = "assembly.step"
BOM = "bom.json"
MANIFEST = "manifest.json"
READER = "OCCT STEPCAFControl_Reader (XDE/XCAF)"


@dataclass(frozen=True)
class DeliveredPart:
    id: str | None
    definition_name: str | None
    location: TopLoc_Location
    solid: cq.Solid

    @property
    def definition(self) -> DefinitionKey | None:
        identity, separator, revision = (self.definition_name or "").partition("@")
        if separator and identity and revision and "@" not in revision:
            return identity, revision
        return None


def tool_versions() -> dict:
    return {
        "python": python_version(),
        "cadquery": version("cadquery"),
        "cadquery-ocp": version("cadquery-ocp"),
    }


def file_record(data: bytes) -> dict:
    return {"bytes": len(data), "sha256": sha256(data).hexdigest()}


def json_text(record: dict) -> str:
    return json.dumps(record, indent=2, allow_nan=False) + "\n"


def bom_record(design: Design) -> dict:
    groups = defaultdict(list)
    for occurrence in design.occurrences:
        groups[occurrence.definition].append(occurrence.id)
    return {
        "schema_version": 1,
        "assembly_id": design.id,
        "items": [
            {"definition_id": identity, "revision": revision, "quantity": len(occurrences),
             "occurrences": sorted(occurrences)}
            for (identity, revision), occurrences in sorted(groups.items())
        ],
    }


def _xde_document():
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    XCAFDoc_DocumentTool.SetLengthUnit_s(document, 0.001)  # mm in metres
    return document, XCAFDoc_DocumentTool.ShapeTool_s(document.Main())


def _name(label) -> str | None:
    attribute = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return attribute.Get().ToExtString()
    return None


def _set_name(label, name):
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))


def _write_step(design: Design, destination: Path) -> None:
    used = {occurrence.definition for occurrence in design.occurrences}
    definitions = {}
    for part in design.definitions:
        if part.key not in used:
            continue
        solids = (
            cq.Workplane("XY").box(*part.size, centered=(True, True, False))
            .faces(">Z").workplane().pushPoints([point for _, point in part.holes])
            .hole(design.diameter).val().Solids()
        )
        if len(solids) != 1 or not solids[0].isValid():
            raise KernelError(f"could not build a valid solid for {part.id}@{part.revision}")
        definitions["@".join(part.key)] = solids[0]
    components = (
        (occurrence.id, "@".join(occurrence.definition),
         cq.Location(occurrence.origin, (0, 0, 1), occurrence.rotation_deg))
        for occurrence in design.occurrences
    )
    write_named_step(design.id, definitions, components, destination)


def write_named_step(
    assembly_id: str,
    definitions: Mapping[str, cq.Solid],
    components: Iterable[tuple[str, str, cq.Location]],
    destination: Path,
) -> None:
    """Write one assembly root with named, located references to reusable solids."""
    document, tool = _xde_document()
    assembly = tool.NewShape()
    _set_name(assembly, assembly_id)
    labels = {}
    for name, solid in definitions.items():
        label = tool.AddShape(solid.wrapped, False)
        _set_name(label, name)
        labels[name] = label
    for occurrence_id, definition_id, location in components:
        label = tool.AddComponent(assembly, labels[definition_id], location.wrapped)
        _set_name(label, occurrence_id)
    tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    if not writer.Transfer(document) or writer.Write(str(destination)) != IFSelect_RetDone:
        raise KernelError("OCCT could not write hierarchical STEP")


def build_delivery(design: Design, output: Path) -> Path:
    """Publish STEP, BOM and manifest together. Return the manifest path."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise KernelError(f"delivery directory already contains data: {output}")
    with TemporaryDirectory(dir=output.parent) as temporary:
        staged = Path(temporary) / "delivery"
        staged.mkdir()
        try:
            _write_step(design, staged / STEP)
        except (Standard_Failure, RuntimeError, ValueError) as error:
            if isinstance(error, KernelError):
                raise
            raise KernelError(f"CAD build or STEP export failed: {error}") from error
        (staged / BOM).write_text(json_text(bom_record(design)), encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "assembly_id": design.id,
            "units": "mm",
            "inputs": design.inputs,
            "writer": "OCCT STEPCAFControl_Writer (XDE/XCAF)",
            "tool_versions": tool_versions(),
            "artifacts": {name: file_record((staged / name).read_bytes()) for name in (STEP, BOM)},
        }
        (staged / MANIFEST).write_text(json_text(manifest), encoding="utf-8")
        staged.rename(output)
    return output / MANIFEST


def bounds(shape: cq.Shape) -> tuple[float, ...]:
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    return box.Get()


def read_step(data: bytes, assembly_id: str) -> tuple[list[DeliveredPart], list[dict]]:
    """Import the provided bytes and recover each component's label, prototype and pose."""
    try:
        return _read_step(data, assembly_id)
    except (Standard_Failure, RuntimeError) as error:
        raise KernelError(f"OCCT could not read delivered STEP: {error}") from error


def _read_step(data, assembly_id):
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadStream(STEP, BytesIO(data)) != IFSelect_RetDone:
        raise KernelError("OCCT could not parse delivered STEP")
    length, angle, solid_angle = (TColStd_SequenceOfAsciiString() for _ in range(3))
    reader.Reader().FileUnits(length, angle, solid_angle)
    names = {length.Value(i).ToCString() for i in range(1, length.Length() + 1)}
    if names != {"millimetre"}:
        raise KernelError(f"delivered STEP must declare millimetres, found {sorted(names)}")
    reader.ChangeReader().SetSystemLengthUnit(1.0)
    document, tool = _xde_document()
    if not reader.Transfer(document):
        raise KernelError("OCCT could not transfer STEP to an XDE assembly")

    roots = TDF_LabelSequence()
    tool.GetFreeShapes(roots)
    if roots.Length() != 1:
        return [], [{"code": "assembly_root_missing_or_ambiguous", "root_count": roots.Length()}]
    root = roots.Value(1)
    if (_name(root) != assembly_id or not tool.IsAssembly_s(root)
            or not tool.GetLocation_s(root).IsIdentity()):
        return [], [{"code": "assembly_root_unresolved", "delivered_id": _name(root)}]

    components = TDF_LabelSequence()
    tool.GetComponents_s(root, components)
    parts, diagnostics = [], []
    for index in range(1, components.Length() + 1):
        component = components.Value(index)
        occurrence_id = _name(component)
        definition = TDF_Label()
        if not tool.GetReferredShape_s(component, definition) or not tool.IsSimpleShape_s(definition):
            diagnostics.append({"code": "occurrence_definition_unresolved", "occurrence_id": occurrence_id})
            continue
        shape = tool.GetShape_s(definition)
        if shape.IsNull():
            diagnostics.append({"code": "occurrence_solid_unresolved", "occurrence_id": occurrence_id})
            continue
        solid = cq.Shape.cast(shape)
        if not isinstance(solid, cq.Solid) or not solid.isValid():
            diagnostics.append({"code": "occurrence_solid_unresolved", "occurrence_id": occurrence_id})
            continue
        parts.append(DeliveredPart(occurrence_id, _name(definition), tool.GetLocation_s(component), solid))
    return parts, diagnostics
