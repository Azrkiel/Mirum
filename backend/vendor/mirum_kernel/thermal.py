"""Bounded steady conduction, registered field transfer and axial clearance in SI."""

from bisect import bisect_left
from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from math import fsum, isfinite
from pathlib import Path
from platform import python_implementation, python_version

from .structural import digest


SCHEMA = 'mirum_thermal_bar_v1'
FRAME = 'bar_lower_corner_positive_x'
FIELD_REF = 'conduction.temperature'
FREE = 'cold_fixed_hot_free'
SOURCE_MESHES = (4, 8, 16)
TARGET_MESHES = (5, 9, 17)
RISE_LIMIT = EXPANSION_LIMIT = 0.02
CHANGE_LIMIT = IMBALANCE_LIMIT = 0.01
CLEARANCE_LIMIT_M = 2e-8
TRANSFER_LIMIT_K = 1e-9
BOUNDARIES = {'sink_region': 'bar.cold_end', 'heat_region': 'bar.hot_end', 'insulation_region': 'bar.sides'}


class Diagnostic(ValueError):
    def __init__(self, code, detail, field=None):
        self.code = code
        self.detail = detail
        self.field = field
        super().__init__(f'{code}: {detail}')


@dataclass(frozen=True)
class Case:
    length_m: float
    width_m: float
    depth_m: float
    conductivity_w_m_k: float
    power_w: float
    sink_k: float
    sink_region: str
    heat_region: str
    insulation_region: str
    alpha_per_k: float
    reference_k: float
    restraint: str
    cold_gap_m: float
    clearance_allowance_m: float
    field_reference: str

    def __post_init__(self):
        bounds = {
            'length_m': (0.001, 1), 'width_m': (0.001, 1), 'depth_m': (0.001, 1),
            'conductivity_w_m_k': (1, 10000), 'power_w': (0.001, 100),
            'sink_k': (0, 2000), 'reference_k': (0, 2000), 'alpha_per_k': (1e-8, 1e-3),
            'cold_gap_m': (0, 0.01), 'clearance_allowance_m': (0, 0.01),
        }
        for field, (lo, hi) in bounds.items():
            value = getattr(self, field)
            if type(value) not in (float, int) or not isfinite(value) or not lo <= value <= hi:
                raise Diagnostic('case_out_of_bounds', f'{field} must be in [{lo}, {hi}] in SI', field)


def validate_boundary(name, reference):
    expected = BOUNDARIES[name]
    if reference == 'bar.ends':
        raise Diagnostic('boundary_ambiguous', 'bar.ends selects the cold and hot end', name)
    if reference != expected:
        raise Diagnostic('boundary_missing', f'{reference!r} does not resolve to {expected}', name)


def validate_field_reference(reference):
    if reference == 'conduction.fields':
        raise Diagnostic('field_ambiguous', 'reference selects multiple temperature fields', 'field_reference')
    if reference != FIELD_REF:
        raise Diagnostic('field_missing', f'{reference!r} does not resolve to {FIELD_REF}', 'field_reference')


@cache
def solver_fingerprint():
    return dict(engine='mirum_axial_fvm_and_free_strain_v1',
                implementation_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                digest_implementation_sha256=sha256(Path(__file__).with_name('structural.py').read_bytes()).hexdigest(),
                runtime=python_implementation(), python=python_version())


def geometry(case):
    return dict(shape='rectangular_prism', frame=FRAME, dimensions_m=[float(case.length_m), float(case.width_m),
                                                                      float(case.depth_m)])


def mesh(case, elements):
    x = [case.length_m * i / elements for i in range(elements)] + [float(case.length_m)]
    record = dict(geometry_sha256=digest(geometry(case)), frame=FRAME, elements=elements,
                  coordinates_m=x, topology='axial_two_node_segments')
    return dict(record, sha256=digest(record))


def conduction_inputs(case):
    for name in BOUNDARIES:
        validate_boundary(name, getattr(case, name))
    bar = geometry(case)
    area = case.width_m * case.depth_m
    material = dict(model='constant_isotropic_conductivity', conductivity_w_m_k=float(case.conductivity_w_m_k))
    return dict(
        schema=SCHEMA, geometry=bar, geometry_sha256=digest(bar), material=material,
        material_sha256=digest(material), solver=solver_fingerprint(),
        sink=dict(region=case.sink_region, x_m=0.0, outward_normal=[-1, 0, 0], absolute_k=float(case.sink_k)),
        heat=dict(region=case.heat_region, x_m=float(case.length_m), outward_normal=[1, 0, 0],
                  inward_w=float(case.power_w), inward_w_m2=case.power_w / area, area_m2=area),
        insulation=dict(region=case.insulation_region, faces=['-Y', '+Y', '-Z', '+Z'], outward_w_m2=0.0),
        meshes=[mesh(case, n) for n in SOURCE_MESHES],
        limits=dict(finest_rise_relative=RISE_LIMIT, finest_pair_relative_lt=CHANGE_LIMIT,
                    heat_imbalance_relative=IMBALANCE_LIMIT))


