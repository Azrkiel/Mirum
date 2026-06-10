"""
Hierarchical Subagent Orchestrator for Text-to-CAD Pipeline.

This module implements the "Chief Engineer" router — a custom Python
orchestrator that makes isolated calls to Gemini 2.5 Flash.
Each function represents a distinct subagent with its own system prompt
and context boundary, preventing spatial hallucinations through strict
context isolation.

Subagent Flow:
  User Prompt → Planner → [Machinist + Critic Loop] × N (concurrent) → Assembler → .glb

The Assembler is now DETERMINISTIC — it loads pre-compiled .step files
and applies manifest translations directly via CadQuery, with zero LLM
involvement. Telemetry is captured for every Machinist attempt.
"""

import asyncio
import copy
import json
import logging
import os
import pathlib
import re
import time
import uuid
from typing import Any

import google.generativeai as genai

logger = logging.getLogger("mirum.agents")

from classifier import classify_part
from compiler import _WORK_DIR, execute_cad_script
from failure_analysis import build_retry_context, classify_failure
from propulsion.rea_schema import (
    mentions_propulsion,
    propulsion_rea_schema_extension,
)
from propulsion.validation import validate_all as validate_propulsion_spec
from retrieval import (
    add_to_index,
    extract_techniques,
    retrieve_few_shots,
    retrieve_for_failure,
)
from strategies import get_active_rules
from schemas import AssemblyManifest, DesignRequirements, MateType, PartDefinition
from strategies import get_strategy
from telemetry import log_attempt
from config import GEMINI_MODEL_HEAVY, GEMINI_MODEL_LITE, GEMINI_MODEL_PRO

# Heavy tier: Machinist first attempt (gemini-3.5-flash)
MODEL = GEMINI_MODEL_HEAVY
# Pro tier: Planner, REA, Refiner, Machinist on retry (gemini-3.1-pro-preview)
MODEL_PRO = GEMINI_MODEL_PRO
# Lite tier: Clarifier (gemini-3.1-flash-lite-preview — cheap Q&A)
MODEL_LITE = GEMINI_MODEL_LITE

# Directory for pre-compiled .step files consumed by the deterministic
# assembler. Defaults to /tmp/mirum/parts because the previous location
# (backend/tmp_parts, a Docker named volume) was created as root:root
# and is unwritable by the `caduser` the backend runs as. Override via
# MIRUM_PARTS_DIR if a persistent writable location is available.
_TMP_PARTS_DIR = pathlib.Path(
    os.environ.get("MIRUM_PARTS_DIR", "/tmp/mirum/parts")
)

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize_part_id(raw: str) -> str:
    """Strip part_id to alphanumeric + underscore only (max 64 chars)."""
    return _SAFE_ID_RE.sub("_", raw)[:64] or "unnamed_part"


def cleanup_tmp_parts() -> None:
    """Remove all files from the tmp_parts directory."""
    try:
        _TMP_PARTS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for f in _TMP_PARTS_DIR.iterdir():
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                pass


class ScriptError(RuntimeError):
    """RuntimeError subclass that carries the last attempted CadQuery script."""

    def __init__(self, message: str, script: str = ""):
        super().__init__(message)
        self.script = script

# Max concurrent Machinist calls (respects Gemini rate limits)
_MACHINIST_SEMAPHORE = asyncio.Semaphore(3)

# Maps mate types to CadQuery constraint type strings for assembly.solve()
MATE_CONSTRAINTS: dict[str, list[str]] = {
    "FASTENED":    ["Plane"],
    "REVOLUTE":    ["Axis"],
    "SLIDER":      ["Plane"],
    "CYLINDRICAL": ["Axis"],
    "PLANAR":      ["Plane"],
    "BALL":        ["Point"],
}

# Fields that Gemini's structured output schema does NOT support.
_UNSUPPORTED_SCHEMA_KEYS = {
    "title", "default", "minItems", "maxItems", "minimum", "maximum",
    "minLength", "maxLength", "$defs",
}


def _flatten_any_of(schema: dict[str, Any]) -> dict[str, Any]:
    """Flatten Pydantic-style ``anyOf: [X, {"type": "null"}]`` into ``X`` + nullable.

    Gemini's ``response_schema`` rejects ``anyOf`` outright. Pydantic v2 emits it
    for every ``Optional[...]`` field. We pick the first non-null variant, merge
    any sibling keys (description, etc.) onto it, and mark it ``nullable``.
    """
    variants = schema["anyOf"]
    non_null = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
    has_null = len(non_null) < len(variants)
    picked = non_null[0] if non_null else {}
    merged = {k: v for k, v in schema.items() if k != "anyOf"}
    for k, v in picked.items():
        merged.setdefault(k, v)
    if has_null:
        merged["nullable"] = True
    return merged


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip JSON Schema fields unsupported by the Gemini API."""
    # Flatten until stable — a picked variant may itself carry anyOf.
    # Bounded by the number of fields, so this cannot loop indefinitely.
    guard = 0
    while "anyOf" in schema and guard < 16:
        schema = _flatten_any_of(schema)
        guard += 1
    if "anyOf" in schema:
        # Defensive: drop anyOf rather than recurse forever if something pathological slips through.
        schema = {k: v for k, v in schema.items() if k != "anyOf"}
    cleaned = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[key] = _sanitize_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _sanitize_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


_REF_PREFIX = "#/$defs/"


def _inline_refs(node: Any, defs: dict[str, Any], seen: frozenset = frozenset()) -> Any:
    """Recursively resolve ``$ref`` pointers against a ``$defs`` map.

    Replaces the prior string-replace approach, which failed when a def's body
    itself contained a ref to another def that had already been processed —
    the nested ref re-entered the main schema after its own pass was over.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
            name = ref[len(_REF_PREFIX):]
            if name in seen or name not in defs:
                # Cycle or dangling ref — drop the $ref so the output stays clean.
                return {k: _inline_refs(v, defs, seen) for k, v in node.items() if k != "$ref"}
            resolved = _inline_refs(defs[name], defs, seen | {name})
            siblings = {k: _inline_refs(v, defs, seen) for k, v in node.items() if k != "$ref"}
            if isinstance(resolved, dict):
                merged = dict(resolved)
                merged.update(siblings)  # siblings override resolved keys (JSON Schema 2020-12)
                return merged
            return resolved
        return {k: _inline_refs(v, defs, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs, seen) for item in node]
    return node


def _get_gemini_schema() -> dict[str, Any]:
    """Generate a Gemini-compatible JSON schema from AssemblyManifest."""
    t0 = time.monotonic()
    raw = AssemblyManifest.model_json_schema()
    defs = raw.pop("$defs", {})
    resolved = _inline_refs(raw, defs)
    sanitized = _sanitize_schema(resolved)
    leftover_ref = _find_key(sanitized, "$ref")
    if leftover_ref is not None:
        logger.error("Gemini schema still contains $ref at path: %s", leftover_ref)
    leftover_any = _find_key(sanitized, "anyOf")
    if leftover_any is not None:
        logger.error("Gemini schema still contains anyOf at path: %s", leftover_any)
    logger.info(
        "_get_gemini_schema: built in %.3fs (%d defs inlined, %d top-level keys)",
        time.monotonic() - t0,
        len(defs),
        len(sanitized),
    )
    return sanitized


