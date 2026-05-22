"""Physics-validator passes for ``PropulsionSpec``.

Each ``validate_*`` returns a list of human-readable error strings (empty
list = OK). ``validate_all`` runs every applicable check and returns the
aggregated list so the REA retry channel can show all problems at once.
"""

from __future__ import annotations

import math
from typing import List

from .specs import (
    ChamberSpec,
    CoolingChannelSpec,
    InjectorSpec,
    NozzleSpec,
    PropulsionSpec,
)


# Per-intent minimum-wall constants (mm). Visualization-only intent slackens
# the spacing budget so artists can iterate without engineering rejection.
_MIN_LAND_MM = {
    "analysis":       1.0,
    "manufacturable": 1.5,
    "visualization":  0.2,
}
_MIN_INJECTOR_GAP_MM = {
    "analysis":       0.5,
    "manufacturable": 1.0,
    "visualization":  0.1,
}


def validate_expansion_ratio(spec: NozzleSpec) -> List[str]:
    """Cross-check ``expansion_ratio`` vs ``exit_radius / throat_radius``.

    Returns one error if both are given and disagree by more than 2 percent.
    If exactly one is given, the missing one is inferred (no error).
    """
    if spec.throat_radius is None:
        return ["NozzleSpec.throat_radius is required for expansion-ratio check."]

    if spec.expansion_ratio is None and spec.exit_radius is None:
        return ["NozzleSpec must supply either expansion_ratio or exit_radius."]

    if spec.expansion_ratio is not None and spec.exit_radius is not None:
        derived = (spec.exit_radius / spec.throat_radius) ** 2
        rel_err = abs(derived - spec.expansion_ratio) / spec.expansion_ratio
        if rel_err > 0.02:
            return [
                f"expansion_ratio={spec.expansion_ratio:.3f} disagrees with "
                f"derived (r_e/r_t)^2={derived:.3f} by {rel_err * 100:.1f}% "
                f"(>2% tolerance)."
            ]
    return []


def derive_missing_nozzle_fields(spec: NozzleSpec) -> NozzleSpec:
    """Return a copy of ``spec`` with derivable fields filled in.

    * Fills ``exit_radius`` from ``throat_radius * sqrt(expansion_ratio)``.
    * Fills ``expansion_ratio`` from ``(exit_radius / throat_radius) ** 2``.

    Caller responsibility: run ``validate_expansion_ratio`` first to confirm
    consistency.
    """
    update = {}
    if spec.expansion_ratio is None and spec.exit_radius is not None and spec.throat_radius is not None:
        update["expansion_ratio"] = (spec.exit_radius / spec.throat_radius) ** 2
    elif spec.exit_radius is None and spec.expansion_ratio is not None and spec.throat_radius is not None:
        update["exit_radius"] = spec.throat_radius * math.sqrt(spec.expansion_ratio)
    return spec.model_copy(update=update) if update else spec


def validate_channel_geometry(
    channels: CoolingChannelSpec,
    nozzle: NozzleSpec,
    intent: str,
) -> List[str]:
    """Check that ``count`` channels of ``width`` + min land fit at the throat circumference.

    Uses the throat-circle perimeter because that is the tightest constriction
    in the regen jacket. The wall radius is approximated as ``throat_radius +
    channel_depth + 0.5 mm`` (depth into the outer wall plus a token wall).
    """
    if nozzle.throat_radius is None:
        return ["Cooling-channel check skipped: throat_radius missing."]
    if channels.count <= 0:
        return ["CoolingChannelSpec.count must be > 0."]
    if channels.width <= 0 or channels.depth <= 0:
        return ["CoolingChannelSpec.width and depth must be > 0."]

    min_land = _MIN_LAND_MM.get(intent, _MIN_LAND_MM["analysis"])
    # Channels are milled into the jacket wall which sits outside the nozzle
    # liner. Assume a 1.0 mm liner wall as the minimum for regen designs --
    # this is realistic (thin walls are needed for heat transfer) and matches
    # the geometry of typical pump-fed engines (e.g. RL10, Vulcain).
    nozzle_wall = 1.0
    wall_outer_radius = nozzle.throat_radius + channels.depth + nozzle_wall
    circumference = 2 * math.pi * wall_outer_radius
    required = channels.count * (channels.width + min_land)
    if required > circumference:
        return [
            f"{channels.count} channels of width {channels.width:.2f} mm with "
            f"land {min_land:.2f} mm need {required:.2f} mm of perimeter; "
            f"available throat-region perimeter is only "
            f"{circumference:.2f} mm at r_outer={wall_outer_radius:.2f} mm "
            f"(wall={nozzle_wall:.2f} mm assumed)."
        ]
    return []


