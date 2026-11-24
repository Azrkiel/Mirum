"""Independent planform controls, deterministic search and revision binding."""
from dataclasses import asdict
import random

import numpy as np
import pytest

from cem.glider.requirements import GliderMissionRequirements as Mission


def test_shape_search_cannot_claim_physical_construction():
    with pytest.raises(ValueError, match='shape'):
        Mission(launch={'method': 'drop'}, process='sheet_foam',
                construction='segmented_foam_wing_v1',
                discovery={'wing_shape': 'multi_station'})


def test_station_chords_change_real_geometry_without_moving_dihedral_joints():
    from cem.glider.shapes import ShapedWing, wing_from_parameters, ETAS
    from backend.tests.test_cem_pipeline import example
    _, _, gl = example()
    original = gl.wing.surface()
    wing = ShapedWing(**asdict(gl.wing), chord_factors=(1., 1.15, .85, 1.1, 1.))
    surface = wing.surface()
    assert len(surface.sections) == 5
    assert [s.y for s in surface.sections] == pytest.approx(np.array(ETAS)*wing.span/2)
    assert surface.sections[1].chord == pytest.approx(gl.wing.chord_at(ETAS[1])*1.15)
    assert surface.sections[2].chord == pytest.approx(original.sections[1].chord*.85)
    assert surface.sections[2].z == 0.
    assert surface.sections[-1].z == original.sections[-1].z
    assert surface.true_area() != pytest.approx(original.true_area(), rel=1e-4)
    restored = wing_from_parameters(asdict(wing))
    assert restored.surface() == surface


@pytest.mark.parametrize('factors', [(1., 1., 1.), (1., 2., 1., 1., 1.),
                                    (1., float('nan'), 1., 1., 1.),
                                    (.9, 1., 1., 1., 1.), (True, 1., 1., 1., 1.)])
def test_unbounded_or_malformed_shape_cannot_be_reconstructed(factors):
    from cem.glider.shapes import ShapedWing
    from backend.tests.test_cem_pipeline import example
    _, _, gl = example()
    with pytest.raises(ValueError):
        ShapedWing(**asdict(gl.wing), chord_factors=factors)


def test_shape_sampling_is_seeded_and_local_changes_preserve_bounds():
    from cem.glider.shapes import sample_shape
    from backend.tests.test_cem_pipeline import example
    _, _, gl = example()
    a = sample_shape(gl.wing, random.Random(42))
    b = sample_shape(gl.wing, random.Random(42))
    assert asdict(a) == asdict(b)
    assert a.chord_factors != (1.,)*5
    child = sample_shape(gl.wing, random.Random(9), a, radius=.05)
    assert all(abs(x-y) <= .05 for x, y in zip(a.chord_factors, child.chord_factors))


def test_shape_search_withholds_the_old_feasibility_bound():
    from cem.glider.feasibility import feasibility
    from cem.glider.calibration import initial
    req = Mission(launch={'method': 'drop'}, reference_target=1200.,
                  discovery={'wing_shape': 'multi_station'})
    report = feasibility(req, initial())
    assert report['optimistic_bound'] is None
    assert report['bound_state'] == 'rebuild_required_for_shape'
    assert report['verdict'] == 'marginal' and report['continue_search'] is True


def test_shaped_parameters_cannot_be_used_for_a_baseline_mission():
    from cem.glider.shapes import sample_shape, wing_from_parameters
    from backend.tests.test_cem_pipeline import example
    req, _, gl = example()
    with pytest.raises(ValueError, match='discovery mode'):
        wing_from_parameters(asdict(sample_shape(gl.wing, random.Random(1))), req)


def test_real_shaped_candidate_passes_the_existing_solver_gates():
    from cem.glider.shapes import ShapedWing
    from cem.glider.calibration import initial
    from cem.glider.model import configuration, evaluate_candidate
    from backend.tests.test_cem_pipeline import example
    req, _, gl = example()
    data = req.model_dump(mode='json')
    data['discovery']['wing_shape'] = 'multi_station'
    req = Mission.model_validate(data)
    request, gl.cfg = configuration(req, initial(), 'sheet_foam')
    gl.wing = ShapedWing(**asdict(gl.wing), chord_factors=(1., 1.02, .99, 1.01, 1.))
    result = evaluate_candidate(gl, request, req, initial(), 'sheet_foam')
    assert result['valid'], result['fail_reasons']
    assert result['validity_envelope']['min_confidence'] >= .8
    assert result['manufacturability']['manufacturing_ready'] is False
    assert result['objective_value'] > 0


