"""Load sizing and delivered-section checks, independent of aerodynamic admission."""
import math

import numpy as np
import pytest
import trimesh

from cem.glider.processes import CARBON, RODS_M, spar_size
from cem.glider.structure import bending_moment, converged_spar, verify_spar


def rod(diameter=.006, span=1.):
    transform = trimesh.geometry.align_vectors([0, 0, 1], [0, 1, 0])
    return trimesh.creation.cylinder(radius=diameter/2, height=span, sections=48, transform=transform)


def test_elliptical_load_has_known_root_moment_and_zero_tip():
    y = np.linspace(0., .5, 17)
    moments = [bending_moment(.2, 1., 'drop', float(s)) for s in y]
    assert moments[0] == pytest.approx(2*.2*9.81/(3*math.pi))
    assert moments[-1] == 0.
    assert all(a >= b >= 0 for a, b in zip(moments, moments[1:]))


@pytest.mark.parametrize('value', [0., -1., float('nan'), float('inf')])
def test_nonphysical_loading_fails(value):
    with pytest.raises(ValueError):
        converged_spar(value, 1., 'drop')


def test_catalogue_sizing_closes_its_own_mass_and_uses_actual_mass():
    spar = converged_spar(.12, 1.2, 'drop')
    assert spar['sizing_mass_kg'] == pytest.approx(.12 + spar['mass_kg'])
    assert spar['diameter_m'] == spar_size(spar['sizing_mass_kg'], 1.2, 'drop')['diameter_m']
    assert spar['stress_pa'] <= spar['allowable_with_sf_pa']
    assert 1 <= spar['sizing_iterations'] <= len(RODS_M)
    assert spar['diameter_m'] < spar_size(2., 1.2, 'drop')['diameter_m']


def test_local_checks_use_delivered_polygon_not_ideal_circle():
    mesh = rod()
    result = verify_spar(mesh, .2, 1., 'drop')
    assert result['state'] == 'passed'
    assert result['load_path_state'] == 'unverified'
    assert len(result['stations']) == 17
    assert result['stations'][0]['moment_nm'] == pytest.approx(2*.2*9.81/(3*math.pi))
    assert result['stations'][-1]['moment_nm'] == 0.
    assert result['root_section_inertia_m4'] < math.pi*.006**4/64
    assert result['material'] == CARBON
    assert result['mass_kg'] == .2
    assert result['max_stress_pa'] > 0.


def test_undersized_delivered_rod_fails_even_if_declared_sizing_passed():
    result = verify_spar(rod(.002), .8, 1., 'catapult')
    assert result['state'] == 'failed'
    assert result['max_stress_pa'] > result['allowable_with_sf_pa']


def test_added_measured_mass_increases_load_without_resizing_artifact():
    mesh = rod()
    a, b = [verify_spar(mesh, mass, 1., 'drop') for mass in (.2, .24)]
    assert b['max_stress_pa'] == pytest.approx(1.2*a['max_stress_pa'])


@pytest.mark.parametrize('mutation', ['short', 'offset', 'tilt', 'waist'])
def test_unsupported_rod_geometry_cannot_use_uniform_section_formula(mutation):
    mesh = rod()
    if mutation == 'short':
        mesh.apply_scale([1., .9, 1.])
    elif mutation == 'offset':
        mesh.apply_translation([0., .01, 0.])
    elif mutation == 'tilt':
        mesh.vertices[:, 2] += .01*mesh.vertices[:, 1]
    else:
        # A connected neck can have plausible total inertia, but is not a
        # uniform stock rod. Never infer its root section from global inertia.
        from cem.glider.geometry import closed_mesh
        rings = []
        for y, radius in [(-.5, .003), (0., .001), (.5, .003)]:
            t = np.linspace(0, 2*math.pi, 48, endpoint=False)
            rings.append(np.column_stack([radius*np.cos(t), np.full(48, y), radius*np.sin(t)]))
        mesh = closed_mesh(rings)
    with pytest.raises(ValueError):
        verify_spar(mesh, .2, 1., 'drop')


def test_sized_glider_mass_cap_does_not_replace_design_load():
    from backend.tests.test_cem_pipeline import example
    _, _, gl = example()
    a = gl.spar()
    gl.cfg['limits']['max_total_mass_kg'] *= 2
    assert gl.spar() == a
    gl.ballast_kg += .2
    assert gl.spar()['sizing_mass_kg'] > a['sizing_mass_kg']
    assert gl.spar()['root_moment_nm'] > a['root_moment_nm']


@pytest.mark.parametrize('mutation', [None, 'missing', 'mass', 'station', 'material',
                                      'inertia', 'summary', 'load_path', 'artifact'])
def test_parent_recomputes_artifact_bound_structural_evidence(tmp_path, mutation):
    from cem.evidence import structural_evidence, file_digest
    from cem.glider.geometry import load_part_mesh
    from gliders.model.export import write_stl
    name = '0123456789ab_spar.stl'
    mesh = rod()
    write_stl(str(tmp_path/name), mesh.vertices, mesh.faces)
    result = verify_spar(load_part_mesh(tmp_path/name), .2, 1., 'drop')
    report = dict(bundle={'parts': [dict(name='spar', file=name, sha256=file_digest(tmp_path/name))]},
                  closure=dict(measured_mass_kg=.2, spar_structure=result,
                               measured_spar_stress_pa=result['max_stress_pa'],
                               measured_spar_root_inertia_m4=result['root_section_inertia_m4']))
    payload = dict(elite={'parameters': {'wing': {'span': 1.}}},
                   requirements={'launch': {'method': 'drop'}})
    if mutation == 'missing':
        report['closure'].pop('spar_structure')
    elif mutation == 'mass':
        report['closure']['measured_mass_kg'] *= 1.2
    elif mutation == 'station':
        result['stations'][0]['stress_pa'] = 0.
    elif mutation == 'material':
        result['material']['allowable_pa'] *= 2
    elif mutation == 'inertia':
        result['root_section_inertia_m4'] *= 2
    elif mutation == 'summary':
        report['closure']['measured_spar_stress_pa'] = 0.
    elif mutation == 'load_path':
        result['load_path_state'] = 'passed'
    elif mutation == 'artifact':
        write_stl(str(tmp_path/name), rod(.002).vertices, rod(.002).faces)
    if mutation is None:
        structural_evidence(report, payload, tmp_path)
    else:
        with pytest.raises((ValueError, KeyError)):
            structural_evidence(report, payload, tmp_path)
