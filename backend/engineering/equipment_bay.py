"""Product specification and the pinned equipment-bay catalog boundary."""

from hashlib import sha256
import json
from math import pi
from pathlib import Path
from typing import Literal

from pydantic import Field

from .capabilities import StrictModel

CAPABILITY_ID = 'equipment_bay'
KERNEL_REVISION = '25c232fdfc7114dd371a2c964f417eb98e8d4a71'
CATALOG_SHA256 = 'ae7b45037ac3bcf719e0563fd9fe4fddaec2d95e2cd403df470f9018bee8eea2'
CATALOG = Path(__file__).parent / 'fixtures' / 'equipment_bay'
FIXED_FIELDS = ('length_mm', 'width_mm', 'module_count')
CANDIDATE_FIELDS = ('preferred_angle_rad', 'supplier')
CHECKS = ('bay_envelope', 'mounting_alignment', 'occurrence_binding')


class EquipmentBaySpec(StrictModel):
    length_mm: float = Field(ge=1, le=5000)
    width_mm: float = Field(ge=1, le=5000)
    module_count: Literal[8, 10, 12]
    preferred_angle_rad: float = Field(ge=0, le=pi / 2)
    supplier: Literal['nominal', 'supplier-shifted']

    def model_post_init(self, __context):
        if self.preferred_angle_rad not in (0, pi / 2):
            raise ValueError('The preferred angle must be 0 or 90 degrees')


def catalog_digest() -> str:
    return CATALOG_SHA256


def kernel_inputs(spec: dict) -> tuple[bytes, bytes | None]:
    """Apply reviewed scalar parameters to the pinned catalog while preserving stable IDs."""
    spec = EquipmentBaySpec.model_validate_json(json.dumps(spec))
    design_bytes, supplier_bytes = ((CATALOG / key).read_bytes() for key in ('design.json', 'supplier.json'))
    if sha256(design_bytes + supplier_bytes).hexdigest() != CATALOG_SHA256:
        raise ValueError('Equipment-bay catalog digest does not match its registered version')
    design = json.loads(design_bytes)
    design['bay_size'] = [spec.length_mm, spec.width_mm]
    design['rotation_deg'] = 0 if spec.preferred_angle_rad == 0 else 90
    modules = design['modules'][:spec.module_count]
    for index in range(len(modules), spec.module_count):
        modules.append({'id': f'module-{index + 1:02}', 'slot': list(divmod(index, 2))})
    design['modules'] = modules
    supplier = supplier_bytes if spec.supplier == 'supplier-shifted' else None
    return json.dumps(design, separators=(',', ':'), sort_keys=True).encode(), supplier


def selected_rotation(plan: dict) -> int:
    angle = plan['selected_rotation_deg']
    if angle is not None:
        return angle
    # Build a diagnostic candidate even when search finds no solution.
    candidates = plan['candidates']
    return next((row['rotation_deg'] for row in candidates if row['packaging_fits']),
                candidates[0]['rotation_deg'])
