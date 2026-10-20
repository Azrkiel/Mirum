"""
Constraint-Based Assembly Schemas for Text-to-CAD Pipeline.

These Pydantic models define the strict inter-agent communication contract.
They are used as `response_schema` in Gemini API calls to enforce structured,
hallucination-resistant output from every subagent.

PREFERRED: spatial relationships are TOPOLOGICAL and RELATIVE. Parts are
connected via named anchor tags (CadQuery selectors) and MatingRule.mate_type
constraints solved by the Assembler.

LEGACY EXCEPTION: MatingRule.translation is an absolute 'X, Y, Z' offset in mm.
It is deprecated but still honoured when a rule has no mate_type (and for the
single-part self-referencing rule '0, 0, 0'), so absolute placement remains
possible through that one field.
"""

import math
import re
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from propulsion.specs import PropulsionSpec

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_]")

# Planar-face selectors the mate-joint assembler can resolve on an imported
# STEP body: a signed axis direction, optionally indexed ('>Z[-2]'). Named
# semantic anchors do not survive STEP export, so they cannot carry a joint.
NATIVE_FACE_SELECTOR_RE = re.compile(r"[<>][XYZ](\[-?\d{1,2}\])?")

# Upper bound on a declared joint clearance (mm). Larger values are not a fit.
MAX_CLEARANCE_MM = 1000.0


def sanitize_part_id(raw: str) -> str:
    """Strip part_id to alphanumeric + underscore only (max 64 chars).

    This is the namespace the generated assembly actually uses (node names,
    STEP labels, temporary file stems), so manifest uniqueness is checked
    here as well as on the raw strings.
    """
    return _SAFE_ID_RE.sub("_", raw)[:64] or "unnamed_part"


class MateType(str, Enum):
    # Kinematic constraint types (core)
    FASTENED = "FASTENED"        # fully rigid — no relative motion
    REVOLUTE = "REVOLUTE"        # rotation about shared axis only
    SLIDER = "SLIDER"            # translation along shared axis only
    CYLINDRICAL = "CYLINDRICAL"  # rotation + translation along axis
    PLANAR = "PLANAR"            # translation in contact plane, free rotation
    BALL = "BALL"                # free rotation about contact point

    # Mechanism-level coupling constraints (T2-03)
    GEAR = "GEAR"                # coupled revolute: angular velocity ratio
    SCREW = "SCREW"              # coupled revolute + slider: pitch rate
    RACK_PINION = "RACK_PINION"  # revolute ↔ slider coupling
    CAM = "CAM"                  # surface-contact (follower on cam profile)


class PartDefinition(BaseModel):
    """A single mechanical part to be manufactured by the Machinist subagent.

    Each part is generated in COMPLETE ISOLATION — the Machinist receives only
    this definition and must produce a self-contained CadQuery script that
    creates the part centered at the origin. The part's relationship to the
    broader assembly is handled entirely by anchor_tags and MatingRules —
    the Machinist must NEVER embed assembly-level positioning logic.

    EXCEPTION — Coupled Geometric Subsystems (Domain D Aerospace):
    When a part represents a tightly coupled aerodynamic subsystem (e.g., a
    wing segment with internal ribs and spars), the Machinist is permitted —
    and REQUIRED — to generate the entire subsystem within a single self-
    contained script. This is necessary because internal structures depend on
    a shared tooling body (the ``inner_void`` — the directly-lofted airfoil
    core solid). Splitting such a subsystem across multiple PartDefinition
    entries makes it impossible to share that master geometry, causing ribs
    to clip through shell walls. In these cases the Machinist must produce a
    ``cq.Assembly`` containing all sub-bodies (skin, ribs, spars) and assign
    it to ``result``.
    """

    part_id: str = Field(
        ...,
        max_length=200,
        description=(
            "A unique, snake_case identifier for this part (e.g., 'base_plate', "
            "'motor_housing', 'm5_bolt'). This ID is referenced by MatingRules "
            "to connect parts in the assembly. Must be unique across the entire "
            "AssemblyManifest."
        ),
    )

    description: str = Field(
        ...,
        max_length=2000,
        description=(
            "A detailed physical description of the part that the Machinist "
            "subagent will use to write CadQuery code. Include ALL geometric "
            "details: dimensions (in millimeters), shape primitives (box, "
            "cylinder, etc.), features (holes, fillets, chamfers, slots), and "
            "material intent. Be explicit and unambiguous. "
            "Example: 'A 100mm x 60mm x 5mm rectangular base plate with four "
            "M5 through-holes at each corner, 8mm from each edge, and a 2mm "
            "fillet on all top edges.' "
            "DO NOT include any absolute (x, y, z) positioning relative to "
            "other parts. Each part is modeled at the origin in isolation."
        ),
    )

    anchor_tags: list[str] = Field(
        ...,
        description=(
            "A list of named anchors on this part where other parts can attach. "
            "Each anchor tag MUST be one of the following: "
            "(1) A valid CadQuery face/edge selector string such as '>Z' (top "
            "face), '<Z' (bottom face), '>X' (right face), '<X' (left face), "
            "'>Y' (front face), '<Y' (back face). "
            "(2) A descriptive named tag for a specific feature, e.g., "
            "'hole_center', 'slot_midpoint', 'flange_top'. The Machinist will "
            "use cq.Assembly().tag() to register these in the CadQuery script. "
            "CRITICAL: Anchor tags are TOPOLOGICAL REFERENCES, not coordinates. "
            "NEVER use tuples like '(10, 20, 0)' or any numeric coordinate as "
            "an anchor tag. The Assembler subagent uses these tags to mate "
            "parts together via CadQuery's constraint system."
        ),
    )


