"""Bounded closed OML meshes, with measured skin-equivalent mass properties.

These are analysis/template bundles. They are not hollow FDM parts and must not
be advertised as print-ready. Skin-equivalent mass is explicitly an areal law,
not material density times the volume of a solid outer mould line.
"""
from pathlib import Path
import hashlib
import math
import struct

import numpy as np

MAX_FACETS = 100000
MAX_BYTES = 84 + 50 * MAX_FACETS


def closed_mesh(rings):
    import trimesh
    vertices = np.vstack(rings)
    n = len(rings[0])
    faces = []
    for j in range(len(rings) - 1):
        for i in range(n):
            a, b, c, d = j*n+i, j*n+(i+1)%n, (j+1)*n+i, (j+1)*n+(i+1)%n
            faces.extend([(a, b, c), (b, d, c)])
    # NACA sections and fuselage ellipse rings are star-shaped about their
    # centroids. Explicit end caps belong to construction, never verifier repair.
    for j, flip in [(0, True), (len(rings)-1, False)]:
        center = len(vertices)
        vertices = np.vstack([vertices, np.mean(rings[j], axis=0)])
        for i in range(n):
            face = (center, j*n+i, j*n+(i+1)%n)
            faces.append(face[::-1] if flip else face)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(mesh, multibody=True)
    return mesh


def surface_meshes(surface):
    from gliders.model.components import naca
    # No duplicated closure point; finite NACA trailing edge is closed by a facet.
    af = naca(surface.airfoil).repanel(n_points_per_side=33).coordinates
    if np.linalg.norm(af[0] - af[-1]) < 1e-10:
        af = af[:-1]
    sections = surface.sections
    rings = []
    for j, s in enumerate(sections):
        neighbor = sections[min(j+1, len(sections)-1)] if j < len(sections)-1 else sections[j-1]
        direction = np.array([0., neighbor.y - s.y, neighbor.z - s.z])
        if j == len(sections)-1:
            direction *= -1
        direction /= np.linalg.norm(direction)
        normal = np.cross(np.array([1., 0., 0.]), direction)
        t = math.radians(s.twist)
        chord_axis = np.array([math.cos(t), 0., -math.sin(t)])
        thickness_axis = normal * math.cos(t) + np.array([math.sin(t), 0., 0.])
        rings.append(np.array([s.x, s.y, s.z]) + s.chord *
                     (af[:, :1] * chord_axis + af[:, 1:] * thickness_axis))
    meshes = [closed_mesh(rings)]
    if surface.symmetric:
        mirrored = meshes[0].copy()
        mirrored.apply_scale([1., -1., 1.])
        meshes.append(mirrored)
    return meshes


def build_meshes(gl):
    import trimesh
    parts = []
    for surface in [gl.wing_surface(), *gl.tail_surfaces()]:
        for side, mesh in enumerate(surface_meshes(surface)):
            density = (gl.cfg['materials']['wing_areal_density_kg_m2'] if surface.role == 'wing'
                       else gl.cfg['materials']['tail_areal_density_kg_m2']) / 2
            parts.append((f'{surface.name}_{side}', mesh, dict(law='skin_equivalent', areal_density_kg_m2=density)))
    # Match the existing fuselage cross-section model rather than replacing it
    # with a visually similar ellipse.
    ap = gl.fuse.to_asb()
    rings = []
    for xs in ap.xsecs:
        # AeroSandbox's superellipse mesh uses its own cross-section definition.
        y, z = xs.get_3D_coordinates(theta=np.linspace(0, 2*np.pi, 48, endpoint=False))[1:]
        rings.append(np.column_stack([np.full(48, float(xs.xyz_c[0])), y, z]))
    mesh = closed_mesh(rings)
    parts.append(('fuselage', mesh, dict(law='skin_equivalent',
                 areal_density_kg_m2=gl.cfg['materials']['fuselage_shell_areal_density_kg_m2'])))
    spar = gl.spar()
    transform = trimesh.geometry.align_vectors([0, 0, 1], [0, 1, 0])
    transform[:3, 3] = [gl.wing_position()[0] + .3*gl.wing.root_chord, 0, gl.wing_position()[1]]
    rod = trimesh.creation.cylinder(radius=spar['diameter_m']/2, height=spar['span_m'], sections=48, transform=transform)
    parts.append(('spar', rod, dict(law='solid', density_kg_m3=1600.)))
    return parts


