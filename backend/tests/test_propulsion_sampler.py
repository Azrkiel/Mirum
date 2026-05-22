"""Tests for the Phase 2 propulsion spec sampler (data_pipeline/propulsion_sampler.py).

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
        "nozzle_only", "chamber_plus_nozzle", "full_assembly_no_regen",
        "full_assembly_with_regen", "injector_only",
    }


def test_each_profile_samples_valid_spec() -> None:
    s = ps.PropulsionSampler(seed=1)
    for profile in ps.PROFILES:
        spec = _first(s, profile)
        assert isinstance(spec, PropulsionSpec)
        assert validate_all(spec) == [], f"{profile}: validate_all not empty: {validate_all(spec)}"


def test_profiles_populate_expected_subsystems() -> None:
    s = ps.PropulsionSampler(seed=2)
    n = _first(s, "nozzle_only")
    assert n.nozzle and not n.chamber and not n.injector and not n.cooling_channels
    inj = _first(s, "injector_only")
    assert inj.injector and not inj.nozzle and not inj.chamber and not inj.cooling_channels
    cpn = _first(s, "chamber_plus_nozzle")
    assert cpn.nozzle and cpn.chamber and not cpn.injector and not cpn.cooling_channels
    fa = _first(s, "full_assembly_no_regen")
    assert fa.nozzle and fa.chamber and fa.injector and not fa.cooling_channels
    far = _first(s, "full_assembly_with_regen")
    assert far.nozzle and far.chamber and far.injector and far.cooling_channels


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
        test_profiles_populate_expected_subsystems,
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