class MatingRule(BaseModel):
    """A constraint that connects two parts and specifies placement coordinates.

    MatingRules define both the logical connection between parts (via anchors)
    and the precise 3D translation for the Assembler to position each part.
    """

    source_part_id: str = Field(
        ...,
        description=(
            "The part_id of the first part in this mating pair. Must exactly "
            "match a part_id defined in the AssemblyManifest.parts list."
        ),
    )

    source_anchor: str = Field(
        ...,
        description=(
            "The anchor tag on the source part where the connection originates. "
            "Must exactly match one of the anchor_tags defined in the source "
            "part's PartDefinition. Example: '>Z'."
        ),
    )

    target_part_id: str = Field(
        ...,
        description=(
            "The part_id of the second part in this mating pair. Must exactly "
            "match a part_id defined in the AssemblyManifest.parts list. "
            "Must be different from source_part_id."
        ),
    )

    target_anchor: str = Field(
        ...,
        description=(
            "The anchor tag on the target part where the connection terminates. "
            "Must exactly match one of the anchor_tags defined in the target "
            "part's PartDefinition. Example: '<Z'."
        ),
    )

    mate_type: Optional[MateType] = Field(
        default=None,
        description=(
            "The kinematic constraint type. When provided, the Assembler uses "
            "cq.Constraint-based solving instead of absolute XYZ positioning. "
            "FASTENED = fully rigid (no relative motion). "
            "REVOLUTE = rotation about shared axis only. "
            "SLIDER = translation along shared axis only. "
            "CYLINDRICAL = rotation + translation along axis. "
            "PLANAR = translation in contact plane. "
            "BALL = free rotation about contact point. "
            "Prefer mate_type over translation for all new assemblies."
        ),
    )

    translation: Optional[str] = Field(
        default=None,
        description=(
            "DEPRECATED — use mate_type instead. "
            "Absolute 3D translation 'X, Y, Z' in mm for the target part. "
            "Used only when mate_type is not provided (backward compatibility). "
            "The base/first part is always at '0, 0, 0'."
        ),
    )

    clearance: float = Field(
        ...,
        description=(
            "Axial gap in millimeters between the source and target anchor "
            "faces, measured along the source anchor face normal. "
            "Use 0.0 for flush contact; small positive values separate the "
            "faces (e.g., 0.5). It is NOT a radial or diametral fit. Must be "
            "0.0 for BALL joints and for translation-only rules (an absolute "
            "translation cannot apply a gap)."
        ),
    )

    # T2-02: DoF range annotation for bounded kinematic constraints
    dof_min: Optional[float] = Field(
        default=None,
        description=(
            "Minimum range for the unconstrained degree of freedom. "
            "Degrees for REVOLUTE/CYLINDRICAL, mm for SLIDER/CYLINDRICAL. "
            "Example: 0.0 for a hinge that cannot close past flush. "
            "Leave None for unbounded joints (bearings, free sliders)."
        ),
    )

    dof_max: Optional[float] = Field(
        default=None,
        description=(
            "Maximum range for the unconstrained degree of freedom. "
            "Degrees for REVOLUTE/CYLINDRICAL, mm for SLIDER/CYLINDRICAL. "
            "Example: 90.0 for a hinge that opens 90°. "
            "Leave None for unbounded joints."
        ),
    )

    dof_unit: Optional[str] = Field(
        default=None,
        description=(
            "Unit for dof_min / dof_max. Must be 'deg' (rotational) or "
            "'mm' (translational). Required when dof_min or dof_max is set."
        ),
    )

    # T2-03: Mechanical coupling parameters for GEAR, SCREW, RACK_PINION
    coupling_ratio: Optional[float] = Field(
        default=None,
        description=(
            "Coupling ratio for mechanism-level mate types. "
            "GEAR: teeth_driven / teeth_driver (e.g., 2.0 = 2:1 reduction). "
            "SCREW: lead in mm per revolution (e.g., 1.5 for M1.5 pitch). "
            "RACK_PINION: mm of rack travel per radian of pinion rotation. "
            "Not applicable to kinematic constraint types (FASTENED, REVOLUTE, etc.)."
        ),
    )

    @field_validator("translation")
    @classmethod
    def _validate_translation_format(cls, v: Optional[str]) -> Optional[str]:
        """Enforce the 'X, Y, Z' contract the assembler relies on.

        Without this, a malformed translation ('10, 20', '1, a, 3', or any
        free string) passes validation and only blows up at assembly time
        with an IndexError / ValueError deep in run_assembler().
        """
        if v is None:
            return v
        parts = [p.strip() for p in v.split(",")]
        if len(parts) != 3:
            raise ValueError(
                f"translation must be 'X, Y, Z' (exactly 3 comma-separated "
                f"numbers in mm), got {v!r}"
            )
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            raise ValueError(
                f"translation components must all be numeric, got {v!r}"
            )
        if not all(math.isfinite(n) for n in nums):
            raise ValueError(
                f"translation components must be finite (no inf/nan), got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _require_mate_type_or_translation(self) -> "MatingRule":
        if self.mate_type is None and self.translation is None:
            raise ValueError(
                "MatingRule must have either 'mate_type' or 'translation'. "
                "Provide mate_type (preferred) for constraint-based assembly, "
                "or translation 'X, Y, Z' for legacy positioning."
            )
        if self.mate_type is not None and self.translation is not None:
            raise ValueError(
                "MatingRule has both 'mate_type' and an absolute 'translation'; "
                "a joint places the target, so the translation would be ignored. "
                "Provide only one."
            )
        c = self.clearance
        if not math.isfinite(c) or c < 0.0 or c > MAX_CLEARANCE_MM:
            raise ValueError(
                f"clearance must be a finite gap in [0, {MAX_CLEARANCE_MM:g}] mm, got {c!r}"
            )
        if c != 0.0 and self.mate_type is None:
            raise ValueError(
                "clearance must be 0 on a translation-only rule: the absolute "
                "translation fully places the part and no gap would be applied."
            )
        if c != 0.0 and self.mate_type == MateType.BALL:
            raise ValueError(
                "clearance must be 0 for a BALL joint: a ball/socket radial gap "
                "is not representable by planar anchor faces."
            )
        return self


# ---------------------------------------------------------------------------
# Phase 3: Aerodynamic analysis schemas (XFOIL pipeline integration)
# ---------------------------------------------------------------------------

class FlightEnvelopeSpec(BaseModel):
    """Flight conditions for XFOIL polar analysis.

    Mirrors the FlightEnvelope TypedDict in analysis/airfoil.py.  Populated
    by the REA from Domain D prompts when flight conditions are explicit
    (e.g. "cruise at Mach 0.3, Re=1.5e6"); missing fields fall back to
    analysis.airfoil._DEFAULT_ENV.
    """

    alpha_start: Optional[float] = Field(default=None, description="Angle of attack sweep start (deg).")
    alpha_end:   Optional[float] = Field(default=None, description="Angle of attack sweep end (deg).")
    alpha_step:  Optional[float] = Field(default=None, description="Angle of attack sweep step (deg).")
    reynolds:    Optional[float] = Field(default=None, description="Reynolds number based on chord.")
    mach:        Optional[float] = Field(default=None, description="Free-stream Mach number.")
    n_crit:      Optional[float] = Field(default=None, description="e^N transition criterion (9 = clean tunnel).")


class AirfoilSpec(BaseModel):
    """Airfoil identification needed to slice the wing STEP for XFOIL.

    Populated by the REA when the prompt names a NACA airfoil and/or wing
    dimensions. Used by analyze_wing_step to (a) cut the STEP at mid-span
    and (b) fall back to analytic NACA coordinates if the OCC slice fails.
    """

    naca_code: Optional[str] = Field(
        default=None,
        max_length=4,
        description="4-digit NACA code (e.g. '0012', '2412'). Used for label + fallback.",
    )
    chord_mm:      Optional[float] = Field(default=None, description="Representative chord length (mm).")
    span_mm:       Optional[float] = Field(default=None, description="Total wing span (mm). Mid-span is span_mm/2.")
    sweep_le_deg:  Optional[float] = Field(default=None, description="Leading-edge sweep angle (degrees). DA planform variants.")
    dihedral_deg:  Optional[float] = Field(default=None, description="Dihedral angle (degrees). Positive = tip raised upward.")
    root_pitch_deg: Optional[float] = Field(default=None, description="Geometric pitch angle at blade root (degrees). DB twisted blades.")
    tip_pitch_deg:  Optional[float] = Field(default=None, description="Geometric pitch angle at blade tip (degrees). DB twisted blades.")


# PropulsionSpec (Domain F) is the canonical aerospace model owned by the
# propulsion package and imported at the top of this module
# (`from propulsion.specs import PropulsionSpec`). It is no longer redefined
# here so the REA schema, classifier vocab, validators, and the Phase 2 sampler
# all share one source of truth.


class AircraftSpec(BaseModel):
    """Aircraft-level conditions for the 3-D aero (lifting-line) AND structural /
    flight-envelope stages (Domain D).

    RECONCILE NOTE (2026-06-08): single canonical merge of the two independently
    developed definitions (aero stack + structures stack). Rules:
      * every field Optional with a default — run_aero / run_struct are opt-in
        and the REA may not extract every field; the geometry / analysis stages
        guard at the use site, not at schema construction.
      * bounds are the INTERSECTION (tightest) of the two branches, so each
        branch's original rejection tests still hold.
      * fields are the UNION of both branches.
    Bounds still reject hallucinated REA outputs (the plan's REA risk).
    """

    mtow_kg:          Optional[float] = Field(default=None, gt=0.0, le=1.0e6,   description="Maximum take-off weight (mass), kg.")
    cruise_alt_m:     Optional[float] = Field(default=None, ge=0.0, le=20000.0, description="Cruise altitude, m (ISA model valid to ~20 km).")
    cruise_speed_ms:  Optional[float] = Field(default=None, gt=0.0, le=400.0,   description="Cruise true airspeed, m/s.")
    category:         Optional[str]   = Field(default=None, max_length=16,      description="FAR-23 category: 'normal' | 'utility' | 'acrobatic'.")
    cl_max:           Optional[float] = Field(default=None, gt=0.0, le=3.5,     description="Maximum lift coefficient (analysis default 1.5).")
    lift_curve_slope: Optional[float] = Field(default=None, gt=0.0, lt=20.0,    description="3-D wing CLα per radian (default 5.7).")
    gust_velocity_ms: Optional[float] = Field(default=None, ge=0.0, lt=200.0,   description="Discrete gust velocity, m/s (default 15.24 = 50 fps).")
    vne_factor:       Optional[float] = Field(default=None, gt=1.0, lt=3.0,     description="V_NE / V_cruise ratio (default 1.4).")


class WingBoxSpec(BaseModel):
    """Wing-box spec — canonical union of the structures (analysis section
    sizing) and aero (deterministic geometry layout) definitions (Domain D).

    RECONCILE NOTE (2026-06-08): merged from two independent definitions. All
    fields Optional (each consumer sets only its own subset); the spar-cap /
    web-thickness fields feed the bending/shear margin analysis, the spar-
    fraction / rib-count / skin fields drive the cad_aero builders. The
    _front_before_rear invariant is preserved but None-guarded so structures-
    style construction (no spar fractions) does not raise.

    spar_web_thickness_m (analysis) and web_thickness_m (geometry builder) are
    intentionally distinct fields for distinct consumers — not a duplication.
    """

    # structures (analysis section sizing) inputs
    chord_root_m:         Optional[float] = Field(default=None, gt=0.0, lt=100.0, description="Chord at the analysed station, m.")
    thickness_to_chord:   Optional[float] = Field(default=None, gt=0.0, lt=1.0,   description="Section thickness ratio (t/c).")
    cap_area_m2:          Optional[float] = Field(default=None, gt=0.0, lt=1.0,   description="Area of ONE spar cap, m².")
    spar_web_thickness_m: Optional[float] = Field(default=None, gt=0.0, lt=1.0,   description="Shear-web thickness, m (structures analysis).")
    n_spars:              Optional[int]   = Field(default=None, ge=1,   le=10,    description="Number of shear webs (default 2).")
    # aero (deterministic geometry layout) inputs
    front_spar_x_frac:    Optional[float] = Field(default=None, ge=0.0, le=1.0,   description="Front-spar chordwise fraction x/c (geometry).")
    rear_spar_x_frac:     Optional[float] = Field(default=None, ge=0.0, le=1.0,   description="Rear-spar chordwise fraction x/c (geometry).")
    n_ribs:               Optional[int]   = Field(default=None, ge=2,   le=50,    description="Rib count (geometry).")
    web_thickness_m:      Optional[float] = Field(default=None, gt=0.0, le=0.1,   description="Web thickness for the cad_aero builder.")
    skin_thickness_m:     Optional[float] = Field(default=None, gt=0.0, le=0.1,   description="Skin panel thickness for the cad_aero builder.")

    @model_validator(mode="after")
    def _front_before_rear(self) -> "WingBoxSpec":
        # None-guarded: structures-style construction omits the spar fractions.
        if (self.front_spar_x_frac is not None
                and self.rear_spar_x_frac is not None
                and self.front_spar_x_frac >= self.rear_spar_x_frac):
            raise ValueError(
                f"front_spar_x_frac ({self.front_spar_x_frac}) must be forward of "
                f"rear_spar_x_frac ({self.rear_spar_x_frac})"
            )
        return self


class Wing3DAeroReport(BaseModel):
    """Whole-wing (3-D) aerodynamic result from the lifting-line or AVL stage.

    Distinct from PolarReport (2-D sectional XFOIL): this carries finite-span
    CL/CDi/span-efficiency. `source` records which solver produced it.
    """

    CL_3D: float
    CDi: float = Field(..., ge=0.0, description="Induced drag coefficient.")
    span_efficiency_e: float = Field(..., gt=0.0, le=1.05)
    source: Literal["lifting_line", "avl"]
    Cl_local: Optional[list[float]] = Field(default=None, description="Local section Cl per station.")
    L_distribution: Optional[list[float]] = Field(default=None, description="Lift per unit span [N/m].")


class PolarReport(BaseModel):
    """Aerodynamic polar result delivered alongside the GLB on /generate.

    Mirrors the PolarResult TypedDict in analysis/airfoil.py.  Carries the
    full alpha/Cl/Cd/Cm/L_D arrays so the frontend can plot the polar.
    """

    naca_code:       str
    chord_mm:        float
    reynolds:        float
    mach:            float
    n_crit:          float
    alpha:           list[float]
    Cl:              list[float]
    Cd:              list[float]
    Cm:              list[float]
    L_D:             list[float]
    converged_count: int
    stall_alpha:     Optional[float] = None
    Cl_max:          Optional[float] = None


# ---------------------------------------------------------------------------
# Phase 4: Structural & flight-envelope analysis schemas
# (mirror the TypedDicts in analysis/structures.py)
# ---------------------------------------------------------------------------

class WingSpec(BaseModel):
    """Reference wing planform inputs needed by the structural envelope."""

    area_m2:      Optional[float] = Field(default=None, gt=0.0, lt=10000.0, description="Reference wing area S, m².")
    span_m:       Optional[float] = Field(default=None, gt=0.0, lt=500.0,   description="Full tip-to-tip wing span, m.")
    mean_chord_m: Optional[float] = Field(default=None, gt=0.0, lt=50.0,    description="Mean aerodynamic/geometric chord; defaults to S/b when absent.")


class StructuralReport(BaseModel):
    """Wing structural + flight-envelope result delivered alongside the GLB
    on /generate.  Flattened version of the StructuralReport TypedDict in
    analysis/structures.py so the SSE payload is one level deep.
    """

    # Cruise condition (derived from ISA + lift = weight)
    altitude_m:          float
    density_kgm3:        float
    dynamic_pressure_pa: float
    cl_cruise:           float
    wing_loading_pa:     float

    # Flight envelope
    category:        str
    n_maneuver_pos:  float
    n_maneuver_neg:  float
    n_gust_pos:      float
    n_gust_neg:      float
    n_limit_pos:     float
    n_limit_neg:     float
    n_ultimate_pos:  float
    n_ultimate_neg:  float
    v_stall_ms:      float
    v_corner_ms:     float
    v_ne_ms:         float

    # Ultimate root loads + margins
    root_bending_moment_nm:   float
    root_shear_force_n:       float
    bending_stress_pa:        float
    bending_allowable_pa:     float
    bending_margin_of_safety: float
    bending_passed:           bool
    shear_stress_pa:          float
    shear_allowable_pa:       float
    shear_margin_of_safety:   float
    shear_passed:             bool

    # Evidence status (disambiguates a null/inf margin: "zero_load" is a
    # genuine zero load; "not_evaluated" is missing evidence) + provenance.
    bending_status:        str = "evaluated"
    shear_status:          str = "evaluated"
    governing_load_case:   str = "positive_ultimate"
    method:                str = "analytic_sizing_estimate"
    engineering_certified: bool = False

    material:  str
    passed:    bool
    warnings:  list[str]


class AssemblyManifest(BaseModel):
    """The complete blueprint for a multi-part mechanical assembly.

    This is the output of the Draftsman (Planner) subagent and the input to
    the orchestration pipeline. It defines WHAT to build (parts) and HOW to
    connect them (mating_rules).

    The manifest is the single source of truth for the entire assembly. Every
    downstream subagent receives only its relevant slice of this manifest:
    - The Machinist gets one PartDefinition at a time.
    - The Assembler gets all validated part scripts + the mating_rules.

    ANTI-HALLUCINATION CONTRACT:
    - Parts must be self-contained and origin-centered.
    - All inter-part spatial relationships are encoded ONLY in mating_rules.
    - No absolute coordinates anywhere in this manifest.
    """

    assembly_name: str = Field(
        ...,
        max_length=200,
        description=(
            "A descriptive, snake_case name for the overall assembly "
            "(e.g., 'flanged_bearing_mount', 'gearbox_housing'). Used as the "
            "filename stem for the exported .glb file."
        ),
    )

    parts: list[PartDefinition] = Field(
        ...,
        description=(
            "An ordered list of all individual parts in the assembly. Each part "
            "will be manufactured independently by the Machinist subagent. "
            "Every part_id must be unique. List parts in logical build order "
            "(base/foundation first, fasteners last)."
        ),
    )

    mating_rules: list[MatingRule] = Field(
        ...,
        description=(
            "The list of constraints that define how parts connect to each "
            "other. Every mating rule references exactly two parts by their "
            "part_id and specifies which anchor_tags to mate. The Assembler "
            "subagent translates these into CadQuery constraint calls. "
            "RULES: "
            "(1) All referenced part_ids must exist in the parts list. "
            "(2) All referenced anchors must exist in the respective part's "
            "anchor_tags. "
            "(3) The assembly graph must be connected — no orphan parts. "
            "(4) NEVER encode absolute positions. Positioning is emergent "
            "from the chain of relative constraints."
        ),
    )

    aero_report: Optional[PolarReport] = Field(
        default=None,
        description=(
            "Aerodynamic polar attached after export when /generate was "
            "invoked with run_aero=True on a Domain D assembly. None when "
            "aero analysis was not requested, the assembly is not a wing, "
            "or XFOIL was unavailable / failed (see SSE 'aero' event detail)."
        ),
    )

    wing_aero_report: Optional[Wing3DAeroReport] = Field(
        default=None,
        description=(
            "Whole-wing 3-D aero (lifting-line baseline or AVL) attached after "
            "export on a Domain D wing assembly when run_aero=True. Distinct from "
            "the 2-D sectional aero_report. None when not a finite wing or the 3-D "
            "stage was unavailable."
        ),
    )

    @model_validator(mode="after")
    def _validate_manifest(self) -> "AssemblyManifest":  # noqa: C901
        # --- Size limits (not expressible in Gemini's schema) ---
        if len(self.parts) > 50:
            raise ValueError(
                f"Too many parts ({len(self.parts)}). Maximum is 50."
            )
        if len(self.mating_rules) > 100:
            raise ValueError(
                f"Too many mating rules ({len(self.mating_rules)}). Maximum is 100."
            )
        for part in self.parts:
            if len(part.anchor_tags) > 20:
                raise ValueError(
                    f"Part '{part.part_id}' has {len(part.anchor_tags)} "
                    f"anchor tags. Maximum is 20."
                )

        # --- Identity: non-empty, unique raw ids, unique generated names ---
        if not self.parts:
            raise ValueError("AssemblyManifest requires at least one part.")
        safe_names: dict[str, str] = {}
        for part in self.parts:
            safe = sanitize_part_id(part.part_id)
            if safe in safe_names:
                other = safe_names[safe]
                if other == part.part_id:
                    raise ValueError(f"Duplicate part_id {part.part_id!r}; every part_id must be unique.")
                raise ValueError(
                    f"part_ids {other!r} and {part.part_id!r} collide as assembly "
                    f"name {safe!r}; rename one of them."
                )
            safe_names[safe] = part.part_id

        # --- Referential integrity ---
        valid_ids = {p.part_id for p in self.parts}
        for rule in self.mating_rules:
            if rule.source_part_id not in valid_ids:
                raise ValueError(
                    f"Mating rule references undefined part: {rule.source_part_id}"
                )
            if rule.target_part_id not in valid_ids:
                raise ValueError(
                    f"Mating rule references undefined part: {rule.target_part_id}"
                )

        # --- Self-reference and anchor resolution ---
        declared = {p.part_id: set(p.anchor_tags) for p in self.parts}
        for rule in self.mating_rules:
            if rule.source_part_id == rule.target_part_id:
                # Only the legacy absolute self-placement ('0, 0, 0' for a
                # single part) is meaningful; a part cannot form a joint
                # with itself. Its anchors are not used.
                if rule.mate_type is not None:
                    raise ValueError(
                        f"Self-referencing mating rule on {rule.source_part_id!r} cannot "
                        f"carry mate_type {rule.mate_type.value}; only a translation-only "
                        "self-placement is allowed."
                    )
                continue
            for pid, anchor in ((rule.source_part_id, rule.source_anchor),
                                (rule.target_part_id, rule.target_anchor)):
                native = NATIVE_FACE_SELECTOR_RE.fullmatch(anchor) is not None
                if rule.mate_type is not None and not native:
                    raise ValueError(
                        f"Unsupported anchor {anchor!r} on part {pid!r}: a mate_type joint "
                        "needs a planar face selector such as '>Z' or '<X' (optionally "
                        "indexed, '>Z[-2]'); named anchors are not resolvable on the "
                        "imported part geometry."
                    )
                if not native and anchor not in declared[pid]:
                    raise ValueError(
                        f"Anchor {anchor!r} is not declared in part {pid!r} anchor_tags "
                        "and is not a supported face selector."
                    )

        # --- Placement / connectivity policy (multi-part only) ---
        # parts[0] is the grounded base. A part is placed either by a chain of
        # mate joints from exactly one placed root (the base, or a part with
        # an explicit translation), or by its own explicit translation. A part
        # with neither would silently land at the origin.
        if len(self.parts) > 1:
            ground = self.parts[0].part_id
            translations: dict[str, str] = {}
            for rule in self.mating_rules:
                if rule.mate_type is not None:
                    continue
                pid = rule.target_part_id
                xyz = tuple(float(v) for v in rule.translation.split(","))
                if pid in translations and translations[pid] != xyz:
                    raise ValueError(
                        f"Part {pid!r} has conflicting placement: two different "
                        "absolute translations."
                    )
                translations[pid] = xyz
            parent = {p.part_id: p.part_id for p in self.parts}

            def find(pid: str) -> str:
                while parent[pid] != pid:
                    parent[pid] = parent[parent[pid]]
                    pid = parent[pid]
                return pid

            mated: set[str] = set()
            for rule in self.mating_rules:
                if rule.mate_type is None or rule.source_part_id == rule.target_part_id:
                    continue
                mated.update((rule.source_part_id, rule.target_part_id))
                parent[find(rule.source_part_id)] = find(rule.target_part_id)
            components: dict[str, list[str]] = {}
            for part in self.parts:
                if part.part_id in mated:
                    components.setdefault(find(part.part_id), []).append(part.part_id)
            for members in components.values():
                roots = [m for m in members if m == ground or m in translations]
                if not roots:
                    raise ValueError(
                        f"Parts {sorted(members)} are not connected by mate joints to the "
                        f"grounded base part {ground!r} or to an explicitly placed part."
                    )
                if len(roots) > 1:
                    extra = [m for m in roots if m != ground] if ground in roots else roots[1:]
                    raise ValueError(
                        f"Part {extra[0]!r} has conflicting placement: it is positioned by "
                        "mate joints and also by an absolute translation."
                    )
            for part in self.parts:
                pid = part.part_id
                if pid not in mated and pid != ground and pid not in translations:
                    raise ValueError(
                        f"Part {pid!r} has no placement: no mate joint path to the grounded "
                        f"base part {ground!r} and no explicit translation."
                    )
        return self


# ---------------------------------------------------------------------------
# T2-12: Requirements Engineering Agent (REA) schema
# ---------------------------------------------------------------------------

class DesignRequirements(BaseModel):
    """Structured requirements extracted from a user's design brief.

    The REA parses the raw prompt into this document before it reaches
    the Planner. The Planner receives both the raw prompt AND this
    structured document, treating the requirements as the authoritative spec.
    """

    primary_function: str = Field(
        ...,
        description=(
            "One sentence describing what the assembly or part must do. "
            "Example: 'A rotating joint to connect two structural arms with 90° range of motion.'"
        ),
    )

    key_dimensions: Optional[dict[str, float]] = Field(
        default=None,
        description=(
            "Map of dimension name → value in mm. Only include dimensions "
            "explicitly stated or clearly implied by the prompt. "
            "Example: {'shaft_diameter_mm': 20.0, 'total_length_mm': 150.0}"
        ),
    )

    material_class: Optional[str] = Field(
        default=None,
        description=(
            "Material family inferred from context. "
            "One of: aluminum, steel, stainless_steel, plastic, carbon_fiber, "
            "titanium, brass, ceramic, rubber, or None if unspecified."
        ),
    )

    environment: Optional[str] = Field(
        default=None,
        description=(
            "Operating environment. "
            "One of: standard, outdoor, corrosive, high_temp, cryogenic, "
            "vacuum, underwater, or None if unspecified."
        ),
    )

    connecting_interfaces: list[str] = Field(
        default_factory=list,
        description=(
            "Named interfaces or features that mate with external parts. "
            "Example: ['M8 through bolt holes on flange', 'keyed bore for 20mm shaft']"
        ),
    )

    production_volume: Optional[str] = Field(
        default=None,
        description=(
            "Expected production scale. "
            "One of: prototype, low_volume, production, or None if unspecified."
        ),
    )

    inferred_domain: Optional[str] = Field(
        default=None,
        description=(
            "Predicted geometric domain. "
            "A = structural/prismatic, B = mechanical/parametric, "
            "C = organic/ergonomic, D = aerospace/aerodynamic."
        ),
    )

    confidence: float = Field(
        default=1.0,
        description=(
            "REA confidence score 0–1. Below 0.5 means the prompt is too vague "
            "to extract reliable requirements — the Clarifier should be invoked."
        ),
    )

    flight_env: Optional[FlightEnvelopeSpec] = Field(
        default=None,
        description=(
            "Domain D only: flight conditions extracted from the prompt for "
            "downstream XFOIL analysis. Populate only when the prompt names "
            "explicit conditions (Reynolds, Mach, AoA range, N_crit). All "
            "fields are optional — missing fields fall back to airfoil-module "
            "defaults at analysis time."
        ),
    )

    airfoil_spec: Optional[AirfoilSpec] = Field(
        default=None,
        description=(
            "Domain D only: airfoil identification needed to slice the wing "
            "STEP and label the polar (NACA code, chord, span, sweep, dihedral, "
            "pitch angles for twisted blades). Populate when the prompt names "
            "a NACA airfoil or wing dimensions."
        ),
    )

    propulsion_spec: Optional[PropulsionSpec] = Field(
        default=None,
        description=(
            "Domain F only: propulsion geometry parameters extracted from the prompt "
            "(nozzle throat/exit diameters, chamber dimensions, fuel inlet count, "
            "nacelle highlight radius, etc.). Populate when inferred_domain is 'F'."
        ),
    )

    aircraft_spec: Optional[AircraftSpec] = Field(
        default=None,
        description=(
            "Domain D only: aircraft-level conditions (MTOW, cruise altitude/speed, "
            "FAR-23 category, optional CL_max / lift-curve slope / gust velocity / "
            "V_NE factor) for the 3-D lifting-line AND structural / flight-envelope "
            "stages. Populate when the prompt names a weight and cruise condition."
        ),
    )

    wing_spec: Optional[WingSpec] = Field(
        default=None,
        description=(
            "Domain D only: reference wing planform (area, span, mean chord) for the "
            "structural envelope. When absent the structural stage tries to derive "
            "area from airfoil_spec.chord_mm × span_mm and skips if neither is set."
        ),
    )

    wing_box_spec: Optional[WingBoxSpec] = Field(
        default=None,
        description=(
            "Domain D only: wing-box section sizing (chord at station, t/c, spar-cap "
            "area, web thickness, n_spars) for the bending / shear margin analysis, "
            "AND/OR deterministic geometry layout (front/rear spar x/c, rib count, "
            "web/skin thickness) driving the cad_aero builders. Populate when the "
            "prompt describes a structural wing (spars, ribs)."
        ),
    )


# ---------------------------------------------------------------------------
# T2-08: Multi-turn refinement schemas
# ---------------------------------------------------------------------------

class ManifestDiff(BaseModel):
    """Differential update to an existing AssemblyManifest.

    The RefinerAgent emits only the changes needed — unchanged parts are
    omitted. The orchestrator merges this diff with the original manifest
    before re-running affected Machinists.
    """

    modified_parts: list[str] = Field(
        default_factory=list,
        description=(
            "part_ids of parts whose description has changed and must be "
            "re-generated by the Machinist. Scripts for all other parts "
            "are reused from the original run."
        ),
    )

    added_parts: list[PartDefinition] = Field(
        default_factory=list,
        description="New PartDefinition entries to add to the assembly.",
    )

    removed_parts: list[str] = Field(
        default_factory=list,
        description="part_ids to remove from the assembly and manifest.",
    )

    modified_mates: list[MatingRule] = Field(
        default_factory=list,
        description=(
            "Updated MatingRule entries. Rules are matched by "
            "(source_part_id, target_part_id) pair. New rules are appended; "
            "existing rules with matching IDs are replaced."
        ),
    )

    updated_descriptions: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of part_id -> updated description for modified parts. "
            "The Machinist uses the updated description; the part_id is unchanged."
        ),
    )


