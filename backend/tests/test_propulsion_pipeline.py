"""Integration tests for the aerospace Domain F path.

The pre-pivot deterministic ``agents._planner_from_propulsion_spec`` short-circuit
was removed in the aerospace re-scope (Domain F now flows through the normal LLM
Planner + the ``rule_domain_f`` strategy), so the tests here cover what is still
deterministic:

1. ``verification.geometry._propulsion_fields`` derives the certificate's
   throat/exit areas + expansion ratio from the aerospace flat ``PropulsionSpec``.
2. The surviving helpers build valid solids for the canonical Domain F shapes,
   including the ``rule_domain_f`` "straight duct" pattern (a nacelle cowl with
   throat == fan_face) used for afterburner / transition ducts. (CadQuery.)

Each geometry call is held to the 30-second production sandbox budget.
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

# Stub google.generativeai before importing backend modules.
_google = sys.modules.setdefault("google", types.ModuleType("google"))
_genai = sys.modules.setdefault("google.generativeai", types.ModuleType("google.generativeai"))
if not hasattr(_genai, "GenerativeModel"):
    _genai.GenerativeModel = lambda *args, **kwargs: None  # type: ignore[attr-defined]
setattr(_google, "generativeai", _genai)

try:
    import cadquery as _cq  # noqa: F401
    _HAS_CADQUERY = True
    # See test_propulsion.py: cadquery < 2.7 spuriously flags the lip-filleted
    # nacelle solid invalid. Gate strict validity on the production kernel.
    _vparts = tuple(int(x) for x in _cq.__version__.split(".")[:2])
    _STRICT_VALIDITY = _vparts >= (2, 7)
except Exception:
    _HAS_CADQUERY = False
    _STRICT_VALIDITY = False

_SANDBOX_BUDGET_S = 30.0


def _skip_without_cadquery() -> None:
    if not _HAS_CADQUERY:
        try:
            import pytest
            pytest.skip("cadquery unavailable")
        except ImportError:
            raise RuntimeError("cadquery unavailable")


def _time_call(fn, *args, **kwargs):
    t0 = time.monotonic()
    out = fn(*args, **kwargs)
    return out, time.monotonic() - t0


# ---------------------------------------------------------------------------
# Certificate field derivation (pure Python)
# ---------------------------------------------------------------------------

def test_cert_fields_nozzle_areas_and_expansion() -> None:
    from propulsion.specs import PropulsionSpec
    from verification.geometry import _propulsion_fields
    spec = PropulsionSpec(propulsion_type="nozzle", throat_diameter_mm=40.0, exit_diameter_mm=130.0)
    fields = _propulsion_fields(spec)
    assert math.isclose(fields["throat_area_mm2"], math.pi * 20.0 ** 2, rel_tol=1e-9)
    assert math.isclose(fields["exit_area_mm2"], math.pi * 65.0 ** 2, rel_tol=1e-9)
    # geometric expansion ratio = (exit_d / throat_d)^2 = (130/40)^2 = 10.5625
    assert math.isclose(fields["geometric_expansion_ratio"], (130.0 / 40.0) ** 2, rel_tol=1e-9)
    # No rocket regen channels in aerospace Domain F.
    assert fields["channel_count_detected"] is None


def test_cert_fields_none_spec_is_all_none() -> None:
    from verification.geometry import _propulsion_fields
    fields = _propulsion_fields(None)
    assert all(v is None for v in fields.values())


def test_cert_fields_nacelle_without_nozzle_diameters_is_graceful() -> None:
    from propulsion.specs import PropulsionSpec
    from verification.geometry import _propulsion_fields
    spec = PropulsionSpec(propulsion_type="nacelle", highlight_radius_mm=250.0, fan_face_radius_mm=210.0)
    fields = _propulsion_fields(spec)
    assert fields["throat_area_mm2"] is None
    assert fields["geometric_expansion_ratio"] is None


# ---------------------------------------------------------------------------
# Helper integration (CadQuery)
# ---------------------------------------------------------------------------

def test_full_nozzle_build_within_budget() -> None:
    _skip_without_cadquery()
    from cad_utils import make_nozzle_profile
    n, dt = _time_call(
        make_nozzle_profile,
        chamber_radius=60.0, throat_radius=20.0, exit_radius=65.0, chamber_length=120.0,
        conv_half_angle_deg=30.0, div_half_angle_deg=15.0, wall_thickness=5.0,
    )
    assert dt < _SANDBOX_BUDGET_S, f"nozzle took {dt:.2f}s > {_SANDBOX_BUDGET_S}s budget"
    val = n.val()
    assert val.isValid() and val.Volume() > 0
    # The revolved bell reaches at least the exit radius in the radial plane.
    bb = val.BoundingBox()
    radial_extent = max(bb.ylen, bb.zlen) / 2.0
    assert radial_extent >= 65.0 - 1.0, f"radial extent {radial_extent} below exit radius 65"


def test_afterburner_straight_duct_via_nacelle_cowl() -> None:
    """rule_domain_f models a straight afterburner/transition duct as a nacelle
    cowl with throat == fan_face (constant inner bore)."""
    _skip_without_cadquery()
    from cad_utils import make_nacelle_cowl
    asm, dt = _time_call(
        make_nacelle_cowl,
        highlight_radius=83.0, throat_radius=80.0, fan_face_radius=80.0,
        intake_length=400.0, cowl_thickness=3.0, lip_radius=2.0,
    )
    assert dt < _SANDBOX_BUDGET_S, f"straight duct took {dt:.2f}s"
    comp = asm.toCompound()
    assert comp.Volume() > 0
    if _STRICT_VALIDITY:
        assert comp.isValid(), "straight-duct nacelle solid invalid on production kernel"


def _run_all() -> int:
    pure = [
        test_cert_fields_nozzle_areas_and_expansion,
        test_cert_fields_none_spec_is_all_none,
        test_cert_fields_nacelle_without_nozzle_diameters_is_graceful,
    ]
    geom = [
        test_full_nozzle_build_within_budget,
        test_afterburner_straight_duct_via_nacelle_cowl,
    ]
    funcs = list(pure)
    if _HAS_CADQUERY:
        funcs += geom
    else:
        for fn in geom:
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
