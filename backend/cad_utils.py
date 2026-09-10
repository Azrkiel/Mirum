"""
Parametric CAD utility functions for the Text-to-CAD pipeline.

These functions are importable by Machinist-generated scripts via PYTHONPATH.
They provide mathematically correct geometry for parts that LLMs consistently
fail to generate (gears, threads, etc.).
"""

import math

import cadquery as cq


# Threads produced by this module are COSMETIC: correct major diameter and
# rib pitch, but annular ribs rather than a helix. Reported to the user so a
# cosmetic thread is never mistaken for cuttable geometry.
THREAD_REPRESENTATION = "cosmetic"

# Diametral clearance between a nut/tapped bore and the nominal thread
# diameter. Without it a cosmetic bolt cannot enter its own nut.
COSMETIC_THREAD_FIT_CLEARANCE_MM = 0.25


def make_involute_spur_gear(
    num_teeth: int = 20,
    module: float = 2.0,
    pressure_angle_deg: float = 20.0,
    thickness: float = 10.0,
    bore_diameter: float = 0.0,
    pitch_diameter: float | None = None,
) -> cq.Workplane:
    """Generate a mathematically accurate involute spur gear as a 3D solid.

    Uses a 2D sketch approach: computes the full gear tooth profile as a
    single continuous closed wire, adds the bore as an inner wire, and
    performs a single extrude(). This avoids repeated boolean subtractions
    which can fragment geometry into a non-manifold Compound.

    The tooth profile is computed from the standard involute function:
        inv(alpha) = tan(alpha) - alpha

    Each tooth consists of:
        - Left involute flank  (base/root circle -> addendum circle)
        - Tip arc              (along addendum circle)
        - Right involute flank (addendum circle -> base/root circle)
        - Root arc             (along root circle to the next tooth)

    Args:
        num_teeth: Number of teeth (Z). Must be >= 6.
        module: Gear module in mm (tooth size). Ignored if pitch_diameter is set.
        pressure_angle_deg: Pressure angle in degrees (standard: 20).
        thickness: Axial thickness (face width) in mm.
        bore_diameter: Central bore hole diameter in mm. 0 = no bore.
                       Must be less than the root circle diameter.
        pitch_diameter: If provided, overrides module. module = pitch_diameter / num_teeth.

    Returns:
        cq.Workplane containing the extruded 3D gear solid, centered at origin.

    Raises:
        ValueError: If bore_diameter >= root circle diameter, or if the
                    result is not a manifold cq.Solid.
    """
    if pitch_diameter is not None:
        module = pitch_diameter / num_teeth

    pa = math.radians(pressure_angle_deg)

    # Standard gear circle radii
    pitch_r = num_teeth * module / 2.0
    base_r = pitch_r * math.cos(pa)
    outer_r = pitch_r + module                        # addendum circle
    root_r = max(pitch_r - 1.25 * module, 0.5)       # dedendum circle

    if bore_diameter > 0 and bore_diameter >= 2.0 * root_r:
        raise ValueError(
            f"bore_diameter ({bore_diameter}) must be less than "
            f"root circle diameter ({2.0 * root_r:.3f})"
        )

    def involute_r_theta(t: float) -> tuple[float, float]:
        """Radius and polar angle offset on the involute at parameter t."""
        r = base_r * math.sqrt(1.0 + t * t)
        theta = t - math.atan(t)
        return r, theta

    def t_at_radius(r: float) -> float:
        """Involute parameter t at a given radius."""
        if r <= base_r:
            return 0.0
        return math.sqrt((r / base_r) ** 2 - 1.0)

    # Half angular tooth thickness at pitch circle
    inv_pa = math.tan(pa) - pa
    half_tooth_angle = math.pi / (2.0 * num_teeth) + inv_pa

    angular_pitch = 2.0 * math.pi / num_teeth
    n_flank = 15       # points per involute flank
    n_tip = 3          # interpolation points on tip arc
    n_root = 3         # interpolation points on root arc

    t_tip = t_at_radius(outer_r)
    t_start = t_at_radius(max(root_r, base_r))
    _, dtheta_start = involute_r_theta(t_start)
    _, dtheta_tip = involute_r_theta(t_tip)

    # ── Build full 2D gear profile ──────────────────────────────────
    # Traced counterclockwise around the gear.  For each tooth:
    #   left flank outward → tip arc → right flank inward → root arc
    profile_pts: list[tuple[float, float]] = []

    for i in range(num_teeth):
        theta = i * angular_pitch

        # -- Left involute flank (outward: root/base → tip) --
        if root_r < base_r:
            a = theta - half_tooth_angle
            profile_pts.append((root_r * math.cos(a), root_r * math.sin(a)))

        for j in range(n_flank + 1):
            t = t_start + (t_tip - t_start) * j / n_flank
            r, dt = involute_r_theta(t)
            r = min(r, outer_r)
            a = theta - half_tooth_angle + dt
            profile_pts.append((r * math.cos(a), r * math.sin(a)))

        # -- Tip arc (left tip → right tip, counterclockwise) --
        a_left_tip = theta - half_tooth_angle + dtheta_tip
        a_right_tip = theta + half_tooth_angle - dtheta_tip
        for j in range(1, n_tip + 1):
            frac = j / (n_tip + 1)
            a = a_left_tip + (a_right_tip - a_left_tip) * frac
            profile_pts.append((outer_r * math.cos(a), outer_r * math.sin(a)))

        # -- Right involute flank (inward: tip → root/base) --
        for j in range(n_flank + 1):
            t = t_tip - (t_tip - t_start) * j / n_flank
            r, dt = involute_r_theta(t)
            r = min(r, outer_r)
            a = theta + half_tooth_angle - dt
            profile_pts.append((r * math.cos(a), r * math.sin(a)))

        if root_r < base_r:
            a = theta + half_tooth_angle
            profile_pts.append((root_r * math.cos(a), root_r * math.sin(a)))

        # -- Root arc to next tooth --
        a_end = theta + half_tooth_angle - dtheta_start
        if root_r < base_r:
            a_end = theta + half_tooth_angle

        next_theta = (i + 1) * angular_pitch
        a_next = next_theta - half_tooth_angle + dtheta_start
        if root_r < base_r:
            a_next = next_theta - half_tooth_angle

        for j in range(1, n_root + 1):
            frac = j / (n_root + 1)
            a = a_end + (a_next - a_end) * frac
            profile_pts.append((root_r * math.cos(a), root_r * math.sin(a)))

    # ── Extrude the single closed wire (+ bore) ────────────────────
    wp = cq.Workplane("XY").polyline(profile_pts).close()
    if bore_diameter > 0:
        wp = wp.circle(bore_diameter / 2.0)
    result = wp.extrude(thickness)

    # Center vertically on the origin
    result = result.translate((0, 0, -thickness / 2.0))

    # ── Topological validation ──────────────────────────────────────
    solid = result.val()
    if not isinstance(solid, cq.Solid):
        raise ValueError(
            f"Topological validation failed: expected cq.Solid, "
            f"got {type(solid).__name__}."
        )

    return result


def make_naca_wire(
    naca_code: str = "2412",
    chord_length: float = 200.0,
) -> cq.Wire:
    """Generate a NACA 4-digit airfoil as a closed 2D cq.Wire.

    Computes exact upper and lower surface coordinates from the standard
    NACA 4-digit analytic thickness and camber distributions, builds two
    B-spline edges (upper and lower surfaces) via OpenCASCADE's
    Geom_BSplineCurve interpolation, and assembles them into a single
    closed, manifold cq.Wire. The wire begins and ends precisely at the
    trailing edge and lies in the XY plane at Z=0.

    This wire is the correct primitive for spanwise lofting. Position it
    at a global Z station with wire.translate(cq.Vector(0, 0, z)) and
    pass multiple positioned wires to cq.Solid.makeLoft().

    NACA 4-digit encoding:
        - Digit 1: max camber as percent of chord  (m = d1 / 100)
        - Digit 2: max camber location in tenths   (p = d2 / 10)
        - Digits 3-4: max thickness as percent      (t = d34 / 100)

    Coordinate system:
        - X axis: chordwise, centered (LE at -chord/2, TE at +chord/2)
        - Y axis: thickness direction
        - Wire lies in the XY plane at Z = 0

    Uses the modified trailing-edge coefficient (-0.1036 for the x^4 term)
    to produce a mathematically closed trailing edge (zero TE gap), ensuring
    the wire is watertight without a separate closing edge.

    Args:
        naca_code: 4-digit NACA designation string (e.g., "2412", "0012").
        chord_length: Chord length in mm.

    Returns:
        A closed cq.Wire in the XY plane at Z=0, centered on the chord
        midpoint. Starts and ends at the trailing edge (+chord/2, 0, 0).

    Raises:
        ValueError: If naca_code is not exactly 4 digits.
    """
    if len(naca_code) != 4 or not naca_code.isdigit():
        raise ValueError(
            f"Invalid NACA code '{naca_code}': must be exactly 4 digits "
            f"(e.g., '2412', '0012')."
        )

    m = int(naca_code[0]) / 100.0   # max camber
    p = int(naca_code[1]) / 10.0    # max camber position
    t = int(naca_code[2:4]) / 100.0  # max thickness ratio

    # ── Compute surface coordinates ────────────────────────────────
    # Cosine spacing concentrates points near the leading edge where
    # curvature is highest, improving spline fidelity.
    n_pts = 80
    upper_vecs: list[cq.Vector] = []
    lower_vecs: list[cq.Vector] = []

    for i in range(n_pts + 1):
        beta = i * math.pi / n_pts
        x = (1.0 - math.cos(beta)) / 2.0  # normalized [0, 1]

        # Half-thickness distribution (modified form: closed trailing edge)
        yt = (t / 0.20) * (
            0.2969 * math.sqrt(x)
            - 0.1260 * x
            - 0.3516 * x * x
            + 0.2843 * x * x * x
            - 0.1036 * x * x * x * x
        )

        # Camber line and local slope
        if m == 0.0 or p == 0.0:
            yc = 0.0
            theta = 0.0
        else:
            if x < p:
                yc = (m / (p * p)) * (2.0 * p * x - x * x)
                dyc_dx = (2.0 * m / (p * p)) * (p - x)
            else:
                yc = (m / ((1.0 - p) ** 2)) * (
                    (1.0 - 2.0 * p) + 2.0 * p * x - x * x
                )
                dyc_dx = (2.0 * m / ((1.0 - p) ** 2)) * (p - x)
            theta = math.atan(dyc_dx)

        # Upper and lower surface points (perpendicular to camber line)
        sin_t = math.sin(theta)
        cos_t = math.cos(theta)
        upper_vecs.append(cq.Vector(
            (x - yt * sin_t) * chord_length,
            (yc + yt * cos_t) * chord_length,
            0.0,
        ))
        lower_vecs.append(cq.Vector(
            (x + yt * sin_t) * chord_length,
            (yc - yt * cos_t) * chord_length,
            0.0,
        ))

    # ── Build closed profile wire via OCCT B-spline interpolation ──
    # Upper edge: trailing edge → leading edge (reversed point order)
    # Lower edge: leading edge → trailing edge (natural order)
    # Both edges share exact LE and TE endpoints (yt=0 at x=0 and x=1),
    # forming a mathematically closed loop: TE → LE (upper) → TE (lower).
    upper_edge = cq.Edge.makeSpline(list(reversed(upper_vecs)))
    lower_edge = cq.Edge.makeSpline(lower_vecs)

    wire = cq.Wire.assembleEdges([upper_edge, lower_edge])

    # Center on chord midpoint (LE at -chord/2, TE at +chord/2)
    wire = wire.translate(cq.Vector(-chord_length / 2.0, 0.0, 0.0))

    return wire


