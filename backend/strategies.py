"""
Domain-Specific CadQuery Generation Strategies.

Each strategy provides a tailored system prompt that guides the Machinist
LLM toward the correct CadQuery API patterns for the classified geometric
domain. This prevents the one-size-fits-all primitive approach that fails
on complex geometry.

Rules are stored as modular blocks in RULE_BLOCKS and assembled dynamically
by get_strategy() based on domain classification and keyword matching on
the part description. This eliminates injecting irrelevant rules that
waste context and confuse the model.
"""

# --------------------------------------------------------------------------
# Modular rule blocks — each is a self-contained instruction unit
# --------------------------------------------------------------------------
FEATURE_VOCABULARY = (
    "ENGINEERING FEATURE VOCABULARY — use these terms when interpreting prompts "
    "and generating part descriptions. Prefer these terms over purely spatial language.\n"
    "  - Boss: raised cylindrical protrusion (positive material feature)\n"
    "  - Pocket: recessed cavity (negative material feature, open top)\n"
    "  - Bore: through or blind cylindrical hole\n"
    "  - Counterbore: stepped bore — larger diameter entry + smaller diameter depth\n"
    "  - Countersink: conical chamfered bore entry (for flat-head screws)\n"
    "  - Flange: flat rim extending from a cylindrical feature for mating\n"
    "  - Web: thin connecting wall between structural ribs\n"
    "  - Gusset: triangular reinforcing feature at a corner joint\n"
    "  - Rib: thin plate projecting from a surface for stiffness\n"
    "  - Thread boss: cylindrical protrusion with internal or external thread\n"
    "  - Shell: thin-walled hollow version of a solid (uniform wall thickness)\n"
    "  - Keyway: rectangular slot in a shaft or hub for a key\n"
    "  - Spline: multi-lobe shaft feature for torque transmission\n"
    "  - Chamfer: angled cut on an edge to remove the sharp corner\n"
    "  - Fillet: rounded transition between two surfaces\n"
    "  - Gudgeon pin: wrist pin connecting piston to connecting rod\n"
    "  - Lands: raised cylindrical bands on a shaft for bearing contact\n"
)

