# Aerospace Expansion Plan — Mirum T2CIII
**Date:** 2026-05-23  
**Author:** Claude  
**Scope:** All unsupported aerospace geometry use cases → GLB + STEP export

---

## Overview

Mirum's Domain D today covers aerodynamic lifting surfaces (wings, stabilizers,
turbine blade blanks) using OML Master Modeling. This plan expands coverage to
all unsupported aerospace geometry types organized into four workstreams:

| Workstream | Scope | Domain assignment |
|------------|-------|-------------------|
| **DA** — Planform Variants | Swept, delta, canard, dihedral | Domain D extension |
| **DB** — Twisted Blades | Propeller, helicopter rotor, wind turbine | Domain D extension |
| **DC** — Multi-Element Airfoils | Slotted flaps, Fowler flaps, leading-edge slats | Domain D extension |
| **DD** — Winglets | Blended winglet, raked tip, split scimitar | Domain D extension |
| **DF** — Propulsion | Rocket nozzle, combustion chamber, fuel inlet, nacelle/inlet duct | **New Domain F** |
| **DG** — Vehicle Bodies | Fuselage, nose cone fairing, pod body | **New Domain G** |

**Target acceptance criterion for every workstream:** the pipeline produces a
non-empty GLB renderable in the web viewer AND a valid STEP file that opens in
Inventor or SolidWorks without errors. Geometry certificate must report
`volume_mm3 > 0` and `passed: true`.

---

## Architecture principles

1. **cad_utils.py is the geometry library.** Every new deterministic geometry
   helper (wire generator, solid builder, profile sampler) goes here. It is
   sandbox-allowlisted. Do NOT add network calls, file I/O, or subprocess
   invocations.

2. **strategies.py is the Machinist prompt injector.** New rule blocks describe
   the workflow and forbidden patterns. Keyword triggers in `_KEYWORD_TRIGGERS`
   activate the right rule blocks without polluting every prompt.

3. **classifier.py routes the domain.** The Lite model classifies; we extend its
   system prompt and enum. New domains (F, G) require adding their letter to the
   `MateType` schema enum and the classifier's JSON schema.

4. **schemas.py carries per-domain parameters.** New parameters extracted by the
   REA (e.g. twist distribution, throat diameter, fuselage frame stations) go
   here as Optional fields on `FlightEnvelopeSchema` or a new sibling schema.

5. **Security gate applies** if any of the above touches `compiler.py`,
   `main.py` endpoints, or `schemas.py` MateType enum.

---

## Workstream DA — Planform Variants

**Parts:** swept wing, delta wing, canard, dihedral wing, tapered + swept

**Why not working today:** The existing Domain D rule set generates root and tip
wires at Y=0 and Y=span with no X-offset. Sweep requires the tip wire to be
offset along X (chord direction). Delta wings reduce tip chord to near-zero.
Dihedral requires the tip wire to be offset along Z (vertical). None of these
transforms are in the current prompt or cad_utils.

### DA-01: `make_planform_wires()` in `cad_utils.py`

```python
def make_planform_wires(
    naca_code: str,
    root_chord: float,
    tip_chord: float,
    span: float,
    sweep_le_deg: float = 0.0,   # leading-edge sweep angle (degrees)
    dihedral_deg: float = 0.0,   # dihedral angle (degrees)
    twist_tip_deg: float = 0.0,  # washout twist at tip (degrees, nose-down positive)
) -> tuple[cq.Wire, cq.Wire]:
```

Returns `(root_wire, tip_wire)` already rotated into XZ, translated, swept, and
twisted. The caller passes these directly to `cq.Solid.makeLoft([tip_wire,
root_wire])`. No changes to the Machinist script structure — only the wire
generation changes.

- **Sweep:** tip wire X-offset = `span * tan(sweep_le_deg)`. Applied via
  `.translate(cq.Vector(x_offset, span, 0))` after the Y-span translation.
- **Dihedral:** tip wire Z-offset = `span * tan(dihedral_deg)`. Applied via
  `.translate(cq.Vector(x_offset, span, z_offset))`.
