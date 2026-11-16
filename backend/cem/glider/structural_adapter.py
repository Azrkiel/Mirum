"""Task B consumers of A's shared contracts; no native qualification by labels.

Strip forces stay point loads. CG moments are translated once to the installed
spar, retaining section couples. No smoothing changes first moments, no drag or
tail force disappears silently, and the load factor is applied exactly once.
"""
import hashlib
from pathlib import Path

import numpy as np

from cem.aero.contracts import (Binding, InstalledGeometry, StructuralLoadCase,
                                InstalledMaterial, InstalledStructure, Support)
from cem.core import canonical_json, digest
from . import mounted_wing


def consume_loads(geometry, case, *, expected_binding, root_axis_m, half_span_m, surfaces=('wing',)):
    return _consume_loads(geometry, case, expected_binding=expected_binding,
        root_axis_m=root_axis_m, half_span_m=half_span_m, surfaces=surfaces,
        reference_span_m=geometry.reference.span_m, allowed_roles=('wing',))


def consume_lifting_surface(geometry, case, *, expected_binding, root_axis_m, surface):
    """Exact resultants for one C lifting member, not B construction admission.

    Preserve its actual role and its own span. A foreplane or tandem wing must
    not inherit the reference span of the largest installed lifting surface.
    This transfers vectors/couples only; no beam, mount or material law expands.
    """
    matches = [s for s in geometry.surfaces if s.name == surface]
    if len(matches) != 1 or not matches[0].mirror_y:
        raise ValueError('One explicitly mirrored installed lifting surface required')
    s = matches[0]
    if s.role not in ('wing', 'canard', 'forewing', 'aftwing'):
        raise ValueError('Unsupported family lifting-surface role')
    half_span = max(sec.leading_edge_m[1] for sec in s.sections)
    return _consume_loads(geometry, case, expected_binding=expected_binding,
        root_axis_m=root_axis_m, half_span_m=half_span, surfaces=(surface,),
        reference_span_m=2*half_span, allowed_roles=(s.role,))


def _consume_loads(geometry, case, *, expected_binding, root_axis_m, half_span_m,
                   surfaces, reference_span_m, allowed_roles):
    geometry = InstalledGeometry.model_validate_json(geometry.model_dump_json())
    case = StructuralLoadCase.model_validate_json(case.model_dump_json())
    expected_binding = Binding.model_validate_json(expected_binding.model_dump_json())
    if geometry.binding != expected_binding or case.binding != expected_binding:
        raise ValueError('Stale geometry/hardware/mission/process/calibration/implementation binding')
    root = np.asarray(root_axis_m, dtype=float)
    if (root.shape != (3,) or not np.isfinite([*root, half_span_m]).all()
            or root[1] != 0 or not 0 < half_span_m <= .7
            or abs(half_span_m*2-reference_span_m) > 2e-7 or case.load_factor > 12
            or not surfaces or len(set(surfaces)) != len(surfaces)):
        raise ValueError('Invalid bounded installed structural reference')
    roles = {s.name: s.role for s in geometry.surfaces}
    if any(roles.get(name) not in allowed_roles for name in surfaces):
        raise ValueError('Unsupported or missing installed wing surface')
    cg = np.array(geometry.reference.cg_m)
    factor = case.load_factor
    sides = {-1: [], 1: []}
    omitted, omitted_force, omitted_moment = [], np.zeros(3), np.zeros(3)
    for strip in case.loads.strips:
        if strip.surface not in roles:
            raise ValueError('Strip refers to absent installed surface')
        if strip.surface not in surfaces:
            omitted.append(strip.surface)
            omitted_force += np.array(strip.force_n)*factor
            omitted_moment += (np.array(strip.moment_cg_nm)+np.cross(cg-root, strip.force_n))*factor
            continue
        center = np.array(strip.center_m)
        if abs(center[1]) < 1e-12 or abs(center[1]) > half_span_m+2e-7:
            raise ValueError('Ambiguous centreline or out-of-span structural strip')
        force = np.array(strip.force_n)*factor
        # Recorded moment is about CG and includes the position cross force.
        # Removing that part recovers the actual section couple; adding it a
        # second time would double-count sweep/chord load transfer.
        couple = (np.array(strip.moment_cg_nm)-np.cross(center-cg, np.array(strip.force_n)))*factor
        sides[-1 if center[1] < 0 else 1].append(dict(center_m=center.tolist(),
            force_n=force.tolist(), couple_nm=couple.tolist()))
    if any(not rows for rows in sides.values()):
        raise ValueError('Both physical half-wing loads are required; no implicit mirroring')
    results = {}
    for side, rows in sides.items():
        force = sum((np.array(r['force_n']) for r in rows), np.zeros(3))
        moment = sum((np.cross(np.array(r['center_m'])-root, r['force_n'])+r['couple_nm']
                      for r in rows), np.zeros(3))
        results['left' if side < 0 else 'right'] = dict(strips=rows, force_n=force.tolist(),
            moment_root_nm=moment.tolist(), reaction_force_n=(-force).tolist(),
            reaction_moment_nm=(-moment).tolist())
    all_force = np.array(case.loads.force_n)*factor
    all_moment = (np.array(case.loads.moment_cg_nm)+np.cross(cg-root, case.loads.force_n))*factor
    if not np.isfinite([*all_force, *all_moment, *omitted_force, *omitted_moment]).all():
        raise ValueError('Nonfinite transformed structural load')
    return dict(state='incomplete', qualification_state='shared_load_contract_only',
        binding=expected_binding.model_dump(mode='json'), load_case_sha256=digest(case),
        operating_sha256=digest(case.loads.operating_point), load_factor_applied=factor,
        root_axis_m=root.tolist(), half_span_m=half_span_m, sides=results,
        omitted_surfaces=sorted(set(omitted)),
        unassigned_surface_resultants=dict(force_n=omitted_force.tolist(), moment_root_nm=omitted_moment.tolist()),
        all_surface_resultants=dict(force_n=all_force.tolist(), moment_root_nm=all_moment.tolist()),
        manufacturing_ready=False,
        unsupported=['Shared load validity is not native evidence replay or structural qualification.',
                     'Body, inertial/payload/rod, gust and landing loads are absent from A steady lifting loads.',
                     'Multi-axis beam/joint strength, delivered trim and aero/material correspondence remain required.'])


