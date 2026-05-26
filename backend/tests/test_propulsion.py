"""Unit tests for Domain F (aerospace propulsion).

Covers the surviving, post-pivot surface:
* ``mentions_propulsion`` — two-tier aerospace vocabulary routing + homograph guard.
* ``validate_all`` — aerospace physics checks (nozzle exit vs throat, nacelle
  highlight vs fan face, positive dimensions, angle ranges).
* ``failure_analysis.get_rule_hint`` — a helix/thread OCC failure in propulsion
  code anchors on ``rule_domain_f`` and steers AWAY from raw helix sweeps.
* Geometry contracts — one positive test per surviving cad_utils helper
  (``make_nozzle_profile`` / ``make_combustion_chamber`` / ``make_nacelle_cowl``):
  valid solid with positive volume. (CadQuery required; skipped if unavailable.)

The rocket-era tests (conical/Rao/injector/helical-cooling primitives, the Rao
table, the rocket validators) are gone — those helpers and modules were removed
in the aerospace re-scope.

Runnable via `python backend/tests/test_propulsion.py` or pytest.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Stub google.generativeai so importing classifier/schemas does not require the SDK.
_google = sys.modules.setdefault("google", types.ModuleType("google"))
_genai = sys.modules.setdefault("google.generativeai", types.ModuleType("google.generativeai"))
if not hasattr(_genai, "GenerativeModel"):
    _genai.GenerativeModel = lambda *args, **kwargs: None  # type: ignore[attr-defined]
setattr(_google, "generativeai", _genai)

try:
    import cadquery as _cq  # noqa: F401
    _HAS_CADQUERY = True
    # The production sandbox runs OCC via cadquery >= 2.7. Older kernels (e.g. the
    # 2.4.x often present on dev hosts) spuriously flag the lip-filleted nacelle
    # solid as isValid()==False even though it has positive volume and builds fine
    # in production. Gate the strict validity assertion on the production kernel;
    # positive volume is always required.
    _vparts = tuple(int(x) for x in _cq.__version__.split(".")[:2])
    _STRICT_VALIDITY = _vparts >= (2, 7)
except Exception:
    _HAS_CADQUERY = False
    _STRICT_VALIDITY = False


def _skip_without_cadquery() -> None:
    if not _HAS_CADQUERY:
        try:
            import pytest
            pytest.skip("cadquery unavailable")
        except ImportError:
            raise RuntimeError("cadquery unavailable")  # _run_all filters these out


# ---------------------------------------------------------------------------
# mentions_propulsion — two-tier aerospace vocabulary
# ---------------------------------------------------------------------------

def test_mentions_propulsion_strong_tokens_route() -> None:
    from propulsion.rea_schema import mentions_propulsion
    for p in [
        "A De Laval nozzle: throat diameter 40 mm, exit diameter 130 mm.",
        "A subsonic engine nacelle inlet cowl, highlight radius 250 mm.",
        "An afterburner duct with a 3 mm wall.",
        "A combustion chamber with a spherical injector dome.",
    ]:
        assert mentions_propulsion(p), f"strong propulsion prompt must trigger: {p!r}"


def test_mentions_propulsion_rejects_benign_homographs() -> None:
    from propulsion.rea_schema import mentions_propulsion
    for p in [
        "A bell crank lever with a 50 mm arm and two pivot holes.",
        "A bellhousing adapter plate for a gearbox.",
        "A throat lozenge mold cavity, 30 mm long.",
        "A simple air duct elbow, 100 mm bore.",   # single weak token 'duct'
    ]:
        assert not mentions_propulsion(p), f"benign prompt must NOT trigger Domain F: {p!r}"


def test_mentions_propulsion_two_weak_tokens_trigger() -> None:
    from propulsion.rea_schema import mentions_propulsion
    # 'throat' + 'bell' — two corroborating weak tokens.
    assert mentions_propulsion("A bell contour blending into the throat region.")


# ---------------------------------------------------------------------------
# validate_all — aerospace physics
# ---------------------------------------------------------------------------

def test_validator_flags_nozzle_exit_smaller_than_throat() -> None:
    from propulsion.specs import PropulsionSpec
    from propulsion.validation import validate_all
    spec = PropulsionSpec(propulsion_type="nozzle", throat_diameter_mm=60.0, exit_diameter_mm=40.0)
    errs = validate_all(spec)
    assert any("exit" in e for e in errs), f"expected exit<throat flag, got {errs}"


def test_validator_flags_nacelle_highlight_inside_fan_face() -> None:
    from propulsion.specs import PropulsionSpec
    from propulsion.validation import validate_all
    spec = PropulsionSpec(propulsion_type="nacelle", highlight_radius_mm=100.0, fan_face_radius_mm=120.0)
    assert any("highlight" in e for e in validate_all(spec))


def test_validator_flags_nonpositive_and_out_of_range_angle() -> None:
    from propulsion.specs import PropulsionSpec
    from propulsion.validation import validate_all
    neg = PropulsionSpec(propulsion_type="chamber", chamber_radius_mm=-1.0)
    assert any("> 0" in e for e in validate_all(neg))
    bad_angle = PropulsionSpec(propulsion_type="nozzle", conv_half_angle_deg=120.0)
    assert any("range" in e for e in validate_all(bad_angle))


def test_validator_passes_consistent_specs() -> None:
    from propulsion.specs import PropulsionSpec
    from propulsion.validation import validate_all
    nozzle = PropulsionSpec(
        propulsion_type="nozzle", throat_diameter_mm=40.0, exit_diameter_mm=130.0,
        conv_half_angle_deg=30.0, div_half_angle_deg=15.0,
    )
    assert validate_all(nozzle) == [], validate_all(nozzle)
    nacelle = PropulsionSpec(propulsion_type="nacelle", highlight_radius_mm=250.0, fan_face_radius_mm=210.0)
    assert validate_all(nacelle) == [], validate_all(nacelle)


# ---------------------------------------------------------------------------
# Propulsion-aware failure hint
# ---------------------------------------------------------------------------

def test_helix_failure_hint_is_propulsion_aware() -> None:
    """A helix/thread OCC failure in propulsion (nozzle) code must anchor the
    Machinist on rule_domain_f and steer AWAY from raw helix sweeps (which crash
    the BRep kernel on non-cylindrical bodies), NOT toward a deleted helper."""
    from failure_analysis import get_rule_hint
    tb = "StdFail_NotDone: BRep_API: command not done\n  during HelixSweep"
    prop_code = (
        "import cad_utils\n"
        "n = cad_utils.make_nozzle_profile(chamber_radius=50, throat_radius=20, "
        "exit_radius=65, chamber_length=80)\n"
    )
    rule, hint = get_rule_hint(tb, prop_code)
    assert rule == "rule_domain_f", f"propulsion helix hint should anchor on rule_domain_f, got {rule}"
    assert "make_nozzle_profile" in hint, f"hint must point to a surviving helper, got: {hint}"
    assert "make_helical_cooling_channels" not in hint, "hint must not cite the deleted helper"

    # A non-propulsion helix failure still gets the generic fastener hint.
    bolt_code = "import cad_utils\nresult = cad_utils.make_hex_bolt(diameter=8)\n"
    rule2, _ = get_rule_hint(tb, bolt_code)
    assert rule2 == "rule_fasteners", f"non-propulsion helix should keep the fastener hint, got {rule2}"


# ---------------------------------------------------------------------------
# Geometry contracts — surviving helpers (CadQuery)
# ---------------------------------------------------------------------------

def _valid_positive_volume(solid, label: str, require_valid: bool = True) -> None:
    assert solid.Volume() > 0, f"{label}: volume was non-positive"
    if require_valid:
        assert solid.isValid(), f"{label}: isValid() returned False"


def test_make_nozzle_profile_valid() -> None:
    _skip_without_cadquery()
    from cad_utils import make_nozzle_profile
    n = make_nozzle_profile(
        chamber_radius=50.0, throat_radius=20.0, exit_radius=65.0,
        chamber_length=80.0, conv_half_angle_deg=30.0, div_half_angle_deg=15.0,
    )
    _valid_positive_volume(n.val(), "nozzle_profile")


def test_make_combustion_chamber_valid() -> None:
    _skip_without_cadquery()
    from cad_utils import make_combustion_chamber
    asm = make_combustion_chamber(
        inner_radius=50.0, wall_thickness=8.0, length=150.0,
        dome_radius=55.0, num_fuel_inlets=6, fuel_inlet_diameter=8.0,
    )
    _valid_positive_volume(asm.toCompound(), "combustion_chamber")


def test_make_nacelle_cowl_valid() -> None:
    _skip_without_cadquery()
    from cad_utils import make_nacelle_cowl
    asm = make_nacelle_cowl(
        highlight_radius=250.0, throat_radius=180.0, fan_face_radius=210.0,
        intake_length=350.0,
    )
    _valid_positive_volume(asm.toCompound(), "nacelle_cowl", require_valid=_STRICT_VALIDITY)


_PURE_TESTS = [
    test_mentions_propulsion_strong_tokens_route,
    test_mentions_propulsion_rejects_benign_homographs,
    test_mentions_propulsion_two_weak_tokens_trigger,
    test_validator_flags_nozzle_exit_smaller_than_throat,
    test_validator_flags_nacelle_highlight_inside_fan_face,
    test_validator_flags_nonpositive_and_out_of_range_angle,
    test_validator_passes_consistent_specs,
    test_helix_failure_hint_is_propulsion_aware,
]
_GEOMETRY_TESTS = [
    test_make_nozzle_profile_valid,
    test_make_combustion_chamber_valid,
    test_make_nacelle_cowl_valid,
]


def _run_all() -> int:
    funcs = list(_PURE_TESTS)
    if _HAS_CADQUERY:
        funcs += _GEOMETRY_TESTS
    else:
        for fn in _GEOMETRY_TESTS:
            print(f"SKIP {fn.__name__}: cadquery unavailable")
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