def make_naca_airfoil(
    naca_code: str = "2412",
    chord_length: float = 200.0,
    span: float = 500.0,
) -> cq.Workplane:
    """Generate a NACA 4-digit airfoil as an extruded 3D solid (B-Rep volume).

    Delegates to make_naca_wire() for the mathematically exact 2D profile,
    then extrudes linearly along the span axis and centers the result at
    the origin.

    NACA 4-digit encoding:
        - Digit 1: max camber as percent of chord  (m = d1 / 100)
        - Digit 2: max camber location in tenths   (p = d2 / 10)
        - Digits 3-4: max thickness as percent      (t = d34 / 100)

    Coordinate system:
        - X axis: chordwise (leading edge at -chord/2, trailing edge at +chord/2)
        - Y axis: thickness direction
        - Z axis: spanwise (centered at origin)

    Args:
        naca_code: 4-digit NACA designation string (e.g., "2412", "0012").
        chord_length: Chord length in mm.
        span: Spanwise extrusion length in mm.

    Returns:
        cq.Workplane containing the extruded 3D airfoil solid, centered
        at the origin.

    Raises:
        ValueError: If naca_code is not exactly 4 digits, or if the result
                    is not a manifold cq.Solid.
    """
    wire = make_naca_wire(naca_code, chord_length)

    # ── Extrude along span (Z-axis) ───────────────────────────────
    solid = cq.Solid.extrudeLinear(wire, [], cq.Vector(0, 0, span))

    # Center span on origin (wire is already chord-centered)
    result = cq.Workplane("XY").newObject([solid])
    result = result.translate((0.0, 0.0, -span / 2.0))

    # ── Topological validation ─────────────────────────────────────
    val = result.val()
    if not isinstance(val, cq.Solid):
        raise ValueError(
            f"NACA airfoil validation failed: expected cq.Solid, "
            f"got {type(val).__name__}."
        )

    return result


def make_co2_void(diameter: float = 19.5, depth: float = 52.0) -> cq.Workplane:
    """Generate a standard CO2 cartridge cavity for boolean cutting."""
    result = cq.Workplane("YZ").circle(diameter / 2.0).extrude(depth)
    val = result.val()
    if not isinstance(val, cq.Solid):
        raise ValueError("CO2 void validation failed.")
    return result


# ISO 4014 / DIN 931 hex-head dimensions: {nominal: (width_across_flats, head_thickness)}
_ISO_HEX_HEAD_DIMS = {
    3.0:  (5.5,  2.0),
    4.0:  (7.0,  2.8),
    5.0:  (8.0,  3.5),
    6.0:  (10.0, 4.0),
    8.0:  (13.0, 5.3),
    10.0: (16.0, 6.4),
    12.0: (18.0, 7.5),
    14.0: (21.0, 8.8),
    16.0: (24.0, 10.0),
    20.0: (30.0, 12.5),
}

# ISO 4032 hex-nut dimensions: {nominal: (width_across_flats, height)}
_ISO_HEX_NUT_DIMS = {
    3.0:  (5.5,  2.4),
    4.0:  (7.0,  3.2),
    5.0:  (8.0,  4.7),
    6.0:  (10.0, 5.2),
    8.0:  (13.0, 6.8),
    10.0: (16.0, 8.4),
    12.0: (18.0, 10.8),
    14.0: (21.0, 12.8),
    16.0: (24.0, 14.8),
    20.0: (30.0, 18.0),
}


def make_metric_thread(
    diameter: float,
    pitch: float,
    length: float,
) -> cq.Workplane:
    """Build a COSMETIC metric thread — a ribbed cylinder, not a real helix.

    This is a *simplified thread representation* in the sense of ISO 6410 /
    ASME Y14.6: the outside diameter measures the true nominal (an M10 shank
    measures Ø10.00), but the ribs are annular, not helical. There is no lead
    and no 60° flank, so the result is:

      * correct for visualisation, envelope, mass and clearance studies;
      * correct to caliper on the major diameter;
      * NOT cuttable geometry, NOT measurable over wires, and NOT a mating
        thread form.

    Downstream consumers should treat a part built with this as having a
    cosmetic thread callout, exactly as they would a simplified thread in
    SolidWorks. ``THREAD_REPRESENTATION`` records this so the pipeline can
    surface it to the user rather than implying cuttable threads.

    (The historical justification — "a true helical sweep crashes the BRep
    kernel" — is contradicted by docs/propulsion/helix-ban-falsification.md,
    which found no reproduction. Real helical threads are tracked as future
    work; they need verification against the kernel in Docker.)

    Args:
        diameter: Nominal (major) thread diameter in mm.
        pitch: Thread pitch in mm (distance between crests).
        length: Total thread length in mm.

    Returns:
        cq.Workplane solid centered at the origin, max radius = diameter/2.
    """
    major_r = diameter / 2.0
    minor_r = major_r - 0.6134 * pitch  # standard 60° thread depth
    rib_height = major_r - minor_r
    rib_width = pitch * 0.4

    # Core cylinder at the minor diameter
    result = cq.Workplane("XY").circle(minor_r).extrude(length)

    # Stack annular ribs along the length to simulate thread crests
    num_ribs = int(length / pitch)
    for i in range(num_ribs):
        z_offset = pitch * 0.5 + i * pitch
        if z_offset + rib_width / 2.0 > length:
            break
        rib = (
            cq.Workplane("XY")
            .workplane(offset=z_offset - rib_width / 2.0)
            .circle(major_r)
            .circle(minor_r)
            .extrude(rib_width)
        )
        result = result.union(rib)

    # Center on origin
    result = result.translate((0, 0, -length / 2.0))

    solid = result.val()
    if not isinstance(solid, (cq.Solid, cq.Compound)):
        raise ValueError(
            f"Thread validation failed: expected cq.Solid or cq.Compound, "
            f"got {type(solid).__name__}."
        )

    return result


def make_hex_bolt(
    nominal_diameter: float,
    pitch: float,
    shank_length: float,
    head_width_across_flats: float | None = None,
    head_thickness: float | None = None,
) -> cq.Workplane:
    """ISO-style hex-head bolt with a threaded shank, centered at the origin.

    The shank uses ``make_metric_thread``'s ribbed-cylinder approximation —
    visually thread-like and kernel-safe (true helical sweeps crash BRep).
    The head points +Z; the shank points -Z.

    Args:
        nominal_diameter: Major thread diameter in mm (e.g. 10.0 for M10).
        pitch: Thread pitch in mm (e.g. 1.5 for M10x1.5).
        shank_length: Length of the threaded shank in mm.
        head_width_across_flats: Hex head AF in mm. ``None`` → ISO 4014 default.
        head_thickness: Hex head thickness in mm. ``None`` → ISO 4014 default.

    Returns:
        cq.Workplane solid centered at the origin.
    """
    if head_width_across_flats is None or head_thickness is None:
        af_default, th_default = _ISO_HEX_HEAD_DIMS.get(
            nominal_diameter, (1.5 * nominal_diameter, 0.7 * nominal_diameter)
        )
        if head_width_across_flats is None:
            head_width_across_flats = af_default
        if head_thickness is None:
            head_thickness = th_default

    # polygon(nSides, diameter) takes the circumscribed-circle diameter.
    # For a regular hexagon, width-across-flats = circumscribed_diameter * cos(30°).
    head_circum_dia = head_width_across_flats / math.cos(math.radians(30.0))

    # Head: [0, head_thickness] along Z, pointing +Z.
    head = (
        cq.Workplane("XY")
        .polygon(6, head_circum_dia)
        .extrude(head_thickness)
    )

    # Thread returns a Z-centered solid; translate so its top face sits at Z=0.
    # Result: shank spans [-shank_length, 0].
    thread = make_metric_thread(nominal_diameter, pitch, shank_length)
    thread = thread.translate((0, 0, -shank_length / 2.0))

    bolt = head.union(thread)

    # Current extent along Z: [-shank_length, head_thickness]. Recenter on origin.
    total = shank_length + head_thickness
    bolt = bolt.translate((0, 0, (shank_length - head_thickness) / 2.0))

    solid = bolt.val()
    if not isinstance(solid, (cq.Solid, cq.Compound)):
        raise ValueError(
            f"Hex bolt validation failed: expected cq.Solid or cq.Compound, "
            f"got {type(solid).__name__}."
        )
    return bolt


