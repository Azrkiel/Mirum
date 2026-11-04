"""One bounded solid-foam wing construction; no joint/load-transfer qualification.

Outer triangles are preserved exactly. A polygonal cylindrical through-channel
is joined to each end contour by an explicit annular cap, without mesh booleans
or verifier repair. Delivered material must already be closed before analysis.
"""
import math
from functools import lru_cache

import numpy as np

FOAM = dict(density_kg_m3=35., radial_clearance_m=.00025, min_web_m=.001,
            max_stock_thickness_m=.020)
MODE = 'foam_core_wing_v1'
METHOD = 'delivered-foam-core-channel-v1'
TOL = 2e-7
MAX_CORE_FACETS = 12000


def enabled(gl):
    return gl.cfg.get('cem_construction') == MODE


def channel_radius(diameter):
    # Extra vertices only subdivide this regular 64-gon; its minimum apothem
    # therefore bounds the clearance from a circular rod conservatively.
    return (diameter/2 + FOAM['radial_clearance_m'])/math.cos(math.pi/64)


def spar_position(gl):
    """Place the straight stock rod on the root airfoil's 30% camber line."""
    x, z = gl.wing_position()
    if not enabled(gl):
        return np.array([x+.3*gl.wing.root_chord, 0., z])
    code = gl.wing.naca
    m, p, u = int(code[0])/100, int(code[1])/10, .3
    camber = (m/p**2*(2*p*u-u*u) if u < p else
              m/(1-p)**2*(1-2*p+2*p*u-u*u)) if m else 0.
    t, chord = math.radians(gl.wing.incidence_deg), gl.wing.root_chord
    return np.array([x+chord*(u*math.cos(t)+camber*math.sin(t)), 0.,
                     z+chord*(camber*math.cos(t)-u*math.sin(t))])


@lru_cache(maxsize=128)
def _outer(airfoil, sections):
    from gliders.model.components import Surface, Section
    from .geometry import surface_meshes
    return tuple(surface_meshes(Surface('wing', [Section(*s) for s in sections], True, airfoil, 'wing')))


def outer_meshes(gl):
    s = gl.wing_surface()
    return _outer(s.airfoil, tuple((a.x, a.y, a.z, a.chord, a.twist) for a in s.sections))


@lru_cache(maxsize=128)
def _cores(airfoil, sections, position, diameter):
    return tuple(build_core(m, np.array(position), diameter) for m in _outer(airfoil, sections))


def core_meshes(gl):
    s = gl.wing_surface()
    sections = tuple((a.x, a.y, a.z, a.chord, a.twist) for a in s.sections)
    cores = _cores(s.airfoil, sections, tuple(spar_position(gl)), gl.spar()['diameter_m'])
    for core in cores:
        stock_thickness(gl, core)
    return cores


def stock_thickness(gl, mesh):
    # Rotate the cutting stock into the root chord frame, then measure the
    # complete delivered material envelope, rather than using NACA digits.
    t = math.radians(gl.wing.incidence_deg)
    thickness = float(np.ptp(mesh.vertices@np.array([math.sin(t), 0., math.cos(t)])))
    if thickness > FOAM['max_stock_thickness_m']+TOL:
        raise ValueError('Delivered foam core exceeds stock thickness')
    return thickness


def density(gl):
    return FOAM['density_kg_m3']*gl.cfg.get('cem_foam_mass_factor', 1.)


def size_spar(gl, base_items):
    from .processes import RODS_M, spar_size
    from .structure import bending_moment
    base_mass = sum(p.mass for p in base_items if p.name != 'wing')
    volume = sum(m.volume for m in outer_meshes(gl))
    theta = 2*math.pi/48
    polygon_ratio = 48*math.sin(theta)*(2+math.cos(theta))/(6*math.pi)
    for i, diameter in enumerate(RODS_M):
        # The 64-gon is a conservative minimum removed volume. Additional cap
        # alignment vertices remove slightly more foam, never more spar mass.
        removed = 32*channel_radius(diameter)**2*math.sin(2*math.pi/64)*gl.wing.span
        if volume <= removed:
            raise ValueError('No positive foam material volume for this spar')
        # Obtain the actual catalogue rod mass independently of the load guess.
        rod_mass = math.pi*diameter**2/4*gl.wing.span*1600.
        total = base_mass+(volume-removed)*density(gl)+rod_mass
        result = spar_size(total/polygon_ratio, gl.wing.span, gl.cfg['cem_launch_method'])
        if result['diameter_m'] <= diameter:
            result.update(diameter_m=diameter, mass_kg=rod_mass, sizing_mass_kg=total,
                          sizing_iterations=i+1, root_moment_nm=bending_moment(
                              total, gl.wing.span, gl.cfg['cem_launch_method'], 0.),
                          section_model='48-sided stock rod; solid foam wing/channel mass')
            return result
    raise ValueError('No catalogue rod closes the solid foam mass/load sizing')


