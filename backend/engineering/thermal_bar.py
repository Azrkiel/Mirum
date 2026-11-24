"""Typed product inputs and SI conversion for the versioned thermal bar."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import StringConstraints, model_validator

from cem.core import StrictModel
from vendor.mirum_kernel import thermal


CAPABILITY_ID = 'thermal_bar'
KERNEL_REVISION = '35b158ff7a6d97231f87d1a25f404f19d31bd9bb'
TRANSFER_MODEL = 'common_x_linear_trapezoid_v1'
GEOMETRY_LIMIT_MM = 1e-5
VOLUME_RELATIVE_LIMIT = 1e-8
CHECKS = ('bar_geometry', 'conduction', 'expansion', 'clearance')

# MEL name -> (reviewed specification key, physical dimension if numeric).
FIELDS = {
    'length': ('length_mm', 'length'),
    'width': ('width_mm', 'length'),
    'depth': ('depth_mm', 'length'),
    'conductivity': ('conductivity_w_m_k', 'conductivity'),
    'heat_input': ('power_w', 'power'),
    'sink_temperature': ('sink_k', 'temperature'),
    'sink_region': ('sink_region', None),
    'heat_region': ('heat_region', None),
    'insulation_region': ('insulation_region', None),
    'material_reference': ('material_reference', None),
    'expansion_coefficient': ('alpha_per_k', 'thermal_expansion'),
    'stress_free_temperature': ('reference_k', 'temperature'),
    'restraint': ('restraint', None),
    'uniform_temperature_rise': ('uniform_delta_k', 'temperature_difference'),
    'field_reference': ('field_reference', None),
    'transfer_model': ('transfer_model', None),
    'cold_gap': ('cold_gap_mm', 'length'),
    'clearance_allowance': ('clearance_allowance_mm', 'length'),
}
LENGTH_FIELDS = tuple(key for key, dimension in FIELDS.values() if dimension == 'length')


def validate_references(specification):
    for name in thermal.BOUNDARIES:
        thermal.validate_boundary(name, specification.get(name))
    thermal.validate_field_reference(specification.get('field_reference'))
    if specification.get('transfer_model') != TRANSFER_MODEL:
        raise thermal.Diagnostic('field_incompatible', 'requires the reviewed common +X transfer model', 'transfer_model')
    if specification.get('restraint') != thermal.FREE:
        raise thermal.Diagnostic('unsupported_restraint', 'requires the cold end fixed and the hot end axially free',
                                 'restraint')


def si_case(specification):
    data = {key: specification[key] for key in thermal.Case.__dataclass_fields__ if key in specification}
    for key in LENGTH_FIELDS:
        data[key.removesuffix('_mm') + '_m'] = float(Decimal(str(specification[key])) / 1000)
    return thermal.Case(**data)


class ThermalBarSpec(StrictModel):
    length_mm: float
    width_mm: float
    depth_mm: float
    conductivity_w_m_k: float
    power_w: float
    sink_k: float
    sink_region: str
    heat_region: str
    insulation_region: str
    material_reference: Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]
    alpha_per_k: float
    reference_k: float
    restraint: str
    uniform_delta_k: Literal[1.0]
    field_reference: str
    transfer_model: str
    cold_gap_mm: float
    clearance_allowance_mm: float

    @model_validator(mode='after')
    def _model_domain(self):
        specification = self.model_dump()
        validate_references(specification)
        si_case(specification)
        return self