def _find_key(obj: Any, needle: str, path: str = "$") -> str | None:
    """Return the dotted path of the first occurrence of ``needle`` in ``obj``, or None."""
    if isinstance(obj, dict):
        if needle in obj:
            return path
        for k, v in obj.items():
            hit = _find_key(v, needle, f"{path}.{k}")
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hit = _find_key(v, needle, f"{path}[{i}]")
            if hit is not None:
                return hit
    return None


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    text = re.sub(r"^```(?:python|py)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


# Base REA response schema (Gemini-safe: no $ref / anyOf / defaults). The Domain F
# `propulsion_spec` field is NOT part of the base — it is merged in only for
# propulsion prompts by `_rea_schema_for`, keeping the schema lean otherwise.
_REA_BASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "primary_function": {"type": "string"},
        "key_dimensions": {
            "type": "object",
            "description": "dimension_name -> value_in_mm",
        },
        "material_class": {"type": "string"},
        "environment": {"type": "string"},
        "connecting_interfaces": {
            "type": "array",
            "items": {"type": "string"},
        },
        "production_volume": {"type": "string"},
        "inferred_domain": {"type": "string"},
        "confidence": {"type": "number"},
        # Phase 3 (XFOIL): only populate for Domain D when explicitly stated
        "flight_env": {
            "type": "object",
            "description": (
                "Domain D only. Populate ONLY if the prompt names "
                "explicit aerodynamic conditions. Omit otherwise."
            ),
            "properties": {
                "alpha_start": {"type": "number"},
                "alpha_end":   {"type": "number"},
                "alpha_step":  {"type": "number"},
                "reynolds":    {"type": "number"},
                "mach":        {"type": "number"},
                "n_crit":      {"type": "number"},
            },
        },
        "airfoil_spec": {
            "type": "object",
            "description": (
                "Domain D only. Populate from the prompt when a NACA "
                "airfoil and/or wing dimensions are named."
            ),
            "properties": {
                "naca_code":      {"type": "string"},
                "chord_mm":       {"type": "number"},
                "span_mm":        {"type": "number"},
                "sweep_le_deg":   {"type": "number"},
                "dihedral_deg":   {"type": "number"},
                "root_pitch_deg": {"type": "number"},
                "tip_pitch_deg":  {"type": "number"},
            },
        },
        # Phase 4: structural / flight-envelope spec extraction (Domain D).
        # Feeds the opt-in run_struct + run_aero stages. Geometry-layout fields
        # (spar fractions / rib count) are intentionally OMITTED — the cad_aero
        # builders use a fixed canonical layout, so the REA need not guess them.
        "aircraft_spec": {
            "type": "object",
            "description": (
                "Domain D only. Populate when the prompt names a take-off weight "
                "AND a cruise condition (altitude and/or speed). Drives the "
                "load-factor envelope + structural margins. NEVER guess values "
                "the user did not give."
            ),
            "properties": {
                "mtow_kg":         {"type": "number"},
                "cruise_alt_m":    {"type": "number"},
                "cruise_speed_ms": {"type": "number"},
                "category":        {"type": "string"},
                "cl_max":          {"type": "number"},
            },
        },
        "wing_spec": {
            "type": "object",
            "description": (
                "Domain D only. Reference wing planform for the structural "
                "envelope; populate when wing area and/or span are named."
            ),
            "properties": {
                "area_m2":      {"type": "number"},
                "span_m":       {"type": "number"},
                "mean_chord_m": {"type": "number"},
            },
        },
        "wing_box_spec": {
            "type": "object",
            "description": (
                "Domain D only. Wing-box SECTION SIZING for bending/shear "
                "margins; populate when the prompt describes a structural wing "
                "(spar caps, shear web, thickness-to-chord)."
            ),
            "properties": {
                "chord_root_m":         {"type": "number"},
                "thickness_to_chord":   {"type": "number"},
                "cap_area_m2":          {"type": "number"},
                "spar_web_thickness_m": {"type": "number"},
                "n_spars":              {"type": "integer"},
            },
        },
    },
    "required": ["primary_function", "connecting_interfaces", "confidence"],
}


def _rea_schema_for(prompt: str) -> dict[str, Any]:
    """Build the REA response schema for ``prompt``.

    The Domain F ``propulsion_spec`` field (owned by the propulsion package, so the
    REA, classifier vocab, and validators share one source of truth) is merged in
    only when the prompt mentions propulsion — keeping the schema minimal for the
    other domains. Returns a fresh copy so callers may mutate it freely.
    """
    schema = copy.deepcopy(_REA_BASE_SCHEMA)
    if mentions_propulsion(prompt):
        schema["properties"].update(propulsion_rea_schema_extension())
    return schema


async def run_rea(prompt: str) -> "DesignRequirements | None":
    """T2-12: Requirements Engineering Agent.

    Extracts structured design requirements from a raw user prompt before
    it reaches the Planner. Returns a DesignRequirements object, or None
    on failure (caller continues without structured requirements).

    The REA and Clarifier are independent — both can run concurrently.
    REA feeds forward to the Planner; Clarifier feeds back to the user.
    """
    _REA_SCHEMA = _rea_schema_for(prompt)

    model = genai.GenerativeModel(
        model_name=MODEL_PRO,
        system_instruction=(
            "You are a mechanical engineering requirements analyst. "
            "Extract structured design requirements from the user's CAD design brief. "
            "Be conservative — only include information explicitly stated or clearly implied. "
            "Do NOT invent specifications not mentioned in the prompt. "
            "Set confidence < 0.5 if the prompt is too vague to reliably extract requirements. "
            # Phase 3 (XFOIL) — Domain D aerodynamic extraction
            "If inferred_domain is 'D' (aerospace/aerodynamic) AND the prompt explicitly "
            "names flight conditions, populate `flight_env`. NEVER guess Re or Mach — "
            "only set fields the user gave numerically. If the prompt names a NACA airfoil or "
            "wing dimensions, populate `airfoil_spec` with: naca_code (4-digit string e.g. '2412'), "
            "chord_mm, span_mm. Also populate sweep_le_deg if a sweep angle is stated, "
            "dihedral_deg if dihedral is stated, root_pitch_deg / tip_pitch_deg if blade pitch "
            "angles are stated (for propellers/rotors). Leave fields absent when not given. "
            # Phase 4: structural / flight-envelope spec extraction (Domain D)
            "STRUCTURAL EXTRACTION (Domain D): if the prompt gives a take-off weight AND a "
            "cruise condition, populate `aircraft_spec` (mtow_kg, cruise_alt_m, cruise_speed_ms; "
            "also category as 'normal'/'utility'/'acrobatic' and cl_max ONLY if stated). If a wing "
            "area or span is given, populate `wing_spec` (area_m2, span_m, mean_chord_m). If the "
            "prompt describes a structural wing box (spar caps, shear web, t/c), populate "
            "`wing_box_spec` SECTION-SIZING fields (chord_root_m, thickness_to_chord, cap_area_m2, "
            "spar_web_thickness_m, n_spars). Convert units to SI (kg, m, m/s). Do NOT invent the "
            "wing-box geometry layout — only fill fields the user actually gave. "
            "Examples: 'A 1200 kg light aircraft cruising at 60 m/s at 3000 m with a 16 m^2, 10.7 m "
            "span wing' -> aircraft_spec{mtow_kg:1200, cruise_alt_m:3000, cruise_speed_ms:60}, "
            "wing_spec{area_m2:16, span_m:10.7}. 'utility-category wing box, 1.8 m root chord, 12% "
            "t/c, 2 spars' -> aircraft_spec{category:'utility'}, wing_box_spec{chord_root_m:1.8, "
            "thickness_to_chord:0.12, n_spars:2}. "
            "If inferred_domain is 'F' (propulsion), populate `propulsion_spec` with all "
            "named dimensions: propulsion_type ('nozzle'/'chamber'/'nacelle'/'duct'), "
            "throat_diameter_mm, exit_diameter_mm, chamber_radius_mm, chamber_length_mm, "
            "wall_thickness_mm, num_fuel_inlets, highlight_radius_mm, fan_face_radius_mm, "
            "conv_half_angle_deg, div_half_angle_deg. Omit fields not explicitly stated. "
            "Return JSON matching the DesignRequirements schema."
        ),
    )

    try:
        rea_t0 = time.monotonic()
        logger.info("run_rea: calling Gemini (prompt=%d chars)", len(prompt))
        response = await model.generate_content_async(
            f"Extract design requirements from this brief:\n\n{prompt}",
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": _REA_SCHEMA,
            },
        )
        logger.info("run_rea: Gemini returned in %.1fs", time.monotonic() - rea_t0)
        import json as _json
        raw = _json.loads(response.text)
        # Log token usage
        try:
            usage = response.usage_metadata
            from telemetry import log_token_usage
            log_token_usage(
                agent="rea",
                prompt_tokens=usage.prompt_token_count or 0,
                completion_tokens=usage.candidates_token_count or 0,
            )
        except Exception:
            pass
        requirements = DesignRequirements(**raw)
        # Deterministic Domain F physics validation. Non-blocking: the REA feeds
        # the Planner regardless, but contradictory propulsion geometry (e.g. an
        # exit narrower than the throat, or a cowl inside its fan face) is surfaced
        # in telemetry so it can be triaged.
        if requirements.propulsion_spec is not None:
            prop_issues = validate_propulsion_spec(requirements.propulsion_spec)
            if prop_issues:
                logger.warning(
                    "run_rea: propulsion_spec failed %d physics check(s): %s",
                    len(prop_issues),
                    "; ".join(prop_issues),
                )
        return requirements
    except Exception:
        logger.exception("run_rea: failed — Planner will proceed without structured requirements")
        return None  # Non-blocking — Planner proceeds without structured requirements


