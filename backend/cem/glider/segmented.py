"""Installed polyhedral spars and explicit sleeves/bonds under engineering priors.

This is a material-geometry and load-case screen. Assumed coupon strengths do
not qualify joints, foam load transfer, a wing saddle, fatigue or manufacture.
"""
import math
from functools import lru_cache

import numpy as np

from . import segmented_geometry as geo
from .foam_core import FOAM, density
from .processes import CARBON, LAUNCH_LOAD_FACTORS, RODS_M
from .structure import bending_moment

MODE = 'segmented_foam_wing_v1'
METHOD = 'delivered-polyhedral-segments-and-joints-v1'
JOINT = dict(density_kg_m3=1850., allowable_pa=250e6, safety_factor=2.,
             wall_m=.0006, overlap_m=.030, stress_concentration_factor=2.)
BOND = dict(density_kg_m3=1100., allowable_shear_pa=8e6, safety_factor=2., thickness_m=.00015)


def enabled(gl):
    return gl.cfg.get('cem_construction') == MODE


def reference_wings(gl):
    from .foam_core import outer_meshes
    return outer_meshes(gl)


def _camber(code, u=.3):
    m, p = int(code[0])/100, int(code[1])/10
    if not m:
        return 0.
    return m/p**2*(2*p*u-u*u) if u < p else m/(1-p)**2*(1-2*p+2*p*u-u*u)