- **Delta / zero-tip:** tip_chord can be as small as 5mm. `make_naca_wire` at
  5mm still produces a valid closed wire.
- **Twist:** tip wire additional rotation around X-axis by `twist_tip_deg`
  before translation. This is washout (reduces effective AoA at tip).

### DA-02: `rule_planform_variants` in `strategies.py`

New rule block that:
- Documents `make_planform_wires()` signature
- Specifies that ALL swept/delta/dihedral scripts MUST use this helper
- Lists forbidden patterns: manually translating tip wire along X without
  accounting for sweep geometry, using `.workplane(offset=)` for spanwise
  stations on swept geometry (does not account for sweep)
- Documents correct rib-blank sizing for swept wings (oversized blank must span
  full chord at the sweep-corrected station)

**Keyword triggers:** `swept wing`, `delta wing`, `delta`, `canard`, `dihedral`,
`swept`, `leading edge sweep`, `taper ratio`, `planform`

### DA-03: REA schema extension

Add to `FlightEnvelopeSchema` (or a new `WingPlanformSchema`):
```python
sweep_le_deg: Optional[float] = None   # leading-edge sweep (degrees)
dihedral_deg: Optional[float] = None   # dihedral (degrees)
```
REA extracts these from prompts like "35-degree swept wing" or "5-degree
dihedral."

### DA-04: Classifier update

Extend Domain D description in `classifier.py` system prompt to include:
`swept wings, delta wings, canards, dihedral configurations, tapered planforms`

### DA-05: Eval prompts (6 regression cases)

```
D-sweep-01: "NACA 0012, span 800mm, 35-degree leading-edge sweep, root chord 250mm, tip chord 120mm, 4 ribs, 2 spars"
D-delta-01: "delta wing NACA 0006, root chord 600mm, span 400mm, tip chord 20mm, 3 ribs"
D-canard-01: "canard surface NACA 2409, span 300mm, chord 80mm, 2 ribs"
D-dihedral-01: "NACA 4412 wing, 800mm span, 200mm root chord, 6-degree dihedral, 5 ribs"
D-sweep-02: "swept stabilizer NACA 0009, 30-degree sweep, 500mm span, 2 spars"
D-combo-01: "NACA 2412 wing, 600mm span, 25-degree sweep, 4-degree dihedral, 5 ribs, front and rear spar"
```

**Acceptance:** GLB + STEP, geometry cert passes, no inverted normals.

---

## Workstream DB — Twisted Blades

**Parts:** propeller blade, helicopter rotor blade, wind turbine blade

**Why not working today:** A twisted blade requires N cross-sectional wires along
the span, each rotated by the local geometric pitch angle. The current Domain D
OML approach only generates two wires (root + tip). `makeLoft` through N wires
produces the twist distribution.

### DB-01: `make_twisted_blade()` in `cad_utils.py`

```python
def make_twisted_blade(
    naca_code: str,
    root_chord: float,
    tip_chord: float,
    span: float,
    root_pitch_deg: float,       # geometric pitch angle at root (degrees)
    tip_pitch_deg: float,        # geometric pitch angle at tip (washout)
    num_stations: int = 8,       # number of loft cross-sections
    sweep_le_deg: float = 0.0,
) -> cq.Solid:
```

Internally:
1. For each station `i` in `range(num_stations)`: compute `y = i * span / (num_stations - 1)`
2. Interpolate chord and pitch angle linearly (or via cosine spacing)
3. Generate `make_naca_wire(naca_code, chord)`, rotate into XZ, apply pitch
   rotation around Y-axis (the span axis), translate to `(x_sweep, y, 0)`
4. Pass all N wires to `cq.Solid.makeLoft(wires)` — multi-section loft

Returns the twisted blade solid directly (the caller wraps it in an Assembly).

**Note on XFOIL:** XFOIL analyzes 2D cross-sections. For twisted blades, run
the polar at the 75% span station chord and pitch (the aerodynamically
representative section). `_run_aero_stage` should detect the twist and extract
the 75% station parameters from `FlightEnvelopeSchema`.

