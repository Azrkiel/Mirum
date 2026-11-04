"""
NLP Classification Tier for the Text-to-CAD Strategy Pattern.

Classifies user part descriptions into geometric domains so the Machinist
can apply the correct CadQuery generation strategy. This is a fast, cheap
Gemini call that runs before the Machinist to prevent blind primitive-only
generation.

Domains:
  A (Primitives/Structural)    — boxes, plates, brackets, enclosures
  B (Mechanical/Parametric)    — gears, bearings, threads, cams, springs
  C (Organic/Ergonomic)        — handles, shells, fairings, grips, spoons
  D (Aerospace/Aerodynamics)   — wings, airfoils, NACA profiles, ribs, spars
  E (Sheet Metal/Thin-Wall)    — bent plates, enclosure panels, PCB trays (T3-06)
"""

import os
import re

import google.generativeai as genai

from config import GEMINI_MODEL_LITE
from propulsion.rea_schema import mentions_propulsion

MODEL = GEMINI_MODEL_LITE

# Decisive lifting-surface (Domain D) and vehicle-body (Domain G) tokens. When
# any of these is present we defer Domain F routing to the LLM classifier, so a
# "wing with engine nacelle" stays Domain D and a "fairing over an inlet duct"
# stays Domain G rather than being hijacked by the deterministic F short-circuit.
_NON_F_GUARD = re.compile(
    r"\b(wing|airfoil|aerofoil|naca|camber|chord|span|spar|rib|"
    r"fuselage|nose cone|ogive|fairing|shroud|capsule|body tube|haack)\b"
)

# Gemini-compatible schema (no unsupported fields)
_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "domain": {
            "type": "string",
            "enum": ["A", "B", "C", "D", "E", "F", "G"],
            "description": (
                "The geometric domain: "
                "'A' for primitives/structural (boxes, plates, enclosures, brackets, frames). "
                "'B' for mechanical/parametric (gears, bearings, threads, cams, pulleys, springs, fasteners). "
                "'C' for organic/ergonomic (handles, mouse shells, grips, bottles, spoons, helmets, CO2 dragsters, derby cars). "
                "'D' for aerospace/aerodynamics (wings, airfoils, NACA profiles, ribs, spars, "
                "camber, leading edge, trailing edge, wing segments, aerostructures, swept wings, "
                "delta wings, propellers, rotor blades, turbine blade blanks). "
                "'E' for sheet metal/thin-wall (bent plates, enclosure panels, PCB mounting trays, "
                "chassis panels, steel/aluminum gauges, flanged sheet parts, stampings). "
                "'F' for propulsion/axisymmetric (rocket nozzles, De Laval nozzles, combustion chambers, "
                "fuel inlets, oxidizer inlets, afterburner ducts, nacelle cowlings, inlet ducts, "
                "thrust chambers, injector faces). "
                "'G' for vehicle bodies/fairings (aircraft fuselages, nose cones, payload fairings, "
                "rocket body tubes, re-entry capsules, crew capsules, pod bodies, shrouds, "
                "streamlined enclosures with varying cross-sections)."
            ),
        },
        "reasoning": {
            "type": "string",
            "description": "One sentence explaining why this domain was chosen.",
        },
        "key_params": {
            "type": "string",
            "description": (
                "Extracted engineering parameters as a comma-separated string. "
                "For Domain A: 'width=X, height=Y, depth=Z' (bounding box). "
                "For Domain B: 'num_teeth=X, module=Y, bore=Z' or similar parametric values. "
                "For Domain C: 'length=X, max_width=Y, cross_sections=N' or similar. "
                "Extract whatever numeric values are present in the description."
            ),
        },
    },
    "required": ["domain", "reasoning", "key_params"],
}