def export_bundle(gl, directory, output_id):
    from gliders.model import export
    import trimesh
    directory.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, mesh, material in build_meshes(gl):
        if len(mesh.faces) > MAX_FACETS or not mesh.is_watertight or not mesh.is_winding_consistent or mesh.volume <= 0:
            raise ValueError(f'Construction did not produce a closed oriented {name} mesh')
        path = directory / f'{output_id}_{name}.stl'
        export.write_stl(str(path), mesh.vertices, mesh.faces)
        with path.open('r+b') as f:
            f.write(f'Mirum CEM {output_id}; mm; analysis OML'.encode().ljust(80, b' '))
        manifest.append(dict(name=name, file=path.name, material=material,
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    export.export_templates(gl, str(directory))
    for path in list(directory.glob('*.svg')):
        if path.name.startswith(output_id + '_'):
            continue
        path.write_text(path.read_text().replace('<svg', f'<!-- Mirum CEM {output_id}; screening template -->\n<svg', 1))
        path.rename(directory / f'{output_id}_{path.name}')
    return dict(output_id=output_id, parts=manifest, manufacturing_ready=False,
                geometry_kind='closed outer mould lines + solid spar; skin-equivalent analysis',
                missing_features=['spar channels', 'rib layout', 'ballast/payload cavities', 'FDM shell/infill/bed splitting'])


def measure_part(path, material):
    import trimesh
    if path.stat().st_size < 84 or path.stat().st_size > MAX_BYTES:
        raise ValueError('Mesh size outside evidence budget')
    with path.open('rb') as f:
        f.read(80)
        count = struct.unpack('<I', f.read(4))[0]
    if count > MAX_FACETS or path.stat().st_size != 84 + 50 * count:
        raise ValueError('Malformed binary STL or facet budget exceeded')
    mesh = trimesh.load_mesh(path, file_type='stl', process=True)
    mesh.apply_scale(.001)  # manufacturing STL units are mm
    if not np.isfinite(mesh.vertices).all() or not mesh.is_watertight or not mesh.is_winding_consistent or mesh.volume <= 0:
        raise ValueError('Required closed oriented geometry evidence is missing')
    if material['law'] == 'solid':
        props = mesh.mass_properties
        mass = float(props['volume'] * material['density_kg_m3'])
        cg = np.asarray(props['center_mass'])
        inertia = np.asarray(props['inertia']) * material['density_kg_m3']
    else:
        triangles = mesh.triangles
        masses = mesh.area_faces * material['areal_density_kg_m2']
        mass = float(masses.sum())
        cg = np.sum(masses[:, None] * triangles.mean(axis=1), axis=0) / mass
        centered = triangles - cg
        total = centered.sum(axis=1)
        moments = (np.einsum('nki,nkj->nij', centered, centered) + np.einsum('ni,nj->nij', total, total)) / 12
        inertia = np.sum(masses[:, None, None] * (np.trace(moments, axis1=1, axis2=2)[:, None, None] * np.eye(3) - moments), axis=0)
    if not math.isfinite(mass) or mass <= 0 or not np.isfinite(inertia).all() or np.min(np.linalg.eigvalsh(inertia)) <= 0:
        raise ValueError('Nonphysical measured mass properties')
    return dict(mass_kg=mass, cg_m=cg.tolist(), inertia_kg_m2=inertia.tolist(),
                volume_m3=float(mesh.volume), area_m2=float(mesh.area),
                bounds_m=mesh.bounds.tolist(), material_law=material,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