@lru_cache(maxsize=64)
def _construct(code, sections, diameter, foam_density):
    from gliders.model.components import Surface, Section, naca
    from .geometry import surface_meshes, surface_rings, closed_mesh
    surface = Surface('wing', [Section(*s) for s in sections], True, code, 'wing')
    rings = surface_rings(surface)
    if len(rings) != 3 or abs(surface.sections[0].y) > geo.TOL:
        raise ValueError('Segmented construction requires the three-station polyhedral layout')
    # The existing centre panel must be flat at the root so the two reference
    # half-wing mould lines meet without an invented gap or repaired union.
    if abs(surface.sections[1].z-surface.sections[0].z) > geo.TOL:
        raise ValueError('Segmented construction requires a flat centre panel')
    af = naca(code).repanel(n_points_per_side=33).coordinates
    if np.linalg.norm(af[0]-af[-1]) < 1e-10:
        af = af[:-1]
    centres = []
    for ring in rings:
        mapping = np.linalg.lstsq(np.column_stack([np.ones(len(af)), af]), ring, rcond=None)[0]
        centres.append(np.array([1., .3, _camber(code)])@mapping)
    points = [p*np.array([1, -1, 1]) for p in centres[:0:-1]]+centres
    points = np.asarray(points)
    axes = np.asarray([geo.unit(b-a) for a, b in zip(points, points[1:])])
    tip_normal = geo.unit(np.cross(rings[-1][10]-rings[-1][0], rings[-1][30]-rings[-1][0]))
    normals = [tip_normal*np.array([1, -1, 1])]
    normals += [geo.unit(a+b) for a, b in zip(axes, axes[1:])]
    normals.append(tip_normal)
    normals = np.asarray(normals)
    for i in range(len(normals)):
        if np.dot(normals[i], axes[min(i, len(axes)-1)]) < 0:
            normals[i] *= -1
    # Transport one polygon orientation through the entire chain. Rods, bond
    # holes and sleeve bores then share the same interfaces at every lap;
    # independently chosen polygon phases could create tiny material overlaps.
    frames = [geo.basis(axes[0])]
    for j in range(1, len(axes)):
        v, n = axes[j-1], normals[j]
        ellipse = frames[-1]-np.outer(v, n@frames[-1])/np.dot(n, v)
        frame = (np.eye(3)-np.outer(axes[j], axes[j]))@ellipse
        if not np.allclose(frame.T@frame, np.eye(2), atol=1e-10):
            raise ValueError('Cannot transport the stock section through the joint')
        frames.append(frame)
    refs = surface_meshes(surface)
    # Original loft cells share the original airfoil-section planes. Their
    # union, unlike a single miter-cut panel's convex kernel, covers each OML.
    cells = []
    for sign in (-1., 1.):
        for k, (a, b) in enumerate(zip(rings, rings[1:])):
            aa, bb = [r*np.array([1., sign, 1.]) for r in (a, b)]
            ends = [centres[k]*np.array([1., sign, 1.]), centres[k+1]*np.array([1., sign, 1.])]
            cuts = [geo.unit(np.cross(r[10]-r[0], r[30]-r[0])) for r in (aa, bb)]
            for j in range(2):
                if np.dot(cuts[j], ends[1]-ends[0]) < 0:
                    cuts[j] *= -1
            cells.append(dict(mesh=closed_mesh([aa, bb]), points=np.asarray(ends), normals=np.asarray(cuts)))
    radius = diameter/2
    sleeve_inner = radius+BOND['thickness_m']
    sleeve_outer = sleeve_inner+JOINT['wall_m']
    channel_radius = (sleeve_outer+FOAM['radial_clearance_m'])/math.cos(math.pi/64)
    parts, panels, joints = {}, [], []
    for i, axis in enumerate(axes):
        ends, cuts = points[i:i+2], normals[i:i+2]
        length = float(np.linalg.norm(ends[1]-ends[0]))
        if length < 2*JOINT['overlap_m']+.01:
            raise ValueError('Panel is too short for nonoverlapping joint laps')
        outer = geo.panel_outer(refs[1 if i < 2 else 0], ends, cuts)
        core, web = geo.core(outer, ends, cuts, axis, channel_radius, FOAM['min_web_m'], cells[:2] if i < 2 else cells[2:])
        t = math.radians(surface.sections[0].twist)
        chord = np.array([math.cos(t), 0., -math.sin(t)])
        thin = geo.unit(np.cross(axis, chord))
        stock = float(np.ptp(core.vertices@thin))
        if stock > FOAM['max_stock_thickness_m']+geo.TOL:
            raise ValueError('Segmented material panel exceeds foam stock thickness')
        rod = geo.tube(ends, cuts, [axis], radius, frames=[frames[i]])
        parts[f'wing_panel_{i}'] = dict(mesh=core, density=foam_density)
        parts[f'spar_panel_{i}'] = dict(mesh=rod, density=CARBON['density_kg_m3'])
        panels.append(dict(points=ends, normals=cuts, axis=axis, outer=outer,
                           web_m=web, stock_thickness_m=stock, channel_radius_m=channel_radius,
                           radius_m=radius, thin_axis=thin, side=-1 if i < 2 else 1))
    for j in range(1, len(points)-1):
        overlap = JOINT['overlap_m']
        ends = [points[j]-overlap*axes[j-1], points[j], points[j]+overlap*axes[j]]
        cuts = [axes[j-1], normals[j], axes[j]]
        sleeve = geo.tube(ends, cuts, axes[j-1:j+1], sleeve_outer, sleeve_inner, frames=frames[j-1:j+1])
        bond = geo.tube(ends, cuts, axes[j-1:j+1], sleeve_inner, radius, frames=frames[j-1:j+1])
        parts[f'joint_{j-1}'] = dict(mesh=sleeve, density=JOINT['density_kg_m3'])
        parts[f'bond_{j-1}'] = dict(mesh=bond, density=BOND['density_kg_m3'])
        joints.append(dict(point=points[j], axes=axes[j-1:j+1], overlap_m=overlap,
                           radius_m=radius, outer_radius_m=sleeve_outer, inner_radius_m=sleeve_inner))
    return dict(parts=parts, panels=panels, joints=joints, points=points, axes=axes,
                diameter_m=diameter, channel_radius_m=channel_radius, cells=cells)


