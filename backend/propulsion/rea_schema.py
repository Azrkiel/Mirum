"""Hand-written Gemini ``response_schema`` + classifier vocab for Domain F.

We deliberately do NOT derive the schema from the Pydantic model. Gemini's
structured-output mode rejects ``$ref``, ``anyOf``, ``default``, ``title``, etc.,
and the ``_get_gemini_schema`` sanitizer in ``agents.py`` is bespoke to
``AssemblyManifest``. Keeping this schema hand-written mirrors the ``_REA_SCHEMA``
pattern in ``agents.py`` and keeps the propulsion spec shape in one place.

The shape here is the **delta** merged into the REA schema:
``propulsion_spec`` becomes an optional top-level dict on the response. It mirrors
``specs.PropulsionSpec`` field-for-field.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Classifier vocabulary (two-tier, used by ``mentions_propulsion``)
# ---------------------------------------------------------------------------
# Two tiers so benign homographs ("bell crank", "bellhousing", "throat lozenge",
# "air duct", "inlet manifold") do not force Domain F:
#   * strong tokens — a single occurrence routes to propulsion. Matched as a
#     plain substring (so plurals like "nozzles"/"nacelles" still hit). None of
#     these is a prefix of a common non-propulsion word.
#   * weak tokens — ambiguous single words that also appear in unrelated parts.
#     Matched as whole words and only corroborated: one weak hit is NOT enough;
#     routing needs a strong hit or >= 2 distinct weak hits (e.g. "throat" + "bell").
_STRONG_PROPULSION_VOCAB: tuple[str, ...] = (
    "nozzle",
    "de laval",
    "converging-diverging",
    "convergent-divergent",
    "combustion chamber",
    "thrust chamber",
    "afterburner",
    "nacelle",
    "inlet duct",
    "fuel inlet",
    "oxidizer inlet",
    "injector",
    "bypass ratio",
    "fan face",
    "highlight radius",
)

_WEAK_PROPULSION_VOCAB: tuple[str, ...] = (
    "throat",
    "bell",
    "cowl",
    "cowling",
    "inlet",
    "duct",
    "dome",
)

# Public, ordered vocabulary kept for the classifier's reasoning string and any
# external callers that iterate it.
PROPULSION_VOCAB: tuple[str, ...] = (
    _STRONG_PROPULSION_VOCAB + _WEAK_PROPULSION_VOCAB
)


# ---------------------------------------------------------------------------
# Gemini response_schema (aerospace flat shape — mirrors specs.PropulsionSpec)
# ---------------------------------------------------------------------------
PROPULSION_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Domain F only. Populate from the prompt when propulsion geometry "
        "parameters are named (nozzle, combustion chamber, nacelle/inlet cowl, "
        "afterburner duct). Omit fields not explicitly stated."
    ),
    "properties": {
        "propulsion_type":     {"type": "string",  "description": "'nozzle' / 'chamber' / 'nacelle' / 'duct' / 'generic'."},
        "throat_diameter_mm":  {"type": "number",  "description": "Nozzle throat diameter (mm)."},
        "exit_diameter_mm":    {"type": "number",  "description": "Nozzle exit-plane diameter (mm)."},
        "chamber_radius_mm":   {"type": "number",  "description": "Combustion chamber inner radius (mm)."},
        "chamber_length_mm":   {"type": "number",  "description": "Combustion chamber cylindrical length (mm)."},
        "wall_thickness_mm":   {"type": "number",  "description": "Component wall thickness (mm)."},
        "num_fuel_inlets":     {"type": "integer", "description": "Number of fuel inlet ports."},
        "highlight_radius_mm": {"type": "number",  "description": "Nacelle inlet highlight (outer lip) radius (mm)."},
        "fan_face_radius_mm":  {"type": "number",  "description": "Nacelle fan-face (inner duct) radius (mm)."},
        "conv_half_angle_deg": {"type": "number",  "description": "Nozzle converging-section half-angle (deg)."},
        "div_half_angle_deg":  {"type": "number",  "description": "Nozzle diverging-section half-angle (deg)."},
    },
}


def propulsion_rea_schema_extension() -> dict[str, Any]:
    """Return the ``properties`` fragment to merge into the REA schema.

    The caller merges this into ``_REA_SCHEMA["properties"]`` so ``propulsion_spec``
    becomes an optional top-level field. It is NOT added to ``required`` — only
    Domain F prompts should populate it.
    """
    return {"propulsion_spec": PROPULSION_SPEC_SCHEMA}


def _whole_word_present(token: str, text: str) -> bool:
    """True if ``token`` occurs as a standalone word, not a prefix of a longer
    word (so 'bell' does not match 'bellhousing', 'cowl' not 'cowlick')."""
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def mentions_propulsion(text: str) -> bool:
    """Cheap two-tier keyword trigger used by the REA and classifier alike.

    A single strong token routes to Domain F. Weak, ambiguous tokens count only
    when corroborated: a strong hit, or at least two distinct weak hits
    (e.g. 'throat' + 'bell'), is required. This stops benign homographs
    ('bell crank', 'bellhousing', 'throat lozenge', 'air duct') from forcing the
    propulsion domain.
    """
    lo = text.lower()
    if any(token in lo for token in _STRONG_PROPULSION_VOCAB):
        return True
    weak_hits = sum(1 for token in _WEAK_PROPULSION_VOCAB if _whole_word_present(token, lo))
    return weak_hits >= 2
