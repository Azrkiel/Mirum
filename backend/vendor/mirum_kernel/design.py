"""Editable equipment-bay design and the study's two-row placement rules."""

from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import combinations, product
import json
from math import dist, isfinite
from pathlib import Path
import re

XY = tuple[float, float]
XYZ = tuple[float, float, float]
Holes = tuple[tuple[str, XY], ...]
DefinitionKey = tuple[str, str]

ORIENTATIONS_DEG = (0, 90)
GEOMETRY_ROUNDOFF_MM = 1e-6
ALIGNMENT_ROUNDOFF_MM = 1e-9


class KernelError(ValueError):
    """Invalid design or delivery."""


@dataclass(frozen=True)
class Definition:
    id: str
    revision: str
    size: XYZ
    holes: Holes

    @property
    def key(self) -> DefinitionKey:
        return self.id, self.revision


@dataclass(frozen=True)
class Occurrence:
    id: str
    definition: DefinitionKey
    origin: XYZ
    rotation_deg: int

    def to_assembly_xy(self, point: XY) -> XY:
        x, y = point
        dx, dy = (-y, x) if self.rotation_deg == 90 else (x, y)
        return self.origin[0] + dx, self.origin[1] + dy


@dataclass(frozen=True)
class Design:
    id: str
    bay_size: XY
    diameter: float
    alignment_tolerance: float
    lookup_radius: float
    tray: Occurrence
    modules: tuple[Occurrence, ...]
    definitions: tuple[Definition, ...]
    module_holes: Holes
    source_sha256: str
    substitution_sha256: str | None = None
    rotation_override_deg: int | None = None

    @property
    def occurrences(self) -> tuple[Occurrence, ...]:
        return (self.tray, *self.modules)

    @property
    def inputs(self) -> dict:
        inputs = {
            "design_sha256": self.source_sha256,
            "substitution_sha256": self.substitution_sha256,
        }
        if self.rotation_override_deg is not None:
            inputs["rotation_override_deg"] = self.rotation_override_deg
        return inputs


def tray_hole_id(occurrence_id: str, hole_id: str) -> str:
    return f"{occurrence_id}.{hole_id}"


def read_json(data: bytes, name: str):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise KernelError(f"duplicate key {key!r}")
            result[key] = value
        return result

    def reject_constant(value):
        raise KernelError(f"invalid JSON number {value}")

    try:
        return json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, RecursionError) as error:
        raise KernelError(f"{name}: {error}") from error


def _fields(record, field, *keys):
    if not isinstance(record, dict) or record.keys() != set(keys):
        raise KernelError(f"{field}: expected exactly {', '.join(keys)}")


