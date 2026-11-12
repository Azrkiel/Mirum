"""Versioned, provider-free metadata for the bounded digital beam capability.

This registry does not probe native availability or execute geometry. B04 owns
the adapter; B09/B10 own enforcement of the declared operational budgets.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import ValidationError

from cem.core import StrictModel
from .spec import CantileverSpec

if TYPE_CHECKING:
    from .contracts import CandidateSpec, EngineeringBrief


REQUIRED_CHECKS = (
    'native_geometry', 'dimensional_evidence_budget', 'independent_step_read',
    'independent_step_transfer', 'single_rectangular_solid', 'planar_faces',
    'finite_measurements', 'size_requirements', 'datum_frame', 'axis_aligned_faces',
    'rectangular_face_pairs', 'complete_prism_metrics', 'measured_root_section',
    'mesh_convergence', 'analytic_benchmark', 'equilibrium', 'small_deflection',
    'small_strain', 'stress_allowable', 'displacement_requirement',
)

UNSUPPORTED_FEATURES = (
    'holes', 'fillets', 'extra_bodies', 'varying_cross_section', 'other_supports',
    'other_loads', 'shear_deformation', 'fatigue', 'buckling', 'contact', 'thermal',
    'general_3d_stress', 'physical_material_qualification', 'manufacturing_variation',
    'standards_certification',
)


class NumericalPolicy(StrictModel):
    version: Literal['beam_numerical_v1'] = 'beam_numerical_v1'
    min_slenderness: float = 10.0
    max_displacement_span_ratio: float = 0.02
    max_strain: float = 0.005
    max_topology_tolerance_mm: float = 1e-6
    topology_to_dimensional_tolerance_ratio: float = 0.1
    dimension_guard_topology_multiplier: float = 2.0
    datum_budget_mm: float = 1e-6
    face_normal_error: float = 1e-9
    metric_absolute_error: float = 1e-6
    metric_relative_error: float = 1e-8
    convergence_relative_error: float = 1e-7
    analytic_relative_error: float = 1e-7
    equilibrium_relative_error: float = 1e-7
    mesh_elements: tuple[int, ...] = (4, 8, 16)
    min_dimensional_tolerance_mm: float = 1e-5
    max_dimensional_tolerance_mm: float = 0.1
    max_section_tolerance_ratio: float = 0.01
    max_length_mm: float = 10000.0
    max_section_mm: float = 1000.0
    max_modulus_mpa: float = 10000000.0
    max_allowable_mpa: float = 100000.0
    max_force_n: float = 100000000.0
    max_displacement_mm: float = 100.0


class ResourcePolicy(StrictModel):
    version: Literal['engineering_resources_v1'] = 'engineering_resources_v1'
    max_candidates: int = 4
    active_jobs: int = 1
    queue_size: int = 0
    native_stage_seconds: int = 30
    native_memory_mib: int = 1536
    native_output_bytes: int = 1048576
    overall_seconds: int = 420
    ai_calls: int = 1
    ai_repairs: int = 1
    ai_call_seconds: int = 45
    prompt_characters: int = 8000
    structured_response_bytes: int = 16 * 1024
    run_bytes: int = 32 * 1024**2
    retained_artifact_runs: int = 50
    storage_bytes: int = 512 * 1024**2
    bundle_retention_seconds: int = 6 * 3600
    state_retention_seconds: int = 7 * 86400
    api_processes: int = 1
    replicas: int = 1


class CapabilityDescriptor(StrictModel):
    id: Literal['rectangular_cantilever'] = 'rectangular_cantilever'
    version: Literal['1.0.0'] = '1.0.0'
    units: Literal['mm_N_MPa'] = 'mm_N_MPa'
    geometry_scope: str = 'single_solid_axis_aligned_rectangular_prism_positive_xyz'
    support: str = 'ideal_clamp_x0'
    load: str = 'static_transverse_positive_z_tip_force'
    physics_method: str = 'euler_bernoulli_beam_fem_v1'
    engineering_certified: Literal[False] = False
    standards: Literal['not_evaluated'] = 'not_evaluated'
    modes: tuple[str, ...] = ('fixed_design', 'bounded_thickness')
    selection_rule: str = 'smallest passing thickness in the reviewed set'
    required_inputs: tuple[str, ...] = (
        'fixed_length_width', 'digital_dimensional_tolerance', 'thickness_mode_and_choices',
        'tip_force', 'displacement_limit', 'elastic_modulus', 'allowable_stress',
        'material_and_allowable_reference', 'acknowledged_support_load_and_material_assumptions',
    )
    required_checks: tuple[str, ...] = REQUIRED_CHECKS
    exports: tuple[str, ...] = (
        'step', 'glb', 'source', 'brief', 'specification', 'attempts', 'report_json',
        'report_markdown', 'manifest', 'zip',
    )
    unsupported_features: tuple[str, ...] = UNSUPPORTED_FEATURES
    runtime_requirements: tuple[str, ...] = (
        'Linux bounded subprocesses', 'CadQuery 2.6.1', 'OCP 7.8.1.1.post1',
        'NumPy 2.2.6', 'mandatory native and beam evidence; probe before execution',
    )
    limitations: tuple[str, ...] = (
        'Homogeneous linear elasticity; ideal clamp; static transverse +Z end force at X=L',
        'Digital dimensional evidence; no physical testing, manufacturing release or standards conformity',
        'User-approved illustrative or sourced material values; no certified stock-property claim',
        'Required physics evidence cannot be replaced by geometry validity',
        'B01 reference search: 27.47 s and small artifacts; worst-case budgets remain unqualified',
        'V1 exports and operational limits are contracts for later consumers, not current API availability',
    )
    numerical: NumericalPolicy = NumericalPolicy()
    resources: ResourcePolicy = ResourcePolicy()


CAPABILITY = CapabilityDescriptor()


class UnsupportedCapability(ValueError):
    def as_dict(self) -> dict:
        return {'state': 'unsupported', 'reason': str(self)}


def list_capabilities() -> tuple[CapabilityDescriptor, ...]:
    return (CAPABILITY,)


def get_capability(capability_id: str, version: str, *,
                   numerical_policy_version: str = 'beam_numerical_v1',
                   resource_policy_version: str = 'engineering_resources_v1') -> CapabilityDescriptor:
    if (capability_id, version, numerical_policy_version, resource_policy_version) != (
            CAPABILITY.id, CAPABILITY.version, CAPABILITY.numerical.version, CAPABILITY.resources.version):
        raise UnsupportedCapability('Capability/policy version unavailable; prepare and review a supported brief')
    return CAPABILITY


def validate_beam_request(specification: dict, *, features: tuple[str, ...] = (),
                          support: str = 'ideal_clamp_x0',
                          load: str = 'static_transverse_positive_z_tip_force',
                          mode: str = 'fixed_design',
                          candidate_heights_mm: list[float] | None = None) -> tuple[CantileverSpec, ...]:
    """Preparation only: validate every option, then expose the normalized set.

    Execution must consume an unchanged finalized brief (B03/B07). The legacy
    approved_candidates/generate_with_feedback functions deliberately keep order.
    """
    if features:
        raise UnsupportedCapability(f'Unsupported features: {", ".join(features)}')
    if support != CAPABILITY.support or load != CAPABILITY.load:
        raise UnsupportedCapability('Unsupported support or load; only ideal clamp and +Z tip force are supported')
    if mode not in CAPABILITY.modes:
        raise UnsupportedCapability('Unsupported design mode')
    if candidate_heights_mm is not None and type(candidate_heights_mm) is not list:
        raise UnsupportedCapability('Candidate choices must be a reviewed list')
    heights = candidate_heights_mm if candidate_heights_mm is not None else []
    if ((mode == 'fixed_design' and heights) or
            (mode == 'bounded_thickness' and not 1 <= len(heights) <= CAPABILITY.resources.max_candidates)):
        raise UnsupportedCapability('Fixed mode uses its specification thickness; sizing needs one to four choices')
    try:
        spec = CantileverSpec.model_validate(specification)
        candidates = [CantileverSpec.model_validate(dict(spec.model_dump(), height_mm=h))
                      for h in heights] if heights else [spec]
    except ValidationError as error:
        raise UnsupportedCapability(f'Unsupported beam requirements: {error}') from None
    by_height = {candidate.height_mm: candidate for candidate in candidates}
    return tuple(by_height[height] for height in sorted(by_height))


class CapabilityAdapter(Protocol):
    """B04 supplies native implementation; does not modify the glider CEM API."""
    descriptor: CapabilityDescriptor

    def candidates(self, brief: EngineeringBrief) -> tuple[CandidateSpec, ...]: ...
    async def evaluate(self, candidate: CandidateSpec) -> dict: ...
