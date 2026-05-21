"""Pydantic specs for Domain F (Propulsion).

Layout
------
- ``NozzleSpec``           — single bell/conical nozzle geometry (per-type validated)
- ``CoolingChannelSpec``   — regenerative cooling channel set
- ``InjectorSpec``         — injector plate orifice pattern
- ``ChamberSpec``          — combustion chamber + L* sizing
- ``PropulsionSpec``       — sibling composer + ``intent`` flag

All sub-specs are siblings of ``PropulsionSpec`` (depth-2 from
``DesignRequirements``). Sub-fields are nullable to keep the Gemini-emitted
schema flat (no ``anyOf`` after sanitization).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Intent flag — gates downstream Critic strictness
# ---------------------------------------------------------------------------
Intent = Literal["analysis", "manufacturable", "visualization"]
"""Downstream use of the generated geometry.

- ``analysis``      — CFD-ready watertight; inlet/outlet faces tagged.
- ``manufacturable`` — minimum-wall checks, fillets on internal corners.
- ``visualization`` — geometry only; gating checks skipped.
"""


# ---------------------------------------------------------------------------
# NozzleSpec
# ---------------------------------------------------------------------------
class NozzleSpec(BaseModel):
    """Single bell-or-conical nozzle.

    Field requirements depend on ``type``:

    * ``conical``: ``throat_radius``, ``exit_radius`` (or ``expansion_ratio``),
      ``half_angle_deg``, ``converging_angle_deg``, ``chamber_radius``.
    * ``rao_bell`` / ``truncated_bell``: ``throat_radius``, ``expansion_ratio``,
      ``chamber_radius``. ``length_fraction`` defaults to 0.8 when omitted.

    Unused fields stay ``None`` so the Gemini schema flattens cleanly.
    """

    type: Literal["conical", "rao_bell", "truncated_bell"] = Field(
        ...,
        description=(
            "Nozzle topology. 'conical' = straight divergent cone. "
            "'rao_bell' = parabolic-skirt bell from NASA SP-125 method-of-characteristics "
            "approximation. 'truncated_bell' = rao_bell with length_fraction < 1.0."
        ),
    )

    throat_radius: Optional[float] = Field(
        default=None,
        description="Throat radius in mm. Required for every nozzle type.",
    )

    exit_radius: Optional[float] = Field(
        default=None,
        description=(
            "Exit-plane radius in mm. For conical, supply either exit_radius "
            "or expansion_ratio. For Rao bells, derived from expansion_ratio."
        ),
    )

    expansion_ratio: Optional[float] = Field(
        default=None,
        description=(
            "Geometric area ratio epsilon = A_e / A_t. For bells, this is the "
            "primary input. For conicals, exit_radius is derived from "
            "r_t * sqrt(epsilon) when exit_radius is omitted."
        ),
    )

    half_angle_deg: Optional[float] = Field(
        default=None,
        description=(
            "Conical divergent half-angle in degrees. Required for type='conical'. "
            "Typical value: 12 to 18."
        ),
    )

    converging_angle_deg: Optional[float] = Field(
        default=None,
        description=(
            "Converging-section wall angle in degrees (chamber to throat). "
            "Typical value: 30 to 45."
        ),
    )

    length_fraction: Optional[float] = Field(
        default=None,
        description=(
            "Bell length as a fraction of an equivalent 15-degree conical "
            "nozzle. Common values: 0.6, 0.7, 0.8 (default), 0.9, 1.0. "
            "Ignored for type='conical'."
        ),
    )

    chamber_radius: Optional[float] = Field(
        default=None,
        description="Combustion-chamber inner radius in mm. Sets the converging-section base.",
    )

    @model_validator(mode="after")
    def _enforce_per_type_required(self) -> "NozzleSpec":
        if self.throat_radius is None:
            raise ValueError("NozzleSpec.throat_radius is required for every nozzle type.")
        if self.throat_radius <= 0:
            raise ValueError("NozzleSpec.throat_radius must be positive (mm).")

        if self.type == "conical":
            if self.exit_radius is None and self.expansion_ratio is None:
                raise ValueError(
                    "Conical nozzle requires either exit_radius or expansion_ratio."
                )
            if self.half_angle_deg is None:
                raise ValueError("Conical nozzle requires half_angle_deg.")
            if self.half_angle_deg <= 0 or self.half_angle_deg >= 45:
                raise ValueError(
                    f"Conical half_angle_deg out of physical range "
                    f"(got {self.half_angle_deg}; expected (0, 45))."
                )
            if self.converging_angle_deg is None:
                raise ValueError("Conical nozzle requires converging_angle_deg.")
            if self.chamber_radius is None or self.chamber_radius <= self.throat_radius:
                raise ValueError(
                    "Conical nozzle requires chamber_radius > throat_radius."
                )
        else:  # rao_bell / truncated_bell
            if self.expansion_ratio is None:
                raise ValueError(
                    f"{self.type} nozzle requires expansion_ratio."
                )
            if self.expansion_ratio <= 1.0:
                raise ValueError(
                    f"expansion_ratio must be > 1 (got {self.expansion_ratio})."
                )
            if self.chamber_radius is None or self.chamber_radius <= self.throat_radius:
                raise ValueError(
                    f"{self.type} nozzle requires chamber_radius > throat_radius."
                )
            if self.length_fraction is not None and not (0.5 <= self.length_fraction <= 1.0):
                raise ValueError(
                    f"length_fraction must lie in [0.5, 1.0] "
                    f"(got {self.length_fraction})."
                )
        return self


# ---------------------------------------------------------------------------
# CoolingChannelSpec
# ---------------------------------------------------------------------------
class CoolingChannelSpec(BaseModel):
    """Regenerative cooling channels milled into the outer wall of a nozzle."""

    count: int = Field(
        ...,
        description="Number of channels distributed around the circumference (e.g. 60).",
    )

    width: float = Field(
        ...,
        description="Tangential channel width in mm.",
    )

    depth: float = Field(
        ...,
        description="Radial channel depth in mm (measured into the wall).",
    )

    helix_angle_deg: float = Field(
        default=0.0,
        description=(
            "Helix lead angle in degrees, measured from the nozzle axis. "
            "0 = straight axial channels; 15-30 typical for helical regen."
        ),
    )

    axial_length: Optional[float] = Field(
        default=None,
        description=(
            "Axial length over which the channels run, in mm. "
            "If omitted, the cad_utils helper uses the full nozzle length."
        ),
    )


# ---------------------------------------------------------------------------
# InjectorSpec
# ---------------------------------------------------------------------------
class InjectorSpec(BaseModel):
    """Injector plate orifice pattern."""

    orifice_count: int = Field(
        ...,
        description=(
            "Total orifice count. For 'impinging_doublet' this is the count "
            "of impinging pairs; the helper draws 2*count actual holes."
        ),
    )

    orifice_diameter: float = Field(
        ...,
        description="Orifice diameter in mm.",
    )

    pattern: Literal["impinging_doublet", "like_doublet", "coaxial", "showerhead"] = Field(
        ...,
        description=(
            "Injector pattern family. Phase 1 supports impinging_doublet, "
            "like_doublet, coaxial, showerhead."
        ),
    )

    distribution_radius: float = Field(
        ...,
        description=(
            "Radius of the circle on which orifices (or orifice pairs) are "
            "distributed in mm."
        ),
    )

    impingement_angle_deg: Optional[float] = Field(
        default=30.0,
        description=(
            "Total angle between an impinging pair in degrees. "
            "Each orifice tilts by half this angle toward the centerline."
        ),
    )

    plate_radius: Optional[float] = Field(
        default=None,
        description=(
            "Injector plate outer radius in mm. Defaults to distribution_radius "
            "plus 1.5 * orifice_diameter when omitted."
        ),
    )

    plate_thickness: float = Field(
        default=5.0,
        description="Injector plate thickness in mm.",
    )


# ---------------------------------------------------------------------------
# ChamberSpec
# ---------------------------------------------------------------------------
class ChamberSpec(BaseModel):
    """Combustion chamber + L* sizing."""

    radius: float = Field(
        ...,
        description="Combustion chamber inner radius in mm.",
    )

    l_star: float = Field(
        ...,
        description=(
            "Characteristic length L* in meters (chamber volume / throat area). "
            "Typical: 0.8-1.2 m for LOX/Kerosene; 0.5-0.9 m for LOX/LH2."
        ),
    )

    contraction_ratio: Optional[float] = Field(
        default=None,
        description=(
            "Chamber-to-throat area ratio epsilon_c = A_chamber / A_throat. "
            "Derived from radius and throat_radius when omitted."
        ),
    )


# ---------------------------------------------------------------------------
# PropulsionSpec — top-level composer
# ---------------------------------------------------------------------------
class PropulsionSpec(BaseModel):
    """Propulsion subsystem composer. Sits as a sibling of ``airfoil_spec``
    on ``DesignRequirements`` (depth-2 from the REA root).
    """

    intent: Intent = Field(
        default="analysis",
        description=(
            "Downstream use of the geometry. 'analysis' enables CFD-ready "
            "tagging; 'manufacturable' enables min-wall + fillet checks; "
            "'visualization' skips the extras."
        ),
    )

    nozzle: Optional[NozzleSpec] = Field(
        default=None,
        description="Nozzle geometry. Omit when the prompt is a chamber- or injector-only.",
    )

    chamber: Optional[ChamberSpec] = Field(
        default=None,
        description="Combustion chamber geometry. Omit for nozzle-only prompts.",
    )

    injector: Optional[InjectorSpec] = Field(
        default=None,
        description="Injector plate geometry. Omit when no injector is described.",
    )

    cooling_channels: Optional[CoolingChannelSpec] = Field(
        default=None,
        description=(
            "Regenerative cooling channel set. Omit for radiation- or "
            "ablative-cooled designs."
        ),
    )

    @model_validator(mode="after")
    def _require_at_least_one_subsystem(self) -> "PropulsionSpec":
        if not any((self.nozzle, self.chamber, self.injector, self.cooling_channels)):
            raise ValueError(
                "PropulsionSpec must populate at least one of "
                "nozzle / chamber / injector / cooling_channels."
            )
        if self.cooling_channels is not None and self.nozzle is None:
            raise ValueError(
                "cooling_channels requires a nozzle (channels are milled into the nozzle wall)."
            )
        return self
