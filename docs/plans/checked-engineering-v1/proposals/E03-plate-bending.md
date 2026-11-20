# E03 proposal: rectangular plate in bending (draft, not approved)

State: PROPOSAL. Nothing here is an approved physics envelope. No solver may be written until the owner or a
domain engineer signs the contract (E03 acceptance item 1; see [handoffs/E03.md](../handoffs/E03.md)).
Written in the style of [CAPABILITY.md](../CAPABILITY.md); beam evidence is never reused for this family.

## Why this family

Chosen by the lead under the owner's delegation, to serve a combined system: AI proposes typed inputs,
deterministic code sizes and verifies. Different structural model (2-D bending) from the beam, independent
closed-form benchmarks, small exclusion list.

## Proposed supported meaning

One homogeneous, solid, flat rectangular plate, constant thickness `h`, in-plane dimensions `a` (X) by `b` (Y),
`a >= b`. Static, uniform normal pressure `q` on one face. One edge condition for all four edges:
`simply_supported` (SS) or `clamped` (CL). Isotropic linear-elastic material, small deflection. Units mm, N, MPa.

| Parameter | Proposed range (all to be confirmed by sign-off) |
|---|---|
| `a`, `b` | bounds to be set by reviewer; `1 <= a/b <= 5` proposed |
| `h` (sized by the deterministic side) | reviewed candidate set of 1 to 4 values, like `bounded_thickness` |
| `q` | positive, reviewer-set bound |
| edges | `simply_supported` or `clamped`, never mixed in V1 |
| material | `E`, `nu`, allowable stress: user-supplied, illustrative only, never certified stock |

Manufacturing objective: smallest passing thickness in the reviewed set (as the beam). No global optimum claim.
Material and allowable inputs need an explicit user-approved reference; their presence does not certify
properties. `engineering_certified` stays false; standards stay `not_evaluated`.

## Declared model and validity limits

Kirchhoff-Love thin-plate theory, small deflection: `D = E h^3 / (12 (1 - nu^2))`.
Proposed validity guards (numbers are reviewer decisions, listed as the proposal):
- span/thickness: `b/h >= 10` (thin plate); below this the request is rejected, not clipped (shear deformation excluded).
- `w_max / h <= 0.2` proposed (membrane stiffening ignored below this); above it, reject as large deflection.
- stress below the supplied allowable with the reviewer-set safety factor; linear range only.
- `0 < nu < 0.5`, `E > 0`, all inputs finite.

## Claims and acceptance gates

| Advertised claim | Gate |
|---|---|
| Geometry is the declared plate | independent STEP measurement of `a`, `b`, `h`, flatness, single solid, digital tolerances |
| Max deflection within limit | solver `w_max` vs closed form within a declared tolerance; limit from `q`, edges |
| Max bending stress below allowable | solver stress vs closed form; peak location and treatment per the singularity section |
| Load equilibrium | sum of reactions equals `q a b` within a declared relative tolerance |
| Mesh convergence | at least three refinements, convergence of `w_max` and stress at declared locations |
| Within validity envelope | `b/h`, `w_max/h`, `a/b`, material guards above; failure is rejection naming the guard |

Every tolerance is set by the signed contract before any run. Missing native evidence is `EngineeringUnavailable`,
never a downgrade to geometry-only acceptance.

## Independent benchmarks

Solver choice (closed form or 2-D Kirchhoff FE) is open; either is cross-checked against sources outside the solver.
- Simply supported, uniform load: Navier double sine series (Timoshenko and Woinowsky-Krieger, *Theory of Plates and
  Shells*, simply supported rectangular plate under uniform load); Levy single series as a second check.
- Clamped on all edges: coefficient tables in the same book (rectangular plates with built-in edges, uniform load,
  deflection and moment coefficients by `a/b`). Coefficients are NOT reproduced here; every value used is
  to be verified against the source table, with edition and table number recorded in the evidence.
- Limit check: large `a/b` tends to cylindrical bending, the beam-strip results (SS `5 q L^4 / (384 D)`, CL
  `q L^4 / (384 D)` per unit width); to be verified at implementation.
Benchmarks run in the native verifier, independent of the generator's own formulas.

## Convergence and singularities

SS plate: stress converges with refinement. Clamped plate: stress peaks at mid-edge; corner results from FE can be
mesh sensitive. Proposed rule: report stress only at declared locations (center for SS, mid-edge for CL), away
from corner elements, and require refinement convergence there. A non-converging location is reported as
`not_evaluated`, never clipped.

## Exclusions (each needs a rejection fixture)

Holes or cutouts, stiffeners or ribs, thickness variation, large deflection (membrane action), buckling, laminates
or composites, anisotropy, dynamic or impact loads, fatigue, contact, thermal loads, mixed or point supports,
non-uniform or point loads, thick-plate shear deformation, manufacturing variation, physical material
qualification, standards certification. Unknown specification fields fail.

## Failure cases to qualify

Thin-ratio violation, `w/h` above limit, reviewed set all failing, non-finite input, unsupported edge condition,
solver non-convergence, benchmark mismatch beyond tolerance, extra feature in STEP. Out-of-family prompts end
`unsupported` or in clarification, never a substituted capability.

## AI proposal boundary

AI may propose only typed inputs (span, aspect ratio, pressure, edge condition, material reference, candidate
thicknesses), shown to the user as a reviewed brief. Deterministic code sizes the thickness, runs the checks and
decides acceptance. Unstated values are never invented: missing `q`, edges or material triggers clarification.
AI output never sets tolerances, limits or acceptance.

## Alternatives considered

| Family | Why not first |
|---|---|
| L-bracket, 3-D FEA | needs 3-D meshing, load/support mapping and singular re-entrant corners (card step 4 cost); no simple closed-form benchmark |
| Euler column | buckling knockdown is empirical; benchmark depends on imperfection assumptions |

## Decision needed

Sign-off of this written contract (or an edited one) by the owner or a domain engineer: parameter ranges, validity
guards, tolerances, benchmark table editions. Until then E03 stays blocked.