RULE_BLOCKS = {
    # ── Core universal rules (always injected) ────────────────────────
    "rule_imports": (
        "(1) Import cadquery as cq at the top of the script. "
    ),
    "rule_result_var": (
        "(2) The script must define a variable named 'result' containing "
        "the final cq.Workplane object. "
    ),
    "rule_origin": (
        "(3) Model the part centered at the origin. "
    ),
    "rule_raw_python": (
        "(4) Return ONLY raw executable Python. No markdown, no code fences, "
        "no comments. "
    ),
    "rule_3d_solid": (
        "(5) You MUST create a 3D solid with real volume. NEVER return a flat "
        "2D sketch or an unextruded workplane. "
    ),
    "rule_no_hallucinated_methods": (
        "(6) NEVER invent CadQuery methods. The following DO NOT EXIST as "
        "built-in Workplane methods: rotate_about_origin(), revolve_about(), "
        "sweep_along(), make_gear(), make_thread(). For gears and threads, use "
        "the cad_utils library helpers (make_involute_spur_gear, "
        "make_metric_thread, make_hex_bolt, make_hex_nut) described in the "
        "domain rules below. "
        "Workplane.shell() takes ONE positional argument (thickness) — it does "
        "NOT accept a faces= kwarg. Faces are selected BEFORE the call: "
        ".faces('>Z').shell(thickness). See rule_shell_api for the full pattern."
    ),
    "rule_shell_api": (
        "SHELL OPERATION — exact CadQuery API. Workplane.shell(thickness) takes "
        "ONE positional argument: wall thickness (float). Positive thickness "
        "grows outward, negative thickness hollows inward. Faces to leave open "
        "are selected on the workplane BEFORE .shell() is called — they are "
        "NEVER passed as a keyword argument. "
        "\n"
        "  CORRECT:   solid.faces('>Z').shell(-2.0)         # hollow box, top open\n"
        "  CORRECT:   solid.faces('>Z').shell(2.0)          # +2mm wall outward, top open\n"
        "  WRONG:     solid.shell(2.0, faces=...)           # 'faces' is NOT a parameter\n"
        "  WRONG:     solid.shell(2.0, thickness=2.0)       # no kwargs accepted\n"
        "\n"
        "Canonical pattern for a hollow box with an open top:\n"
        "  result = cq.Workplane('XY').box(L, W, H).faces('>Z').shell(-wall_thickness)\n"
        "If multiple faces should be open, chain selectors before .shell(): "
        "solid.faces('>Z or <Z').shell(-t). Wall thickness must be much smaller "
        "than the smallest dimension; otherwise OCCT raises StdFail_NotDone."
    ),
    "rule_face_selectors": (
        "(7) Only use standard face selectors: '>Z', '<Z', '>X', '<X', '>Y', '<Y'. "
        "NEVER invent face names. "
    ),
    "rule_no_part_tagging": (
        "(8) DO NOT CALL .tag() IN YOUR SCRIPT. The anchor_tags declared in "
        "the PartDefinition are CONTRACT METADATA for the downstream assembler — "
        "they are NOT something your script has to implement. The assembler uses "
        "standard directional selectors ('>Z', '<Z', '>X', '<X', '>Y', '<Y') "
        "directly on the imported .step and does not read any tags from your "
        "script. Do NOT call result.tag(...), Workplane.tag(...), or any variant. "
        "Do NOT reference cq.selectors.FaceByAll, cq.selectors.PlaneSelector, or "
        "other non-existent selectors. Your job is ONLY to assign a valid "
        "cq.Workplane solid to `result`."
    ),

    # ── Conditional shared rules (keyword-triggered) ──────────────────
    "rule_polar_array": (
        "POLAR ARRAY RULE (MANDATORY for circular patterns): "
        "When the description mentions 'bolt circle', 'holes evenly spaced in a circle', "
        "'circular pattern', 'radial pattern', 'equally spaced around', or any N features "
        "arranged in a ring, you MUST use CadQuery's .polarArray() method. "
        "Do NOT manually compute positions with sin/cos loops for circular patterns. "
        "Pattern: .faces('>Z').workplane().polarArray(radius=R, startAngle=0, angle=360, count=N).hole(D) "
        "where R is the bolt circle radius (typically 60-75% of the part outer radius), "
        "N is the feature count, and D is the hole diameter. "
    ),
    "rule_loft_chaining": (
        "STRICT LOFT CHAINING (MANDATORY): "
        "You are FORBIDDEN from passing shape variables into .loft(). "
        "All lofts MUST be chained on a single continuous cq.Workplane stack. "
        "CORRECT: cq.Workplane('XY').rect(10,10).workplane(offset=10).circle(5).loft() "
        "WRONG: shape1 = wp.rect(10,10); shape2 = wp2.circle(5); shape1.loft(shape2) "
    ),
    "rule_no_2d_methods": (
        "NO HALLUCINATED 2D METHODS: "
        "The methods .fillet2D() and .chamfer2D() DO NOT EXIST in CadQuery. "
        "NEVER use them. To round or chamfer edges: create the 3D solid first "
        "(via .extrude() or .loft()), then apply .edges('|Z').fillet(r) or "
        ".edges('|Z').chamfer(r) on the solid. "
    ),
    "rule_safe_filleting": (
        "SAFE FILLETING (prevent BRep crashes): "
        "NEVER hardcode large fillet radii. Large radii on thin walls crash "
        "OpenCASCADE with StdFail_NotDone. When the prompt asks for smooth/ergonomic "
        "edges without specifying an exact radius, default to 1.0 or 2.0. "
        "RULE: fillet radius MUST be less than half of the thinnest wall dimension. "
    ),
    "rule_face_reselection": (
        "EXPLICIT FACE RE-SELECTION AFTER BOOLEANS: "
        "After any .cut() or .union(), the workplane context stack may reference "
        "a stale face. You MUST explicitly re-select a face before starting a new "
        "sketch. Pattern: part = base.cut(pocket); "
        "part = part.faces('>Z').workplane().circle(5).extrude(10). "
        "NEVER start a new sketch directly on the result of a boolean. "
    ),
    "rule_text_api": (
        ".text() API (MANDATORY): "
        "CadQuery's .text(txt, fontsize, distance) accepts ONLY these keyword "
        "arguments: combine (bool), font (str). The keyword 'cut' DOES NOT EXIST "
        "on .text(). NEVER use cut=True or cut=False with .text(). "
        "To add raised text: .text('hello', 8, 2) — default combine=True adds it. "
        "To cut engraved text: .text('hello', 8, -2) — use negative distance."
    ),

    # ── Feature vocabulary rule (injected into A + B) ─────────────────
    "rule_feature_vocabulary": FEATURE_VOCABULARY,

    # ── Domain A: Primitives / Structural ─────────────────────────────
    "rule_domain_a": (
        "DOMAIN A STRATEGY — Extrusions + Booleans: "
        "(A1) Start with a primary primitive: .box(L, W, H) or .cylinder(H, R). "
        "Choose based on the dominant shape in the description. "
        "(A2) Add features using boolean operations: "
        "  - Holes: .faces('>Z').workplane().center(x, y).circle(r).cutThruAll() "
        "  - Slots: .faces('>Z').workplane().slot2D(length, width).cutThruAll() "
        "  - Pockets: .faces('>Z').workplane().rect(w, h).cutBlind(-depth) "
        "(A3) For L-brackets or bent parts: create two boxes and .union() them. "
        "(A4) Apply fillets/chamfers last: .edges('|Z').fillet(r) "
        "(A5) For circular mounting patterns (bolt circles, evenly spaced holes), "
        "use .polarArray(): "
        ".faces('>Z').workplane().polarArray(radius=R, startAngle=0, angle=360, count=N).hole(D) "
        "where R is the bolt circle radius (60-75% of the part radius). "
        "For non-circular patterns (grid, rectangular), use .center(x,y) offsets. "
        "(A6) Keep it simple. If the part can be made with one .box() + a few "
        "cuts, do that. Do not over-engineer."
    ),

    # ── Domain B: Mechanical / Parametric ─────────────────────────────
    "rule_domain_b": (
        "DOMAIN B STRATEGY — Parametric Math + Library Functions: "
        "\n"
        "GEAR RULE (MANDATORY — USE THE LIBRARY): "
        "For ANY gear (spur gear, pinion, bevel gear approximation, etc.), "
        "you MUST use the pre-built utility function: "
        "  from cad_utils import make_involute_spur_gear "
        "  result = make_involute_spur_gear( "
        "      num_teeth=20, "
        "      module=2.0, "
        "      pressure_angle_deg=20.0, "
        "      thickness=10.0, "
        "      bore_diameter=8.0, "
        "      pitch_diameter=None  # overrides module if set "
        "  ) "
        "Do NOT write gear tooth math yourself. EVER. "
        "\n"
        "(B1) For BEARINGS: Create concentric cylinders (outer race, inner race) "
        "with .circle(r_outer).circle(r_inner).extrude(width). Add ball pockets "
        "using a sin/cos loop with .cut(). "
        "(B2) For PULLEYS/WHEELS: Use .circle(r).extrude(w), then cut a V-groove "
        "or flat groove using .revolve() on a trapezoidal profile, or boolean cut "
        "a torus-shaped ring. "
        "(B3) For THREADED CYLINDERS (studs, threaded rods, external threads on "
        "a custom shaft), USE the pre-built library helper: "
        "  from cad_utils import make_metric_thread "
        "  result = make_metric_thread(diameter=10.0, pitch=1.5, length=40.0) "
        "This returns a stable ribbed-cylinder approximation centered at the "
        "origin. NEVER attempt helical sweeps — they crash the BRep kernel. "
        "NEVER approximate as a plain cylinder — the helper is already the "
        "reliable approximation. For complete HEX BOLTS and HEX NUTS, use the "
        "dedicated fastener helpers instead (see FASTENER RULE below). "
        "(B4) For CAMS: Compute the cam profile as polar coordinates using math, "
        "convert to (x,y) points, use .polyline(points).close().extrude(). "
        "(B5) For SPRINGS: Approximate as a cylinder or torus. Do NOT attempt "
        "3D helical sweeps. "
        "(B6) For SPROCKETS: Similar to gears — use .polyline() with computed "
        "tooth profiles, or .circle().extrude() with .cut() loops for teeth. "
        "(B7) Extract all numeric engineering parameters from the description "
        "(teeth, module, pitch, diameter, bore, etc.) and apply them precisely."
    ),

    "rule_fasteners": (
        "FASTENER RULE (MANDATORY — USE THE LIBRARY): "
        "For any standard HEX BOLT / HEX SCREW, you MUST use: "
        "  from cad_utils import make_hex_bolt "
        "  result = make_hex_bolt( "
        "      nominal_diameter=10.0,   # M10 -> 10.0, M8 -> 8.0, etc. "
        "      pitch=1.5,                # thread pitch in mm "
        "      shank_length=40.0,        # threaded shank length in mm "
        "      # head_width_across_flats=17.0,  # optional; ISO 4014 default used if omitted "
        "      # head_thickness=6.4,            # optional; ISO 4014 default used if omitted "
        "  ) "
        "\n"
        "For any standard HEX NUT, you MUST use: "
        "  from cad_utils import make_hex_nut "
        "  result = make_hex_nut( "
        "      nominal_diameter=10.0, "
        "      pitch=1.5, "
        "      # width_across_flats=17.0,  # optional; ISO 4032 default used if omitted "
        "      # height=8.0,               # optional; ISO 4032 default used if omitted "
        "  ) "
        "\n"
        "If the prompt specifies exact head/nut dimensions (e.g. '17mm width "
        "across flats'), pass them explicitly; otherwise leave the optional "
        "arguments out and the helper picks ISO standard values. "
        "\n"
        "Do NOT hand-build a bolt or nut from .polygon() + .circle() + "
        ".extrude() — the helpers already produce a valid centered solid with "
        "proper threads. Hand-rolled fasteners historically fail because the "
        "LLM invents face selectors, miscomputes the hex circumscribed radius, "
        "or tries to model threads as a plain cylinder with no ribs."
    ),

    # ── Domain C: Organic / Ergonomic ─────────────────────────────────
    "rule_domain_c": (
        "DOMAIN C STRATEGY — Cross-Sectional Lofting: "
        "\n"
        "(C0) PRIMITIVE FAST PATH — check this FIRST. If the part description "
        "names a single primitive shape, use the matching CadQuery primitive "
        "directly. Do NOT loft a primitive out of many cross-sections. "
        "  - sphere / ball / globe          → cq.Workplane('XY').sphere(radius) "
        "  - cylinder / rod / shaft         → cq.Workplane('XY').cylinder(h, r) "
        "  - box / cube                     → cq.Workplane('XY').box(L, W, H) "
        "  - ellipsoid (one axis stretched) → 2-section .loft() between two ellipses, NOT 10+ sections "
        "Decoration on top of a primitive (e.g., baseball stitching) is OPTIONAL: "
        "the body must always succeed first. If a decorative sweep or stitch step "
        "is risky, omit it and return the clean primitive. "
        "\n"
        "Use the lofting strategy below ONLY when no primitive matches the "
        "shape (handles, ergonomic grips, freeform sculpts, animal heads). "
        "\n"
        "(C1) LOFT APPROACH — for shapes no primitive matches: "
        "Use .loft() for genuinely organic shapes (handles, grips, animal heads, freeform). "
        "Do NOT try to approximate complex organic shapes with stacks of boxes and cylinders — "
        "they will look wrong. The steps: "
        "  1. Create multiple 2D cross-section profiles at different Z heights. "
        "  2. Each cross-section should be a closed wire (circle, ellipse, or spline). "
        "  3. Use .workplane(offset=z) to step to the next Z-height before drawing each section. "
        "  4. Loft between them with .loft(ruled=False) for smooth interpolation. "
        "\n"
        "CRITICAL — STRICT CHAINING RULE: "
        "All lofts MUST be chained on a SINGLE continuous cq.Workplane object. "
        "NEVER assign intermediate cross-sections to separate variables and then "
        "pass them into .loft(). This causes a ValueError. "
        "\n"
        "EXAMPLE PATTERN (CORRECT — single chain): "
        "  result = ( "
        "      cq.Workplane('XY') "
        "      .rect(40, 60)  # base cross-section "
        "      .workplane(offset=30) "
        "      .rect(35, 50)  # mid cross-section "
        "      .workplane(offset=30) "
        "      .rect(20, 30)  # top cross-section "
        "      .loft()  # smooth interpolation between sections "
        "  ) "
        "\n"
        "WRONG PATTERN (causes ValueError — NEVER do this): "
        "  s1 = cq.Workplane('XY').rect(40, 60) "
        "  s2 = s1.workplane(offset=30).rect(35, 50) "
        "  result = s1.loft(s2)  # CRASHES "
        "\n"
        "(C2) For more complex shapes, use .spline() instead of .rect(): "
        "  Define each cross-section as a spline through control points. "
        "  pts = [(x1,y1), (x2,y2), ...] "
        "  .spline(pts, periodic=True) for closed spline profiles. "
        "\n"
        "(C3) For handles/grips: loft between circular/elliptical sections of "
        "varying sizes along the grip axis. Add ergonomic finger grooves by "
        "varying the cross-section radius sinusoidally. "
        "\n"
        "(C4) HOLLOWING / SHELLING (MANDATORY — No Russian Dolls): "
        "Do NOT hollow out organic parts by boolean-subtracting a smaller copy. "
        "This produces fragile, non-manifold geometry that crashes the BRep kernel. "
        "MANDATORY: Create the outer loft, then select the opening face and use "
        ".shell(-wall_thickness). "
        "Example: result = [loft chain].faces('>Z').shell(-2.0) "
        "\n"
        "(C5) For aerodynamic shapes (fairings, nose cones): loft from a large "
        "base profile to a small tip circle or point (use a tiny circle, not a point). "
        "\n"
        "(C6) Apply .fillet() on sharp edges LAST, after the loft is complete. "
        "(C7) Ensure at least 3 cross-sections for smooth shapes (more = smoother)."
    ),

    # ── Domain C variant: Low-Poly / Game-Asset Composition ───────────
    # Replaces rule_domain_c when the part description signals a stylized
    # creature / character feature. Periodic-spline lofts are the wrong
    # tool for these — they trigger BSpline interpolation failures
    # (BSplCLib::Interpolate Standard_OutOfRange) and aren't needed for
    # game-asset fidelity. Substitution is performed in _resolve_rule_names.
    "rule_domain_c_low_poly": (
        "DOMAIN C STRATEGY — Low-Poly Primitive Composition (Game-Asset Mode): "
        "This part is a stylized creature/character feature. Build it from "
        "simple primitives unioned together, NOT from periodic-spline lofts. "
        "Spline lofts with many control points trigger BSpline interpolation "
        "failures and are not needed for game-asset fidelity. "
        "\n"
        "(L1) PREFERRED PRIMITIVES (combine with .union()): "
        "  - Sphere:           cq.Workplane('XY').sphere(radius) "
        "  - Cylinder:         cq.Workplane('XY').cylinder(height, radius) "
        "  - Box:              cq.Workplane('XY').box(L, W, H) "
        "  - Capsule:          cylinder + sphere on each end "
        "  - Tapered cylinder: short two-section .loft() between two circles "
        "  - Ellipsoid:        short two-section .loft() between two ellipses "
        "\n"
        "(L2) DO NOT use periodic-spline lofts (.spline(pts, periodic=True) "
        "fed into .loft()). Stay with primitives plus, at most, one short "
        ".loft() between two ordinary circles or ellipses. Never more than "
        "three cross-sections in a single loft chain. "
        "\n"
        "(L3) DO NOT manually edit individual spline control points to add "
        "ear bumps, eye sockets, or other details. If the part needs a bump, "
        "model it as a separate small primitive (sphere/cone) and .union() "
        "it onto the parent shape at the desired position via "
        "result = base.union(detail.translate((x, y, z))). "
        "\n"
        "(L4) DO NOT call .shell() on this part. Game-asset parts are solid; "
        "the Domain C shelling mandate does not apply in low-poly mode. "
        "\n"
        "(L5) FILLETING: optional. If applied, use a small radius — no "
        "larger than 0.1 × the smallest dimension of the smallest "
        "sub-primitive. Skipping fillet entirely is acceptable for low-poly "
        "assets. "
        "\n"
        "(L6) Avoid boolean subtractions unless strictly required. Low-poly "
        "assets rarely need negative features, and boundary cleanup on a "
        "cluster of unioned primitives is fragile. "
        "\n"
        "(L7) NEVER call .cut() / .union() / .shell() / .fillet() inside a "
        "loft profile lambda or on a 2D wire. These methods need a 3D solid. "
        "If the profile needs a notch, build the part FIRST, then subtract "
        "from the resulting solid. (See rule_no_boolean_on_wire.) "
        "\n"
        "(L8) WORKED EXAMPLE — a cat front leg in a sitting pose (~60mm): "
        "  upper = cq.Workplane('XY').cylinder(40, 6) "
        "  knee  = cq.Workplane('XY').sphere(7).translate((0, 0, -20)) "
        "  lower = cq.Workplane('XY').cylinder(20, 5).translate((0, 0, -32)) "
        "  paw   = ( "
        "      cq.Workplane('XY') "
        "      .ellipse(10, 8) "
        "      .workplane(offset=6).ellipse(8, 6) "
        "      .loft() "
        "      .translate((0, 0, -45)) "
        "  ) "
        "  result = upper.union(knee).union(lower).union(paw) "
        "Notice: every component is a clean primitive or a 2-section loft, "
        "no .cut() inside any profile, no .shell()."
    ),

    # ── Domain E: Sheet Metal / Thin-Wall ─────────────────────────────
    "rule_domain_e": (
        "DOMAIN E STRATEGY — Sheet Metal and Thin-Wall Parts:\n"
        "You are generating a sheet metal part using CadQuery.\n"
        "\n"
        "SHEET METAL DESIGN RULES (follow strictly):\n"
        "  - Minimum bend radius: 1× material thickness for soft materials (aluminum, "
        "copper), 2× for hard materials (steel, stainless)\n"
        "  - Minimum flange length: 2× material thickness + bend radius\n"
        "  - Hole-to-edge distance: minimum 2× material thickness from any hole edge\n"
        "  - Hole-to-hole distance: minimum 3× material thickness between hole centers\n"
        "  - K-factor for mild steel: 0.33 (inside setback = K × material_thickness × tan(bend_angle/2))\n"
        "  - Standard gauges: 0.8mm (20 ga), 1.0mm (18 ga), 1.2mm (16 ga), 1.5mm (14 ga), 2.0mm (11 ga)\n"
        "  - Bend relief: add 0.5mm × material_thickness slots at each corner of a bent flange\n"
        "\n"
        "GENERATION APPROACH:\n"
        "  Sheet metal parts are generated as thin extruded profiles — NOT as thick solids.\n"
        "  CadQuery does not natively support flat-pattern unfolding. Generate the folded "
        "final shape only (not the flat pattern).\n"
        "\n"
        "(E1) FOR SIMPLE L-BRACKETS AND CHANNELS:\n"
        "  Use stepwise extrusions:\n"
        "  base = cq.Workplane('XY').rect(base_width, base_length).extrude(material_thickness)\n"
        "  wall = base.faces('>Z').workplane().rect(wall_height, base_length).extrude(material_thickness)\n"
        "  result = base.union(wall)\n"
        "\n"
        "(E2) FOR ENCLOSURE PANELS WITH FLANGES:\n"
        "  Create the panel as a flat plate, then add flange extrusions on each edge.\n"
        "  Panel: .box(width, height, thickness)\n"
        "  Flange: select edge face, .workplane().rect(flange_depth, height).extrude(thickness)\n"
        "\n"
        "(E3) FOR CUTOUTS AND KNOCKOUTS:\n"
        "  Use .faces().workplane().hole() or .cutBlind() for round/rectangular cutouts.\n"
        "  For slots: .rect(slot_width, slot_height).cutThruAll()\n"
        "\n"
        "(E4) MATERIAL THICKNESS:\n"
        "  Always use the cad_utils make_bent_bracket() or make_enclosure_panel() templates "
        "when available — they enforce the design rules automatically.\n"
        "  If generating from scratch: the material thickness must be UNIFORM across all faces.\n"
        "\n"
        "(E5) AVAILABLE SHEET METAL TEMPLATES (from cad_utils.py):\n"
        "  from cad_utils import make_bent_bracket, make_enclosure_panel\n"
        "  make_bent_bracket(material_thickness, base_length, base_width, flange_height, "
        "bend_radius, n_base_holes, hole_diameter)\n"
        "  make_enclosure_panel(width, height, material_thickness, flange_depth, "
        "cutout_positions=[])\n"
    ),

    # ── Domain D: Aerospace / Aerodynamics ────────────────────────────
    "rule_d_overview": (
        "DOMAIN D STRATEGY — OML Master Modeling (Solid-Skin Architecture): "
        "\n"
        "All Domain D scripts follow one deterministic workflow called "
        "OML Master Modeling. The agent lofts a single solid called oml_solid "
        "from the user's requested airfoil wires. This solid IS the wing skin. "
        "It is NEVER hollowed, NEVER shelled, and NEVER boolean-subtracted. "
        "Internal ribs and spars are generated by intersecting oversized blanks "
        "DIRECTLY with oml_solid, producing mathematically perfect cross-sections "
        "that are guaranteed to sit inside the aerodynamic envelope. "
        "\n\n"
        "WHY OML MASTER (NOT Dual-Loft, NOT .shell()): "
        "OpenCASCADE cannot reliably perform boolean subtractions on "
        "out-of-phase NACA camber polynomials. The Dual-Loft approach "
        "(outer_wing.cut(inner_void)) suffers from geometric clipping when "
        "the inner and outer airfoil profiles have mismatched curvature "
        "distributions, producing corrupt B-Reps or empty results. The "
        ".shell() command crashes on NACA trailing-edge cusps with "
        "StdFail_NotDone. The OML Master approach eliminates ALL boolean "
        "subtractions from the skin entirely — the solid OML is the single "
        "source of truth, and the web viewer simply hides the skin layer to "
        "reveal the internal structure. Zero kernel crashes, zero ghost "
        "pointers, zero topological consumption bugs. "
    ),
    "rule_no_shell": (
        "RULE 1 — THE SOLID OML MASTER (NEVER HOLLOW): "
        "\n"
        "The agent MUST NEVER attempt to hollow out the wing skin. There is "
        "NO inner_void. There is NO skin cut. There is NO .shell() call. "
        "The oml_solid is simply the lofted solid of the user's requested "
        "airfoil wires — a fully solid wing volume representing the Outer "
        "Mold Line. "
        "\n"
        "The agent generates TWO wires (root + tip) at the user's requested "
        "NACA code and chord lengths, lofts them into oml_solid, and that "
        "is the complete wing skin. "
        "\n"
        "FORBIDDEN — any of these = immediate kernel crash or bad geometry: "
        "  - .shell() — BANNED in ALL Domain D scripts, any argument, any face "
        "  - .cut() on oml_solid to hollow it — BANNED "
        "  - Generating an inner_void or any second airfoil loft for subtraction "
        "  - Core Deflation (deriving a thinner NACA code) — NO LONGER NEEDED "
        "  - Any boolean subtraction on the wing skin solid "
    ),
    "rule_spatial_alignment": (
        "RULE 2 — STRICT SPATIAL ALIGNMENT: "
        "\n"
        "Every Domain D script MUST use this hardcoded axis convention: "
        "\n"
        "  X axis = CHORD      (leading edge to trailing edge) "
        "  Y axis = SPAN       (root at Y=0, tip at Y=span) "
        "  Z axis = THICKNESS  (pressure surface to suction surface) "
        "\n"
        "This convention ensures that: "
        "  - The airfoil cross-section lies in the XZ plane. "
        "  - Rib blanks sketched on cq.Workplane('XZ') are perpendicular to the "
        "    span axis and produce true cross-sectional ribs. "
        "  - .extrude() on an XZ sketch advances along Y (span), giving ribs "
        "    their correct thickness along the span. "
        "\n"
        "ALL make_naca_wire() outputs MUST be rotated into the XZ plane "
        "IMMEDIATELY after generation: "
        "  wire = wire.rotate((0,0,0), (1,0,0), -90) "
        "This 90-degree rotation around the X axis maps Y -> -Z, placing the "
        "airfoil profile into XZ with chord along X and thickness along Z. "
        "\n"
        "Tip wires MUST be translated along the Y-axis: "
        "  tip_wire = tip_wire.translate(cq.Vector(0, span, 0)) "
        "\n"
        "FORBIDDEN — using raw unrotated wires: "
        "  - Lofting unrotated XY-plane wires along Z (span along Z) "
        "  - Translating the tip wire with cq.Vector(0, 0, span) "
        "  - Any orientation where span does not run along Y "
    ),
    "rule_reversed_lofting": (
        "RULE 3 — REVERSED LOFTING (POSITIVE B-Rep): "
        "\n"
        "When passing wires to makeLoft, the array MUST be REVERSED — tip "
        "wire FIRST, root wire LAST: "
        "\n"
        "  oml_solid = cq.Workplane('XZ').newObject([ "
        "      cq.Solid.makeLoft([tip_wire, root_wire]) "
        "  ]) "
        "\n"
        "WHY REVERSED: OpenCASCADE's makeLoft() derives face normals from the "
        "loft direction vector. Lofting from root (Y=0) to tip (Y=span) "
        "produces normals that point inward, creating an inside-out solid "
        "whose boolean operations silently fail or produce inverted geometry. "
        "Lofting from tip (Y=span) to root (Y=0) ensures outward-facing "
        "normals consistent with CadQuery's convention. "
        "\n"
        "FORBIDDEN LOFT ORDERS AND PATTERNS: "
        "  - cq.Solid.makeLoft([root_wire, tip_wire])  # WRONG — inverted normals "
        "  - .workplane(offset=y) for positioning airfoil sections "
        "  - Passing shape variables into .loft() on a Workplane chain "
        "  - Extruding a thin solid to steal a face for lofting "
        "  - Translating the tip along Z: cq.Vector(0, 0, span)  # WRONG AXIS "
    ),
    "rule_rib_spar_intersection": (
        "RULE 4 — DIRECT RIB & SPAR INTERSECTION (WITH oml_solid): "
        "\n"
        "Ribs and spars are generated by intersecting oversized blanks "
        "DIRECTLY with oml_solid. Because oml_solid is a simple, directly-lofted "
        "solid (not a boolean result), it is always geometrically valid and "
        "the intersection always succeeds. "
        "\n"
        "Unlike other domains, aerospace assemblies (a wing with ribs, a "
        "stabilizer with spars) are HIGHLY COUPLED. The aerodynamic skin, "
        "internal ribs, and spars share a common outer mold line and cannot "
        "be generated as independent parts that are later assembled. "
        "\n"
        "MANDATORY: The Machinist MUST generate the ENTIRE subsystem — oml_solid "
        "plus ALL internal ribs and spars — within a SINGLE CadQuery script "
        "using SHARED VARIABLES. The oml_solid is the shared source of truth "
        "from which every internal part is derived. "
        "\n"
        "RIB GENERATION — XZ blank, extrude along Y, intersect oml_solid: "
        "  For each rib at spanwise station y_pos: "
        "  rib_blank = ( "
        "      cq.Workplane('XZ') "
        "      .rect(chord * 1.2, chord) "
        "      .extrude(rib_thickness) "
        "      .translate((0, y_pos - rib_thickness / 2.0, 0)) "
        "  ) "
        "  rib = rib_blank.intersect(oml_solid) "
        "\n"
        "WHY XZ: The airfoil cross-section is in the XZ plane (chord=X, "
        "thickness=Z). A rib is a cross-sectional slice of the wing, so "
        "it must lie in the same XZ plane. .extrude() on an XZ sketch "
        "advances along Y (span), giving the rib its thickness in the "
        "span direction. .translate() along Y places it at the station. "
        "\n"
        "WHY OVERSIZED BLANK (chord * 1.2): The blank must be larger than "
        "the airfoil cross-section at every spanwise station. The intersection "
        "with oml_solid trims it to the exact airfoil profile. An undersized "
        "blank clips the rib short. "
        "\n"
        "SPAR GENERATION — YZ blank at chordwise X-offset, intersect oml_solid: "
        "  Spars run the full span at fixed chordwise positions (typically 25% "
        "  and 60% chord from leading edge). Sketch on YZ, offset along X, "
        "  and intersect with oml_solid: "
        "  spar_blank = ( "
        "      cq.Workplane('YZ') "
        "      .workplane(offset=x_pos) "
        "      .rect(span * 1.1, chord * 0.5) "
        "      .extrude(spar_thickness) "
        "  ) "
        "  spar = spar_blank.intersect(oml_solid) "
        "\n"
        "FORBIDDEN PATTERNS (any of these = immediate failure): "
        "  - .shell() — BANNED (Rule 1) "
        "  - .cut() on oml_solid to hollow it — BANNED "
        "  - make_naca_wire() called to shape a rib or spar "
        "  - Any 2D airfoil sketch used for internal structure geometry "
        "  - Rib blanks sketched on any plane other than XZ "
        "  - Spar blanks sketched on any plane other than YZ "
        "  - cq.Workplane('XY').rect(...).extrude(t)  # longitudinal slab "
    ),
    "rule_assembly_export": (
        "RULE 5 — ASSEMBLY & DUAL-FORMAT EXPORT: "
        "\n"
        "The script MUST NEVER use .union() to merge separate parts. Unioning "
        "collapses distinct bodies into a monolithic solid, destroying the "
        "topological boundaries the Assembler agent needs for constraint-based "
        "mating. "
        "\n"
        "The script MUST NEVER assign a Python dict or list to the result "
        "variable. The pipeline exporter calls cq.Assembly().add(result) which "
        "raises TypeError on dicts and lists. "
        "\n"
        "MANDATORY: The script MUST explicitly instantiate a cq.Assembly(), "
        "add oml_solid as 'wing_skin', add every rib and spar as named bodies, "
        "and assign the assembly to result: "
        "  assembly = cq.Assembly() "
        "  assembly.add(oml_solid, name='wing_skin') "
        "  assembly.add(rib, name='rib_1') "
        "  result = assembly "
        "\n"
        "By leaving wing_skin as a solid master, the web viewer can simply "
        "hide the skin layer to reveal the mathematically perfect internal "
        "ribs and spars — no boolean kernel crashes, no ghost pointers, no "
        "topological consumption bugs. "
        "\n"
        "DUAL-FORMAT EXPORT: The code must export BOTH formats: "
        "  assembly.save('output.glb')   # WebGL viewer "
        "  assembly.save('output.step')  # Inventor/SolidWorks true B-Rep "
    ),
    "rule_d_workflow": (
        "STEP-BY-STEP GENERATION PROCESS: "
        "\n\n"
        "STEP 1 — AIRFOIL WIRE GENERATION + ROTATION (TWO WIRES): "
        "\n"
        "For ANY airfoil, wing section, or aerodynamic profile, the agent MUST: "
        "  (a) Generate TWO wires — root and tip — at the user's requested "
        "      NACA code and chord lengths. "
        "  (b) Rotate BOTH wires into the XZ plane. "
        "  (c) Translate the tip wire along Y to the span station. "
        "\n"
        "  from cad_utils import make_naca_wire "
        "  import cadquery as cq "
        "\n"
        "  naca_code = '2412' "
        "  root_wire = make_naca_wire(naca_code=naca_code, chord_length=root_chord) "
        "  root_wire = root_wire.rotate((0,0,0), (1,0,0), -90) "
        "  tip_wire = make_naca_wire(naca_code=naca_code, chord_length=tip_chord) "
        "  tip_wire = tip_wire.rotate((0,0,0), (1,0,0), -90) "
        "  tip_wire = tip_wire.translate(cq.Vector(0, span, 0)) "
        "\n"
        "make_naca_wire() returns a closed cq.Wire in the XY plane. The .rotate() "
        "call swings it into the XZ plane so the chord runs along X and the "
        "thickness along Z, matching the Spatial Alignment Standard (Rule 2). "
        "Do NOT manually sketch airfoil curves or approximate with ellipses. "
        "Do NOT use make_naca_airfoil() — use make_naca_wire(). "
        "Do NOT skip the .rotate() — an unrotated wire puts span along Z, "
        "causing all rib blanks to produce longitudinal slices instead of "
        "cross-sectional ribs. "
        "\n\n"
        "STEP 2 — SINGLE LOFT TO oml_solid (Rule 3 — reversed order): "
        "\n"
        "Loft the solid OML using cq.Solid.makeLoft() with REVERSED wire order "
        "(tip first, root last) to ensure outward-facing normals: "
        "\n"
        "  oml_solid = cq.Workplane('XZ').newObject([ "
        "      cq.Solid.makeLoft([tip_wire, root_wire]) "
        "  ]) "
        "\n"
        "This is the ONLY loft in the entire script. There is no inner_void, "
        "no second loft, and no boolean subtraction of the skin. "
        "\n"
        "FORBIDDEN LOFTING PATTERNS: "
        "  - cq.Solid.makeLoft([root_wire, tip_wire])  # inverted normals "
        "  - .workplane(offset=y) for positioning airfoil sections "
        "  - Passing shape variables into .loft() on a Workplane chain "
        "  - Extruding a thin solid to steal a face for lofting "
        "  - Translating the tip along Z: cq.Vector(0, 0, span)  # WRONG AXIS "
        "\n\n"
        "STEP 3 — INTERNAL STRUCTURE (RIBS & SPARS via intersection): "
        "\n"
        "Because oml_solid is a directly-lofted solid (not a boolean result), "
        "it is guaranteed to be a valid, non-empty body. Intersections with "
        "oml_solid can be performed in any order — there is no topological "
        "consumption bug because oml_solid is never consumed by a .cut(). "
        "\n"
        "RIB GENERATION — XZ blank, extrude along Y, intersect oml_solid: "
        "  For each rib at spanwise station y_pos: "
        "  rib_blank = ( "
        "      cq.Workplane('XZ') "
        "      .rect(chord * 1.2, chord) "
        "      .extrude(rib_thickness) "
        "      .translate((0, y_pos - rib_thickness / 2.0, 0)) "
        "  ) "
        "  rib = rib_blank.intersect(oml_solid) "
        "\n"
        "SPAR GENERATION — YZ blank at chordwise X-offset, intersect oml_solid: "
        "  spar_blank = ( "
        "      cq.Workplane('YZ') "
        "      .workplane(offset=x_pos) "
        "      .rect(span * 1.1, chord * 0.5) "
        "      .extrude(spar_thickness) "
        "  ) "
        "  spar = spar_blank.intersect(oml_solid) "
        "\n\n"
        "STEP 4 — ASSEMBLY PACKAGING & DUAL-FORMAT EXPORT: "
        "\n"
        "  assembly = cq.Assembly() "
        "  assembly.add(oml_solid, name='wing_skin') "
        "  for i, rib in enumerate(ribs, 1): "
        "      assembly.add(rib, name=f'rib_{i}') "
        "  for spar_name, spar in spars.items(): "
        "      assembly.add(spar, name=spar_name) "
        "  result = assembly "
        "\n"
        "  # Dual-format export "
        "  assembly.save('output.glb')   # WebGL viewer "
        "  assembly.save('output.step')  # Inventor/SolidWorks true B-Rep "
        "\n"
        "Anchor semantics for the Assembler (standard loft orientation): "
        "  '>Z' / '<Z' — pressure / suction skin surfaces "
        "  '>X' / '<X' — trailing edge / leading edge faces "
        "  '<Y' / '>Y' — root / tip spanwise end faces "
    ),
    "rule_d_example": (
        "COMPLETE WORKED EXAMPLE: "
        "\n"
        "The following example demonstrates the OML Master Modeling workflow "
        "end-to-end. TWO wires generated (root + tip), both rotated into XZ, "
        "tip translated along Y, loft uses reversed order [tip_wire, root_wire]. "
        "Ribs intersect oml_solid directly. No .shell() call anywhere. No "
        "inner_void. No .cut() on the skin. No .union(). No dict assigned "
        "to result. "
        "\n\n"
        "  from cad_utils import make_naca_wire "
        "  import cadquery as cq "
        "\n"
        "  # --- Parameters (single script, shared variables) --- "
        "  naca_code = '2412' "
        "  root_chord = 200.0 "
        "  tip_chord = 120.0 "
        "  span = 500.0 "
        "  num_ribs = 5 "
        "  rib_thickness = 2.0 "
        "  spar_thickness = 2.0 "
        "\n"
        "  # --- Step 1: Generate TWO wires and rotate into XZ plane --- "
        "  root_wire = make_naca_wire(naca_code, chord_length=root_chord) "
        "  root_wire = root_wire.rotate((0,0,0), (1,0,0), -90) "
        "  tip_wire = make_naca_wire(naca_code, chord_length=tip_chord) "
        "  tip_wire = tip_wire.rotate((0,0,0), (1,0,0), -90) "
        "  tip_wire = tip_wire.translate(cq.Vector(0, span, 0)) "
        "\n"
        "  # --- Step 2: Single loft — tip first, root last --- "
        "  oml_solid = cq.Workplane('XZ').newObject([ "
        "      cq.Solid.makeLoft([tip_wire, root_wire]) "
        "  ]) "
        "\n"
        "  # --- Step 3a: Ribs — intersect oversized blanks with oml_solid --- "
        "  rib_spacing = span / (num_ribs + 1) "
        "  ribs = [] "
        "  for i in range(1, num_ribs + 1): "
        "      y_pos = i * rib_spacing "
        "      rib_blank = ( "
        "          cq.Workplane('XZ') "
        "          .rect(root_chord * 1.2, root_chord) "
        "          .extrude(rib_thickness) "
        "          .translate((0, y_pos - rib_thickness / 2.0, 0)) "
        "      ) "
        "      rib = rib_blank.intersect(oml_solid) "
        "      ribs.append(rib) "
        "\n"
        "  # --- Step 3b: Spars — intersect oversized blanks with oml_solid --- "
        "  spar_positions = { "
        "      'spar_front': -root_chord / 2.0 + root_chord * 0.25, "
        "      'spar_rear':  -root_chord / 2.0 + root_chord * 0.60, "
        "  } "
        "  spars = {} "
        "  for spar_name, x_pos in spar_positions.items(): "
        "      spar_blank = ( "
        "          cq.Workplane('YZ') "
        "          .workplane(offset=x_pos) "
        "          .rect(span * 1.1, root_chord * 0.5) "
        "          .extrude(spar_thickness) "
        "      ) "
        "      spars[spar_name] = spar_blank.intersect(oml_solid) "
        "\n"
        "  # --- Step 4: Package into Assembly & export --- "
        "  assembly = cq.Assembly() "
        "  assembly.add(oml_solid, name='wing_skin') "
        "  for i, rib in enumerate(ribs, 1): "
        "      assembly.add(rib, name=f'rib_{i}') "
        "  for spar_name, spar in spars.items(): "
        "      assembly.add(spar, name=spar_name) "
        "  result = assembly "
        "\n"
        "  # Dual-format export "
        "  assembly.save('output.glb') "
        "  assembly.save('output.step') "
    ),
    "rule_wire_to_solid": (
        "WIRE-TO-SOLID CONVERSION RULES (for non-loft use cases): "
        "\n"
        "If you ever need to extrude a cq.Wire from make_naca_wire() into a "
        "solid (e.g., for a simple fin or single-section airfoil without internal "
        "structure), the ONLY safe path is Wire -> rotate -> Face -> "
        "Workplane.add().extrude(): "
        "  placed_wire = my_wire.rotate((0,0,0), (1,0,0), -90) "
        "  airfoil_face = cq.Face.makeFromWires(placed_wire) "
        "  airfoil_solid = cq.Workplane('XZ').add(airfoil_face).extrude(thickness) "
        "\n"
        "FORBIDDEN: "
        "  - cq.Workplane().add(wire).extrude() — Wire on Workplane crashes "
        "  - my_face.extrude(...) — cq.Face has no .extrude() method "
        "  - Skipping the .rotate() — leaves the wire in XY, wrong orientation "
        "  - .shell() — BANNED everywhere in Domain D "
    ),
    "rule_d_parameters": (
        "PARAMETER EXTRACTION (with defaults): "
        "  - NACA code: 4-digit string (default '2412') "
        "  - Root chord: mm (default 200.0) "
        "  - Tip chord: mm (default same as root for constant-chord, "
        "    or 60% of root for tapered wings) "
        "  - Span: mm (default 500.0) "
        "  - Rib count: integer (default 5) "
        "  - Spar count: integer (default 2) "
        "  - Rib thickness: mm (default 2.0) "
        "  - Spar thickness: mm (default 2.0) "
        "If the user specifies a NACA designation like 'NACA 0012' or 'NACA 4415', "
        "extract the 4-digit code and pass it to make_naca_wire(). "
    ),

    "rule_no_boolean_on_wire": (
        "BOOLEAN OPS REQUIRE A 3D SOLID — NEVER call them on a 2D wire. "
        "The methods .cut(), .union(), .intersect(), .shell(), .fillet(), and "
        ".chamfer() all operate on a 3D solid. Calling any of them on a "
        "workplane that holds only a wire (.circle(), .rect(), .ellipse(), "
        ".polyline(), .polygon(), .spline()) crashes the kernel with "
        "'Cannot find a solid on the stack or in the parent chain'. "
        "\n"
        "RULE: Convert the wire to a solid FIRST via .extrude(thickness), "
        ".revolve(), or .loft() before any boolean / shell / fillet op. "
        "\n"
        "INSIDE A LOFT CHAIN, NEVER call .cut() or .union() on a profile. "
        "Loft profiles are wires; cut/union do not apply. If a section needs "
        "a notch, build the loft FIRST, then subtract the notch from the "
        "resulting solid: "
        "  result = ( cq.Workplane('XY').circle(10) "
        "             .workplane(offset=20).circle(8) "
        "             .loft() ) "
        "  result = result.faces('>Z').workplane().rect(5, 5).cutBlind(-2) "
        "\n"
        "WRONG (causes 'Cannot find a solid' — NEVER do this): "
        "  cq.Workplane('XY').circle(10).cut(other_workplane)        # circle is a wire "
        "  lambda wp: wp.circle(r).cut(rect_wp)                       # inside a loft profile "
        "  cq.Workplane('XY').rect(10,10).fillet(1)                   # rect is a wire "
        "  cq.Workplane('XY').ellipse(5,3).shell(-1)                  # ellipse is a wire "
    ),

    "rule_co2_dragster": (
        "CO2 DRAGSTER STRATEGY (MANDATORY): "
        "When generating a CO2 dragster, you MUST follow this exact boolean sequence: "
        "1. Generate the main aerodynamic body (assume length runs along X-axis). "
        "2. Import: from cad_utils import make_co2_void. "
        "3. Instantiate: co2 = make_co2_void(). "
        "4. Translate void to the REAR of the car. "
        "5. Cut chamber: body = body.cut(co2). "
        "6. Cut front and rear axle holes using "
        ".workplane('XZ').center(...).circle(r).cutThruAll(). "
        "DO NOT manually model the CO2 chamber."
    ),

    # ── Domain F: Propulsion ──────────────────────────────────────────
    "rule_domain_f": (
        "DOMAIN F STRATEGY — Propulsion Geometry (Revolve-First Architecture): "
        "\n"
        "Domain F parts (nozzles, combustion chambers, nacelles, inlet ducts) are "
        "axisymmetric bodies of revolution. Every Domain F script follows this workflow: "
        "\n"
        "STEP 1 — IMPORT THE PROPULSION LIBRARY: "
        "  from cad_utils import make_nozzle_profile, make_combustion_chamber, make_nacelle_cowl "
        "\n"
        "STEP 2 — USE THE CORRECT HELPER (copy the template exactly, fill in dimensions): "
        "\n"
        "  • Rocket nozzle (De Laval / C-D convergent-divergent): "
        "      nozzle = make_nozzle_profile( "
        "          chamber_radius=50.0,       # inner radius of chamber section (mm) "
        "          throat_radius=20.0,        # nozzle throat — minimum flow radius (mm) "
        "          exit_radius=65.0,          # exit-plane radius; > chamber_radius = bell nozzle "
        "          chamber_length=80.0,       # length of the straight cylindrical chamber (mm) "
        "          conv_half_angle_deg=30.0,  # converging taper half-angle, range 15–45 deg "
        "          div_half_angle_deg=15.0,   # diverging taper half-angle, range 10–25 deg "
        "          wall_thickness=5.0,        # uniform nozzle wall thickness (mm) "
        "      )  # returns cq.Workplane — wrap in Assembly below "
        "      assembly = cq.Assembly() "
        "      assembly.add(nozzle, name='nozzle_body') "
        "      result = assembly "
        "\n"
        "  • Combustion chamber with spherical injector dome: "
        "      result = make_combustion_chamber( "
        "          inner_radius=50.0,         # chamber inner radius (mm) "
        "          wall_thickness=8.0,        # chamber wall thickness (mm) "
        "          length=150.0,              # cylindrical chamber section length (mm) "
        "          dome_radius=55.0,          # spherical dome radius — typically 0.9–1.1 × inner_radius "
        "          num_fuel_inlets=6,         # number of fuel inlet ports; typical: 4, 6, or 8 "
        "          fuel_inlet_diameter=8.0,   # diameter of each fuel inlet bore (mm) "
        "      )  # returns cq.Assembly with 'chamber_body' and 'dome' — assign directly to result "
        "\n"
        "  • Subsonic nacelle / inlet cowl: "
        "      result = make_nacelle_cowl( "
        "          highlight_radius=250.0,    # outer lip radius at inlet highlight plane (mm) "
        "          throat_radius=180.0,       # minimum inner-duct radius at the throat (mm) "
        "          fan_face_radius=210.0,     # inner-duct radius at the fan/engine face (mm) "
        "          intake_length=350.0,       # axial length from highlight to fan face (mm) "
        "          cowl_thickness=8.0,        # nacelle wall (cowl) thickness (mm) "
        "          lip_radius=15.0,           # fillet radius at the leading-edge lip (mm) "
        "      )  # returns cq.Assembly with 'inner_duct' and 'outer_cowl' — assign to result "
        "\n"
        "  • Straight duct / afterburner duct / transition duct (constant cross-section): "
        "      Use make_nacelle_cowl with throat_radius == fan_face_radius for a straight bore: "
        "      wall = outer_radius - inner_radius  # e.g. 83 - 80 = 3mm "
        "      result = make_nacelle_cowl( "
        "          highlight_radius=83.0,     # = outer radius of the duct (mm) "
        "          throat_radius=80.0,        # = inner radius; equal to fan_face for straight duct "
        "          fan_face_radius=80.0,      # = same as throat_radius (no area change) "
        "          intake_length=400.0,       # duct axial length (mm) "
        "          cowl_thickness=3.0,        # wall thickness = outer_radius - inner_radius "
        "          lip_radius=2.0,            # small inlet fillet (mm) "
        "      )  # make_nacelle_cowl handles straight ducts correctly — NEVER use raw cylinder "
        "\n"
        "STEP 3 — ASSEMBLY OUTPUT: "
        "  Always assign a cq.Assembly to `result`. "
        "  Named bodies: 'nozzle_body', 'chamber_body', 'dome', 'inner_duct', 'outer_cowl'. "
        "  The web viewer uses these names to isolate components. "
        "\n"
        "FUEL INLETS — radial bore pattern: "
        "  Fuel inlet ports are always drilled axially through the chamber "
        "  wall using polarArray(). NEVER model them as tangential channels "
        "  or spiral grooves (BRep kernel crashes on helical cuts). "
        "  Pattern: .faces('<Z').workplane().polarArray(r, 0, 360, n).circle(d/2).cutThruAll() "
        "\n"
        "FORBIDDEN: "
        "  - Attempting helical cooling channels (OCC BRep cannot reliably sweep a "
        "    helix around a non-cylindrical surface — always crashes). "
        "  - Using .shell() to hollow a revolved nozzle (StdFail_NotDone on cusps). "
        "  - Manually building the nozzle profile as a raw polyline sketch (use the helper). "
        "  - Placing the nozzle exit at a different axis than X "
        "    (the revolve axis MUST be the X-axis). "
    ),

    # ── Domain G: Vehicle Bodies ──────────────────────────────────────
    "rule_domain_g": (
        "DOMAIN G STRATEGY — Vehicle Bodies (Multi-Station Loft Architecture): "
        "\n"
        "Domain G parts (fuselages, nose cones, fairings, pod bodies) are "
        "built from multi-station elliptical or circular lofts with optional "
        "structural frame rings. "
        "\n"
        "STEP 1 — IMPORT THE BODY LIBRARY: "
        "  from cad_utils import make_nose_cone, make_fuselage_loft "
        "\n"
        "MANDATORY (NO EXCEPTIONS): Use make_fuselage_loft for ANY Domain G cylindrical or "
        "fuselage body — even if the cross-section is constant (pure cylinder). Never use "
        "cq.Workplane().cylinder() or .extrude() for Domain G fuselages: those produce no "
        "structural frames and are forbidden. "
        "\n"
        "STEP 2 — USE THE CORRECT HELPER (copy the template exactly, fill in dimensions): "
        "\n"
        "  • Nose cone / fairing tip: "
        "      nose = make_nose_cone( "
        "          base_diameter=200.0,       # outer diameter at base — must match body tube (mm) "
        "          length=600.0,              # total nose cone length from base to tip (mm) "
        "          profile='ogive',           # shape family: 'ogive' | 'conical' | 'parabolic' | 'haack' "
        "                                     #   'haack'  = minimum drag (Von Kármán LD-Haack series) "
        "                                     #   'ogive'  = tangent ogive, best all-around choice "
        "                                     #   'conical' = simplest, highest drag "
        "          wall_thickness=0.0,        # 0 = solid nose cone; >0 = hollow shell (mm) "
        "          shoulder_length=20.0,      # cylindrical shoulder at base for body tube mating (mm) "
        "      )  # returns cq.Workplane — wrap in Assembly below "
        "      assembly = cq.Assembly() "
        "      assembly.add(nose, name='nose_cone') "
        "      result = assembly "
        "\n"
        "  • Fuselage / pod body section: "
        "      result = make_fuselage_loft( "
        "          stations=[                 # list of (x_axial, width, height) tuples — mm "
        "              (0,    150.0, 130.0),  # forward station (smallest) "
        "              (300,  300.0, 250.0),  # widest section "
        "              (800,  280.0, 240.0),  # aft taper begins "
        "              (1000, 200.0, 180.0),  # tail station "
        "          ],                         # x_axial must be monotonically increasing "
        "          wall_thickness=3.0,        # skin wall thickness (mm); 0 = solid "
        "          num_frames=6,              # number of structural frame rings "
        "          frame_thickness=2.0,       # frame ring axial thickness (mm) "
        "          frame_height=20.0,         # frame ring depth into skin (mm) "
        "          close_nose=True,           # True = cap first station "
        "          close_tail=False,          # False = leave tail open for nozzle / engine "
        "      )  # returns cq.Assembly with 'skin' and 'frame_N' bodies — assign to result "
        "\n"
        "  • Constant-section cylinder (e.g. simple fuselage tube, 500mm dia × 1500mm): "
        "      result = make_fuselage_loft( "
        "          stations=[                 # use identical width/height at each end "
        "              (0,    500.0, 500.0),  # forward face — width == height for circle "
        "              (1500, 500.0, 500.0),  # aft face "
        "          ], "
        "          wall_thickness=4.0,        # skin thickness "
        "          num_frames=6,              # structural frame rings "
        "          close_nose=False,          # leave both ends open for mating "
        "          close_tail=False, "
        "      ) "
        "\n"
        "STEP 3 — COMBINED NOSE + FUSELAGE ASSEMBLY: "
        "  nose = make_nose_cone(base_diameter=200.0, length=600.0, profile='ogive') "
        "  fus  = make_fuselage_loft( "
        "      stations=[(0, 200.0, 200.0), (800, 200.0, 200.0)], "
        "      wall_thickness=3.0, num_frames=6 "
        "  ) "
        "  fus.add(nose, name='nose_cone', "
        "          loc=cq.Location(cq.Vector(0, 0, 0)))  # fus is already an Assembly "
        "  result = fus "
        "\n"
        "AXIS CONVENTION: "
        "  X axis = axial (nose to tail, increasing toward tail) "
        "  YZ plane = cross-section plane (width along Y, height along Z) "
        "  This matches make_fuselage_loft station ordering. "
        "\n"
        "FORBIDDEN: "
        "  - .shell() on a lofted fuselage solid (crashes on complex topology). "
        "  - Manually drawing elliptical cross-sections with approximated splines "
        "    (use .ellipse(a, b) only, or let make_fuselage_loft handle it). "
        "  - Using circular cross-sections when width != height is specified. "
        "  - Non-monotonic station ordering (x_axial must strictly increase tip to tail). "
    ),

    # ── DA: Planform variant rule (keyword-triggered for Domain D) ────
    "rule_planform_variants": (
        "PLANFORM VARIANT RULE — Swept / Delta / Dihedral Wings: "
        "\n"
        "When the wing has a leading-edge sweep angle, dihedral, or delta planform, "
        "the agent MUST use make_planform_wires() instead of generating raw "
        "make_naca_wire() calls and manually translating the tip wire. "
        "\n"
        "IMPORT AND USAGE: "
        "  from cad_utils import make_planform_wires "
        "  root_wire, tip_wire = make_planform_wires( "
        "      naca_code='2412', "
        "      root_chord=250.0, "
        "      tip_chord=120.0, "
        "      span=800.0, "
        "      sweep_le_deg=35.0,   # positive = tip swept rearward "
        "      dihedral_deg=5.0,    # positive = tip raised upward "
        "      twist_tip_deg=2.0,   # positive = nose-down washout at tip "
        "  ) "
        "  oml_solid = cq.Workplane('XZ').newObject([ "
        "      cq.Solid.makeLoft([tip_wire, root_wire]) "
        "  ]) "
        "\n"
        "DELTA WINGS: set tip_chord to a small value (>= 5mm, never 0). "
        "  root_wire, tip_wire = make_planform_wires( "
        "      naca_code='0006', root_chord=600.0, tip_chord=10.0, span=400.0, "
        "      sweep_le_deg=55.0 "
        "  ) "
        "\n"
        "CANARDS: same as a normal wing — call make_planform_wires() with the "
        "canard's own chord, span, and sweep values. "
        "\n"
        "RIB BLANKS FOR SWEPT WINGS: oversized blanks must be large enough to "
        "span the full chord at the sweep-corrected station. Use root_chord * 1.5 "
        "as the blank width to guarantee coverage at all sweep angles <= 60°. "
        "\n"
        "FORBIDDEN: "
        "  - Manually translating tip wire along X without the sweep offset "
        "  - Using .workplane(offset=y) for tip positioning on swept wings "
        "  - sweep_le_deg > 60 or < -60 (loft singularities) "
    ),

    # ── DB: Twisted blade rule (keyword-triggered for Domain D) ───────
    "rule_twisted_blade": (
        "TWISTED BLADE RULE — Propellers, Rotors, Wind Turbine Blades: "
        "\n"
        "When a blade with varying pitch angle (washout/twist) is requested, "
        "the agent MUST use make_twisted_blade() from cad_utils. "
        "\n"
        "IMPORT AND USAGE: "
        "  from cad_utils import make_twisted_blade "
        "  blade = make_twisted_blade( "
        "      naca_code='4412', "
        "      root_chord=60.0, "
        "      tip_chord=25.0, "
        "      span=300.0, "
        "      root_pitch_deg=30.0,  # geometric pitch at root "
        "      tip_pitch_deg=12.0,   # geometric pitch at tip (washout) "
        "      num_stations=8,       # loft cross-sections; 6-12 is ideal "
        "  ) "
        "  assembly = cq.Assembly() "
        "  assembly.add(blade, name='blade_1') "
        "  result = assembly "
        "\n"
        "MULTI-BLADE PROPELLERS: use polar array of rotated blade copies. "
        "  For a 3-blade prop, create one blade then: "
        "  assembly = cq.Assembly() "
        "  for i in range(3): "
        "      assembly.add( "
        "          blade, "
        "          name=f'blade_{i+1}', "
        "          loc=cq.Location(cq.Vector(0,0,0), cq.Vector(0,1,0), i*120) "
        "      ) "
        "  result = assembly "
        "\n"
        "PITCH CONVENTION: "
        "  root_pitch_deg > tip_pitch_deg = washout (standard for propellers). "
        "  Higher pitch = designed for higher speed/altitude. "
        "  Typical values: root 20–40°, tip 8–15°. "
        "\n"
        "FORBIDDEN: "
        "  - Using make_naca_wire() + manual pitch rotation for twisted blades "
        "    (model consistently uses wrong rotation axis, producing flat blades) "
        "  - Rotating the tip wire around X instead of Y "
        "    (X rotation = within XZ plane, NOT pitch rotation) "
        "  - num_stations < 4 (produces faceted geometry) "
    ),
}

