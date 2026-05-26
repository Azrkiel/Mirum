"""Phase 2, Step 1 — aerospace propulsion spec sampler.

Samples valid aerospace ``PropulsionSpec`` instances across the Domain F shape
profiles (nozzle, nozzle+chamber, combustion chamber, nacelle/inlet cowl,
afterburner duct), uniformly within the envelope that ``propulsion.validation.
validate_all`` accepts. Pure Python, no Gemini.

After the rocket → aerospace re-scope, Domain F geometry is produced by the
revolve-first helpers ``make_nozzle_profile`` / ``make_combustion_chamber`` /
``make_nacelle_cowl``. The rocket sampler (Rao bells, helical cooling channels,
injector plates) is gone — those helpers and the rocket spec were removed.

Each candidate is checked with ``validate_all`` and rejected on failure;
per-profile rejection rate is tracked to surface sampler bugs and envelope gaps.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Iterator, Optional

# Make the backend ``propulsion`` package importable (mirrors finetune_machinist.py).
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from propulsion.specs import PropulsionSpec  # noqa: E402
from propulsion.validation import validate_all  # noqa: E402

PROFILES = (
    "nozzle",
    "nozzle_with_chamber",
    "combustion_chamber",
    "nacelle_inlet",
    "afterburner_duct",
)

_FUEL_INLET_COUNTS = (4, 6, 8)


class PropulsionSampler:
    """Reproducible (seedable) sampler of valid aerospace PropulsionSpec instances."""

    def __init__(self, seed: Optional[int] = None) -> None:
        self.rng = random.Random(seed)
        self.attempts = {p: 0 for p in PROFILES}
        self.accepts = {p: 0 for p in PROFILES}

    # ------------------------------------------------------------------ #
    # Field samplers (feasibility-aware — sized so validate_all passes)
    # ------------------------------------------------------------------ #
    def _nozzle_fields(self) -> dict:
        rng = self.rng
        throat_d = round(rng.uniform(10.0, 80.0), 2)
        # C-D nozzle: exit strictly wider than the throat (expansion ratio 1.5–4x diameter).
        exit_d = round(throat_d * rng.uniform(1.5, 4.0), 2)
        return {
            "propulsion_type": "nozzle",
            "throat_diameter_mm": throat_d,
            "exit_diameter_mm": exit_d,
            "conv_half_angle_deg": round(rng.uniform(15.0, 45.0), 1),
            "div_half_angle_deg": round(rng.uniform(8.0, 25.0), 1),
            "wall_thickness_mm": round(rng.uniform(2.0, 10.0), 2),
        }

    def _chamber_fields(self) -> dict:
        rng = self.rng
        return {
            "chamber_radius_mm": round(rng.uniform(20.0, 120.0), 2),
            "chamber_length_mm": round(rng.uniform(50.0, 300.0), 2),
            "wall_thickness_mm": round(rng.uniform(3.0, 12.0), 2),
            "num_fuel_inlets": rng.choice(_FUEL_INLET_COUNTS),
        }

    def _nacelle_fields(self) -> dict:
        rng = self.rng
        highlight = round(rng.uniform(120.0, 400.0), 2)
        # Inner fan face strictly inside the outer highlight lip.
        fan_face = round(highlight * rng.uniform(0.6, 0.92), 2)
        return {
            "propulsion_type": "nacelle",
            "highlight_radius_mm": highlight,
            "fan_face_radius_mm": fan_face,
            "wall_thickness_mm": round(rng.uniform(3.0, 12.0), 2),
        }

    def _duct_fields(self) -> dict:
        rng = self.rng
        # Straight/afterburner duct: constant inner bore, outer = inner + wall.
        inner = round(rng.uniform(40.0, 150.0), 2)
        wall = round(rng.uniform(2.0, 8.0), 2)
        return {
            "propulsion_type": "duct",
            "fan_face_radius_mm": inner,
            "highlight_radius_mm": round(inner + wall, 2),
            "wall_thickness_mm": wall,
        }

    # ------------------------------------------------------------------ #
    # Profile builder
    # ------------------------------------------------------------------ #
    def _build(self, profile: str) -> PropulsionSpec:
        if profile == "nozzle":
            return PropulsionSpec(**self._nozzle_fields())
        if profile == "nozzle_with_chamber":
            fields = self._nozzle_fields()
            chamber = self._chamber_fields()
            # Keep one wall thickness; chamber radius must clear the throat radius.
            chamber.pop("wall_thickness_mm", None)
            chamber["chamber_radius_mm"] = round(
                max(chamber["chamber_radius_mm"], fields["throat_diameter_mm"] / 2.0 * 1.5), 2
            )
            fields.update(chamber)
            return PropulsionSpec(**fields)
        if profile == "combustion_chamber":
            return PropulsionSpec(propulsion_type="chamber", **self._chamber_fields())
        if profile == "nacelle_inlet":
            return PropulsionSpec(**self._nacelle_fields())
        if profile == "afterburner_duct":
            return PropulsionSpec(**self._duct_fields())
        raise ValueError(f"unknown profile: {profile}")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def sample_one(self, profile: str) -> Optional[PropulsionSpec]:
        """Sample one candidate for ``profile``. Returns None if it fails the
        Pydantic validators or ``validate_all`` (counted as a rejection)."""
        if profile not in self.attempts:
            raise ValueError(f"unknown profile: {profile!r}")
        self.attempts[profile] += 1
        try:
            spec = self._build(profile)
        except Exception:
            return None
        if validate_all(spec):
            return None
        self.accepts[profile] += 1
        return spec

    def generate(
        self,
        n: int,
        profiles: tuple[str, ...] = PROFILES,
        max_attempts_per_accept: int = 50,
    ) -> Iterator[tuple[str, PropulsionSpec]]:
        """Yield ``n`` ``(profile, spec)`` pairs, round-robin across ``profiles``.

        Raises ``RuntimeError`` if the rejection rate is so high that ``n`` valid
        specs cannot be produced within the attempt budget (signals a sampler bug
        or an over-tight envelope)."""
        produced = 0
        guard = 0
        limit = max(1, n) * max_attempts_per_accept + len(profiles)
        while produced < n and guard < limit:
            profile = profiles[produced % len(profiles)]
            guard += 1
            spec = self.sample_one(profile)
            if spec is not None:
                produced += 1
                yield profile, spec
        if produced < n:
            raise RuntimeError(
                f"sampler produced only {produced}/{n} valid specs in {guard} "
                f"attempts (rejection rate {self.rejection_rate():.1%})"
            )

    def rejection_rate(self, profile: Optional[str] = None) -> float:
        """Fraction of attempts rejected, overall or for one profile."""
        if profile is not None:
            attempts = self.attempts[profile]
            return 0.0 if attempts == 0 else 1.0 - self.accepts[profile] / attempts
        total_attempts = sum(self.attempts.values())
        total_accepts = sum(self.accepts.values())
        return 0.0 if total_attempts == 0 else 1.0 - total_accepts / total_attempts
