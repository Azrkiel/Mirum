"""Phase 2, Step 1 — propulsion spec sampler.

Samples valid ``PropulsionSpec`` instances across five topology profiles,
uniformly within the envelope that ``propulsion.validation.validate_all``
accepts. Pure Python, no Gemini. Per Phase 2 plan (docs/propulsion/
phase2-synthetic-data.md) §3 Step 1: each candidate is checked with
``validate_all`` and rejected on failure; per-profile rejection rate is tracked
to surface sampler bugs and envelope gaps.

Field ranges are sized against the **strictest** intent's spacing budget
(manufacturable: 1.5 mm channel land, 1.0 mm injector gap) so a candidate is
valid for whatever ``intent`` is sampled.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from typing import Iterator, Optional

# Make the backend ``propulsion`` package importable (mirrors finetune_machinist.py).
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from propulsion.specs import (  # noqa: E402
    ChamberSpec,
    CoolingChannelSpec,
    InjectorSpec,
    NozzleSpec,
    PropulsionSpec,
)
from propulsion.validation import validate_all  # noqa: E402

PROFILES = (
    "nozzle_only",
    "chamber_plus_nozzle",
    "full_assembly_no_regen",
    "full_assembly_with_regen",
    "injector_only",
)

_INTENTS = ("analysis", "manufacturable", "visualization")
_INJECTOR_PATTERNS = ("impinging_doublet", "like_doublet", "coaxial", "showerhead")

# Rao-table envelope (propulsion.rao_table): epsilon in [5, 100], Lf in [0.6, 1.0].
_EPS_LO, _EPS_HI = 5.0, 100.0
_LF_LO, _LF_HI = 0.6, 1.0
# Strictest spacing budget (manufacturable) — size margins against this so any
# sampled intent passes validate_*.
_MAX_LAND_MM = 1.5
_MAX_GAP_MM = 1.0
_LINER_WALL_MM = 1.0  # matches validate_channel_geometry's assumption


class PropulsionSampler:
    """Reproducible (seedable) sampler of valid PropulsionSpec instances."""

    def __init__(self, seed: Optional[int] = None) -> None:
        self.rng = random.Random(seed)
        self.attempts = {p: 0 for p in PROFILES}
        self.accepts = {p: 0 for p in PROFILES}

    # ------------------------------------------------------------------ #
    # Sub-spec field samplers (feasibility-aware)
    # ------------------------------------------------------------------ #
    def _nozzle(self) -> NozzleSpec:
        rng = self.rng
        throat = round(rng.uniform(5.0, 80.0), 2)
        eps = round(rng.uniform(_EPS_LO, _EPS_HI), 2)
        chamber_r = round(throat * rng.uniform(1.6, 3.2), 2)
        # Supply expansion_ratio only (let exit_radius derive) so the
        # expansion-ratio consistency check has nothing to disagree with.
        if rng.random() < 0.5:
            return NozzleSpec(
                type="rao_bell",
                throat_radius=throat,
                expansion_ratio=eps,
                length_fraction=round(rng.uniform(_LF_LO, _LF_HI), 3),
                chamber_radius=chamber_r,
            )
        return NozzleSpec(
            type="conical",
            throat_radius=throat,
            expansion_ratio=eps,
            half_angle_deg=round(rng.uniform(12.0, 18.0), 1),
            converging_angle_deg=round(rng.uniform(25.0, 40.0), 1),
            chamber_radius=chamber_r,
        )

    def _chamber(self, radius: float) -> ChamberSpec:
        # Reuse the nozzle's chamber_radius so the two stay consistent.
        return ChamberSpec(radius=radius, l_star=round(self.rng.uniform(0.5, 1.2), 3))

    def _injector(self) -> InjectorSpec:
        rng = self.rng
        pattern = rng.choice(_INJECTOR_PATTERNS)
        count = rng.randint(6, 40)
        dia = round(rng.uniform(0.8, 3.0), 2)
        eff = count * (2 if pattern in ("impinging_doublet", "like_doublet") else 1)
        # Size the distribution circle so eff*(dia + gap) <= 2*pi*R with margin.
        min_r = eff * (dia + _MAX_GAP_MM) / (2 * math.pi)
        dist_r = round(min_r * rng.uniform(1.15, 1.6), 2)
        return InjectorSpec(
            orifice_count=count,
            orifice_diameter=dia,
            pattern=pattern,
            distribution_radius=dist_r,
        )

    def _cooling(self, nozzle: NozzleSpec) -> CoolingChannelSpec:
        rng = self.rng
        width = round(rng.uniform(1.0, 3.0), 2)
        depth = round(rng.uniform(1.5, 4.0), 2)
        # validate_channel_geometry budgets the throat-region perimeter at
        # r = throat + depth + liner_wall; keep count below what fits with land.
        circ = 2 * math.pi * (nozzle.throat_radius + depth + _LINER_WALL_MM)
        max_count = int(circ / (width + _MAX_LAND_MM) * 0.85)
        count = rng.randint(8, max(8, max_count))
        return CoolingChannelSpec(
            count=count,
            width=width,
            depth=depth,
            helix_angle_deg=round(rng.uniform(0.0, 25.0), 1),
        )

    # ------------------------------------------------------------------ #
    # Profile builder
    # ------------------------------------------------------------------ #
    def _build(self, profile: str) -> PropulsionSpec:
        intent = self.rng.choice(_INTENTS)
        if profile == "injector_only":
            return PropulsionSpec(intent=intent, injector=self._injector())

        nozzle = self._nozzle()
        if profile == "nozzle_only":
            return PropulsionSpec(intent=intent, nozzle=nozzle)

        chamber = self._chamber(nozzle.chamber_radius)
        if profile == "chamber_plus_nozzle":
            return PropulsionSpec(intent=intent, nozzle=nozzle, chamber=chamber)

        injector = self._injector()
        if profile == "full_assembly_no_regen":
            return PropulsionSpec(
                intent=intent, nozzle=nozzle, chamber=chamber, injector=injector
            )
        if profile == "full_assembly_with_regen":
            return PropulsionSpec(
                intent=intent,
                nozzle=nozzle,
                chamber=chamber,
                injector=injector,
                cooling_channels=self._cooling(nozzle),
            )
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
        specs cannot be produced within the attempt budget (signals a sampler
        bug or an over-tight envelope)."""
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