def construct(gl, diameter):
    if gl.wing.style != 'polyhedral':
        raise ValueError('Segmented construction supports polyhedral wings only')
    s = gl.wing_surface()
    return _construct(s.airfoil, tuple((a.x, a.y, a.z, a.chord, a.twist) for a in s.sections),
                      diameter, density(gl))


def material_inventory(gl):
    # Inventory does not depend on worker-supplied files or a successfully
    # selected diameter. Only the bounded layout determines physical names.
    result = {}
    if gl.wing.style != 'polyhedral':
        raise ValueError('Unsupported segmented part inventory')
    for i in range(4):
        result[f'wing_panel_{i}'] = dict(law='solid', density_kg_m3=density(gl))
        result[f'spar_panel_{i}'] = dict(law='solid', density_kg_m3=CARBON['density_kg_m3'])
    for i in range(3):
        result[f'joint_{i}'] = dict(law='solid', density_kg_m3=JOINT['density_kg_m3'])
        result[f'bond_{i}'] = dict(law='solid', density_kg_m3=BOND['density_kg_m3'])
    return result


def load_envelope(assembly, mass, span, method, y):
    """Conservative bounds for every outboard station, under ideal elliptical lift.

    The maximum |dx/dy| bounds the swept-path moment about Y. Moments from
    vertical lift do not depend on z. These bounds decrease toward the tip;
    the inboard endpoint therefore bounds an entire panel, not just samples.
    """
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError('Missing converged structural mass')
    u = min(1., abs(float(y))/(span/2))
    moment = bending_moment(mass, span, method, u*span/2)
    slope = max(abs((b[0]-a[0])/(b[1]-a[1])) for a, b in zip(assembly['points'], assembly['points'][1:]))
    shear = LAUNCH_LOAD_FACTORS[method]*mass*9.81/math.pi*(math.acos(u)-u*math.sqrt(max(0., 1-u*u)))
    return moment, shear, slope


def _stress(properties, moment, shear, slope, axis, hollow=False, concentration=1.):
    radius = properties['extreme_radius_m']
    bending = moment*math.sqrt(1+slope*slope)
    torsion = moment*(abs(axis[0])+slope*abs(axis[1]))
    sigma = bending*radius/properties['min_inertia_m4']
    # Section mechanics remain an engineering screen for the near-circular
    # polygon, not a Saint-Venant/FEA qualification. A 10% torsion allowance
    # and declared joint concentration factor are retained explicitly.
    tau = 1.1*torsion*radius/properties['polar_inertia_m4']
    tau += (2. if hollow else 4/3)*shear/properties['area_m2']
    return concentration*math.sqrt(sigma*sigma+3*tau*tau)