_CORE_RULES = [
    "rule_imports",
    "rule_result_var",
    "rule_origin",
    "rule_raw_python",
    "rule_3d_solid",
    "rule_no_boolean_on_wire",
    "rule_no_hallucinated_methods",
    "rule_face_selectors",
    "rule_no_part_tagging",
]

# Domain preambles — first line of each system prompt
_DOMAIN_PREAMBLES = {
    "A": (
        "You are a CadQuery machinist specialized in STRUCTURAL PARTS. "
        "The part you are generating is classified as Domain A (Primitives/Structural). "
    ),
    "B": (
        "You are a CadQuery machinist specialized in MECHANICAL PARTS. "
        "The part you are generating is classified as Domain B (Mechanical/Parametric). "
    ),
    "C": (
        "You are a CadQuery machinist specialized in ORGANIC SHAPES. "
        "The part you are generating is classified as Domain C (Organic/Ergonomic). "
    ),
    "D": (
        "You are a CadQuery machinist specialized in AEROSPACE STRUCTURES. "
        "The part you are generating is classified as Domain D (Aerospace/Aerodynamics). "
    ),
    "E": (
        "You are a CadQuery machinist specialized in SHEET METAL PARTS. "
        "The part you are generating is classified as Domain E (Sheet Metal/Thin-Wall). "
    ),
    "F": (
        "You are a CadQuery machinist specialized in PROPULSION COMPONENTS. "
        "The part you are generating is classified as Domain F (Propulsion/Axisymmetric). "
    ),
    "G": (
        "You are a CadQuery machinist specialized in VEHICLE BODY STRUCTURES. "
        "The part you are generating is classified as Domain G (Vehicle Bodies/Fairings). "
    ),
}

