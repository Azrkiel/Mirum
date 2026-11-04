"""RegenChamberSpec: typed envelope, contour, and rejection rules. No cadquery."""
import math
import sys

import pytest


def spec_data(**changes):
    values = dict(
        family='regen_chamber', units='mm',
        chamber_radius_mm=35.0, chamber_length_mm=40.0, convergent_length_mm=50.0,
        throat_radius_mm=20.0, divergent_length_mm=80.0, exit_radius_mm=45.0,
        wall_thickness_mm=7.0, channel_count=32, channel_radius_mm=1.0,
        channel_twist_rad_per_mm=0.012, manifold_radius_mm=1.5, end_margin_mm=8.0,
        voxel_size_mm=0.4,
        assumptions='geometry_only_no_gas_dynamics_thermal_or_structural_analysis')
    values.update(changes)
    return values


def make(**changes):
    from engineering.regen_spec import RegenChamberSpec
    return RegenChamberSpec.model_validate(spec_data(**changes))


def test_valid_example_passes_and_is_frozen():
    s = make()
    assert s.family == 'regen_chamber'
    with pytest.raises(Exception):
        s.wall_thickness_mm = 1.0


def test_import_needs_no_cadquery_or_compiler():
    import engineering.regen_spec  # noqa: F401
    assert 'cadquery' not in sys.modules
    assert 'compiler' not in sys.modules


def test_digest_independent_of_key_order_and_canonical_is_stable():
    from engineering.regen_spec import RegenChamberSpec, canonical_spec, specification_digest
    a = RegenChamberSpec.model_validate(spec_data())
    b = RegenChamberSpec.model_validate(dict(reversed(list(spec_data().items()))))
    assert canonical_spec(a) == canonical_spec(b)
    assert specification_digest(a) == specification_digest(b)
    assert len(specification_digest(a)) == 64
    assert specification_digest(a) != specification_digest(make(channel_count=33))


def test_contour_values_and_c1_continuity():
    from engineering.regen_spec import inner_radius
    s = make()
    L = s.chamber_length_mm + s.convergent_length_mm + s.divergent_length_mm
    zt = s.chamber_length_mm + s.convergent_length_mm
    assert inner_radius(s, 0.0) == pytest.approx(35.0)
    assert inner_radius(s, 40.0) == pytest.approx(35.0)
    assert inner_radius(s, 65.0) == pytest.approx(27.5)  # mid of convergent blend
    assert inner_radius(s, zt) == pytest.approx(20.0)
    assert inner_radius(s, zt + 40.0) == pytest.approx(32.5)
    assert inner_radius(s, L) == pytest.approx(45.0)
    h = 1e-4
    for z in (40.0, zt):  # slope continuity at the joins: zero on both sides
        left = (inner_radius(s, z) - inner_radius(s, z - h)) / h
        right = (inner_radius(s, z + h) - inner_radius(s, z)) / h
        assert abs(left) < 1e-3 and abs(right) < 1e-3
    # finite max slope never exceeds analytic bound pi/2*dR/len
    zs = [i * L / 20000 for i in range(20001)]
    rs = [inner_radius(s, z) for z in zs]
    mx = max(abs(rs[i + 1] - rs[i]) / (zs[i + 1] - zs[i]) for i in range(20000))
    assert mx <= max(math.pi / 2 * 15 / 50, math.pi / 2 * 25 / 80) + 1e-6


@pytest.mark.parametrize('changes', [
    {'family': 'cantilever'}, {'units': 'inch'}, {'assumptions': 'validated'},
    {'standard': 'ASME'},                       # extra field
    {'channel_count': 32.0}, {'channel_count': True}, {'wall_thickness_mm': '6'},
    {'wall_thickness_mm': float('nan')}, {'exit_radius_mm': float('inf')},
    {'wall_thickness_mm': 0.0}, {'wall_thickness_mm': -1.0},
])
def test_strict_typing_rejects(changes):
    with pytest.raises(ValueError):
        make(**changes)


@pytest.mark.parametrize('changes,pattern', [
    ({'throat_radius_mm': 35.0}, 'throat'),
    ({'throat_radius_mm': 45.0, 'chamber_radius_mm': 50.0, 'exit_radius_mm': 45.0}, 'throat'),
    ({'convergent_length_mm': 20.0}, 'slope'),
    ({'divergent_length_mm': 30.0}, 'slope'),
    ({'wall_thickness_mm': 4.4, 'channel_radius_mm': 1.0}, 'wall'),
    ({'channel_radius_mm': 0.7}, 'channel radius'),
    ({'channel_count': 48}, 'rib'),
    ({'manifold_radius_mm': 3.0}, 'manifold'),
    ({'end_margin_mm': 3.0}, 'end margin'),
    ({'end_margin_mm': 40.0}, 'manifold'),            # forward ring would sit in the blend
    ({'end_margin_mm': 78.0}, 'manifold'),            # aft ring too near the throat
    ({'voxel_size_mm': 0.05}, 'voxel'),
    ({'voxel_size_mm': 1.5}, 'voxel'),
    ({'channel_count': 3}, 'channel'),
    ({'channel_count': 257}, 'channel'),
])
def test_validators_reject_with_clear_message(changes, pattern):
    with pytest.raises(ValueError, match=f'(?i){pattern}'):
        make(**changes)


def test_voxel_count_budget():
    # Would pass every geometric rule but exceed 3e8 bounding voxels.
    with pytest.raises(ValueError, match='(?i)voxel count'):
        make(voxel_size_mm=0.1, channel_radius_mm=1.0)


def test_spike_parameters_are_rejected():
    """Spike: N=48, rc=1.1, throat 15, wall 5, voxel 0.4. Suspected speck cause."""
    with pytest.raises(ValueError):
        make(channel_count=48, channel_radius_mm=1.1, throat_radius_mm=15.0,
             wall_thickness_mm=5.0, convergent_length_mm=40.0)


def test_spike_equivalent_rejected_for_throat_rib():
    """Isolate the rib rule: finer voxel so wall/slope rules pass, spike N/rc/throat/wall."""
    with pytest.raises(ValueError, match='(?i)rib'):
        make(channel_count=48, channel_radius_mm=1.1, throat_radius_mm=15.0,
             wall_thickness_mm=5.0, convergent_length_mm=60.0, voxel_size_mm=0.25)


def test_existing_cantilever_spec_untouched():
    from engineering.spec import CantileverSpec  # noqa: F401