def validate_injector_clearance(
    injector: InjectorSpec,
    intent: str,
) -> List[str]:
    """Check that orifices fit on the distribution circle without overlap.

    For ``impinging_doublet`` the helper places 2*count orifices, so the
    effective count doubles.
    """
    if injector.orifice_count <= 0 or injector.orifice_diameter <= 0:
        return ["InjectorSpec.orifice_count and orifice_diameter must be > 0."]
    if injector.distribution_radius <= 0:
        return ["InjectorSpec.distribution_radius must be > 0."]

    eff_count = injector.orifice_count
    if injector.pattern in ("impinging_doublet", "like_doublet"):
        eff_count *= 2

    gap = _MIN_INJECTOR_GAP_MM.get(intent, _MIN_INJECTOR_GAP_MM["analysis"])
    perimeter = 2 * math.pi * injector.distribution_radius
    required = eff_count * (injector.orifice_diameter + gap)
    if required > perimeter:
        return [
            f"Pattern '{injector.pattern}' needs {eff_count} holes of diameter "
            f"{injector.orifice_diameter:.2f} mm plus {gap:.2f} mm gap "
            f"({required:.2f} mm) but the distribution circle perimeter is only "
            f"{perimeter:.2f} mm. Reduce orifice_count or increase distribution_radius."
        ]
    return []


def validate_chamber(chamber: ChamberSpec, nozzle: NozzleSpec | None) -> List[str]:
    """Sanity-check the chamber spec and (when paired) its relation to the nozzle."""
    errors: List[str] = []
    if chamber.radius <= 0:
        errors.append("ChamberSpec.radius must be > 0.")
    if chamber.l_star <= 0:
        errors.append("ChamberSpec.l_star must be > 0.")
    if nozzle is not None and nozzle.throat_radius is not None:
        if chamber.radius <= nozzle.throat_radius:
            errors.append(
                f"ChamberSpec.radius ({chamber.radius:.2f}) must exceed "
                f"throat_radius ({nozzle.throat_radius:.2f})."
            )
        derived_contraction = (chamber.radius / nozzle.throat_radius) ** 2
        if chamber.contraction_ratio is not None:
            rel_err = abs(derived_contraction - chamber.contraction_ratio) / chamber.contraction_ratio
            if rel_err > 0.02:
                errors.append(
                    f"contraction_ratio={chamber.contraction_ratio:.3f} disagrees "
                    f"with derived (r_c/r_t)^2={derived_contraction:.3f}."
                )
    return errors


def validate_all(propulsion_spec: PropulsionSpec) -> List[str]:
    """Run every applicable sub-validator. Returns the aggregated error list."""
    errors: List[str] = []
    intent = propulsion_spec.intent

    if propulsion_spec.nozzle is not None:
        errors.extend(validate_expansion_ratio(propulsion_spec.nozzle))

    if propulsion_spec.cooling_channels is not None and propulsion_spec.nozzle is not None:
        errors.extend(
            validate_channel_geometry(
                propulsion_spec.cooling_channels,
                propulsion_spec.nozzle,
                intent,
            )
        )

    if propulsion_spec.injector is not None:
        errors.extend(validate_injector_clearance(propulsion_spec.injector, intent))

    if propulsion_spec.chamber is not None:
        errors.extend(validate_chamber(propulsion_spec.chamber, propulsion_spec.nozzle))

    return errors