### DB-02: `rule_twisted_blade` in `strategies.py`

- Documents `make_twisted_blade()` signature and when to use it vs.
  `make_naca_wire()` + manual loft
- Specifies: pitch rotation is around the Y-axis (span axis), NOT the X-axis
- Forbidden: computing twist using `.rotate()` in a loop without using the
  helper (model tends to get the rotation axis wrong)
- Hub geometry: for propellers/rotors, add a central hub cylinder
  (`.cylinder(hub_length, hub_radius)`) and `.union()` it to the blade solid

**Keyword triggers:** `propeller`, `prop`, `rotor blade`, `helicopter blade`,
`wind turbine blade`, `turbine blade`, `twisted blade`, `pitch angle`,
`geometric pitch`, `washout`

### DB-03: Schema extension

```python
root_pitch_deg: Optional[float] = None
tip_pitch_deg: Optional[float] = None
num_blades: Optional[int] = None   # for assembly (3-blade prop = 3 rotated copies)
```

For multi-blade assemblies, the Machinist uses a polar array of the twisted
blade solid around the hub axis.

### DB-04: Eval prompts (5 regression cases)

```
D-prop-01: "2-blade propeller, NACA 4412, root chord 60mm, tip chord 25mm, span 300mm, root pitch 30deg, tip pitch 12deg"
D-prop-02: "3-blade propeller NACA 2412, 400mm diameter, root pitch 25deg, tip pitch 8deg, hub diameter 40mm"
D-rotor-01: "helicopter rotor blade NACA 0012, 1200mm span, root chord 80mm, tip chord 50mm, 10deg root pitch, 4deg tip pitch"
D-wind-01: "wind turbine blade NACA 4418, 2000mm span, 150mm root chord, 60mm tip chord, 15deg root pitch, 3deg tip pitch"
D-prop-03: "variable-pitch propeller blade NACA 0015, 500mm span, 70mm chord, 20-degree geometric pitch"
```

---

## Workstream DC — Multi-Element Airfoils

**Parts:** wing with slotted flap, Fowler flap, leading-edge slat, Krueger flap

**Why not working today:** The OML approach builds one lofted solid from one
NACA profile. A two-element airfoil (main element + flap) requires two
independent OML solids positioned relative to each other with a calibrated slot
gap. The Machinist has no guidance on multi-element geometry.

### DC-01: `make_slotted_flap()` in `cad_utils.py`

```python
def make_slotted_flap(
    main_naca: str,
    flap_naca: str,
    main_chord: float,
    flap_chord_frac: float,      # flap chord as fraction of main chord (0.25–0.40)
    span: float,
    flap_deflection_deg: float,  # positive = trailing-edge down
    slot_gap_mm: float = 3.0,    # gap between main element and flap
    num_ribs: int = 4,
) -> cq.Assembly:
```

Strategy:
1. Build main element OML solid from `main_naca` at `main_chord * (1 - flap_chord_frac)`
2. Build flap OML solid from `flap_naca` at `main_chord * flap_chord_frac`
3. Position flap at trailing edge of main element, offset by `slot_gap_mm`,
   rotated by `flap_deflection_deg` around the flap leading edge hinge point
4. Generate ribs for both elements via intersection with their respective OMLs
5. Return `cq.Assembly` with named bodies: `main_skin`, `flap_skin`, `main_rib_N`, `flap_rib_N`

**Positioning math:** Flap leading-edge X-position = main chord trailing edge X
\+ `slot_gap_mm * cos(flap_deflection_deg)`. Flap Z-position = `-slot_gap_mm
* sin(flap_deflection_deg)`. Rotation applied via
`.rotate((hinge_x, 0, hinge_z), (0, 1, 0), flap_deflection_deg)`.

### DC-02: `make_leading_edge_slat()` in `cad_utils.py`

```python
def make_leading_edge_slat(
    main_naca: str,
    slat_chord_frac: float = 0.15,  # slat chord as fraction of main chord
    main_chord: float = 200.0,
    span: float = 500.0,
    slat_deflection_deg: float = 20.0,
    slot_gap_mm: float = 2.5,
) -> cq.Assembly:
```

