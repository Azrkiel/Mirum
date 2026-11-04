"""Fixture-only bonded straight-spar five-station wing kit for Task B.

Named envelope: flat, unswept, untwisted symmetric NACA section, straight solid
stock rod with a full-span adhesive annulus and eight foam panels. A bonded
root test-fixture sleeve supplies the declared fixed boundary. It is NOT a
fuselage attachment. No discovery/UI construction mode is enabled by this kit.
"""
from pathlib import Path
import hashlib
import math

import numpy as np

from cem.core import digest
from gliders.model.export import write_stl
from . import segmented_geometry as geo
from .foam_core import FOAM
from .geometry import closed_mesh, surface_rings, load_part_mesh, measure_part
from .physical_loads import RootSupport
from .shapes import ShapedWing

MODE = 'bonded_straight_shaped_wing_fixture_v1'
ADHESIVE = dict(allowable_shear_pa=8e6)
CLAMP = dict(density_kg_m3=1850., wall_m=.001, span_fraction=.275)
DENSITIES = dict(foam=35., spar=1600., adhesive=1100., clamp=1850.)


def construct(wing, diameter_m=.004):
    if (not isinstance(wing, ShapedWing) or wing.dihedral_deg != 0
            or wing.sweep_qc_deg != 0 or wing.incidence_deg != 0 or wing.washout_deg != 0
            or wing.style != 'polyhedral' or wing.naca != '0012'
            or not .6 <= wing.span <= 1.4 or not .10 <= wing.root_chord <= .16
            or not .7 <= wing.taper <= 1.
            or not math.isfinite(diameter_m) or not .002 <= diameter_m <= .008):
        raise ValueError('Outside named flat symmetric straight-spar fixture envelope')
    rings = surface_rings(wing.surface())
    point = np.array([.3*wing.root_chord, 0., 0.])
    radius = diameter_m/2
    # Fill a polygonal bore around the rod with adhesive; no clearance is labelled
    # bonded. Both surfaces use a common 64-gon phase.
    outer_radius = (radius+FOAM['radial_clearance_m'])/math.cos(math.pi/64)
    clamp_radius = outer_radius+CLAMP['wall_m']
    overlap = float(rings[1][0, 1])
    parts, interfaces = {}, []
    for side in (-1, 1):
        for i, (a, b) in enumerate(zip(rings, rings[1:])):
            outer = closed_mesh([a*np.array([1., side, 1.]), b*np.array([1., side, 1.])])
            # Centre panels are bored for the clamp, which replaces foam.
            # Elsewhere the bore is filled exactly by the adhesive annulus.
            bore_radius = clamp_radius if i == 0 else outer_radius
            ends = np.tile(point, (2, 1)); ends[:, 1] = outer.bounds[:, 1]
            axis = np.array([0., 1., 0.]); cuts = np.array([axis, axis])
            core, _ = geo.core(outer, ends, cuts, axis, bore_radius, FOAM['min_web_m'],
                              [dict(mesh=outer, points=ends, normals=cuts)])
            # The core inserts contour-alignment rays on its circle. Snap
            # only those bore vertices onto the shared regular polygon so its
            # entire boundary coincides with the mating material (no tiny
            # unbonded crescent gaps between differently tessellated circles).
            delta = core.vertices[:, [0, 2]]-point[[0, 2]]
            radial = np.linalg.norm(delta, axis=1)
            mask = np.abs(radial-bore_radius) < 1e-10
            angle = np.arctan2(delta[mask, 1], delta[mask, 0])
            step = 2*math.pi/64
            offset = np.mod(angle, step)-step/2
            factor = math.cos(step/2)/np.cos(offset)
            vertices = core.vertices.copy()
            vertices[np.ix_(mask, [0, 2])] = point[[0, 2]]+delta[mask]*factor[:, None]
            core.vertices = vertices
            if np.ptp(core.vertices[:, 2]) > FOAM['max_stock_thickness_m']+geo.TOL:
                raise ValueError('Delivered foam exceeds named stock thickness')
            name = f'foam_{side}_{i}'
            parts[name] = dict(mesh=core, material=dict(law='solid', density_kg_m3=DENSITIES['foam']))
            interfaces.append(dict(name=name, bounds_y_m=outer.bounds[:, 1].tolist()))
    ends = np.array([point+[0., -wing.span/2, 0.], point+[0., wing.span/2, 0.]])
    normal = np.array([0., 1., 0.])
    for name, outer, inner, density in [('spar', radius, 0., DENSITIES['spar']),
            ('adhesive', outer_radius, radius, DENSITIES['adhesive'])]:
        parts[name] = dict(mesh=geo.tube(ends, [normal, normal], [normal], outer, inner, count=64),
                           material=dict(law='solid', density_kg_m3=density))
    # The root test clamp replaces the centre panels' bored-out material. Its
    # fixture constraint is external; no fuselage attachment is implied.
    ends = np.array([point+[0., -overlap, 0.], point+[0., overlap, 0.]])
    parts['clamp'] = dict(mesh=geo.tube(ends, [normal, normal], [normal],
        clamp_radius, outer_radius, count=64),
        material=dict(law='solid', density_kg_m3=DENSITIES['clamp']))
    revision = digest(dict(mode=MODE, wing=wing.__dict__, diameter_m=diameter_m,
                           foam=FOAM, adhesive=ADHESIVE, clamp=CLAMP, densities=DENSITIES))
    return dict(mode=MODE, geometry_sha256=revision, parts=parts, interfaces=interfaces,
                radius_m=radius, half_span_m=wing.span/2, point=point)