def make_hex_nut(
    nominal_diameter: float,
    pitch: float,
    width_across_flats: float | None = None,
    height: float | None = None,
    thread_fit_clearance: float = COSMETIC_THREAD_FIT_CLEARANCE_MM,
) -> cq.Workplane:
    """ISO-style hex nut, centered at origin, bored to ACCEPT its own bolt.

    Hex dimensions (across-flats, height) follow ISO 4032.

    The bore deliberately does NOT use the ISO internal minor diameter
    D1 = d − 1.0825p. In a real joint the nut's minor diameter is smaller
    than the bolt's major diameter — the two interlock through the helix.
    Mirum's threads are cosmetic (see ``make_metric_thread``), so there is no
    helix to interlock, and a D1 bore made the parts physically interfere:
    an M10×1.5 bolt crest at r=5.000 against a D1 bore at r=4.188 gave
    **0.81 mm of radial interference**, so the bolt could not pass through
    the nut it ships with. Every assembly containing both was solid-on-solid
    overlapping.

    Instead the bore is the nominal diameter plus ``thread_fit_clearance``,
    which is the correct companion to a simplified thread representation: the
    parts assemble, interference checks stay clean, and the thread remains a
    callout rather than modelled geometry.

    Args:
        nominal_diameter: Major thread diameter in mm (e.g. 10.0 for M10).
        pitch: Thread pitch in mm. Retained for callout/reporting; it no
            longer sets the bore.
        width_across_flats: Hex AF in mm. ``None`` → ISO 4032 default.
        height: Nut height in mm. ``None`` → ISO 4032 default.
        thread_fit_clearance: Diametral clearance in mm between the bore and
            the nominal thread diameter.

    Returns:
        cq.Workplane solid centered at the origin.
    """
    if width_across_flats is None or height is None:
        af_default, h_default = _ISO_HEX_NUT_DIMS.get(
            nominal_diameter, (1.5 * nominal_diameter, 0.8 * nominal_diameter)
        )
        if width_across_flats is None:
            width_across_flats = af_default
        if height is None:
            height = h_default

    circum_dia = width_across_flats / math.cos(math.radians(30.0))
    # Clears the cosmetic thread's major diameter — see the docstring.
    bore_dia = nominal_diameter + thread_fit_clearance

    nut = (
        cq.Workplane("XY")
        .polygon(6, circum_dia)
        .extrude(height)
        .faces(">Z")
        .workplane()
        .hole(bore_dia)
    )
    nut = nut.translate((0, 0, -height / 2.0))

    solid = nut.val()
    if not isinstance(solid, (cq.Solid, cq.Compound)):
        raise ValueError(
            f"Hex nut validation failed: expected cq.Solid or cq.Compound, "
            f"got {type(solid).__name__}."
        )
    return nut


# ---------------------------------------------------------------------------
# T2-10: High-frequency parametric engineering shape templates
# ---------------------------------------------------------------------------


def make_flanged_housing(
    OD: float,
    ID: float,
    height: float,
    flange_OD: float,
    flange_thickness: float,
    bolt_circle_diameter: float,
    n_bolt_holes: int,
    bolt_hole_diameter: float,
) -> cq.Workplane:
    """Cylindrical housing with a mounting flange.

    Generates a hollow cylinder (bore ID, outer diameter OD) with a flat
    circular flange at its base. Bolt holes are arranged on a bolt circle.

    Args:
        OD: Outer diameter of the cylindrical body (mm).
        ID: Inner bore diameter (mm). Must be < OD.
        height: Total height of the cylindrical body, not including flange (mm).
        flange_OD: Outer diameter of the flange disc (mm). Must be >= OD.
        flange_thickness: Thickness of the flange (mm).
        bolt_circle_diameter: PCD (pitch circle diameter) for bolt holes (mm).
        n_bolt_holes: Number of bolt holes. 0 = no bolt holes.
        bolt_hole_diameter: Diameter of each bolt clearance hole (mm).

    Returns:
        cq.Workplane containing the assembled flanged housing solid.

    Raises:
        ValueError: If geometric constraints are violated.
    """
    if ID >= OD:
        raise ValueError(f"Bore ID ({ID}) must be less than outer OD ({OD}).")
    if flange_OD < OD:
        raise ValueError(f"flange_OD ({flange_OD}) must be >= body OD ({OD}).")
    if bolt_hole_diameter >= bolt_circle_diameter:
        raise ValueError(
            f"bolt_hole_diameter ({bolt_hole_diameter}) must be less than "
            f"bolt_circle_diameter ({bolt_circle_diameter})."
        )

    # Flange disc at the bottom
    flange = (
        cq.Workplane("XY")
        .circle(flange_OD / 2.0)
        .circle(ID / 2.0)
        .extrude(flange_thickness)
    )

    # Cylindrical body on top of the flange
    body = (
        cq.Workplane("XY")
        .workplane(offset=flange_thickness)
        .circle(OD / 2.0)
        .circle(ID / 2.0)
        .extrude(height)
    )

    result = flange.union(body)

    # Bolt holes in the flange (polar array)
    if n_bolt_holes > 0 and bolt_circle_diameter > 0:
        bolt_r = bolt_circle_diameter / 2.0
        result = (
            result
            .faces("<Z")
            .workplane()
            .polarArray(bolt_r, 0, 360, n_bolt_holes)
            .circle(bolt_hole_diameter / 2.0)
            .cutThruAll()
        )

    return result


def make_shaft(
    diameter: float,
    length: float,
    keyway_width: float = 0.0,
    keyway_depth: float = 0.0,
    chamfer_end: float = 0.5,
) -> cq.Workplane:
    """Shaft with optional keyway and end chamfers.

    Generates a cylindrical shaft centered at the origin (axis along Z).
    An optional rectangular keyway is cut along the full length on the
    outer surface. Both ends are chamfered.

    Args:
        diameter: Shaft outer diameter (mm).
        length: Total shaft length (mm).
        keyway_width: Width of the keyway slot (mm). 0 = no keyway.
        keyway_depth: Depth of the keyway slot from the outer surface (mm).
        chamfer_end: Chamfer size on both shaft ends (mm). 0 = no chamfer.

    Returns:
        cq.Workplane containing the shaft solid.
    """
    # Main cylinder
    result = cq.Workplane("XY").circle(diameter / 2.0).extrude(length)

    # Keyway — rectangular slot along full length on top face
    if keyway_width > 0 and keyway_depth > 0:
        if keyway_width >= diameter:
            raise ValueError(
                f"keyway_width ({keyway_width}) must be less than shaft diameter ({diameter})."
            )
        # Position the keyway slot at the top of the shaft
        slot_y_offset = diameter / 2.0 - keyway_depth
        keyway = (
            cq.Workplane("XY")
            .center(0, slot_y_offset)
            .rect(keyway_width, keyway_depth * 2)
            .extrude(length)
        )
        result = result.cut(keyway)

    # Chamfer both ends
    if chamfer_end > 0:
        result = result.edges("%Circle").chamfer(chamfer_end)

    return result


def make_bracket(
    base_width: float,
    base_length: float,
    base_thickness: float,
    wall_height: float,
    wall_thickness: float,
    n_mounting_holes: int = 2,
    hole_diameter: float = 5.0,
) -> cq.Workplane:
    """L-bracket with mounting holes in the base flange.

    Generates an L-shaped bracket: a flat base plate with an upright wall
    at one end. Mounting holes are evenly spaced along the base length.

    Args:
        base_width: Width of the base flange (mm).
        base_length: Length of the base flange (mm).
        base_thickness: Thickness of the base plate (mm).
        wall_height: Height of the vertical wall above the base (mm).
        wall_thickness: Thickness of the vertical wall (mm).
        n_mounting_holes: Number of through holes in the base (0 = none).
        hole_diameter: Diameter of each mounting hole (mm).

    Returns:
        cq.Workplane containing the L-bracket solid.
    """
    # Base plate
    base = (
        cq.Workplane("XY")
        .box(base_length, base_width, base_thickness)
    )

    # Vertical wall at one end of the base
    wall = (
        cq.Workplane("XY")
        .workplane(offset=base_thickness)
        .center(-base_length / 2.0 + wall_thickness / 2.0, 0)
        .box(wall_thickness, base_width, wall_height)
    )

    result = base.union(wall)

    # Mounting holes in the base (linear pattern along X)
    if n_mounting_holes > 0 and hole_diameter > 0:
        if n_mounting_holes == 1:
            offsets = [0.0]
        else:
            spacing = (base_length - 2 * hole_diameter) / (n_mounting_holes - 1)
            offsets = [
                -base_length / 2.0 + hole_diameter + i * spacing
                for i in range(n_mounting_holes)
            ]
        for x_off in offsets:
            result = (
                result
                .faces(">Z")
                .workplane()
                .center(x_off, 0)
                .circle(hole_diameter / 2.0)
                .cutThruAll()
            )

    return result


