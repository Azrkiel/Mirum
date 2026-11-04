"""Measured two-plate/two-rivet collar-to-keel attachment prototype.

This provides a real material interface chain and bounded nominal checks. The
keel is a local fuselage member, not a qualified complete fuselage. Equal
connector stiffness, bearing contact, rigid collar/plates and material priors
are explicit assumptions; no friction/preload capacity is credited.
"""
import copy
import math

import numpy as np
from scipy.spatial import Delaunay

from cem.core import digest
from . import segmented_geometry as geo
from .physical_loads import resultants

MODE = 'two_plate_riveted_keel_v2'
SPEC = dict(plate_width_m=.044, plate_bottom_m=-.045, plate_top_m=.007,
            plate_thickness_m=.005, keel_width_m=.030, keel_bottom_m=-.045,
            keel_top_m=-.020, keel_front_m=-.050, keel_back_m=.120,
            bolt_half_pitch_m=.012, bolt_z_m=-.032, bolt_radius_m=.0015,
            head_radius_m=.003, head_thickness_m=.001, collar_bond_thickness_m=.00015)
MATERIALS = dict(
    plate=dict(density_kg_m3=2700., normal_pa=150e6, shear_pa=70e6, bearing_pa=120e6),
    rivet=dict(density_kg_m3=2700., normal_pa=150e6, shear_pa=80e6),
    bond=dict(density_kg_m3=1100., normal_pa=1e6, shear_pa=8e6),
    keel=dict(density_kg_m3=600., shear_pa=4e6, bearing_pa=12e6),
    safety_factor=2.)


def perforated_prism(xz_bounds, y_bounds, holes, count=64):
    """Trusted extrusion of a rectangle with disjoint polygonal through-bores.

    Delaunay is used only when it contains EVERY prescribed contour edge.
    Boundary incidence after hole removal must equal the original loops. Thus
    centroid filtering cannot silently accept an unconstrained triangulation
    crossing a bore. Unsupported triangulations fail instead of repairing.
    """
    x0, z0, x1, z1 = xz_bounds
    y0, y1 = y_bounds
    if (not np.isfinite([*xz_bounds, *y_bounds]).all() or x1 <= x0 or z1 <= z0 or y1 <= y0
            or type(count) is not int or count < 16 or count > 128 or len(holes) > 4):
        raise ValueError('Invalid bounded perforated stock')
    contours = [np.array([[x0, z0], [x1, z0], [x1, z1], [x0, z1]])]
    for i, (x, z, radius) in enumerate(holes):
        if (not np.isfinite([x, z, radius]).all() or radius <= 0
                or min(x-x0, x1-x, z-z0, z1-z) <= radius):
            raise ValueError('Bore is outside the stock')
        if any(math.hypot(x-a, z-b) <= radius+r for a, b, r in holes[:i]):
            raise ValueError('Intersecting attachment bores')
        theta = np.linspace(0, 2*math.pi, count, endpoint=False)
        contours.append(np.column_stack([x+radius*np.cos(theta), z+radius*np.sin(theta)]))
    points = np.vstack(contours)
    loops, start = [], 0
    for contour in contours:
        loops.append(np.arange(start, start+len(contour))); start += len(contour)
    boundary = {tuple(sorted((a, b))) for loop in loops for a, b in zip(loop, np.roll(loop, -1))}
    triangles = Delaunay(points).simplices
    edges = {tuple(sorted((a, b))) for tri in triangles for a, b in zip(tri, np.roll(tri, -1))}
    if not boundary <= edges:
        raise ValueError('Triangulation did not preserve stock/bore boundaries')
    centers = points[triangles].mean(axis=1)
    keep = np.ones(len(triangles), dtype=bool)
    for hole in contours[1:]:
        edge = np.roll(hole, -1, axis=0)-hole
        delta = centers[:, None, :]-hole
        cross = edge[:, 0]*delta[:, :, 1]-edge[:, 1]*delta[:, :, 0]
        keep &= ~np.all(cross >= -1e-16, axis=1)
    triangles = triangles[keep]
    counts = {}
    for tri in triangles:
        for a, b in zip(tri, np.roll(tri, -1)):
            key = tuple(sorted((a, b))); counts[key] = counts.get(key, 0)+1
    if {edge for edge, n in counts.items() if n == 1} != boundary or max(counts.values()) > 2:
        raise ValueError('Incomplete perforated material section')
    vertices = np.vstack([np.column_stack([points[:, 0], np.full(len(points), y), points[:, 1]])
                          for y in (y0, y1)])
    faces = [*triangles, *(triangles+len(points))]
    for loop in loops:
        geo.connect(loop, loop+len(points), faces)
    return geo.checked_mesh(vertices, faces)