# Rules always injected for a given domain (on top of core rules)
_DOMAIN_RULES = {
    "A": ["rule_feature_vocabulary", "rule_domain_a"],
    "B": ["rule_feature_vocabulary", "rule_domain_b"],
    "C": ["rule_domain_c"],
    "E": ["rule_feature_vocabulary", "rule_domain_e"],
    "D": [
        "rule_d_overview",
        "rule_no_shell",
        "rule_spatial_alignment",
        "rule_reversed_lofting",
        "rule_rib_spar_intersection",
        "rule_assembly_export",
        "rule_d_workflow",
        "rule_d_example",
        "rule_wire_to_solid",
        "rule_d_parameters",
    ],
    "F": ["rule_feature_vocabulary", "rule_domain_f"],
    "G": ["rule_feature_vocabulary", "rule_domain_g"],
}

# Keyword triggers — inject these rules when ANY keyword matches the description
_KEYWORD_TRIGGERS = {
    "rule_polar_array": [
        "bolt circle", "circular pattern", "radial pattern",
        "equally spaced around", "evenly spaced", "polar array",
        "holes in a circle",
    ],
    "rule_loft_chaining": [
        "loft", "taper", "cross-section", "fairing", "nose cone",
        "swept", "transition",
    ],
    "rule_no_2d_methods": [
        "fillet", "chamfer", "round edge",
    ],
    "rule_safe_filleting": [
        "fillet", "chamfer", "round", "smooth edge", "ergonomic",
    ],
    "rule_face_reselection": [
        "cut", "pocket", "hole", "slot", "subtract", "boolean",
    ],
    "rule_text_api": [
        "text", "label", "engrav", "emboss", "letter", "font",
        "inscri", "carve",
    ],
    "rule_fasteners": [
        "hex bolt", "hex nut", "hex head bolt", "hex screw", "hex-head",
        "bolt", "nut", "screw", "fastener", "threaded rod", "stud",
        "m3 ", "m4 ", "m5 ", "m6 ", "m8 ", "m10", "m12", "m14", "m16", "m20",
    ],
    "rule_co2_dragster": [
        "dragster", "co2 car", "derby car", "race car",
    ],
    "rule_feature_vocabulary": [
        "boss", "pocket", "bore", "counterbore", "countersink", "flange",
        "web", "gusset", "rib", "keyway", "spline shaft", "gudgeon",
        "thread boss", "shell", "lands",
    ],
    "rule_shell_api": [
        "shell", "hollow", "wall thickness", "open top", "open box",
        "shelled", "thin wall", "thin-wall", "hollowed",
    ],
    "rule_domain_e": [
        "sheet metal", "sheet steel", "sheet aluminum", "bent plate",
        "thin wall", "thin-wall", "enclosure panel", "pcb tray", "pcb mount",
        "chassis panel", "metal gauge", "steel gauge", "aluminum gauge",
        "bend radius", "flange height", "k-factor", "flat pattern",
        "bend allowance", "metal bracket", "stamped",
    ],
    # DA: Planform variants — inject when Domain D prompt mentions sweep/dihedral/delta
    "rule_planform_variants": [
        "swept wing", "swept-wing", "delta wing", "delta-wing", "delta planform",
        "canard", "dihedral", "anhedral", "leading-edge sweep", "leading edge sweep",
        "sweep angle", "taper ratio", "swept", "sweep", "swept back",
        "low-sweep", "high-sweep", "variable sweep",
    ],
    # DB: Twisted blades — inject when Domain D prompt mentions propeller/rotor/twist
    "rule_twisted_blade": [
        "propeller", "prop blade", "rotor blade", "helicopter blade", "rotor",
        "wind turbine blade", "turbine blade", "twisted blade", "pitch angle",
        "geometric pitch", "washout", "variable pitch", "blade twist",
        "fixed pitch prop", "fixed-pitch", "variable-pitch",
    ],
    # Low-poly / game-asset override for Domain C. When any of these match
    # the part description, _resolve_rule_names substitutes rule_domain_c
    # with rule_domain_c_low_poly inside the domain rule list (it does NOT
    # append, to avoid contradictory instructions in the prompt).
    "rule_domain_c_low_poly": [
        "low poly", "low-poly", "lowpoly",
        "game asset", "game-asset", "video game", "video-game",
        "game character", "game model",
        "feline", "canine", "creature",
        "muzzle", "whisker", "paw",
        "cat", "dog", "rabbit", "horse",
        "lion", "tiger", "bear", "wolf", "fox",
        "deer", "elephant", "giraffe", "zebra",
        "monkey", "kangaroo", "dragon",
    ],
}


