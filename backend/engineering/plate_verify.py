"""Independent STEP oracle and checked acceptance for the rectangular_plate contract.

Geometry is measured from the STEP file with OCP, never from the spec. The physics
gates (`_run_physics_gates`) are separable from the OCP measurement. A rejection
returns a report; missing evidence raises EngineeringUnavailable.
cadquery/OCP are imported only inside functions.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import math
from pathlib import Path
import platform

from .plate_spec import (
    CHECK_IDS, FAMILY, FAMILY_VERSION, GUARD_IDS, PLATE_NUMERICAL_V1, PlateBrief, PlateSpec,
    evaluate_static_guards, specification_digest,
)
from .service import EngineeringUnavailable

SCOPE = 'rectangular_plate_v1'
_AXIS_TOL = 1e-9
_LIMITATIONS = [
    'Kirchhoff thin-plate theory (Hermite bicubic finite elements); transverse shear is covered only by '
    'the stated deflection allowance',
    'Simply supported: edges and corners held against lift; the corner reaction forces are reported, not '
    'designed away',
    'Clamped: ideal rigid clamp on all four edges',
    'Small deflection, linear elastic, uniform pressure, uniform edge condition only',
    'No safety factor applied: the allowable stress and deflection are user-supplied limits',
    'Digital dimensions only; no physical validation, material certification or manufacturing variation',
    'No engineering standard conformity evaluated',
]


def _plain(value):
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, 'item') and callable(value.item) and not isinstance(value, (str, bytes)):
        try:
            return _plain(value.item())
        except (ValueError, TypeError):
            pass
    return value


def _finite(name, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EngineeringUnavailable(f'{name} is missing or non-finite')
    return float(value)


# --------------------------------------------------------------- report plumbing

def _new_report(spec: PlateSpec) -> dict:
    def blank():
        return {'state': 'not_evaluated', 'quantity': None, 'limit': None, 'comparison': None, 'reason': None}

    return {
        'scope': SCOPE, 'family': FAMILY, 'family_version': FAMILY_VERSION,
        'passed': False, 'outcome': 'rejected', 'rejection': None,
        'engineering_certified': False,
        'standards': {'state': 'not_evaluated', 'reason': 'No engineering standard conformity implemented'},
        'design_intent': {'state': 'not_evaluated'},
        'physics': {'state': 'not_evaluated'},
        'guards': {gid: blank() for gid in GUARD_IDS},
        'checks': {cid: blank() for cid in CHECK_IDS},
        'measurements': {}, 'mesh_history': [], 'references': {}, 'vm_max_location': None,
        'corner_forces': None,
        'numerical_policy': _plain(dict(PLATE_NUMERICAL_V1)),
        'limitations': list(_LIMITATIONS),
        'spec_sha256': specification_digest(spec),
    }


def _gate(report, kind, gate_id, quantity, limit, comparison, passed, reason_if_failed, **extra) -> bool:
    """Record a gate; set report['rejection'] on the first failure. Returns passed."""
    entry = report[kind][gate_id]
    entry.update(
        state='passed' if passed else 'failed',
        quantity=None if quantity is None else float(quantity),
        limit=_plain(limit), comparison=comparison,
        reason=None if passed else reason_if_failed,
    )
    entry.update(extra)
    if not passed and report['rejection'] is None:
        report['rejection'] = {'id': gate_id, 'reason': reason_if_failed}
    return bool(passed)


def _relative_change(last, previous) -> float:
    if last == 0:
        raise EngineeringUnavailable('Finest-level result is zero; relative change undefined')
    return abs(last - previous) / abs(last)


# ----------------------------------------------------------------- geometry

def measure_step(step_path) -> dict:
    """Independent OCP measurement. Unreadable STEP -> {'readable': False, ...}."""
    try:
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape
        from OCP.TopoDS import TopoDS
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Plane
        from OCP.BRepBndLib import BRepBndLib
        from OCP.Bnd import Bnd_Box
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
    except Exception as exc:
        raise EngineeringUnavailable(f'cadquery/OCP unavailable: {type(exc).__name__}') from exc
    try:
        reader = STEPControl_Reader()
        if reader.ReadFile(str(step_path)) != IFSelect_RetDone:
            return {'readable': False, 'reason': 'STEP file could not be read'}
        roots = reader.NbRootsForTransfer()
        transferred = reader.TransferRoots() if roots > 0 else 0
        if roots <= 0 or transferred != roots:
            return {'readable': False, 'reason': 'STEP roots could not be transferred'}
        shape = reader.OneShape()
        solids = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_SOLID, solids)
        faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
        measured = {'readable': True, 'solids': int(solids.Extent()), 'faces': int(faces.Extent()),
                    'planar_faces': 0, 'axis_aligned_faces': 0}
        for i in range(1, faces.Extent() + 1):
            face = TopoDS.Face_s(faces.FindKey(i))
            surface = BRepAdaptor_Surface(face)
            if surface.GetType() != GeomAbs_Plane:
                continue
            measured['planar_faces'] += 1
            d = surface.Plane().Axis().Direction()
            comps = sorted((abs(d.X()), abs(d.Y()), abs(d.Z())), reverse=True)
            if abs(comps[0] - 1.0) < _AXIS_TOL and comps[1] < _AXIS_TOL and comps[2] < _AXIS_TOL:
                measured['axis_aligned_faces'] += 1
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, box, False, False)
        if box.IsVoid():
            return {'readable': False, 'reason': 'STEP shape has no bounding box'}
        x0, y0, z0, x1, y1, z1 = box.Get()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        measured.update(
            bbox_min=[x0, y0, z0], length_a_mm=x1 - x0, width_b_mm=y1 - y0, thickness_mm=z1 - z0,
            volume_mm3=props.Mass(),
        )
        return measured
    except Exception as exc:
        return {'readable': False, 'reason': f'STEP measurement failed: {type(exc).__name__}'}


def _geometry_gate(report, spec: PlateSpec, m: dict) -> bool:
    limit = {'volume_relative': PLATE_NUMERICAL_V1['geometry_volume_relative'],
             'dimensional_tolerance_mm': spec.dimensional_tolerance_mm}
    if not m.get('readable'):
        return _gate(report, 'checks', 'geometry', None, limit, 'le', False,
                     m.get('reason', 'STEP unreadable'), measurements=_plain(m))
    tol = spec.dimensional_tolerance_mm
    nominal = (spec.length_a_mm, spec.width_b_mm, spec.thickness_mm)
    actual = (m['length_a_mm'], m['width_b_mm'], m['thickness_mm'])
    values = [*actual, *m['bbox_min'], m['volume_mm3']]
    finite = all(math.isfinite(v) for v in values)
    volume_nominal = nominal[0] * nominal[1] * nominal[2]
    volume_error = abs(m['volume_mm3'] - volume_nominal) / volume_nominal if finite else None
    m = dict(m, volume_relative_error=volume_error)
    reason = None
    if m['solids'] != 1:
        reason = f"Expected exactly one solid, found {m['solids']}"
    elif m['faces'] != 6:
        reason = f"Expected exactly six faces, found {m['faces']}"
    elif m['planar_faces'] != 6:
        reason = 'All faces must be planar'
    elif m['axis_aligned_faces'] != 6:
        reason = 'All faces must be axis-aligned'
    elif not finite:
        reason = 'Non-finite geometry measurements'
    elif any(abs(a - n) > tol for a, n in zip(actual, nominal)):
        reason = 'Bounding-box lengths differ from the spec beyond the dimensional tolerance'
    elif any(abs(c) > tol for c in m['bbox_min']):
        reason = 'Bounding-box minimum corner is not at the origin within tolerance'
    elif volume_error > PLATE_NUMERICAL_V1['geometry_volume_relative']:
        reason = 'Volume differs from a*b*h beyond the relative limit'
    return _gate(report, 'checks', 'geometry', volume_error, limit, 'le',
                 reason is None, reason, measurements=_plain(m))


# ------------------------------------------------------------------ physics

def _default_reference(a, b, h, q, E, nu, edge_condition) -> dict:
    from . import plate_reference
    if edge_condition == 'simply_supported':
        r = plate_reference.navier_ss(a, b, h, q, E, nu)
        return {'w_max': r['w_max'], 'vm_max': r['vm_max'], 'method': 'navier_ss_r1'}
    r = plate_reference.fd_plate(a, b, h, q, E, nu, edge_condition)['extrapolated']
    return {'w_max': r['w_centre'], 'vm_max': r['vm_max'], 'method': 'fd_plate_r2_extrapolated'}


def _run_physics_gates(report, spec: PlateSpec, fe_solver=None, reference=None) -> None:
    """FE levels + small_deflection ... stress_limit. Mutates report; raises Unavailable on bad evidence."""
    p = PLATE_NUMERICAL_V1
    a, b, h = spec.length_a_mm, spec.width_b_mm, spec.thickness_mm
    if fe_solver is None:
        from .plate_fea import solve_levels as fe_solver
    try:
        levels = fe_solver(
            length_a_mm=a, width_b_mm=b, thickness_mm=h, pressure_mpa=spec.pressure_mpa,
            youngs_modulus_mpa=spec.youngs_modulus_mpa, poissons_ratio=spec.poissons_ratio,
            edge_condition=spec.edge_condition)
    except Exception as exc:
        raise EngineeringUnavailable(f'Finite-element solve failed: {type(exc).__name__}: {exc}') from exc
    if not isinstance(levels, (list, tuple)) or len(levels) < 2 or not all(isinstance(v, dict) for v in levels):
        raise EngineeringUnavailable('Finite-element solver returned fewer than two mesh levels')
    for lv in levels:
        for key in ('w_max', 'vm_max_mpa', 'equilibrium_relative_error'):
            _finite(f'FE {key}', lv.get(key))
    levels = [_plain(lv) for lv in levels]
    report['mesh_history'] = levels
    finest, previous = levels[-1], levels[-2]
    report['vm_max_location'] = finest.get('vm_max_location')
    if spec.edge_condition == 'simply_supported':
        report['corner_forces'] = {'finest_n': finest.get('corner_force_n'),
                                   'per_level_n': [lv.get('corner_force_n') for lv in levels],
                                   'twist_force_n': finest.get('corner_twist_force_n')}
    w_fe, vm_fe = float(finest['w_max']), float(finest['vm_max_mpa'])
    report['physics']['state'] = 'failed'

    ref_fn = reference if reference is not None else _default_reference
    try:
        ref = ref_fn(a, b, h, spec.pressure_mpa, spec.youngs_modulus_mpa, spec.poissons_ratio,
                     spec.edge_condition)
        ref_w, ref_vm = _finite('reference w_max', ref['w_max']), _finite('reference vm_max', ref['vm_max'])
    except EngineeringUnavailable:
        raise
    except Exception as exc:
        raise EngineeringUnavailable(f'Reference solution failed: {type(exc).__name__}: {exc}') from exc
    if ref_w == 0 or ref_vm == 0:
        raise EngineeringUnavailable('Reference value is zero; benchmark error undefined')
    report['references'] = {'method': str(ref.get('method', 'unspecified')), 'w_max': ref_w, 'vm_max': ref_vm}

    # Gates run in contract order; each runs only while no earlier gate failed.
    ratio = w_fe / h
    _gate(report, 'guards', 'small_deflection', ratio, p['small_deflection_max_ratio'], 'le',
          ratio <= p['small_deflection_max_ratio'], 'Deflection exceeds the small-deflection limit w/h')
    if report['rejection'] is None:
        eq = max(float(lv['equilibrium_relative_error']) for lv in levels)
        _gate(report, 'checks', 'equilibrium', eq, p['equilibrium'], 'le', eq <= p['equilibrium'],
              'Finite-element equilibrium residual exceeds the limit')
    if report['rejection'] is None:
        d = _relative_change(w_fe, float(previous['w_max']))
        _gate(report, 'checks', 'mesh_convergence_deflection', d, p['mesh_convergence_deflection'], 'le',
              d <= p['mesh_convergence_deflection'], 'Deflection mesh convergence failed')
    if report['rejection'] is None:
        s = _relative_change(vm_fe, float(previous['vm_max_mpa']))
        _gate(report, 'checks', 'mesh_convergence_stress', s, p['mesh_convergence_stress'], 'le',
              s <= p['mesh_convergence_stress'], 'Stress mesh convergence failed')
    if report['rejection'] is None:
        _gate(report, 'checks', 'symmetry', None, None, None, True, None)
        report['checks']['symmetry']['reason'] = 'quarter model by construction'
    if report['rejection'] is None:
        e = abs(w_fe / ref_w - 1)
        _gate(report, 'checks', 'benchmark_deflection', e, p['benchmark_deflection'], 'le',
              e <= p['benchmark_deflection'], 'Deflection differs from the analytic reference')
    if report['rejection'] is None:
        e = abs(vm_fe / ref_vm - 1)
        _gate(report, 'checks', 'benchmark_stress', e, p['benchmark_stress'], 'le',
              e <= p['benchmark_stress'], 'Stress differs from the analytic reference')
    if report['rejection'] is None:
        w_check = w_fe * (1 + p['shear_allowance_coefficient'] * (h / b) ** 2)
        _gate(report, 'checks', 'deflection_limit', w_check, spec.max_deflection_mm, 'le',
              w_check <= spec.max_deflection_mm, 'Deflection (with shear allowance) exceeds the limit')
    if report['rejection'] is None:
        _gate(report, 'checks', 'stress_limit', vm_fe, spec.allowable_stress_mpa, 'le',
              vm_fe <= spec.allowable_stress_mpa, 'Von Mises stress exceeds the allowable')
    if report['rejection'] is None:
        report['physics']['state'] = 'passed'


# --------------------------------------------------------------- entry points

def _versions_and_hashes(report, step_path) -> None:
    report['step_sha256'] = hashlib.sha256(Path(step_path).read_bytes()).hexdigest()
    report['versions'] = {'python': platform.python_version()}
    for key, package in (('cadquery', 'cadquery'), ('ocp', 'cadquery-ocp'), ('numpy', 'numpy')):
        try:
            report['versions'][key] = version(package)
        except PackageNotFoundError:
            report['versions'][key] = 'unavailable'
    here = Path(__file__).parent
    names = ('plate_spec', 'plate_fea', 'plate_reference', 'plate_geometry', 'plate_verify')
    report['implementation_sha256'] = {
        f'{n}.py': hashlib.sha256((here / f'{n}.py').read_bytes()).hexdigest() for n in names}


def verify_plate_step(step_path, spec: PlateSpec, *, fe_solver=None, reference=None) -> dict:
    path = Path(step_path)
    if not path.is_file():
        raise EngineeringUnavailable('STEP file does not exist')
    report = _new_report(spec)
    # Static guards (small_deflection is evaluated after the FE solve).
    for result in evaluate_static_guards(spec):
        _gate(report, 'guards', result['guard'], result['quantity'], result['limit'],
              'ge' if result['guard'] == 'thin_ratio' else 'within',
              result['passed'], f"Static guard {result['guard']} failed")
        if report['rejection'] is not None:
            break
    if report['rejection'] is None:
        measured = measure_step(path)
        if _geometry_gate(report, spec, measured):
            report['design_intent']['state'] = 'passed'
            report['measurements'] = {k: measured[k] for k in
                                      ('length_a_mm', 'width_b_mm', 'thickness_mm', 'volume_mm3')}
        else:
            report['design_intent']['state'] = 'failed'
    if report['rejection'] is None:
        _run_physics_gates(report, spec, fe_solver, reference)
    report['passed'] = report['rejection'] is None
    report['outcome'] = 'accepted' if report['passed'] else 'rejected'
    _versions_and_hashes(report, path)
    return _plain(report)


def select_smallest_passing(brief: PlateBrief, *, workdir, step_factory=None, fe_solver=None,
                            reference=None) -> dict:
    if step_factory is None:
        from .plate_geometry import export_step as step_factory
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    attempts = []
    selected = None
    for index, spec in enumerate(brief.candidates()):
        step_path = work / f'plate_{index}.step'
        step_factory(spec, step_path)
        report = verify_plate_step(step_path, spec, fe_solver=fe_solver, reference=reference)
        attempts.append(report)
        if report['passed']:
            selected = float(spec.thickness_mm)
            break
    return {'outcome': 'accepted' if selected is not None else 'rejected',
            'selected_thickness_mm': selected, 'attempts': attempts}
