"""Versioned manufacturing assumptions and bounded carbon-rod bending sizing."""
import math

PROCESSES = {
    'sheet_foam': dict(wing_areal_density_kg_m2=.20, tail_areal_density_kg_m2=.12,
                      fuselage_shell_areal_density_kg_m2=.35, wall_thickness_m=.003,
                      max_washout_deg=2., max_sheet_thickness_m=.020),
    'balsa': dict(wing_areal_density_kg_m2=.17, tail_areal_density_kg_m2=.10,
                 fuselage_shell_areal_density_kg_m2=.30, wall_thickness_m=.0015,
                 max_washout_deg=3.),
    'fdm_lwpla': dict(wing_areal_density_kg_m2=.38, tail_areal_density_kg_m2=.28,
                     fuselage_shell_areal_density_kg_m2=.38, wall_thickness_m=.0008,
                     max_washout_deg=3.),
    'fdm_pla': dict(wing_areal_density_kg_m2=.90, tail_areal_density_kg_m2=.70,
                   fuselage_shell_areal_density_kg_m2=.90, wall_thickness_m=.0008,
                   max_washout_deg=3.),
    'hybrid': dict(wing_areal_density_kg_m2=.20, tail_areal_density_kg_m2=.12,
                  fuselage_shell_areal_density_kg_m2=.38, wall_thickness_m=.0008,
                  max_washout_deg=2., max_sheet_thickness_m=.020),
}
LAUNCH_LOAD_FACTORS = {'drop': 2., 'hand_toss': 3., 'discus': 8., 'catapult': 12., 'hi_start': 8.}
CARBON = dict(density_kg_m3=1600., allowable_pa=400e6, safety_factor=2., youngs_modulus_pa=100e9)
RODS_M = [.002, .003, .004, .005, .006, .008, .010, .012, .016, .020]


def spar_size(mass_kg, span_m, method):
    if not (math.isfinite(mass_kg) and math.isfinite(span_m) and mass_kg > 0 and span_m > 0):
        raise ValueError('Invalid spar loads')
    # Half-wing root moment under elliptical TOTAL lift n*W: n W b / (3 pi).
    moment = LAUNCH_LOAD_FACTORS[method] * mass_kg * 9.81 * span_m / (3 * math.pi)
    allowable = CARBON['allowable_pa'] / CARBON['safety_factor']
    diameter = next((d for d in RODS_M if 32 * moment / (math.pi * d**3) <= allowable), None)
    if diameter is None:
        raise ValueError('No spar in the approved catalogue satisfies launch bending')
    stress = 32 * moment / (math.pi * diameter ** 3)
    return dict(diameter_m=diameter, span_m=span_m, root_moment_nm=moment,
                stress_pa=stress, allowable_with_sf_pa=allowable,
                mass_kg=math.pi * diameter**2 / 4 * span_m * CARBON['density_kg_m3'],
                safety_factor=CARBON['safety_factor'], load_factor=LAUNCH_LOAD_FACTORS[method],
                limitations=['Ideal elliptical lift; bending only. Joints, torsion, buckling, deflection and fatigue unevaluated.'])


def materials(process, calibration):
    p = PROCESSES[process]
    factor = calibration['mass_factor'][process]['median']
    return dict(wing_areal_density_kg_m2=p['wing_areal_density_kg_m2'] * factor,
                tail_areal_density_kg_m2=p['tail_areal_density_kg_m2'] * factor,
                fuselage_shell_areal_density_kg_m2=p['fuselage_shell_areal_density_kg_m2'] * factor,
                fixed_hardware_mass_kg=.010, wall_thickness_m=p['wall_thickness_m'], wing_cg_chord_fraction=.42)


def manufacturing_checks(gl, process, spar):
    p, failures = PROCESSES[process], []
    if gl.wing.washout_deg > p['max_washout_deg']:
        failures.append('twist exceeds process envelope')
    if process in ('sheet_foam', 'hybrid'):
        if gl.wing.style != 'polyhedral' and gl.wing.dihedral_deg > 0:
            failures.append('sheet surfaces need discrete dihedral joints')
        if gl.wing.root_chord * int(gl.wing.naca[2:]) / 100 > p['max_sheet_thickness_m']:
            failures.append('airfoil exceeds foam stock thickness')
    # Rod must remain within the section across the span (constant full-span rod).
    min_chord = min(s.chord for s in gl.wing.surface().sections)
    if spar['diameter_m'] * 1.1 > min_chord * int(gl.wing.naca[2:]) / 100:
        failures.append('spar plus clearance does not fit tip airfoil thickness')
    return dict(state='failed' if failures else 'passed', failures=failures,
                manufacturing_ready=False,
                limitations=['Process screening only; toolpaths, shell walls, joints and printer bed splits are not verified.'])
