"""Canonical Pydantic spec for Domain F (Propulsion / axisymmetric bodies).

After the rocket → aerospace re-scope, Domain F covers converging-diverging
nozzles, combustion chambers, nacelle/inlet cowls, and afterburner ducts — all
built by the deterministic revolve-first helpers in ``cad_utils``
(``make_nozzle_profile`` / ``make_combustion_chamber`` / ``make_nacelle_cowl``).

This module is the single source of truth for the propulsion spec shape. It is
imported by ``schemas.DesignRequirements`` (the live REA output), mirrored as a
Gemini ``response_schema`` in ``rea_schema.PROPULSION_SPEC_SCHEMA``, physics-checked
by ``validation.validate_all``, and sampled by the Phase 2 data pipeline.

All fields are optional: the REA populates only what the prompt states.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PropulsionSpec(BaseModel):
    """Propulsion geometry parameters for Domain F parts.

    Populated by the REA from Domain F prompts (rocket nozzles, combustion
    chambers, nacelle inlets, afterburner ducts). Used by the Machinist to
    parameterize the correct propulsion helper from ``cad_utils``.
    """

    propulsion_type: Optional[str] = Field(
        default=None,
        description="Component type: 'nozzle', 'chamber', 'nacelle', 'duct', or 'generic'.",
    )
    throat_diameter_mm:    Optional[float] = Field(default=None, description="Nozzle throat diameter (mm).")
    exit_diameter_mm:      Optional[float] = Field(default=None, description="Nozzle exit plane diameter (mm).")
    chamber_radius_mm:     Optional[float] = Field(default=None, description="Combustion chamber inner radius (mm).")
    chamber_length_mm:     Optional[float] = Field(default=None, description="Combustion chamber cylindrical length (mm).")
    wall_thickness_mm:     Optional[float] = Field(default=None, description="Component wall thickness (mm).")
    num_fuel_inlets:       Optional[int]   = Field(default=None, description="Number of fuel inlet ports.")
    highlight_radius_mm:   Optional[float] = Field(default=None, description="Nacelle inlet highlight outer radius (mm).")
    fan_face_radius_mm:    Optional[float] = Field(default=None, description="Nacelle fan face inner radius (mm).")
    conv_half_angle_deg:   Optional[float] = Field(default=None, description="Nozzle converging section half-angle (degrees).")
    div_half_angle_deg:    Optional[float] = Field(default=None, description="Nozzle diverging section half-angle (degrees).")