def _field(grid, temperatures, conduction_sha256):
    record = dict(schema=SCHEMA, kind='absolute_temperature', unit='K', coordinate_unit='m',
                  frame=FRAME, geometry_sha256=grid['geometry_sha256'], mesh_sha256=grid['sha256'],
                  producer_sha256=conduction_sha256, x_m=grid['coordinates_m'], temperature_k=temperatures)
    return dict(record, sha256=digest(record))


def _conduction_sample(case, grid, dependency):
    # Assemble flux conservation with a prescribed cold node and a power at the last node.
    # Eliminate the symmetric tridiagonal system in temperature rise to avoid Kelvin offsets.
    x = grid['coordinates_m']
    ka = case.conductivity_w_m_k * case.width_m * case.depth_m
    conductances = [ka / (right - left) for left, right in zip(x, x[1:])]
    diagonal = [left + right for left, right in zip(conductances, conductances[1:])] + conductances[-1:]
    rhs = [0.0] * (len(conductances) - 1) + [case.power_w]
    for i, conductance in enumerate(conductances[1:], 1):
        ratio = conductance / diagonal[i - 1]
        diagonal[i] -= ratio * conductance
        rhs[i] += ratio * rhs[i - 1]
    rises = [0.0] * len(x)
    rises[-1] = rhs[-1] / diagonal[-1]
    for i in range(len(diagonal) - 2, -1, -1):
        rises[i + 1] = (rhs[i] + conductances[i + 1] * rises[i + 2]) / diagonal[i]
    flux = [g * (right - left) for g, left, right in zip(conductances, rises, rises[1:])]
    residuals = [left - right for left, right in zip(flux, flux[1:])] + [flux[-1] - case.power_w]
    temperatures = [case.sink_k + rise for rise in rises]
    balance = case.power_w - flux[0]
    ref = case.power_w * case.length_m / ka
    return dict(elements=grid['elements'], mesh_sha256=grid['sha256'], hot_k=temperatures[-1],
                rise_k=rises[-1], sink_out_w=flux[0], heat_balance_w=balance,
                heat_imbalance_relative=abs(balance) / case.power_w, nodal_residual_w=residuals,
                max_abs_residual_w=max(map(abs, residuals)), range_k=[min(temperatures), max(temperatures)],
                analytical_rise_k=ref, analytical_hot_k=case.sink_k + ref, analytical_sink_out_w=case.power_w,
                rise_error_relative=abs(rises[-1] - ref) / ref, field=_field(grid, temperatures, dependency))


def _change(previous, finest, scale):
    return abs(previous - finest) / (abs(finest) or abs(scale) or 1.0)


def solve_conduction(case, inputs):
    dependency = digest(inputs)
    samples = [_conduction_sample(case, grid, dependency) for grid in inputs['meshes']]
    finest = samples[-1]
    change = _change(samples[-2]['rise_k'], finest['rise_k'], finest['analytical_rise_k'])
    passed = (finest['rise_error_relative'] <= RISE_LIMIT and change < CHANGE_LIMIT
              and max(s['heat_imbalance_relative'] for s in samples) <= IMBALANCE_LIMIT)
    return dict(state='passed' if passed else 'failed', samples=samples, finest_pair_relative_change=change)


def _resolve_field(fields, case, producer, source):
    if not fields:
        raise Diagnostic('field_missing', 'no temperature field available')
    if len(fields) != 1:
        raise Diagnostic('field_ambiguous', 'expected exactly one field for the source mesh')
    field = fields[0]
    if not isinstance(field, dict):
        raise Diagnostic('field_incompatible', 'temperature field must be a record')
    if field.get('producer_sha256') != producer:
        raise Diagnostic('field_stale', 'producer dependency does not match the current conduction case')
    metadata = dict(schema=SCHEMA, kind='absolute_temperature', unit='K', coordinate_unit='m',
                    frame=FRAME, geometry_sha256=digest(geometry(case)), mesh_sha256=source['sha256'])
    if any(field.get(key) != value for key, value in metadata.items()):
        raise Diagnostic('field_incompatible', 'schema, units, frame, geometry or mesh differs')
    x, temperatures = field.get('x_m'), field.get('temperature_k')
    if x != source['coordinates_m'] or not isinstance(temperatures, list) or len(temperatures) != len(x):
        raise Diagnostic('field_incompatible', 'samples must match the source coordinates and node count')
    if any(type(t) not in (int, float) or not isfinite(t) or t < 0 for t in temperatures):
        raise Diagnostic('field_incompatible', 'samples must be finite absolute temperatures in Kelvin')
    if digest({key: value for key, value in field.items() if key != 'sha256'}) != field.get('sha256'):
        raise Diagnostic('field_stale', 'field digest does not match the supplied samples')
    return field


