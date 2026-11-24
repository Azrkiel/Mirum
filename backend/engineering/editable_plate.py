"""Reviewed inputs for the editable plate capability."""

from typing import Literal

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from vendor.mirum_editable_plate import Plate, PlateError

from .capabilities import StrictModel

CAPABILITY_ID = 'editable_plate'
KERNEL_REVISION = '6a68e99c67b99375f2a49a794f5d44464c6307e3'
FIXED_FIELDS = ('height_mm', 'thickness_mm', 'intent', 'datum_ref', 'units')
CHECKS = ('plate_envelope', 'plate_bores', 'plate_binding')

# Each reported diagnostic belongs to one gate. Unrecognized diagnostics fail binding.
ENVELOPE = frozenset(('envelope_mismatch', 'volume_mismatch'))
BORES = frozenset(('feature_missing', 'bore_mismatch', 'unexpected_bores'))
AMBIGUITY = frozenset(('ambiguous_feature', 'ambiguous_datum'))


class EditablePlateSpec(StrictModel):
    width_mm: float
    height_mm: float
    thickness_mm: float
    intent: str
    datum_ref: str
    units: Literal['mm'] = 'mm'

    @model_validator(mode='after')
    def plate_contract(self):
        try:
            Plate(**self.model_dump())
        except PlateError as error:
            diagnostic = error.diagnostic
            raise PydanticCustomError(diagnostic['code'], diagnostic['message'],
                                      {'field': diagnostic['field']}) from None
        return self