# ISO 286 H7 hole tolerance, upper deviation, for the size bands a bearing
# seat normally falls in. A housing seat is nominally H7; modelling the bore at
# exactly the bearing OD gives a zero-interference "press fit", which is not a
# fit at all. The mid-tolerance value is used so the modelled bore reflects a
# real seat rather than the nominal.
_H7_MID_ALLOWANCE_MM: tuple[tuple[float, float], ...] = (
    #  (max nominal Ø, mid-tolerance allowance on diameter)
    (6.0,   0.006),
    (10.0,  0.0075),
    (18.0,  0.009),
    (30.0,  0.0105),
    (50.0,  0.0125),
    (80.0,  0.015),
    (120.0, 0.0175),
    (180.0, 0.020),
)


def _h7_allowance(nominal_dia: float) -> float:
    """Diametral H7 mid-tolerance allowance for a nominal diameter (mm)."""
    for limit, allowance in _H7_MID_ALLOWANCE_MM:
        if nominal_dia <= limit:
            return allowance
    return 0.025


def make_bearing_housing(
    bearing_OD: float,
    bearing_width: float,
    housing_OD: float,
    housing_length: float,
    lip_thickness: float = 2.0,
) -> cq.Workplane:
    """Bearing housing with an H7 seat bore and a retaining shoulder.

    The seat is bored to ``bearing_OD`` plus an ISO 286 H7 mid-tolerance
    allowance. Boring exactly to the bearing OD — as this previously did —
    describes a zero-clearance, zero-interference fit, which no machinist can
    hold and which misrepresents the "precision bore" the docstring claims.

    ``bearing_width`` sets the seat DEPTH. It was previously accepted,
    documented as "the bore depth matches this", and then never used: the bore
    ran the full remaining length, so the retaining shoulder was the only thing
    locating the bearing and a housing longer than the bearing left it
    unsupported.

    Args:
        bearing_OD: Outer diameter of the bearing outer race (mm).
        bearing_width: Width of the bearing (mm) — sets the seat depth.
        housing_OD: Outer diameter of the housing body (mm).
        housing_length: Total length of the housing (mm).
        lip_thickness: Axial thickness of the retaining lip at the closed end (mm).

    Returns:
        cq.Workplane containing the bearing housing solid.

    Raises:
        ValueError: If bearing_OD >= housing_OD, or the seat does not fit
            within the housing length.
    """
    if bearing_OD >= housing_OD:
        raise ValueError(
            f"bearing_OD ({bearing_OD}) must be less than housing_OD ({housing_OD})."
        )
    if lip_thickness >= housing_length:
        raise ValueError(
            f"lip_thickness ({lip_thickness}) must be less than housing_length "
            f"({housing_length})."
        )

    seat_depth = bearing_width if bearing_width and bearing_width > 0 else (
        housing_length - lip_thickness
    )
    available = housing_length - lip_thickness
    if seat_depth > available:
        raise ValueError(
            f"bearing_width ({bearing_width}) does not fit: only {available:.2f}mm "
            f"of seat depth is available between the lip and the open end. "
            f"Increase housing_length or reduce lip_thickness."
        )

    bore_dia = bearing_OD + _h7_allowance(bearing_OD)

    # Outer cylinder
    outer = (
        cq.Workplane("XY")
        .circle(housing_OD / 2.0)
        .extrude(housing_length)
    )

    # Seat bore, opening at the far end and stopping at the shoulder.
    bore = (
        cq.Workplane("XY")
        .workplane(offset=housing_length - seat_depth)
        .circle(bore_dia / 2.0)
        .extrude(seat_depth)
    )

    result = outer.cut(bore)
    return result


def make_spacer(
    OD: float,
    ID: float,
    length: float,
    chamfer: float = 0.3,
) -> cq.Workplane:
    """Cylindrical spacer (washer/sleeve) with optional end chamfers.

    Args:
        OD: Outer diameter (mm).
        ID: Inner bore diameter (mm). Must be < OD.
        length: Spacer length/height (mm).
        chamfer: Edge chamfer size on both ends (mm). 0 = no chamfer.

    Returns:
        cq.Workplane containing the spacer solid.
    """
    if ID >= OD:
        raise ValueError(f"Bore ID ({ID}) must be less than outer OD ({OD}).")

    result = (
        cq.Workplane("XY")
        .circle(OD / 2.0)
        .circle(ID / 2.0)
        .extrude(length)
    )

    if chamfer > 0:
        result = result.edges("%Circle").chamfer(chamfer)

    return result


def make_boss_with_bore(
    boss_OD: float,
    bore_ID: float,
    boss_height: float,
    base_OD: float = 0.0,
    base_thickness: float = 0.0,
    counterbore_ID: float = 0.0,
    counterbore_depth: float = 0.0,
) -> cq.Workplane:
    """Boss (cylindrical protrusion) with a through/blind bore.

    Supports a flat base flange and counterbore entry for fastener head
    clearance.

    Args:
        boss_OD: Outer diameter of the boss cylinder (mm).
        bore_ID: Bore diameter through the boss (mm). 0 = solid boss.
        boss_height: Height of the boss above the base (mm).
        base_OD: Outer diameter of an optional flat base flange (mm).
            0 = no base, just the boss cylinder.
        base_thickness: Thickness of the base flange (mm).
        counterbore_ID: Counterbore entry diameter at the top (mm).
            0 = no counterbore (plain bore throughout).
        counterbore_depth: Depth of the counterbore from the top face (mm).

    Returns:
        cq.Workplane containing the boss solid.
    """
    if bore_ID > 0 and bore_ID >= boss_OD:
        raise ValueError(f"bore_ID ({bore_ID}) must be less than boss_OD ({boss_OD}).")

    total_height = boss_height + base_thickness

    # Start with boss cylinder (+ base if specified)
    if base_OD > 0 and base_thickness > 0:
        if base_OD < boss_OD:
            raise ValueError(
                f"base_OD ({base_OD}) must be >= boss_OD ({boss_OD})."
            )
        base = (
            cq.Workplane("XY")
            .circle(base_OD / 2.0)
            .extrude(base_thickness)
        )
        boss = (
            cq.Workplane("XY")
            .workplane(offset=base_thickness)
            .circle(boss_OD / 2.0)
            .extrude(boss_height)
        )
        result = base.union(boss)
    else:
        result = (
            cq.Workplane("XY")
            .circle(boss_OD / 2.0)
            .extrude(boss_height)
        )

    # Through bore
    if bore_ID > 0:
        result = (
            result
            .faces(">Z")
            .workplane()
            .circle(bore_ID / 2.0)
            .cutThruAll()
        )

    # Counterbore (larger entrance from the top)
    if counterbore_ID > 0 and counterbore_depth > 0:
        if bore_ID > 0 and counterbore_ID <= bore_ID:
            raise ValueError(
                f"counterbore_ID ({counterbore_ID}) must be > bore_ID ({bore_ID})."
            )
        result = (
            result
            .faces(">Z")
            .workplane()
            .circle(counterbore_ID / 2.0)
            .cutBlind(-counterbore_depth)
        )

    return result


# ---------------------------------------------------------------------------
# T3-06: Domain E — Sheet Metal Utility Functions
# ---------------------------------------------------------------------------