def transfer(case, producer, source_elements, target_elements, fields, registration=None):
    if source_elements not in SOURCE_MESHES or target_elements not in TARGET_MESHES:
        raise Diagnostic('field_incompatible', 'source or target mesh is outside the bounded benchmark')
    source, target = mesh(case, source_elements), mesh(case, target_elements)
    field = _resolve_field(fields, case, producer, source)
    if registration is None:
        registration = dict(frame=FRAME, target_to_source_x_offset_m=0.0)
    offset = registration.get('target_to_source_x_offset_m') if isinstance(registration, dict) else None
    if (not isinstance(registration, dict) or registration.get('frame') != FRAME
            or type(offset) not in (int, float) or not isfinite(offset)):
        raise Diagnostic('field_incompatible', 'registration requires the common axial frame and a finite offset')
    x, temperature = field['x_m'], field['temperature_k']
    mapped = [node + offset for node in target['coordinates_m']]
    if mapped[0] < x[0] or mapped[-1] > x[-1]:
        raise Diagnostic('field_out_of_domain', 'target coordinates exceed the source interval')
    values = []
    for node in mapped:
        right = bisect_left(x, node)
        if x[right] == node:
            values.append(temperature[right])
        else:
            left = right - 1
            fraction = (node - x[left]) / (x[right] - x[left])
            values.append(temperature[left] + fraction * (temperature[right] - temperature[left]))
    area = case.width_m * case.depth_m
    expected = [case.sink_k + case.power_w * node / (case.conductivity_w_m_k * area) for node in mapped]
    return dict(source_mesh=source, target_mesh=target, input_field_sha256=field['sha256'],
                registration=registration, interpolation='piecewise_linear_no_extrapolation',
                source_nodes=len(x), covered_target_nodes=len(values), coverage_fraction=1.0,
                target_temperature_k=values,
                max_transfer_error_k=max(abs(value - ref) for value, ref in zip(values, expected)))


def _integral(x, temperatures, reference):
    rises = [temperature - reference for temperature in temperatures]
    return fsum((b - a) * (left + right) / 2 for a, b, left, right in zip(x, x[1:], rises, rises[1:]))


def expansion_inputs(case, conduction):
    material = dict(alpha_per_k=float(case.alpha_per_k), stress_free_temperature_k=float(case.reference_k))
    return dict(
        schema=SCHEMA, solver=solver_fingerprint(), geometry_sha256=digest(geometry(case)),
        material=material, material_sha256=digest(material), restraint=case.restraint,
        boundary=dict(cold=dict(region='bar.cold_end', ux_m=0.0), hot=dict(region='bar.hot_end', axial='free'),
                      applied_mechanical_load_n=0.0, displacement_direction='+X'),
        field_reference=case.field_reference, producer_sha256=conduction['dependency_sha256'],
        fields=[row['field']['sha256'] for row in conduction['result']['samples']],
        targets=[mesh(case, n) for n in TARGET_MESHES],
        integration='composite_trapezoid_of_target_temperature_minus_reference',
        limits=dict(finest_expansion_relative=EXPANSION_LIMIT, finest_pair_relative_lt=CHANGE_LIMIT,
                    transfer_absolute_k=TRANSFER_LIMIT_K, uniform_delta_k=1.0))