def _sides(mesh):
    bounds = mesh.bounds[:, 1]
    y = mesh.triangles[:, :, 1]
    caps = np.any(np.all(np.abs(y[:, :, None]-bounds) <= TOL, axis=1), axis=1)
    return mesh.submesh([np.flatnonzero(~caps)], append=True, repair=False)


def _rings(sides, position):
    counts = np.bincount(sides.edges_unique_inverse)
    edges = sides.edges_unique[counts == 1]
    if not len(edges):
        raise ValueError('Missing two open outer contours for channel construction')
    rings = []
    for y in sides.bounds[:, 1]:
        end = edges[np.all(np.abs(sides.vertices[edges, 1]-y) <= TOL, axis=1)]
        ids = np.unique(end)
        if len(ids) < 8 or len(end) != len(ids):
            raise ValueError('Invalid outer contour')
        v = sides.vertices[ids]
        angle = np.arctan2(v[:, 2]-position[2], v[:, 0]-position[0])
        v = v[np.argsort(angle)]
        # Angular order is valid only for a star-shaped, single contour.
        index = ids[np.argsort(angle)]
        expected = {tuple(sorted((a, b))) for a, b in zip(index, np.roll(index, -1))}
        if expected != {tuple(sorted(e)) for e in end}:
            raise ValueError('Non-star-shaped outer contour')
        rings.append(v)
    if sum(len(r) for r in rings) != len(edges):
        raise ValueError('Unexpected open outer boundary')
    return rings


def _web(outer, position, radius):
    sides = _sides(outer)
    normals = sides.face_normals
    planes = np.einsum('ij,ij->i', normals, sides.triangles[:, 0])
    radial = np.linalg.norm(normals[:, [0, 2]], axis=1)
    if np.any(radial < 1e-10):
        raise ValueError('Unsupported channel wall orientation')
    centres = np.tile(position, (2, 1)); centres[:, 1] = outer.bounds[:, 1]
    margin = (planes-np.max(centres@normals.T, axis=0))/radial-radius
    minimum = float(np.min(margin))
    if not math.isfinite(minimum) or minimum < FOAM['min_web_m']-TOL:
        raise ValueError('Spar channel plus minimum foam web is not enclosed')
    return minimum


def _annulus(outer, inner, position):
    """Triangulate two nested star contours by merging their angular events."""
    def angles(points):
        return np.mod(np.arctan2(points[:, 2]-position[2], points[:, 0]-position[0]), 2*np.pi)
    a, b = angles(outer), angles(inner)
    oi, ii = np.argsort(a), np.argsort(b)
    # The channel includes every outer-vertex angle, so both loops share a cut.
    cut = a[oi[0]]
    j = int(np.argmin(np.abs(np.angle(np.exp(1j*(b-cut))))))
    ii = np.roll(np.arange(len(inner)), -j)
    aa = np.r_[np.mod(a[oi]-cut, 2*np.pi), 2*np.pi]
    bb = np.r_[np.mod(b[ii]-cut, 2*np.pi), 2*np.pi]
    bb[0] = 0.
    points = np.vstack([outer, inner]); faces = []
    i = j = 0
    while i < len(outer) or j < len(inner):
        o, q = int(oi[i % len(outer)]), len(outer)+int(ii[j % len(inner)])
        if i < len(outer) and (j == len(inner) or aa[i+1] <= bb[j+1]):
            faces.append((o, int(oi[(i+1) % len(outer)]), q)); i += 1
        else:
            faces.append((o, len(outer)+int(ii[(j+1) % len(inner)]), q)); j += 1
    return points, np.asarray(faces)


