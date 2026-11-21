# E03 contract: rectangular plate in bending, `rectangular_plate` 1.0.0 (SIGNED)

State: **SIGNED** 2026-11-20 by the lead acting as domain engineer under the owner's explicit delegation in the
lead session ("E03: you are the domain engineer, do whatever it takes to make this good"). This satisfies E03
acceptance item 1. Every number below is fixed by this contract before any solver run; a change is a new contract
version with its own sign-off. Beam evidence is never reused for this family.

Changes from the 2026-11-20 draft, with reasons:
1. **Stress is the surface von Mises maximum over the whole plate, not a value at two declared points.** A simply
   supported plate carries twisting moment `Mxy` at the corners (corner force `R = 2 Mxy`, about `0.065 q a^2` for a
   square plate); the corner von Mises stress `sqrt(3) * 6 Mxy / h^2` can then exceed the center bending stress
   (`0.0479 q a^2` times `6/h^2`). R1 computes the exact field at implementation.
   Checking only the center would be unconservative. Rectangular plates with one edge condition on all four
   edges have no stress singularity at their 90-degree corners, so the full-field maximum converges.
2. **Thin-plate guard raised from `b/h >= 10` to `b/h >= 20`, plus a deflection shear allowance.** Kirchhoff theory
   under-predicts deflection (unconservative). Against Mindlin theory, the clamped square plate is about 19% low at
   `b/h = 10` and about 5% low at `b/h = 20`; the error scales with `(h/b)^2`. The checked deflection is
   `w_check = w_K * (1 + 20 (h/b)^2)`, an upper bound on that error for both edge conditions and every `a/b` in range.
3. **Two independent references, not book tables alone:** an analytic Navier series (SS) and a finite-difference
   biharmonic solver with Richardson extrapolation (SS and CL), both written separately from the FE solver; the book
   tables are a third, cited check.
4. **Simply supported is defined as "edges and corners held against lift".** Kirchhoff SS plates need corner
   hold-down forces `R = 2 Mxy`; the result reports them, and the limitation is stated to the user.
5. **No separate safety factor**, as the beam: the user's allowable stress is the design allowable and must name its
   source. The UI and the package say so.

## Supported meaning

One homogeneous, solid, flat rectangular plate of constant thickness `h`, in-plane dimensions `a` (X) by `b` (Y),
`a >= b`, mid-plane at Z = h/2 with the plate occupying `[0,a] x [0,b] x [0,h]`. Static uniform pressure `q` on the
+Z face acting in -Z. One edge condition on all four edges: `simply_supported` (transverse displacement zero on
every edge and at the corners, rotation free) or `clamped` (displacement and rotation zero). Isotropic linear-elastic
material, small deflection, Kirchhoff-Love thin-plate theory: `D = E h^3 / (12 (1 - nu^2))`. Units mm, N, MPa.

## Specification (`PlateSpec`, strict, frozen, unknown fields fail)

| Field | Type / bound |
|---|---|
| `family` | `"rectangular_plate"` |
| `units` | `"mm_N_MPa"` |
| `length_a_mm` | (20, 3000] |
| `width_b_mm` | (20, 3000], and `length_a_mm >= width_b_mm` (never swapped silently), `length_a_mm / width_b_mm <= 5` |
| `thickness_mm` | (0.1, 150] (one candidate; see selection) |
| `edge_condition` | `"simply_supported"` or `"clamped"` |
| `pressure_mpa` | (0, 10] |
| `youngs_modulus_mpa` | (0, 1e7] |
| `poissons_ratio` | [0.1, 0.45] |
| `allowable_stress_mpa` | (0, 1e5] (design allowable; no hidden factor) |
| `max_deflection_mm` | (0, 100] and `<= 0.02 * width_b_mm` |
| `dimensional_tolerance_mm` | [1e-5, 0.1] and `<= 0.01 * thickness_mm` |
| `material_reference` | 1-240 chars, with `source_kind` in {illustrative, user_supplied, documented} as the beam |
| `assumptions` | `"kirchhoff_uniform_pressure_uniform_edges_linear_elastic"` |

