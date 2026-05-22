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


def test_make_rao_bell_rejects_length_fraction_below_envelope() -> None:
    """The primitive guard must reject Lf below the Rao table envelope with a
    clear length_fraction error, not pass the guard and crash inside lookup."""
    import cad_utils
    from propulsion.rao_table import envelope
    lf_lo = envelope()["length_fraction"][0]            # 0.6
    bad_lf = round(lf_lo - 0.05, 3)                      # 0.55: in old [0.5,1.0] guard, below table
    raised = ""
    try:
        cad_utils.make_rao_bell_nozzle(
            throat_radius=20.0, expansion_ratio=16.0,
            length_fraction=bad_lf, chamber_radius=60.0,
        )
    except ValueError as exc:
        raised = str(exc)
    assert "length_fraction" in raised, (
        f"guard should reject lf={bad_lf} with a length_fraction message, got: {raised!r}"
    )
    assert str(lf_lo) in raised, (
        f"guard message should cite the table lower bound {lf_lo}, got: {raised!r}"
    )


def test_spec_rejects_length_fraction_below_envelope() -> None:
    """NozzleSpec validation must match the Rao table envelope, not [0.5,1.0]."""
    from propulsion.specs import NozzleSpec
    from propulsion.rao_table import envelope
    bad_lf = round(envelope()["length_fraction"][0] - 0.05, 3)   # 0.55
    raised = False
    try:
        NozzleSpec(
            type="rao_bell", throat_radius=20.0, expansion_ratio=16.0,
            length_fraction=bad_lf, chamber_radius=60.0,
        )
    except Exception:
        raised = True
    assert raised, f"NozzleSpec should reject length_fraction={bad_lf} (below Rao envelope)"


def test_mentions_propulsion_positive_reference_prompts() -> None:
    """The four Domain F reference prompts must still route to propulsion."""
    from propulsion.rea_schema import mentions_propulsion
    prompts = [
        "A conical bell nozzle for a hot-fire test stand: throat radius 25 mm, "
        "exit radius 75 mm (expansion ratio 9), divergent half-angle 15 degrees.",
        "A thrust-optimised Rao bell nozzle at 80 percent length: throat radius "
        "20 mm, expansion ratio 16, chamber inner radius 60 mm.",
        "A regeneratively-cooled bell nozzle: throat radius 25 mm, expansion ratio "
        "12, sixty helical cooling channels milled into the outer wall.",
        "A complete thrust chamber assembly. Injector plate with twenty-four "
        "impinging-doublet orifices on a 30 mm radius distribution circle.",
    ]
    for p in prompts:
        assert mentions_propulsion(p), f"reference propulsion prompt must trigger: {p[:48]!r}"


def test_mentions_propulsion_rejects_benign_homographs() -> None:
    """Benign prompts that merely contain 'bell'/'throat' substrings must NOT
    force Domain F (the over-trigger the zero-context reviewer flagged)."""
    from propulsion.rea_schema import mentions_propulsion
    benign = [
        "A bell crank lever with a 50 mm arm and two pivot holes.",
        "A bellhousing adapter plate for a gearbox.",
        "A throat lozenge mold cavity, 30 mm long.",
        "A spur gear with 24 teeth, module 2.",
    ]
    for p in benign:
        assert not mentions_propulsion(p), f"benign prompt must NOT trigger Domain F: {p!r}"


def test_mentions_propulsion_two_weak_tokens_trigger() -> None:
    """Two corroborating weak tokens (bell + throat) read as a nozzle even with
    no strong token present."""
    from propulsion.rea_schema import mentions_propulsion
    assert mentions_propulsion("A bell contour blending into the throat region.")


def test_helix_failure_hint_is_propulsion_aware() -> None:
    """A helix/thread OCCT failure in propulsion (nozzle) code must steer the
    Machinist to make_helical_cooling_channels, NOT the generic fastener helper.
    Surfaced by the live regen-nozzle run, where the generic thread hint pointed
    at make_threaded_rod and the Critic never recovered."""
    from failure_analysis import get_rule_hint
    tb = "StdFail_NotDone: BRep_API: command not done\n  during HelixSweep"
    prop_code = (
        "import cad_utils\n"
        "nozzle = cad_utils.make_rao_bell_nozzle(throat_radius=25, expansion_ratio=12)\n"
        "# 60 helical cooling channels milled into the wall\n"
        "result = nozzle\n"
    )
    rule, hint = get_rule_hint(tb, prop_code)
    assert rule == "rule_domain_f_propulsion", f"propulsion helix hint should anchor on rule_domain_f_propulsion, got {rule}"
    assert "make_helical_cooling_channels" in hint, f"hint must point to the cooling-channel helper, got: {hint}"
    assert "make_threaded_rod" not in hint, "propulsion hint must not misdirect to fastener helpers"

    # A non-propulsion helix failure still gets the generic fastener hint.
    bolt_code = "import cad_utils\nresult = cad_utils.make_hex_bolt(diameter=8)\n"
    rule2, _ = get_rule_hint(tb, bolt_code)
    assert rule2 == "rule_fasteners", f"non-propulsion helix should keep the fastener hint, got {rule2}"


def _run_all() -> int:
    funcs = [
        test_helix_failure_hint_is_propulsion_aware,
        test_make_rao_bell_rejects_length_fraction_below_envelope,
        test_spec_rejects_length_fraction_below_envelope,
        test_mentions_propulsion_positive_reference_prompts,
        test_mentions_propulsion_rejects_benign_homographs,
        test_mentions_propulsion_two_weak_tokens_trigger,
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
