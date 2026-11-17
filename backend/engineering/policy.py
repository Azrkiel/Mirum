"""Fail-closed V1 evidence policy. No CAD oracle, solver or narrative override.

Decisions describe candidates. Only B09/B10 may commit an accepted run after
atomic bundle publication. Digests provide consistency, not authenticity.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
from typing import Literal

from .capabilities import CAPABILITY
from .contracts import (ArtifactIdentity, CandidateSpec, CheckResult, CheckState,
                        ComparisonLimit, ContractModel, Measurement, Outcome, Sha256, Text)
from .spec import build_source


def report_digest(report: dict) -> str:
    return hashlib.sha256(json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class Stage(ContractModel):
    state: CheckState
    reason: Text | None = None


class Standards(ContractModel):
    state: Literal['not_evaluated']
    reason: Text


class BeamSolution(ContractModel):
    elements: int
    degrees_of_freedom: int
    tip_displacement_mm: float
    tip_rotation_rad: float
    root_bending_stress_mpa: float
    root_reaction_force_n: float
    root_reaction_moment_n_mm: float
    relative_equilibrium_residual: float
    strain_energy_n_mm: float
    second_moment_mm4: float


class PhysicsEvidence(Stage):
    method: Literal['euler_bernoulli_beam_fem_v1'] | None = None
    units: Literal['mm_N_MPa'] | None = None
    mesh_history: tuple[BeamSolution, ...] | None = None
    solution: BeamSolution | None = None
    relative_mesh_change: float | None = None
    mesh_converged: bool | None = None
    analytic_benchmark_passed: bool | None = None
    relative_analytic_error: float | None = None
    max_tip_deflection_mm: float | None = None
    allowable_stress_mpa: float | None = None
    material_reference: Text | None = None
    boundary_conditions: Literal['X=0 ideal clamp; +Z end force at X=L'] | None = None


class WorkerCheck(ContractModel):
    state: CheckState
    measurements: tuple[Measurement, ...]
    limits: tuple[ComparisonLimit, ...]
    reason: Text | None


class NativeGeometry(ContractModel):
    passed: bool
    checks: dict[str, bool]
    failures: tuple[Text, ...]
    volume_mm3: float | None
    surface_area_mm2: float | None
    bounding_box: dict[str, float | None]
    solid_count: int
    max_topology_tolerance_mm: float | None
    scope: Literal['structural_solid_set_v1']


class NumericalEvidence(ContractModel):
    max_topology_tolerance_mm: float
    dimension_guard_mm: float
    datum_budget_mm: float
    is_manufacturing_uncertainty: Literal[False]


class NativeReport(ContractModel):
    scope: Literal['rectangular_cantilever_v1']
    passed: bool
    engineering_certified: Literal[False]
    spec_sha256: Sha256
    design_intent: Stage
    physics: PhysicsEvidence
    standards: Standards
    checks: dict[str, WorkerCheck]
    failures: tuple[Text, ...]
    measurements: dict[str, float]
    limitations: tuple[Text, ...]
    native_geometry: NativeGeometry
    numerical_policy: NumericalEvidence | None = None
    step_sha256: Sha256
    step_bytes: int
    versions: dict[str, Text]
    implementation_sha256: dict[str, Sha256]
    artifacts: dict[str, Sha256] | None = None
    assumptions: Literal['ideal_clamp_end_force_linear_elastic'] | None = None


class PolicyDecision(ContractModel):
    available: bool
    outcome: Outcome
    checks: tuple[CheckResult, ...]
    reasons: tuple[Text, ...]


class _InvalidEvidence(ValueError):
    """Carries a fixed safe diagnostic, never a worker exception/input dump."""


def _require(condition, reason):
    if not condition:
        raise _InvalidEvidence(reason)


def _limits(name, candidate, report, measured):
    """Pin comparisons to policy/spec; do not trust worker-supplied limits."""
    s, p, m = candidate.specification, CAPABILITY.numerical, report.measurements
    table = {
        'native_geometry': [('native_checks_passed', 'count', 'eq', len(report.native_geometry.checks))],
        'dimensional_evidence_budget': [('topology_tolerance', 'mm', 'le', min(p.max_topology_tolerance_mm, s.dimensional_tolerance_mm/10))],
        'independent_step_read': [('read_status', 'count', 'eq', 1)],
        'independent_step_transfer': [('transferred_roots', 'count', 'eq', measured.get('roots')), ('roots', 'count', 'ge', 1)],
        'single_rectangular_solid': [(n, 'count', 'eq', v) for n, v in [('solids', 1), ('faces', 6), ('edges', 12), ('vertices', 8)]],
        'planar_faces': [('planar_faces', 'count', 'eq', 6)],
        'finite_measurements': [('finite_coordinates', 'count', 'eq', 6), ('positive_dimensions', 'count', 'eq', 3)],
        'size_requirements': [(n+'_guarded_error', 'mm', 'le', s.dimensional_tolerance_mm) for n in ('length', 'width', 'height')],
        'datum_frame': [(n+'_origin', 'mm', 'abs_le', p.datum_budget_mm) for n in ('x', 'y', 'z')],
        'axis_aligned_faces': [('max_normal_error', 'ratio', 'le', p.face_normal_error)],
        'rectangular_face_pairs': [(n+'_faces', 'count', 'eq', 2) for n in ('x', 'y', 'z')] + [('root_faces', 'count', 'eq', 1)],
        'mesh_convergence': [('relative_mesh_change', 'ratio', 'le', p.convergence_relative_error)],
        'analytic_benchmark': [('relative_analytic_error', 'ratio', 'le', p.analytic_relative_error)],
        'equilibrium': [('max_equilibrium_residual', 'ratio', 'le', p.equilibrium_relative_error)],
        'small_strain': [('strain', 'ratio', 'le', p.max_strain)],
        'stress_allowable': [('root_stress', 'MPa', 'le', s.allowable_stress_mpa)],
        'displacement_requirement': [('tip_displacement', 'mm', 'le', s.max_tip_deflection_mm)],
    }
    if name == 'complete_prism_metrics':
        # A rejected geometry prefix may not have the final measurement block.
        # Its error/limit measurements are still native evidence; the limit is
        # derived from the actual native aggregate metrics available at this stage.
        native = report.native_geometry
        return [('volume_error', 'mm3', 'le', max(p.metric_absolute_error, native.volume_mm3*p.metric_relative_error)),
                ('area_error', 'mm2', 'le', max(p.metric_absolute_error, native.surface_area_mm2*p.metric_relative_error))]
    if name == 'measured_root_section':
        width = m.get('width_mm', report.native_geometry.bounding_box['y_mm'])
        height = m.get('height_mm', report.native_geometry.bounding_box['z_mm'])
        inertia = m.get('section_iy_mm4', width*height**3/12)
        return [('root_area_error', 'mm2', 'le', max(p.metric_absolute_error, width*height*p.metric_relative_error)),
                ('root_inertia_error', 'mm4', 'le', max(p.metric_absolute_error, inertia*p.metric_relative_error))]
    if name == 'small_deflection':
        return [('tip_displacement', 'mm', 'le', m['length_mm']*p.max_displacement_span_ratio)]
    return table[name]


def _comparison(value, limit):
    if limit.comparison == 'le': return value <= limit.value
    if limit.comparison == 'ge': return value >= limit.value
    if limit.comparison == 'eq': return value == limit.value
    return abs(value) <= limit.value


def _validate(candidate, evaluation):
    from .cantilever import report_checks
    candidate = CandidateSpec.model_validate(candidate.model_dump())
    _require(CandidateSpec.model_validate(evaluation['candidate'].model_dump()) == candidate, 'Candidate association mismatch')
    raw = evaluation['report']
    encoded = json.dumps(raw, allow_nan=False)
    _require(len(encoded.encode()) <= CAPABILITY.resources.native_output_bytes, 'Native report exceeds budget')
    report = NativeReport.model_validate_json(encoded)
    _require(raw['engineering_certified'] is False, 'Unsupported certification claim')
    _require(evaluation['report_sha256'] == report_digest(raw), 'Report changed after capture')
    checks = tuple(CheckResult.model_validate(c.model_dump()) for c in evaluation['checks'])
    _require(checks == report_checks(candidate, raw), 'Typed checks differ from native report')
    step = ArtifactIdentity(path='model.step', sha256=report.step_sha256, bytes=report.step_bytes)
    _require(evaluation['step_identity'] == step, 'STEP identity mismatch')
    paths = [Path(__file__).with_name(n) for n in ('verify.py', 'beam_fea.py', 'spec.py')]
    paths.append(Path(__file__).parents[1]/'verification'/'solid_set.py')
    _require(report.implementation_sha256 == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}, 'Unsupported verifier revision')
    _require(all(report.versions[k] == version(package) for k, package in
                 [('cadquery', 'cadquery'), ('ocp', 'cadquery-ocp'), ('numpy', 'numpy')]), 'Unsupported native runtime')
    _require(report.versions['python'] == platform.python_version(), 'Unsupported Python runtime')
    native = report.native_geometry
    required_native = {'native_backend', 'step_file', 'step_size_limit', 'step_read', 'step_transfer',
        'non_null_shape', 'complete_solid_accounting', 'closed_shells', 'solid_limit', 'topology_limit',
        'solid_presence', 'brep_valid_geometry', 'positive_finite_metrics', 'finite_optimal_bounds',
        'topology_tolerance_policy', 'no_positive_volume_interpenetration'}
    _require(native.passed and not native.failures and required_native <= native.checks.keys()
             and all(native.checks.values()), 'Required native structural evidence incomplete')
    _require(native.volume_mm3 is not None and native.volume_mm3 > 0
             and native.surface_area_mm2 is not None and native.surface_area_mm2 > 0
             and native.max_topology_tolerance_mm is not None and native.max_topology_tolerance_mm > 0
             and native.solid_count > 0 and set(native.bounding_box) == {'x_mm', 'y_mm', 'z_mm'}
             and all(v is not None and v > 0 for v in native.bounding_box.values()), 'Native measurements incomplete')
    raw_ids = tuple(report.checks)
    _require(raw_ids == CAPABILITY.required_checks[:len(raw_ids)] and raw_ids, 'Required check prefix incomplete')
    failed = []
    for check in checks:
        if check.state == CheckState.NOT_EVALUATED:
            _require(bool(failed), 'Mandatory evidence not evaluated before a measured failure')
            continue
        _require(not failed and check.state in (CheckState.PASSED, CheckState.FAILED), 'Unexpected check state/order')
        measured = {m.name: m.value for m in check.measurements}
        expected = tuple(ComparisonLimit(name=n, unit=u, comparison=op, value=float(value))
                         for n, u, op, value in _limits(check.check_id, candidate, report, measured))
        _require(check.limits == expected and set(measured) == {v.name for v in expected}, 'Missing measurements or unsupported limits')
        passes = all(_comparison(measured[limit.name], limit) for limit in expected)
        _require(passes == (check.state == CheckState.PASSED), 'Check flag contradicts measured comparison')
        if check.check_id == 'dimensional_evidence_budget':
            _require(measured['topology_tolerance'] == native.max_topology_tolerance_mm, 'Topology tolerance association mismatch')
            if check.state == CheckState.PASSED:
                numerical = report.numerical_policy
                _require(numerical is not None and raw['numerical_policy']['is_manufacturing_uncertainty'] is False
                         and numerical.max_topology_tolerance_mm == native.max_topology_tolerance_mm
                         and numerical.dimension_guard_mm == 2*native.max_topology_tolerance_mm
                         and numerical.datum_budget_mm == CAPABILITY.numerical.datum_budget_mm,
                         'Missing or inconsistent digital tolerance policy')
        if check.check_id == 'single_rectangular_solid':
            _require(measured['solids'] == native.solid_count, 'Solid count association mismatch')
        if check.state == CheckState.FAILED:
            failed.append(check.reason)
    _require(tuple(report.failures) == tuple(failed), 'Failure explanations do not match measured checks')
    geometry_failed = any(c.state == CheckState.FAILED for c in checks[:13])
    _require(report.design_intent.state == (CheckState.FAILED if geometry_failed else CheckState.PASSED),
             'Design intent state contradicts measured checks')
    _require((report.physics.state == CheckState.NOT_EVALUATED) == geometry_failed, 'Physics evaluation state contradicts geometry')
    if report.measurements:
        m, s = report.measurements, candidate.specification
        _require(set(m) == {'length_mm', 'width_mm', 'height_mm', 'volume_mm3', 'surface_area_mm2',
                             'section_area_mm2', 'section_iy_mm4'} and all(v > 0 for v in m.values()), 'Incomplete measured geometry')
        by_id = {c.check_id: c for c in checks}
        actual_size = {q.name: q.value for q in by_id['size_requirements'].measurements}
        t = native.max_topology_tolerance_mm
        _require(all(actual_size[n+'_guarded_error'] == abs(m[n+'_mm']-v)+2*t for n, v in
                    [('length', s.length_mm), ('width', s.width_mm), ('height', s.height_mm)]), 'Geometry measurements changed')
    physics = report.physics
    if physics.state != CheckState.NOT_EVALUATED:
        _require(all(getattr(physics, name) is not None for name in PhysicsEvidence.model_fields if name not in ('state', 'reason')),
                 'Incomplete beam evidence')
        _require(tuple(h.elements for h in physics.mesh_history) == CAPABILITY.numerical.mesh_elements
                 and all(h.degrees_of_freedom == 2*h.elements for h in physics.mesh_history)
                 and physics.solution == physics.mesh_history[-1], 'Incomplete or changed mesh solution')
        s, m, last = candidate.specification, report.measurements, physics.solution
        _require(physics.allowable_stress_mpa == s.allowable_stress_mpa
                 and physics.max_tip_deflection_mm == s.max_tip_deflection_mm
                 and physics.material_reference == s.material_reference
                 and all(h.second_moment_mm4 == m['section_iy_mm4'] for h in physics.mesh_history), 'Physics input association mismatch')
        _require(physics.mesh_converged == (physics.relative_mesh_change <= CAPABILITY.numerical.convergence_relative_error)
                 and physics.analytic_benchmark_passed == (physics.relative_analytic_error <= CAPABILITY.numerical.analytic_relative_error),
                 'Beam flags contradict numerical evidence')
        expected_values = {
            'mesh_convergence': physics.relative_mesh_change, 'analytic_benchmark': physics.relative_analytic_error,
            'equilibrium': max(h.relative_equilibrium_residual for h in physics.mesh_history),
            'small_deflection': last.tip_displacement_mm,
            'small_strain': last.root_bending_stress_mpa/s.youngs_modulus_mpa,
            'stress_allowable': last.root_bending_stress_mpa, 'displacement_requirement': last.tip_displacement_mm}
        _require(all(check.measurements[0].value == expected_values[check.check_id] for check in checks
                     if check.check_id in expected_values and check.state != CheckState.NOT_EVALUATED), 'Physics measurements changed')
    expected_outcome = Outcome.REJECTED if failed else Outcome.ACCEPTED
    _require(Outcome(evaluation['outcome']) == expected_outcome and report.passed == (not failed), 'Outcome contradicts evidence')
    if not failed:
        _require(report.design_intent.state == physics.state == CheckState.PASSED, 'Geometry-only evidence cannot pass physics')
        artifacts = evaluation['artifacts']
        _require(set(artifacts) == {'model.py', 'model.step', 'preview.glb'} and report.artifacts is not None, 'Captured artifacts incomplete')
        for path, key in [('model.py', 'source_sha256'), ('model.step', 'step_sha256'), ('preview.glb', 'glb_sha256')]:
            _require(type(artifacts[path]) is bytes and hashlib.sha256(artifacts[path]).hexdigest() == report.artifacts[key], 'Delivered bytes changed')
        _require(report.artifacts['step_sha256'] == step.sha256 and len(artifacts['model.step']) == step.bytes
                 and artifacts['model.py'] == build_source(candidate.specification).encode(), 'Delivered revision differs from checked revision')
    else:
        _require(not evaluation['artifacts'], 'Rejected candidate cannot expose accepted geometry')
    return PolicyDecision(available=True, outcome=expected_outcome, checks=checks, reasons=tuple(failed))


def assess(candidate: CandidateSpec, evaluation: dict) -> PolicyDecision:
    """An invalid/missing evidence association is unavailable, never acceptance."""
    try:
        return _validate(candidate, evaluation)
    except _InvalidEvidence as error:
        return PolicyDecision(available=False, outcome=Outcome.NOT_EVALUATED, checks=(), reasons=(str(error),))
    except Exception:
        return PolicyDecision(available=False, outcome=Outcome.NOT_EVALUATED, checks=(),
                              reasons=('Required evidence missing, inconsistent or outside the pinned policy',))