# --------------------------------------------------------------------------
# Strategy builder
# --------------------------------------------------------------------------

def _resolve_rule_names(domain: str, description: str = "") -> list[str]:
    """Return the ordered list of rule names that should fire for (domain, description).

    Order: core rules, then domain rules, then keyword-triggered rules. De-duplicated.
    Used by get_strategy to build the prompt and by get_active_rules to surface the
    same rule list to the critic-loop retry path.
    """
    desc_lower = description.lower()
    seen: set[str] = set()
    ordered: list[str] = []

    for name in _CORE_RULES:
        if name not in seen:
            seen.add(name)
            ordered.append(name)

    # Low-poly override: when the description signals a stylized
    # creature/game-asset feature, swap rule_domain_c for the low-poly
    # variant in-place (Domain C only). This keeps the override in the
    # domain section rather than letting it land later as a contradictory
    # extra rule alongside the strict loft mandate.
    domain_rule_names = list(_DOMAIN_RULES.get(domain, _DOMAIN_RULES["A"]))
    low_poly_keywords = _KEYWORD_TRIGGERS.get("rule_domain_c_low_poly", ())
    low_poly_active = (
        domain == "C"
        and bool(description)
        and any(kw in desc_lower for kw in low_poly_keywords)
    )
    if low_poly_active:
        domain_rule_names = [
            "rule_domain_c_low_poly" if n == "rule_domain_c" else n
            for n in domain_rule_names
        ]

    for name in domain_rule_names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    if description:
        for rule_name, keywords in _KEYWORD_TRIGGERS.items():
            # Handled by the domain substitution above — never inject
            # this rule as an extra trailing block.
            if rule_name == "rule_domain_c_low_poly":
                continue
            if rule_name not in seen and any(kw in desc_lower for kw in keywords):
                seen.add(rule_name)
                ordered.append(rule_name)
    return ordered


