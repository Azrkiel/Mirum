# Rectangular plate capability 1.0.0 (`rectangular_plate`)

Integrated by Wave E E03a (#115, solver and references) and E03b (#116, adapter, router, API, package, UI,
qualification). The signed contract is [proposals/E03-plate-bending.md](proposals/E03-plate-bending.md); this page
describes what the integrated capability does and never changes a contract number. Where a number is quoted it is
quoted as the contract states it. The descriptor is `PLATE` in `backend/engineering/capabilities.py`, appended after
the other generators (the beam stays first). Evidence: [evidence/E03.json](evidence/E03.json). Handoff:
[handoffs/E03.md](handoffs/E03.md).

## Supported meaning

One homogeneous, solid, flat rectangular plate of constant thickness `h`, in-plane dimensions `a` (X) by `b` (Y),
`a >= b`, occupying `[0,a] x [0,b] x [0,h]`. Static uniform pressure `q` on the +Z face acting in -Z. One edge
condition on all four edges: `simply_supported` (transverse displacement zero on every edge and at the corners,
rotation free; edges and corners are held against lift) or `clamped` (displacement and rotation zero). Isotropic
linear-elastic material, small deflection, Kirchhoff-Love thin-plate theory with `D = E h^3 / (12 (1 - nu^2))`.
Units mm, N, MPa.

## Typed inputs (`PlateBrief`)

`PlateBrief` is strict, frozen and rejects unknown fields; invalid values are rejected, never clipped, swapped or
defaulted, and every value must be finite.

| Field | Meaning |
|---|---|
| `mode` | `fixed_design` (one thickness) or `bounded_thickness` (one to four candidates) |
| `requirements` | `PlateRequirements`, shared by every thickness (below) |
| `candidate_thicknesses_mm` | the reviewed thickness set; sorted ascending and deduplicated |

`requirements` holds `family` (`rectangular_plate`), `units` (`mm_N_MPa`), `length_a_mm`, `width_b_mm`,
`edge_condition`, `pressure_mpa`, `youngs_modulus_mpa`, `poissons_ratio`, `allowable_stress_mpa`,
`max_deflection_mm`, `dimensional_tolerance_mm`, `material_reference` (`source_kind` in illustrative,
user_supplied, documented, plus a 1-240 character reference) and `assumptions`
(`kirchhoff_uniform_pressure_uniform_edges_linear_elastic`). Bounds are those of the contract's specification table
and are read from `PLATE_NUMERICAL_V1` in `backend/engineering/plate_spec.py`; they are not repeated here.
`length_a_mm >= width_b_mm` is never swapped silently.

## Selection

Smallest passing thickness in the reviewed set of one to four. One generator candidate runs the whole set in
ascending order (`PlateAdapter`), one native subprocess per thickness, and stops at the first thickness that passes
every gate. A thickness that fails a guard or check is a measured rejection naming it. No global optimum claim.

## Required checks (13, in execution order)

The order is `PLATE_CHECK_ORDER`; the first failed entry is the real rejection. Four guards, then nine checks. Every
limit is read from `PLATE_NUMERICAL_V1` (`backend/engineering/plate_spec.py`), the single source of the contract's
numerical policy; the descriptor reads it programmatically and does not retype it.

| # | Id | Kind | Limit source |
|---|---|---|---|
| 1 | `thin_ratio` | guard | contract validity guard `thin_ratio`, `PLATE_NUMERICAL_V1` |
| 2 | `aspect_ratio` | guard | contract validity guard `aspect_ratio`, `PLATE_NUMERICAL_V1` |
| 3 | `material` | guard | contract validity guard `material`, `PLATE_NUMERICAL_V1` |
| 4 | `geometry` | check | STEP measured by the independent OCP reader against `a, b, h` and `dimensional_tolerance_mm`; one solid, six planar axis-aligned faces; volume `a b h` |
| 5 | `small_deflection` | guard | contract validity guard `small_deflection` (`w_K,max / thickness_mm`), `PLATE_NUMERICAL_V1` |
| 6 | `equilibrium` | check | `abs(sum(reactions) - q a b) / (q a b)` against the contract's `equilibrium` threshold |
| 7 | `mesh_convergence_deflection` | check | last two mesh levels, contract threshold |
| 8 | `mesh_convergence_stress` | check | last two mesh levels, contract threshold |
| 9 | `symmetry` | check | quarter model by construction; full-plate check fixtures only |
| 10 | `benchmark_deflection` | check | finest level vs the independent reference (R1 Navier for SS, R2 finite difference for CL), contract threshold |
| 11 | `benchmark_stress` | check | finest level vs the independent reference, contract threshold |
| 12 | `deflection_limit` | check | `w_check = w_K,max * (1 + 20 (h/b)^2) <= max_deflection_mm` |
| 13 | `stress_limit` | check | maximum surface von Mises over the whole plate `<= allowable_stress_mpa` |