Similar approach: slat is a thin symmetric airfoil (NACA 0008–0010) positioned
ahead of and above the main element leading edge.

### DC-03: `rule_multi_element` in `strategies.py`

- Documents `make_slotted_flap()` and `make_leading_edge_slat()` signatures
- Key rule: each element is an independent OML solid — **never boolean-union
  the elements** (they must be separate Assembly bodies for the viewer to
  show slot geometry)
- Forbidden: manually sketching the slot gap as a subtraction from the main
  element (leads to near-zero-thickness face crashes)

**Keyword triggers:** `slotted flap`, `fowler flap`, `trailing edge flap`,
`leading edge slat`, `slat`, `multi-element`, `two-element airfoil`,
`high lift`, `flap deflection`, `flap chord`

### DC-04: Eval prompts (4 regression cases)

```
D-flap-01: "NACA 2412 wing with 30% chord slotted flap, 20-degree deflection, 500mm span, 200mm main chord"
D-flap-02: "Fowler flap configuration, main element NACA 4415, flap NACA 2409, 35% flap chord, 600mm span"
D-slat-01: "NACA 2412 wing with leading edge slat, 15% slat chord, 20-degree slat deflection, 400mm span"
D-flap-03: "full high-lift system: NACA 2415 main element, leading edge slat, trailing edge slotted flap, 800mm span"
```

---

## Workstream DD — Winglets

**Parts:** blended winglet, raked tip, split scimitar winglet, sharklet

**Why not working today:** A winglet is a secondary wing surface that curves
upward (or downward for anhedral winglets) from the main wing tip, connected
via a smooth blend radius. This requires two independent OML lofts joined at
the cant angle transition, with the inner blend handled by a ruled surface
patch. CadQuery's `makeLoft` can handle this via a three-section loft (root,
transition, tip-up) but the axis rotation at the cant break is not in any
existing prompt.

### DD-01: `make_winglet()` in `cad_utils.py`

```python
def make_winglet(
    wing_naca: str,
    winglet_naca: str,
    wing_span: float,
    wing_root_chord: float,
    wing_tip_chord: float,
    winglet_span: float,          # vertical height of winglet
    winglet_root_chord: float,    # = wing tip chord typically
    winglet_tip_chord: float,     # smaller, tapers to point
    cant_angle_deg: float = 90.0, # 90 = vertical, 75 = blended
    sweep_le_deg: float = 35.0,   # winglet LE sweep
    num_wing_ribs: int = 5,
    num_winglet_ribs: int = 2,
) -> cq.Assembly:
```

Strategy:
1. Build main wing OML solid (existing OML approach) from root to tip
2. Build winglet OML solid: wires in a plane rotated `cant_angle_deg` from the
   span axis (Y) — so a 90° cant winglet extends vertically (along Z from the
   wing tip), a 75° cant winglet leans outboard
3. Connect at the wing tip: winglet root wire == wing tip wire, positioned at
   `(x_sweep_offset, wing_span, 0)`
4. Rib generation for each sub-solid via intersection with respective OML
5. Return Assembly: `wing_skin`, `winglet_skin`, ribs for both

**The key transform:** winglet wire generation uses a rotated workplane. The
winglet spans along the rotated axis: for a 90° cant winglet, the winglet span
axis is Z. Wire generation calls `make_naca_wire()` as normal, but the rotation
into the perpendicular plane is `rotate((0,0,0), (0,1,0), 90)` (rotate 90° around
span axis Y) followed by translation to the cant-angle direction.

### DD-02: `rule_winglets` in `strategies.py`

- Documents the cant-angle transform
- Key rule: winglet and main wing are separate OML solids in the Assembly —
  do NOT loft them as a single continuous solid (the curvature break at the
  cant angle causes B-Rep failures)
- The "blend" is visual — viewer shows them as adjacent named bodies

**Keyword triggers:** `winglet`, `wingtip device`, `raked tip`, `split
scimitar`, `sharklet`, `blended winglet`, `tip fence`, `cant angle`

### DD-03: Eval prompts (3 regression cases)