Invalid values are rejected, never clipped. Every value must be finite.

## Validity guards (each failure is a rejection naming the guard)

| Guard id | Rule |
|---|---|
| `thin_ratio` | `width_b_mm / thickness_mm >= 20` |
| `small_deflection` | `w_K,max / thickness_mm <= 0.2` (linear theory; membrane action ignored, conservative below this) |
| `aspect_ratio` | `1 <= a/b <= 5` |
| `material` | `E > 0`, `0.1 <= nu <= 0.45` |

## Selection

`fixed_design` (one thickness) or `bounded_thickness` (one to four candidates, sorted ascending, deduplicated, each a
full `PlateSpec`). Execution stops at the first candidate that passes every gate: **smallest passing thickness in the
reviewed set**. A candidate failing a validity guard is a measured rejection naming the guard. No global optimum claim.

## Model and solver (fixed)

- **Native solver:** conforming Bogner-Fox-Schmit rectangular element (4 nodes, DOF `w, w_x, w_y, w_xy` per node,
  bicubic Hermite), consistent load vector, quarter-plate model with symmetry conditions, dense NumPy solve
  (no SciPy, no new dependency). Edge conditions: SS sets `w = 0` and the tangential slope to zero on the edge
  (`w_xy` stays free: it carries the corner twisting); CL sets `w`, both slopes and `w_xy` to zero. Symmetry lines set
  the normal slope and `w_xy` to zero.
- **Stress recovery:** moments `Mx, My, Mxy` from the element curvatures at every element's 4x4 Gauss points and
  nodes; surface stresses `sigma = 6 M / h^2`; von Mises `sqrt(sx^2 - sx sy + sy^2 + 3 txy^2)`; the maximum over the
  plate is the checked stress.
- **Mesh levels:** `n_b = 4, 8, 16` elements across the quarter's short side, `n_a = ceil(n_b * a/b)`, total DOF
  capped at 6,000 (the cap lowers `n_a` and is recorded; it never skips a level).
- **Reactions and corner forces:** from the assembled system residual at constrained DOF; SS corner forces reported.

## Numerical policy `plate_numerical_v1` (fixed before any run)

| Check id | Threshold |
|---|---|
| `mesh_convergence_deflection` | relative change of `w_max` between the last two levels `<= 0.2%` |
| `mesh_convergence_stress` | relative change of max von Mises between the last two levels `<= 1.0%` |
| `equilibrium` | `abs(sum(reactions) - q a b) / (q a b) <= 1e-9` |
| `symmetry` | quarter model by construction; full-plate check fixtures only |
| `benchmark_deflection` | finest-level `w_max` vs the independent reference `<= 0.5%` |
| `benchmark_stress` | finest-level max von Mises vs the independent reference `<= 1.5%` |
| `deflection_limit` | `w_check = w_K,max * (1 + 20 (h/b)^2) <= max_deflection_mm` |
| `stress_limit` | max surface von Mises `<= allowable_stress_mpa` |
| geometry | STEP: one solid, six planar axis-aligned faces, bounding box `a, b, h` within `dimensional_tolerance_mm`, volume `a b h` within `1e-6` relative |

A level that does not converge is `not_evaluated` for its claim and the job fails closed (`EngineeringUnavailable`
for missing evidence; a measured non-convergence is a rejection with guard `mesh_convergence_*`). Never clipped,
never downgraded to geometry-only acceptance.

## Independent references (in the verifier, separate code from the FE, written by a different worker)