async def run_clarifier(prompt: str) -> list[str]:
    """Pre-Planner clarification agent.

    Identifies the most important ambiguities in a design prompt that would
    prevent generating a correct assembly. Returns a list of clarifying
    question strings (max 5). Returns an empty list if the prompt is
    sufficiently detailed.

    This is a fast, cheap call — it should complete in <5 seconds.
    """
    model = genai.GenerativeModel(
        model_name=MODEL_LITE,
        system_instruction=(
            "You are a mechanical engineering requirements analyst. "
            "Given a user's 3D CAD design prompt, identify ONLY the most critical "
            "missing specifications that would prevent generating a correct model. "
            "Focus on structural information: part count, key dimensions, "
            "connection types, mechanism type. "
            "Do NOT ask about decorative details, surface finish, or materials "
            "unless the part cannot be generated without them. "
            "If the prompt is clear and specific enough to generate a model, "
            "return an empty list. "
            "Return a JSON object: {\"ambiguities\": [\"question 1\", \"question 2\", ...]}"
            "Maximum 4 questions. Short, concrete questions only."
        ),
    )

    try:
        response = await model.generate_content_async(
            f"Identify missing specifications in this design prompt:\n\n{prompt}",
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "ambiguities": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["ambiguities"],
                },
            },
        )
        import json as _json
        data = _json.loads(response.text)
        return data.get("ambiguities", [])[:4]
    except Exception:
        return []  # Fail silently — clarification is optional


async def run_planner(
    user_prompt: str,
    requirements: "DesignRequirements | None" = None,
) -> AssemblyManifest:
    """Subagent 1: The Draftsman.

    Converts a natural-language mechanical request into a structured
    AssemblyManifest using Gemini's structured output mode.
    """
    model = genai.GenerativeModel(
        model_name=MODEL_PRO,
        system_instruction=(
            "You are the Chief Draftsman for a mechanical CAD system. "
            "Convert the user's mechanical request into a strict AssemblyManifest JSON. "
            "Follow ALL anchor_tag and mating_rule constraints defined in the schema. "
            "RULES: "
            "(1) Every part must be self-contained and modeled at the origin. "
            "(2) anchor_tags MUST be standard CadQuery directional selectors ONLY: "
            "'>Z' (top), '<Z' (bottom), '>X' (right), '<X' (left), '>Y' (front), '<Y' (back). "
            "NEVER use custom named anchors like 'face_top_horizontal' or 'hole_center'. "
            "NEVER use coordinate tuples. "
            "(3) mating_rules must reference only existing part_ids and anchor_tags. "
            "(4) The assembly graph must be fully connected — no orphan parts. "
            "(5) List parts in logical build order: base/foundation first, fasteners last. "
            "(6) All dimensions must be in millimeters. "
            "\n"
            "ASSEMBLY CONSTRAINT MODEL (PREFERRED): "
            "Instead of specifying absolute XYZ translations, prefer using 'mate_type' in each MatingRule. "
            "Available mate types: "
            "  FASTENED — fully rigid, no relative motion (use for bolted joints, welds, press fits) "
            "  REVOLUTE — rotation only about shared axis (use for hinges, pins, shafts in bearings) "
            "  SLIDER — translation only along shared axis (use for drawer slides, linear guides) "
            "  CYLINDRICAL — rotation + translation along axis (use for loose shafts in clearance bores) "
            "  PLANAR — translation in the contact plane (use for sliding plates) "
            "  BALL — free rotation about contact point (use for ball joints, universal joints) "
            "When mate_type is provided, the constraint solver will automatically compute correct positions. "
            "You do NOT need to specify translation coordinates when using mate_type. "
            "For the source_anchor and target_anchor, use face selectors that describe the mating surfaces: "
            "  '>Z' = top face (pointing up), '<Z' = bottom face (pointing down) "
            "  '>X' = right face, '<X' = left face, '>Y' = front face, '<Y' = back face "
            "EXAMPLE: To stack part B on top of part A, use: "
            "  source_part_id='part_a', source_anchor='>Z', target_part_id='part_b', target_anchor='<Z', mate_type='FASTENED' "
            "\n"
            "SINGLE-PART EXCEPTION: For single-part assemblies, use a self-referencing rule with translation='0, 0, 0' as before. "
            "\n"
            "DOMAIN D AEROSPACE SUBSYSTEM RULE (OVERRIDES MULTI-PART RULE A): "
            "When the request involves an aerodynamic structure with internal "
            "reinforcement — specifically a wing, stabilizer, fin, or airfoil "
            "segment that contains ribs, spars, bulkheads, or any internal "
            "structural members — you MUST NOT decompose it into separate "
            "PartDefinition entries (e.g., do NOT create 'wing_skin', 'rib_1', "
            "'rib_2', 'spar_front' as separate parts). "
            "Instead, define a SINGLE PartDefinition with part_id "
            "'wing_segment_assembly' (or an appropriate name). In the "
            "description field, explicitly instruct the Machinist to: "
            "  (1) Loft the airfoil core solid — this IS the inner_void. "
            "  (2) Shell the inner_void OUTWARD (positive wall thickness) to grow the skin. "
            "  (3) Generate all ribs and spars by intersecting oversized blanks "
            "      with inner_void. "
            "  (4) Package everything into a cq.Assembly and assign it to result. "
            "Include the requested rib count, spar count, NACA code, chord, span, "
            "and any other parameters in the description. "
            "Use anchor_tags: ['>Z', '<Z', '>X', '<X', '<Y', '>Y']. "
            "This is MANDATORY because internal ribs depend on a shared boolean "
            "tooling body (the inner_void) that cannot be split across scripts. "
            "Create a self-referencing mating_rule with translation '0, 0, 0'. "
            "\n"
            "MULTI-PART PLANNING RULES: "
            "(A) For NON-aerospace assemblies, you MUST break down every multi-part "
            "assembly into DISTINCT, INDIVIDUALLY NAMED parts. Do NOT group identical "
            "parts into a single entry. For example, a table must have 'tabletop', "
            "'leg_1', 'leg_2', 'leg_3', 'leg_4' — five separate PartDefinition "
            "entries, NOT 'tabletop' and 'legs'. Even if parts are geometrically "
            "identical, each instance must be its own entry with a unique part_id. "
            "(B) When mate_type is not provided, each MatingRule's 'translation' field MUST contain "
            "the precise absolute 3D coordinate (X, Y, Z) in millimeters for where to place the target part. "
            "The base/first part is always at '0, 0, 0'. However, mate_type is now preferred for all new assemblies."
        ),
    )

    # Build planner input — prepend structured requirements if available (T2-12)
    if requirements is not None:
        import json as _json
        req_summary_parts = [f"PRIMARY FUNCTION: {requirements.primary_function}"]
        if requirements.key_dimensions:
            dims = ", ".join(f"{k}={v}mm" for k, v in requirements.key_dimensions.items())
            req_summary_parts.append(f"KEY DIMENSIONS: {dims}")
        if requirements.material_class:
            req_summary_parts.append(f"MATERIAL: {requirements.material_class}")
        if requirements.environment:
            req_summary_parts.append(f"ENVIRONMENT: {requirements.environment}")
        if requirements.connecting_interfaces:
            req_summary_parts.append(
                f"INTERFACES: {'; '.join(requirements.connecting_interfaces)}"
            )
        if requirements.inferred_domain:
            req_summary_parts.append(f"DOMAIN: {requirements.inferred_domain}")
        req_block = "\n".join(req_summary_parts)
        planner_input = (
            f"STRUCTURED REQUIREMENTS (authoritative — use these as the spec):\n"
            f"{req_block}\n\n"
            f"RAW PROMPT (for additional context not captured above):\n"
            f"{user_prompt}"
        )
    else:
        planner_input = user_prompt

    schema = _get_gemini_schema()
    logger.info(
        "run_planner: calling Gemini (model=%s, prompt=%d chars, has_requirements=%s)",
        MODEL, len(planner_input), requirements is not None,
    )
    llm_t0 = time.monotonic()
    response = await model.generate_content_async(
        planner_input,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
    )
    logger.info(
        "run_planner: Gemini returned in %.1fs (%d chars of JSON)",
        time.monotonic() - llm_t0,
        len(response.text or ""),
    )

    # Log token usage for cost analysis (T1-10)
    try:
        usage = response.usage_metadata
        from telemetry import log_token_usage
        log_token_usage(
            agent="planner",
            prompt_tokens=usage.prompt_token_count or 0,
            completion_tokens=usage.candidates_token_count or 0,
        )
    except Exception:
        pass

    validate_t0 = time.monotonic()
    manifest = AssemblyManifest.model_validate_json(response.text)
    logger.info(
        "run_planner: manifest validated in %.3fs (parts=%d, mates=%d)",
        time.monotonic() - validate_t0,
        len(manifest.parts),
        len(manifest.mating_rules),
    )
    return manifest