The model is the contract's conforming Bogner-Fox-Schmit element on a quarter plate at mesh levels
`n_b = 4, 8, 16`. A level that does not converge fails closed: measured non-convergence is a rejection with guard
`mesh_convergence_*`; missing evidence is `EngineeringUnavailable`. Nothing is clipped or downgraded to
geometry-only acceptance.

## Claim and honesty fields

- Claim `checked_design`. The `claim` Literal was widened for the plate only; other generators' labels are unchanged.
- `engineering_certified` is false. Standards are `not_evaluated`.
- No AI path in E03: `proposers` is `{}`. No proposer, no provider call, no budget.

## Native execution policy

One subprocess per thickness, with the beam's policy: 30 s timeout, 1536 MiB memory, 1 MiB output limit
(`ProcessRunner`, sourced `backend/engineering/service.py:34`) and the same environment allowlist as the beam
(`service.py`). The native stage must be available; an unavailable stage or a spec-digest mismatch is
`unavailable`, never an acceptance.

## Package contents

Primary artifact `generator-body.step`; payloads `generator-body.step`, `generator-report.json`,
`generator-spec.canonical.json`. The report carries per-thickness attempts, the mesh history, the references used,
the guards and checks, the simply supported corner forces and the implementation hashes. It contains no filesystem
paths. The `checked_design` heading and outcome line are the only package text that differs from the other claims.

## User-facing notes

- Simply supported means edges and corners are held down; the corner hold-down forces are reported because the
  Kirchhoff plate needs them (`R = 2 Mxy`). A plate whose corners may lift is outside the capability.
- The allowable stress is the user's design allowable and must name its source. No hidden safety factor is applied.
- Deflection includes a shear allowance: `w_check = w_K (1 + 20 (h/b)^2)`, where `w_K` is the Kirchhoff value.
  Thick-plate shear deformation beyond that allowance is excluded.
- The checked stress is the surface von Mises maximum over the whole plate (for a simply supported square plate it
  can sit at the corner), not a value at declared points.

## Router behaviour and exclusions

`_plate()` in `backend/engineering/router.py` routes a prompt to `rectangular_plate` only when it names a plate
with rectangular/flat/panel wording, a uniform/distributed/pressure load, a simply supported, clamped or fixed edge
condition, and sizing intent (size, thickness, deflection, displacement or stress). It refuses prompts that name an
excluded feature (holes or cutouts, ribs or stiffeners, free edges, cantilevers and beams, point loads, buckling or
in-plane loads, composites, thermal or dynamic loads, non-rectangular outlines, variable thickness and the other
contract exclusions) and a prompt that also matches another capability is ambiguous. The word "beam" overrides the
plate rule, so a cantilever beam stays a beam. A plate-like prompt outside the family ends `unsupported`, never a
substituted capability. Every contract exclusion has a rejection fixture (`unsupported_features` in the descriptor).

## Known limitations

- Job-level failure text is the search layer's fixed "No candidate in the reviewed set passed". The named guard or
  check that failed is in each attempt's `failures`, in the attempts payload and the report, not in the job reason.
- Digital checks only: no physical validation, material certification, manufacturing variation or standards
  conformity.
- No AI proposal path; the plate is entered through the typed form or the typed API only.
- Beam evidence is not reused; the plate has its own qualification ([evidence/E03.json](evidence/E03.json)). No
  plate replay (V12) qualification was run in this phase.