def structural_checks(assembly, meshes, mass, span, method):
    panels, joints, failures = [], [], []
    for i, panel in enumerate(assembly['panels']):
        p, axis = panel['points'], panel['axis']
        mesh = meshes[f'spar_panel_{i}']
        section = geo.section(mesh, np.mean(p, axis=0), axis)
        stations = []
        for point in np.linspace(p[0], p[1], 17):
            moment, shear, slope = load_envelope(assembly, mass, span, method, point[1])
            stress = _stress(section, moment, shear, slope, axis)
            stations.append(dict(y_m=float(point[1]), moment_bound_nm=moment*math.sqrt(1+slope*slope),
                                 shear_bound_n=shear, equivalent_stress_bound_pa=stress))
        maximum = max(s['equivalent_stress_bound_pa'] for s in stations)
        allowable = CARBON['allowable_pa']/CARBON['safety_factor']
        if maximum > allowable*(1+1e-5):
            failures.append(f'spar_panel_{i} fails bounded launch bending/shear/torsion')
        panels.append(dict(name=f'spar_panel_{i}', section=section, stations=stations,
                           max_stress_pa=maximum, allowable_with_sf_pa=allowable))
    for i, joint in enumerate(assembly['joints']):
        sleeve, bond = meshes[f'joint_{i}'], meshes[f'bond_{i}']
        point, axes = joint['point'], joint['axes']
        moment, shear, slope = load_envelope(assembly, mass, span, method, point[1])
        laps = []
        for sign, axis in zip((-1, 1), axes):
            # Actual material extents and actual annular sections, not the
            # declared overlap or an ideal-circle section substituted for STL.
            overlap = (-float(np.min((sleeve.vertices-point)@axis)) if sign < 0 else
                       float(np.max((sleeve.vertices-point)@axis)))
            bond_overlap = (-float(np.min((bond.vertices-point)@axis)) if sign < 0 else
                            float(np.max((bond.vertices-point)@axis)))
            overlap = min(overlap, bond_overlap)
            if overlap < JOINT['overlap_m']-geo.TOL:
                raise ValueError('Delivered sleeve/bond overlap is incomplete')
            origin = point+sign*axis*overlap/2
            section = geo.section(sleeve, origin, axis, hollow=True)
            stress = _stress(section, moment, shear, slope, axis,
                             hollow=True, concentration=JOINT['stress_concentration_factor'])
            # Sinusoidal axial bond shear transfers bending around a cylinder;
            # add direct shear conservatively. Peel/defects/foam are unevaluated.
            r = min(panels[i]['section']['extreme_radius_m'], panels[i+1]['section']['extreme_radius_m'])*math.cos(math.pi/48)
            bond_shear = moment*math.sqrt(1+slope*slope)/(math.pi*r*r*overlap)+shear/(2*math.pi*r*overlap)
            geo.section(bond, origin, axis, hollow=True)
            if stress > JOINT['allowable_pa']/JOINT['safety_factor']*(1+1e-5):
                failures.append(f'joint_{i} fails assumed sleeve strength')
            if bond_shear > BOND['allowable_shear_pa']/BOND['safety_factor']*(1+1e-5):
                failures.append(f'bond_{i} fails assumed lap shear')
            laps.append(dict(overlap_m=overlap, section=section, sleeve_stress_pa=stress,
                             bond_shear_pa=bond_shear, interface_radius_m=r))
        joints.append(dict(name=f'joint_{i}', bond_name=f'bond_{i}', laps=laps,
                           qualification_state='engineering_priors_not_test_qualified'))
    return dict(state='failed' if failures else 'passed', failures=failures, method=METHOD,
                mass_kg=float(mass), panels=panels, joints=joints, carbon=dict(CARBON),
                sleeve_assumptions=dict(JOINT), adhesive_assumptions=dict(BOND),
                load_path_state='unverified', manufacturing_ready=False,
                limitations=['Ideal elliptical total lift and bounded swept-path beam loads; no tail loads.',
                             'Coupon strength, bond peel/defects, foam transfer, wing saddle, buckling, deflection and fatigue unqualified.'])


def size_spar(gl, base_items):
    base_mass = sum(p.mass for p in base_items if p.name != 'wing')
    last = 'No catalogue section satisfies the construction/load envelope'
    for i, diameter in enumerate(RODS_M):
        try:
            assembly = construct(gl, diameter)
        except ValueError as exc:
            last = str(exc)
            continue
        mass = base_mass+sum(p['mesh'].volume*p['density'] for p in assembly['parts'].values())
        checks = structural_checks(assembly, {n: p['mesh'] for n, p in assembly['parts'].items()},
                                   mass, gl.wing.span, gl.cfg['cem_launch_method'])
        if checks['state'] != 'passed':
            last = '; '.join(checks['failures']); continue
        maximum = max(p['max_stress_pa'] for p in checks['panels'])
        return dict(diameter_m=diameter, span_m=gl.wing.span, sizing_mass_kg=float(mass),
                    sizing_iterations=i+1, mass_kg=sum(p['mesh'].volume*p['density'] for n, p in assembly['parts'].items() if n.startswith('spar_')),
                    stress_pa=maximum, allowable_with_sf_pa=CARBON['allowable_pa']/CARBON['safety_factor'],
                    root_moment_nm=bending_moment(mass, gl.wing.span, gl.cfg['cem_launch_method'], 0.),
                    section_model=METHOD)
    raise ValueError('No segmented catalogue assembly admitted: '+last)


