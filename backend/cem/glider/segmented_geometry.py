"""Bounded oblique-cut material primitives for a polyhedral wing assembly."""
import math
import numpy as np

TOL = 2e-7
MAX_FACES = 12000


def unit(value):
    value = np.asarray(value, dtype=float)
    length = np.linalg.norm(value)
    if not np.isfinite(value).all() or length < 1e-10:
        raise ValueError('Missing finite construction direction')
    return value/length


def basis(normal):
    n = unit(normal)
    e = np.array([1., 0., 0.])
    if abs(n[0]) > .9:
        e = np.array([0., 0., 1.])
    e = unit(e-n*np.dot(e, n))
    return np.column_stack([e, np.cross(n, e)])


def cap_ring(point, normal, axis, radius, theta, frame=None):
    """Intersection of a true circular cylinder with an oblique cap plane."""
    n, axis = unit(normal), unit(axis)
    cosine = np.dot(n, axis)
    if cosine*cosine < .5:
        raise ValueError('Oblique cut exceeds the bounded construction angle')
    frame = basis(axis) if frame is None else frame
    ellipse = frame-np.outer(axis, n@frame)/cosine
    circle = np.column_stack([np.cos(theta), np.sin(theta)])
    return np.asarray(point)+radius*circle@ellipse.T


def checked_mesh(vertices, faces):
    import trimesh
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(mesh, multibody=True)  # trusted builder only
    if (len(mesh.faces) > MAX_FACES or mesh.body_count != 1 or not mesh.is_watertight
            or not mesh.is_winding_consistent or mesh.volume <= 0):
        raise ValueError('Construction did not produce one closed material component')
    return mesh


def connect(a, b, faces):
    for i in range(len(a)):
        j = (i+1) % len(a)
        faces.extend([(a[i], a[j], b[i]), (a[j], b[j], b[i])])


def tube(points, normals, axes, outer_radius, inner_radius=0., count=48, frames=None):
    """One solid rod or continuous mitered sleeve/bond layer, including end caps."""
    theta = np.linspace(0., 2*np.pi, count, endpoint=False)
    given_frames = frames is not None
    frames = [basis(a) for a in axes] if frames is None else frames
    if len(points) == 3 and not given_frames:
        # One shared miter ellipse defines both lap frames. Projecting it onto
        # each axis-normal plane gives an orthonormal circle frame. This avoids
        # twisting the polygon between unlike cap planes or disconnecting it
        # at a three-dimensional bend.
        b, n, v = basis(normals[1]), unit(normals[1]), unit(axes[0])
        q = b.T@(np.eye(3)-np.outer(v, v))@b
        values, vectors = np.linalg.eigh(q)
        if np.min(values) < .5:
            raise ValueError('Miter angle exceeds construction envelope')
        middle = b@(vectors*(1/np.sqrt(values)))@vectors.T
        frames = [(np.eye(3)-np.outer(a, a))@middle for a in axes]
        if any(not np.allclose(f.T@f, np.eye(2), atol=1e-10) for f in frames):
            raise ValueError('Joint cut is not the common axis bisector')
    vertices, faces, outside, inside = [], [], [], []
    for i, (point, normal) in enumerate(zip(points, normals)):
        axis = axes[min(i, len(axes)-1)]
        frame = frames[min(i, len(frames)-1)]
        ring = cap_ring(point, normal, axis, outer_radius, theta, frame)
        outside.append(np.arange(len(vertices), len(vertices)+count)); vertices.extend(ring)
        if inner_radius:
            ring = cap_ring(point, normal, axis, inner_radius, theta, frame)
            inside.append(np.arange(len(vertices), len(vertices)+count)); vertices.extend(ring)
    for i in range(len(points)-1):
        connect(outside[i], outside[i+1], faces)
        if inner_radius:
            connect(inside[i], inside[i+1], faces)
    for i in (0, len(points)-1):
        if inner_radius:
            connect(outside[i], inside[i], faces)
        else:
            center = len(vertices); vertices.append(np.asarray(points[i]))
            faces.extend((center, outside[i][j], outside[i][(j+1) % count]) for j in range(count))
    return checked_mesh(np.asarray(vertices), faces)