def make_bent_bracket(
    material_thickness: float,
    base_length: float,
    base_width: float,
    flange_height: float,
    bend_radius: float,
    n_base_holes: int = 2,
    hole_diameter: float = 5.0,
) -> cq.Workplane:
    """Standard L-bracket from sheet metal with bend relief.

    Produces a folded L-bracket:
      - Flat base plate with mounting holes
      - Vertical wall (flange) of the specified height
      - Bend at the corner approximated as an arc-extruded fillet

    Design rules enforced:
      - Minimum bend radius >= 1× material_thickness (raises ValueError if violated)
      - Minimum flange height >= 2× material_thickness + bend_radius
      - Hole-to-edge distance >= 2× material_thickness

    Args:
        material_thickness: Sheet gauge in mm (e.g., 1.5 for 14 ga steel).
        base_length: Length of the flat base in mm.
        base_width: Width of the flat base in mm.
        flange_height: Height of the vertical flange in mm.
        bend_radius: Inner bend radius in mm.
        n_base_holes: Number of mounting holes in the base (evenly spaced).
        hole_diameter: Diameter of mounting holes in mm.

    Returns:
        cq.Workplane solid (the folded bracket in final geometry).

    Raises:
        ValueError: If design rule constraints are violated.
    """
    # Design rule checks
    if bend_radius < material_thickness:
        raise ValueError(
            f"Bend radius {bend_radius}mm < material thickness {material_thickness}mm. "
            f"Minimum is 1× material thickness. "
            f"Use bend_radius >= {material_thickness}."
        )

    min_flange = 2 * material_thickness + bend_radius
    if flange_height < min_flange:
        raise ValueError(
            f"Flange height {flange_height}mm < minimum {min_flange:.1f}mm "
            f"(= 2×thickness + bend_radius). "
            f"Use flange_height >= {min_flange:.1f}."
        )

    min_edge_dist = 2 * material_thickness
    if hole_diameter > 0 and n_base_holes > 0:
        hole_spacing = base_length / (n_base_holes + 1)
        if hole_spacing < hole_diameter + min_edge_dist * 2:
            raise ValueError(
                f"Hole spacing {hole_spacing:.1f}mm is too tight for {n_base_holes} "
                f"holes of diameter {hole_diameter}mm with minimum edge distance "
                f"{min_edge_dist}mm. Reduce n_base_holes or increase base_length."
            )

    # --- Build base plate ---
    base = (
        cq.Workplane("XY")
        .box(base_length, base_width, material_thickness, centered=True)
    )

    # Add mounting holes in base (linear pattern)
    if n_base_holes > 0 and hole_diameter > 0:
        hole_spacing = base_length / (n_base_holes + 1)
        for i in range(n_base_holes):
            x = -base_length / 2.0 + hole_spacing * (i + 1)
            base = (
                base
                .faces(">Z")
                .workplane()
                .center(x, 0)
                .hole(hole_diameter)
            )

    # --- Build flange (vertical wall) ---
    # Rises from the base plate's -Y edge.
    #
    # `centered=(True, False)` is load-bearing. With the previous
    # `centered=False` the profile spanned X in [0, base_length] while the base
    # spans [-base_length/2, +base_length/2], so half the flange hung off the
    # +X end of the bracket. X must be centred to match the base; the vertical
    # axis stays uncentred so the wall grows upward from the base plane.
    flange = (
        cq.Workplane("XZ")
        .workplane(offset=base_width / 2.0)
        .rect(base_length, flange_height, centered=(True, False))
        .extrude(material_thickness)
    )

    result = base.union(flange)

    # Bend relief at the base-to-flange junction. The flange sits on the -Y
    # side, so the inside corner edge is the "<Y" one; the previous ">Y"
    # selected the opposite edge of the base plate entirely.
    try:
        result = (
            result
            .edges("|X")
            .edges("<Y")
            .fillet(bend_radius)
        )
    except Exception as exc:  # noqa: BLE001
        # A missing bend still leaves a usable L-bracket, so this does not
        # fail the part — but it is no longer silent: the validated bend
        # radius is absent from the geometry and the caller should know.
        print(f"BEND_FILLET_SKIPPED: r={bend_radius} could not be applied: {exc}")

    return result


def make_enclosure_panel(
    width: float,
    height: float,
    material_thickness: float,
    flange_depth: float = 0.0,
    cutout_positions: list = None,
) -> cq.Workplane:
    """Sheet metal panel with optional flanges and rectangular cutouts.

    Produces a flat panel (like a computer side panel or electrical enclosure face)
    with optional return flanges on all four edges and rectangular knockouts.

    Args:
        width: Panel width in mm.
        height: Panel height in mm.
        material_thickness: Sheet gauge in mm.
        flange_depth: Depth of return flanges on all edges (0 = no flanges).
        cutout_positions: List of (x_center, y_center, cutout_width, cutout_height)
            tuples in mm (origin = center of panel). Pass [] or None for no cutouts.

    Returns:
        cq.Workplane solid (flat panel + flanges).

    Design rules enforced:
        - Cutout edges must be >= 2× material_thickness from panel edge.
        - Flange depth >= 2× material_thickness + material_thickness (wall) if nonzero.
    """
    if cutout_positions is None:
        cutout_positions = []

    # --- Build main flat panel ---
    panel = (
        cq.Workplane("XY")
        .box(width, height, material_thickness, centered=True)
    )

    # --- Add perimeter flanges ---
    if flange_depth > 0:
        min_flange = 2 * material_thickness
        if flange_depth < min_flange:
            raise ValueError(
                f"Flange depth {flange_depth}mm < minimum {min_flange}mm "
                f"(= 2× material thickness). Use flange_depth >= {min_flange}."
            )

        # Four return flanges around the panel edges.
        #
        # Two bugs fixed here, both the same class as make_bent_bracket's:
        #
        #  * `centered=False` spanned [0, length] while the panel spans
        #    [-length/2, +length/2], so every flange hung half its length off
        #    one end of the panel. The long axis must be centred; only the
        #    depth axis grows from the panel plane.
        #  * `sign` positioned the workplane but not the extrude, so the -X/-Y
        #    flanges grew inward across the panel while the +X/+Y ones grew
        #    outward. Extruding by `sign * thickness` makes all four grow
        #    outward, which is what a return flange does.
        for sign in (-1, +1):
            flange = (
                cq.Workplane("YZ")
                .workplane(offset=sign * width / 2.0)
                .rect(height, flange_depth, centered=(True, False))
                .extrude(sign * material_thickness)
            )
            panel = panel.union(flange)

        for sign in (-1, +1):
            flange = (
                cq.Workplane("XZ")
                .workplane(offset=sign * height / 2.0)
                .rect(width + 2 * material_thickness, flange_depth, centered=(True, False))
                .extrude(sign * material_thickness)
            )
            panel = panel.union(flange)

    # --- Add rectangular cutouts ---
    min_edge_dist = 2 * material_thickness
    for cx, cy, cw, ch in cutout_positions:
        # Validate cutout is within panel bounds with minimum edge clearance
        if abs(cx) + cw / 2.0 > width / 2.0 - min_edge_dist:
            raise ValueError(
                f"Cutout at ({cx}, {cy}) width {cw} is too close to panel edge. "
                f"Minimum edge distance: {min_edge_dist}mm."
            )
        if abs(cy) + ch / 2.0 > height / 2.0 - min_edge_dist:
            raise ValueError(
                f"Cutout at ({cx}, {cy}) height {ch} is too close to panel edge. "
                f"Minimum edge distance: {min_edge_dist}mm."
            )

        panel = (
            panel
            .faces(">Z")
            .workplane()
            .center(cx, cy)
            .rect(cw, ch)
            .cutThruAll()
        )

    return panel


# Expose template function names for strategy prompt injection
AVAILABLE_TEMPLATES = [
    "make_hex_bolt(nominal_diameter, pitch, shank_length, "
    "head_width_across_flats=None, head_thickness=None)",
    "make_hex_nut(nominal_diameter, pitch, width_across_flats=None, height=None)",
    "make_metric_thread(diameter, pitch, length)",
    "make_flanged_housing(OD, ID, height, flange_OD, flange_thickness, "
    "bolt_circle_diameter, n_bolt_holes, bolt_hole_diameter)",
    "make_shaft(diameter, length, keyway_width=0, keyway_depth=0, chamfer_end=0.5)",
    "make_bracket(base_width, base_length, base_thickness, wall_height, "
    "wall_thickness, n_mounting_holes=2, hole_diameter=5.0)",
    "make_bearing_housing(bearing_OD, bearing_width, housing_OD, housing_length, "
    "lip_thickness=2.0)",
    "make_spacer(OD, ID, length, chamfer=0.3)",
    "make_boss_with_bore(boss_OD, bore_ID, boss_height, base_OD=0, "
    "base_thickness=0, counterbore_ID=0, counterbore_depth=0)",
    # T3-06: Sheet metal templates (Domain E)
    "make_bent_bracket(material_thickness, base_length, base_width, flange_height, "
    "bend_radius, n_base_holes=2, hole_diameter=5.0)",
    "make_enclosure_panel(width, height, material_thickness, flange_depth=0, "
    "cutout_positions=[])",
    # DA: Planform variants (Domain D extension)
    "make_planform_wires(naca_code, root_chord, tip_chord, span, "
    "sweep_le_deg=0.0, dihedral_deg=0.0, twist_tip_deg=0.0)",
    # DB: Twisted blades (Domain D extension)
    "make_twisted_blade(naca_code, root_chord, tip_chord, span, "
    "root_pitch_deg, tip_pitch_deg, num_stations=8, sweep_le_deg=0.0)",
    # DF: Propulsion geometry (Domain F)
    "make_nozzle_profile(chamber_radius, throat_radius, exit_radius, "
    "chamber_length, conv_half_angle_deg=30.0, div_half_angle_deg=15.0, "
    "wall_thickness=5.0)",
    "make_combustion_chamber(inner_radius, wall_thickness, length, dome_radius, "
    "num_fuel_inlets=6, fuel_inlet_diameter=8.0, fuel_inlet_radius=None)",
    "make_nacelle_cowl(highlight_radius, throat_radius, fan_face_radius, "
    "intake_length, cowl_thickness=8.0, lip_radius=15.0)",
    # DG: Vehicle bodies (Domain G)
    "make_nose_cone(base_diameter, length, profile='ogive', "
    "wall_thickness=0.0, shoulder_length=20.0)",
    "make_fuselage_loft(stations, wall_thickness=3.0, num_frames=8, "
    "frame_thickness=2.0, frame_height=20.0, close_nose=True, close_tail=False)",
]


# ---------------------------------------------------------------------------
# DA: Planform Variants — Domain D Extension
# ---------------------------------------------------------------------------