```
D-wl-01: "NACA 2412 wing 800mm span, 200mm root chord, with 90-degree vertical winglet 80mm tall, NACA 0010, 35-degree LE sweep"
D-wl-02: "blended winglet, main wing NACA 4412 600mm span, 75-degree cant, winglet span 60mm"
D-wl-03: "sharklet-style winglet on NACA 2415 wing, 500mm span, winglet height 70mm, 30-degree LE sweep"
```

---

## Workstream DF — Domain F: Propulsion

**Parts:** rocket nozzle (De Laval), combustion chamber, fuel inlet manifold,
afterburner duct, subsonic nacelle inlet, supersonic inlet (pitot / mixed
compression)

**Why not working today:** No Domain F exists. The classifier has no propulsion
category. Propulsion geometry is built primarily from bodies of revolution
(`cq.Workplane.revolve()`) and internal channel networks — fundamentally
different from lofted aerodynamic surfaces.

### DF-01: Add Domain F to `classifier.py`

Extend the domain enum: `["A", "B", "C", "D", "E", "F"]`

Domain F system-prompt description:
> **Domain F (Propulsion):** Axisymmetric or near-axisymmetric propulsion
> components built primarily with revolve operations. Examples: rocket nozzles
> (converging-diverging De Laval profiles), combustion chambers, afterburner
> ducts, nacelle cowlings, inlet ducts, fuel injector manifolds, thrust chambers.
> Key signals: "nozzle," "throat," "exit diameter," "combustion chamber,"
> "injector," "fuel inlet," "oxidizer," "afterburner," "nacelle," "inlet duct,"
> "cowling," "bypass ratio."
> PRIORITY: Domain F takes precedence over Domain B when "nozzle," "combustion,"
> or "thrust" keywords are present. A "rocket nozzle" is Domain F, NOT Domain B.

### DF-02: `make_nozzle_profile()` in `cad_utils.py`

```python
def make_nozzle_profile(
    chamber_radius: float,       # combustion chamber inner radius (mm)
    throat_radius: float,        # nozzle throat radius (mm)
    exit_radius: float,          # nozzle exit radius (mm)
    chamber_length: float,       # length of cylindrical chamber section (mm)
    conv_half_angle_deg: float = 30.0,   # converging section half-angle
    div_half_angle_deg: float = 15.0,    # diverging section half-angle (conical)
    wall_thickness: float = 5.0,
) -> cq.Workplane:
```

Produces a 2D profile wire in the XZ plane (X = axial, Z = radial) representing
the inner and outer walls of the nozzle. The caller passes this to `.revolve(360,
(0,0,0), (1,0,0))` to produce the 3D solid. The profile consists of:
1. Cylindrical chamber section (rect at chamber_radius for chamber_length)
2. Converging section (straight line at conv_half_angle_deg from chamber to throat)
3. Throat fillet (small radius blend at the throat — prevents stress concentration)
4. Diverging section (straight line at div_half_angle_deg from throat to exit)
5. Outer wall at `inner_radius + wall_thickness` matching the same profile

**Returns:** A closed 2D profile wire suitable for `.revolve()`.

### DF-03: `make_combustion_chamber()` in `cad_utils.py`

```python
def make_combustion_chamber(
    inner_radius: float,
    wall_thickness: float,
    length: float,
    dome_radius: float,          # spherical dome cap at the injector face
    num_fuel_inlets: int = 6,
    fuel_inlet_diameter: float = 8.0,
    fuel_inlet_radius: float = None,  # radial position; defaults to 0.6 * inner_radius
) -> cq.Assembly:
```

Strategy:
1. Main body: `.cylinder(length, inner_radius + wall_thickness)` minus
   `.cylinder(length, inner_radius)` (hollow cylinder)
2. Dome: `.sphere(dome_radius)` positioned at the injector face, boolean-cut to
   keep only the hemisphere, union to the cylinder
3. Fuel inlets: `polar_array` of `.cylinder()` holes drilled radially through
   the chamber wall at the injector face end. Holes are parallel to the X-axis
   (axial direction), positioned at `fuel_inlet_radius` from center.