# ---------------------------------------------------------------------------
# T3-08: Product and Manufacturing Information (PMI) Annotations
# ---------------------------------------------------------------------------

class PMIAnnotation(BaseModel):
    """A single Product and Manufacturing Information annotation on a B-rep face.

    PMI annotations encode tolerances, surface finish callouts, and GD&T
    symbols on specific faces of a generated part. They are attached to
    OCCT B-rep faces using XDE/XCAF attributes and included in STEP export
    (ISO 10303-242 AP242 PMI representation).

    Use case: Expert-level prompts that specify tolerances (e.g., "H7 fit
    for a 608 bearing", "Ra 0.8 surface finish on the sealing face") should
    produce STEP files with corresponding PMI annotations — making the output
    manufacture-ready rather than just geometrically correct.
    """

    face_selector: str = Field(
        ...,
        description=(
            "CadQuery face selector identifying the annotated face. "
            "Examples: '>Z' (top face), '<Z' (bottom face), "
            "'inner_bore_face' (named tag), '>X' (rightmost face). "
            "Must match one of the anchor_tags defined in the PartDefinition."
        ),
    )

    tolerance_type: str = Field(
        ...,
        description=(
            "Type of PMI annotation. Must be one of: "
            "'linear' (dimensional tolerance, e.g., ±0.05), "
            "'angular' (angle tolerance, e.g., ±0.5°), "
            "'surface_finish' (Ra/Rz surface roughness, e.g., Ra 0.8), "
            "'gdt' (GD&T symbol, e.g., cylindricity, flatness, position). "
        ),
    )

    value: str = Field(
        ...,
        description=(
            "The annotation value as a string. Examples: "
            "'H7' (ISO hole fit tolerance), "
            "'±0.05' (bilateral linear tolerance in mm), "
            "'Ra 0.8' (surface roughness in µm), "
            "'⊙ ⌀0.1 A B' (GD&T position callout), "
            "'f7' (ISO shaft fit tolerance), "
            "'IT6' (ISO tolerance grade)."
        ),
    )

    reference_dimension: Optional[float] = Field(
        default=None,
        description=(
            "The nominal dimension in mm that this tolerance applies to. "
            "Required for linear and angular tolerances. "
            "Example: 20.0 for a 20mm bore with H7 tolerance."
        ),
    )

    datum_labels: list[str] = Field(
        default_factory=list,
        description=(
            "Datum reference labels for GD&T annotations. "
            "Example: ['A', 'B'] for a position callout referencing datums A and B."
        ),
    )