def is_single_part(manifest: AssemblyManifest) -> bool:
    """Check if the manifest describes a single part (no real assembly needed)."""
    if len(manifest.parts) == 1:
        return True
    return False


async def _score_and_log(prompt: str, code: str, part_id: str) -> None:
    """Fire-and-forget LVM score computation."""
    try:
        from evaluation import compute_lvm_score, log_lvm_score
        score = await compute_lvm_score(prompt, code, part_id)
        if score.get("lvm_score") is not None:
            log_lvm_score(score)
    except Exception:
        pass


async def run_machinist(
    part_def: PartDefinition,
    domain: str = "A",
    error_context: str = "",
    retry_failure_class: str | None = None,
    retry_techniques: list[str] | None = None,
) -> str:
    """Subagent 2: The Machinist.

    Generates isolated CadQuery Python code for a single part.
    The domain must be pre-classified by the caller; the system prompt
    is built dynamically based on domain and part description keywords.

    On retry (``retry_failure_class`` or ``retry_techniques`` provided), the
    few-shots are pulled by ``retrieve_for_failure`` so the Machinist sees a
    working example of the exact pattern that just blew up, instead of more
    semantically-similar parts that share the same broken technique.

    Model escalation: first attempt uses the Heavy flash model for speed.
    Any retry (error_context present) escalates to Pro for deeper reasoning
    about the logic error that caused the failure.
    """
    system_prompt = get_strategy(domain, part_def.description)

    # Always use Heavy (gemini-3.5-flash) — Pro escalation on retries removed.
    active_model = MODEL
    model = genai.GenerativeModel(
        model_name=active_model,
        system_instruction=system_prompt,
    )

    # Few-shot retrieval augmentation (T1-05). Description similarity on the
    # first attempt; failure-aware retrieval on retry.
    if retry_failure_class or retry_techniques:
        few_shots = retrieve_for_failure(
            failure_class=retry_failure_class or "unknown",
            techniques=retry_techniques or [],
            domain=domain,
            k=3,
        )
        header = (
            "\n\nWorking CadQuery examples that successfully use the "
            f"technique that just failed for you ({retry_failure_class or 'unknown'}):\n"
        )
    else:
        few_shots = retrieve_few_shots(part_def.description, domain=domain, k=3)
        header = (
            "\n\nHere are successful CadQuery examples for similar parts "
            "(for reference only — generate NEW code for this specific part):\n"
        )

    few_shot_section = ""
    if few_shots:
        examples = []
        for ex in few_shots:
            preview = ex["code"][:800]
            examples.append(f"# Example output for similar part:\n```python\n{preview}\n```")
        few_shot_section = header + "\n\n".join(examples) + "\n\n---\n\n"

    prompt = (
        few_shot_section +
        f"Generate CadQuery code for this part:\n"
        f"Part ID: {part_def.part_id}\n"
        f"Description: {part_def.description}\n"
        f"Anchor Tags: {part_def.anchor_tags}\n"
    )

    if error_context:
        prompt += (
            f"\nYour previous attempt FAILED with this error:\n"
            f"---\n{error_context}\n---\n"
            f"Fix the CadQuery code to resolve this error. "
            f"Return the complete corrected script."
        )

    response = await model.generate_content_async(prompt)

    # Log token usage for cost analysis (T1-10)
    try:
        usage = response.usage_metadata
        from telemetry import log_token_usage
        log_token_usage(
            agent=f"machinist_{domain}",
            prompt_tokens=usage.prompt_token_count or 0,
            completion_tokens=usage.candidates_token_count or 0,
        )
    except Exception:
        pass

    return _strip_markdown_fences(response.text)


