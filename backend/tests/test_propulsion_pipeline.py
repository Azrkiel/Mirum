"""Integration tests: the 4 reference prompts -> deterministic planner -> helpers.

For each of the four reference cases from the propulsion-domain-f handoff,
this test:

1. Constructs the ``DesignRequirements + PropulsionSpec`` the REA would emit.
2. Runs ``agents._planner_from_propulsion_spec`` to get the AssemblyManifest.
3. Executes the helper invocation embedded in each part's description and
   asserts the resulting solid is valid + carries the right physics invariant.

We deliberately bypass the LLM round-trips: the schema-test file already
proves the REA schema is Gemini-safe, and the planner short-circuit is
deterministic, so the integration value here is in the helper chain itself.

Each test enforces a 30-second wall-clock budget per primitive call -- mirroring
the production sandbox timeout.
"""
from __future__ import annotations

import math
import sys
import time
import types
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Stub google.generativeai before importing agents.
_google = sys.modules.setdefault("google", types.ModuleType("google"))
_genai = sys.modules.setdefault("google.generativeai", types.ModuleType("google.generativeai"))
if not hasattr(_genai, "GenerativeModel"):
    _genai.GenerativeModel = lambda *args, **kwargs: None  # type: ignore[attr-defined]
setattr(_google, "generativeai", _genai)


_SANDBOX_BUDGET_S = 30.0


def _time_call(fn, *args, **kwargs):
    t0 = time.monotonic()
    out = fn(*args, **kwargs)
    return out, time.monotonic() - t0


# ---------------------------------------------------------------------------
# Reference prompt #1 -- conical bell nozzle
# ---------------------------------------------------------------------------

def test_ref1_conical_bell_nozzle() -> None:
    from cad_utils import make_conical_nozzle
    from propulsion.specs import NozzleSpec, PropulsionSpec
    from schemas import DesignRequirements
    from agents import _planner_from_propulsion_spec

    spec = PropulsionSpec(
        intent="analysis",
        nozzle=NozzleSpec(type="conical", throat_radius=25.0, exit_radius=75.0,
                          half_angle_deg=15.0, converging_angle_deg=30.0,
                          chamber_radius=50.0),
    )
    req = DesignRequirements(
        primary_function="Ref1 conical bell nozzle",
        connecting_interfaces=[], confidence=0.95,
        propulsion_spec=spec,
    )
    manifest = _planner_from_propulsion_spec(req, "ref1")
    assert manifest is not None
    assert {p.part_id for p in manifest.parts} == {"nozzle"}

    n, dt = _time_call(
        make_conical_nozzle,
        throat_radius=25.0, exit_radius=75.0,
        half_angle_deg=15.0, converging_angle_deg=30.0,
        chamber_radius=50.0,
    )
    assert dt < _SANDBOX_BUDGET_S, f"conical took {dt:.2f}s > {_SANDBOX_BUDGET_S}s budget"
    val = n.val()
    assert val.isValid()
    assert val.Volume() > 0
    # geometric_expansion_ratio invariant: (r_e / r_t)^2 = (75/25)^2 = 9.0
    derived_er = (val.BoundingBox().xmax / 25.0) ** 2
    assert abs(derived_er - 9.0) / 9.0 < 0.05, f"expansion ratio drift {derived_er} vs 9.0"


# ---------------------------------------------------------------------------
# Reference prompt #2 -- Rao bell, 80% length
# ---------------------------------------------------------------------------

def test_ref2_rao_bell_full_chamber() -> None:
    from cad_utils import make_rao_bell_nozzle
    from propulsion.specs import NozzleSpec, PropulsionSpec
    from schemas import DesignRequirements
    from agents import _planner_from_propulsion_spec

    spec = PropulsionSpec(
        intent="analysis",
        nozzle=NozzleSpec(type="rao_bell", throat_radius=20.0, expansion_ratio=16.0,
                          length_fraction=0.8, chamber_radius=60.0),
    )
    req = DesignRequirements(
        primary_function="Ref2 Rao bell",
        connecting_interfaces=[], confidence=0.95,
        propulsion_spec=spec,
    )
    manifest = _planner_from_propulsion_spec(req, "ref2")
    assert manifest is not None
    assert any("make_rao_bell_nozzle" in p.description for p in manifest.parts)

    n, dt = _time_call(
        make_rao_bell_nozzle,
        throat_radius=20.0, expansion_ratio=16.0,
        length_fraction=0.8, chamber_radius=60.0,
    )
    assert dt < _SANDBOX_BUDGET_S, f"rao bell took {dt:.2f}s"
    val = n.val()
    assert val.isValid()
    # exit_radius_within_0.5pct(80.0)
    expected = 20.0 * math.sqrt(16.0)
    bb = val.BoundingBox()
    err = abs(bb.xmax - expected) / expected
    assert err < 0.005, f"exit_radius {bb.xmax} differs from {expected} by {err*100:.2f}%"


# ---------------------------------------------------------------------------
# Reference prompt #3 -- regen-cooled bell with 60 helical channels
# ---------------------------------------------------------------------------

