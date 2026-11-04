# CEM continuation: bounded wing planform discovery

Continues `cem-next-phase` at `ffe8d34a3930443d1b7bbb021e3224815f514357`.
The selected direction is new shape discovery with explicit screening limits.
This slice expands wing planforms while retaining conventional, T-tail and V-tail
layouts. It does not implement a tailless, canard, tandem or other new family.

## Use it

Select **Physics-based glider CEM**, then **Wing planform search → Five-station
chord shapes (screening only)** in Streamlit. The typed equivalent is
`discovery.wing_shape="multi_station"`; see
[`examples/cem/glider-shape-search.json`](../../examples/cem/glider-shape-search.json).
Omitting the option retains the existing template search. Advanced mission JSON
continues to override the form.

Only `construction="screening_oml"` is supported for this shape mode. Requests
combining it with either physical foam-core construction fail validation rather
than substituting a different construction. The segmented-wing control remains
available separately.

## Geometry and search

The five normalized half-span stations are 0, 0.275, 0.55, 0.775 and 1. Three
interior chord factors vary independently from 0.85 to 1.15; root and tip factors
stay at one. Chords interpolate linearly between stations. Sweep follows the
base quarter-chord path; incidence, washout and dihedral retain their existing
controls. The polyhedral joint stays at 0.55 half-span.

`cem/glider/shapes.py` supplies the same station geometry to aerodynamic
surfaces, mass strips, meshes and templates. This is an expanded piecewise-linear
planform space, not B-spline/CST airfoil optimization. Spar clearance screening
now checks the minimum station chord, including interior minima.

The initial comparator and every fourth evaluation use existing templates. The
remaining evaluations use seeded chord-shape exploration and bounded local
mutation of admitted parents. Shape factors add archive descriptors, so distinct
planform cells can survive without winning the same aspect-ratio/loading cell.
All evaluations, including the comparator, count against the existing budget.

Shape factors are saved in the wing parameters and therefore participate in elite
revision hashes and the design/artifact binding. Build and parent verification
reconstruct the shaped wing rather than dropping its controls. The parent rejects
shape parameters in a baseline mission and rejects result labels/factors that
differ from their archived geometry revision.

## Screening limits

- `parametric_wing_shape_discovery=true` describes this capability.
  `novel_shape_discovery=false` remains: there is no independent novelty metric,
  novel aircraft-family builder or verified claim of a superior new design.
- Archive elites retain `novelty_state="not_claimed"` and explicit missing
  independent XFOIL/AVL rechecks. Their layout remains an existing layout.
- The old feasibility envelope is withheld for this search:
  `bound_state="rebuild_required_for_shape"`, `optimistic_bound=null` and
  `verdict="marginal"`. Maximization continues; a reference target cannot stop it.
- Every actual candidate still passes or fails the existing trim, stability,
  speed, dimensions, process and per-station Reynolds/confidence screens.
- The structural model still assumes ideal elliptical total lift. It does not
  recover a shape-specific aerodynamic load distribution or flexible-wing loads.
  OML areal-density mass is not fabricated-material closure.
- A discovered candidate can fail the separate delivered-geometry closure gate.
  Rejection blocks bundle delivery. Manufacturing acceptance and engineering
  certification remain false regardless of nominal performance.

## Current-instance evidence

The 16-evaluation example, seed 42, admitted eight multi-station elites. Its best
shaped elite predicted about 128.78 m from a 10 m still-air drop. The initial
comparator predicted about 119.48 m. Other wing, fuselage and tail parameters
also changed during search: this comparison does not isolate a chord-shape gain
and does not establish independently verified improvement.

The live design job for that shaped elite was **rejected**: the straight stock
spar lacked delivered-wing enclosure evidence and measured-mass trim requested a
geometry revision. The bundle endpoint returned HTTP 409. These are existing
physical-closure limitations exposed correctly by the expanded search.

The separate segmented-foam control completed as **screened**, with an HTTP-200,
SHA-256-verified ZIP containing 18 STLs. Its discovery objective was about 123.15 m.
No physical flight, independent solver qualification or fabrication acceptance is
established by either run.

Tests cover real solver admission for a shaped candidate; real shape-to-mesh and
mass changes; exact station reconstruction; invalid/nonfinite controls; seeded
sampling, budget accounting and local bounds; parent rejection of relabelled
shape evidence; feasibility invalidation; and Streamlit form/advanced-JSON
submission. Existing CEM, segmented geometry, API and legacy-glider regression
checks are included in the combined verification command.

The final combined run completed **225 passed**, with one existing
Starlette/AnyIO deprecation warning, in 141.50 seconds. `git diff --check` passed.
After a final backend restart, the HTTP shape search reproduced the same eight
shaped elites and rejected design under source fingerprint
`mirum-cem-glider-0.1.0+3eac1dce3117`; its bundle endpoint again returned HTTP 409.
The isolated dependency check and UI health request passed. Reusable install and
startup instructions were saved to the environment draft. Publication and
fresh-task restoration were not performed. This verification does not establish
deployment or environment publication.

## Next shape-discovery slices

1. Add paired comparisons holding all non-shape parameters fixed, and independent
   XFOIL/AVL rechecks before claiming a verified gain.
2. Extend installed structure and closure to these planforms, including actual
   load transfer, then admit physical construction modes individually.
3. Add new family-specific builders and trim/stability admission. Do not reuse
   conventional tail-volume rules for tailless/canard/tandem aircraft or relabel
   the current planforms as those families.
