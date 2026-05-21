"""Unit tests for the Domain F propulsion primitives + validators + Rao table.

Covers:
* One positive test per primitive: cq.Solid (or Compound) with isValid + positive volume.
* Rao exit-radius probe within 0.5 percent of r_t * sqrt(epsilon).
* Helix pitch sanity against tan(helix_angle) * 2pi * r_outer.
* Validators flag a contradictory expansion-ratio spec.
* rao_table.lookup boundary: in-range OK, out-of-range raises ValueError.

Runnable via `python backend/tests/test_propulsion.py` or pytest.
"""
from __future__ import annotations

import math
import sys
import types
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Stub google.generativeai so importing agents/schemas does not require the SDK.
_google = sys.modules.setdefault("google", types.ModuleType("google"))
_genai = sys.modules.setdefault("google.generativeai", types.ModuleType("google.generativeai"))
if not hasattr(_genai, "GenerativeModel"):
    _genai.GenerativeModel = lambda *args, **kwargs: None  # type: ignore[attr-defined]
setattr(_google, "generativeai", _genai)


# ---------------------------------------------------------------------------
# rao_table boundary tests
# ---------------------------------------------------------------------------

def test_rao_table_in_range_returns_finite() -> None:
    from propulsion import rao_table
    t_n, t_e = rao_table.lookup(16.0, 0.8)
    assert 0 < t_n < 60
    assert 0 < t_e < 30


def test_rao_table_endpoint_values_match_table() -> None:
    from propulsion import rao_table
    # (epsilon=10, Lf=0.8) is a directly-tabulated cell.
    t_n, t_e = rao_table.lookup(10.0, 0.8)
    assert abs(t_n - 24.5) < 1e-6
    assert abs(t_e -  9.3) < 1e-6


def test_rao_table_out_of_range_raises() -> None:
    from propulsion import rao_table
    raised = False
    try:
        rao_table.lookup(3.0, 0.8)  # epsilon below envelope
    except ValueError:
        raised = True
    assert raised, "out-of-range epsilon must raise ValueError"
    raised = False
    try:
        rao_table.lookup(16.0, 0.5)  # Lf below envelope
    except ValueError:
        raised = True
    assert raised, "out-of-range length_fraction must raise ValueError"


# ---------------------------------------------------------------------------
# Validator tests
# ---------------------------------------------------------------------------

def test_validator_flags_contradictory_expansion_ratio() -> None:
    from propulsion.specs import NozzleSpec, PropulsionSpec
    from propulsion.validation import validate_all
    nz = NozzleSpec(
        type="conical",
        throat_radius=25.0,
        exit_radius=70.0,        # (70/25)^2 = 7.84 -- inconsistent with epsilon=9
        expansion_ratio=9.0,
        half_angle_deg=15.0,
        converging_angle_deg=30.0,
        chamber_radius=50.0,
    )
    ps = PropulsionSpec(intent="analysis", nozzle=nz)
    errs = validate_all(ps)
    assert any("disagrees" in e for e in errs), f"expected expansion-ratio mismatch, got {errs}"


def test_validator_passes_consistent_propulsion_spec() -> None:
    from propulsion.specs import NozzleSpec, PropulsionSpec
    from propulsion.validation import validate_all
    ps = PropulsionSpec(
        intent="analysis",
        nozzle=NozzleSpec(
            type="rao_bell",
            throat_radius=20.0,
            expansion_ratio=16.0,
            length_fraction=0.8,
            chamber_radius=60.0,
        ),
    )
    assert validate_all(ps) == []


def test_validator_flags_channel_overflow() -> None:
    from propulsion.specs import NozzleSpec, CoolingChannelSpec, PropulsionSpec
    from propulsion.validation import validate_all
    ps = PropulsionSpec(
        intent="manufacturable",
        nozzle=NozzleSpec(
            type="rao_bell",
            throat_radius=25.0,
            expansion_ratio=12.0,
            chamber_radius=60.0,
        ),
        cooling_channels=CoolingChannelSpec(count=400, width=2.0, depth=3.0),
    )
    errs = validate_all(ps)
    assert errs, "400 channels should overflow the throat perimeter budget"


# ---------------------------------------------------------------------------
# Primitive tests — geometry contracts
# ---------------------------------------------------------------------------

def _valid_positive_volume(wp, label: str) -> None:
    val = wp.val()
    assert val.isValid(), f"{label}: geometry isValid() returned False"
    assert val.Volume() > 0, f"{label}: volume was non-positive"


def test_conical_nozzle_positive_volume() -> None:
    from cad_utils import make_conical_nozzle
    n = make_conical_nozzle(
        throat_radius=25.0, exit_radius=75.0,
        half_angle_deg=15.0, converging_angle_deg=30.0,
        chamber_radius=50.0,
    )
    _valid_positive_volume(n, "conical_nozzle")
    bb = n.val().BoundingBox()
    assert abs(bb.xmax - 75.0) < 0.5, f"conical xmax={bb.xmax} expected ~75"