4. Returns `cq.Assembly` with named bodies: `chamber_body`, `dome`

### DF-04: `make_nacelle_cowl()` in `cad_utils.py`

```python
def make_nacelle_cowl(
    highlight_radius: float,     # inlet highlight (lip) outer radius
    throat_radius: float,        # inlet throat radius (smallest inner radius)
    fan_face_radius: float,      # fan face radius (engine face)
    intake_length: float,        # axial length of the inlet duct
    cowl_thickness: float = 8.0,
    lip_radius: float = 15.0,    # highlight lip fillet radius
) -> cq.Assembly:
```

A subsonic nacelle inlet modeled as:
1. Inner flowpath: lofted surface from highlight to throat to fan face (elliptic
   profile — widest at highlight, contracts to throat, expands to fan face)
2. Outer cowl: revolve of the outer lip profile
3. Lip: torus section fillet blending inner and outer at the highlight
Returns: Assembly with `inner_duct`, `outer_cowl`, `lip`

### DF-05: `rule_domain_f` in `strategies.py`

Core rules:
1. **REVOLVE FIRST:** All propulsion bodies start from a 2D profile in the
   XZ plane (X=axial, Z=radial) revolved 360° around the X-axis
2. **Use the library:** `make_nozzle_profile()` for nozzles,
   `make_combustion_chamber()` for chambers, `make_nacelle_cowl()` for inlets
3. **Fuel inlets are radial bores:** use `polarArray()` to place inlet holes
   through the chamber wall — same pattern as bolt circles in Domain A
4. **Never subtract a flowpath from the outer body separately** — the
   `make_combustion_chamber()` helper handles hollowing correctly
5. **Export format:** `cq.Assembly` with named bodies + STEP export

Keyword triggers: `nozzle`, `de laval`, `converging-diverging`, `c-d nozzle`,
`rocket nozzle`, `combustion chamber`, `thrust chamber`, `afterburner`,
`fuel inlet`, `oxidizer inlet`, `injector`, `nacelle`, `inlet duct`, `cowling`,
`engine pod`, `bypass`, `fan face`, `highlight radius`, `throat diameter`

### DF-06: Schema — `PropulsionSchema`

New Pydantic model extracted by REA for Domain F prompts:
```python
class PropulsionSchema(BaseModel):
    propulsion_type: Literal["nozzle", "chamber", "nacelle", "duct", "generic"]
    throat_diameter_mm: Optional[float] = None
    exit_diameter_mm: Optional[float] = None
    chamber_pressure_bar: Optional[float] = None   # advisory only, not used in geometry
    num_fuel_inlets: Optional[int] = None
    highlight_radius_mm: Optional[float] = None
    fan_face_radius_mm: Optional[float] = None
    bypass_ratio: Optional[float] = None           # advisory
```

### DF-07: Eval prompts (8 regression cases)

```
F-nozzle-01: "rocket nozzle, throat diameter 30mm, exit diameter 90mm, chamber radius 50mm, chamber length 80mm, wall thickness 4mm"
F-nozzle-02: "De Laval nozzle, 15-degree diverging half-angle, 25mm throat, 70mm exit, 60mm chamber length"
F-chamber-01: "cylindrical combustion chamber, 60mm inner radius, 5mm wall, 120mm length, 6 fuel inlet ports at 36mm radius"
F-chamber-02: "thrust chamber with spherical dome, 50mm radius, 8 oxidizer inlets, 100mm chamber length"
F-nacelle-01: "subsonic nacelle inlet, 120mm highlight radius, 90mm throat radius, 100mm fan face radius, 200mm intake length"
F-nacelle-02: "engine nacelle cowl, 150mm highlight, 10mm lip radius, 110mm throat, 250mm length"
F-nozzle-03: "afterburner duct, 80mm inlet radius, 85mm exit radius, 400mm length, 3mm wall thickness"
F-combo-01: "rocket engine assembly: combustion chamber 55mm radius 100mm length, converging-diverging nozzle 20mm throat 60mm exit"
```

---