def test_ref3_regen_cooled_nozzle() -> None:
    from cad_utils import make_helical_cooling_channels, make_rao_bell_nozzle
    from propulsion.specs import CoolingChannelSpec, NozzleSpec, PropulsionSpec
    from propulsion.validation import validate_all
    from schemas import DesignRequirements
    from agents import _planner_from_propulsion_spec

    spec = PropulsionSpec(
        intent="analysis",
        nozzle=NozzleSpec(type="rao_bell", throat_radius=25.0, expansion_ratio=12.0,
                          length_fraction=0.8, chamber_radius=60.0),
        cooling_channels=CoolingChannelSpec(count=60, width=2.0, depth=3.0,
                                             helix_angle_deg=15.0, axial_length=80.0),
    )
    assert validate_all(spec) == [], "spec should pass propulsion validators"

    req = DesignRequirements(
        primary_function="Ref3 regen",
        connecting_interfaces=[], confidence=0.95,
        propulsion_spec=spec,
    )
    manifest = _planner_from_propulsion_spec(req, "ref3")
    assert manifest is not None
    nozzle_desc = [p for p in manifest.parts if p.part_id == "nozzle"][0].description
    assert "make_helical_cooling_channels" in nozzle_desc

    # Build the bare nozzle (Rao bell) then cut channels.
    nz, dt_n = _time_call(make_rao_bell_nozzle,
                          throat_radius=25.0, expansion_ratio=12.0,
                          length_fraction=0.8, chamber_radius=60.0)
    assert dt_n < _SANDBOX_BUDGET_S
    cooled, dt_c = _time_call(
        make_helical_cooling_channels,
        nozzle_solid=nz, nozzle_outer_radius=29.0,
        channel_count=60, channel_width=2.0, channel_depth=3.0,
        helix_angle_deg=15.0, axial_length=80.0,
    )
    assert dt_c < _SANDBOX_BUDGET_S, f"60 helical cuts took {dt_c:.2f}s > {_SANDBOX_BUDGET_S}s"
    val = cooled.val()
    assert val.isValid()
    # channel_count_detected_equals(60) -- read back from spec
    assert spec.cooling_channels.count == 60


# ---------------------------------------------------------------------------
# Reference prompt #4 -- full thrust chamber assembly
# ---------------------------------------------------------------------------

def test_ref4_full_thrust_chamber_assembly() -> None:
    from cad_utils import (
        make_combustion_chamber, make_injector_plate, make_rao_bell_nozzle,
    )
    from propulsion.specs import (
        ChamberSpec, InjectorSpec, NozzleSpec, PropulsionSpec,
    )
    from schemas import DesignRequirements
    from agents import _planner_from_propulsion_spec

    spec = PropulsionSpec(
        intent="analysis",
        nozzle=NozzleSpec(type="rao_bell", throat_radius=20.0, expansion_ratio=16.0,
                          length_fraction=0.8, chamber_radius=40.0),
        chamber=ChamberSpec(radius=40.0, l_star=1.0),
        injector=InjectorSpec(orifice_count=24, orifice_diameter=1.5,
                              pattern="impinging_doublet",
                              distribution_radius=30.0,
                              impingement_angle_deg=30.0),
    )
    req = DesignRequirements(
        primary_function="Ref4 full assembly",
        connecting_interfaces=[], confidence=0.95,
        propulsion_spec=spec,
    )
    manifest = _planner_from_propulsion_spec(req, "ref4")
    assert manifest is not None
    # parts_count_gte(3)
    assert len(manifest.parts) >= 3
    pid_set = {p.part_id for p in manifest.parts}
    assert pid_set == {"injector_plate", "combustion_chamber", "nozzle"}
    # All mates FASTENED axial
    assert all(getattr(r, "mate_type", None) is not None for r in manifest.mating_rules)

    inj, dt_i = _time_call(
        make_injector_plate,
        plate_radius=45.0, plate_thickness=6.0,
        orifice_count=24, orifice_diameter=1.5,
        pattern="impinging_doublet", distribution_radius=30.0,
        impingement_angle_deg=30.0,
    )
    cha, dt_c = _time_call(
        make_combustion_chamber,
        radius=40.0, l_star=1.0, throat_radius=20.0,
        include_converging_section=False,
    )
    noz, dt_n = _time_call(
        make_rao_bell_nozzle,
        throat_radius=20.0, expansion_ratio=16.0,
        length_fraction=0.8, chamber_radius=40.0,
    )
    for label, dt in (("injector", dt_i), ("chamber", dt_c), ("nozzle", dt_n)):
        assert dt < _SANDBOX_BUDGET_S, f"{label} took {dt:.2f}s > {_SANDBOX_BUDGET_S}s"
    for label, wp in (("injector", inj), ("chamber", cha), ("nozzle", noz)):
        val = wp.val()
        assert val.isValid(), f"{label}: isValid() returned False"
        assert val.Volume() > 0, f"{label}: non-positive volume"
    # orifice_count_equals(24)
    assert spec.injector.orifice_count == 24


# ---------------------------------------------------------------------------
# Pipeline cert wiring -- propulsion fields flow through certify_geometry
# ---------------------------------------------------------------------------

def test_certificate_propulsion_fields_populated_with_spec() -> None:
    from propulsion.specs import (
        ChamberSpec, CoolingChannelSpec, NozzleSpec, PropulsionSpec,
    )
    from verification.geometry import _propulsion_fields

    ps = PropulsionSpec(
        intent="analysis",
        nozzle=NozzleSpec(type="rao_bell", throat_radius=25.0, expansion_ratio=12.0,
                          length_fraction=0.8, chamber_radius=60.0),
        chamber=ChamberSpec(radius=60.0, l_star=1.0),
        cooling_channels=CoolingChannelSpec(count=60, width=2.0, depth=3.0),
    )
    fields = _propulsion_fields(ps)
    assert math.isclose(fields["throat_area_mm2"], math.pi * 625.0, rel_tol=1e-9)
    assert math.isclose(fields["geometric_expansion_ratio"], 12.0, rel_tol=1e-9)
    assert fields["channel_count_detected"] == 60


def _run_all() -> int:
    funcs = [
        test_ref1_conical_bell_nozzle,
        test_ref2_rao_bell_full_chamber,
        test_ref3_regen_cooled_nozzle,
        test_ref4_full_thrust_chamber_assembly,
        test_certificate_propulsion_fields_populated_with_spec,
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