def solve_expansion(case, inputs, conduction):
    validate_field_reference(case.field_reference)
    if case.restraint != FREE:
        raise Diagnostic('unsupported_restraint', 'requires the cold end fixed and the hot end axially free',
                         'restraint')
    area = case.width_m * case.depth_m
    hot_rise = case.power_w * case.length_m / (case.conductivity_w_m_k * area)
    ref = case.alpha_per_k * case.length_m * (case.sink_k - case.reference_k + hot_rise / 2)
    uniform_ref = case.alpha_per_k * case.length_m
    samples = []
    for grid, row in zip(inputs['targets'], conduction['result']['samples'], strict=True):
        delivery = transfer(case, inputs['producer_sha256'], row['elements'], grid['elements'], [row['field']])
        x = grid['coordinates_m']
        integral = _integral(x, delivery['target_temperature_k'], case.reference_k)
        uniform = case.alpha_per_k * _integral(x, [case.reference_k + 1] * len(x), case.reference_k)
        expansion = case.alpha_per_k * integral
        scale = abs(ref) or abs(case.alpha_per_k * case.length_m * hot_rise)
        samples.append(dict(delivery, integration=inputs['integration'], integral_k_m=integral,
                            mean_rise_above_reference_k=integral / case.length_m, expansion_m=expansion,
                            expansion_error_relative=abs(expansion - ref) / scale,
                            uniform_field=_field(grid, [case.reference_k + 1] * len(x), 'manufactured_uniform_plus_one_k'),
                            uniform_expansion_m=uniform, uniform_error_relative=abs(uniform - uniform_ref) / uniform_ref))
    change = _change(samples[-2]['expansion_m'], samples[-1]['expansion_m'], uniform_ref)
    passed = (samples[-1]['expansion_error_relative'] <= EXPANSION_LIMIT and change < CHANGE_LIMIT
              and max(s['uniform_error_relative'] for s in samples) <= EXPANSION_LIMIT
              and max(s['max_transfer_error_k'] for s in samples) <= TRANSFER_LIMIT_K)
    return dict(state='passed' if passed else 'failed', samples=samples, analytical_expansion_m=ref,
                analytical_uniform_expansion_m=uniform_ref, finest_pair_relative_change=change)


def _clearance(case, expansion):
    result = expansion['result']
    growth = result['samples'][-1]['expansion_m']
    gap = case.cold_gap_m - growth
    reference = case.cold_gap_m - result['analytical_expansion_m']
    margin = gap - case.clearance_allowance_m
    error = abs(gap - reference)
    return dict(state='passed' if margin >= 0 and error <= CLEARANCE_LIMIT_M else 'failed',
                expansion_m=growth, gap_m=gap, analytical_gap_m=reference, absolute_error_m=error, margin_m=margin)


def run(case, *, previous=None):
    """Run stage dependencies in order. Reuse only matching, intact passing results."""
    previous = {} if previous is None else previous
    stages, calls = {}, {}

    def execute(name, inputs, solve, *args):
        dependency = digest(inputs)
        prior = previous.get(name, {})
        result = prior.get('result', {})
        if prior.get('dependency_sha256') != dependency or prior.get('inputs') != inputs:
            prior_state = 'stale' if prior else 'absent'
        elif result.get('state') == 'passed' and prior.get('result_sha256') == digest(result):
            prior_state = 'passed'
        else:
            prior_state = 'failed'
        action = 'reused' if prior_state == 'passed' else 'computed'
        stage = stages[name] = dict(inputs=inputs, dependency_sha256=dependency, prior_state=prior_state, action=action)
        if action == 'computed':
            calls[name] = 1
            result = solve(*args)
        stage.update(result=result, result_sha256=digest(result))
        return stage

    try:
        inputs = conduction_inputs(case)
        conduction = execute('conduction', inputs, solve_conduction, case, inputs)
        if conduction['result']['state'] != 'passed':
            return dict(schema=SCHEMA, stages=stages, calls=calls, verdict='failed')
        inputs = expansion_inputs(case, conduction)
        expansion = execute('expansion', inputs, solve_expansion, case, inputs, conduction)
        if expansion['result']['state'] != 'passed':
            return dict(schema=SCHEMA, stages=stages, calls=calls, verdict='failed')
        inputs = dict(expansion_sha256=expansion['result_sha256'], expansion_dependency_sha256=expansion['dependency_sha256'],
                      plane=dict(stationary=True, frame=FRAME, normal=[-1, 0, 0],
                                 x_m=case.length_m + case.cold_gap_m),
                      definition='plane_x_minus_displaced_hot_end_x',
                      cold_gap_m=float(case.cold_gap_m), allowance_m=float(case.clearance_allowance_m),
                      limit_absolute_error_m=CLEARANCE_LIMIT_M)
        clearance = execute('clearance', inputs, _clearance, case, expansion)
        return dict(schema=SCHEMA, stages=stages, calls=calls, verdict=clearance['result']['state'])
    except Diagnostic as exc:
        return dict(schema=SCHEMA, stages=stages, calls=calls, verdict=exc.code,
                    diagnostic=dict(code=exc.code, detail=exc.detail, field=exc.field))