async def _save_part_step(part_id: str, script: str) -> str:
    """Save a compiled part as an isolated .step file for the deterministic assembler.

    Re-executes the validated script with an appended export stanza.
    Returns the absolute path to the saved .step file.

    Raises ScriptError if the export subprocess fails — previously this
    silently ignored failures, leaving downstream stages to blow up with a
    confusing 'missing file' error.
    """
    safe_id = _sanitize_part_id(part_id)
    _TMP_PARTS_DIR.mkdir(parents=True, exist_ok=True)
    # Unique per call: two concurrent requests can both hold a part named e.g.
    # "base". A deterministic `{safe_id}.step` path made them clobber each
    # other on the shared tmpfs — request A's assembler could even load
    # request B's geometry. The uuid suffix isolates every export.
    step_path = (_TMP_PARTS_DIR / f"{safe_id}_{uuid.uuid4().hex}.step").resolve()
    if not str(step_path).startswith(str(_TMP_PARTS_DIR.resolve())):
        raise ValueError(f"Path traversal detected in part_id: {part_id!r}")
    safe_path = step_path.as_posix()

    export_script = (
        f"{script}\n\n"
        f"import cadquery as cq\n"
        f"if isinstance(result, cq.Assembly):\n"
        f"    result.save(r'{safe_path}')\n"
        f"else:\n"
        f"    cq.exporters.export(result, r'{safe_path}')\n"
    )
    result = await execute_cad_script(export_script)
    if result["status"] != "success":
        raise ScriptError(
            f"Failed to export .step for part '{part_id}':\n"
            f"{result.get('traceback', 'unknown error')}",
            script=export_script,
        )
    return safe_path


async def run_critic_loop(
    part_def: PartDefinition,
    max_retries: int = 3,
    save_step: bool = True,
) -> tuple[str, str, dict | None]:
    """Subagent 3: The QA Inspector (Critic Loop).

    Runs the Machinist's code via subprocess with concurrency control.
    On failure, feeds the traceback back for self-correction.

    Args:
        part_def: The part definition to compile.
        max_retries: Max machinist attempts before giving up.
        save_step: Whether to export a .step file for the deterministic
            assembler. Single-part requests skip this to avoid an extra
            subprocess call whose output they'd just throw away.

    Returns:
        (validated_code, step_file_path, geometry_cert) — the clean CadQuery
        source, the absolute path to the pre-compiled .step file (empty string
        when save_step is False), and a GeometryCertificate dict (or None when
        save_step is False or OCC is unavailable).
    """
    async with _MACHINIST_SEMAPHORE:
        # Classify once per part — domain is stable across retries
        classification = await classify_part(part_def.description)
        domain = classification.get("domain", "A")

        error_context = ""
        retry_failure_class: str | None = None
        retry_techniques: list[str] | None = None

        for attempt in range(1, max_retries + 1):
            code = await run_machinist(
                part_def,
                domain=domain,
                error_context=error_context,
                retry_failure_class=retry_failure_class,
                retry_techniques=retry_techniques,
            )
            result = await execute_cad_script(code)

            success = result["status"] == "success"
            failure_op = None
            if not success:
                failure_op = classify_failure(
                    result.get("traceback", ""), code
                )
            log_attempt(
                part_id=part_def.part_id,
                domain=domain,
                attempt=attempt,
                code=code,
                error=None if success else result.get("traceback"),
                success=success,
                failure_operation=failure_op,
            )

            if success:
                # T2-04: G2 continuity check for Domain C parts
                g2_feedback = ""
                if domain == "C" and save_step and attempt < max_retries:
                    try:
                        from continuity_check import check_g2_continuity
                        # Save a temp step to check
                        temp_step = await _save_part_step(
                            part_def.part_id + "_g2check", code
                        )
                        g2_result = check_g2_continuity(temp_step, domain)
                        if not g2_result.passed:
                            # Inject G2 feedback and retry
                            g2_feedback = g2_result.feedback_message
                            log_attempt(
                                part_id=part_def.part_id,
                                domain=domain,
                                attempt=attempt,
                                code=code,
                                error=f"G2_CONTINUITY_FAIL: {g2_result.violation_count} violations",
                                success=False,
                                failure_operation="continuity",
                            )
                            error_context = g2_feedback
                            retry_failure_class = "continuity"
                            retry_techniques = extract_techniques(code)
                            # Clean up temp step, continue retry loop
                            try:
                                import os as _os
                                _os.remove(temp_step)
                            except OSError:
                                pass
                            continue
                        # G2 passed — remove temp step (will save below)
                        try:
                            import os as _os
                            _os.remove(temp_step)
                        except OSError:
                            pass
                    except Exception:
                        pass  # G2 check is advisory — never block compilation

                # T3-07: Gaussian curvature analysis for Domain C parts
                # Runs after G2 check if retries remain
                if domain == "C" and save_step and attempt < max_retries:
                    try:
                        from geometry_analysis import curvature_check_for_critic
                        gauss_step = await _save_part_step(
                            part_def.part_id + "_gauss_check", code
                        )
                        gauss_result = curvature_check_for_critic(
                            gauss_step, domain, part_def.description, n_samples=20
                        )
                        # Clean up temp step
                        try:
                            import os as _os
                            _os.remove(gauss_step)
                        except OSError:
                            pass
                        if not gauss_result.passed:
                            log_attempt(
                                part_id=part_def.part_id,
                                domain=domain,
                                attempt=attempt,
                                code=code,
                                error=(
                                    f"GAUSSIAN_CURVATURE_FAIL: "
                                    f"{gauss_result.n_saddle_points} saddle points "
                                    f"({gauss_result.n_saddle_points}/{max(gauss_result.n_sample_points,1):.0%})"
                                ),
                                success=False,
                                failure_operation="gaussian_curvature",
                            )
                            error_context = gauss_result.feedback_message
                            retry_failure_class = "gaussian_curvature"
                            retry_techniques = extract_techniques(code)
                            continue
                    except Exception:
                        pass  # Curvature check is advisory — never block compilation

                # Add to retrieval index for future few-shot injection
                add_to_index(
                    description=part_def.description,
                    code=code,
                    domain=domain,
                )
                # LVM scoring (T1-07) — async, fire-and-forget, non-blocking
                asyncio.create_task(_score_and_log(part_def.description, code, part_def.part_id))
                step_path = (
                    await _save_part_step(part_def.part_id, code)
                    if save_step
                    else ""
                )

                # Geometry certificate — runs for all domains when a STEP file
                # was produced.  Never blocks delivery; failures are informational.
                geometry_cert: dict | None = None
                if step_path:
                    try:
                        from verification.geometry import certify_geometry
                        geometry_cert = dict(certify_geometry(step_path))
                    except Exception as _cert_exc:
                        logger.debug("geometry cert skipped: %s", _cert_exc)

                return code, step_path, geometry_cert

            active_rules = get_active_rules(domain, part_def.description)
            error_context = build_retry_context(
                traceback=result.get("traceback", ""),
                code=code,
                active_rules=active_rules,
            )
            retry_failure_class = failure_op
            retry_techniques = extract_techniques(code)
            if attempt == max_retries:
                raise ScriptError(
                    f"Part '{part_def.part_id}' failed after {max_retries} attempts. "
                    f"Last error:\n{error_context}",
                    script=code,
                )

        raise ScriptError(f"Part '{part_def.part_id}' failed unexpectedly.")


