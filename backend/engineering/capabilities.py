"""Versioned, provider-free metadata for the checked-design capabilities.

This registry does not probe native availability or execute geometry. The beam
adapter lives in ``cantilever``; generator adapters resolve lazily from the
descriptor's import string. Importing this module loads only pydantic,
``cem.core`` and the pure-pydantic glider requirement models: no provider,
CadQuery, solver, trimesh, scipy or web framework (a test pins this).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import StringConstraints, ValidationError

from cem.core import StrictModel
from cem.glider.requirements import GliderMissionRequirements
from .spec import CantileverSpec

if TYPE_CHECKING:
    from .contracts import CandidateSpec, EngineeringBrief, GeneratorCandidate


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


Sha256 = Annotated[str, StringConstraints(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')]
GENERATOR_SPEC_PREFIX = 'specification.'
# Payloads every generator package carries beside its own ``generator-*`` files.
GENERATOR_BASE_PAYLOADS = ('brief.json', 'selected_spec.json', 'attempts.json', 'report.json', 'report.md')
GENERATOR_DIAGNOSTIC_BASE = ('brief.json', 'attempts.json', 'report.json', 'report.md')
GENERATOR_PREFIX = 'generator-'
# Families the glider CEM can build. Pinned against ``cem.glider.model.SUPPORTED_FAMILIES``
# by a CI drift test; copied here so this registry imports no solver code.
GLIDER_SUPPORTED_FAMILIES = ('conventional', 't_tail', 'v_tail')
REGEN_MIN_VOXEL_SIZE_MM = 0.4


class PolicyReference(StrictModel):
    """A read-only snapshot of a constant owned by another module.

    No decision reads ``value``. A drift test resolves ``module.attribute`` and
    asserts equality, so a changed constant fails CI instead of drifting silently.
    Inline literals carry no module; ``source`` names ``file:line``.
    """
    name: str
    module: str | None = None
    attribute: str | None = None
    value: float | int | str | bool
    source: str

    def resolve(self):
        if self.module is None or self.attribute is None:
            raise ValueError('Inline policy literal has no importable owner')
        return getattr(importlib.import_module(self.module), self.attribute)


class ConditionalCheck(StrictModel):
    """A required check that exists only when a specification leaf has one value."""
    path: str
    equals: str
    check_id: str


def _leaf(payload: Any, path: str):
    for part in path.split('.'):
        if not isinstance(payload, dict) or part not in payload:
            return None
        payload = payload[part]
    return payload


def _leaves(payload: dict, prefix: str = '') -> list[str]:
    names = []
    for key in sorted(payload):
        name = f'{prefix}{key}'
        names += _leaves(payload[key], name + '.') if isinstance(payload[key], dict) and payload[key] else [name]
    return names


class GeneratorDescriptor(StrictModel):
    id: str
    version: str
    label: str
    numerical_version: str
    spec_schema: str
    base_checks: tuple[str, ...]
    conditional_checks: tuple[ConditionalCheck, ...] = ()
    unsupported_features: tuple[str, ...]
    refuse_with: str | None = None
    selection_rule: str
    numerical: tuple[PolicyReference, ...]
    runtime_limits: tuple[PolicyReference, ...]
    primary_artifact: str
    payloads: tuple[str, ...]
    adapter: str
    proposers: dict[str, str] = {}
    engineering_certified: Literal[False] = False
    standards: Literal['not_evaluated'] = 'not_evaluated'
    limitations: tuple[str, ...]
    # The engineering budget binds every capability; its values are never overridden here.
    resources: ResourcePolicy = ResourcePolicy()

    @property
    def all_check_ids(self) -> tuple[str, ...]:
        return self.base_checks + tuple(c.check_id for c in self.conditional_checks)

    def parse_spec(self, payload: dict):
        """Validate through the strict spec model's JSON boundary; errors propagate."""
        schema = resolve(self.spec_schema)
        encoded = json.dumps(payload, allow_nan=False)
        return (schema.model_validate_json(encoded) if hasattr(schema, 'model_validate_json')
                else schema.validate_json(encoded))

    def required_checks(self, specification: dict) -> tuple[str, ...]:
        return self.base_checks + tuple(c.check_id for c in self.conditional_checks
                                        if _leaf(specification, c.path) == c.equals)

    def review_fields(self, specification: dict) -> tuple[str, ...]:
        return tuple(GENERATOR_SPEC_PREFIX + name for name in _leaves(specification))

    def refuse(self, specification: Any) -> tuple[tuple[str, str], ...]:
        """Return ``(field, reason)`` for every unsupported request; empty means supported."""
        if self.refuse_with is None or not isinstance(specification, dict):
            return ()
        return tuple(resolve(self.refuse_with)(specification))

    def expected_limits(self, specification: dict) -> dict:
        """The closed limit table: ``{check_id: tuple[ComparisonLimit, ...]}``.

        Built only from this descriptor's pinned ``PolicyReference`` values and the
        reviewed specification, never from a generator report. A check absent from the
        table has no pinned limit set yet and can never be accepted.
        """
        from .contracts import ComparisonLimit
        pinned = {r.name: r.value for r in self.numerical}
        table = {}
        for check_id, rows in _LIMIT_TABLE[self.id](specification).items():
            table[check_id] = tuple(ComparisonLimit(name=name, unit=unit, comparison=comparison,
                                                    value=float(pinned[ref] if isinstance(ref, str) else ref))
                                    for name, unit, comparison, ref in rows)
        return table

    @property
    def diagnostic_payloads(self) -> tuple[str, ...]:
        return tuple(p for p in self.payloads if p.endswith('.json'))