def cap_mask(mesh, points, normals):
    mask = np.zeros(len(mesh.faces), dtype=bool)
    for point, normal in zip(points, normals):
        distance = (mesh.triangles-np.asarray(point))@unit(normal)
        mask |= np.all(np.abs(distance) <= TOL, axis=1)
    return mask


def boundary_ring(mesh, point, normal):
    counts = np.bincount(mesh.edges_unique_inverse)
    edges = mesh.edges_unique[counts == 1]
    b = basis(normal)
    selected = edges[np.all(np.abs((mesh.vertices[edges]-point)@unit(normal)) <= TOL, axis=1)]
    ids = np.unique(selected)
    if len(ids) < 8 or len(selected) != len(ids):
        raise ValueError('Missing single closed material end contour')
    xy = (mesh.vertices[ids]-point)@b
    order = np.argsort(np.arctan2(xy[:, 1], xy[:, 0])); ids = ids[order]
    wanted = {tuple(sorted((a, b))) for a, b in zip(ids, np.roll(ids, -1))}
    if wanted != {tuple(sorted(e)) for e in selected}:
        raise ValueError('End contour is not star-shaped about the spar')
    return mesh.vertices[ids]


def panel_outer(half_wing, points, normals):
    """Partition the original OML at shared joint planes; no shape substitution."""
    mesh = half_wing.copy()
    for point, normal in [(points[0], normals[0]), (points[1], -normals[1])]:
        mesh = clip_mesh(mesh, point, normal)
    # Explicitly close only the open contours created by trusted clipping.
    vertices, faces = list(mesh.vertices), list(mesh.faces)
    for point, normal in zip(points, normals):
        counts = np.bincount(mesh.edges_unique_inverse)
        boundary = mesh.edges_unique[counts == 1]
        if not np.any(np.all(np.abs((mesh.vertices[boundary]-point)@normal) <= TOL, axis=1)):
            continue
        ring = boundary_ring(mesh, point, normal)
        start = len(vertices); vertices.extend(ring)
        center = len(vertices); vertices.append(np.asarray(point))
        faces.extend((center, start+i, start+(i+1) % len(ring)) for i in range(len(ring)))
    return checked_mesh(vertices, faces)


def clip_mesh(mesh, point, normal):
    """Bounded triangle/half-space clipping, without a planar-boolean package."""
    import trimesh
    if len(mesh.faces) > MAX_FACES:
        raise ValueError('Clipping exceeds material mesh budget')
    triangles = []
    for triangle in mesh.triangles:
        polygon = []
        for a, b in zip(triangle, np.roll(triangle, -1, axis=0)):
            da, db = np.dot(a-point, normal), np.dot(b-point, normal)
            if da >= -1e-12:
                polygon.append(a)
            if (da > 1e-12 and db < -1e-12) or (da < -1e-12 and db > 1e-12):
                polygon.append(a+(b-a)*da/(da-db))
        for i in range(1, len(polygon)-1):
            face = np.array([polygon[0], polygon[i], polygon[i+1]])
            if np.linalg.norm(np.cross(face[1]-face[0], face[2]-face[0])) > 1e-18:
                triangles.append(face)
    if not triangles:
        raise ValueError('Empty clipped material panel')
    vertices = np.asarray(triangles).reshape(-1, 3)
    return trimesh.Trimesh(vertices, np.arange(len(vertices)).reshape(-1, 3), process=True)