async def classify_part(description: str) -> dict:
    """Classify a part description into a geometric domain.

    Args:
        description: The part's physical description from PartDefinition.

    Returns:
        {"domain": "A"|"B"|"C"|"D", "reasoning": str, "key_params": str}
    """
    # Deterministic Domain F short-circuit: the two-tier propulsion vocab is
    # homograph-guarded (a single strong token like 'nozzle'/'nacelle'/'afterburner',
    # or two corroborating weak tokens). It saves a Gemini call and makes Domain F
    # routing reproducible — unless a decisive Domain D/G token is also present, in
    # which case we let the LLM classifier apply its priority rules.
    if mentions_propulsion(description) and not _NON_F_GUARD.search(description.lower()):
        return {
            "domain": "F",
            "reasoning": "Deterministic propulsion-vocabulary match (Domain F short-circuit).",
            "key_params": "",
        }

    # Domain routing decides which strategy prompt the Machinist gets, so it
    # runs at the registry's temperature 0.0 rather than the API default —
    # a sampling coin-flip between Domain A and Domain D swaps the entire
    # rule set out from under the generator.
    from agents import _agent_model  # noqa: PLC0415 — avoids a circular import at module load
    _cls_model, _cls_gen = _agent_model("classifier", MODEL)
    model = genai.GenerativeModel(
        model_name=_cls_model,
        system_instruction=(
            "You are a mechanical engineering classifier. Given a part description, "
            "determine which geometric domain it belongs to. "
            "\n"
            "Domain A (Primitives/Structural): Parts best made with extrusions and "
            "boolean operations on basic shapes. Examples: base plates, enclosures, "
            "L-brackets, mounting blocks, simple frames, spacers, standoffs. "
            "Key signal: described mainly by length/width/height, holes, slots, fillets. "
            "\n"
            "Domain B (Mechanical/Parametric): Parts requiring precise mathematical "
            "curves or parametric formulas. Examples: spur gears, helical gears, "
            "bearings, cams, pulleys, lead screws, threads, sprockets, turbine blades. "
            "Key signal: mentions teeth, pitch, module, pressure angle, involute, "
            "thread pitch, helix, or similar engineering parameters. "
            "\n"
            "Domain C (Organic/Ergonomic): Parts with smooth, flowing surfaces that "
            "cannot be made with simple extrusions. Examples: ergonomic handles, "
            "mouse shells, bottle shapes, helmet visors, "
            "phone cases with contoured backs, joystick grips, CO2 dragsters, derby cars. "
            "Key signal: described by curves, comfort, organic shapes, aerodynamic bodies, "
            "or cross-sectional profiles that vary along an axis. "
            "\n"
            "Domain D (Aerospace/Aerodynamics): Wing sections, airfoil profiles, and "
            "aerostructural components with internal reinforcement. Examples: NACA airfoil "
            "extrusions, wing segments with ribs/spars, aerodynamic control surfaces, "
            "stabilizer fins, turbine blade blanks, propeller blades, swept wings, "
            "delta wings, canards, horizontal stabilizers, and leading/trailing edge assemblies. "
            "Key signal: mentions 'wing', 'airfoil', 'NACA', 'camber', 'chord', 'span', "
            "'ribs', 'spars', 'leading edge', 'trailing edge', 'aerofoil', 'swept', "
            "'propeller', 'rotor blade', 'turbine blade', or any airfoil code (e.g., '2412'). "
            "\n"
            "PRIORITY: Domain D takes precedence over ALL other domains when aerospace "
            "keywords are present. A 'wing rib' is Domain D, NOT Domain A. An 'airfoil "
            "fairing' is Domain D, NOT Domain C. A 'turbine blade blank' or 'propeller "
            "blade' is Domain D, NOT Domain B. "
            "\n"
            "Domain E (Sheet Metal/Thin-Wall): Parts made from thin sheet stock that are "
            "bent, stamped, or formed rather than machined. Examples: enclosure panels, "
            "PCB mounting trays, bent plate brackets, chassis panels, sheet steel "
            "housings, metal cabinet doors, air duct flanges, electrical box covers. "
            "Key signal: mentions 'sheet metal', 'thin plate', 'bent', 'gauge', "
            "'bend radius', 'flange depth', 'PCB tray', 'enclosure panel', 'stamped', "
            "or gives thickness as a standard gauge (0.8mm, 1.0mm, 1.2mm, 1.5mm, 2.0mm). "
            "\n"
            "Domain F (Propulsion/Axisymmetric): Propulsion components built primarily "
            "from axisymmetric bodies of revolution — converging-diverging nozzles, "
            "combustion chambers, fuel inlets, afterburner ducts, nacelle cowlings, "
            "inlet ducts, and injector faces. "
            "Key signal: mentions 'nozzle', 'throat', 'De Laval', 'converging-diverging', "
            "'combustion chamber', 'fuel inlet', 'oxidizer inlet', 'injector', "
            "'afterburner', 'thrust chamber', 'nacelle', 'inlet duct', 'cowling', "
            "'bypass ratio', 'fan face', 'highlight radius'. "
            "\n"
            "Domain G (Vehicle Bodies/Fairings): Large-scale bodies of revolution or "
            "multi-station lofted vehicle structures — fuselages, nose cones, payload "
            "fairings, rocket body tubes, re-entry capsules, pod bodies, and shrouds. "
            "Key signal: mentions 'fuselage', 'nose cone', 'ogive', 'fairing', 'shroud', "
            "'payload section', 'rocket body', 'body tube', 'capsule', 'pod body', "
            "'fuselage section', 'Haack series', 'parabolic nose', 'tangent ogive'. "
            "\n"
            "PRIORITY: Domain F takes precedence over Domain B when 'nozzle', 'combustion', "
            "'thrust', or 'injector' keywords are present. A 'rocket nozzle' is Domain F, "
            "NOT Domain B. "
            "Domain G takes precedence over Domain C when 'fuselage', 'nose cone', "
            "'fairing', or 'body tube' keywords are present. A 'nose cone' is Domain G, "
            "NOT Domain C. "
            "Domain D takes precedence over ALL other domains when aerospace lifting-surface "
            "keywords are present. "
            "Domain E takes precedence over Domain A when sheet metal keywords are present. "
            "\n"
            "When in doubt between A and B, choose B (parametric is safer than primitive). "
            "When in doubt between A and C, choose A (structural is more reliable). "
            "When in doubt between A and E, choose E if any gauge/bend/sheet keywords present. "
            "Extract all numeric parameters you can find from the description."
        ),
    )

    response = await model.generate_content_async(
        f"Classify this part:\n{description}",
        generation_config={
            **_cls_gen,
            "response_mime_type": "application/json",
            "response_schema": _CLASSIFICATION_SCHEMA,
        },
    )

    import json
    return json.loads(response.text)