class GliderCapabilitySpec(StrictModel):
    requirements: GliderMissionRequirements
    calibration_version: Sha256
    selection_rule: Literal['max_objective_value'] = 'max_objective_value'


# Closed limit tables: ``(limit name, unit, comparison, pinned constant name | literal)``.
# They hold only what section 5.3 and the pinned constants already fix. Each adapter PR
# completes its own table (TODO U6.3 dragster, U6.1 regen): a check missing
# here cannot be accepted until then, so nothing is ever approved on a guessed limit.
def _dragster_limits(spec: dict) -> dict:
    wedge = spec.get('style') == 'wedge'
    wall = ('wall_distance_error', 'mm', 'le', 'WALL_DISTANCE_TOL_MM')
    void = ('residual_void', 'mm3', 'le', 'VOID_TOLERANCE_MM3')
    return {
        'absolute_bounds': (('bounds_deviation', 'mm', 'le', 'WEDGE_BOUNDS_TOL_MM' if wedge else 'LOFT_BOUNDS_TOL_MM'),),
        'reference_volume': (('volume_relative_error', 'ratio', 'le', 'LOFT_VOLUME_REL_TOL'),),
        'symmetry': (('centroid_y_offset', 'mm', 'abs_le', 'CENTROID_Y_TOL_MM'),),
        'pocket_void': (void,), 'front_axle_void': (void,), 'rear_axle_void': (void,),
        'pocket_wall': (wall,), 'front_bore_wall': (wall,), 'rear_bore_wall': (wall,),
    }


def _regen_limits(spec: dict) -> dict:
    return {
        'volume': (('volume_relative_error', 'ratio', 'le', 'VOLUME_TOLERANCE'),),
        'bbox': (('bbox_error_voxels', 'count', 'le', 'BBOX_TOLERANCE_VOXELS'),),
        'channel_count': (('channel_count', 'count', 'eq', spec.get('channel_count', 0)),),
    }


