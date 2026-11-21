"""Strict input contract and static validity guards for rectangular plates."""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


PLATE_NUMERICAL_V1 = MappingProxyType({
    'version': 'plate_numerical_v1',
    'length_a_mm_gt': 20.0,
    'length_a_mm_le': 3000.0,
    'width_b_mm_gt': 20.0,
    'width_b_mm_le': 3000.0,
    'thickness_mm_gt': 0.1,
    'thickness_mm_le': 150.0,
    'pressure_mpa_gt': 0.0,
    'pressure_mpa_le': 10.0,
    'youngs_modulus_mpa_gt': 0.0,
    'youngs_modulus_mpa_le': 1e7,
    'poissons_ratio_min': 0.1,
    'poissons_ratio_max': 0.45,
    'allowable_stress_mpa_gt': 0.0,
    'allowable_stress_mpa_le': 1e5,
    'max_deflection_mm_gt': 0.0,
    'max_deflection_mm_le': 100.0,
    'max_deflection_width_ratio': 0.02,
    'dimensional_tolerance_mm_min': 1e-5,
    'dimensional_tolerance_mm_max': 0.1,
    'tolerance_thickness_ratio': 0.01,
    'material_reference_max_chars': 240,
    'aspect_ratio_min': 1.0,
    'aspect_ratio_max': 5.0,
    'thin_ratio_min': 20.0,
    'small_deflection_max_ratio': 0.2,
    'max_candidates': 4,
    'mesh_levels_n_b': (4, 8, 16),
    'max_dof': 6000,
    'stress_gauss_points_per_direction': 4,
    'mesh_convergence_deflection': 0.002,
    'mesh_convergence_stress': 0.01,
    'equilibrium': 1e-9,
    'benchmark_deflection': 0.005,
    'benchmark_stress': 0.015,
    'shear_allowance_coefficient': 20.0,
    'geometry_volume_relative': 1e-6,
    'navier_relative_stop': 1e-10,
    'navier_term_cap': 401,
    'fd_grid_multipliers': (1, 2, 4),
    'strip_limit_relative': 0.02,
    'strip_limit_aspect_ratio': 5.0,
    'r3_relative_tolerance': 0.005,
    'r3_nu': 0.3,
})

GUARD_IDS = ('thin_ratio', 'small_deflection', 'aspect_ratio', 'material')
CHECK_IDS = (
    'geometry', 'mesh_convergence_deflection', 'mesh_convergence_stress', 'equilibrium',
    'symmetry', 'benchmark_deflection', 'benchmark_stress', 'deflection_limit', 'stress_limit',
)
EDGE_CONDITIONS = ('simply_supported', 'clamped')
FAMILY = 'rectangular_plate'
FAMILY_VERSION = '1.0.0'

_PLATE_CONFIG = ConfigDict(
    extra='forbid', strict=True, frozen=True, allow_inf_nan=False, revalidate_instances='always',
)
_ThicknessMm = Annotated[
    float,
    Field(gt=PLATE_NUMERICAL_V1['thickness_mm_gt'], le=PLATE_NUMERICAL_V1['thickness_mm_le']),
]


class PlateMaterialReference(BaseModel):
    model_config = _PLATE_CONFIG

    source_kind: Literal['illustrative', 'user_supplied', 'documented']
    reference: Annotated[str, StringConstraints(
        strip_whitespace=True,
        pattern=r'\S',
        max_length=PLATE_NUMERICAL_V1['material_reference_max_chars'],
    )]


class PlateRequirements(BaseModel):
    """Plate inputs shared by every thickness candidate."""

    model_config = _PLATE_CONFIG

    family: Literal['rectangular_plate']
    units: Literal['mm_N_MPa']
    length_a_mm: float = Field(
        gt=PLATE_NUMERICAL_V1['length_a_mm_gt'], le=PLATE_NUMERICAL_V1['length_a_mm_le'],
    )
    width_b_mm: float = Field(
        gt=PLATE_NUMERICAL_V1['width_b_mm_gt'], le=PLATE_NUMERICAL_V1['width_b_mm_le'],
    )
    edge_condition: Literal['simply_supported', 'clamped']
    pressure_mpa: float = Field(
        gt=PLATE_NUMERICAL_V1['pressure_mpa_gt'], le=PLATE_NUMERICAL_V1['pressure_mpa_le'],
    )
    youngs_modulus_mpa: float = Field(
        gt=PLATE_NUMERICAL_V1['youngs_modulus_mpa_gt'], le=PLATE_NUMERICAL_V1['youngs_modulus_mpa_le'],
    )
    poissons_ratio: float = Field(
        ge=PLATE_NUMERICAL_V1['poissons_ratio_min'], le=PLATE_NUMERICAL_V1['poissons_ratio_max'],
    )
    allowable_stress_mpa: float = Field(
        gt=PLATE_NUMERICAL_V1['allowable_stress_mpa_gt'], le=PLATE_NUMERICAL_V1['allowable_stress_mpa_le'],
    )
    max_deflection_mm: float = Field(
        gt=PLATE_NUMERICAL_V1['max_deflection_mm_gt'], le=PLATE_NUMERICAL_V1['max_deflection_mm_le'],
    )
    dimensional_tolerance_mm: float = Field(
        ge=PLATE_NUMERICAL_V1['dimensional_tolerance_mm_min'],
        le=PLATE_NUMERICAL_V1['dimensional_tolerance_mm_max'],
    )
    material_reference: PlateMaterialReference
    assumptions: Literal['kirchhoff_uniform_pressure_uniform_edges_linear_elastic']

    @model_validator(mode='after')
    def validate_shared_limits(self) -> Self:
        if self.length_a_mm < self.width_b_mm:
            raise ValueError('Plate requires length_a_mm >= width_b_mm')
        if self.aspect_ratio > PLATE_NUMERICAL_V1['aspect_ratio_max']:
            raise ValueError('aspect_ratio exceeds the plate aspect ratio limit')
        if self.max_deflection_mm > PLATE_NUMERICAL_V1['max_deflection_width_ratio'] * self.width_b_mm:
            raise ValueError('max_deflection_mm exceeds the limit relative to width_b_mm')
        return self

    @property
    def aspect_ratio(self) -> float:
        return self.length_a_mm / self.width_b_mm