def build_core(outer, position, diameter):
    import trimesh
    radius = channel_radius(diameter)
    _web(outer, position, radius)
    sides = _sides(outer)
    rings = _rings(sides, position)
    angles = [np.linspace(0., 2*np.pi, 64, endpoint=False)]
    angles += [np.mod(np.arctan2(r[:, 2]-position[2], r[:, 0]-position[0]), 2*np.pi) for r in rings]
    theta = np.unique(np.concatenate(angles))
    circle = np.column_stack([position[0]+radius*np.cos(theta), np.zeros(len(theta)),
                              position[2]+radius*np.sin(theta)])
    inner = []
    for y in outer.bounds[:, 1]:
        r = circle.copy(); r[:, 1] = y; inner.append(r)
    vertices, faces = [np.asarray(sides.vertices)], [np.asarray(sides.faces)]
    offset = len(sides.vertices)
    vertices.append(np.vstack(inner))
    n = len(theta)
    for i in range(n):
        j = (i+1) % n
        faces.append(np.array([(offset+i, offset+j, offset+n+i),
                               (offset+j, offset+n+j, offset+n+i)]))
    offset += 2*n
    for ring, hole in zip(rings, inner):
        points, cap = _annulus(ring, hole, position)
        vertices.append(points); faces.append(cap+offset); offset += len(points)
    mesh = trimesh.Trimesh(np.vstack(vertices), np.vstack(faces), process=True)
    trimesh.repair.fix_normals(mesh, multibody=True)  # trusted construction only
    if (len(mesh.faces) > MAX_CORE_FACETS or not mesh.is_watertight
            or not mesh.is_winding_consistent or mesh.body_count != 1 or mesh.volume <= 0):
        raise ValueError('Channel construction did not produce one closed material core')
    return mesh