def _glider_limits(spec: dict) -> dict:
    """Every glider check, from the ``cem/evidence.validate`` floors (pinned names) and the spec.

    Literals not in ``GLIDER.numerical`` carry their ``backend/cem/evidence.py`` line:
    spar enclosure tolerance 2e-7 (:67), root inertia floor 1e-16 (:264), stress slack
    ``1+1e-5`` (:263). Counts of 0 are "no failure" tallies of the report's own flags.
    """
    construction = _leaf(spec, 'requirements.construction')
    max_mass = _leaf(spec, 'requirements.constraints.max_mass_kg')
    table = {
        'discovery_archive': (('archive_elites', 'count', 'ge', 1),),
        'candidate_selection': (('candidates', 'count', 'ge', 1),),
        'design_binding': (('binding_mismatches', 'count', 'eq', 0),),
        'closure': (('closure_failures', 'count', 'eq', 0),),
        'part_accounting': (('bundle_parts', 'count', 'ge', 'parts_min'),
                            ('part_count_headroom', 'count', 'ge', 0),
                            ('part_accounting_failures', 'count', 'eq', 0)),
        'spar_enclosure': (('spar_enclosure_tolerance_m', 'm', 'le', 2e-7),
                           ('spar_enclosure_excess_m', 'm', 'le', 0),
                           ('spar_enclosure_failures', 'count', 'eq', 0)),
        'mass_closure': (('mass_relative_error', 'ratio', 'le', 'mass_relative_error_max'),),
        'measured_mass_aero': (('measured_mass_kg', 'kg', 'le', max_mass if isinstance(max_mass, (int, float)) else 0.5),
                               ('measured_mass_aero_invalid', 'count', 'eq', 0)),
        'validity_envelope': (('min_station_reynolds', 'ratio', 'ge', 'min_station_reynolds'),
                              ('min_confidence', 'ratio', 'ge', 'min_confidence')),
        'spar_structure': (('spar_stress_to_allowable', 'ratio', 'le', 1.0 + 1e-5),
                           ('measured_spar_root_inertia_m4', 'm4', 'ge', 1e-16),
                           ('spar_structure_failures', 'count', 'eq', 0)),
        'uncertainty_bands': (('uncertainty_samples', 'count', 'eq', 'uncertainty_samples'),
                              ('sustained_samples', 'count', 'eq', 0),
                              ('band_violations', 'count', 'eq', 0)),
        'minimum_performance': (('minimum_performance_failures', 'count', 'eq', 0),),
        'artifact_binding': (('artifact_hash_mismatches', 'count', 'eq', 0),
                             ('bundle_bytes', 'count', 'le', 'artifact_bytes_max')),
    }
    if construction == 'foam_core_wing_v1':
        table['foam_core'] = (('foam_core_failures', 'count', 'eq', 0),)
    if construction == 'segmented_foam_wing_v1':
        table['segmented_structure'] = (('segmented_structure_failures', 'count', 'eq', 0),)
    return table


_LIMIT_TABLE = {'co2_dragster': _dragster_limits, 'regen_chamber': _regen_limits, 'glider_cem': _glider_limits}


def refuse_dragster(spec: dict):
    if spec.get('schema_version') == 1:
        yield 'specification.schema_version', 'Version 1 dragster specs stay on the original endpoint; v1 checked runs need schema_version 2'


def refuse_regen(spec: dict):
    voxel = spec.get('voxel_size_mm')
    if isinstance(voxel, (int, float)) and not isinstance(voxel, bool) and voxel < REGEN_MIN_VOXEL_SIZE_MM:
        yield 'specification.voxel_size_mm', f'Voxel sizes below {REGEN_MIN_VOXEL_SIZE_MM} mm are not supported'


def refuse_glider(spec: dict):
    requirements = spec.get('requirements')
    if not isinstance(requirements, dict):
        return
    environment = requirements.get('environment')
    if isinstance(environment, dict) and (environment.get('kind') in ('thermal', 'ridge')
                                          or environment.get('thermal') or environment.get('ridge')):
        yield 'specification.requirements.environment', 'Thermal and ridge environments are not supported'
    discovery = requirements.get('discovery')
    families = discovery.get('families', 'all') if isinstance(discovery, dict) else 'all'
    if families == 'all' or (isinstance(families, list) and not set(families) <= set(GLIDER_SUPPORTED_FAMILIES)):
        yield ('specification.requirements.discovery.families',
               'List only supported families explicitly: ' + ', '.join(GLIDER_SUPPORTED_FAMILIES))


def _ref(name, module, attribute, value, source):
    return PolicyReference(name=name, module=module, attribute=attribute, value=value, source=source)


def _inline(name, value, source):
    return PolicyReference(name=name, value=value, source=source)


_TOL = 'backend/dragsters/tolerances.py'
_VAL = 'backend/dragsters/validation_v2.py'
_MESH = 'backend/engineering/regen_mesh.py'
_RSPEC = 'backend/engineering/regen_spec.py'
_EVID = 'backend/cem/evidence.py'