def selected(gl):
    return construct(gl, gl.spar()['diameter_m'])


def verify_delivery(gl, meshes, mass):
    assembly = selected(gl)
    if set(meshes) != set(assembly['parts']):
        raise ValueError('Missing segmented core/rod/joint/bond accounting')
    for name, item in assembly['parts'].items():
        geo.same_surface(meshes[name], item['mesh'])
    channel_gaps, stock = [], []
    sleeve_radius = assembly['diameter_m']/2+BOND['thickness_m']+JOINT['wall_m']
    for i, panel in enumerate(assembly['panels']):
        core = meshes[f'wing_panel_{i}']; p = panel['points']; axis = panel['axis']
        delta = core.vertices-p[0]
        radial = np.linalg.norm(delta-np.outer(delta@axis, axis), axis=1)
        inner = np.all(np.abs(radial[core.faces]-panel['channel_radius_m']) <= geo.TOL, axis=1)
        if np.count_nonzero(inner) < 128:
            raise ValueError('Missing complete delivered segment channel')
        n, vertex = core.face_normals[inner], core.triangles[inner, 0]
        gap = float(np.min(np.min(p@n.T, axis=0)-np.einsum('ij,ij->i', n, vertex)))-sleeve_radius
        if gap < FOAM['radial_clearance_m']-geo.TOL:
            raise ValueError('Delivered channel leaves insufficient sleeve clearance')
        channel_gaps.append(gap)
        thickness = float(np.ptp(core.vertices@panel['thin_axis']))
        if thickness > FOAM['max_stock_thickness_m']+geo.TOL:
            raise ValueError('Delivered panel exceeds foam stock thickness')
        stock.append(thickness)
        for name, mesh in meshes.items():
            if name.startswith('wing_'):
                continue
            clipped = mesh
            try:
                for point, normal in [(p[0], panel['normals'][0]), (p[1], -panel['normals'][1])]:
                    clipped = geo.clip_mesh(clipped, point, normal)
            except ValueError as exc:
                if str(exc) == 'Empty clipped material panel': continue
                raise
            delta = clipped.vertices-p[0]
            radial = np.linalg.norm(delta-np.outer(delta@axis, axis), axis=1)
            if float(np.max(radial)) > sleeve_radius+geo.TOL:
                raise ValueError('Installed rod/sleeve/bond is outside its segment channel')
    clearances = []
    # Test entire clipped triangles against the original cell union. All
    # physical rod, sleeve and bond surfaces are covered, including junctions.
    for sign, cells in [(-1, assembly['cells'][:2]), (1, assembly['cells'][2:])]:
        for name, mesh in meshes.items():
            if name.startswith('wing_'):
                continue
            try:
                half = geo.clip_mesh(mesh, np.array([0., 0., 0.]), np.array([0., sign, 0.]))
            except ValueError as exc:
                if str(exc) == 'Empty clipped material panel': continue
                raise
            gap = geo.cell_clearance(half, cells)
            if gap < FOAM['min_web_m']-geo.TOL:
                raise ValueError('Installed segmented structure leaves the wing envelope')
            clearances.append(gap)
    checks = structural_checks(assembly, meshes, mass, gl.wing.span, gl.cfg['cem_launch_method'])
    return dict(**checks, min_outer_clearance_m=min(clearances),
                min_channel_clearance_m=min(channel_gaps),
                min_foam_web_m=min(p['web_m'] for p in assembly['panels']),
                stock_thickness_m=stock,
                outer_shape_state='partition_of_reference_oml')