async def run_machinist_batch(
    parts: list[PartDefinition],
) -> tuple[dict[str, str], dict[str, str], dict[str, dict | None]]:
    """Run all Machinist critic loops concurrently with rate limiting.

    Deduplicates parts with identical descriptions: calls the Machinist
    only once per unique description and reuses the cached code for all
    identical parts.

    Returns:
        (part_scripts, step_files, geometry_certs) — three dicts mapping
        part_id to validated code, pre-compiled .step file path, and
        GeometryCertificate dict (or None when OCC is unavailable).
    """
    # Group parts by description for deduplication
    desc_to_parts: dict[str, list[PartDefinition]] = {}
    for part in parts:
        desc_to_parts.setdefault(part.description, []).append(part)

    # Build one representative per unique description
    unique_parts = [group[0] for group in desc_to_parts.values()]

    tasks = [run_critic_loop(part) for part in unique_parts]
    results = await asyncio.gather(*tasks)

    # Map representative description -> (code, step_path, geometry_cert)
    desc_to_result = {
        part.description: result for part, result in zip(unique_parts, results)
    }

    # Fan out cached results to all parts sharing the same description
    part_scripts: dict[str, str] = {}
    step_files: dict[str, str] = {}
    geometry_certs: dict[str, dict | None] = {}
    for part in parts:
        code, step_path, geometry_cert = desc_to_result[part.description]
        part_scripts[part.part_id] = code
        step_files[part.part_id] = step_path
        geometry_certs[part.part_id] = geometry_cert

    return part_scripts, step_files, geometry_certs


async def run_single_part_export(part_script: str, output_filename: str) -> str:
    """Generate a simple export script for single-part models (no assembly).

    If the Machinist already produced a cq.Assembly (e.g., Domain D subsystem),
    we save it directly instead of nesting it inside another assembly.
    """
    return (
        f"{part_script}\n\n"
        f"import cadquery as cq\n"
        f"if isinstance(result, cq.Assembly):\n"
        f"    result.save('{output_filename}')\n"
        f"else:\n"
        f"    _export_asm = cq.Assembly()\n"
        f"    _export_asm.add(result, name='part')\n"
        f"    _export_asm.save('{output_filename}')\n"
    )


# --------------------------------------------------------------------------
# Phase 1: Deterministic Assembler (ZERO LLM involvement)
# --------------------------------------------------------------------------

def run_assembler(
    manifest: AssemblyManifest,
    step_files: dict[str, str],
) -> str:
    """Deterministic assembler — generates a CadQuery script from .step files.

    Loads each pre-compiled .step file via cq.importers.importStep(),
    applies the exact X, Y, Z translations from the manifest's mating_rules,
    adds them to a cq.Assembly, and saves the final .glb and .step files.

    No LLM is involved. The output is a fully deterministic script.
    """
    lines = ["import cadquery as cq", ""]
    lines.append("assembly = cq.Assembly()")
    lines.append("")

    added: set[str] = set()

    # Add each part at its manifest-specified position
    for part in manifest.parts:
        pid = part.part_id
        if pid in added or pid not in step_files:
            continue

        safe_pid = _sanitize_part_id(pid)

        # Find translation: look for a mating rule where this part is the target.
        # The base/first part is never a target — defaults to origin (0, 0, 0).
        tx, ty, tz = 0.0, 0.0, 0.0
        for rule in manifest.mating_rules:
            if rule.target_part_id == pid:
                coords = [float(v.strip()) for v in rule.translation.split(",")]
                tx, ty, tz = coords[0], coords[1], coords[2]
                break

        safe_path = step_files[pid].replace("\\", "/")
        var = safe_pid if safe_pid.isidentifier() else f"part_{safe_pid}"

        lines.append(f"{var} = cq.importers.importStep(r'{safe_path}')")
        lines.append(
            f"assembly.add({var}, name='{safe_pid}', "
            f"loc=cq.Location(cq.Vector({tx}, {ty}, {tz})))"
        )
        lines.append("")
        added.add(pid)

    # Only .glb is returned to the client. We intentionally do NOT write
    # output.step here — every concurrent request would race on the same
    # filename, and no downstream consumer reads it.
    lines.append("assembly.save('output.glb')")

    return "\n".join(lines)


def check_assembly_interpenetration(
    step_files: dict[str, str],
) -> list[tuple[str, str]]:
    """Check for pairwise volumetric interpenetration between assembly parts.

    Loads each compiled .step file and checks whether any pair of solids
    share non-zero common volume. Parts are checked at their origin positions
    (pre-solve), so this catches obvious design errors where parts overlap
    even before constraint solving.

    Returns a list of (part_id_a, part_id_b) pairs that interpenetrate.
    An empty list means no interpenetration detected.

    Note: This is a conservative check at origin. Post-solve interpenetration
    (caused by bad constraint anchors) requires extracting positioned shapes
    from the solved assembly, which is implemented as a future enhancement.
    """
    try:
        import cadquery as cq
    except ImportError:
        return []

    shapes: dict[str, object] = {}
    for name, path in step_files.items():
        try:
            shapes[name] = cq.importers.importStep(path)
        except Exception:
            continue

    violations: list[tuple[str, str]] = []
    names = list(shapes.keys())

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            na, nb = names[i], names[j]
            try:
                common = shapes[na].intersect(shapes[nb])
                # If the intersection has any solid bodies, parts overlap
                if common.solids().size() > 0:
                    violations.append((na, nb))
            except Exception:
                pass  # Intersection may fail for complex geometry — skip

    return violations


def run_assembler_constraint(
    manifest: AssemblyManifest,
    step_files: dict[str, str],
) -> str:
    """Constraint-based assembler — uses cq.Constraint + assembly.solve().

    Generates a CadQuery script that:
    1. Loads each .step file
    2. Adds all parts to a cq.Assembly (no location — solver positions them)
    3. Applies typed constraints from mating_rules
    4. Calls assembly.solve() to let OCCT compute positions
    5. Saves the result

    Falls back gracefully: if mate_type is missing for a rule, skips that
    constraint (the part will remain at its default position).
    """
    lines = ["import cadquery as cq", ""]

    # Load all parts
    added: set[str] = set()
    var_names: dict[str, str] = {}
    for part in manifest.parts:
        pid = part.part_id
        if pid in added or pid not in step_files:
            continue
        safe_pid = _sanitize_part_id(pid)
        var = safe_pid if safe_pid.isidentifier() else f"part_{safe_pid}"
        var_names[pid] = var
        safe_path = step_files[pid].replace("\\", "/")
        lines.append(f"{var} = cq.importers.importStep(r'{safe_path}')")
        added.add(pid)

    lines.append("")
    lines.append("assembly = cq.Assembly()")
    lines.append("")

    # Mixed-mode manifests: the solver only places parts that carry a
    # mate_type. A part whose only rule is translation-only would otherwise be
    # added with no location and silently left at the origin. Pre-compute an
    # initial location for those parts so they land where the manifest says.
    # Parts that ARE the target of a real constraint are left to the solver.
    constrained_targets = {
        r.target_part_id
        for r in manifest.mating_rules
        if r.mate_type is not None and r.source_part_id != r.target_part_id
    }
    translation_locs: dict[str, tuple[float, float, float]] = {}
    for r in manifest.mating_rules:
        if r.source_part_id == r.target_part_id or r.mate_type is not None:
            continue
        if r.translation is None or r.target_part_id in constrained_targets:
            continue
        try:
            cx, cy, cz = [float(v.strip()) for v in r.translation.split(",")]
        except (ValueError, IndexError):
            continue  # malformed — MatingRule's validator should prevent this
        translation_locs[r.target_part_id] = (cx, cy, cz)

    # Add all parts to assembly; constrained parts get no location (solver
    # positions them), translation-only parts get their manifest location.
    for part in manifest.parts:
        pid = part.part_id
        if pid not in var_names:
            continue
        safe_pid = _sanitize_part_id(pid)
        var = var_names[pid]
        if pid in translation_locs:
            tx, ty, tz = translation_locs[pid]
            lines.append(
                f"assembly.add({var}, name='{safe_pid}', "
                f"loc=cq.Location(cq.Vector({tx}, {ty}, {tz})))"
            )
        else:
            lines.append(f"assembly.add({var}, name='{safe_pid}')")

    lines.append("")
    lines.append("# Apply kinematic constraints")

    # Apply constraints from mating rules
    for rule in manifest.mating_rules:
        # Skip self-referencing rules (single-part assemblies)
        if rule.source_part_id == rule.target_part_id:
            continue
        if rule.mate_type is None:
            continue  # No mate_type — skip (part stays at default position)

        src_safe = _sanitize_part_id(rule.source_part_id)
        tgt_safe = _sanitize_part_id(rule.target_part_id)
        constraint_types = MATE_CONSTRAINTS.get(str(rule.mate_type), ["Plane"])

        # Build CadQuery selector strings
        # anchor format is ">Z", "<Z" etc — we use faces@ prefix
        src_sel = f"faces@{rule.source_anchor}"
        tgt_sel = f"faces@{rule.target_anchor}"

        for ct in constraint_types:
            lines.append(
                f"assembly.constrain('{src_safe}@{src_sel}', "
                f"'{tgt_safe}@{tgt_sel}', '{ct}')"
            )

    lines.append("")
    lines.append("# Solve the constraint system")
    lines.append("try:")
    lines.append("    assembly.solve()")
    lines.append("    print('SOLVER_SUCCESS')")
    lines.append("except Exception as _solve_err:")
    lines.append("    print(f'SOLVER_FAILURE: {_solve_err}')")
    lines.append("")
    lines.append("assembly.save('output.glb')")

    return "\n".join(lines)


