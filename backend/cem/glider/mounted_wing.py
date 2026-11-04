"""Delivered shaped wing, collar, bearing plates, rivets and local fuselage keel.

This prototype has a physical root cutback. Its installed lifting geometry is
different from the uncut search wing. It cannot reuse search trim or be admitted
until the actual installed revision has aerodynamic and structural closure.
"""
from pathlib import Path

import numpy as np

from cem.core import digest, canonical_json
from . import attachment, foam_transfer, physical_fixture as fixture
from . import segmented_geometry as geo
from .geometry import closed_mesh, surface_rings, load_part_mesh, measure_part
from .physical_loads import (BeamMaterial, RootSupport, InstalledLoadCase, StationLoad,
                             analyze as analyze_beam)

MODE = 'bonded_shaped_wing_riveted_keel_prototype_v2'
COLLAR_BOND_THICKNESS_M = .00025
ROD_PRIORS = dict(youngs_modulus_pa=100e9, shear_modulus_pa=5e9,
    allowable_normal_pa=400e6, allowable_shear_pa=40e6, safety_factor=2.)


def construct(wing, diameter_m=.004):
    assembly = fixture.construct(wing, diameter_m)
    point = assembly['point']
    collar_radius = float(np.max(np.linalg.norm(
        assembly['parts']['clamp']['mesh'].vertices[:, [0, 2]]-point[[0, 2]], axis=1)))
    mount = attachment.construct(point, collar_radius)
    cutback = mount['root_cutback_m']
    rings = surface_rings(wing.surface())
    if cutback >= rings[1][0, 1]:
        raise ValueError('Attachment exceeds the first shaped-wing panel')
    first = rings[0]+(rings[1]-rings[0])*cutback/rings[1][0, 1]
    for side in (-1, 1):
        outer = closed_mesh([r*np.array([1., side, 1.]) for r in (first, rings[1])])
        bond_radius = collar_radius+COLLAR_BOND_THICKNESS_M
        assembly['parts'][f'foam_{side}_0']['mesh'] = fixture.material_core(outer, point, bond_radius)
        ends_y = sorted([side*cutback, side*float(rings[1][0, 1])])
        ends = np.tile(point, (2, 1)); ends[:, 1] = ends_y
        bond = geo.tube(ends, [[0., 1., 0.]]*2, [[0., 1., 0.]], bond_radius, collar_radius, count=64)
        assembly['parts'][f'wing_collar_bond_{side}'] = dict(mesh=bond,
            material=dict(law='solid', density_kg_m3=fixture.DENSITIES['adhesive']))
    assembly['parts'].update(mount['parts'])
    assembly.update(mode=MODE, root_cutback_m=cutback, collar_radius_m=collar_radius,
        geometry_sha256=digest(dict(mode=MODE, wing_fixture_sha256=assembly['geometry_sha256'],
            attachment_sha256=mount['revision_sha256'], root_cutback_m=cutback,
            collar_bond_thickness_m=COLLAR_BOND_THICKNESS_M)))
    return assembly


def mass_properties(parts):
    mass = sum(p['mass_kg'] for p in parts)
    cg = sum(p['mass_kg']*np.array(p['cg_m']) for p in parts)/mass
    inertia = np.zeros((3, 3))
    for p in parts:
        d = np.array(p['cg_m'])-cg
        inertia += np.array(p['inertia_kg_m2'])+p['mass_kg']*(np.dot(d, d)*np.eye(3)-np.outer(d, d))
    return dict(mass_kg=float(mass), cg_m=cg.tolist(), inertia_kg_m2=inertia.tolist())


def _stock_volume(wing, assembly):
    """Independent volume partition using station lofts and polygon stock laws."""
    import math
    rings = surface_rings(wing.surface())
    cutback = assembly['root_cutback_m']
    first = rings[0]+(rings[1]-rings[0])*cutback/rings[1][0, 1]
    rings[0] = first
    oml = 2*sum(closed_mesh([a, b]).volume for a, b in zip(rings, rings[1:]))
    def area(radius):
        return 32*radius*radius*math.sin(2*math.pi/64)
    s = attachment.SPEC
    collar = area(assembly['collar_radius_m'])
    bore = area(s['bolt_radius_m'])
    plate = s['plate_thickness_m']*(s['plate_width_m']*(s['plate_top_m']-s['plate_bottom_m'])-collar-2*bore)
    keel = s['keel_width_m']*((s['keel_back_m']-s['keel_front_m'])*(s['keel_top_m']-s['keel_bottom_m'])-2*bore)
    rivet = bore*2*cutback+2*area(s['head_radius_m'])*s['head_thickness_m']
    return float(oml+2*cutback*collar+2*plate+keel+2*rivet)


