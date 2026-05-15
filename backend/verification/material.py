"""
backend/verification/material.py — material density table and mass computation.

Provides:
  - _DENSITY_TABLE: 20-entry map of material_class → density (kg/m³)
  - resolve_material(material_class) → (canonical_name, density_kg_m3)
  - mass_properties(volume_mm3, density_kg_m3) → {mass_g, mass_kg, density_kg_m3}

The material_class strings come from DesignRequirements.material_class
(schemas.py) and match the vocabulary documented there:
  aluminum, steel, stainless_steel, plastic, carbon_fiber, titanium,
  brass, ceramic, rubber, or None.

Additional common classes are supported via the alias map below.
Unknown classes fall back to the default (aluminum).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("mirum.verification.material")

# ---------------------------------------------------------------------------
# Density table — 20 entries, kg/m³
# Sources: MatWeb, ASM Handbook Vol. 2, Matweb online database.
# ---------------------------------------------------------------------------

_DENSITY_TABLE: dict[str, float] = {
    # Metals
    "aluminum":        2700.0,   # 6061-T6 typ.
    "aluminium":       2700.0,   # British spelling alias
    "steel":           7850.0,   # mild / carbon steel
    "stainless_steel": 7900.0,   # 304 austenitic
    "titanium":        4500.0,   # Ti-6Al-4V typ.
    "brass":           8500.0,   # CuZn37 typ.
    "copper":          8960.0,   # C11000
    "magnesium":       1740.0,   # AZ31 wrought
    "zinc":            7133.0,   # Zamak 3 typ.
    "inconel":         8190.0,   # IN625, nickel superalloy
    # Polymers / composites
    "plastic":         1200.0,   # generic / ABS
    "abs":             1050.0,
    "pla":             1240.0,
    "petg":            1270.0,
    "nylon":           1140.0,   # PA6
    "polycarbonate":   1200.0,   # PC
    "carbon_fiber":    1600.0,   # CFRP laminate, 60% Vf typ.
    # Other
    "ceramic":         3900.0,   # alumina (Al₂O₃)
    "rubber":          1100.0,   # natural vulcanised
    "glass":           2500.0,   # borosilicate
}

# Default used when material_class is None or unknown
_DEFAULT_MATERIAL = "aluminum"

# Alias map — maps known synonyms to canonical table keys
_ALIASES: dict[str, str] = {
    "aluminium":          "aluminum",
    "al":                 "aluminum",
    "stainless":          "stainless_steel",
    "ss":                 "stainless_steel",
    "steel_stainless":    "stainless_steel",
    "carbon_fibre":       "carbon_fiber",
    "cfrp":               "carbon_fiber",
    "cf":                 "carbon_fiber",
    "ti":                 "titanium",
    "titanium_alloy":     "titanium",
    "poly":               "plastic",
    "abs_plastic":        "abs",
    "rubber_natural":     "rubber",
    "ni_superalloy":      "inconel",
}


def resolve_material(material_class: str | None) -> tuple[str, float]:
    """Map a material_class string to (canonical_name, density_kg_m3).

    Args:
        material_class: Value from DesignRequirements.material_class,
            or None if unspecified.

    Returns:
        (canonical_name, density_kg_m3) — always returns a valid pair.
        Falls back to aluminum when the class is unknown or None.

    Examples:
        >>> resolve_material("titanium")
        ('titanium', 4500.0)
        >>> resolve_material("ALUMINIUM")   # case-insensitive
        ('aluminum', 2700.0)
        >>> resolve_material(None)
        ('aluminum', 2700.0)
        >>> resolve_material("unobtainium")  # unknown → fallback
        ('aluminum', 2700.0)
    """
    if not material_class:
        return _DEFAULT_MATERIAL, _DENSITY_TABLE[_DEFAULT_MATERIAL]

    key = material_class.lower().strip().replace("-", "_").replace(" ", "_")

    # Resolve alias first
    key = _ALIASES.get(key, key)

    if key in _DENSITY_TABLE:
        return key, _DENSITY_TABLE[key]

    # Partial-match fallback: check if any table key starts with the given key
    for table_key in _DENSITY_TABLE:
        if table_key.startswith(key) or key.startswith(table_key):
            logger.debug(
                "resolve_material: partial match %r → %r", material_class, table_key
            )
            return table_key, _DENSITY_TABLE[table_key]

    logger.warning(
        "resolve_material: unknown material class %r — falling back to %r",
        material_class,
        _DEFAULT_MATERIAL,
    )
    return _DEFAULT_MATERIAL, _DENSITY_TABLE[_DEFAULT_MATERIAL]


def mass_properties(volume_mm3: float, density_kg_m3: float) -> dict[str, float]:
    """Compute mass from volume and density.

    Args:
        volume_mm3: Solid volume in cubic millimetres (from certify_geometry).
        density_kg_m3: Material density in kg/m³ (from resolve_material).

    Returns:
        dict with keys:
          mass_g          — mass in grams
          mass_kg         — mass in kilograms
          density_kg_m3   — the density used (echoed back for traceability)

    Examples:
        >>> mass_properties(6000.0, 2700.0)   # 10×20×30 mm aluminum box
        {'mass_g': 16.2, 'mass_kg': 0.0162, 'density_kg_m3': 2700.0}
    """
    # 1 mm³ = 1e-6 cm³ = 1e-9 m³
    volume_m3 = volume_mm3 * 1e-9
    mass_kg = density_kg_m3 * volume_m3
    mass_g = mass_kg * 1000.0
    return {
        "mass_g": round(mass_g, 4),
        "mass_kg": round(mass_kg, 7),
        "density_kg_m3": density_kg_m3,
    }