def test_discovery_explores_shapes_within_budget_and_binds_archive_revisions(monkeypatch):
    import copy
    from cem.evidence import validate
    from cem.glider import discovery
    from cem.glider.calibration import initial
    from cem.glider.shapes import ShapedWing, wing_from_parameters
    from cem.core import canonical_json, digest
    calls = []
    def evaluate(gl, *args):
        calls.append(gl.wing)
        return dict(valid=True, fail_reasons='', tail_kind=gl.tail.kind,
                    aspect_ratio=gl.wing_ref()['AR'], wing_loading_g_dm2=5.,
                    mass_kg=.2, CL_trim=.6, CD_trim=.05, CLmax_est=1., glide_speed_m_s=5.,
                    validity_envelope={'min_confidence': .9, 'min_station_reynolds': 60000., 'low_re_degraded': True},
                    manufacturability={'state': 'passed'},
                    objective_value=gl.wing_surface().true_area())
    monkeypatch.setattr(discovery, 'evaluate_candidate', evaluate)
    req = Mission(launch={'method': 'drop'}, process='balsa',
                  discovery={'wing_shape': 'multi_station', 'budget_evals': 8})
    a = discovery.discover(req, initial())
    assert len(calls) == 8 and not isinstance(calls[0], ShapedWing)
    assert sum(isinstance(w, ShapedWing) for w in calls) == 6
    b = discovery.discover(req, initial())
    assert canonical_json(a) == canonical_json(b)
    assert a['capabilities']['parametric_wing_shape_discovery'] is True
    assert a['capabilities']['novel_shape_discovery'] is False
    shaped = [e for e in a['per_process']['balsa']['archive'] if 'chord_factors' in e['parameters']['wing']]
    assert shaped
    for elite in shaped:
        wing_from_parameters(elite['parameters']['wing'], req)
        assert elite['elite_id'] == digest({k: elite[k] for k in ('process', 'parameters', 'placement')})[:16]
        assert elite['novelty_state'] == 'not_claimed'
        assert elite['independent_recheck']['state'] == 'not_evaluated'
    payload = dict(kind='discovery', requirements=req.model_dump(mode='json'), calibration=initial())
    report = dict(a, requirements=payload['requirements'], calibration=initial(), engineering_certified=False)
    validate(report, payload, {}, None)
    for field, replacement in [('wing_shape', 'baseline'), ('chord_shape_factors', [1.]*5)]:
        tampered = copy.deepcopy(report)
        elite = next(e for e in tampered['per_process']['balsa']['archive'] if 'chord_factors' in e['parameters']['wing'])
        elite['result'][field] = replacement
        with pytest.raises(ValueError, match='Archive wing shape'):
            validate(tampered, payload, {}, None)


def test_shape_sections_drive_mass_and_mesh_and_survive_reconstruction(tmp_path):
    from cem.glider.shapes import ShapedWing, wing_from_parameters
    from cem.glider.geometry import export_bundle, load_part_mesh, surface_rings
    from backend.tests.test_cem_pipeline import example
    _, _, gl = example()
    base_mass = gl.mass_summary()['mass']
    gl.wing = ShapedWing(**asdict(gl.wing), chord_factors=(1., 1.15, 1.15, 1.15, 1.))
    assert gl.mass_summary()['mass'] > base_mass
    surface = gl.wing_surface()
    bundle = export_bundle(gl, tmp_path, '0123456789ab')
    part = next(p for p in bundle['parts'] if p['name'] == 'wing_0')
    mesh = load_part_mesh(tmp_path/part['file'])
    for ring in surface_rings(surface):
        for vertex in ring:
            assert np.min(np.linalg.norm(mesh.vertices-vertex, axis=1)) < 2e-7
    gl.wing = wing_from_parameters(asdict(gl.wing))
    assert gl.wing_surface() == surface
