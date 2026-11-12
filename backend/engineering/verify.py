"""Independent STEP geometry oracle and bounded beam-model acceptance.

Native topology, digital design intent, idealized physics and standards are
separate evidence layers. This does not validate physical material provenance,
manufacturing variation, clamp compliance or regulatory conformity.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import math
from pathlib import Path

from .spec import CantileverSpec, specification_digest
from .beam_fea import solve_cantilever


class _Rejected(Exception):
    pass


def verify_step(step_path: str, spec: CantileverSpec) -> dict:
    from verification.solid_set import inspect_step
    report = {
        'scope': 'rectangular_cantilever_v1', 'passed': False,
        'engineering_certified': False, 'spec_sha256': specification_digest(spec),
        'design_intent': {'state': 'not_evaluated'},
        'physics': {'state': 'not_evaluated'},
        'standards': {'state': 'not_evaluated', 'reason': 'No engineering standard conformity implemented'},
        'checks': {}, 'failures': [], 'measurements': {},
        'limitations': [
            '1D Euler-Bernoulli beam FEA; not 3D solid FEA or experimental validation',
            'Homogeneous linear-elastic material; user-supplied allowable and ideal clamp',
            'Static transverse end force only; no shear deformation, notches, buckling, fatigue or contact',
            'Digital dimensions only; no manufacturing tolerance propagation or material certification',
        ],
    }

    def require(name, condition, message, evidence):
        # Values come from this independent reader/solver, never the adapter.
        # Each tuple is (name, value, unit, comparison, limit).
        report['checks'][name] = {
            'state': 'passed' if condition else 'failed',
            'measurements': [dict(name=n, value=float(v), unit=u) for n, v, u, _, _ in evidence],
            'limits': [dict(name=n, value=float(limit), unit=u, comparison=op)
                       for n, _, u, op, limit in evidence],
            'reason': None if condition else message,
        }
        if not condition:
            report['failures'].append(message)
            raise _Rejected

    try:
        structural = inspect_step(step_path)
        report['native_geometry'] = structural
        require('native_geometry', structural.get('passed') is True, 'Native geometry rejected',
                [('native_checks_passed', sum(v is True for v in structural['checks'].values()),
                  'count', 'eq', len(structural['checks']))])
        topology_tolerance = structural.get('max_topology_tolerance_mm')
        require('dimensional_evidence_budget', isinstance(topology_tolerance, (int, float))
                and math.isfinite(topology_tolerance) and 0 < topology_tolerance
                <= min(1e-6, spec.dimensional_tolerance_mm / 10),
                'Native numeric tolerance exceeds the dimensional evidence budget',
                [('topology_tolerance', topology_tolerance, 'mm', 'le', min(1e-6, spec.dimensional_tolerance_mm / 10))])
        report['numerical_policy'] = {'max_topology_tolerance_mm': topology_tolerance,
                                      'dimension_guard_mm': 2 * topology_tolerance,
                                      'datum_budget_mm': 1e-6,
                                      'is_manufacturing_uncertainty': False}
        from OCP.STEPControl import STEPControl_Reader
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        import cadquery as cq
        import OCP
        reader = STEPControl_Reader()
        from OCP.IFSelect import IFSelect_RetDone
        read_status = reader.ReadFile(step_path)
        require('independent_step_read', read_status == IFSelect_RetDone,
                'Independent STEP read failed', [('read_status', int(read_status), 'count', 'eq', int(IFSelect_RetDone))])
        roots = reader.NbRootsForTransfer()
        transferred = reader.TransferRoots() if roots > 0 else 0
        require('independent_step_transfer', roots > 0 and transferred == roots,
                'Independent STEP root transfer failed', [('transferred_roots', transferred, 'count', 'eq', roots),
                                                         ('roots', roots, 'count', 'ge', 1)])
        shape = cq.Shape.cast(reader.OneShape())
        solids = shape.Solids()
        require('single_rectangular_solid', len(solids) == 1 and len(shape.Faces()) == 6
                and len(shape.Edges()) == 12 and len(shape.Vertices()) == 8,
                'Supported model requires exactly one unmodified rectangular solid',
                [(n, v, 'count', 'eq', target) for n, v, target in
                 [('solids', len(solids), 1), ('faces', len(shape.Faces()), 6),
                  ('edges', len(shape.Edges()), 12), ('vertices', len(shape.Vertices()), 8)]])
        solid = solids[0]
        faces = solid.Faces()
        require('planar_faces', all(face.geomType() == 'PLANE' for face in faces),
                'Nonplanar geometry is outside the verified family',
                [('planar_faces', sum(f.geomType() == 'PLANE' for f in faces), 'count', 'eq', 6)])
        # Native optimal, tessellation-independent bounds; no tolerance inflation.
        from OCP.BRepBndLib import BRepBndLib
        from OCP.Bnd import Bnd_Box
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(solid.wrapped, box, False, False)
        bounds = box.Get()
        lengths = [bounds[i+3] - bounds[i] for i in range(3)]
        length, width, height = lengths
        require('finite_measurements', all(math.isfinite(v) for v in bounds)
                and all(v > 0 for v in lengths), 'Nonfinite or empty measured dimensions',
                [('finite_coordinates', sum(math.isfinite(v) for v in bounds), 'count', 'eq', 6),
                 ('positive_dimensions', sum(v > 0 for v in lengths), 'count', 'eq', 3)])
        require('size_requirements', all(abs(actual - requested) + 2*topology_tolerance <= spec.dimensional_tolerance_mm
                for actual, requested in zip(lengths, (spec.length_mm, spec.width_mm, spec.height_mm))),
                'Measured dimensions violate approved size tolerances',
                [(n + '_guarded_error', abs(a-b) + 2*topology_tolerance, 'mm', 'le', spec.dimensional_tolerance_mm)
                 for n, a, b in zip(('length', 'width', 'height'), lengths,
                                    (spec.length_mm, spec.width_mm, spec.height_mm))])
        require('datum_frame', all(abs(bounds[i]) <= 1e-6 for i in range(3)),
                'Model is outside the required positive XYZ datum frame',
                [(n + '_origin', bounds[i], 'mm', 'abs_le', 1e-6) for i, n in enumerate(('x', 'y', 'z'))])
        counts = [0, 0, 0]
        root = None
        normal_errors = []
        for face in faces:
            normal = face.normalAt().toTuple()
            axis = max(range(3), key=lambda i: abs(normal[i]))
            normal_errors.append(max(abs(abs(normal[axis])-1), *(abs(normal[i]) for i in range(3) if i != axis)))
            require('axis_aligned_faces', abs(abs(normal[axis])-1) < 1e-9
                    and all(abs(normal[i]) < 1e-9 for i in range(3) if i != axis),
                    'Face orientation violates the rectangular prism contract',
                    [('max_normal_error', max(normal_errors), 'ratio', 'le', 1e-9)])
            counts[axis] += 1
            if axis == 0 and abs(face.Center().x) < 1e-6:
                root = face
        require('rectangular_face_pairs', counts == [2, 2, 2] and root is not None,
                'Required rectangular face pairs and root section are unavailable',
                [(n + '_faces', counts[i], 'count', 'eq', 2) for i, n in enumerate(('x', 'y', 'z'))] +
                [('root_faces', int(root is not None), 'count', 'eq', 1)])
        volume = solid.Volume()
        area = solid.Area()
        require('complete_prism_metrics', abs(volume - length*width*height) <= max(1e-6, volume*1e-8)
                and abs(area - 2*(length*width + length*height + width*height)) <= max(1e-6, area*1e-8),
                'Measured shape is not the complete requested prism',
                [('volume_error', abs(volume-length*width*height), 'mm3', 'le', max(1e-6, volume*1e-8)),
                 ('area_error', abs(area-2*(length*width+length*height+width*height)), 'mm2', 'le', max(1e-6, area*1e-8))])
        properties = GProp_GProps()
        BRepGProp.SurfaceProperties_s(root.wrapped, properties)
        inertia = properties.MatrixOfInertia().Value(2, 2)
        require('measured_root_section', abs(properties.Mass()-width*height) < max(1e-6,width*height*1e-8)
                and math.isfinite(inertia) and inertia > 0
                and abs(inertia-width*height**3/12) <= max(1e-6,inertia*1e-8),
                'Native measured root section is inconsistent',
                [('root_area_error', abs(properties.Mass()-width*height), 'mm2', 'le', max(1e-6,width*height*1e-8)),
                 ('root_inertia_error', abs(inertia-width*height**3/12), 'mm4', 'le', max(1e-6,inertia*1e-8))])
        report['measurements'] = dict(length_mm=length, width_mm=width, height_mm=height,
                                      volume_mm3=volume, surface_area_mm2=area,
                                      section_area_mm2=properties.Mass(), section_iy_mm4=inertia)
        report['design_intent']['state'] = 'passed'
        solutions = [solve_cantilever(length_mm=length, width_mm=width, height_mm=height,
                     youngs_modulus_mpa=spec.youngs_modulus_mpa, tip_force_n=spec.tip_force_n,
                     second_moment_mm4=inertia, elements=n) for n in (4, 8, 16)]
        last, previous = solutions[-1], solutions[-2]
        convergence = max(abs(last[key]-previous[key]) / abs(last[key])
                          for key in ('tip_displacement_mm', 'root_bending_stress_mpa'))
        analytic_displacement = spec.tip_force_n * length**3 / (3*spec.youngs_modulus_mpa*inertia)
        analytic_stress = spec.tip_force_n * length * height / (2*inertia)
        benchmark_error = max(abs(last['tip_displacement_mm']/analytic_displacement-1),
                              abs(last['root_bending_stress_mpa']/analytic_stress-1))
        physics = dict(state='failed', method='euler_bernoulli_beam_fem_v1', units='mm_N_MPa',
                       mesh_history=solutions, solution=last, relative_mesh_change=convergence,
                       mesh_converged=convergence <= 1e-7,
                       analytic_benchmark_passed=benchmark_error <= 1e-7,
                       relative_analytic_error=benchmark_error,
                       max_tip_deflection_mm=spec.max_tip_deflection_mm,
                       allowable_stress_mpa=spec.allowable_stress_mpa,
                       material_reference=spec.material_reference,
                       boundary_conditions='X=0 ideal clamp; +Z end force at X=L')
        report['physics'] = physics
        require('mesh_convergence', physics['mesh_converged'], 'Beam mesh convergence failed',
                [('relative_mesh_change', convergence, 'ratio', 'le', 1e-7)])
        require('analytic_benchmark', physics['analytic_benchmark_passed'], 'Beam analytic benchmark failed',
                [('relative_analytic_error', benchmark_error, 'ratio', 'le', 1e-7)])
        require('equilibrium', all(s['relative_equilibrium_residual'] <= 1e-7 for s in solutions),
                'Finite-element equilibrium residual exceeded policy',
                [('max_equilibrium_residual', max(s['relative_equilibrium_residual'] for s in solutions), 'ratio', 'le', 1e-7)])
        require('small_deflection', last['tip_displacement_mm'] <= length * 0.02,
                'Result exceeds small-deflection capability',
                [('tip_displacement', last['tip_displacement_mm'], 'mm', 'le', length*0.02)])
        require('small_strain', last['root_bending_stress_mpa'] / spec.youngs_modulus_mpa <= 0.005,
                'Result exceeds the 0.5 percent small-strain application policy',
                [('strain', last['root_bending_stress_mpa']/spec.youngs_modulus_mpa, 'ratio', 'le', 0.005)])
        require('stress_allowable', last['root_bending_stress_mpa'] <= spec.allowable_stress_mpa,
                'Measured-geometry beam stress exceeds user-approved allowable',
                [('root_stress', last['root_bending_stress_mpa'], 'MPa', 'le', spec.allowable_stress_mpa)])
        require('displacement_requirement', last['tip_displacement_mm'] <= spec.max_tip_deflection_mm,
                'Measured-geometry tip deflection exceeds the approved requirement',
                [('tip_displacement', last['tip_displacement_mm'], 'mm', 'le', spec.max_tip_deflection_mm)])
        physics['state'] = 'passed'
        report['passed'] = True
    except _Rejected:
        if report['design_intent']['state'] == 'not_evaluated':
            report['design_intent']['state'] = 'failed'
    except Exception:
        report['failures'].append('Required native or physics evidence unavailable')
        report['checks']['required_evidence'] = {'state': 'unavailable'}
    finally:
        # Rejected revisions need the same exact identity/runtime association.
        report['step_sha256'] = hashlib.sha256(Path(step_path).read_bytes()).hexdigest()
        report['step_bytes'] = Path(step_path).stat().st_size
        import platform
        report['versions'] = {'python': platform.python_version()}
        for key, package in (('cadquery', 'cadquery'), ('ocp', 'cadquery-ocp'), ('numpy', 'numpy')):
            try:
                report['versions'][key] = version(package)
            except PackageNotFoundError:
                report['versions'][key] = 'unavailable'
        sources = [Path(__file__), Path(__file__).with_name('beam_fea.py'),
                   Path(__file__).with_name('spec.py'),
                   Path(__file__).parents[1] / 'verification' / 'solid_set.py']
        report['implementation_sha256'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                           for path in sources}
    return report