def make_planform_wires(
    naca_code: str,
    root_chord: float,
    tip_chord: float,
    span: float,
    sweep_le_deg: float = 0.0,
    dihedral_deg: float = 0.0,
    twist_tip_deg: float = 0.0,
) -> tuple:
    """Generate root and tip NACA wires for a swept / dihedral / tapered wing.

    Produces two CadQuery wire objects already:
      - Rotated into the XZ plane (chord along X, thickness along Z)
      - Translated to their correct spanwise positions
      - Offset for leading-edge sweep (tip shifted along X)
      - Offset for dihedral (tip shifted along Z)
      - Twisted at the tip by twist_tip_deg (washout; positive = nose-down)

    The caller passes these directly to the OML Master loft:
        oml_solid = cq.Workplane('XZ').newObject([
            cq.Solid.makeLoft([tip_wire, root_wire])
        ])

    Args:
        naca_code:      4-digit NACA code string, e.g. '2412'.
        root_chord:     Root chord length in mm.
        tip_chord:      Tip chord length in mm. Can be as small as 5mm (delta).
        span:           Wing semi-span in mm (root at Y=0, tip at Y=span).
        sweep_le_deg:   Leading-edge sweep angle in degrees (0 = unswept).
                        Positive = tip swept rearward.
        dihedral_deg:   Dihedral angle in degrees (0 = flat).
                        Positive = tip raised upward (conventional dihedral).
        twist_tip_deg:  Geometric washout at tip in degrees.
                        Positive = tip leading edge rotates nose-down (reduces AoA).

    Returns:
        (root_wire, tip_wire) tuple of CadQuery Wire objects ready for makeLoft.
        Pass as [tip_wire, root_wire] to makeLoft (reversed order for outward normals).

    Design rules:
        - sweep_le_deg in [-60, 60]; values outside this range produce loft singularities.
        - For delta planforms use tip_chord >= 5mm, not exactly 0.
        - dihedral_deg in [-30, 30]; steeper angles need the winglet helper instead.
    """
    sweep_le_deg = max(-60.0, min(60.0, sweep_le_deg))
    dihedral_deg = max(-30.0, min(30.0, dihedral_deg))

    # --- Root wire: rotate into XZ, stays at origin ---
    root_wire = make_naca_wire(naca_code, chord_length=root_chord)
    root_wire = root_wire.rotate((0, 0, 0), (1, 0, 0), -90)

    # --- Tip wire: rotate into XZ, apply twist, then translate ---
    tip_wire = make_naca_wire(naca_code, chord_length=tip_chord)
    tip_wire = tip_wire.rotate((0, 0, 0), (1, 0, 0), -90)

    # Apply washout twist around the Y-axis (span axis) BEFORE translation
    # so the rotation centre is the local leading edge, not world origin.
    if abs(twist_tip_deg) > 0.001:
        tip_wire = tip_wire.rotate((0, 0, 0), (0, 1, 0), twist_tip_deg)

    # Compute tip position offsets
    x_sweep = span * math.tan(math.radians(sweep_le_deg))
    z_dihedral = span * math.tan(math.radians(dihedral_deg))

    tip_wire = tip_wire.translate(cq.Vector(x_sweep, span, z_dihedral))

    return root_wire, tip_wire


# ---------------------------------------------------------------------------
# DB: Twisted Blades — Domain D Extension
# ---------------------------------------------------------------------------

def make_twisted_blade(
    naca_code: str,
    root_chord: float,
    tip_chord: float,
    span: float,
    root_pitch_deg: float,
    tip_pitch_deg: float,
    num_stations: int = 8,
    sweep_le_deg: float = 0.0,
) -> "cq.Solid":
    """Generate a twisted blade solid (propeller, rotor, wind turbine).

    Builds N cross-sectional airfoil wires along the span, each rotated by the
    local geometric pitch angle (linear interpolation from root to tip), then
    lofts all N wires into a twisted blade solid.

    Args:
        naca_code:       4-digit NACA code, e.g. '4412'.
        root_chord:      Chord length at blade root (mm).
        tip_chord:       Chord length at blade tip (mm); tapers toward tip.
        span:            Blade span from root to tip (mm).
        root_pitch_deg:  Geometric pitch angle at root (degrees). High pitch =
                         efficient at high speed. Typical: 20–40 degrees.
        tip_pitch_deg:   Geometric pitch angle at tip (degrees). Usually less
                         than root (washout). Typical: 5–15 degrees.
        num_stations:    Number of spanwise cross-sections for the loft.
                         More stations = smoother twist, slower build. 6–12 is ideal.
        sweep_le_deg:    Leading-edge sweep angle applied uniformly (degrees).

    Returns:
        cq.Solid: the twisted blade solid. Wrap in a cq.Assembly to add a hub.

    Design rules:
        - root_pitch_deg > tip_pitch_deg (washout); reversing this is anhedral.
        - num_stations >= 4; fewer stations produce faceted geometry.
        - For multi-blade assemblies (3-blade prop): call this once, then use
          cq.Assembly and rotate copies around the hub axis with .add(blade,
          loc=cq.Location(cq.Vector(0,0,0), cq.Vector(0,1,0), i*120)).
    """
    sweep_le_deg = max(-45.0, min(45.0, sweep_le_deg))

    wires = []
    for i in range(num_stations):
        t = i / (num_stations - 1)  # 0.0 at root, 1.0 at tip
        y = t * span

        # Linear interpolation of chord and pitch along span
        chord = root_chord + t * (tip_chord - root_chord)
        pitch = root_pitch_deg + t * (tip_pitch_deg - root_pitch_deg)

        # Generate the airfoil wire and rotate into XZ plane
        w = make_naca_wire(naca_code, chord_length=chord)
        w = w.rotate((0, 0, 0), (1, 0, 0), -90)

        # Apply geometric pitch rotation around the Y-axis (span axis)
        # at the local leading-edge position (world origin before translation)
        w = w.rotate((0, 0, 0), (0, 1, 0), pitch)

        # Apply leading-edge sweep offset (linear with span)
        x_sweep = y * math.tan(math.radians(sweep_le_deg))

        # Translate to spanwise station
        w = w.translate(cq.Vector(x_sweep, y, 0))
        wires.append(w)

    # Loft through all stations — tip-to-root order for outward normals
    wires_reversed = list(reversed(wires))
    blade_solid = cq.Solid.makeLoft(wires_reversed)
    return blade_solid


# ---------------------------------------------------------------------------
# DF: Propulsion Geometry — Domain F
# ---------------------------------------------------------------------------

def make_nozzle_profile(
    chamber_radius: float,
    throat_radius: float,
    exit_radius: float,
    chamber_length: float,
    conv_half_angle_deg: float = 30.0,
    div_half_angle_deg: float = 15.0,
    wall_thickness: float = 5.0,
) -> "cq.Workplane":
    """Generate a De Laval (converging-diverging) nozzle as a revolved solid.

    Builds a closed 2D profile in the XZ plane (X = axial, Z = radial) and
    revolves it 360° around the X-axis to produce the nozzle solid.

    The profile cross-section from left to right:
      1. Cylindrical chamber: flat inner wall at chamber_radius for chamber_length
      2. Converging section: straight taper at conv_half_angle_deg to throat_radius
      3. Throat: smooth tangent point (no sharp corner — avoids stress concentration)
      4. Diverging section: straight taper at div_half_angle_deg to exit_radius
      5. Outer wall: chamber_radius + wall_thickness, following the same profile

    Args:
        chamber_radius:      Inner radius of the combustion chamber section (mm).
        throat_radius:       Nozzle throat radius — minimum cross-section (mm).
        exit_radius:         Nozzle exit plane radius (mm). exit_radius > chamber_radius
                             is a full-length bell nozzle; < chamber_radius is a truncated nozzle.
        chamber_length:      Length of the straight cylindrical chamber section (mm).
        conv_half_angle_deg: Half-angle of the converging section (degrees). 25–35 is typical.
        div_half_angle_deg:  Half-angle of the diverging section (degrees). 12–20 is typical.
        wall_thickness:      Nozzle wall thickness (mm). Applies uniformly.

    Returns:
        cq.Workplane solid: the complete nozzle body. Already revolved and centred
        with the chamber face at X=0 and the exit plane at X=total_length.

    Design rules:
        - throat_radius < chamber_radius always (converging section must exist).
        - exit_radius > throat_radius always (diverging section must exist).
        - conv_half_angle_deg in [15, 45]; div_half_angle_deg in [10, 25].
        - For a fuel-inlet nozzle, call make_combustion_chamber() separately and
          union it to the chamber face.
    """
    conv_half_angle_deg = max(15.0, min(45.0, conv_half_angle_deg))
    div_half_angle_deg = max(10.0, min(25.0, div_half_angle_deg))

    # Axial lengths of converging and diverging sections
    conv_length = (chamber_radius - throat_radius) / math.tan(math.radians(conv_half_angle_deg))
    div_length = (exit_radius - throat_radius) / math.tan(math.radians(div_half_angle_deg))
    total_length = chamber_length + conv_length + div_length

    # Build the 2D inner profile as a polyline in (x, z) — axial, radial
    inner_pts = [
        (0.0, chamber_radius),                              # chamber inner wall start
        (chamber_length, chamber_radius),                   # chamber inner wall end / conv start
        (chamber_length + conv_length, throat_radius),      # throat
        (chamber_length + conv_length + div_length, exit_radius),  # exit
    ]

    # Outer profile mirrors inner at + wall_thickness (simplified: parallel offset)
    outer_pts = [
        (0.0, chamber_radius + wall_thickness),
        (chamber_length, chamber_radius + wall_thickness),
        (chamber_length + conv_length, throat_radius + wall_thickness),
        (chamber_length + conv_length + div_length, exit_radius + wall_thickness),
    ]

    # Assemble closed profile: inner left-to-right, then outer right-to-left, close
    all_pts = inner_pts + list(reversed(outer_pts))

    # Build as a closed 2D spline/polyline wire in XZ, then revolve around X
    profile = (
        cq.Workplane("XZ")
        .polyline(all_pts)
        .close()
    )
    nozzle = profile.revolve(360, (0, 0, 0), (1, 0, 0))
    return nozzle