def verify(wing, diameter_m, manifest, directory, *, left=None, right=None,
           operating_sha256=None):
    """Re-import everything and reconstruct support evidence in this process.

    Caller-supplied interface summaries, material laws and stored reactions are
    never trusted. Optional fixture loads must name this delivered revision.
    """
    expected = construct(wing, diameter_m)
    if manifest['mode'] != MODE or manifest['geometry_sha256'] != expected['geometry_sha256']:
        raise ValueError('Mismatched mounted construction revision')
    directory = Path(directory)
    items = manifest['parts']
    names = set(expected['parts'])
    if len(items) != len(names) or {i['name'] for i in items} != names:
        raise ValueError('Missing or duplicate installed material inventory')
    if {p.name for p in directory.glob('*.stl')} != {n+'.stl' for n in names}:
        raise ValueError('Unaccounted installed geometry')
    measured, meshes = [], {}
    for item in items:
        name = item['name']
        if item['file'] != name+'.stl' or item['material'] != expected['parts'][name]['material']:
            raise ValueError('Installed path or material law differs from revision')
        path = directory/item['file']
        mesh = load_part_mesh(path)
        geo.same_surface(mesh, expected['parts'][name]['mesh'])
        props = measure_part(path, item['material'])
        if props['sha256'] != item['sha256']:
            raise ValueError('Installed material hash differs from delivery')
        meshes[name] = mesh
        measured.append(dict(name=name, **props))
    mount_meshes = {name: meshes[name] for name in attachment.construct(
        expected['point'], expected['collar_radius_m'])['parts']}
    interfaces = attachment.measure(expected['point'], meshes['clamp'], mount_meshes,
                                    collar_radius_m=expected['collar_radius_m'])
    foam_interfaces = foam_transfer.measure(expected, meshes)
    volume = sum(p['volume_m3'] for p in measured)
    relative_error = abs(volume/_stock_volume(wing, expected)-1.)
    if relative_error > 1e-5:
        raise ValueError('Installed material partition differs from delivered envelope')
    mechanics = transfer = beams = None
    if left is not None or right is not None:
        if left is None or right is None or operating_sha256 is None:
            raise ValueError('Both installed half-wing loads and operating identity required')
        mechanics = attachment.analyze(left, right, geometry_sha256=expected['geometry_sha256'],
            operating_sha256=operating_sha256, half_span_m=expected['half_span_m'], interfaces=interfaces)
        transfer = foam_transfer.analyze(left, right, foam_interfaces)
        section = geo.section(meshes['spar'], expected['point'], np.array([0., 1., 0.]))
        support = RootSupport(overlap_m=float(min(-meshes['clamp'].bounds[0, 1], meshes['clamp'].bounds[1, 1])),
            radius_m=section['extreme_radius_m'], bond_allowable_shear_pa=fixture.ADHESIVE['allowable_shear_pa'],
            safety_factor=2.)
        beams = [analyze_beam(case, geometry_sha256=expected['geometry_sha256'],
            operating_sha256=operating_sha256, half_span_m=expected['half_span_m'],
            section=section, material=BeamMaterial(**ROD_PRIORS), support=support,
            max_deflection_m=expected['half_span_m']/20, max_twist_rad=.0872664626)
            for case in (left, right)]
    failed = ((mechanics is not None and mechanics['supported_checks_state'] != 'passed')
              or (transfer is not None and transfer['nominal_checks_state'] != 'passed')
              or (beams is not None and any(b['state'] != 'passed' for b in beams)))
    return dict(state='failed' if failed else 'incomplete', mode=MODE,
        geometry_sha256=expected['geometry_sha256'], material_properties=mass_properties(measured),
        measured_parts=measured, material_partition_relative_error=float(relative_error),
        attachment_interfaces=interfaces, attachment_mechanics=mechanics,
        foam_interfaces=foam_interfaces, foam_transfer=transfer,
        half_wing_beams=beams,
        root_cutback_m=expected['root_cutback_m'], load_path_state='partial',
        manufacturing_ready=False, engineering_certified=False,
        blockers=['Installed lifting geometry has a root cutback; search-wing trim cannot be reused.',
                  'Foam-to-spar transfer and adhesive peel require qualification.',
                  'Attachment contact elasticity, stiffness and complete fuselage transfer are unqualified.',
                  'Task A validated installed loads and delivered-revision trim are required.'])