def _rivet(x, z, grip, spec):
    thickness = spec['head_thickness_m']
    y = [-grip-thickness, -grip, -grip, grip, grip, grip+thickness]
    radii = [spec['head_radius_m']]*2+[spec['bolt_radius_m']]*2+[spec['head_radius_m']]*2
    theta = np.linspace(0, 2*math.pi, 64, endpoint=False)
    rings = [np.column_stack([x+r*np.cos(theta), np.full(64, yy), z+r*np.sin(theta)])
             for yy, r in zip(y, radii)]
    vertices, faces = list(np.vstack(rings)), []
    for i in range(len(rings)-1):
        geo.connect(np.arange(64)+64*i, np.arange(64)+64*(i+1), faces)
    for index in (0, len(rings)-1):
        center = len(vertices); vertices.append([x, y[index], z])
        faces.extend((center, index*64+j, index*64+(j+1) % 64) for j in range(64))
    return geo.checked_mesh(vertices, faces)


def construct(point, collar_radius_m):
    point = np.asarray(point, dtype=float)
    if (point.shape != (3,) or not np.isfinite(point).all() or point[1] != 0
            or not math.isfinite(collar_radius_m) or not .002 <= collar_radius_m <= .006):
        raise ValueError('Unsupported installed collar interface')
    s = SPEC
    pitch, bolt_z, bolt_r = s['bolt_half_pitch_m'], s['bolt_z_m'], s['bolt_radius_m']
    bolts = [(-pitch, bolt_z, bolt_r), (pitch, bolt_z, bolt_r)]
    parts = {}
    for side in (-1, 1):
        ends = sorted([side*s['keel_width_m']/2, side*(s['keel_width_m']/2+s['plate_thickness_m'])])
        bond_radius = collar_radius_m+s['collar_bond_thickness_m']
        mesh = perforated_prism([-s['plate_width_m']/2, s['plate_bottom_m'],
                                 s['plate_width_m']/2, s['plate_top_m']], ends,
                                [(0., 0., bond_radius), *bolts])
        parts[f'mount_plate_{side}'] = dict(mesh=mesh, kind='plate')
        bond = geo.tube([[0., ends[0], 0.], [0., ends[1], 0.]],
            [[0., 1., 0.]]*2, [[0., 1., 0.]], bond_radius, collar_radius_m, count=64)
        parts[f'mount_collar_bond_{side}'] = dict(mesh=bond, kind='bond')
    mesh = perforated_prism([s['keel_front_m'], s['keel_bottom_m'], s['keel_back_m'], s['keel_top_m']],
                           [-s['keel_width_m']/2, s['keel_width_m']/2], bolts)
    parts['fuselage_keel'] = dict(mesh=mesh, kind='keel')
    grip = s['keel_width_m']/2+s['plate_thickness_m']
    for i, (x, z, _) in enumerate(bolts):
        parts[f'mount_rivet_{i}'] = dict(mesh=_rivet(x, z, grip, s), kind='rivet')
    for part in parts.values():
        part['mesh'].apply_translation(point)
        part['material'] = dict(law='solid', density_kg_m3=MATERIALS[part.pop('kind')]['density_kg_m3'])
    return dict(parts=parts, root_cutback_m=grip,
                revision_sha256=digest(dict(mode=MODE, spec=s, material_priors=MATERIALS,
                    point=point.tolist(), collar_radius_m=collar_radius_m)))


def _polygon(loop, columns):
    xy = loop[:, columns]
    a, b = xy[:-1], xy[1:]
    cross = a[:, 0]*b[:, 1]-b[:, 0]*a[:, 1]
    area = float(cross.sum()/2)
    if abs(area) < 1e-16:
        raise ValueError('Missing finite section area')
    centroid = np.sum((a+b)*cross[:, None], axis=0)/(6*area)
    return abs(area), centroid


