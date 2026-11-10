"""Spec v2: one strict, versioned model per body style plus shared wheel/stock/material blocks.

Importable with stdlib + pydantic only (frontend image has no CadQuery). The lofted-style
containment rules use ``profiles`` (the mathematical definition of each body), never a builder.
v1 (``spec.DragsterSpec``) stays frozen; ``parse_any`` dispatches on ``schema_version`` and
``upgrade_v1`` converts a v1 wedge into a v2 wedge with field provenance.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from . import profiles
from .spec import DragsterSpec

POCKET_STEP_MM = 0.5     # lofted pocket containment sampling step (<= 0.5 mm, ends included)
BORE_STEP_MM = 0.25      # lofted bore side-height sampling step (<= 0.25 mm, ends included)
UPGRADE_WHEEL_WIDTH_MM = 6.0
UPGRADE_SIDE_CLEARANCE_MM = 1.5


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


class Wheels(_Strict):
    front_diameter_mm: float = Field(ge=16, le=45)
    rear_diameter_mm: float = Field(ge=16, le=45)
    width_mm: float = Field(ge=2, le=12)
    side_clearance_mm: float = Field(ge=0.5, le=5)


class Stock(_Strict):
    length_mm: float = Field(ge=160, le=320)
    width_mm: float = Field(ge=30, le=80)
    height_mm: float = Field(ge=25, le=80)


class Material(_Strict):
    name: str = Field(pattern=r'^[A-Za-z0-9 _-]{1,40}$')
    density_g_cm3: float = Field(ge=0.05, le=2.0)


class _CommonV2(_Strict):
    schema_version: Literal[2]
    units: Literal['mm']
    length_mm: float = Field(ge=160, le=300)
    ground_clearance_mm: float = Field(ge=1, le=10)
    front_axle_x_mm: float = Field(ge=10, le=290)
    rear_axle_x_mm: float = Field(ge=10, le=290)
    axle_bore_diameter_mm: float = Field(ge=2, le=5)
    pocket_diameter_mm: float = Field(ge=12, le=24)
    pocket_depth_mm: float = Field(ge=30, le=80)
    pocket_center_z_mm: float = Field(ge=10, le=60)
    min_wall_mm: float = Field(ge=2, le=5)
    wheels: Wheels
    stock: Stock | None          # key required, value may be null
    material: Material | None    # key required, value may be null

    @field_validator('schema_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('schema_version must be the integer 2')
        return value

    # Derived (not fields): wheel contact is ground Z=0, so the axle sits at the wheel radius.
    @property
    def front_axle_z_mm(self) -> float:
        return self.wheels.front_diameter_mm / 2

    @property
    def rear_axle_z_mm(self) -> float:
        return self.wheels.rear_diameter_mm / 2

    def _axles(self):
        return [('front_axle_x_mm', 'wheels.front_diameter_mm', self.front_axle_x_mm, self.front_axle_z_mm),
                ('rear_axle_x_mm', 'wheels.rear_diameter_mm', self.rear_axle_x_mm, self.rear_axle_z_mm)]

    def _common_constraints(self):
        L, g, t = self.length_mm, self.ground_clearance_mm, self.min_wall_mm
        ra, rp = self.axle_bore_diameter_mm / 2, self.pocket_diameter_mm / 2
        D, zp = self.pocket_depth_mm, self.pocket_center_z_mm
        xf, xr = self.front_axle_x_mm, self.rear_axle_x_mm
        fd, rd = self.wheels.front_diameter_mm, self.wheels.rear_diameter_mm
        yield xf < xr, 'front_axle_x_mm must precede rear_axle_x_mm'
        yield (xr - xf >= (fd + rd) / 2 + 2,
               'front_axle_x_mm/rear_axle_x_mm spacing must clear wheels.front_diameter_mm/wheels.rear_diameter_mm '
               '(half-sum + 2 mm)')
        for name, wheel, x, za in self._axles():
            yield x - ra >= t, f'{name}/axle_bore_diameter_mm must clear nose by min_wall_mm'
            yield x + ra <= L - t, f'{name}/axle_bore_diameter_mm must clear length_mm by min_wall_mm'
            yield (za - ra >= g + t,
                   f'{name}/{wheel}/axle_bore_diameter_mm must clear ground_clearance_mm + min_wall_mm')
        yield zp - rp >= g + t, 'pocket_center_z_mm/pocket_diameter_mm must clear ground_clearance_mm + min_wall_mm'
        for name, wheel, x, za in self._axles():
            yield (x + ra + t <= L - D or za + ra + t <= zp - rp,
                   f'{name}/{wheel}/axle_bore_diameter_mm must clear the pocket (pocket_depth_mm, '
                   'pocket_center_z_mm, pocket_diameter_mm) by min_wall_mm axially or vertically')

    def _style_constraints(self):  # pragma: no cover - every concrete style overrides
        raise NotImplementedError

    @model_validator(mode='after')
    def supported_envelope(self):
        # Ordered and lazy: later checks (profile sampling) may assume earlier ones held.
        for constraints in (self._common_constraints(), self._style_constraints()):
            for passed, reason in constraints:
                if not passed:
                    raise ValueError(reason)
        return self


class WedgeV2(_CommonV2):
    style: Literal['wedge']
    width_mm: float = Field(ge=32, le=60)
    height_mm: float = Field(ge=28, le=45)
    nose_height_mm: float = Field(ge=8, le=18)

    def _style_constraints(self):
        L, W, H, N = self.length_mm, self.width_mm, self.height_mm, self.nose_height_mm
        g, t, D, zp = self.ground_clearance_mm, self.min_wall_mm, self.pocket_depth_mm, self.pocket_center_z_mm
        ra, rp = self.axle_bore_diameter_mm / 2, self.pocket_diameter_mm / 2
        yield N < H, 'nose_height_mm must be below height_mm'
        yield (L - D >= L / 2 + t,
               'pocket_depth_mm must remain in the flat rear housing of length_mm with min_wall_mm')
        yield W / 2 >= rp + t, 'width_mm must contain pocket_diameter_mm and min_wall_mm'
        yield (zp + rp <= g + H - t,
               'pocket_center_z_mm/pocket_diameter_mm exceeds height_mm above ground_clearance_mm minus min_wall_mm')
        for name, wheel, x, za in self._axles():
            yield (za + ra + t <= g + N + (H - N) * min(2 * (x - ra) / L, 1),
                   f'{name}/{wheel}/axle_bore_diameter_mm must clear wedge top (ground_clearance_mm, '
                   'nose_height_mm, height_mm, length_mm, min_wall_mm)')


class _LoftedV2(_CommonV2):
    side_height_frac: float = Field(ge=0.3, le=0.8)
    crown_exponent: float = Field(ge=2.0, le=4.0)

    def _shape_constraints(self):  # pragma: no cover - every lofted style overrides
        raise NotImplementedError

    def _style_constraints(self):
        yield from self._shape_constraints()
        yield from self._containment_constraints()

    def _containment_constraints(self):
        L, g, t, n = self.length_mm, self.ground_clearance_mm, self.min_wall_mm, self.crown_exponent
        ra, rp = self.axle_bore_diameter_mm / 2, self.pocket_diameter_mm / 2
        stations = profiles.stations_for(self)
        pocket_ok = all(
            profiles.disk_margin(*profiles.interpolate(stations, x), n, 0.0, self.pocket_center_z_mm - g, rp + t) >= 0
            for x in profiles.sample_xs(L - self.pocket_depth_mm, L, POCKET_STEP_MM))
        yield (pocket_ok,
               f'pocket_center_z_mm/pocket_diameter_mm/pocket_depth_mm with min_wall_mm must lie inside the '
               f'{self.style} section (side_height_frac, crown_exponent and the style widths/heights)')
        for name, wheel, x, za in self._axles():
            lowest_side = min(profiles.interpolate(stations, xs)[1]
                              for xs in profiles.sample_xs(x - ra, x + ra, BORE_STEP_MM))
            yield (za - g + ra + t <= lowest_side,
                   f'{name}/{wheel}/axle_bore_diameter_mm with min_wall_mm must stay below the side height '
                   f'(side_height_frac, ground_clearance_mm and the {self.style} heights)')


class TeardropV2(_LoftedV2):
    style: Literal['teardrop']
    nose_width_mm: float = Field(ge=8, le=30)
    nose_height_mm: float = Field(ge=6, le=25)
    max_width_mm: float = Field(ge=32, le=64)
    max_height_mm: float = Field(ge=26, le=55)
    max_section_x_frac: float = Field(ge=0.25, le=0.6)
    tail_width_mm: float = Field(ge=26, le=64)
    tail_height_mm: float = Field(ge=24, le=55)

    def _shape_constraints(self):
        yield self.nose_width_mm < self.max_width_mm, 'nose_width_mm must be below max_width_mm'
        yield self.nose_height_mm < self.max_height_mm, 'nose_height_mm must be below max_height_mm'
        yield self.tail_width_mm <= self.max_width_mm, 'tail_width_mm must not exceed max_width_mm'
        yield self.tail_height_mm <= self.max_height_mm, 'tail_height_mm must not exceed max_height_mm'


class NarrowWaistV2(_LoftedV2):
    style: Literal['narrow_waist']
    nose_width_mm: float = Field(ge=8, le=30)
    nose_height_mm: float = Field(ge=6, le=25)
    front_section_x_frac: float = Field(ge=0.10, le=0.30)
    front_width_mm: float = Field(ge=24, le=56)
    waist_x_frac: float = Field(ge=0.35, le=0.60)
    waist_width_mm: float = Field(ge=14, le=40)
    body_height_mm: float = Field(ge=18, le=40)
    housing_width_mm: float = Field(ge=30, le=64)
    housing_height_mm: float = Field(ge=28, le=60)
    housing_length_mm: float = Field(ge=40, le=110)

    def _shape_constraints(self):
        L, hl = self.length_mm, self.housing_length_mm
        yield (self.front_section_x_frac * L < self.waist_x_frac * L,
               'front_section_x_frac must be ahead of waist_x_frac')
        yield (self.waist_x_frac * L <= L - hl - 10,
               'waist_x_frac*length_mm must be at least 10 mm ahead of the housing (length_mm - housing_length_mm)')
        yield self.waist_width_mm < self.front_width_mm, 'waist_width_mm must be narrower than front_width_mm'
        yield self.waist_width_mm < self.housing_width_mm, 'waist_width_mm must be narrower than housing_width_mm'
        yield self.nose_width_mm <= self.front_width_mm, 'nose_width_mm must not exceed front_width_mm'
        yield self.nose_height_mm <= self.body_height_mm, 'nose_height_mm must not exceed body_height_mm'
        yield (self.housing_height_mm >= self.body_height_mm + 4,
               'housing_height_mm must be at least body_height_mm + 4 (raised rear housing)')
        yield (self.pocket_depth_mm <= hl - self.min_wall_mm,
               'pocket_depth_mm must fit inside housing_length_mm minus min_wall_mm')


DragsterSpecV2 = Annotated[Union[WedgeV2, TeardropV2, NarrowWaistV2], Field(discriminator='style')]
SPEC_V2_ADAPTER: TypeAdapter = TypeAdapter(DragsterSpecV2)
V2_MODELS = (WedgeV2, TeardropV2, NarrowWaistV2)


def parse_v2(text: str):
    """Validate strict v2 JSON text into the matching style model."""
    return SPEC_V2_ADAPTER.validate_json(text)


def _reject_constant(token: str):
    raise ValueError(f'non-standard JSON constant {token} is not allowed')


def parse_any(text: str):
    """Parse strict spec JSON as v1 (``DragsterSpec``) or v2, dispatching on ``schema_version``."""
    data = json.loads(text, parse_constant=_reject_constant)
    if not isinstance(data, dict):
        raise ValueError('a dragster spec must be a JSON object')
    version = data.get('schema_version')
    if type(version) is not int or version not in (1, 2):
        raise ValueError('schema_version must be the integer 1 or 2')
    if version == 1:
        return DragsterSpec.model_validate_json(text)
    return parse_v2(text)


_V1_SHARED = ('length_mm', 'width_mm', 'height_mm', 'nose_height_mm', 'ground_clearance_mm', 'front_axle_x_mm',
              'rear_axle_x_mm', 'axle_bore_diameter_mm', 'pocket_diameter_mm', 'pocket_depth_mm',
              'pocket_center_z_mm', 'min_wall_mm')


def upgrade_v1(v1: DragsterSpec) -> tuple[WedgeV2, dict[str, str]]:
    """Convert a v1 wedge into a v2 wedge plus per-field provenance.

    Raises ``ValueError`` when the v1 values fall outside v2 ranges or rules; such a file still
    generates unchanged through the v1 path.
    """
    if not isinstance(v1, DragsterSpec):
        raise TypeError('upgrade_v1 expects a v1 DragsterSpec')
    diameter = 2 * v1.axle_z_mm
    data = {
        'schema_version': 2, 'style': 'wedge', 'units': 'mm',
        **{field: getattr(v1, field) for field in _V1_SHARED},
        'wheels': {'front_diameter_mm': diameter, 'rear_diameter_mm': diameter,
                   'width_mm': UPGRADE_WHEEL_WIDTH_MM, 'side_clearance_mm': UPGRADE_SIDE_CLEARANCE_MM},
        'stock': None, 'material': None,
    }
    try:
        spec = WedgeV2.model_validate_json(json.dumps(data, allow_nan=False))
    except ValidationError as exc:
        reasons = '; '.join(f"{'.'.join(str(p) for p in err['loc']) or 'spec'}: {err['msg']}" for err in exc.errors())
        raise ValueError(f'v1 spec cannot be upgraded to v2 ({reasons}); the v1 file still generates as v1') from None
    provenance = {field: 'migrated_from_v1' for field in _V1_SHARED}
    provenance |= {'wheels.front_diameter_mm': 'derived_from_v1_axle_z',
                   'wheels.rear_diameter_mm': 'derived_from_v1_axle_z',
                   'wheels.width_mm': 'preset:wedge', 'wheels.side_clearance_mm': 'preset:wedge',
                   'stock': 'preset:wedge', 'material': 'preset:wedge'}
    return spec, provenance


def canonical_spec_v2(spec) -> str:
    return json.dumps(spec.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), allow_nan=False)


def specification_digest_v2(spec) -> str:
    return hashlib.sha256(canonical_spec_v2(spec).encode('utf-8')).hexdigest()