def core(outer, points, normals, axis, radius, min_web, cells):
    """Closed foam material around a circular, oblique-ended through-channel."""
    from .foam_core import _annulus
    sides = outer.submesh([np.flatnonzero(~cap_mask(outer, points, normals))], append=True, repair=False)
    rings = [boundary_ring(sides, p, n) for p, n in zip(points, normals)]
    angles = [np.linspace(0., 2*np.pi, 64, endpoint=False)]
    for ring, point, normal in zip(rings, points, normals):
        # Every exterior contour ray must also occur on the elliptical hole.
        xy = (ring-point)@basis(axis)
        angles.append(np.mod(np.arctan2(xy[:, 1], xy[:, 0]), 2*np.pi))
    # Keep every regular 64-gon angle, but avoid nearly coincident alignment
    # vertices which collapse under float32 STL quantization at world offsets.
    chosen = list(angles[0])
    for angle in np.concatenate(angles[1:]):
        separation = np.abs(np.angle(np.exp(1j*(np.asarray(chosen)-angle))))
        if float(np.min(separation)) > 1e-4:
            chosen.append(float(angle))
    theta = np.sort(chosen)
    holes = [cap_ring(p, n, axis, radius, theta) for p, n in zip(points, normals)]
    # A circumscribed probe bounds the complete channel polygon. Clip it into
    # the original OML panel cells before face-halfspace tests; a miter cut may
    # include a small concave corner spanning two such cells.
    probe = tube(points, normals, [axis], radius/math.cos(math.pi/64), count=64)
    margin = cell_clearance(probe, cells)
    if margin < min_web-TOL:
        raise ValueError('Segment channel plus minimum foam web is not enclosed')
    vertices, faces = list(sides.vertices), list(sides.faces)
    hi = []
    for hole in holes:
        hi.append(np.arange(len(vertices), len(vertices)+len(theta))); vertices.extend(hole)
    connect(hi[0], hi[1], faces)
    for ring, hole, point, normal in zip(rings, holes, points, normals):
        b = basis(normal)
        # Reuse the planar annulus topology only; all output coordinates remain
        # the exact three-dimensional exterior and circular-cylinder cuts.
        def flat(r):
            xy = (r-point)@b
            return np.column_stack([xy[:, 0], np.zeros(len(r)), xy[:, 1]])
        _, cap = _annulus(flat(ring), flat(hole), np.zeros(3))
        start = len(vertices); vertices.extend(ring); vertices.extend(hole)
        faces.extend((cap+start).tolist())
    return checked_mesh(vertices, faces), margin


def cell_clearance(mesh, cells):
    """Conservative complete-triangle certificate over the original cell union."""
    covered = np.zeros(len(mesh.vertices), dtype=bool)
    # Together with the shared internal cut plane this proves that the cell
    # slabs cover whole triangles, including interiors, not just vertices.
    if (np.min((mesh.vertices-cells[0]['points'][0])@cells[0]['normals'][0]) < -TOL
            or np.max((mesh.vertices-cells[-1]['points'][1])@cells[-1]['normals'][1]) > TOL):
        raise ValueError('Material path exceeds original root/tip planes')
    minimum = math.inf
    for cell in cells:
        points, normals = cell['points'], cell['normals']
        inside = ((mesh.vertices-points[0])@normals[0] >= -TOL) & ((mesh.vertices-points[1])@normals[1] <= TOL)
        covered |= inside
        clipped = mesh
        try:
            for p, n in [(points[0], normals[0]), (points[1], -normals[1])]:
                clipped = clip_mesh(clipped, p, n)
        except ValueError as exc:
            if str(exc) == 'Empty clipped material panel':
                continue
            raise
        sides = cell['mesh'].submesh([np.flatnonzero(~cap_mask(cell['mesh'], points, normals))], append=True, repair=False)
        n = np.asarray(sides.face_normals)
        planes = np.einsum('ij,ij->i', n, sides.triangles[:, 0])
        distances = planes-np.max(clipped.vertices@n.T, axis=0)
        minimum = min(minimum, float(np.min(distances)))
    if not covered.all() or not math.isfinite(minimum):
        raise ValueError('Material path lies outside the bounded original panel cells')
    return minimum


def same_surface(actual, expected):
    """Canonical delivery binding, plus independent section/volume checks elsewhere."""
    from scipy.spatial import cKDTree
    if (len(actual.faces) > MAX_FACES or not actual.is_watertight
            or not actual.is_winding_consistent or actual.body_count != 1
            or actual.volume <= 0 or not np.isfinite(actual.vertices).all()
            or len(actual.faces) != len(expected.faces)):
        raise ValueError('Missing canonical connected material surface')
    distance, index = cKDTree(expected.triangles_center).query(actual.triangles_center)
    if np.max(distance) > TOL or len(np.unique(index)) != len(index):
        raise ValueError('Delivered material surface differs from the construction revision')
    delta = np.linalg.norm(actual.triangles[:, :, None]-expected.triangles[index, None, :], axis=-1)
    if np.max(np.min(delta, axis=2)) > TOL:
        raise ValueError('Delivered material vertices differ from the construction revision')