def _bores(mesh, y):
    loops = geo.section_loops(mesh, np.array([0., y, 0.]), np.array([0., 1., 0.]))
    loops.sort(key=lambda loop: -_polygon(loop, [0, 2])[0])
    result = []
    for loop in loops[1:]:
        area, center = _polygon(loop, [0, 2])
        radius = float(np.max(np.linalg.norm(loop[:, [0, 2]]-center, axis=1)))
        result.append(dict(center_xz_m=center.tolist(), radius_m=radius, area_m2=area))
    return result


def net_section(mesh, z):
    """Area and Iy from measured disjoint rectangular ligaments at a bore centre.

    This extrusion's bores run along Y. At their centre heights, a horizontal
    cut consists of disjoint solid rectangles, with no nested holes. Canonical
    geometry validation is mandatory before using these bounded critical cuts.
    """
    loops = geo.section_loops(mesh, np.array([0., 0., z]), np.array([0., 0., 1.]))
    area, first, second = 0., 0., 0.
    xs = []
    for loop in loops:
        a, b = loop[:-1, :2], loop[1:, :2]
        cross = a[:, 0]*b[:, 1]-b[:, 0]*a[:, 1]
        sign = 1 if cross.sum() > 0 else -1
        cross *= sign
        area += float(cross.sum()/2)
        first += float(((a[:, 0]+b[:, 0])*cross).sum()/6)
        second += float(((a[:, 0]**2+a[:, 0]*b[:, 0]+b[:, 0]**2)*cross).sum()/12)
        xs.extend(loop[:, 0])
    if area <= 0:
        raise ValueError('Missing attachment ligament')
    center = first/area
    iy = second-area*center*center
    if iy <= 0 or not math.isfinite(iy):
        raise ValueError('Invalid measured attachment net section')
    return dict(area_m2=area, inertia_y_m4=iy, centroid_x_m=center,
                extreme_x_m=max(abs(float(x)-center) for x in xs))