def verify_core(core, expected_outer, position, diameter):
    """Recover the analysis envelope from already-closed delivered material.

    This does not repair the input. Mixed boundary triangles may only close the
    two annular end faces; inner vertices must describe the prescribed through
    channel. Outer side triangles must independently match the aerodynamic OML.
    """
    import trimesh
    from scipy.spatial import cKDTree
    if (len(core.faces) > MAX_CORE_FACETS or not core.is_watertight or not core.is_winding_consistent
            or core.body_count != 1 or core.volume <= 0 or not np.isfinite(core.vertices).all()):
        raise ValueError('Missing closed delivered foam material')
    radius = channel_radius(diameter)
    radial = np.linalg.norm(core.vertices[:, [0, 2]]-np.asarray(position)[[0, 2]], axis=1)
    is_inner = np.abs(radial-radius) <= TOL
    if not np.any(is_inner) or np.any((~is_inner) & (radial < radius+FOAM['min_web_m']-TOL)):
        raise ValueError('Missing prescribed channel or foam web')
    inner_faces = np.all(is_inner[core.faces], axis=1)
    outer_faces = np.all(~is_inner[core.faces], axis=1)
    caps = ~(inner_faces | outer_faces)
    # No internal plugs or extra internal caps are permitted.
    ys = core.triangles[caps, :, 1]
    ends = expected_outer.bounds[:, 1]
    if not np.all(np.any(np.all(np.abs(ys[:, :, None]-ends) <= TOL, axis=1), axis=1)):
        raise ValueError('Channel has an internal obstruction')
    cap_sign = np.sign(core.triangles_center[caps, 1]-np.mean(ends))
    if np.any(core.face_normals[caps, 1]*cap_sign < 1-1e-5):
        raise ValueError('Folded or inverted annular channel cap')
    inward = core.triangles_center[inner_faces][:, [0, 2]]-np.asarray(position)[[0, 2]]
    if np.any(np.einsum('ij,ij->i', core.face_normals[inner_faces][:, [0, 2]], inward) >= 0):
        raise ValueError('Channel normals do not enclose an empty passage')
    outer_sides = core.submesh([np.flatnonzero(outer_faces)], append=True, repair=False)
    expected_sides = _sides(expected_outer)
    if len(outer_sides.faces) != len(expected_sides.faces):
        raise ValueError('Delivered outer shape differs from aerodynamic OML')
    distance, index = cKDTree(expected_sides.triangles_center).query(outer_sides.triangles_center)
    if np.max(distance) > TOL or len(np.unique(index)) != len(index):
        raise ValueError('Delivered outer shape differs from aerodynamic OML')
    delta = np.linalg.norm(outer_sides.triangles[:, :, None, :]-expected_sides.triangles[index, None, :, :], axis=-1)
    if np.max(np.min(delta, axis=2)) > TOL:
        raise ValueError('Delivered aerodynamic vertices differ')
    # The inner boundary must be a full-length prism. All its vertices lie on
    # the prescribed circle and its only boundary loops lie at the two ends.
    channel = core.submesh([np.flatnonzero(inner_faces)], append=True, repair=False)
    hole_rings = _rings(channel, position)
    for ring in hole_rings:
        if len(ring) < 64:
            raise ValueError('Insufficient channel tessellation')
    a, b = [r[:, [0, 2]][np.lexsort((r[:, 2], r[:, 0]))] for r in hole_rings]
    if a.shape != b.shape or not np.allclose(a, b, rtol=0, atol=TOL):
        raise ValueError('Channel sections differ')
    y = channel.vertices[:, 1]
    if not np.all(np.any(np.abs(y[:, None]-ends) <= TOL, axis=1)):
        raise ValueError('Channel has unsupported internal stations')
    if np.max(np.abs(channel.face_normals[:, 1])) > 1e-5:
        raise ValueError('Channel is not prismatic')
    theta = np.sort(np.mod(np.arctan2(a[:, 1]-position[2], a[:, 0]-position[0]), 2*np.pi))
    gap = np.max(np.diff(np.r_[theta, theta[0]+2*np.pi]))
    clearance = radius*math.cos(gap/2)-diameter/2
    if clearance < FOAM['radial_clearance_m']-TOL:
        raise ValueError('Insufficient delivered radial channel clearance')
    # Derive a separate analysis envelope, closing the proven end contours.
    rings = _rings(outer_sides, position)
    vertices, faces = list(outer_sides.vertices), list(outer_sides.faces)
    for ring in rings:
        start = len(vertices); vertices.extend(ring); center = len(vertices); vertices.append(np.mean(ring, axis=0))
        faces.extend((center, start+i, start+(i+1) % len(ring)) for i in range(len(ring)))
    envelope = trimesh.Trimesh(vertices, faces, process=True)
    trimesh.repair.fix_normals(envelope)  # derived envelope, never material repair
    if not envelope.is_watertight or envelope.volume <= core.volume:
        raise ValueError('Cannot recover a closed outer analysis envelope')
    web = _web(envelope, position, radius)
    return dict(state='passed', method=METHOD, clearance_m=float(clearance), min_web_m=web,
                outer_shape_state='matched_parametric_oml', material_volume_m3=float(core.volume),
                load_path_state='unverified'), envelope


def verify_delivery(gl, cores, rod):
    """Require the actual stock rod to occupy the prescribed channel axis."""
    position, diameter = spar_position(gl), gl.spar()['diameter_m']
    centre = np.mean(rod.bounds, axis=0)
    radial = np.linalg.norm(rod.vertices[:, [0, 2]]-position[[0, 2]], axis=1)
    if (not np.allclose(centre[[0, 2]], position[[0, 2]], rtol=0, atol=TOL)
            or abs(float(np.max(radial))-diameter/2) > TOL):
        raise ValueError('Delivered stock rod differs from the prescribed channel axis/diameter')
    evidence, envelopes = [], []
    for core, expected in zip(cores, outer_meshes(gl)):
        item, envelope = verify_core(core, expected, position, diameter)
        item['stock_thickness_m'] = stock_thickness(gl, core)
        evidence.append(item); envelopes.append(envelope)
    if len(cores) != 2 or len(evidence) != 2:
        raise ValueError('Expected exactly two foam wing cores')
    return dict(state='passed', method=METHOD, wings=evidence, load_path_state='unverified',
                material_assumptions=dict(FOAM), manufacturing_ready=False), envelopes