DRAGSTER = GeneratorDescriptor(
    id='co2_dragster', version='2.0.0', label='CO2 dragster body (specification v2)',
    numerical_version='co2_dragster_numerical_v1',
    spec_schema='dragsters.spec_v2:SPEC_V2_ADAPTER',
    base_checks=('single_valid_solid', 'absolute_bounds', 'symmetry', 'reference_volume',
                 'pocket_void', 'front_axle_void', 'rear_axle_void', 'pocket_wall',
                 'front_bore_wall', 'rear_bore_wall', 'spec_revalidation',
                 'wheel_clearance_front', 'wheel_clearance_rear', 'body_above_ground'),
    unsupported_features=('schema_version_1', 'styles_outside_registry', 'wheels_or_hardware_as_step_parts',
                          'fillets', 'spoilers', 'decals', 'performance_claims', 'competition_legality_claims'),
    refuse_with='engineering.capabilities:refuse_dragster',
    selection_rule='single reviewed specification',
    numerical=(
        _ref('LOFT_VOLUME_REL_TOL', 'dragsters.tolerances', 'LOFT_VOLUME_REL_TOL', 0.010, _TOL),
        _ref('LOFT_BOUNDS_TOL_MM', 'dragsters.tolerances', 'LOFT_BOUNDS_TOL_MM', 0.05, _TOL),
        _ref('WEDGE_BOUNDS_TOL_MM', 'dragsters.tolerances', 'WEDGE_BOUNDS_TOL_MM', 0.01, _TOL),
        _ref('WALL_DISTANCE_TOL_MM', 'dragsters.tolerances', 'WALL_DISTANCE_TOL_MM', 0.01, _TOL),
        _ref('VOID_TOLERANCE_MM3', 'dragsters.tolerances', 'VOID_TOLERANCE_MM3', 0.001, _TOL),
        _ref('STATION_STEP_MM', 'dragsters.tolerances', 'STATION_STEP_MM', 10.0, _TOL),
        _ref('CROWN_POINTS', 'dragsters.tolerances', 'CROWN_POINTS', 33, _TOL),
        _ref('CENTROID_Y_TOL_MM', 'dragsters.validation_v2', 'CENTROID_Y_TOL_MM', 0.01, _VAL),
        _ref('AXIS_TOL_MM', 'dragsters.validation_v2', 'AXIS_TOL_MM', 1e-3, _VAL),
        _ref('RADIUS_TOL_MM', 'dragsters.validation_v2', 'RADIUS_TOL_MM', 1e-4, _VAL),
        _ref('PLANE_TOL_MM', 'dragsters.validation_v2', 'PLANE_TOL_MM', 1e-4, _VAL),
        _ref('PARALLEL_TOL', 'dragsters.validation_v2', 'PARALLEL_TOL', 1e-9, _VAL),
        _ref('SAMPLE_STEP_MM', 'dragsters.validation_v2', 'SAMPLE_STEP_MM', 0.25, _VAL),
        _ref('PROBE_MARGIN_MM', 'dragsters.validation_v2', 'PROBE_MARGIN_MM', 0.05, _VAL)),
    runtime_limits=(
        _inline('ProcessRunner.timeout_s', 30, 'backend/dragsters/service.py:48'),
        _inline('ProcessRunner.memory_mb', 1536, 'backend/dragsters/service.py:48'),
        _inline('ProcessRunner.output_limit_bytes', 1048576, 'backend/dragsters/service.py:48')),
    primary_artifact='generator-body.step',
    payloads=('generator-spec.json', 'generator-report.json', 'generator-body.step', 'generator-preview.glb'),
    adapter='engineering.dragster_adapter:DragsterAdapter',
    proposers={'rules': 'engineering.dragster_proposer:RulesProposer'},
    limitations=('Digital geometry checks of a parametric body; no aerodynamic, performance or competition-legality claim',
                 'Verifier failures end as unavailable without a package; no rejected report is written',
                 'Runs past 420 s or 32 MiB are unavailable here; the original endpoint still serves them'))