def measure(point, collar, meshes, *, collar_radius_m):
    """Require actual connected canonical material, then recover every interface."""
    point = np.asarray(point, dtype=float)
    # Actual collar radius is recovered by a cross-section, never inferred from
    # the supplied manifest or an outer bounding-box diameter.
    collar_section = geo.section(collar, point, np.array([0., 1., 0.]), hollow=True)
    radius = collar_section['extreme_radius_m']
    if abs(radius-collar_radius_m) > geo.TOL:
        raise ValueError('Delivered collar differs from installed revision')
    inner = _bores(collar, float(point[1]))
    if len(inner) != 1 or np.linalg.norm(np.array(inner[0]['center_xz_m'])-point[[0, 2]]) > geo.TOL:
        raise ValueError('Missing centred collar through-bore')
    ends = np.tile(point, (2, 1)); ends[:, 1] = collar.bounds[:, 1]
    geo.same_surface(collar, geo.tube(ends, [[0., 1., 0.]]*2, [[0., 1., 0.]],
        collar_radius_m, inner[0]['radius_m'], count=64))
    expected = construct(point, collar_radius_m)
    if set(meshes) != set(expected['parts']):
        raise ValueError('Missing installed mount plate, rivet or fuselage keel')
    for name, item in expected['parts'].items():
        geo.same_surface(meshes[name], item['mesh'])
    plates, rivets = [], []
    for side in (-1, 1):
        mesh = meshes[f'mount_plate_{side}']
        y = float(np.mean(mesh.bounds[:, 1]))
        bores = _bores(mesh, y)
        if len(bores) != 3:
            raise ValueError('Missing collar/fastener through-bores')
        bores.sort(key=lambda b: -b['center_xz_m'][1])
        top, bolts = bores[0], sorted(bores[1:], key=lambda b: b['center_xz_m'][0])
        if (np.linalg.norm(np.array(top['center_xz_m'])-point[[0, 2]]) > geo.TOL
                or abs(top['radius_m']-radius-SPEC['collar_bond_thickness_m']) > geo.TOL):
            raise ValueError('Disconnected collar-to-plate bearing interface')
        if collar.bounds[0, 1] > mesh.bounds[0, 1]+geo.TOL or collar.bounds[1, 1] < mesh.bounds[1, 1]-geo.TOL:
            raise ValueError('Collar does not cover installed mount plates')
        sections = [net_section(mesh, b['center_xz_m'][1]) for b in (top, *bolts)]
        bond_mesh = meshes[f'mount_collar_bond_{side}']
        bond_section = geo.section(bond_mesh, np.array([point[0], y, point[2]]),
            np.array([0., 1., 0.]), hollow=True)
        bond_length = float(np.ptp(bond_mesh.bounds[:, 1]))
        bore_loops = geo.section_loops(collar, point, np.array([0., 1., 0.]))
        outer_loop = max(bore_loops, key=lambda loop: _polygon(loop, [0, 2])[0])
        perimeter = float(np.linalg.norm(np.diff(outer_loop, axis=0), axis=1).sum())
        collar_area = _polygon(outer_loop, [0, 2])[0]
        plates.append(dict(name=f'mount_plate_{side}', y_m=y, bores=bores,
            bolt_bores=bolts, thickness_m=float(np.ptp(mesh.bounds[:, 1])), net_sections=sections,
            collar_bond_section=bond_section, collar_bond_length_m=bond_length,
            collar_perimeter_m=perimeter, collar_enclosed_area_m2=collar_area,
            lower_edge_m=float(mesh.bounds[0, 2])))
    keel = meshes['fuselage_keel']
    keel_bores = sorted(_bores(keel, float(np.mean(keel.bounds[:, 1]))), key=lambda b: b['center_xz_m'][0])
    if len(keel_bores) != 2:
        raise ValueError('Missing keel fastener bores')
    for i in range(2):
        mesh = meshes[f'mount_rivet_{i}']
        x, z = keel_bores[i]['center_xz_m']
        center = np.array([x, 0., z])
        section = geo.section(mesh, center, np.array([0., 1., 0.]))
        radius_i = section['extreme_radius_m']
        for plate in plates:
            bore = plate['bolt_bores'][i]
            if (np.linalg.norm(np.array(bore['center_xz_m'])-[x, z]) > geo.TOL
                    or abs(bore['radius_m']-radius_i) > geo.TOL):
                raise ValueError('Disconnected plate-to-rivet interface')
        if abs(keel_bores[i]['radius_m']-radius_i) > geo.TOL:
            raise ValueError('Disconnected rivet-to-keel interface')
        rivets.append(dict(name=f'mount_rivet_{i}', x_m=float(x), z_m=float(z), section=section))
    return dict(method=MODE, attachment_sha256=expected['revision_sha256'],
        collar_section=collar_section, plates=plates, rivets=rivets,
        keel_width_m=float(np.ptp(keel.bounds[:, 1])),
        keel_bounds_z_m=keel.bounds[:, 2].tolist(),
        contact_state='canonical_conforming_bearing_interfaces')