async def run_assembly_critic_loop(
    manifest: AssemblyManifest,
    step_files: dict[str, str],
) -> str:
    """Execute the deterministic assembler and return the script.

    Unlike the old LLM-based assembler, this has no retry loop — the
    script is deterministic. If it fails, the error is structural
    (bad .step file or missing part) and retrying won't help.
    """
    if len(step_files) != len(manifest.parts):
        raise ScriptError(
            f"Assembly failed: Missing compiled parts. Expected "
            f"{len(manifest.parts)} parts, but only got {len(step_files)}."
        )

    # Interpenetration check (T1-02)
    interp_violations = check_assembly_interpenetration(step_files)
    if interp_violations:
        # Log but don't fail — violations at origin may resolve after solve()
        import logging
        logging.getLogger("mirum.agents").warning(
            "Interpenetration detected between: %s",
            ", ".join(f"{a}↔{b}" for a, b in interp_violations),
        )

    # Choose assembler mode
    use_constraints = any(
        r.mate_type is not None for r in manifest.mating_rules
        if r.source_part_id != r.target_part_id
    )
    script = (
        run_assembler_constraint(manifest, step_files)
        if use_constraints
        else run_assembler(manifest, step_files)
    )

    # This validation run executes in the shared work dir. Saving to the
    # literal 'output.glb' would let two concurrent assemblies clobber the
    # same path, so validate against a unique name. The RETURNED script keeps
    # 'output.glb' — the caller substitutes its own delivery path into it.
    val_name = f"_assembly_validate_{uuid.uuid4().hex}.glb"
    result = await execute_cad_script(script.replace("output.glb", val_name))
    try:
        (_WORK_DIR / val_name).unlink(missing_ok=True)
    except OSError:
        pass

    if result["status"] != "success":
        raise ScriptError(
            f"Deterministic assembly failed:\n{result['traceback']}",
            script=script,
        )

    # Log assembly telemetry
    solver_ok = "SOLVER_FAILURE" not in result.get("output", "")
    try:
        from telemetry import log_assembly_event
        log_assembly_event(
            assembly_name=manifest.assembly_name,
            solver_success=solver_ok,
            constraint_mode=use_constraints,
            interpenetration_pairs=interp_violations,
            part_count=len(manifest.parts),
        )
    except Exception:
        pass  # Non-blocking

    return script


# ---------------------------------------------------------------------------
# T2-02: DoF range validation helpers
# ---------------------------------------------------------------------------

_ROTATIONAL_MATE_TYPES = {"REVOLUTE", "CYLINDRICAL"}
_TRANSLATIONAL_MATE_TYPES = {"SLIDER", "CYLINDRICAL"}


def validate_dof_ranges(manifest: AssemblyManifest) -> list[dict]:
    """Check that mating rules with dof_min/dof_max have consistent units.

    Returns a list of violation dicts. An empty list means no issues.
    This is a schema-level check; post-solve positional validation requires
    extracting solved transforms from OCCT, deferred to a future enhancement.
    """
    violations = []
    for rule in manifest.mating_rules:
        if rule.dof_min is None and rule.dof_max is None:
            continue
        if rule.dof_unit not in ("deg", "mm"):
            violations.append({
                "rule": f"{rule.source_part_id}↔{rule.target_part_id}",
                "issue": f"dof_unit must be 'deg' or 'mm', got: {rule.dof_unit!r}",
            })
            continue
        if rule.dof_min is not None and rule.dof_max is not None:
            if rule.dof_min > rule.dof_max:
                violations.append({
                    "rule": f"{rule.source_part_id}↔{rule.target_part_id}",
                    "issue": (
                        f"dof_min ({rule.dof_min}) > dof_max ({rule.dof_max}) "
                        f"— range is inverted"
                    ),
                })
        # Warn if unit/mate_type combination is suspicious
        mt = str(rule.mate_type) if rule.mate_type else ""
        if rule.dof_unit == "deg" and mt in _TRANSLATIONAL_MATE_TYPES - _ROTATIONAL_MATE_TYPES:
            violations.append({
                "rule": f"{rule.source_part_id}↔{rule.target_part_id}",
                "issue": (
                    f"dof_unit='deg' on SLIDER mate — SLIDER has no rotational DoF; "
                    f"did you mean dof_unit='mm'?"
                ),
            })
    return violations


# ---------------------------------------------------------------------------
# T2-03: Coupling constraint metadata extractor
# ---------------------------------------------------------------------------

_COUPLING_MATE_TYPES = {"GEAR", "SCREW", "RACK_PINION", "CAM"}


def extract_kinematic_metadata(manifest: AssemblyManifest) -> dict:
    """Extract coupling constraint metadata for downstream simulation tools.

    Coupling mates (GEAR, SCREW, RACK_PINION, CAM) are not handled by
    cq.Assembly.solve() — they define velocity relationships between already-
    constrained joints. This function serialises them as a metadata block
    for export alongside the assembled .glb/.step files.

    Returns a dict that can be serialised to JSON and written as
    {assembly_name}_kinematic.json next to the assembly output.
    """
    couplings = []
    for rule in manifest.mating_rules:
        if rule.mate_type is None:
            continue
        mt = str(rule.mate_type).upper()
        if mt not in _COUPLING_MATE_TYPES:
            continue
        entry: dict = {
            "type": mt,
            "driver": rule.source_part_id,
            "driven": rule.target_part_id,
            "driver_anchor": rule.source_anchor,
            "driven_anchor": rule.target_anchor,
        }
        if rule.coupling_ratio is not None:
            entry["coupling_ratio"] = rule.coupling_ratio
            if mt == "GEAR":
                entry["description"] = (
                    f"Gear transmission: {rule.coupling_ratio:.3f}:1 ratio "
                    f"({rule.source_part_id} drives {rule.target_part_id})"
                )
            elif mt == "SCREW":
                entry["description"] = (
                    f"Lead screw: {rule.coupling_ratio:.3f} mm/rev "
                    f"({rule.source_part_id} rotates, {rule.target_part_id} translates)"
                )
            elif mt == "RACK_PINION":
                entry["description"] = (
                    f"Rack-pinion: {rule.coupling_ratio:.3f} mm/rad "
                    f"(pinion {rule.source_part_id}, rack {rule.target_part_id})"
                )
        if rule.dof_min is not None or rule.dof_max is not None:
            entry["dof_range"] = {
                "min": rule.dof_min,
                "max": rule.dof_max,
                "unit": rule.dof_unit,
            }
        couplings.append(entry)

    return {
        "assembly_name": manifest.assembly_name,
        "coupling_constraints": couplings,
        "has_kinematic_coupling": len(couplings) > 0,
    }