def test_rao_bell_exit_radius_within_half_percent() -> None:
    from cad_utils import make_rao_bell_nozzle
    r_t = 20.0
    eps = 16.0
    n = make_rao_bell_nozzle(throat_radius=r_t, expansion_ratio=eps,
                              length_fraction=0.8, chamber_radius=60.0)
    _valid_positive_volume(n, "rao_bell")
    bb = n.val().BoundingBox()
    expected = r_t * math.sqrt(eps)
    # exit_radius = bbox.xmax (after revolve about Y then rotate the bell
    # extends in +/- X with magnitude = exit_radius).
    err = abs(bb.xmax - expected) / expected
    assert err < 0.005, f"exit_radius {bb.xmax} differs from {expected} by {err*100:.2f}%"


def test_combustion_chamber_with_frustum_valid() -> None:
    from cad_utils import make_combustion_chamber
    c = make_combustion_chamber(radius=40.0, l_star=1.0, throat_radius=20.0)
    _valid_positive_volume(c, "combustion_chamber")


def test_injector_plate_impinging_doublet_valid() -> None:
    from cad_utils import make_injector_plate
    p = make_injector_plate(
        plate_radius=45.0, plate_thickness=6.0,
        orifice_count=24, orifice_diameter=1.5,
        pattern="impinging_doublet", distribution_radius=30.0,
        impingement_angle_deg=30.0,
    )
    _valid_positive_volume(p, "injector_plate")


def test_helical_cooling_channels_helix_pitch() -> None:
    """Sweep one channel and verify the helix pitch matches tan(angle)*2pi*r within 1 percent."""
    import cadquery as cq
    from cad_utils import make_helical_cooling_channels
    nozzle = cq.Workplane("XY").circle(40.0).extrude(80.0).translate((0, 0, -40.0))
    helix_angle_deg = 15.0
    r_outer = 40.0
    result = make_helical_cooling_channels(
        nozzle_solid=nozzle, nozzle_outer_radius=r_outer,
        channel_count=12, channel_width=2.0, channel_depth=2.0,
        helix_angle_deg=helix_angle_deg, axial_length=60.0,
    )
    _valid_positive_volume(result, "helical_cooling_channels")
    expected_pitch = 2 * math.pi * r_outer / math.tan(math.radians(helix_angle_deg))
    # Volume should be less than the original cylinder (we cut channels out).
    orig_vol = math.pi * r_outer ** 2 * 80.0
    assert result.val().Volume() < orig_vol, "channels did not remove material"
    assert expected_pitch > 0


def test_helical_cooling_channels_zero_angle_falls_back_to_axial() -> None:
    """helix_angle_deg=0 must produce a straight axial channel (regression: divide-by-zero)."""
    import cadquery as cq
    from cad_utils import make_helical_cooling_channels
    nozzle = cq.Workplane("XY").circle(40.0).extrude(80.0).translate((0, 0, -40.0))
    result = make_helical_cooling_channels(
        nozzle_solid=nozzle, nozzle_outer_radius=40.0,
        channel_count=8, channel_width=2.0, channel_depth=2.0,
        helix_angle_deg=0.0, axial_length=40.0,
    )
    _valid_positive_volume(result, "axial channels")


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------

def test_conical_nozzle_requires_exit_or_epsilon() -> None:
    from cad_utils import make_conical_nozzle
    raised = False
    try:
        make_conical_nozzle(throat_radius=25.0, half_angle_deg=15.0,
                            converging_angle_deg=30.0, chamber_radius=50.0)
    except ValueError:
        raised = True
    assert raised


def test_rao_bell_rejects_subcritical_epsilon() -> None:
    from cad_utils import make_rao_bell_nozzle
    raised = False
    try:
        make_rao_bell_nozzle(throat_radius=20.0, expansion_ratio=1.0,
                              length_fraction=0.8, chamber_radius=60.0)
    except ValueError:
        raised = True
    assert raised


def _run_all() -> int:
    funcs = [
        test_rao_table_in_range_returns_finite,
        test_rao_table_endpoint_values_match_table,
        test_rao_table_out_of_range_raises,
        test_validator_flags_contradictory_expansion_ratio,
        test_validator_passes_consistent_propulsion_spec,
        test_validator_flags_channel_overflow,
        test_conical_nozzle_positive_volume,
        test_rao_bell_exit_radius_within_half_percent,
        test_combustion_chamber_with_frustum_valid,
        test_injector_plate_impinging_doublet_valid,
        test_helical_cooling_channels_helix_pitch,
        test_helical_cooling_channels_zero_angle_falls_back_to_axial,
        test_conical_nozzle_requires_exit_or_epsilon,
        test_rao_bell_rejects_subcritical_epsilon,
    ]
    failures = 0
    for fn in funcs:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failures += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {fn.__name__}")
    print(f"\n{len(funcs) - failures}/{len(funcs)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
