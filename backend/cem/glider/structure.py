"""Bounded stock-rod bending screen, not joint or physical-construction evidence.

The span load is explicitly ideal elliptical TOTAL lift n W. Local section
loads use converged aircraft mass; tail lift, gusts and aeroelasticity are not
reconstructed. The delivered mesh must independently establish a uniform,
centred Y-axis prism before global inertia may represent a local section.
"""
import math

import numpy as np

from .processes import CARBON, LAUNCH_LOAD_FACTORS, RODS_M, spar_size

STATIONS = 17
METHOD = 'measured-uniform-rod-elliptical-bending-v1'


def bending_moment(mass_kg, span_m, method, station_m):
    if not all(math.isfinite(v) for v in (mass_kg, span_m, station_m)) or mass_kg <= 0 or span_m <= 0:
        raise ValueError('Invalid spar loads')
    if method not in LAUNCH_LOAD_FACTORS or not 0 <= station_m <= span_m/2:
        raise ValueError('Invalid load case or span station')
    u = station_m/(span_m/2)
    remainder = max(0., 1-u*u)
    # Integral_y^L q(s) (s-y) ds, q(s)=2 n W/(pi L) sqrt(1-(s/L)^2).
    integral = 2/3 * remainder**1.5 - u*(math.acos(u)-u*math.sqrt(remainder))
    return max(0., LAUNCH_LOAD_FACTORS[method]*mass_kg*9.81*(span_m/2)/math.pi*integral)


def converged_spar(non_spar_mass_kg, span_m, method):
    """Least catalogue fixed point including the selected rod's own mass.

    Ascending discrete sizes make this monotone, bounded by the catalogue.
    Recomputed after every ballast/placement revision; no mutable size cache.
    """
    if not math.isfinite(non_spar_mass_kg) or non_spar_mass_kg <= 0:
        raise ValueError('Invalid non-spar mass')
    mass = non_spar_mass_kg
    # Reserve the loss of section modulus from the delivered 48-sided polygon.
    theta = 2*math.pi/48
    polygon_ratio = 48*math.sin(theta)*(2+math.cos(theta))/(6*math.pi)
    for iteration in range(1, len(RODS_M)+1):
        selected = spar_size(mass/polygon_ratio, span_m, method)
        total = non_spar_mass_kg + selected['mass_kg']
        required = spar_size(total/polygon_ratio, span_m, method)
        if selected['diameter_m'] == required['diameter_m']:
            required.update(sizing_mass_kg=total, sizing_iterations=iteration,
                            root_moment_nm=bending_moment(total, span_m, method, 0.),
                            section_model='48-sided stock rod; ideal elliptical lift')
            return required
        mass = total
    raise ValueError('Spar mass/section sizing did not converge within catalogue budget')


def verify_spar(mesh, mass_kg, span_m, method):
    """Measure a proven uniform prism and check its section along the half-span.

    A bounded convex two-end-ring prism is mandatory. Short, offset, sloping,
    necked and tapered rods cannot use the uniform-section inertia identity.
    The monotonically decreasing elliptical moment is bounded by the root;
    stations document the load distribution, rather than sampling for defects.
    """
    bending_moment(mass_kg, span_m, method, 0.)
    if (len(mesh.faces) > 4096 or not np.isfinite(mesh.vertices).all()
            or not mesh.is_watertight or not mesh.is_winding_consistent
            or mesh.body_count != 1 or mesh.volume <= 0 or not mesh.is_convex):
        raise ValueError('Missing bounded convex stock rod')
    vertices, bounds = np.asarray(mesh.vertices), np.asarray(mesh.bounds)
    length = bounds[1, 1]-bounds[0, 1]
    tolerance = 2e-7  # metre-scale STL quantization only
    if not np.allclose(bounds[:, 1], [-span_m/2, span_m/2], rtol=0, atol=tolerance):
        raise ValueError('Spar must cover the centred full span')
    rings = []
    for y in bounds[:, 1]:
        rings.append(vertices[np.abs(vertices[:, 1]-y) <= tolerance][:, [0, 2]])
    if len(rings[0])+len(rings[1]) != len(vertices) or len(rings[0]) != len(rings[1]):
        raise ValueError('Stock rod must have exactly two matching end rings')
    # Cap centres may be vertices. Lexicographic sorting pairs both end sets.
    a, b = [ring[np.lexsort((ring[:, 1], ring[:, 0]))] for ring in rings]
    if not np.allclose(a, b, rtol=0, atol=1e-10):
        raise ValueError('Stock rod sections are not constant along Y')
    normals_y = np.abs(mesh.face_normals[:, 1])
    if not np.all((normals_y < 1e-8) | (normals_y > 1-1e-8)):
        raise ValueError('Non-prismatic rod side faces')
    # Unit-density mesh inertia; eliminate the distributed span contribution.
    inertia = np.asarray(mesh.moment_inertia)
    section_ixx = (inertia[1, 1]+inertia[0, 0]-inertia[2, 2])/(2*length)
    if not math.isfinite(section_ixx) or section_ixx <= 0:
        raise ValueError('Missing measured section inertia')
    # This screen handles bending about principal X only, not coupled bending.
    if abs(inertia[0, 2]) > 1e-7*section_ixx*length:
        raise ValueError('Unsupported non-principal rod section')
    extreme_z = float(np.max(np.abs(vertices[:, 2]-mesh.center_mass[2])))
    allowable = CARBON['allowable_pa']/CARBON['safety_factor']
    stations = []
    for y in np.linspace(0, span_m/2, STATIONS):
        moment = bending_moment(mass_kg, span_m, method, float(y))
        stations.append(dict(y_m=float(y), moment_nm=moment,
                             stress_pa=moment*extreme_z/section_ixx))
    maximum = max(s['stress_pa'] for s in stations)
    return dict(state='passed' if maximum <= allowable*(1+1e-5) else 'failed', method=METHOD,
                mass_kg=mass_kg, span_m=span_m, load_factor=LAUNCH_LOAD_FACTORS[method],
                material=dict(CARBON), allowable_with_sf_pa=allowable,
                root_section_inertia_m4=float(section_ixx), extreme_z_m=extreme_z,
                max_stress_pa=maximum, stations=stations, load_path_state='unverified',
                limitations=['Ideal elliptical lift, uniform straight stock rod, X-axis bending only.',
                             'Channels, ribs, joints, tail loads, torsion, buckling, deflection and fatigue unverified.'])