def pin_bearing(plane_forces_n, plate_bounds_y_m, keel_width_m):
    """Free-ended pin with uniform plate bearing and affine keel bearing.

    Keel bearing balances both force AND asymmetric roll moment. A constant
    keel reaction would miss the couple. Integrate the piecewise linear load
    exactly; bending extrema include every zero of shear inside each region.
    This is a declared bearing-distribution model, not an elasticity solution.
    """
    p = np.asarray(plane_forces_n, dtype=float)
    bounds = np.asarray(plate_bounds_y_m, dtype=float)
    w = keel_width_m
    if (p.shape != (2,) or bounds.shape != (2, 2) or not np.isfinite([*p, *bounds.flat, w]).all()
            or w <= 0 or np.any(np.diff(bounds, axis=1) <= 0)
            or abs(bounds[0, 1]+w/2) > geo.TOL or abs(bounds[1, 0]-w/2) > geo.TOL):
        raise ValueError('Invalid measured pin bearing regions')
    force = float(p.sum())
    moment = float(np.dot(p, bounds.mean(axis=1)))
    q0, slope = -force/w+6*moment/w**2, -12*moment/w**3
    intervals = [(bounds[0, 0], bounds[0, 1], p[0]/np.ptp(bounds[0]), 0.),
                 (-w/2, w/2, q0, slope),
                 (bounds[1, 0], bounds[1, 1], p[1]/np.ptp(bounds[1]), 0.)]
    shear = bending = maximum_shear = maximum_bending = 0.
    for lo, hi, load, gradient in intervals:
        length = hi-lo
        times = [0., length]
        if gradient != 0:
            roots = np.roots([gradient/2, load, shear])
        elif load != 0:
            roots = [-shear/load]
        else:
            roots = []
        times += [float(np.real(t)) for t in roots if abs(np.imag(t)) < 1e-12 and 0 < np.real(t) < length]
        for t in times:
            maximum_bending = max(maximum_bending, abs(bending+shear*t+load*t*t/2+gradient*t**3/6))
        shear_times = [0., length]
        if gradient != 0 and 0 < -load/gradient < length:
            shear_times.append(-load/gradient)
        maximum_shear = max(maximum_shear, *(abs(shear+load*t+gradient*t*t/2) for t in shear_times))
        bending += shear*length+load*length**2/2+gradient*length**3/6
        shear += load*length+gradient*length**2/2
    if max(abs(shear), abs(bending)) > 1e-9*max(1., float(np.abs(p).sum())):
        raise ValueError('Pin distributed reactions violate equilibrium')
    q1 = q0+slope*w
    # Integral of |affine bearing|, including a possible contact-sign reversal.
    if q0*q1 >= 0:
        absolute_force = w*(abs(q0)+abs(q1))/2
    else:
        absolute_force = (q0*q0+q1*q1)/(2*abs(slope))
    return dict(max_shear_n=float(maximum_shear), max_bending_nm=float(maximum_bending),
        keel_peak_line_load_n_m=float(max(abs(q0), abs(q1))),
        keel_absolute_force_n=float(absolute_force),
        force_residual_n=float(shear), moment_residual_nm=float(bending))


