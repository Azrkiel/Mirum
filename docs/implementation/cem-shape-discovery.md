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

## Paired comparisons and executable rechecks

An archived multi-station elite now offers **Compare shape with fixed hardware**
in Streamlit. Enable **Run independent XFOIL and AVL rechecks** to request
executable diagnostics. The typed route is `POST /cem/glider/compare-shape`:

```json
{
  "discovery_job_id": "<completed discovery job ID>",
  "elite_id": "<shaped archive elite ID>",
  "independent_recheck": true
}
```

Only an elite in that completed archive is accepted. The job reuses its pinned
mission, process and calibration; clients cannot supply replacement geometry.
Poll the usual `/cem/glider/jobs/{job_id}` endpoint. The UI offers the comparison
JSON and a return to the source archive. A comparison is not a design bundle and
cannot authorize geometry delivery.

The two arms use identical five-station geometry implementations. The control
sets every chord factor to one; the shaped arm retains the elite's factors.
Wing/fuselage/tail controls, placement, payload, ballast, tail incidence, materials
and the source stock-rod dimensions remain fixed. Mass, CG, inertia, aerodynamic
coefficients and spar stress are recomputed as consequences of the shape change.
The source rod cannot be resized to rescue an arm that fails its stress screen.
For elliptical-style wings, this control is the five-station unity-factor
approximation, not the separately discretized original template.

A fixed-geometry solve finds speed and alpha at zero pitching moment and the
existing screening approximation `L = W`. It cannot slide the wing, relocate the
payload, change ballast or adjust tail incidence. The usual trim, stability,
Reynolds/confidence, dimensions and process screens still apply. Both arms must
pass before `objective_delta = shaped - baseline` is reported; otherwise the job
is `inconclusive` with a null delta. These two evaluations are a separate job and
do not consume or silently enlarge the completed discovery budget.

`MIRUM_XFOIL_BIN` and `MIRUM_AVL_BIN`, or the server's `PATH`, select installed
executables. The worker receives these two server settings without provider
credentials. Each solver invocation has a 20-second timeout. Accepted raw input
and output artifacts have a 1 MiB per-file evidence limit. Executable hashes,
arm revisions, station conditions, raw artifact hashes and adapter source hashes
bind the diagnostics to the comparison. Parent validation reconstructs the
controls and the fixed rod, checks delta arithmetic, and re-parses completed raw
evidence rather than accepting summary labels alone.

XFOIL runs five geometric-angle samples around the primary equilibrium at every
wing and tail station's actual Reynolds number. Missing angles, nonfinite values,
nonpositive drag, execution errors and timeouts fail that station run. Reported
NeuralFoil/XFOIL coefficient differences are diagnostics, not calibrated error
bars or a performance-acceptance threshold. These geometric station angles do
not incorporate an independently solved downwash distribution or establish stall.

AVL receives all installed lifting surfaces, actual NACA camber, symmetry and CG
reference. Its raw lift, induced drag, pitching moment and required derivatives
must be finite and present. The diagnostic checks `|Cm| <= 0.01`, lift agreement
within 10% of primary equilibrium, and the existing static-margin/lateral-mode
screens at each arm's speed and mass. It includes no fuselage increments or
viscous drag. Missing executables are `unavailable`; failed or missing diagnostics
make the paired independent state `incomplete`. No analytic fallback can satisfy
this evidence. Successful diagnostics are still insufficient for a verified
whole-aircraft performance gain: `verified_gain=false`, manufacturing acceptance
and engineering certification remain false.

## Paired-comparison verification on this instance

The combined CEM, legacy glider, Streamlit, AVL-wrapper and XFOIL-wrapper run
completed **271 passed, 7 skipped** in 123.94 seconds. The seven pre-existing live
XFOIL tests require a binary on the default path; separate explicit-path native
runs below exercised the new diagnostic path. Two dependency deprecation warnings
were reported. `git diff --check` passed.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest \
  backend/tests/test_cem* backend/tests/test_glider* frontend/test_cem_ui.py \
  analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

Tests include real fixed-geometry equilibrium/admission, immutable hardware and
fixed-rod stress rejection, shared-station pairing, stale revisions, null deltas
for failed arms, archive membership, UI submission/rendering, missing executables,
real subprocess test doubles, missing station angles/derivatives, nonfinite and
unstable outputs, timeouts, and raw artifact/operating-command tampering. Test
doubles exercise orchestration and validation; they are not solver qualification.

A native HTTP run of the 16-evaluation example admitted eight shaped elites. The
best shaped elite's frozen-hardware pair predicted **128.614673 m** for the unity
control and **128.750732 m** for the shaped arm, a screening difference of
**+0.136060 m**. Both primary arms passed. The comparison did not change either
arm's placement or source rod, and the discovery retained exactly 16 evaluations.
A comparison bundle request returned HTTP 404 as intended.

The independent diagnostics remained **incomplete**. Debian XFOIL
`6.99.dfsg+1-3+b1` terminated with SIGFPE on both arms. Native AVL 3.32, built from
`RobotLocomotion/avl` source `b67837fa737afdf7a56655b044242619246b3ada`, produced
parseable full-lifting-surface reports but rejected both arms' spiral-divergence
screen. The paired result retained `verified_gain=false`; no independent gain,
manufacturing acceptance or flight validation was established. Executable hashes
and raw AVL artifacts were bound and checked in the parent. The HTTP source
fingerprint was `mirum-cem-glider-0.1.0+2165212f4cd3`.

The verification above was performed locally. Deployment, environment
publication and fresh-task restoration were not performed.

## Next shape-discovery slices

1. Couple the new paired comparisons and executable diagnostics to an independent
   whole-aircraft viscous performance calculation, including downwash/station
   operating conditions and fuselage effects, before claiming a verified gain.
2. Extend installed structure and closure to these planforms, including actual
   load transfer, then admit physical construction modes individually.
3. Add new family-specific builders and trim/stability admission. Do not reuse
   conventional tail-volume rules for tailless/canard/tandem aircraft or relabel
   the current planforms as those families.
