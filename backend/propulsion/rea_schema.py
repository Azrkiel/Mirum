"""Hand-written Gemini ``response_schema`` for the propulsion REA branch.

We deliberately do NOT derive this from the Pydantic models. Gemini's
structured-output mode rejects ``$ref``, ``anyOf``, ``default``, ``title``,
``minItems``, ``maxItems``, etc., and the existing ``_get_gemini_schema``
sanitizer in ``agents.py`` is bespoke to ``AssemblyManifest``. Keeping the
REA schemas hand-written mirrors the existing ``_REA_SCHEMA`` pattern at
``agents.py:239-286`` and avoids feeding Pydantic's anyOf-laden output through
a second sanitization path.

The shape returned here is the **delta** added to the existing REA schema:
``propulsion_spec`` becomes a top-level optional dict on the response. The
caller merges this into ``_REA_SCHEMA`` properties.
"""

from __future__ import annotations

from typing import Any


# Vocabulary used by both REA (pre-trigger) and classifier (Domain F routing).
# Keeping it in one place avoids the two paths drifting.
PROPULSION_VOCAB: tuple[str, ...] = (
    "nozzle",
    "throat",
    "expansion ratio",
    "rao",
    "bell",
    "injector",
    "thrust chamber",
    "combustion chamber",
    "regen",
    "regenerative",
    "cooling channel",
    "l*",
    "characteristic length",
    "impinging doublet",
    "like doublet",
    "coaxial injector",
    "propellant",
    "thruster",
    "rocket engine",
)


# Sub-schemas mirroring the Pydantic models in ``specs.py`` but in the
# Gemini-safe dict shape (no $ref, no anyOf, no defaults).
_NOZZLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Nozzle geometry. Populate when the prompt names a nozzle, bell, "
        "or throat dimension."
    ),
    "properties": {
        "type": {
            "type": "string",
            "enum": ["conical", "rao_bell", "truncated_bell"],
            "description": "Nozzle topology.",
        },
        "throat_radius":        {"type": "number", "description": "Throat radius (mm)."},
        "exit_radius":          {"type": "number", "description": "Exit radius (mm)."},
        "expansion_ratio":      {"type": "number", "description": "A_e / A_t."},
        "half_angle_deg":       {"type": "number", "description": "Conical divergent half-angle (deg). Required for conical."},
        "converging_angle_deg": {"type": "number", "description": "Converging wall angle (deg). Required for conical."},
        "length_fraction":      {"type": "number", "description": "Bell length fraction in [0.5, 1.0] (default 0.8)."},
        "chamber_radius":       {"type": "number", "description": "Combustion chamber radius (mm)."},
    },
    "required": ["type", "throat_radius"],
}

_COOLING_CHANNELS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Regenerative cooling channels milled into the nozzle outer wall.",
    "properties": {
        "count":           {"type": "integer", "description": "Number of channels around circumference."},
        "width":           {"type": "number",  "description": "Tangential channel width (mm)."},
        "depth":           {"type": "number",  "description": "Radial channel depth (mm)."},
        "helix_angle_deg": {"type": "number",  "description": "Helix lead angle from nozzle axis (deg, 0 = axial)."},
        "axial_length":    {"type": "number",  "description": "Axial length of channels (mm). Omit to use full nozzle length."},
    },
    "required": ["count", "width", "depth"],
}

_INJECTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Injector plate orifice pattern.",
    "properties": {
        "orifice_count":         {"type": "integer", "description": "Orifice count (or count of impinging pairs)."},
        "orifice_diameter":      {"type": "number",  "description": "Orifice diameter (mm)."},
        "pattern": {
            "type": "string",
            "enum": ["impinging_doublet", "like_doublet", "coaxial", "showerhead"],
            "description": "Injector pattern family.",
        },
        "distribution_radius":   {"type": "number",  "description": "Radius of orifice (or pair) distribution circle (mm)."},
        "impingement_angle_deg": {"type": "number",  "description": "Total angle between an impinging pair (deg)."},
        "plate_radius":          {"type": "number",  "description": "Injector plate outer radius (mm)."},
        "plate_thickness":       {"type": "number",  "description": "Injector plate thickness (mm)."},
    },
    "required": ["orifice_count", "orifice_diameter", "pattern", "distribution_radius"],
}

_CHAMBER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Combustion chamber and L* sizing.",
    "properties": {
        "radius":            {"type": "number", "description": "Chamber inner radius (mm)."},
        "l_star":            {"type": "number", "description": "Characteristic length L* (meters)."},
        "contraction_ratio": {"type": "number", "description": "A_chamber / A_throat. Derived if omitted."},
    },
    "required": ["radius", "l_star"],
}

PROPULSION_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Propulsion subsystem spec (Domain F). Populate ONLY when the prompt "
        "names rocket-propulsion features (nozzle/throat/expansion ratio/Rao/bell, "
        "thrust chamber/injector/L*, regen cooling)."
    ),
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["analysis", "manufacturable", "visualization"],
            "description": (
                "Downstream use. Infer: 'for CFD' -> analysis, "
                "'machined/manufactured' -> manufacturable, otherwise analysis."
            ),
        },
        "nozzle":           _NOZZLE_SCHEMA,
        "chamber":          _CHAMBER_SCHEMA,
        "injector":         _INJECTOR_SCHEMA,
        "cooling_channels": _COOLING_CHANNELS_SCHEMA,
    },
    "required": ["intent"],
}


def propulsion_rea_schema_extension() -> dict[str, Any]:
    """Return the ``properties`` map fragment to merge into ``_REA_SCHEMA``.

    The caller merges this dict into ``_REA_SCHEMA["properties"]`` so that
    ``propulsion_spec`` becomes an optional top-level field. We do not add
    it to ``required`` -- only Domain F prompts should populate it.
    """
    return {"propulsion_spec": PROPULSION_SPEC_SCHEMA}


def mentions_propulsion(text: str) -> bool:
    """Cheap keyword trigger used by REA and classifier alike."""
    lo = text.lower()
    return any(token in lo for token in PROPULSION_VOCAB)