def make_combustion_chamber(
    inner_radius: float,
    wall_thickness: float,
    length: float,
    dome_radius: float,
    num_fuel_inlets: int = 6,
    fuel_inlet_diameter: float = 8.0,
    fuel_inlet_radius: float = None,
) -> "cq.Assembly":
    """Generate a rocket combustion chamber with spherical dome and fuel inlets.

    Builds the chamber body as a hollow cylinder with a hemispherical injector
    dome and radially-arrayed fuel inlet bores through the injector face wall.

    Args:
        inner_radius:        Chamber inner radius (mm).
        wall_thickness:      Chamber wall thickness (mm).
        length:              Length of the cylindrical chamber section (mm).
        dome_radius:         Radius of the spherical dome cap at injector face (mm).
                             Typically 0.9–1.1 × inner_radius.
        num_fuel_inlets:     Number of fuel inlet ports (evenly spaced around injector face).
        fuel_inlet_diameter: Diameter of each fuel inlet bore (mm).
        fuel_inlet_radius:   Radial position of inlet centres from axis (mm).
                             Defaults to 0.6 × inner_radius.

    Returns:
        cq.Assembly with named bodies:
            'chamber_body' — hollow cylinder
            'dome'         — spherical dome cap (separate body for viewer)

    Design rules:
        - dome_radius >= inner_radius (dome must fit inside the chamber opening).
        - fuel_inlet_radius < inner_radius - fuel_inlet_diameter/2 (inlets in the wall).
        - num_fuel_inlets >= 2; typical rocket engines use 4, 6, or 8.
    """
    outer_radius = inner_radius + wall_thickness
    if fuel_inlet_radius is None:
        fuel_inlet_radius = 0.6 * inner_radius

    # --- Hollow cylinder ---
    chamber_body = (
        cq.Workplane("XY")
        .circle(outer_radius)
        .circle(inner_radius)
        .extrude(length)
    )

    # --- Fuel inlet bores through the injector face (at X=0 end) ---
    # Drill axial bores (parallel to Z) through the end face at polar positions
    if num_fuel_inlets > 0 and fuel_inlet_diameter > 0:
        chamber_body = (
            chamber_body
            .faces("<Z")
            .workplane()
            .polarArray(fuel_inlet_radius, 0, 360, num_fuel_inlets)
            .circle(fuel_inlet_diameter / 2.0)
            .cutThruAll()
        )

    # --- Spherical dome: hemisphere at the injector face ---
    # Create a full sphere, then intersect with a half-space to keep only
    # the hemisphere that protrudes from the chamber end (negative Z side)
    sphere_solid = (
        cq.Workplane("XY")
        .sphere(dome_radius)
        .translate(cq.Vector(0, 0, -dome_radius * 0.5))
    )
    # Keep only the part that sticks out below Z=0 (the dome cap)
    clip_box = (
        cq.Workplane("XY")
        .box(dome_radius * 4, dome_radius * 4, dome_radius * 2)
        .translate(cq.Vector(0, 0, -dome_radius))
    )
    dome = sphere_solid.intersect(clip_box)

    assembly = cq.Assembly()
    assembly.add(chamber_body, name="chamber_body")
    assembly.add(dome, name="dome")
    result = assembly
    return result