class PartPMI(BaseModel):
    """Collection of PMI annotations for a single part.

    Emitted by the Machinist for Expert-level prompts that specify tolerances.
    The Assembler/postprocessor attaches these to OCCT B-rep faces during
    STEP export using STEPCAFControl_Writer.
    """

    part_id: str = Field(
        ...,
        description="Must match a part_id in the AssemblyManifest.",
    )

    annotations: list[PMIAnnotation] = Field(
        default_factory=list,
        description="List of PMI annotations for this part's faces.",
    )

    general_tolerance: Optional[str] = Field(
        default=None,
        description=(
            "General tolerance class for all unspecified dimensions. "
            "Example: 'ISO 2768-m' (medium class), 'ISO 2768-f' (fine class)."
        ),
    )

    material_spec: Optional[str] = Field(
        default=None,
        description=(
            "Material specification string. "
            "Example: 'Al 6061-T6', 'SS 316L', 'AISI 4140'."
        ),
    )


class PMIGenerationRequest(BaseModel):
    """Request to extract PMI from a prompt and attach to an existing part."""

    part_id: str
    part_description: str
    original_prompt: str
    step_path: Optional[str] = None


class RefinementRequest(BaseModel):
    """Request body for the /refine endpoint."""

    original_manifest: AssemblyManifest = Field(
        ...,
        description="The AssemblyManifest from the original /generate run.",
    )

    original_scripts: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of part_id -> CadQuery Python script from the original run. "
            "Scripts for unchanged parts are reused directly."
        ),
    )

    refinement_prompt: str = Field(
        ...,
        max_length=2000,
        description=(
            "Natural-language description of the desired change. "
            "Examples: 'make the shaft 20% longer', 'add a chamfer to the "
            "top flange', 'replace the ball joint with a revolute joint'."
        ),
    )

    session_id: Optional[str] = Field(
        default=None,
        description="Session identifier for telemetry grouping of refinement chains.",
    )
