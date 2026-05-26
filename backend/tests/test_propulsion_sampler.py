"""Tests for the Phase 2 aerospace propulsion spec sampler
(data_pipeline/propulsion_sampler.py).

Pure-Python (pydantic only, no cadquery). Runnable via
`python backend/tests/test_propulsion_sampler.py` or pytest.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # worktree root
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(_ROOT / "data_pipeline"))

from propulsion.specs import PropulsionSpec  # noqa: E402
from propulsion.validation import validate_all  # noqa: E402
import propulsion_sampler as ps  # noqa: E402


def _first(sampler: "ps.PropulsionSampler", profile: str, tries: int = 40) -> PropulsionSpec:
    for _ in range(tries):
        spec = sampler.sample_one(profile)
        if spec is not None:
            return spec
    raise AssertionError(f"{profile}: no valid spec in {tries} tries")


def test_all_profiles_known() -> None:
    assert set(ps.PROFILES) == {
        "nozzle", "nozzle_with_chamber", "combustion_chamber",
        "nacelle_inlet", "afterburner_duct",
    }


def test_each_profile_samples_valid_spec() -> None:
    s = ps.PropulsionSampler(seed=1)
    for profile in ps.PROFILES:
        spec = _first(s, profile)
        assert isinstance(spec, PropulsionSpec)
        assert validate_all(spec) == [], f"{profile}: validate_all not empty: {validate_all(spec)}"


def test_profiles_populate_expected_fields() -> None:
    s = ps.PropulsionSampler(seed=2)
    n = _first(s, "nozzle")
    assert n.propulsion_type == "nozzle"
    assert n.throat_diameter_mm and n.exit_diameter_mm
    assert n.exit_diameter_mm > n.throat_diameter_mm

    nwc = _first(s, "nozzle_with_chamber")
    assert nwc.propulsion_type == "nozzle"
    assert nwc.throat_diameter_mm and nwc.chamber_radius_mm and nwc.chamber_length_mm

    cc = _first(s, "combustion_chamber")
    assert cc.propulsion_type == "chamber"
    assert cc.chamber_radius_mm and cc.chamber_length_mm and cc.num_fuel_inlets

    nac = _first(s, "nacelle_inlet")
    assert nac.propulsion_type == "nacelle"
    assert nac.highlight_radius_mm > nac.fan_face_radius_mm

    duct = _first(s, "afterburner_duct")
    assert duct.propulsion_type == "duct"
    assert duct.highlight_radius_mm > duct.fan_face_radius_mm


def test_generate_yields_requested_count_all_valid() -> None:
    s = ps.PropulsionSampler(seed=3)
    out = list(s.generate(50))
    assert len(out) == 50
    for profile, spec in out:
        assert profile in ps.PROFILES
        assert validate_all(spec) == []


def test_rejection_rate_below_10pct() -> None:
    s = ps.PropulsionSampler(seed=4)
    list(s.generate(200))
    assert s.rejection_rate() < 0.10, f"overall rejection {s.rejection_rate():.2%} exceeds 10%"


def test_seeded_reproducible() -> None:
    a = list(ps.PropulsionSampler(seed=7).generate(12))
    b = list(ps.PropulsionSampler(seed=7).generate(12))
    assert [p for p, _ in a] == [p for p, _ in b]
    assert [s.model_dump() for _, s in a] == [s.model_dump() for _, s in b]


def _run_all() -> int:
    funcs = [
        test_all_profiles_known,
        test_each_profile_samples_valid_spec,
        test_profiles_populate_expected_fields,
        test_generate_yields_requested_count_all_valid,
        test_rejection_rate_below_10pct,
        test_seeded_reproducible,
    ]
    failures = 0
    for fn in funcs:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {fn.__name__}")
    print(f"\n{len(funcs) - failures}/{len(funcs)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