REGEN = GeneratorDescriptor(
    id='regen_chamber', version='1.0.0', label='Regenerative-cooled chamber geometry',
    numerical_version='regen_chamber_numerical_v1',
    spec_schema='engineering.regen_spec:RegenChamberSpec',
    base_checks=('components', 'edge_manifold', 'winding', 'bbox', 'volume', 'channel_count',
                 'hot_wall', 'cold_wall', 'rib', 'circuit_fidelity', 'circuit_coverage',
                 'wall_thickness', 'evidence_binding'),
    unsupported_features=('voxel_size_below_0.4_mm', 'step_export', 'gas_dynamics', 'thermal_analysis',
                          'structural_analysis', 'manufacturing_analysis'),
    refuse_with='engineering.capabilities:refuse_regen',
    selection_rule='single reviewed specification',
    numerical=(
        _ref('VOLUME_TOLERANCE', 'engineering.regen_mesh', 'VOLUME_TOLERANCE', 0.02, _MESH),
        _ref('BBOX_TOLERANCE_VOXELS', 'engineering.regen_mesh', 'BBOX_TOLERANCE_VOXELS', 2.0, _MESH),
        _ref('STATION_COUNT', 'engineering.regen_mesh', 'STATION_COUNT', 7, _MESH),
        _ref('SPECK_VOLUME_MM3', 'engineering.regen_mesh', 'SPECK_VOLUME_MM3', 1e-3, _MESH),
        _ref('WP_MAX_ADAPT_LEAVES', 'engineering.regen_mesh', 'WP_MAX_ADAPT_LEAVES', 50000, _MESH),
        _ref('WP_LEAF_BUDGET', 'engineering.regen_mesh', 'WP_LEAF_BUDGET', 40000000, _MESH),
        _ref('WP_PAIR_BUDGET', 'engineering.regen_mesh', 'WP_PAIR_BUDGET', 60000000, _MESH),
        _ref('WP_MEMORY_BUDGET', 'engineering.regen_mesh', 'WP_MEMORY_BUDGET', 1536 << 20, _MESH),
        _ref('WP_NEIGHBOR_BUDGET', 'engineering.regen_mesh', 'WP_NEIGHBOR_BUDGET', 20000, _MESH),
        _ref('WP_COVERAGE_BUDGET', 'engineering.regen_mesh', 'WP_COVERAGE_BUDGET', 60000000, _MESH),
        _ref('WP_QUEUE_BUDGET', 'engineering.regen_mesh', 'WP_QUEUE_BUDGET', 100000, _MESH),
        _ref('WP_SCRATCH_BUDGET', 'engineering.regen_mesh', 'WP_SCRATCH_BUDGET', 32 << 20, _MESH),
        _ref('MAX_SLOPE', 'engineering.regen_spec', 'MAX_SLOPE', 0.7002075382097097, _RSPEC),
        _ref('MAX_BOUNDING_VOXELS', 'engineering.regen_spec', 'MAX_BOUNDING_VOXELS', 3e8, _RSPEC)),
    runtime_limits=(
        _ref('BUILDER_TIMEOUT_S', 'engineering.regen_cli', 'BUILDER_TIMEOUT_S', 600, 'backend/engineering/regen_cli.py'),
        _inline('JobRunner.timeout_s', 900, 'backend/engineering/regen_web.py:64'),
        _inline('JobRunner.output_limit_bytes', 1 << 30, 'backend/engineering/regen_web.py:64'),
        _inline('worker.RLIMIT_AS_bytes', 4 << 30, 'backend/engineering/regen_web.py:322')),
    primary_artifact='generator-cleaned.stl',
    payloads=('generator-cleaned.stl', 'generator-report.json', 'generator-spec.canonical.json'),
    adapter='engineering.regen_adapter:RegenAdapter',
    limitations=('Geometry only: no gas dynamics, thermal, structural or manufacturing analysis',
                 'Simplified C1 cosine contour, not an optimized nozzle',
                 'Cancelling a run leaves the builder process to its own time limit',
                 'Runs past 420 s or 32 MiB are unavailable here; the original endpoint still serves them'))