def size_fixture(wing, directory, *, non_wing_mass_kg=.05, load_factor=2., peak_torque_nm_m=.01):
    """Least catalogue rod passing the supported *fixture* checks after export.

    The analytical load ramps from zero at the physical root cutback to a tip
    peak; total vertical force is n times measured assembly plus declared extra
    mass. This is a test fixture, not a shape-specific aerodynamic prediction.
    Every diameter gets its own exports, measurements, mass and load identity.
    No stale export or unmeasured ideal-circle properties rescue a candidate.
    """
    from .processes import RODS_M
    if (not np.isfinite([non_wing_mass_kg, load_factor, peak_torque_nm_m]).all()
            or not 0 <= non_wing_mass_kg <= .5 or not 1 <= load_factor <= 12
            or abs(peak_torque_nm_m) > 1.):
        raise ValueError('Outside bounded analytical fixture loading')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError('Sizing needs an empty output directory')
    assumptions = dict(model='outboard-rising-triangular-analytical-load-v1',
        non_wing_mass_kg=non_wing_mass_kg, load_factor=load_factor, peak_torque_nm_m=peak_torque_nm_m)
    operating = digest(assumptions)
    attempts, chosen = [], None
    for diameter in (d for d in RODS_M if .002 <= d <= .008):
        target = directory/f'rod_{round(diameter*1000)}mm'
        try:
            assembly = construct(wing, diameter)
        except ValueError as exc:
            attempts.append(dict(diameter_m=diameter, state='geometry_rejected', reason=str(exc)))
            continue
        manifest = fixture.export_fixture(assembly, target)
        (target/'manifest.json').write_text(canonical_json(manifest)+'\n')
        measured = verify(wing, diameter, manifest, target)
        mass = measured['material_properties']['mass_kg']+non_wing_mass_kg
        root, tip = assembly['root_cutback_m'], wing.span/2
        peak_force = load_factor*mass*9.81/(tip-root)
        case = InstalledLoadCase(geometry_sha256=assembly['geometry_sha256'], operating_sha256=operating,
            stations=(StationLoad(y_m=0., fz_n_m=0., my_nm_m=0.),
                      StationLoad(y_m=root, fz_n_m=0., my_nm_m=0.),
                      StationLoad(y_m=tip, fz_n_m=peak_force, my_nm_m=peak_torque_nm_m)))
        result = verify(wing, diameter, manifest, target, left=case, right=case, operating_sha256=operating)
        (target/'loads.json').write_text(canonical_json(dict(left=case.model_dump(mode='json'),
                                                           right=case.model_dump(mode='json')))+'\n')
        (target/'measurement.json').write_text(canonical_json(result)+'\n')
        attempts.append(dict(diameter_m=diameter, state=result['state'],
            geometry_sha256=assembly['geometry_sha256'], loaded_mass_kg=mass,
            attachment_state=result['attachment_mechanics']['supported_checks_state'],
            foam_nominal_state=result['foam_transfer']['nominal_checks_state'],
            beam_states=[b['state'] for b in result['half_wing_beams']], directory=target.name))
        if result['state'] != 'failed':
            chosen = attempts[-1]
            break
    summary = dict(state='incomplete' if chosen else 'failed', mode=MODE,
        selected_fixture_section=chosen, attempts=attempts, fixture_assumptions=assumptions,
        operating_sha256=operating, manufacturing_ready=False, engineering_certified=False,
        qualification_state='analytical_fixture_only',
        blockers=['Task A installed-revision loads and trim are unavailable.',
                  'Foam peel/concentrations, mount elasticity and complete fuselage transfer remain unqualified.'])
    (directory/'result.json').write_text(canonical_json(summary)+'\n')
    return summary


def main():
    """Local research diagnostic; exit 3 = incomplete, 2 = rejected, never accepted."""
    import argparse
    import json
    from .shapes import ShapedWing
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wing-json', type=Path)
    parser.add_argument('--non-wing-mass-kg', type=float, default=.05)
    parser.add_argument('--load-factor', type=float, default=2.)
    parser.add_argument('--peak-torque-nm-m', type=float, default=.01)
    args = parser.parse_args()
    wing = (ShapedWing(**json.loads(args.wing_json.read_text())) if args.wing_json else
        ShapedWing(id='mounted-fixture', style='polyhedral', span=1., root_chord=.14, taper=.8,
            dihedral_deg=0., sweep_qc_deg=0., incidence_deg=0., washout_deg=0., naca='0012',
            chord_factors=(1., .9, 1.1, .95, 1.)))
    summary = size_fixture(wing, args.output, non_wing_mass_kg=args.non_wing_mass_kg,
        load_factor=args.load_factor, peak_torque_nm_m=args.peak_torque_nm_m)
    print(canonical_json(summary))
    return 3 if summary['state'] == 'incomplete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
