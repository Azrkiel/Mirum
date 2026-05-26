"""Physics-validator passes for the aerospace ``PropulsionSpec``.

Each check returns a list of human-readable error strings (empty list = OK).
``validate_all`` runs every applicable check and returns the aggregated list, so
the REA path can surface all problems at once (see ``agents.run_rea``).

These are deliberately *lenient*: the REA only populates fields the prompt
explicitly states, so a check fires only on values that are non-physical on
their own (non-positive dimension, out-of-range angle) or on pairs that are
internally contradictory (a nozzle exit narrower than its throat, a cowl whose
outer highlight sits inside its fan face). Anything merely unspecified is fine.
"""

from __future__ import annotations

from typing import List

from .specs import PropulsionSpec


# Converging/diverging half-angles outside this band are non-physical for a
# revolved nozzle wall (0 would be a cylinder; >= 90 inverts the taper).
_ANGLE_LO, _ANGLE_HI = 0.0, 90.0


def _positive_dimensions(spec: PropulsionSpec) -> List[str]:
    """Every populated linear dimension must be strictly positive."""
    errors: List[str] = []
    dims = {
        "throat_diameter_mm":  spec.throat_diameter_mm,
        "exit_diameter_mm":    spec.exit_diameter_mm,
        "chamber_radius_mm":   spec.chamber_radius_mm,
        "chamber_length_mm":   spec.chamber_length_mm,
        "wall_thickness_mm":   spec.wall_thickness_mm,
        "highlight_radius_mm": spec.highlight_radius_mm,
        "fan_face_radius_mm":  spec.fan_face_radius_mm,
    }
    for name, value in dims.items():
        if value is not None and value <= 0:
            errors.append(f"{name}={value} must be > 0 (mm).")
    if spec.num_fuel_inlets is not None and spec.num_fuel_inlets < 0:
        errors.append(f"num_fuel_inlets={spec.num_fuel_inlets} must be >= 0.")
    return errors


def _angle_ranges(spec: PropulsionSpec) -> List[str]:
    """Converging / diverging half-angles must lie in (0, 90) degrees."""
    errors: List[str] = []
    for name, value in (
        ("conv_half_angle_deg", spec.conv_half_angle_deg),
        ("div_half_angle_deg",  spec.div_half_angle_deg),
    ):
        if value is not None and not (_ANGLE_LO < value < _ANGLE_HI):
            errors.append(
                f"{name}={value} out of physical range (expected ({_ANGLE_LO}, {_ANGLE_HI}) deg)."
            )
    return errors


def validate_nozzle(spec: PropulsionSpec) -> List[str]:
    """A converging-diverging nozzle expands flow: the exit must be wider than
    the throat when both are given."""
    if spec.throat_diameter_mm is not None and spec.exit_diameter_mm is not None:
        if spec.exit_diameter_mm < spec.throat_diameter_mm:
            return [
                f"exit_diameter_mm={spec.exit_diameter_mm} is smaller than "
                f"throat_diameter_mm={spec.throat_diameter_mm}; a C-D nozzle exit "
                f"must be at least as wide as the throat."
            ]
    return []


def validate_nacelle(spec: PropulsionSpec) -> List[str]:
    """A nacelle/inlet cowl wraps its inner duct: the highlight (outer lip)
    radius must exceed the fan-face (inner duct) radius when both are given."""
    if spec.highlight_radius_mm is not None and spec.fan_face_radius_mm is not None:
        if spec.highlight_radius_mm <= spec.fan_face_radius_mm:
            return [
                f"highlight_radius_mm={spec.highlight_radius_mm} must exceed "
                f"fan_face_radius_mm={spec.fan_face_radius_mm}; the outer cowl lip "
                f"cannot sit inside the inner duct."
            ]
    return []


def validate_all(propulsion_spec: PropulsionSpec) -> List[str]:
    """Run every applicable check. Returns the aggregated error list (empty = OK)."""
    errors: List[str] = []
    errors.extend(_positive_dimensions(propulsion_spec))
    errors.extend(_angle_ranges(propulsion_spec))

    ptype = (propulsion_spec.propulsion_type or "").strip().lower()
    if ptype == "nozzle":
        errors.extend(validate_nozzle(propulsion_spec))
    elif ptype == "nacelle":
        errors.extend(validate_nacelle(propulsion_spec))
    else:
        # For 'duct'/'chamber'/'generic'/unset, still apply the relational checks
        # opportunistically when the relevant field pairs happen to be present.
        errors.extend(validate_nozzle(propulsion_spec))
        errors.extend(validate_nacelle(propulsion_spec))

    return errors