## Workstream DG — Domain G: Vehicle Bodies

**Parts:** aircraft fuselage, nose cone fairing, re-entry capsule, pod body,
rocket body tube

**Why not working today:** No Domain G. Fuselages are multi-station lofts of
elliptical/circular cross-sections — similar to Domain C organic lofts but
with structural frame ring placement and more precise parametric control.
The Domain C rules don't handle frame rings or pressure-vessel wall thickness.

### DG-01: `make_fuselage_loft()` in `cad_utils.py`

```python
def make_fuselage_loft(
    stations: list[tuple[float, float, float]],
    # Each station: (x_axial, width, height) — elliptical cross-section
    wall_thickness: float = 3.0,
    num_frames: int = 8,          # structural frame rings
    frame_thickness: float = 2.0,
    frame_height: float = 20.0,
    close_nose: bool = True,      # taper nose to a point/round
    close_tail: bool = False,     # leave tail open for nozzle/engine attachment
) -> cq.Assembly:
```

Strategy:
1. For each station, generate an elliptical wire:
   `cq.Workplane('YZ').workplane(offset=x).ellipse(width/2, height/2).wire()`
2. Loft all station wires into the outer skin solid
3. If `wall_thickness > 0`: inner skin via inward-offset loft (slightly smaller
   ellipses), boolean subtract inner from outer
4. Frame rings: at `num_frames` evenly spaced X stations, intersect an
   oversized disk solid (like Domain D rib blanks) with the fuselage solid,
   producing a frame ring that matches the local cross-section exactly
5. Return Assembly: `skin`, `frame_1` through `frame_N`

### DG-02: `make_nose_cone()` in `cad_utils.py`

```python
def make_nose_cone(
    base_diameter: float,
    length: float,
    profile: Literal["ogive", "conical", "parabolic", "elliptical", "haack"] = "ogive",
    wall_thickness: float = 0.0,  # 0 = solid
    shoulder_length: float = 20.0,  # cylindrical section at the base
) -> cq.Workplane:
```

Generates the profile curve mathematically (ogive = circular arc, parabolic =
y=r*(x/L)^0.5, Haack series = derived from Von Kármán profile) and revolves it.
These are the same functions used in rocket/missile nose cone design.

### DG-03: `rule_domain_g` in `strategies.py`

Core rules:
1. **Elliptical cross-sections:** always use `.ellipse(a, b)` — not two
   semicircles, not approximating with splines
2. **Loft axis:** axial direction is X. Stations are `.workplane(offset=x)` on
   the YZ plane
3. **Frame rings via intersection:** same OML Master pattern as Domain D ribs —
   oversized disk blanks intersected with the fuselage solid. Never sketch the
   frame ring shape manually
4. **Nose and tail closures:** `make_nose_cone()` for nose; tail left open or
   capped with a flat disk if no nozzle interface
5. **For rocket body tubes:** simple hollow cylinder — use Domain A strategy
   unless structural frames are requested

**Keyword triggers:** `fuselage`, `fuselage section`, `cabin section`,
`pressure vessel`, `body of revolution`, `nose cone`, `ogive`, `haack series`,
`parabolic nose`, `fairing`, `shroud`, `rocket body`, `payload fairing`,
`re-entry capsule`, `crew capsule`, `pod body`

### DG-04: Add Domain G to `classifier.py`

Extend enum to `["A", "B", "C", "D", "E", "F", "G"]`

Domain G system-prompt description:
> **Domain G (Vehicle Bodies):** Axisymmetric or near-axisymmetric bodies of
> revolution for vehicle structures — fuselages, fairings, shrouds, nose cones,
> re-entry capsules, rocket body tubes, payload fairings. Built primarily from
> multi-section elliptical lofts or revolves of profile curves, with optional
> structural frame rings.
> Key signals: "fuselage," "nose cone," "fairing," "shroud," "capsule," "ogive,"
> "body tube," "payload section," "cabin."

### DG-05: Eval prompts (5 regression cases)