| Ref | Method | Used for |
|---|---|---|
| R1 | Navier double sine series, odd `m, n` until the relative change of each quantity is `< 1e-10` (cap 401 terms each); full-field `w, Mx, My, Mxy` | SS: deflection and von Mises maximum |
| R2 | Finite-difference biharmonic (13-point stencil; SS by odd reflection, CL by even reflection), grids `N, 2N, 4N` with Richardson extrapolation | SS and CL: center deflection and the moment maxima |
| R3 | Timoshenko and Woinowsky-Krieger, *Theory of Plates and Shells*, 2nd ed. (1959): Table 8 (SS, uniform load), Table 35 (CL, uniform load), `nu = 0.3`; cross-checked against Roark's *Formulas for Stress and Strain*, Table 11.4 cases 1a and 8a. Values and sources recorded in `backend/tests/fixtures/engineering/plate_benchmarks-v1.json` | cited spot check (tolerance: half a unit in the last printed digit plus 0.5%). Only rows confirmed at sign-off are used: CL Table 35 all rows (read from the book page; deflection column also in Imrak 2007); SS Table 8 rows `a/b = 1.0, 1.2, 1.5, 1.7, 2.0` (two secondary sources each). Other SS rows rely on R1 alone |
| R4 | Strip limits at `a/b = 5`: SS `w -> 5 q b^4 / (384 D)`, CL `w -> q b^4 / (384 D)`, CL edge moment `-> q b^2 / 12` | limit check within 2% |

Reference square-plate values (R3, to be re-verified at implementation against the cited sources):
SS `w = 0.00406 q b^4 / D`, `Mx = My = 0.0479 q b^2` at center; CL `w = 0.00126 q b^4 / D`, edge moment
`-0.0513 q b^2`, center `0.0231 q b^2`.

## Claims and their gates

| Advertised claim | Gate |
|---|---|
| Geometry is the declared plate | geometry row above, measured from the STEP by the independent OCP reader |
| Maximum deflection within the limit (Kirchhoff + shear allowance) | `deflection_limit`, `mesh_convergence_deflection`, `benchmark_deflection`, `small_deflection` |
| Maximum surface von Mises stress below the allowable | `stress_limit`, `mesh_convergence_stress`, `benchmark_stress` |
| Load equilibrium | `equilibrium` |
| Within the validity envelope | all guards |

`engineering_certified` stays false; standards stay `not_evaluated`. The claim category is `checked_design` only after
E03 qualification (card step 5); until then the family is not registered or listed.

## Exclusions (each needs a rejection fixture)

Holes or cutouts, stiffeners or ribs, thickness variation, mixed or partial edge conditions, free edges, point
supports, corner lift-off (SS without hold-down), large deflection, buckling or in-plane loads, laminates,
composites, anisotropy, sandwich panels, dynamic or impact loads, fatigue, contact, thermal loads, non-uniform or
point loads, thick-plate shear deformation beyond the allowance, plasticity, manufacturing variation, physical
material qualification, standards certification. Unknown specification fields fail.

## Failure cases to qualify

`thin_ratio` violated; `small_deflection` violated; `a < b`; `a/b > 5`; all candidates failing; non-finite input;
unsupported edge condition; `nu` out of range; mesh non-convergence (forced by a test hook in the test suite only);
benchmark mismatch beyond tolerance (forced); extra feature in the STEP (hole, fillet, second solid); missing native
evidence -> `EngineeringUnavailable`. Out-of-family prompts end `unsupported` or in clarification, never a
substituted capability.

## AI proposal boundary

Unchanged from the draft: AI may propose only typed inputs (dimensions, pressure, edge condition, material
reference, candidate thicknesses), every value grounded in the prompt (the E05 grounding rule), shown as a reviewed
brief. Missing `q`, edge condition or material triggers clarification. AI never sets tolerances, limits or acceptance.
No AI path is built for this family in E03.

## Implementation phases

- **E03a core** (new files only): `plate_spec.py`, `plate_fea.py` (R-free FE), `plate_reference.py` (R1, R2, R4),
  `plate_geometry.py` (STEP), `plate_verify.py` (OCP measurement + all checks), benchmark fixture and tests.
- **E03b integration + qualification**: adapter, registration, router, API, package, UI, every failure fixture,
  evidence and STATUS. Starts after E03a and the E05 fix loop merge.

## Alternatives considered

| Family | Why not first |
|---|---|
| L-bracket, 3-D FEA | 3-D meshing, load/support mapping and re-entrant corner singularities; no closed-form benchmark |
| Euler column | buckling knockdown is empirical; benchmark depends on imperfection assumptions |