def _identifier(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise KernelError(f"{field}: expected an identifier with letters, digits, '_' or '-'")
    return value


def _number(value, field, *, minimum=None):
    try:
        if type(value) in (int, float) and isfinite(value):
            if minimum is None or value >= minimum:
                return float(value)
    except OverflowError:
        pass
    bound = "" if minimum is None else f" of at least {minimum}"
    raise KernelError(f"{field}: expected a finite number{bound}")


def _vector(value, length, field, *, positive=False):
    if not isinstance(value, list) or len(value) != length:
        raise KernelError(f"{field}: expected {length} coordinates")
    result = tuple(_number(item, f"{field}[{i}]") for i, item in enumerate(value))
    if positive and min(result) <= 0:
        raise KernelError(f"{field}: dimensions must be positive")
    return result


def _validate_bores(part, diameter):
    radius = diameter / 2
    width, depth, _ = part.size
    if not all(isfinite(size) and size > 0 for size in part.size):
        raise KernelError(f"{part.key}: invalid part dimensions")
    for hole_id, (x, y) in part.holes:
        if abs(x) + radius >= width / 2 or abs(y) + radius >= depth / 2:
            raise KernelError(f"{part.key}: bore {hole_id} crosses or touches the part edge")
    for (left, a), (right, b) in combinations(part.holes, 2):
        if dist(a, b) <= diameter:
            raise KernelError(f"{part.key}: bores {left} and {right} overlap or touch")


def load_design(path: Path, *, substitution: Path | None = None, rotation_deg: int | None = None) -> Design:
    """Read and validate the authored files and optional supplier substitution."""
    return parse_design(
        Path(path).read_bytes(),
        substitution=Path(substitution).read_bytes() if substitution is not None else None,
        rotation_deg=rotation_deg,
    )


def parse_design(data: bytes, *, substitution: bytes | None = None, rotation_deg: int | None = None) -> Design:
    """Validate the input bytes and apply an optional supplier and orientation choice."""
    raw = read_json(data, "design")
    _fields(raw, "design", "schema_version", "id", "units", "bay_size", "gap", "margin",
            "rotation_deg", "mounting", "tray", "module", "modules")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise KernelError("design.schema_version: expected 1")
    if raw["units"] != "mm":
        raise KernelError("design.units: expected canonical millimetres ('mm')")
    design_id = _identifier(raw["id"], "design.id")
    bay_size = _vector(raw["bay_size"], 2, "bay_size", positive=True)
    gap = _number(raw["gap"], "gap", minimum=0)
    margin = _number(raw["margin"], "margin", minimum=0)
    authored_rotation = raw["rotation_deg"]
    if type(authored_rotation) is not int or authored_rotation not in ORIENTATIONS_DEG:
        raise KernelError("rotation_deg: expected 0 or 90")
    if rotation_deg is not None and (type(rotation_deg) is not int or rotation_deg not in ORIENTATIONS_DEG):
        raise KernelError("orientation override: expected 0 or 90")
    rotation = authored_rotation if rotation_deg is None else rotation_deg

    mount = raw["mounting"]
    _fields(mount, "mounting", "diameter", "alignment_tolerance", "lookup_radius", "module_holes")
    diameter = _number(mount["diameter"], "mounting.diameter")
    tolerance = _number(mount["alignment_tolerance"], "mounting.alignment_tolerance")
    lookup = _number(mount["lookup_radius"], "mounting.lookup_radius")
    if not (diameter > 0 and 0 < tolerance < lookup):
        raise KernelError("mounting: require positive diameter and 0 < alignment_tolerance < lookup_radius")
    points = mount["module_holes"]
    if not isinstance(points, dict) or len(points) != 4:
        raise KernelError("mounting.module_holes: expected four named holes")
    module_holes = tuple(
        (_identifier(name, "module hole ID"), _vector(point, 2, f"module hole {name}"))
        for name, point in sorted(points.items())
    )

    module_data = raw["module"]
    _fields(module_data, "module", "definition_id", "revision", "size")
    module = Definition(
        _identifier(module_data["definition_id"], "module.definition_id"),
        _identifier(module_data["revision"], "module.revision"),
        _vector(module_data["size"], 3, "module.size", positive=True),
        module_holes,
    )
    tray_data = raw["tray"]
    _fields(tray_data, "tray", "occurrence_id", "definition_id", "revision", "thickness")
    tray_id = _identifier(tray_data["occurrence_id"], "tray.occurrence_id")
    tray_key = (
        _identifier(tray_data["definition_id"], "tray.definition_id"),
        _identifier(tray_data["revision"], "tray.revision"),
    )
    if tray_key[0] == module.id:
        raise KernelError("tray and module definition IDs must be distinct")
    thickness = _number(tray_data["thickness"], "tray.thickness")

    entries = raw["modules"]
    if not isinstance(entries, list) or len(entries) < 2 or len(entries) % 2:
        raise KernelError("modules: expected a positive, even number of occurrences")
    columns = len(entries) // 2
    allowed_slots = set(product(range(columns), range(2)))
    slots = set()
    ids = {tray_id}
    modules = []
    length, width, _ = module.size
    if rotation == 90:
        length, width = width, length
    for entry in entries:
        _fields(entry, "module occurrence", "id", "slot")
        occurrence_id = _identifier(entry["id"], "modules.id")
        if occurrence_id in ids:
            raise KernelError(f"duplicate occurrence ID: {occurrence_id}")
        slot = entry["slot"]
        if (not isinstance(slot, list) or len(slot) != 2
                or any(type(value) is not int for value in slot)
                or tuple(slot) not in allowed_slots or tuple(slot) in slots):
            raise KernelError(f"{occurrence_id}.slot: expected a unique [column, row] in the layout")
        ids.add(occurrence_id)
        slots.add(tuple(slot))
        column, row = slot
        origin = ((column - (columns - 1) / 2) * (length + gap),
                  (row - 0.5) * (width + gap), thickness)
        modules.append(Occurrence(occurrence_id, module.key, origin, rotation))

    tray = Definition(
        *tray_key,
        (columns * length + (columns - 1) * gap + 2 * margin, 2 * width + gap + 2 * margin, thickness),
        tuple(
            (tray_hole_id(occurrence.id, name), occurrence.to_assembly_xy(point))
            for occurrence in modules for name, point in module_holes
        ),
    )
    design = Design(
        design_id, bay_size, diameter, tolerance, lookup,
        Occurrence(tray_id, tray.key, (0, 0, 0), 0), tuple(modules), (tray, module),
        module_holes, sha256(data).hexdigest(),
        rotation_override_deg=rotation if rotation != authored_rotation else None,
    )
    for definition in design.definitions:
        _validate_bores(definition, diameter)
    if substitution is not None:
        design = _substitute(design, module, substitution)
    return design


def _substitute(design, module, data):
    patch = read_json(data, "substitution")
    _fields(patch, "substitution", "schema_version", "occurrence_id", "definition_id",
            "from_revision", "to_revision", "hole_shift_mm")
    if type(patch["schema_version"]) is not int or patch["schema_version"] != 1:
        raise KernelError("substitution.schema_version: expected 1")
    target = _identifier(patch["occurrence_id"], "substitution.occurrence_id")
    revision = _identifier(patch["to_revision"], "substitution.to_revision")
    if target not in {occurrence.id for occurrence in design.modules}:
        raise KernelError(f"substitution: unknown module occurrence {target!r}")
    if (patch["definition_id"], patch["from_revision"]) != module.key or revision == module.revision:
        raise KernelError("substitution: expected the nominal definition and a distinct replacement revision")
    dx, dy = _vector(patch["hole_shift_mm"], 2, "substitution.hole_shift_mm")
    supplier = replace(module, revision=revision,
                       holes=tuple((name, (x + dx, y + dy)) for name, (x, y) in module.holes))
    _validate_bores(supplier, design.diameter)
    return replace(
        design,
        modules=tuple(
            replace(occurrence, definition=supplier.key) if occurrence.id == target else occurrence
            for occurrence in design.modules
        ),
        definitions=(*design.definitions, supplier),
        substitution_sha256=sha256(data).hexdigest(),
    )