def export_fixture(assembly, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError('Fixture exports require an empty directory')
    items = []
    for name, item in assembly['parts'].items():
        path = directory/(name+'.stl')
        mesh = item['mesh']
        write_stl(str(path), mesh.vertices, mesh.faces)
        items.append(dict(name=name, file=path.name, material=item['material'],
                          sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return dict(mode=MODE, geometry_sha256=assembly['geometry_sha256'], parts=items)


def verify_fixture(wing, diameter_m, manifest, directory):
    """Reconstruct revision; import actual bytes; prove surfaces and material laws.

    This isolated fixture measurement is deliberately insufficient for physical
    closure: fixture reactions do not establish a fuselage attachment.
    """
    expected = construct(wing, diameter_m)
    if manifest['mode'] != MODE or manifest['geometry_sha256'] != expected['geometry_sha256']:
        raise ValueError('Mismatched construction revision')
    directory = Path(directory)
    items = manifest['parts']
    names = set(expected['parts'])
    if len(items) != len(names) or {i['name'] for i in items} != names:
        raise ValueError('Missing/duplicate/disconnected material inventory')
    if {p.name for p in directory.glob('*.stl')} != {n+'.stl' for n in names}:
        raise ValueError('Unaccounted delivered geometry')
    measured, meshes = [], {}
    for item in items:
        name = item['name']
        if item['file'] != name+'.stl' or item['material'] != expected['parts'][name]['material']:
            raise ValueError('Invalid delivered path or material law')
        path = directory/item['file']
        mesh = load_part_mesh(path)
        geo.same_surface(mesh, expected['parts'][name]['mesh'])
        props = measure_part(path, item['material'])
        if props['sha256'] != item['sha256']:
            raise ValueError('Delivered artifact hash mismatch')
        meshes[name] = mesh
        measured.append(dict(name=name, **props))
    spar = meshes['spar']
    point, normal = expected['point'], np.array([0., 1., 0.])
    section = geo.section(spar, point, normal)
    # Recover bond and root-support extents from delivered material, not labels.
    bond = geo.section(meshes['adhesive'], point, normal, hollow=True)
    geo.section(meshes['clamp'], point, normal, hollow=True)
    overlap = min(-meshes['clamp'].bounds[0, 1], meshes['clamp'].bounds[1, 1])
    support = RootSupport(overlap_m=float(overlap), radius_m=section['extreme_radius_m'],
        bond_allowable_shear_pa=ADHESIVE['allowable_shear_pa'], safety_factor=2.)
    # Independent material-volume closure against the original station lofts.
    # Surface correspondence above prevents volume cancellation from accepting
    # shifted parts, plugs or hidden voids with a coincidentally correct volume.
    rings = surface_rings(wing.surface())
    oml_volume = 2*sum(closed_mesh([a, b]).volume for a, b in zip(rings, rings[1:]))
    material_volume = sum(p['volume_m3'] for p in measured)
    if abs(material_volume/oml_volume-1.) > 1e-5:
        raise ValueError('Delivered material partition differs from wing envelope')
    wing_parts = measured
    mass = sum(p['mass_kg'] for p in wing_parts)
    cg = sum(p['mass_kg']*np.array(p['cg_m']) for p in wing_parts)/mass
    inertia = np.zeros((3, 3))
    for p in wing_parts:
        d = np.array(p['cg_m'])-cg
        inertia += np.array(p['inertia_kg_m2'])+p['mass_kg']*(np.dot(d, d)*np.eye(3)-np.outer(d, d))
    return dict(state='incomplete', geometry_sha256=expected['geometry_sha256'],
                measured_parts=measured, wing_mass_kg=float(mass), wing_cg_m=cg.tolist(),
                wing_inertia_kg_m2=inertia.tolist(), measured_section=section,
                measured_bond_section=bond, measured_support=support.model_dump(),
                material_volume_relative_error=float(abs(material_volume/oml_volume-1.)),
                manufacturing_ready=False, engineering_certified=False,
                blockers=['Root fixture is not an installed fuselage mount.',
                          'Foam-to-adhesive load transfer and peel remain unqualified.',
                          'Task A validated loads and delivered-aircraft trim are unavailable.'])