# ---------------------------------------------------------------------------
# T2-08: Multi-turn refinement — RefinerAgent
# ---------------------------------------------------------------------------

async def run_refiner(
    original_manifest: "AssemblyManifest",
    original_scripts: dict[str, str],
    refinement_prompt: str,
) -> "ManifestDiff":
    """RefinerAgent: computes a differential update to an existing assembly.

    Given the original manifest and a natural-language refinement request,
    returns a ManifestDiff specifying ONLY the changes needed.
    Unchanged parts are not re-generated.

    Args:
        original_manifest: The AssemblyManifest from the prior /generate run.
        original_scripts: Map of part_id -> CadQuery script from the prior run.
        refinement_prompt: Natural-language description of the desired change.

    Returns:
        ManifestDiff with modified_parts, added_parts, removed_parts,
        modified_mates, and updated_descriptions.
    """
    from schemas import ManifestDiff  # local import to avoid circular deps

    # Summarise the current manifest for the LLM context
    parts_summary = "\n".join(
        f"  - {p.part_id}: {p.description[:120]}..."
        if len(p.description) > 120 else f"  - {p.part_id}: {p.description}"
        for p in original_manifest.parts
    )
    mates_summary = "\n".join(
        f"  - {r.source_part_id} → {r.target_part_id} "
        f"({r.mate_type or 'translation'})"
        for r in original_manifest.mating_rules
        if r.source_part_id != r.target_part_id
    )

    model = genai.GenerativeModel(
        model_name=MODEL_PRO,
        system_instruction=(
            "You are the Chief Draftsman for a mechanical CAD system. "
            "You are given an existing assembly design and a refinement request. "
            "Your job is to output ONLY the changes needed — do not re-specify "
            "unchanged parts or unchanged mates. "
            "\n"
            "Rules:\n"
            "  (1) If a part's geometry changes, add its part_id to modified_parts "
            "      and its new description to updated_descriptions.\n"
            "  (2) If a new part is needed, add it to added_parts as a full PartDefinition.\n"
            "  (3) If a part is removed, add its part_id to removed_parts.\n"
            "  (4) If a mating rule changes (new mate type, different anchors, "
            "      updated coupling_ratio), include the full updated rule in modified_mates.\n"
            "  (5) If nothing changes for a part or mate, do NOT include it.\n"
            "  (6) Keep part descriptions self-contained and in millimeters.\n"
            "  (7) Do not output absolute coordinates — use mate_type constraints.\n"
            "\n"
            "Return JSON matching the ManifestDiff schema exactly."
        ),
    )

    diff_schema = {
        "type": "object",
        "properties": {
            "modified_parts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "part_ids to re-generate",
            },
            "added_parts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "part_id": {"type": "string"},
                        "description": {"type": "string"},
                        "anchor_tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["part_id", "description", "anchor_tags"],
                },
            },
            "removed_parts": {
                "type": "array",
                "items": {"type": "string"},
            },
            "modified_mates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_part_id": {"type": "string"},
                        "source_anchor": {"type": "string"},
                        "target_part_id": {"type": "string"},
                        "target_anchor": {"type": "string"},
                        "mate_type": {"type": "string"},
                        "clearance": {"type": "number"},
                        "dof_min": {"type": "number"},
                        "dof_max": {"type": "number"},
                        "dof_unit": {"type": "string"},
                        "coupling_ratio": {"type": "number"},
                    },
                    "required": [
                        "source_part_id", "source_anchor",
                        "target_part_id", "target_anchor", "clearance",
                    ],
                },
            },
            "updated_descriptions": {
                "type": "object",
                "description": "part_id -> new description for modified parts",
            },
        },
        "required": [
            "modified_parts", "added_parts", "removed_parts",
            "modified_mates", "updated_descriptions",
        ],
    }

    user_message = (
        f"EXISTING ASSEMBLY: {original_manifest.assembly_name}\n\n"
        f"CURRENT PARTS:\n{parts_summary}\n\n"
        f"CURRENT MATES:\n{mates_summary or '  (none)'}\n\n"
        f"REFINEMENT REQUEST:\n{refinement_prompt}\n\n"
        "Output a ManifestDiff with ONLY the changes needed."
    )

    try:
        response = await model.generate_content_async(
            user_message,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": diff_schema,
            },
        )
        # Log token usage
        try:
            usage = response.usage_metadata
            from telemetry import log_token_usage
            log_token_usage(
                agent="refiner",
                prompt_tokens=usage.prompt_token_count or 0,
                completion_tokens=usage.candidates_token_count or 0,
            )
        except Exception:
            pass

        raw = json.loads(response.text)
        return ManifestDiff(**raw)
    except Exception as exc:
        # On failure, return an empty diff (no changes applied)
        import logging
        logging.getLogger("mirum.agents").error("RefinerAgent failed: %s", exc)
        from schemas import ManifestDiff as MD
        return MD()


def apply_manifest_diff(
    original: "AssemblyManifest",
    diff: "ManifestDiff",
    updated_descriptions: dict[str, str] | None = None,
) -> "AssemblyManifest":
    """Merge a ManifestDiff into an existing AssemblyManifest.

    Returns a new AssemblyManifest with the diff applied:
    - Removes removed_parts and their associated mates
    - Adds added_parts
    - Updates descriptions for modified_parts
    - Replaces or appends modified_mates
    """
    import copy
    from schemas import PartDefinition as PD, MatingRule as MR

    desc_map = dict(diff.updated_descriptions or {})

    # Build updated parts list
    new_parts = []
    for part in original.parts:
        if part.part_id in diff.removed_parts:
            continue
        if part.part_id in desc_map:
            # Replace description, keep everything else
            updated = copy.copy(part)
            updated = PD(
                part_id=part.part_id,
                description=desc_map[part.part_id],
                anchor_tags=part.anchor_tags,
            )
            new_parts.append(updated)
        else:
            new_parts.append(part)
    new_parts.extend(diff.added_parts)

    # Build updated mates
    removed_ids = set(diff.removed_parts)
    existing_mates = [
        r for r in original.mating_rules
        if r.source_part_id not in removed_ids
        and r.target_part_id not in removed_ids
    ]
    # Index by (src, tgt) for replacement
    diff_mate_index = {
        (r.source_part_id, r.target_part_id): r
        for r in diff.modified_mates
    }
    new_mates = []
    seen_keys: set = set()
    for r in existing_mates:
        key = (r.source_part_id, r.target_part_id)
        if key in diff_mate_index:
            new_mates.append(diff_mate_index[key])
            seen_keys.add(key)
        else:
            new_mates.append(r)
    # Append genuinely new mates (not replacements)
    for r in diff.modified_mates:
        key = (r.source_part_id, r.target_part_id)
        if key not in seen_keys:
            new_mates.append(r)

    return AssemblyManifest(
        assembly_name=original.assembly_name,
        parts=new_parts,
        mating_rules=new_mates,
    )