def analyze(left, right, *, geometry_sha256, operating_sha256, half_span_m, interfaces):
    """Equal-stiffness, rigid four-contact reaction model; signed equilibrium.

    Left and right inputs both use GLOBAL +Y torque. Positive Fz on the left
    produces negative global Mx. Positive My needs opposite fore/aft reactions.
    No assumed symmetry discards asymmetric roll loads. Missing closure keeps
    the assembly incomplete even when every supported nominal check passes.
    """
    from .physical_loads import InstalledLoadCase
    for case in (left, right):
        InstalledLoadCase.model_validate(case.model_dump())
        if (case.geometry_sha256 != geometry_sha256 or case.operating_sha256 != operating_sha256
                or abs(case.stations[-1].y_m-half_span_m) > geo.TOL):
            raise ValueError('Stale or incomplete installed wing loads')
    plates, rivets = interfaces['plates'], interfaces['rivets']
    if len(plates) != 2 or len(rivets) != 2 or plates[0]['y_m'] >= 0 or plates[1]['y_m'] <= 0:
        raise ValueError('Missing distinct installed support interfaces')
    xref = float(np.mean([r['x_m'] for r in rivets]))
    xs = np.array([r['x_m']-xref for r in rivets])
    ys = np.array([p['y_m'] for p in plates])
    if (not np.isfinite([*xs, *ys, half_span_m]).all() or half_span_m <= 0
            or not np.isclose(ys.sum(), 0., atol=geo.TOL) or min(np.abs(xs)) <= geo.TOL):
        raise ValueError('Unsupported asymmetric connector geometry')
    a, b = resultants(left, 0.), resultants(right, 0.)
    force = a['shear_n']+b['shear_n']
    roll = b['bending_nm']-a['bending_nm']
    pitch = a['torque_nm']+b['torque_nm']
    reactions = np.array([[-force/4-roll*y/(2*np.dot(ys, ys))+pitch*x/(2*np.dot(xs, xs))
                           for x in xs] for y in ys])
    residual = [force+float(reactions.sum()), roll+float(np.sum(reactions*ys[:, None])),
                pitch-float(np.sum(reactions*xs[None, :]))]
    if not np.isfinite(reactions).all() or max(abs(v) for v in residual) > 1e-9*max(1., abs(force), abs(roll), abs(pitch)):
        raise ValueError('Attachment reactions do not balance applied loads')
    sf = MATERIALS['safety_factor']
    checks, details = {}, []
    for i, rivet in enumerate(rivets):
        radius = rivet['section']['extreme_radius_m']
        applied = np.abs(reactions[:, i])
        pin = pin_bearing(-reactions[:, i],
            [[p['y_m']-p['thickness_m']/2, p['y_m']+p['thickness_m']/2] for p in plates],
            interfaces['keel_width_m'])
        shear = 4/3*pin['max_shear_n']/rivet['section']['area_m2']
        bending = pin['max_bending_nm']*radius/rivet['section']['min_inertia_m4']
        keel_force_bound = pin['keel_absolute_force_n']
        keel_bearing = pin['keel_peak_line_load_n_m']/(2*radius)
        z = rivet['z_m']
        keel_edge = min(z-interfaces['keel_bounds_z_m'][0], interfaces['keel_bounds_z_m'][1]-z)-radius
        if keel_edge <= 0:
            raise ValueError('Missing keel tear-out ligament')
        keel_tearout = keel_force_bound/(2*keel_edge*interfaces['keel_width_m'])
        checks[f'rivet_{i}_shear'] = shear <= MATERIALS['rivet']['shear_pa']/sf
        checks[f'rivet_{i}_bending'] = bending <= MATERIALS['rivet']['normal_pa']/sf
        checks[f'keel_{i}_bearing'] = keel_bearing <= MATERIALS['keel']['bearing_pa']/sf
        checks[f'keel_{i}_tearout'] = keel_tearout <= MATERIALS['keel']['shear_pa']/sf
        for j, plate in enumerate(plates):
            plate_bearing = applied[j]/(2*radius*plate['thickness_m'])
            edge = z-plate['lower_edge_m']-radius
            if edge <= 0:
                raise ValueError('Missing mount plate ligament')
            tearout = applied[j]/(2*edge*plate['thickness_m'])
            checks[f'plate_{j}_{i}_bearing'] = bool(plate_bearing <= MATERIALS['plate']['bearing_pa']/sf)
            checks[f'plate_{j}_{i}_tearout'] = bool(tearout <= MATERIALS['plate']['shear_pa']/sf)
        details.append(dict(name=rivet['name'], plane_forces_n=reactions[:, i].tolist(),
            shear_pa=shear, bending_bound_pa=bending, pin_bearing=pin, keel_bearing_bound_pa=keel_bearing,
            keel_tearout_bound_pa=keel_tearout))
    for j, plate in enumerate(plates):
        f = float(abs(reactions[j].sum()))
        m = float(abs(np.dot(reactions[j], xs)))
        stresses = [f/s['area_m2']+m*s['extreme_x_m']/s['inertia_y_m4'] for s in plate['net_sections']]
        checks[f'plate_{j}_net_section'] = max(stresses) <= MATERIALS['plate']['normal_pa']/sf
        # A plain round bearing cannot transmit My. The explicit annular bond
        # supplies a declared nominal torque path without crediting friction.
        normal = f/(plate['collar_perimeter_m']*plate['collar_bond_length_m'])
        shear = normal+m/(2*plate['collar_enclosed_area_m2']*plate['collar_bond_length_m'])
        checks[f'collar_bond_{j}_normal'] = normal <= MATERIALS['bond']['normal_pa']/sf
        checks[f'collar_bond_{j}_shear'] = shear <= MATERIALS['bond']['shear_pa']/sf
    checks = {k: bool(v) for k, v in checks.items()}
    return dict(state='incomplete' if all(checks.values()) else 'failed',
        supported_checks_state='passed' if all(checks.values()) else 'failed',
        checks=checks, applied=dict(fz_n=force, mx_nm=roll, my_nm=pitch),
        reactions_n=reactions.tolist(), equilibrium_residual=residual, rivets=details,
        load_sha256=digest(dict(left=left.model_dump(mode='json'), right=right.model_dump(mode='json'))),
        attachment_sha256=interfaces['attachment_sha256'], material_priors=copy.deepcopy(MATERIALS),
        manufacturing_ready=False, load_path_state='partial',
        unsupported=['collar/plate bond peel, elasticity and local stress concentrations',
                     'rivet clearance, fatigue, head pull-through and axial loading',
                     'keel-to-rest-of-fuselage transfer and stiffness',
                     'foam transfer and adhesive peel', 'validated aerodynamic loads and delivered trim'])