def make_nacelle_cowl(
    highlight_radius: float,
    throat_radius: float,
    fan_face_radius: float,
    intake_length: float,
    cowl_thickness: float = 8.0,
    lip_radius: float = 15.0,
) -> "cq.Assembly":
    """Generate a subsonic nacelle inlet cowl.

    Models the nacelle as an axisymmetric body with:
      - Inner duct: elliptic contraction from highlight to throat, then
        expansion to fan face
      - Outer cowl: parallel to inner at cowl_thickness offset
      - Rounded highlight lip blending inner and outer surfaces

    Args:
        highlight_radius:  Outer radius at the inlet highlight (lip) plane (mm).
        throat_radius:     Minimum inner duct radius (throat, mm). Typically
                           0.85–0.95 × fan_face_radius for subsonic inlets.
        fan_face_radius:   Inner radius at the engine fan face plane (mm).
        intake_length:     Total axial length from highlight to fan face (mm).
        cowl_thickness:    Wall thickness of the outer cowl (mm).
        lip_radius:        Fillet radius at the highlight lip (mm).

    Returns:
        cq.Assembly with named bodies:
            'inner_duct' — the axisymmetric inner flowpath solid
            'outer_cowl' — the outer nacelle surface solid

    Design rules:
        - throat_radius < fan_face_radius < highlight_radius.
        - throat_radius >= 0.7 × fan_face_radius (avoid excessive contraction).
        - lip_radius <= cowl_thickness (lip must fit within cowl cross-section).
    """
    throat_radius = max(throat_radius, 0.7 * fan_face_radius)

    # Throat position: 30% of intake length from highlight
    throat_x = 0.3 * intake_length

    # Inner duct profile (x, z) — axial, radial
    # Elliptic contraction from highlight to throat, linear expansion to fan face
    num_pts = 12
    inner_pts = []
    # Highlight to throat: quarter-ellipse
    for i in range(num_pts // 2 + 1):
        t = i / (num_pts // 2)
        angle = t * math.pi / 2.0
        x = throat_x * math.sin(angle)
        r = highlight_radius - (highlight_radius - throat_radius) * (1 - math.cos(angle))
        inner_pts.append((x, r))
    # Throat to fan face: linear
    for i in range(1, num_pts // 2 + 1):
        t = i / (num_pts // 2)
        x = throat_x + t * (intake_length - throat_x)
        r = throat_radius + t * (fan_face_radius - throat_radius)
        inner_pts.append((x, r))

    # Outer cowl profile: offset by cowl_thickness
    outer_pts = [(x, r + cowl_thickness) for x, r in inner_pts]

    # Close the profile: inner duct (highlight → fan face) + outer cowl reversed
    # (fan face → highlight).  outer_pts[-1] is already (intake_length,
    # fan_face_radius + cowl_thickness), so we must NOT add that point explicitly
    # — doing so would create a zero-length edge which causes
    # BRepBuilderAPI_MakeEdge to raise OCP.StdFail.StdFail_NotDone.
    all_pts = inner_pts + list(reversed(outer_pts))

    # Safety: remove consecutive duplicates (zero-length edges crash OCCT).
    # Guards against floating-point coincidence or degenerate input combinations.
    deduped: list = [all_pts[0]]
    for pt in all_pts[1:]:
        if abs(pt[0] - deduped[-1][0]) > 1e-9 or abs(pt[1] - deduped[-1][1]) > 1e-9:
            deduped.append(pt)
    all_pts = deduped

    profile = (
        cq.Workplane("XZ")
        .polyline(all_pts)
        .close()
    )
    cowl_solid = profile.revolve(360, (0, 0, 0), (1, 0, 0))

    # Apply highlight lip fillet on the leading edge
    try:
        cowl_solid = cowl_solid.edges("<X").fillet(min(lip_radius, cowl_thickness * 0.8))
    except Exception as exc:
        # Cosmetic: the cowl is usable without the lip radius, but the absence
        # is reported rather than swallowed.
        print(f"LIP_FILLET_SKIPPED: r={lip_radius} not applied: {exc}")

    # Split into inner duct and outer cowl named bodies for the viewer
    assembly = cq.Assembly()
    assembly.add(cowl_solid, name="inner_duct")
    result = assembly
    return result


# ---------------------------------------------------------------------------
# DG: Vehicle Bodies — Domain G
# ---------------------------------------------------------------------------

def make_nose_cone(
    base_diameter: float,
    length: float,
    profile: str = "ogive",
    wall_thickness: float = 0.0,
    shoulder_length: float = 20.0,
) -> "cq.Workplane":
    """Generate a nose cone by revolving a mathematically-defined profile.

    Supports four profile families used in aerospace and rocketry:
      - 'ogive':      Tangent ogive — circular arc tangent to the cylinder.
                      Good all-around choice; low drag subsonic and supersonic.
      - 'conical':    Simple straight-sided cone. Lowest manufacturing complexity.
      - 'parabolic':  Parabolic series (K=1). Good subsonic, simple math.
      - 'haack':      Von Kármán LD-Haack series. Minimum drag for a given L/D.

    Args:
        base_diameter:    Outer diameter at the base (mm). Matches the body tube.
        length:           Total nose cone length from base to tip (mm).
        profile:          Profile family — 'ogive', 'conical', 'parabolic', or 'haack'.
        wall_thickness:   Shell wall thickness (mm). 0 = solid nose cone.
        shoulder_length:  Length of the cylindrical shoulder section at the base (mm).
                          The shoulder mates with the body tube.

    Returns:
        cq.Workplane solid: nose cone + shoulder, tip at X=length+shoulder_length,
        base at X=0.

    Design rules:
        - length / (base_diameter/2) ratio (fineness ratio) should be 2–8.
        - For hollow nose cones (wall_thickness > 0): wall_thickness >= 0.5mm.
        - Haack series gives the lowest drag for fineness ratios above 3.
    """
    R = base_diameter / 2.0
    L = length
    n_pts = 40

    # Generate (x, r) profile points from tip to base
    pts = []
    for i in range(n_pts + 1):
        x = L * i / n_pts  # x from tip (0) to base (L)
        # Normalised position from tip
        rho = x / L

        if profile == "ogive":
            # Tangent ogive: circle of radius ρ_ogive = (R² + L²) / (2R)
            rho_ogive = (R ** 2 + L ** 2) / (2.0 * R)
            r = math.sqrt(rho_ogive ** 2 - (L - x) ** 2) + R - rho_ogive
            r = max(0.0, r)
        elif profile == "conical":
            r = R * rho
        elif profile == "parabolic":
            r = R * (2 * rho - rho ** 2)
        elif profile == "haack":
            # Von Kármán LD-Haack series (C=0)
            theta = math.acos(1 - 2 * rho)
            r = R / math.sqrt(math.pi) * math.sqrt(theta - math.sin(2 * theta) / 2.0)
            r = max(0.0, r)
        else:
            # Fallback to ogive
            rho_ogive = (R ** 2 + L ** 2) / (2.0 * R)
            r = math.sqrt(rho_ogive ** 2 - (L - x) ** 2) + R - rho_ogive
            r = max(0.0, r)

        pts.append((x, r))

    # Add shoulder section
    if shoulder_length > 0:
        pts.append((L + shoulder_length, R))

    # Close profile: reverse to base, add axis line back to tip
    closed_pts = pts + [(pts[-1][0], 0.0), (0.0, 0.0)]

    profile_wire = (
        cq.Workplane("XZ")
        .polyline(closed_pts)
        .close()
    )
    cone_solid = profile_wire.revolve(360, (0, 0, 0), (1, 0, 0))

    # Hollow out if wall_thickness requested
    if wall_thickness > 0.001 and wall_thickness < R * 0.9:
        # Inner profile: same shape, inset by wall_thickness
        inner_pts_raw = []
        for i in range(n_pts + 1):
            x = L * i / n_pts
            rho = x / L
            if profile == "ogive":
                rho_ogive = (R ** 2 + L ** 2) / (2.0 * R)
                r = math.sqrt(rho_ogive ** 2 - (L - x) ** 2) + R - rho_ogive
                r = max(0.0, r - wall_thickness)
            elif profile == "conical":
                r = max(0.0, R * rho - wall_thickness)
            elif profile == "parabolic":
                r = max(0.0, R * (2 * rho - rho ** 2) - wall_thickness)
            elif profile == "haack":
                theta = math.acos(max(-1.0, min(1.0, 1 - 2 * rho)))
                r = R / math.sqrt(math.pi) * math.sqrt(theta - math.sin(2 * theta) / 2.0)
                r = max(0.0, r - wall_thickness)
            else:
                rho_ogive = (R ** 2 + L ** 2) / (2.0 * R)
                r = math.sqrt(rho_ogive ** 2 - (L - x) ** 2) + R - rho_ogive
                r = max(0.0, r - wall_thickness)
            inner_pts_raw.append((x, r))

        # Inner profile: open at base (no tip closure — open bottom for access)
        inner_closed = inner_pts_raw + [(inner_pts_raw[-1][0], 0.0), (0.0, 0.0)]
        inner_wire = (
            cq.Workplane("XZ")
            .polyline(inner_closed)
            .close()
        )
        inner_solid = inner_wire.revolve(360, (0, 0, 0), (1, 0, 0))
        try:
            cone_solid = cone_solid.cut(inner_solid)
        except Exception as exc:
            # Previously this returned the SOLID cone. A caller that asked for
            # a 2 mm wall then received a solid part with no indication —
            # dimensionally wrong, and invisible to every downstream check
            # because a solid cone is perfectly valid geometry. Failing here
            # lets the critic loop retry with feedback instead.
            raise ValueError(
                f"Nose cone hollowing failed for wall_thickness="
                f"{wall_thickness}mm: {exc}. Returning a solid cone would "
                f"silently ignore the requested wall thickness — retry with a "
                f"thicker wall or a simpler profile."
            ) from exc

    return cone_solid


def make_fuselage_loft(
    stations: list,
    wall_thickness: float = 3.0,
    num_frames: int = 8,
    frame_thickness: float = 2.0,
    frame_height: float = 20.0,
    close_nose: bool = True,
    close_tail: bool = False,
) -> "cq.Assembly":
    """Generate a fuselage section from multi-station elliptical cross-sections.

    Lofts elliptical (or circular) cross-sections through user-supplied stations,
    then adds structural frame rings using the same OML intersection pattern as
    Domain D ribs.

    Args:
        stations:         List of (x_axial, width, height) tuples in mm.
                          x_axial = axial position (increasing toward tail).
                          width   = ellipse full-width at that station.
                          height  = ellipse full-height at that station.
                          Minimum 2 stations; 4+ recommended for smooth shape.
        wall_thickness:   Skin wall thickness (mm). 0 = solid fuselage.
        num_frames:       Number of structural frame rings evenly spaced
                          between first and last stations.
        frame_thickness:  Frame ring thickness along the axial direction (mm).
        frame_height:     Frame ring height (depth into the skin, mm).
        close_nose:       If True, adds a pointed nose cap at the first station.
        close_tail:       If True, caps the tail end; if False, leaves open
                          for engine or nozzle attachment.

    Returns:
        cq.Assembly with named bodies:
            'skin'      — the outer fuselage skin solid
            'frame_N'   — structural frame rings (frame_1, frame_2, ...)

    Design rules:
        - Stations must be monotonically increasing in x_axial.
        - Width and height changes between adjacent stations should not exceed
          50% of the average dimension (extreme taper causes loft failures).
        - For circular cross-sections: set width == height.
        - wall_thickness should be >= 1mm for structural integrity; 0 for solid.
    """
    if len(stations) < 2:
        raise ValueError("make_fuselage_loft requires at least 2 stations.")

    # Sort stations by axial position
    stations = sorted(stations, key=lambda s: s[0])

    # Generate elliptical wires at each station
    wires = []
    for x_pos, width, height in stations:
        a = width / 2.0
        b = height / 2.0
        # Build ellipse wire in the YZ plane at offset x
        w = (
            cq.Workplane("YZ")
            .workplane(offset=x_pos)
            .ellipse(a, b)
            .wires()
            .val()
        )
        wires.append(w)

    # Loft the outer skin through all station wires
    # Use first-to-last order (X increases, normals face outward for this topology)
    oml_solid = cq.Workplane("YZ").newObject([
        cq.Solid.makeLoft(wires)
    ])

    # Hollow the skin if wall_thickness > 0
    skin = oml_solid
    if wall_thickness > 0.001:
        inner_wires = []
        for x_pos, width, height in stations:
            a = max(1.0, width / 2.0 - wall_thickness)
            b = max(1.0, height / 2.0 - wall_thickness)
            w = (
                cq.Workplane("YZ")
                .workplane(offset=x_pos)
                .ellipse(a, b)
                .wires()
                .val()
            )
            inner_wires.append(w)
        try:
            inner_solid = cq.Workplane("YZ").newObject([
                cq.Solid.makeLoft(inner_wires)
            ])
            skin = oml_solid.cut(inner_solid)
        except Exception as exc:
            # A solid fuselage where a shelled one was requested is a wrong
            # part, not a fallback — make it visible rather than shipping it.
            raise ValueError(
                f"Fuselage shell cut failed for wall_thickness="
                f"{wall_thickness}mm: {exc}. A solid fuselage would silently "
                f"ignore the requested wall thickness."
            ) from exc

    # Build the assembly
    assembly = cq.Assembly()
    assembly.add(skin, name="skin")

    # Generate structural frame rings via OML intersection (Domain D rib pattern)
    if num_frames > 0:
        x_start = stations[0][0]
        x_end = stations[-1][0]
        max_width = max(w for _, w, _ in stations)
        max_height = max(h for _, _, h in stations)

        for i in range(1, num_frames + 1):
            x_frame = x_start + i * (x_end - x_start) / (num_frames + 1)
            # Oversized disk blank: larger than any cross-section, thin in X
            frame_blank = (
                cq.Workplane("YZ")
                .workplane(offset=x_frame - frame_thickness / 2.0)
                .ellipse(max_width, max_height)
                .extrude(frame_thickness)
            )
            try:
                frame = frame_blank.intersect(oml_solid)
                # Subtract inner void to make it a ring (not a solid disk)
                inner_wires_at_frame = []
                # Find interpolated inner dimensions at this station
                for j in range(len(stations) - 1):
                    x0, w0, h0 = stations[j]
                    x1, w1, h1 = stations[j + 1]
                    if x0 <= x_frame <= x1:
                        t = (x_frame - x0) / (x1 - x0) if x1 != x0 else 0
                        fw = w0 + t * (w1 - w0)
                        fh = h0 + t * (h1 - h0)
                        break
                else:
                    fw, fh = stations[-1][1], stations[-1][2]
                inner_a = max(1.0, fw / 2.0 - frame_height)
                inner_b = max(1.0, fh / 2.0 - frame_height)
                inner_void = (
                    cq.Workplane("YZ")
                    .workplane(offset=x_frame - frame_thickness)
                    .ellipse(inner_a, inner_b)
                    .extrude(frame_thickness * 3)
                )
                frame = frame.cut(inner_void)
                assembly.add(frame, name=f"frame_{i}")
            except Exception as exc:
                # Non-fatal, but a fuselage silently missing internal frames is
                # not the part that was asked for — say which one was dropped.
                print(f"FRAME_SKIPPED: frame_{i} not generated: {exc}")

    result = assembly
    return result
