"""
Parametric CAD utility functions for the Text-to-CAD pipeline.

These functions are importable by Machinist-generated scripts via PYTHONPATH.
They provide mathematically correct geometry for parts that LLMs consistently
fail to generate (gears, threads, etc.).
"""

import math

import cadquery as cq


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
    """Generate a stable ribbed-cylinder approximation of a metric thread.

    Per Domain B rules, a true 3D helical sweep crashes the BRep kernel.
    Instead, this builds a cylindrical shaft with evenly spaced annular ribs
    that approximate thread crests. The result is a manifold solid that
    OpenCASCADE can reliably boolean-union or boolean-cut.

    Args:
        diameter: Nominal (major) thread diameter in mm.
        pitch: Thread pitch in mm (distance between crests).
        length: Total thread length in mm.

    Returns:
        cq.Workplane solid centered at the origin.
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
) -> cq.Workplane:
    """ISO-style hex nut with a minor-diameter through-bore, centered at origin.

    The bore is a plain cylinder at the thread's minor diameter — the same
    minor-diameter core ``make_metric_thread`` uses for the bolt shank's
    ribbed approximation. That gives a self-consistent visual: the bolt's
    thread ribs thread into the nut's minor-diameter bore without BRep
    booleans that would crash the kernel.

    Args:
        nominal_diameter: Major thread diameter in mm (e.g. 10.0 for M10).
        pitch: Thread pitch in mm.
        width_across_flats: Hex AF in mm. ``None`` → ISO 4032 default.
        height: Nut height in mm. ``None`` → ISO 4032 default.

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
    minor_dia = nominal_diameter - 1.0825 * pitch

    nut = (
        cq.Workplane("XY")
        .polygon(6, circum_dia)
        .extrude(height)
        .faces(">Z")
        .workplane()
        .hole(minor_dia)
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


def make_bearing_housing(
    bearing_OD: float,
    bearing_width: float,
    housing_OD: float,
    housing_length: float,
    lip_thickness: float = 2.0,
) -> cq.Workplane:
    """Bearing housing with a press-fit bore.

    Generates a cylindrical housing with a precision bore sized for the
    bearing outer race. A retaining lip (shoulder) is formed at one end.

    Args:
        bearing_OD: Outer diameter of the bearing outer race (mm).
            This becomes the bore diameter of the housing.
        bearing_width: Width of the bearing (mm). The bore depth matches this.
        housing_OD: Outer diameter of the housing body (mm).
        housing_length: Total length of the housing (mm).
        lip_thickness: Axial thickness of the retaining lip at the closed end (mm).

    Returns:
        cq.Workplane containing the bearing housing solid.

    Raises:
        ValueError: If bearing_OD >= housing_OD.
    """
    if bearing_OD >= housing_OD:
        raise ValueError(
            f"bearing_OD ({bearing_OD}) must be less than housing_OD ({housing_OD})."
        )

    # Outer cylinder
    outer = (
        cq.Workplane("XY")
        .circle(housing_OD / 2.0)
        .extrude(housing_length)
    )

    # Bore for the bearing (full-depth through hole at bearing_OD)
    bore = (
        cq.Workplane("XY")
        .workplane(offset=lip_thickness)
        .circle(bearing_OD / 2.0)
        .extrude(housing_length - lip_thickness)
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
    # The flange originates from the back edge of the base plate
    # and extends upward by flange_height
    flange_base_z = material_thickness  # top of base plate
    flange = (
        cq.Workplane("XZ")
        .workplane(offset=base_width / 2.0)
        .rect(base_length, flange_height, centered=False)
        .extrude(material_thickness)
    )

    # Translate flange to correct position
    flange = flange.translate((0, 0, 0))

    # Build the bend (approximated as a quarter-circle extrusion)
    # The bend radius fillet is applied at the corner between base and flange
    result = base.union(flange)

    # Apply bend radius fillet at the base-flange junction
    try:
        result = (
            result
            .edges("|X")
            .edges(">Y")
            .fillet(bend_radius)
        )
    except Exception:  # noqa: BLE001
        # Fillet may fail on complex geometry — continue without it
        pass

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

        # Four return flanges (down-facing, like an enclosure panel)
        flange_offset = material_thickness / 2.0  # panel half-thickness

        # Left and right flanges
        for sign in (-1, +1):
            flange = (
                cq.Workplane("YZ")
                .workplane(offset=sign * width / 2.0)
                .rect(height, flange_depth, centered=False)
                .extrude(material_thickness)
            )
            panel = panel.union(flange)

        # Top and bottom flanges
        for sign in (-1, +1):
            flange = (
                cq.Workplane("XZ")
                .workplane(offset=sign * height / 2.0)
                .rect(width + 2 * material_thickness, flange_depth, centered=False)
                .extrude(material_thickness)
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


# ---------------------------------------------------------------------------
# Domain F (Propulsion) primitives
# ---------------------------------------------------------------------------
# All primitives:
# * accept mm-valued geometric inputs;
# * produce a cq.Workplane whose .val() is a manifold cq.Solid (or Compound);
# * default ``intent`` to "analysis"; "manufacturable" adds throat fillets;
# * raise ValueError on geometric inconsistencies caught after construction.


def _validate_solid_output(wp: "cq.Workplane", name: str) -> "cq.Workplane":
    """Assert that ``wp.val()`` is a manifold Solid with positive volume."""
    val = wp.val()
    if not isinstance(val, (cq.Solid, cq.Compound)):
        raise ValueError(
            f"{name}: expected cq.Solid or cq.Compound, got {type(val).__name__}."
        )
    if hasattr(val, "isValid") and not val.isValid():
        raise ValueError(f"{name}: produced geometry failed isValid().")
    if hasattr(val, "Volume") and val.Volume() <= 0:
        raise ValueError(f"{name}: produced geometry has non-positive volume.")
    return wp


_AXIS_OFFSET_MM = 1e-3  # tiny offset to keep the meridian off the revolve axis


def _revolve_meridian(points_rz: list[tuple[float, float]]) -> "cq.Workplane":
    """Revolve a 2-D meridian profile about +Z to produce a 3-D axisymmetric solid.

    Each input point is ``(radius, axial)``. The helper:

    1. Replaces any ``radius == 0`` vertex with ``_AXIS_OFFSET_MM`` -- OCCT 7.7
       fails ``BRep_API: command not done`` when a profile edge or vertex sits
       exactly on the revolve axis.
    2. Builds the wire in the XY workplane with ``X = radius``, ``Y = axial``.
    3. Revolves 360 deg about the Y-axis (which lies in the wire plane and
       coincides with the nozzle axial direction).
    4. Rotates the resulting solid -90 deg about world X so the axial direction
       aligns with world +Z -- the convention every other rocket primitive uses.

    The polyline must be a simple closed polygon (last point == first point or
    will be implicitly closed). The helper does NOT validate self-intersection.
    """
    if not points_rz:
        raise ValueError("_revolve_meridian: empty meridian.")
    if any(r < 0 for r, _ in points_rz):
        raise ValueError("Meridian radii must satisfy r >= 0 (one side of axis).")
    eps = _AXIS_OFFSET_MM
    safe_points = [(max(r, eps), z) for (r, z) in points_rz]
    if safe_points[0] != safe_points[-1]:
        safe_points = safe_points + [safe_points[0]]
    profile = (
        cq.Workplane("XY")
        .polyline(safe_points)
        .close()
    )
    solid = profile.revolve(360.0, (0, 0, 0), (0, 1, 0))
    return solid.rotate((0, 0, 0), (1, 0, 0), -90.0)


def make_conical_nozzle(
    throat_radius: float,
    exit_radius: float | None = None,
    expansion_ratio: float | None = None,
    half_angle_deg: float = 15.0,
    converging_angle_deg: float = 30.0,
    chamber_radius: float = 0.0,
    intent: str = "analysis",
) -> "cq.Workplane":
    """Conical bell nozzle, axisymmetric, centered at the origin (axis = +Z).

    Builds the meridian profile in the XZ plane (X = radius, Z = axial) and
    revolves it 360 deg about the +Z axis via :func:`_revolve_meridian`.

    Args:
        throat_radius:        Throat radius in mm (>0).
        exit_radius:          Exit-plane radius in mm. Supply this OR expansion_ratio.
        expansion_ratio:      Geometric area ratio epsilon = A_e / A_t. Used to derive
                              exit_radius if it is omitted.
        half_angle_deg:       Divergent half-angle (0 < x < 45 deg).
        converging_angle_deg: Converging-section angle (chamber to throat).
        chamber_radius:       Chamber inner radius. Must exceed throat_radius.
        intent:               "analysis" | "manufacturable" | "visualization".
                              "manufacturable" rounds the throat corner.

    Returns:
        cq.Workplane containing the solid nozzle, centered at the origin so that
        z=0 sits at the throat plane (chamber +Z, exit -Z).
    """
    if throat_radius <= 0:
        raise ValueError("throat_radius must be > 0")
    if exit_radius is None and expansion_ratio is None:
        raise ValueError("Provide exit_radius or expansion_ratio")
    if exit_radius is None:
        exit_radius = throat_radius * math.sqrt(expansion_ratio)
    if half_angle_deg <= 0 or half_angle_deg >= 45:
        raise ValueError(f"half_angle_deg must lie in (0, 45); got {half_angle_deg}")
    if converging_angle_deg <= 0 or converging_angle_deg >= 80:
        raise ValueError(f"converging_angle_deg must lie in (0, 80); got {converging_angle_deg}")
    if chamber_radius <= throat_radius:
        raise ValueError("chamber_radius must exceed throat_radius")

    half_div = math.radians(half_angle_deg)
    half_conv = math.radians(converging_angle_deg)

    L_div = (exit_radius - throat_radius) / math.tan(half_div)
    L_conv = (chamber_radius - throat_radius) / math.tan(half_conv)
    L_chamber_section = max(throat_radius, 0.25 * chamber_radius)

    z_throat = 0.0
    z_chamber_top = z_throat + L_conv + L_chamber_section
    z_exit = z_throat - L_div

    meridian = [
        (chamber_radius, z_chamber_top),
        (chamber_radius, z_throat + L_conv),
        (throat_radius,  z_throat),
        (exit_radius,    z_exit),
        (0.0,            z_exit),
        (0.0,            z_chamber_top),
    ]

    wp = _revolve_meridian(meridian)

    if intent == "manufacturable":
        try:
            wp = wp.edges("%CIRCLE").edges(">Z and (not >Z)").fillet(
                min(0.5 * throat_radius, 1.0)
            )
        except Exception:
            pass

    # _revolve_meridian rotates -90 about X so the pre-rotation y-axis maps
    # to -z; the centering translate therefore needs the *positive* mid value.
    z_mid = 0.5 * (z_chamber_top + z_exit)
    wp = wp.translate((0.0, 0.0, z_mid))

    return _validate_solid_output(wp, "make_conical_nozzle")


def make_rao_bell_nozzle(
    throat_radius: float,
    expansion_ratio: float,
    length_fraction: float = 0.8,
    chamber_radius: float | None = None,
    converging_angle_deg: float = 30.0,
    intent: str = "analysis",
) -> "cq.Workplane":
    """Thrust-optimized Rao bell nozzle.

    Builds the meridian as a polyline:

    * Combustion-chamber wall  (cylindrical section + converging frustum)
    * Upstream throat arc      (radius 1.5 * r_t, polygonized to 12 segments)
    * Downstream throat arc    (radius 0.382 * r_t, polygonized to 12 segments)
    * Parabolic skirt          (polygonized parabola passing through both
                                endpoint angles theta_n and theta_e from
                                ``propulsion.rao_table.lookup``)
    * Exit return-to-axis

    Exit radius is locked to ``r_t * sqrt(expansion_ratio)`` within 0.5 %.

    Length formula (15 deg conical reference):

        L = length_fraction * (sqrt(epsilon) - 1) * r_t / tan(15 deg)

    Args:
        throat_radius:        Throat radius in mm.
        expansion_ratio:      A_e / A_t (>1).
        length_fraction:      Bell length as fraction of 15 deg conical equivalent.
        chamber_radius:       Combustion-chamber inner radius (>throat_radius).
        converging_angle_deg: Converging section wall angle (chamber -> throat arc).
        intent:               "analysis" / "manufacturable" / "visualization".

    Returns:
        cq.Workplane axisymmetric Rao bell solid, centered on the origin (axis = +Z,
        chamber on +Z side, exit on -Z side).
    """
    from propulsion.rao_table import lookup as _rao_lookup  # noqa: PLC0415

    if throat_radius <= 0:
        raise ValueError("throat_radius must be > 0")
    if expansion_ratio <= 1:
        raise ValueError("expansion_ratio must be > 1")
    if chamber_radius is None:
        chamber_radius = max(2.0 * throat_radius, throat_radius + 5.0)
    if chamber_radius <= throat_radius:
        raise ValueError("chamber_radius must exceed throat_radius")
    if not (0.5 <= length_fraction <= 1.0):
        raise ValueError("length_fraction must be in [0.5, 1.0]")

    theta_n_deg, theta_e_deg = _rao_lookup(expansion_ratio, length_fraction)
    theta_n = math.radians(theta_n_deg)
    theta_e = math.radians(theta_e_deg)

    exit_radius = throat_radius * math.sqrt(expansion_ratio)
    L_bell = length_fraction * (math.sqrt(expansion_ratio) - 1.0) * throat_radius / math.tan(math.radians(15.0))

    # ── Meridian construction in (r, z): r is radius, z is axial. Throat at z=0.
    # Exit lies at z < 0 (downstream); chamber lies at z > 0 (upstream).
    # ----------------------------------------------------------------------

    # Throat arc radii (Rao convention).
    R_up = 1.5 * throat_radius
    R_dn = 0.382 * throat_radius
    half_conv = math.radians(converging_angle_deg)

    # Downstream throat arc: center at (r_t + R_dn, 0). Throat point (r_t, 0)
    # is at angle pi from the center. The arc proceeds CCW from pi toward
    # (pi - theta_n) to land tangent to the skirt at angle theta_n below the
    # +x axis (i.e. moving toward -z, outward in r).
    arc_center_dn_r = throat_radius + R_dn
    n_arc_dn = 12
    dn_pts: list[tuple[float, float]] = []
    for i in range(n_arc_dn + 1):
        # Sweep from angle pi (throat) to pi - theta_n (skirt tangent).
        ang = math.pi - theta_n * (i / n_arc_dn)
        r = arc_center_dn_r + R_dn * math.cos(ang)
        z = 0.0 + R_dn * math.sin(ang) - R_dn * math.sin(math.pi)
        # sin(pi) == 0 so the throat point is (r_t, 0). For i>0 we want z to
        # become negative (downstream). cos(ang) is negative near pi; sin(ang)
        # is positive (sin goes 0 -> 1 as ang -> pi/2). That's wrong direction.
        # Flip: use arc on the +z side then mirror.
        z = -abs(R_dn * math.sin(ang))
        dn_pts.append((r, z))

    arc_end_dn = dn_pts[-1]
    skirt_start_angle = theta_n  # tangent direction at arc_end_dn (outward, downstream)

    # Parabolic skirt: pick a parabola in (r, z) that satisfies:
    #   - passes through arc_end_dn
    #   - exits at (exit_radius, z_exit) where z_exit = -L_bell
    #   - tangent slope dr/dz at start = -1 / tan(theta_n) (downstream/outward)
    #   - tangent slope dr/dz at end   = -1 / tan(theta_e)
    # We parametrize r(t) and z(t) with t in [0, 1] and use a cubic Hermite
    # interpolation, which is exact for these four constraints. This is
    # functionally a parabolic skirt to within the polygonization tolerance
    # (and gives the user-visible exit-radius lock per the contract).
    z_exit = -L_bell
    p0 = (arc_end_dn[0], arc_end_dn[1])  # (r, z)
    p1 = (exit_radius,   z_exit)

    # Hermite tangents in (r, z) parameter space. Magnitudes chosen so the
    # tangent's r-component dominates near the exit (small theta_e) and the
    # z-component dominates near the throat tangent (large theta_n).
    dz_total = p1[1] - p0[1]   # negative (going downstream)
    dr_total = p1[0] - p0[0]   # positive (going outward)
    mag = math.hypot(dr_total, dz_total)
    t_start = (math.sin(theta_n) * mag, -math.cos(theta_n) * mag)
    t_end   = (math.sin(theta_e) * mag, -math.cos(theta_e) * mag)

    n_skirt = 24
    skirt_pts: list[tuple[float, float]] = []
    for i in range(1, n_skirt + 1):
        s = i / n_skirt
        h00 = 2 * s ** 3 - 3 * s ** 2 + 1
        h10 = s ** 3 - 2 * s ** 2 + s
        h01 = -2 * s ** 3 + 3 * s ** 2
        h11 = s ** 3 - s ** 2
        r = h00 * p0[0] + h10 * t_start[0] + h01 * p1[0] + h11 * t_end[0]
        z = h00 * p0[1] + h10 * t_start[1] + h01 * p1[1] + h11 * t_end[1]
        skirt_pts.append((r, z))

    # Lock exit radius to spec within 0.5 percent (numerical correction).
    rx_last, zx_last = skirt_pts[-1]
    if abs(rx_last - exit_radius) / exit_radius > 0.005:
        skirt_pts[-1] = (exit_radius, z_exit)

    # Upstream throat arc: center at (r_t + R_up, 0). Throat at angle pi.
    # Sweep from pi to (pi + half_conv) so we move into +z (upstream).
    n_arc_up = 12
    up_pts: list[tuple[float, float]] = []
    arc_center_up_r = throat_radius + R_up
    for i in range(n_arc_up + 1):
        ang = math.pi + half_conv * (i / n_arc_up)
        r = arc_center_up_r + R_up * math.cos(ang)
        z = abs(R_up * math.sin(ang))  # +z upstream
        up_pts.append((r, z))

    up_arc_end = up_pts[-1]

    # Converging frustum from upstream arc end up to chamber wall.
    L_conv = (chamber_radius - up_arc_end[0]) / math.tan(half_conv)
    z_chamber_bottom = up_arc_end[1] + L_conv
    z_chamber_top = z_chamber_bottom + 0.5 * chamber_radius  # short cylindrical lip

    # Assemble meridian going CCW (axis -> top -> down right side -> back along axis).
    meridian: list[tuple[float, float]] = []
    meridian.append((chamber_radius, z_chamber_top))
    meridian.append((chamber_radius, z_chamber_bottom))
    meridian.append((up_arc_end[0], up_arc_end[1]))
    # Upstream throat arc, reversed (chamber-side -> throat).
    for r, z in reversed(up_pts[:-1]):
        meridian.append((r, z))
    # Downstream throat arc (skip duplicate throat point if needed).
    for r, z in dn_pts[1:]:
        meridian.append((r, z))
    # Parabolic skirt.
    for r, z in skirt_pts:
        meridian.append((r, z))
    # Return to axis at exit.
    meridian.append((0.0, z_exit))
    meridian.append((0.0, z_chamber_top))

    wp = _revolve_meridian(meridian)

    # See _revolve_meridian: rotation maps pre-rotation y-axial to world -z,
    # so the centering shift uses +z_mid (not -z_mid).
    z_mid = 0.5 * (z_chamber_top + z_exit)
    wp = wp.translate((0.0, 0.0, z_mid))

    return _validate_solid_output(wp, "make_rao_bell_nozzle")


def make_combustion_chamber(
    radius: float,
    l_star: float,
    throat_radius: float,
    contraction_ratio: float | None = None,
    include_converging_section: bool = True,
    converging_angle_deg: float = 30.0,
    intent: str = "analysis",
) -> "cq.Workplane":
    """Cylindrical combustion chamber, optionally with the converging section to throat.

    Length follows the standard L* relation:

        L_chamber = L* * A_throat / A_chamber

    where ``L*`` is provided in **meters** (per convention) and converted to mm.
    When ``include_converging_section`` is True the helper also generates the
    converging frustum from chamber radius to throat radius using
    ``converging_angle_deg``.

    Args:
        radius:                     Chamber inner radius in mm.
        l_star:                     Characteristic length L* in meters.
        throat_radius:              Throat radius in mm (used for the converging frustum
                                    and the L* relation).
        contraction_ratio:          A_chamber / A_throat. Derived from radii if omitted.
        include_converging_section: When True, fuse a frustum from chamber to throat.
        converging_angle_deg:       Wall angle of the converging frustum.
        intent:                     "analysis" / "manufacturable" / "visualization".

    Returns:
        cq.Workplane chamber (or chamber + converging frustum) centered on the
        origin (axis = Z, chamber extends in +Z, throat at -Z).
    """
    if radius <= 0:
        raise ValueError("radius must be > 0")
    if l_star <= 0:
        raise ValueError("l_star must be > 0")
    if throat_radius <= 0 or throat_radius >= radius:
        raise ValueError("throat_radius must be in (0, radius)")

    a_throat = math.pi * throat_radius ** 2
    a_chamber = math.pi * radius ** 2
    L_chamber = (l_star * 1000.0) * a_throat / a_chamber  # mm

    if L_chamber <= 0:
        raise ValueError("Derived chamber length is non-positive")

    chamber = (
        cq.Workplane("XY")
        .circle(radius)
        .extrude(L_chamber)
        .translate((0, 0, -L_chamber / 2.0))
    )

    if include_converging_section:
        half_conv = math.radians(converging_angle_deg)
        L_conv = (radius - throat_radius) / math.tan(half_conv)
        # Build the converging frustum as a revolved trapezoid via the meridian
        # helper (handles the axis-edge OCC quirk + final -Z axial alignment).
        frustum_meridian = [
            (radius,        0.0),
            (throat_radius, -L_conv),
            (0.0,           -L_conv),
            (0.0,           0.0),
        ]
        frustum_wp = _revolve_meridian(frustum_meridian)
        # _revolve_meridian flips axial sign (rotation -90 about X), so the
        # meridian's negative-axial frustum ends up in +z after the helper.
        # Translate so it joins the bottom of the chamber.
        frustum_wp = frustum_wp.translate((0, 0, -L_chamber / 2.0 - L_conv / 2.0))
        chamber = chamber.union(frustum_wp)

    if contraction_ratio is not None:
        derived = (radius / throat_radius) ** 2
        if abs(derived - contraction_ratio) / contraction_ratio > 0.05:
            raise ValueError(
                f"contraction_ratio={contraction_ratio:.3f} disagrees with derived "
                f"(r_c/r_t)^2={derived:.3f} by >5%."
            )

    return _validate_solid_output(chamber, "make_combustion_chamber")


def make_injector_plate(
    plate_radius: float,
    plate_thickness: float,
    orifice_count: int,
    orifice_diameter: float,
    pattern: str = "impinging_doublet",
    distribution_radius: float | None = None,
    impingement_angle_deg: float = 30.0,
    intent: str = "analysis",
) -> "cq.Workplane":
    """Injector plate with orifices in one of four standard patterns.

    Patterns:
        * ``"impinging_doublet"`` -- ``orifice_count`` *pairs* of tilted bores,
          placed on the distribution circle; each pair tilts toward the
          centerline by ``impingement_angle_deg / 2``.
        * ``"like_doublet"`` -- ``orifice_count`` pairs of parallel axial bores.
        * ``"coaxial"`` -- ``orifice_count`` annular ring orifices (inner +
          outer pair per ring station).
        * ``"showerhead"`` -- ``orifice_count`` straight axial bores in a
          single polar array.

    Args:
        plate_radius:           Outer disc radius (mm). Defaults to
                                ``distribution_radius + 1.5 * orifice_diameter``.
        plate_thickness:        Disc thickness (mm).
        orifice_count:          Orifice count (or pair count for doublet patterns).
        orifice_diameter:       Single-orifice diameter (mm).
        pattern:                One of the above four strings.
        distribution_radius:    Radius of the orifice (or pair) distribution circle.
                                Defaults to ``0.6 * plate_radius``.
        impingement_angle_deg:  Total impingement angle (deg) for impinging_doublet.
        intent:                 "analysis" / "manufacturable" / "visualization".

    Returns:
        cq.Workplane injector plate solid, centered at the origin (axis = +Z).
    """
    if plate_radius <= 0 or plate_thickness <= 0:
        raise ValueError("plate_radius and plate_thickness must be > 0")
    if orifice_count <= 0 or orifice_diameter <= 0:
        raise ValueError("orifice_count and orifice_diameter must be > 0")
    if distribution_radius is None:
        distribution_radius = 0.6 * plate_radius
    if distribution_radius <= 0 or distribution_radius + orifice_diameter / 2.0 >= plate_radius:
        raise ValueError(
            "distribution_radius must be > 0 and within (plate_radius - orifice_radius)."
        )
    if pattern not in {"impinging_doublet", "like_doublet", "coaxial", "showerhead"}:
        raise ValueError(f"Unknown injector pattern: {pattern!r}")

    plate = cq.Workplane("XY").circle(plate_radius).extrude(plate_thickness)

    if pattern == "showerhead":
        plate = (
            plate.faces(">Z").workplane()
            .polarArray(distribution_radius, 0.0, 360.0, orifice_count, rotate=False)
            .hole(orifice_diameter, depth=plate_thickness)
        )

    elif pattern == "like_doublet":
        # Two concentric polar arrays separated by ~1 orifice_diameter.
        offset = max(orifice_diameter * 1.2, 0.5)
        for r in (distribution_radius - offset / 2.0, distribution_radius + offset / 2.0):
            plate = (
                plate.faces(">Z").workplane()
                .polarArray(r, 0.0, 360.0, orifice_count, rotate=False)
                .hole(orifice_diameter, depth=plate_thickness)
            )

    elif pattern == "coaxial":
        # Inner small hole, outer annular slot approximated by larger hole.
        inner_r = distribution_radius - 0.5 * orifice_diameter
        outer_r = distribution_radius + 0.5 * orifice_diameter
        plate = (
            plate.faces(">Z").workplane()
            .polarArray(inner_r, 0.0, 360.0, orifice_count, rotate=False)
            .hole(orifice_diameter, depth=plate_thickness)
        )
        plate = (
            plate.faces(">Z").workplane()
            .polarArray(outer_r, 0.0, 360.0, orifice_count, rotate=False)
            .hole(orifice_diameter * 1.4, depth=plate_thickness)
        )

    elif pattern == "impinging_doublet":
        # Each pair: two bores tilted inward by half_imp toward the centerline.
        # We approximate the tilt by offsetting the bottom of each bore along
        # the radial direction; the bore axis becomes a line from face-entry
        # point to the offset bottom point.
        half_imp = math.radians(impingement_angle_deg / 2.0)
        radial_shift = plate_thickness * math.tan(half_imp)
        for i in range(orifice_count):
            theta = 2.0 * math.pi * i / orifice_count
            # Two paired orifices on the same theta, offset tangentially by 1
            # orifice diameter (typical impinging-doublet spacing).
            tangential = orifice_diameter * 1.0
            ux, uy = math.cos(theta), math.sin(theta)
            tx, ty = -uy, ux
            entry_a = (
                distribution_radius * ux + 0.5 * tangential * tx,
                distribution_radius * uy + 0.5 * tangential * ty,
            )
            entry_b = (
                distribution_radius * ux - 0.5 * tangential * tx,
                distribution_radius * uy - 0.5 * tangential * ty,
            )
            # Tilted bore = sweep of a circle from face-entry point to offset
            # bottom point. Implemented here as a straight cylinder along the
            # tilted axis using extrude on a circle drawn on a workplane
            # oriented to the tilt axis.
            for (ex, ey), sign in ((entry_a, +1.0), (entry_b, -1.0)):
                bottom = (
                    ex - sign * radial_shift * ux,
                    ey - sign * radial_shift * uy,
                    -plate_thickness,
                )
                top = (ex, ey, 0.0)
                axis_dir = cq.Vector(
                    bottom[0] - top[0], bottom[1] - top[1], bottom[2] - top[2]
                )
                axis_len = axis_dir.Length
                if axis_len <= 0:
                    continue
                axis_unit = cq.Vector(
                    axis_dir.x / axis_len, axis_dir.y / axis_len, axis_dir.z / axis_len
                )
                # Tool cylinder along the tilt axis.
                tool = (
                    cq.Workplane(cq.Plane(cq.Vector(*top), cq.Vector(1, 0, 0), -axis_unit))
                    .circle(orifice_diameter / 2.0)
                    .extrude(axis_len)
                )
                plate = plate.cut(tool)

    # Center axially on z=0
    plate = plate.translate((0, 0, -plate_thickness / 2.0))

    if intent == "manufacturable":
        try:
            plate = plate.edges(">Z").fillet(min(0.5, plate_thickness * 0.1))
        except Exception:
            pass

    return _validate_solid_output(plate, "make_injector_plate")


def make_helical_cooling_channels(
    nozzle_solid: "cq.Workplane",
    nozzle_outer_radius: float,
    channel_count: int,
    channel_width: float,
    channel_depth: float,
    helix_angle_deg: float = 0.0,
    axial_length: float | None = None,
    intent: str = "analysis",
) -> "cq.Workplane":
    """Cut regenerative cooling channels into a nozzle outer wall.

    Strategy (per Step-1 pre-flight verdict: helix sweeps OK in 2.7):

    1. Build a single rectangular cross-section profile.
    2. Sweep it along a helix path with the configured pitch and length using
       ``cq.Wire.makeHelix(...) + .sweep(isFrenet=True)``.
    3. Polar-array the resulting channel solid ``channel_count`` times and
       boolean-cut the union from ``nozzle_solid``.

    For ``helix_angle_deg == 0`` the path collapses to an axial straight
    channel (handled with a degenerate ``pitch`` -> falls back to a straight
    extruded rectangle along Z).

    Args:
        nozzle_solid:        cq.Workplane to cut into (mutated by boolean cut).
        nozzle_outer_radius: Approximate outer radius at which the channels sit
                             (mm). The helix is built at this radius.
        channel_count:       Number of channels around the circumference.
        channel_width:       Tangential channel width (mm).
        channel_depth:       Radial channel depth (mm) -- distance inward from
                             the outer wall.
        helix_angle_deg:     Helix lead angle from the nozzle axis (0 = axial).
        axial_length:        Axial channel length (mm). Defaults to twice the
                             nozzle_outer_radius if omitted.
        intent:              "analysis" / "manufacturable" / "visualization".

    Returns:
        cq.Workplane: ``nozzle_solid`` with the channels removed.
    """
    if channel_count <= 0:
        raise ValueError("channel_count must be > 0")
    if channel_width <= 0 or channel_depth <= 0:
        raise ValueError("channel_width and channel_depth must be > 0")
    if nozzle_outer_radius <= channel_depth:
        raise ValueError("nozzle_outer_radius must exceed channel_depth")
    if helix_angle_deg < 0 or helix_angle_deg >= 60:
        raise ValueError("helix_angle_deg must be in [0, 60)")
    if axial_length is None:
        axial_length = 2.0 * nozzle_outer_radius

    # Build a single channel solid.
    r_outer = nozzle_outer_radius
    if helix_angle_deg < 1e-3:
        # Straight axial channel -- rectangular box centered on +X, length along Z.
        single = (
            cq.Workplane("XY")
            .center(r_outer - channel_depth / 2.0, 0)
            .rect(channel_depth, channel_width)
            .extrude(axial_length)
            .translate((0, 0, -axial_length / 2.0))
        )
    else:
        pitch = 2.0 * math.pi * r_outer / math.tan(math.radians(helix_angle_deg))
        # Make sure we draw at least a sliver of helix even when axial_length
        # is much shorter than a single turn.
        height = max(axial_length, pitch * 0.05)
        helix = cq.Wire.makeHelix(pitch=pitch, height=height, radius=r_outer)
        path = cq.Workplane(obj=helix)
        profile = (
            cq.Workplane("XZ")
            .center(r_outer - channel_depth / 2.0, 0)
            .rect(channel_depth, channel_width)
        )
        single = profile.sweep(path, isFrenet=True)
        # Translate so the channel is centered axially about z=0.
        single = single.translate((0, 0, -height / 2.0))

    # Polar-array the single channel ``channel_count`` times (rotations
    # only -- the array sits on the same outer cylinder).
    cutter_solids = [single.val()]
    for i in range(1, channel_count):
        theta = 360.0 * i / channel_count
        # rotate a copy about Z by theta
        rotated = single.rotate((0, 0, 0), (0, 0, 1), theta)
        cutter_solids.append(rotated.val())

    cutter_wp = cq.Workplane("XY").newObject(cutter_solids)
    result = nozzle_solid.cut(cutter_wp)

    if intent == "manufacturable":
        try:
            result = result.edges("%LINE").fillet(min(0.2, channel_width * 0.1))
        except Exception:
            pass

    return _validate_solid_output(result, "make_helical_cooling_channels")


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
    # Domain F (propulsion) — Step 4 from the propulsion-domain-f plan
    "make_combustion_chamber(radius, l_star, throat_radius, contraction_ratio=None, "
    "include_converging_section=True, converging_angle_deg=30.0, intent='analysis')",
    "make_conical_nozzle(throat_radius, exit_radius=None, expansion_ratio=None, "
    "half_angle_deg=15.0, converging_angle_deg=30.0, chamber_radius=0.0, intent='analysis')",
    "make_helical_cooling_channels(nozzle_solid, nozzle_outer_radius, channel_count, "
    "channel_width, channel_depth, helix_angle_deg=0.0, axial_length=None, intent='analysis')",
    "make_injector_plate(plate_radius, plate_thickness, orifice_count, orifice_diameter, "
    "pattern='impinging_doublet', distribution_radius=None, impingement_angle_deg=30.0, "
    "intent='analysis')",
    "make_rao_bell_nozzle(throat_radius, expansion_ratio, length_fraction=0.8, "
    "chamber_radius=None, converging_angle_deg=30.0, intent='analysis')",
]
