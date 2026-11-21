"""K25: the route assurance of a single match comes from the descriptor claim, never a constant."""
import pytest

from engineering.capabilities import get_generator
from engineering.router import route

PROMPTS = {
    'co2_dragster': 'Design a wedge CO2 dragster',
    'regen_chamber': 'Design a regeneratively cooled rocket thrust chamber with cooling channels',
    'glider_cem': 'Design a hand-launched glider that maximizes distance',
    'rectangular_cantilever': 'Size a rectangular cantilever beam for a 200 N tip force with a 2 mm deflection limit',
    'rectangular_plate': 'A clamped rectangular plate, 400 x 300 mm, uniform load, find the thickness for a 1 mm deflection limit',
}
EXPECTED = {'co2_dragster': 'geometry_checks_only', 'regen_chamber': 'geometry_checks_only',
            'glider_cem': 'screening_only', 'rectangular_cantilever': 'checked_design',
            'rectangular_plate': 'checked_design'}


@pytest.mark.parametrize('capability', sorted(PROMPTS))
def test_route_assurance_is_the_descriptor_claim(capability):
    decision = route(PROMPTS[capability])
    assert decision.capability_id == capability
    assert decision.assurance == EXPECTED[capability]
    generator = get_generator(capability)
    if generator is not None:
        assert decision.assurance == generator.claim


def test_unrouted_stays_unverified_geometry():
    assert route('make me a cat statue').assurance == 'unverified_geometry'


def test_frontend_labels_render_descriptor_claims_honestly():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'frontend'))
    from route_labels import assurance_label
    assert assurance_label('geometry_checks_only') == 'Geometry checks only'
    assert assurance_label('screening_only') == 'Screening only'
    assert assurance_label('checked_design') == 'Checked design'
    assert assurance_label('unverified_geometry') == 'Unverified geometry'