def get_active_rules(domain: str, description: str = "") -> list[str]:
    """Return the rule names get_strategy() would inject for this (domain, description).

    The critic loop uses this on retry to tell the Machinist which rule it most
    likely violated, anchored against the rules that were actually in scope.
    """
    return _resolve_rule_names(domain, description)


def get_strategy(domain: str, description: str = "") -> str:
    """Build a domain-specific system prompt with dynamic rule injection.

    Assembles the prompt from three layers:
      1. Core universal rules (always present)
      2. Domain-specific rules (always present for the classified domain)
      3. Keyword-triggered rules (injected only when the description matches)

    Args:
        domain: Geometric domain classification ("A", "B", "C", "D", "E", "F", or "G").
        description: The part's physical description for keyword matching.

    Returns:
        Complete system prompt string for the Machinist LLM.
    """
    rule_names = _resolve_rule_names(domain, description)

    preamble = _DOMAIN_PREAMBLES.get(domain, _DOMAIN_PREAMBLES["A"])

    core_blocks: list[str] = []
    domain_blocks: list[str] = []
    extra_blocks: list[str] = []
    domain_rule_set = set(_DOMAIN_RULES.get(domain, _DOMAIN_RULES["A"]))
    core_rule_set = set(_CORE_RULES)
    for name in rule_names:
        block = RULE_BLOCKS[name]
        if name in core_rule_set:
            core_blocks.append(block)
        elif name in domain_rule_set:
            domain_blocks.append(block)
        else:
            extra_blocks.append(block)

    sections = [
        preamble,
        "UNIVERSAL RULES (apply to ALL domains): " + " ".join(core_blocks),
        "\n\n".join(domain_blocks),
    ]
    if extra_blocks:
        sections.append("ADDITIONAL CONTEXT RULES: " + "\n\n".join(extra_blocks))

    return "\n\n".join(sections)


# --------------------------------------------------------------------------
# Fallback: Text Label on Base Plate
# --------------------------------------------------------------------------
FALLBACK_SCRIPT_TEMPLATE = '''import cadquery as cq

base = (
    cq.Workplane("XY")
    .box(120, 60, 5)
    .edges("|Z")
    .fillet(2)
)

label = (
    cq.Workplane("XY")
    .workplane(offset=5)
    .text("{label_text}", fontsize=8, distance=2, combine=False)
)

result = base.union(label)
'''


def get_fallback_script(user_text: str) -> str:
    """Generate a base plate with the user's text extruded on top.

    Used when classification fails or the part description is too vague
    to generate meaningful geometry.
    """
    # Sanitize text for embedding in Python string
    safe_text = user_text[:40].replace('"', "'").replace("\\", "").replace("\n", " ")
    return FALLBACK_SCRIPT_TEMPLATE.format(label_text=safe_text)