```
G-fuse-01: "aircraft fuselage section, 3 stations: nose 200x180mm at x=0, cabin 600x500mm at x=2000mm, tail 200x180mm at x=4000mm, 8 frame rings, 3mm wall"
G-nose-01: "ogive nose cone, 80mm base diameter, 200mm length, 2mm wall"
G-nose-02: "Von Karman (Haack series) nose cone, 100mm base, 250mm length"
G-fuse-02: "cylindrical fuselage section, 500mm diameter, 1500mm length, 6 structural frames, 4mm wall thickness"
G-pod-01: "streamlined pod body, 120mm diameter nose, 300mm cylindrical mid-section, elliptical tail taper, 2mm wall"
```

---

## Cross-cutting tasks

### CC-01: `MateType` enum extension (security gate applies)

`schemas.py` currently enumerates `MateType` for assembly mates. Extend to
cover propulsion and vehicle body connections:
- `AXIAL_FLANGE` — nozzle-to-chamber, duct-to-duct bolted flanges
- `SLIP_FIT` — cowl over inlet, sliding telescope joints

### CC-02: Security review gate

Workstreams DF and DG both touch `schemas.py` (new Pydantic models). Per
worktree-merge-gate.md, a `mirum-security-reviewer` agent pass is required
before merging. Scope: verify `PropulsionSchema` and fuselage station list
cannot be used to inject OS paths or bypass the RLIMIT_AS sandbox.

### CC-03: Sandbox allowlist verification

`cad_utils.py` is on the `_ALLOWED_MODULES` list in `compiler.py`. All new
helpers added in DA–DG workstreams go into `cad_utils.py`. No new allowlist
entries needed. Verify by running `test_compiler_security.py` after each
workstream merge.

### CC-04: XFOIL integration for non-wing shapes

Domain F and G shapes are not airfoils — XFOIL does not apply. The
`_run_aero_stage` function should be updated to skip XFOIL (return early with
`"XFOIL not applicable for Domain F/G geometry"`) when domain is F or G.
Domain DB (twisted blades) should run XFOIL at the 75% span station.

---

## Implementation order (recommended)

| Priority | Workstream | Effort | Dependency |
|----------|-----------|--------|-----------|
| 1 | DA — Planform Variants | Medium | None — extends existing Domain D helpers |
| 2 | DB — Twisted Blades | Medium | DA (shares make_planform_wires sweep logic) |
| 3 | DF — Propulsion (nozzle only) | Medium | None — clean new domain |
| 4 | DG — Nose cones | Small | None — pure revolve |
| 5 | DC — Multi-Element Airfoils | Large | DA (tip positioning math reused) |
| 6 | DD — Winglets | Large | DA (planform + sweep logic) |
| 7 | DF — Full (chamber + nacelle) | Large | DF nozzle complete |
| 8 | DG — Full (fuselage) | Large | DG nose cone complete |

Start with DA and DF-nozzle in parallel — they are independent and have the
highest user value.

---

## Worktree assignments (proposal)

| Branch | Workstream | Security gate |
|--------|-----------|---------------|
| `planform-variants` | DA | No (no schemas.py touch) |
| `twisted-blades` | DB | No |
| `multi-element-airfoils` | DC | No |
| `winglets` | DD | No |
| `propulsion-domain-f` | DF | **Yes** (schemas.py: PropulsionSchema, MateType) |
| `vehicle-bodies-domain-g` | DG | **Yes** (schemas.py: domain enum) |

---

## Acceptance criteria (all workstreams)

For each workstream to be considered complete:

1. **Build:** `docker compose up --build` with no errors
2. **Pipeline:** prompt from the eval suite produces a non-empty GLB that renders
   in the web viewer without "geometry error" overlay
3. **STEP:** STEP file opens in FreeCAD or SolidWorks; `certify_geometry()`
   returns `passed: true` and `volume_mm3 > 0`
4. **No regressions:** `python -m eval_harness.runner --regression-for <letter>`
   shows no newly-failing IDs vs. main baseline
5. **Schema tests:** `python backend/tests/test_prompts_schema.py` passes
6. **Security (DF/DG only):** `mirum-security-reviewer` returns empty CRITICAL
   and HIGH sections