class PlateSpec(PlateRequirements):
    thickness_mm: _ThicknessMm

    @model_validator(mode='after')
    def validate_thickness_limit(self) -> Self:
        if self.dimensional_tolerance_mm > PLATE_NUMERICAL_V1['tolerance_thickness_ratio'] * self.thickness_mm:
            raise ValueError('dimensional_tolerance_mm exceeds the limit relative to thickness_mm')
        return self

    @property
    def flexural_rigidity_n_mm(self) -> float:
        return self.youngs_modulus_mpa * self.thickness_mm**3 / (12 * (1 - self.poissons_ratio**2))


class PlateBrief(BaseModel):
    model_config = _PLATE_CONFIG

    mode: Literal['fixed_design', 'bounded_thickness']
    requirements: PlateRequirements
    candidate_thicknesses_mm: tuple[_ThicknessMm, ...]

    @field_validator('candidate_thicknesses_mm')
    @classmethod
    def normalize_thicknesses(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        candidates = tuple(sorted(set(values)))
        if not candidates:
            raise ValueError('At least one candidate thickness is required')
        maximum = PLATE_NUMERICAL_V1['max_candidates']
        if len(candidates) > maximum:
            raise ValueError(f'At most {maximum} distinct candidate thicknesses are allowed')
        return candidates

    @model_validator(mode='after')
    def validate_candidates(self) -> Self:
        if self.mode == 'fixed_design' and len(self.candidate_thicknesses_mm) != 1:
            raise ValueError('Fixed design requires exactly one candidate thickness')
        self.candidates()
        return self

    def candidates(self) -> tuple[PlateSpec, ...]:
        requirements = self.requirements.model_dump()
        return tuple(
            PlateSpec.model_validate(dict(requirements, thickness_mm=thickness_mm))
            for thickness_mm in self.candidate_thicknesses_mm
        )


def canonical_spec(spec: PlateSpec) -> str:
    return json.dumps(spec.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), allow_nan=False)


def specification_digest(spec: PlateSpec) -> str:
    return hashlib.sha256(canonical_spec(spec).encode()).hexdigest()


def evaluate_static_guards(spec: PlateSpec) -> tuple[dict, ...]:
    thin_ratio = spec.width_b_mm / spec.thickness_mm
    thin_limit = PLATE_NUMERICAL_V1['thin_ratio_min']
    aspect_ratio = spec.aspect_ratio
    aspect_limits = (PLATE_NUMERICAL_V1['aspect_ratio_min'], PLATE_NUMERICAL_V1['aspect_ratio_max'])
    nu = spec.poissons_ratio
    nu_limits = (PLATE_NUMERICAL_V1['poissons_ratio_min'], PLATE_NUMERICAL_V1['poissons_ratio_max'])
    return (
        {'guard': 'thin_ratio', 'quantity': thin_ratio, 'limit': thin_limit, 'passed': thin_ratio >= thin_limit},
        {'guard': 'aspect_ratio', 'quantity': aspect_ratio, 'limit': aspect_limits,
         'passed': aspect_limits[0] <= aspect_ratio <= aspect_limits[1]},
        {'guard': 'material', 'quantity': nu, 'limit': nu_limits,
         'passed': nu_limits[0] <= nu <= nu_limits[1]
         and spec.youngs_modulus_mpa > PLATE_NUMERICAL_V1['youngs_modulus_mpa_gt']},
    )


def first_failed_guard(spec: PlateSpec) -> str | None:
    return next((result['guard'] for result in evaluate_static_guards(spec) if not result['passed']), None)