GLIDER = GeneratorDescriptor(
    id='glider_cem', version='1.0.0', label='Glider CEM discovery and design',
    numerical_version='glider_cem_numerical_v1',
    spec_schema='engineering.capabilities:GliderCapabilitySpec',
    base_checks=('discovery_archive', 'candidate_selection', 'design_binding', 'closure',
                 'part_accounting', 'spar_enclosure', 'mass_closure', 'measured_mass_aero',
                 'validity_envelope', 'spar_structure', 'uncertainty_bands', 'minimum_performance',
                 'artifact_binding'),
    conditional_checks=(
        ConditionalCheck(path='requirements.construction', equals='foam_core_wing_v1', check_id='foam_core'),
        ConditionalCheck(path='requirements.construction', equals='segmented_foam_wing_v1',
                         check_id='segmented_structure')),
    unsupported_features=('thermal_or_ridge_environment', 'families_outside_supported_set', 'shape_comparison',
                          'flight_test_calibration_updates', 'novel_shapes', 'picogk_builder',
                          'manufacturing_readiness', 'certification'),
    refuse_with='engineering.capabilities:refuse_glider',
    selection_rule='max_objective_value',
    numerical=(
        _inline('min_station_reynolds', 30000.0, _EVID + ':268'),
        _inline('min_confidence', 0.8, _EVID + ':267'),
        _inline('mass_relative_error_max', 0.05, _EVID + ':258'),
        _inline('spar_stress_allowable_factor_slack', 1e-5, _EVID + ':263'),
        _inline('uncertainty_samples', 200, _EVID + ':270'),
        _inline('parts_min', 3, _EVID + ':281'),
        _inline('parts_max', 16, _EVID + ':280'),
        _inline('parts_max_segmented', 32, _EVID + ':280'),
        _inline('artifact_bytes_max', 64 * 1024 * 1024, _EVID + ':304')),
    runtime_limits=(),
    primary_artifact='generator-bundle.zip',
    payloads=('generator-bundle.zip', 'generator-result.json'),
    adapter='engineering.glider_adapter:GliderAdapter',
    limitations=('Reduced-order computational model; verified_gain and manufacturing_ready stay false',
                 'Floors are inline literals in cem/evidence.py; implementation_sha256 in each report binds them',
                 'Runs past 420 s or 32 MiB are unavailable here; the original endpoint still serves them'))

GENERATORS: tuple[GeneratorDescriptor, ...] = (DRAGSTER, REGEN, GLIDER)


def list_capabilities() -> tuple[CapabilityDescriptor | GeneratorDescriptor, ...]:
    """The beam stays first: ``frontend/engineering_ui.py`` reads ``capabilities[0]``."""
    return (CAPABILITY, *GENERATORS)


def get_generator(capability_id: str) -> GeneratorDescriptor | None:
    return next((g for g in GENERATORS if g.id == capability_id), None)


def get_capability(capability_id: str, version: str, *,
                   numerical_policy_version: str = 'beam_numerical_v1',
                   resource_policy_version: str = 'engineering_resources_v1'
                   ) -> CapabilityDescriptor | GeneratorDescriptor:
    for descriptor in list_capabilities():
        numerical = (descriptor.numerical.version if isinstance(descriptor, CapabilityDescriptor)
                     else descriptor.numerical_version)
        resources = descriptor.resources.version
        if (capability_id, version, numerical_policy_version, resource_policy_version) == (
                descriptor.id, descriptor.version, numerical, resources):
            return descriptor
    raise UnsupportedCapability('Capability/policy version unavailable; prepare and review a supported brief')


def resolve(reference: str) -> Any:
    """Resolve a lazy ``module:attribute`` import string."""
    module, _, attribute = reference.partition(':')
    return getattr(importlib.import_module(module), attribute)


def adapter_installed(descriptor: GeneratorDescriptor) -> bool:
    module = descriptor.adapter.partition(':')[0]
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def get_adapter(capability_id: str, version: str, **policies) -> CapabilityAdapter:
    """Lazy fixed registration; metadata imports do not initialize native CAD."""
    descriptor = get_capability(capability_id, version, **policies)
    if isinstance(descriptor, CapabilityDescriptor):
        from .cantilever import CantileverAdapter
        return CantileverAdapter()
    module, _, attribute = descriptor.adapter.partition(':')
    try:
        loaded = importlib.import_module(module)
    except ModuleNotFoundError as missing:
        if missing.name == module:
            raise UnsupportedCapability('adapter not installed') from None
        raise
    return getattr(loaded, attribute)()


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
    descriptor: CapabilityDescriptor | GeneratorDescriptor

    def candidates(self, brief: EngineeringBrief) -> tuple[CandidateSpec | GeneratorCandidate, ...]: ...
    async def evaluate(self, candidate: CandidateSpec | GeneratorCandidate) -> dict: ...

    def measurements(self, report_bytes: bytes) -> dict:
        """Generators only: parse the generator's own report into
        ``{check_id: {measurement name: value}}``. The policy requires the typed checks'
        measurements to equal this exactly."""
        ...