def section_resultant(consumed, side, y_m):
    """Exact outboard point-load resultant at a local half-span station.

    A point exactly on the cut belongs to the outboard free body. All six load
    components and section couples are retained in installed global axes.
    """
    if side not in ('left', 'right') or not np.isfinite(y_m) or not 0 <= y_m <= consumed['half_span_m']:
        raise ValueError('Invalid structural cut')
    point = np.array(consumed['root_axis_m']); point[1] = (-1 if side == 'left' else 1)*y_m
    rows = [r for r in consumed['sides'][side]['strips'] if abs(r['center_m'][1]) >= y_m]
    force = sum((np.array(r['force_n']) for r in rows), np.zeros(3))
    moment = sum((np.cross(np.array(r['center_m'])-point, r['force_n'])+r['couple_nm']
                  for r in rows), np.zeros(3))
    return dict(force_n=force.tolist(), moment_nm=moment.tolist())


def _handoff(wing, diameter_m, manifest, directory, binding):
    binding = Binding.model_validate_json(binding.model_dump_json())
    measured = mounted_wing.verify(wing, diameter_m, manifest, directory)
    assembly = mounted_wing.construct(wing, diameter_m)
    parts = {p['name']: p for p in measured['measured_parts']}
    point = assembly['point']
    contacts = []
    def add(parent, child, location, kind):
        contacts.append(dict(support_id=parent+'__'+child, parent_part_id=parent, child_part_id=child,
            location_m=[float(x) for x in location], kind=kind,
            constrained_dofs=['tx','ty','tz','rx','ry','rz'] if kind == 'assumed_bond' else ['tx','tz'],
            parent_artifact_sha256=parts[parent]['sha256'], child_artifact_sha256=parts[child]['sha256']))
    for i, rivet in enumerate(measured['attachment_interfaces']['rivets']):
        add('fuselage_keel', f'mount_rivet_{i}', [rivet['x_m'], 0., rivet['z_m']], 'bearing')
        for side in (-1, 1):
            y = float(np.mean(parts[f'mount_plate_{side}']['bounds_m'], axis=0)[1])
            add(f'mount_rivet_{i}', f'mount_plate_{side}', [rivet['x_m'], y, rivet['z_m']], 'bearing')
    for side in (-1, 1):
        for child, parent in [(f'mount_collar_bond_{side}', f'mount_plate_{side}'),
                              ('clamp', f'mount_collar_bond_{side}')]:
            y = float(np.mean(parts[f'mount_plate_{side}']['bounds_m'], axis=0)[1])
            add(parent, child, [point[0], y, point[2]], 'assumed_bond')
        bond = f'wing_collar_bond_{side}'
        y = float(np.mean(parts[bond]['bounds_m'], axis=0)[1])
        add('clamp', bond, [point[0], y, point[2]], 'assumed_bond')
        add(bond, f'foam_{side}_0', [point[0], y, point[2]], 'assumed_bond')
        for i in range(1, 4):
            name = f'foam_{side}_{i}'; y = float(np.mean(parts[name]['bounds_m'], axis=0)[1])
            add('adhesive', name, [point[0], y, point[2]], 'assumed_bond')
    add('clamp', 'adhesive', point, 'assumed_bond')
    add('adhesive', 'spar', point, 'assumed_bond')
    # Material and interface graphs must include every delivered member.
    visited = {'fuselage_keel'}
    while True:
        expanded = visited | {c['child_part_id'] for c in contacts if c['parent_part_id'] in visited}
        if expanded == visited: break
        visited = expanded
    if visited != set(parts):
        raise ValueError('Disconnected shared installed-material/support graph')
    payload = dict(binding=binding.model_dump(mode='json'),
        delivered_geometry_sha256=measured['geometry_sha256'], contacts=contacts,
        material_artifacts={name: part['sha256'] for name, part in parts.items()},
        contact_measurements=dict(attachment=measured['attachment_interfaces'], foam=measured['foam_interfaces']),
        mechanical_evidence_state='declared', aerodynamic_geometry_correspondence='unverified')
    evidence_bytes = (canonical_json(payload)+'\n').encode()
    evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
    materials = tuple(InstalledMaterial(part_id=name, material_id='declared-'+str(p['material_law']['density_kg_m3'])+'kgm3',
        artifact_sha256=p['sha256'], mass_kg=p['mass_kg'], cg_m=tuple(p['cg_m']),
        inertia_kg_m2=tuple(float(x) for x in np.diag(p['inertia_kg_m2'])),
        material_law='stock_rod' if name == 'spar' else 'volume_density',
        density_kg_m3=p['material_law']['density_kg_m3']) for name, p in parts.items())
    supports = tuple(Support(support_id=c['support_id'], parent_part_id=c['parent_part_id'],
        child_part_id=c['child_part_id'], location_m=tuple(c['location_m']),
        constrained_dofs=tuple(c['constrained_dofs']), evidence_state='declared', artifact_sha256=evidence_sha)
        for c in contacts)
    structure = InstalledStructure(binding=binding, construction=mounted_wing.MODE,
        materials=materials, supports=supports, closure_state='declared')
    report = dict(state='incomplete', installed_structure=structure.model_dump(mode='json'),
        delivered_geometry_sha256=measured['geometry_sha256'],
        support_evidence=dict(file='installed-support-evidence.json', sha256=evidence_sha),
        full_material_inertia_kg_m2={n: p['inertia_kg_m2'] for n, p in parts.items()},
        aerodynamic_geometry_correspondence='unverified', manufacturing_ready=False,
        blockers=['Shared material inertia contract carries diagonals; full tensors are retained separately.',
                  'Mechanical constraints are declared assumptions, not measured attachment stiffness.',
                  'A native replay and correspondence to the actual delivered geometry remain required.'])
    return report, evidence_bytes


def export_structure(wing, diameter_m, manifest, directory, *, binding):
    """Write A-compatible material/support handoff without promoting closure."""
    report, evidence_bytes = _handoff(wing, diameter_m, manifest, directory, binding)
    outputs = [('installed-support-evidence.json', evidence_bytes),
               ('installed-structure.json', (canonical_json(report)+'\n').encode())]
    for name, data in outputs:
        path = Path(directory)/name
        if path.is_symlink() or (path.exists() and path.read_bytes() != data):
            raise ValueError('Preserve existing shared-structure evidence; export a new revision')
    for name, data in outputs:
        path = Path(directory)/name
        if not path.exists(): path.write_bytes(data)
    return report


def validate_structure(report, wing, diameter_m, manifest, directory, *, binding):
    """Parent-style read-only reconstruction from actual material, not labels."""
    expected, evidence_bytes = _handoff(wing, diameter_m, manifest, directory, binding)
    path = Path(directory)/'installed-support-evidence.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024*1024 or path.read_bytes() != evidence_bytes:
        raise ValueError('Changed or missing shared support evidence')
    if canonical_json(report) != canonical_json(expected):
        raise ValueError('Shared installed structure differs from measured revision/context')
    return False  # Validating a declared handoff cannot authorize physical admission.