def section_loops(mesh, point, axis):
    """Complete plane-intersection polylines without Path3D simplification.

    Only coincident intersection endpoints are joined (1e-10 m, far below STL
    quantization). Missing/branched/open loops fail; material is never repaired.
    """
    import itertools
    import trimesh
    if len(mesh.faces) > MAX_FACES:
        raise ValueError('Section exceeds material facet budget')
    segments = trimesh.intersections.mesh_plane(mesh, plane_normal=axis, plane_origin=point)
    vertices, bins, edges = [], {}, set()
    epsilon = 1e-10
    def node(p):
        key = tuple(np.floor(p/epsilon).astype(np.int64))
        for offset in itertools.product((-1, 0, 1), repeat=3):
            nearby = tuple(a+b for a, b in zip(key, offset))
            for i in bins.get(nearby, ()):
                if np.linalg.norm(vertices[i]-p) <= epsilon:
                    return i
        i = len(vertices); vertices.append(p); bins.setdefault(key, []).append(i)
        return i
    for a, b in segments:
        i, j = node(a), node(b)
        if i != j:
            edges.add(tuple(sorted((i, j))))
    adjacent = {i: set() for edge in edges for i in edge}
    for i, j in edges:
        adjacent[i].add(j); adjacent[j].add(i)
    if not adjacent or any(len(v) != 2 for v in adjacent.values()):
        raise ValueError('Missing closed unbranched section boundary')
    loops, visited = [], set()
    for start in sorted(adjacent):
        if start in visited: continue
        path, previous, current = [], None, start
        while current not in visited:
            path.append(vertices[current]); visited.add(current)
            following = sorted(adjacent[current]-({previous} if previous is not None else set()))[0]
            previous, current = current, following
        if current != start or len(path) < 3:
            raise ValueError('Incomplete delivered section loop')
        path.append(path[0]); loops.append(np.asarray(path))
    return loops


def section(mesh, point, axis, hollow=False):
    """Green-theorem area and principal second moments of actual section loops."""
    cut = section_loops(mesh, point, axis)
    if len(cut) != (2 if hollow else 1):
        raise ValueError('Missing delivered section loops')
    b = basis(axis); loops = []; extreme = 0.
    for loop in cut:
        xy = (loop-point)@b
        extreme = max(extreme, float(np.max(np.linalg.norm(xy, axis=1))))
        x, y = xy[:-1].T; xx, yy = xy[1:].T
        cross = x*yy-xx*y
        area = np.sum(cross)/2
        sign = 1 if area > 0 else -1
        area *= sign; cross *= sign
        first = np.array([np.sum((x+xx)*cross), np.sum((y+yy)*cross)])/6
        ix = np.sum((y*y+y*yy+yy*yy)*cross)/12
        iy = np.sum((x*x+x*xx+xx*xx)*cross)/12
        ixy = np.sum((2*x*y+x*yy+xx*y+2*xx*yy)*cross)/24
        loops.append((area, first, np.array([[ix, -ixy], [-ixy, iy]])))
    loops.sort(key=lambda a: -a[0])
    area, first, inertia = loops[0]
    if hollow:
        area -= loops[1][0]; first -= loops[1][1]; inertia -= loops[1][2]
    cg = first/area
    inertia -= area*(np.dot(cg, cg)*np.eye(2)-np.outer(cg, cg))
    values = np.linalg.eigvalsh(inertia)
    if area <= 0 or np.min(values) <= 0 or not np.isfinite(values).all() or np.linalg.norm(cg) > TOL:
        raise ValueError('Invalid delivered principal section')
    return dict(area_m2=float(area), min_inertia_m4=float(np.min(values)),
                polar_inertia_m4=float(np.sum(values)), extreme_radius_m=extreme)
