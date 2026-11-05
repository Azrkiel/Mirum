# Task C implementation and qualification handoff

Latest dynamics-and-controls continuation: [full-inertia linearization with
unsteady terms, step-studied derivatives, mode requirements, physical-surface
control authority and declared-envelope qualification](cem-aircraft-dynamics-control.md),
with its [continuation prompt](cem-aircraft-dynamics-control-continuation.md).
Validated against analytical limits and a published 747 reference; **no family
revision or envelope is numerically qualified** (body, junction, hardware,
hinge-moment, rate/unsteady aerodynamic and requirement inputs are missing; no
yaw control is declared).

Previous aircraft-physics continuation: [force/moment ledger, full-inertia mass
properties, bounded trim, dynamics and missing-input interfaces](cem-aircraft-physics.md),
with its [continuation prompt](cem-aircraft-physics-continuation.md). Machinery
is analytically validated; no actual aircraft is trimmed or qualified.

Latest shared integration: [A into C aerodynamic integration](cem-a-c-integration.md)
merges A's `aae2b7e` and `26cd528` after C's `f921387` continuation. See that
record for merged-runtime validation and the coordinate-family work still open.

Software continuation and current evidence:
[cem-task-c-continuation.md](cem-task-c-continuation.md). The user elected to
leave reference-dependent qualification gates open; the native development
workflow and coordinate/control representation do not grant production admission.

Next software continuation: [native control-response evidence](cem-task-c-control-response.md),
with two-mesh fixed-operating-point channel sweeps and authoritative native replay.

Current aerodynamic continuation: [coordinate polars, coupled lifting loads and
qualification blockers](cem-family-aerodynamics-progress.md). Whole-aircraft
qualification remains incomplete; reference research requires configured access.

The three new families have isolated screening prototype builders in
`backend/cem/glider/families/`. They are **not admitted production families**.
Existing conventional, T-tail and V-tail search remains the production path.

## Implemented scope

| Family | Real geometry | Controls | Fixture rejection policy |
| --- | --- | --- | --- |
| Tailless swept | One swept lifting wing, explicit trailing-edge reflex camber | Symmetric pitch and differential roll elevons | Whole-aircraft trim, static margin, modes, root-before-tip stall margin |
| Canard | Smaller forward lifting plane and separate aft main wing | Foreplane elevator and main-wing aileron | Both directions of interference, foreplane lift share and foreplane-first stall |
| Tandem | Two substantial lifting wings with separate incidence and vertical separation | Front/rear elevators and rear-wing aileron | Both directions of interference, two-wing lift sharing and front-first stall |

All policies also reject missing or nonfinite evidence, missing modes, saturated
controls, incomplete lifting-surface coverage, lift/weight imbalance and stale
geometry identity. There is no conventional tail-volume gate. The numerical
thresholds are development screening policies, not qualified engineering limits.
Positive injected cases demonstrate code paths, not genuine aircraft stability.

Each builder has bounded typed inputs, distributed areal-mass strips, a body and
equipment mass inventory, declared controls and a source-bound revision. Archive
reconstruction checks the complete parameter set, builder identity and generated
geometry identity. Source changes to the builder or common geometry invalidate
old archives. Production provenance additionally hashes every CEM Python source.

`development.search` samples each family's actual incidence/washout parameters
with a fixed seed and bounded evaluation count. It requires an injected fixture
solver. `development.compare` reconstructs two same-family archives with fixed
equipment/body/material inventory. It never reports a performance delta or a
verified gain. These are development harnesses, not the native search/comparison
and design delivery required for final Task C acceptance.

`export_screening` reconstructs the archived geometry and exports native
AeroSandbox lifting-surface meshes through the existing STL writer, in mm, with
SHA-256 identities and an explicit screening manifest. Reflex is included in the
actual exported airfoil coordinates. Body, hardware, hinges and structural parts
are explicitly absent; this export is not a manufacturing/design bundle.
The mass inventory is not closed against these exported lifting OML meshes.

API metadata and discovery capability reports expose geometry, numerical
performance and physical construction separately. The UI keeps these families
out of the production selector and explains their unavailable status. Requests
selecting only unqualified families remain HTTP 422.

## Dependencies and remaining acceptance work

Historical branch heads inspected during the initial prototype implementation:

- A: `cb521103a748ac8a59c4bc4bb57c0796d5b3a13a`.
- B: `bee72785666c1e89e0a07fc1be378770fcd49586`.

At that point both contained task briefs. The subsequent A/B integration is
recorded below. A owns the shared contract; Task C does not enable native
admission from caller-supplied labels.

Requirements for the A handoff:

- Installed geometry including actual reflex coordinates, incidence/washout,
  surface roles, control hinge/span definitions, CG and inertia. The prototype
  has lumped/distributed mass and CG. The shared adapter now supplies diagonal
  point-mass inertia approximations, not measured full-aircraft inertia.
- Revision binding of geometry, controls, hardware, mission, calibration,
  process, atmosphere, operating solution, adapters and solver/raw artifacts.
- Effective local angle, local velocity and Reynolds coverage, station polars
  and loads, plus induced-angle evidence in both directions between lifting
  surfaces. A zero interaction must be supported rather than assumed.
  Since 2026-11-05 the [low-Re decision](cem-low-re-method-decision.md) applies:
  every surface station must be at Re ≥ 50,000 at the lowest speed. Station
  polars come from NeuralFoil with its confidence gate, and XFOIL is used as
  single-point spot checks. The current canard and fore surfaces (about Re 33,000
  at 6 m/s) must be resized to clear the floor.
- Force/moment closure, stable/unstable native reference cases, numerical
  convergence, control authority and dynamic eigenmodes in explicit axes/units.
- Family-specific stall evidence: root-first with retained elevon authority for
  swept tailless; foreplane-first for canard; declared front-first policy for
  tandem. Development scalar stall fixtures do not establish coverage of the
  nonlinear interaction/stall envelope.
- A tailless reflex/elevon-capable solver representation and directional-control
  strategy. No vertical stabilizer or yaw-control geometry is qualified here.

Requirements for the B handoff:

- Actual load transfer, attachments, torsion and elevon/aileron hinge loads in a
  swept wing, including reflexed section spar-clearance constraints.
- Foreplane/main-wing and tandem front/rear-wing attachment and body load paths,
  interaction-aware span loads and family-specific launch/load cases.
- Delivered parts, material law, mass/CG/inertia closure, hardware and control
  accounting, reconstruction and artifact checks for each family separately.

As those services become qualified, integrate each qualified family sequentially into
production registration, search, archive evidence validation, exact design
reconstruction, comparison, worker, API/UI and delivered bundles. Native
end-to-end qualification, deployment, publication and fresh-task restoration
remain unperformed. Flying plank, box wing and blended body remain unsupported.

## Development checks

Use the existing checkout; cloud tasks are already isolated. Do not create a
Git worktree unless the user explicitly requests one.

```sh
cd /workspace/Mirum
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 /workspace/mirum-venv/bin/python -m pytest \
  backend/tests/test_cem_aircraft_families.py -q
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 /workspace/mirum-venv/bin/python -m pytest \
  backend/tests/test_cem* backend/tests/test_glider* frontend/test_cem_ui.py \
  analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

Fixture tests are deliberately labelled as fixture evidence. They cannot enable
`independent_numerical_performance`, `physical_construction` or `verified_gain`.

## Current-instance validation

- Combined regression command above: **334 passed, 7 skipped** in 134.42 s.
  The skipped checks require a live XFOIL executable. One existing Streamlit /
  Altair deprecation warning was reported. The API tests require local socket
  access; a sandboxed TestClient attempt stalled during portal startup, while
  the same test passed with local socket access.
- Native HTTP metadata returned 200 and the three separate prototype capability
  records. Each new-family-only production search returned 422 as required.
- A native conventional-family HTTP discovery job completed exactly eight
  evaluations and returned two candidates. Prototype production gates remained
  false in the parent-validated report. This checks existing-family continuity,
  not native new-family qualification.
- Streamlit health returned 200 / `ok`; the UI form and comparison paths were
  exercised by the regression suite.
- Dependency compatibility and repeat installation passed. `git diff --check`
  passed. The environment draft saves the CEM installation/start instructions
  and this checkout's branch-head SHA. Publication and fresh-task restoration
  have not been performed; review/save the draft in environment settings and
  publish when appropriate. The initial implementation was committed as
  `a4d7101`; nothing has been pushed.

## Native diagnostic continuation

`families/diagnostics.py` now exercises installed AeroSandbox VLM with the actual
family geometry, including the reflexed airfoil coordinates. Reference area is
the sum of installed lifting-wing areas, span the largest installed span and
reference chord their area-weighted mean aerodynamic chord. This avoids silently
using the canard foreplane as the whole-aircraft reference. Archive creation also
copies parameters so editing a returned archive cannot alter the live aircraft.

The diagnostic holds equipment, incidence, ballast and all geometry fixed. It
brackets pitching-moment zero within declared alpha limits, rejects missing or
ambiguous roots, then scales velocity to L=W. It recomputes force, moment and
alpha derivatives at that solution and applies the family's static-margin
screen. All raw operating samples, reference quantities, geometry revision,
conditions, mesh resolutions and diagnostic/library source identities are in
the JSON report. Invalid or nonfinite output raises an error, never a pass.

Two meshes assess sensitivity in alpha (0.5 degrees), relative speed (5%) and
static margin (0.02). These are development diagnostic limits, not a qualified
error estimate. Default resolutions 3 and 6 expose mesh sensitivity in the
positive reference cases. Refining to 6 and 9 produced:

| Family | Equipment x (m) | Alpha (deg), fine mesh | Speed (m/s), fine mesh | Static margin, fine mesh | State |
| --- | --- | --- | --- | --- | --- |
| Tailless swept | 0.22 | 6.518961 | 5.197095 | 0.078272 | diagnostic_equilibrium |
| Canard | 0.30 | 0.664705 | 6.076586 | 0.127629 | diagnostic_equilibrium |
| Tandem | 0.10 | -1.493706 | 9.061661 | 0.099099 | diagnostic_equilibrium |

Each reference starts from that family's defaults with the listed equipment
position set **before** archive generation. The solver never adjusts that
position to rescue an arm. These are separate reference cases, not comparisons
with the default hardware. The default canard is untrimmable within the declared
alpha interval; the default tandem moment root has nonpositive lift. A canard
with equipment at x=0.60 m has positive native Cma and is not admitted. Native
tests retain those rejection cases alongside the positive equilibria.

This inviscid calculation does not evaluate profile/body drag, real glide
performance, control authority, nonlinear interference/stall, dynamic modes or
construction. `diagnostic_equilibrium` is therefore not a qualified family, and
the report is rejected by the admission evidence schema. All production,
performance, physical and verified-gain capabilities remain false.

## A/B shared-contract integration

A implementation/handoff `2191583cb05188b50fb19924e25a3e929fd1f7fa` was merged
first, with C's contract adapters in merge `2db4b70`. The complete A/C regression
run passed **362 tests**, with **8 native solver skips**. B was then obtained
from the user-supplied commit `5f5fc120163e18d37daf765e5956635a63bb1911` and merged
sequentially. Its remote task branch still pointed at its original brief; the
supplied commit contains the actual implementation and qualification handoff.

`families/contracts.py` implements A's `FamilyBuilder` interface for faithful
`mirum-aero-1` geometry, with these explicit limits:

- Canard and tandem retain their actual `canard`, `forewing` and `aftwing` roles;
  no member is renamed to a conventional horizontal tail.
- Reference area/chord/span, CG, inventory mass and diagonal point-mass inertia
  come from the C builder. These remain screening approximations, not measured
  B construction properties or full coupled inertia.
- Callers must supply the installed body stations and the explicit neutral
  state of every control. Body extent must match C's body-length parameter.
  No body is invented from the supplied mass.
- All six A binding identities cover geometry, hardware/controls/inventory,
  mission, process, calibration and implementation. Archive/geometry/model
  revalidation rejects missing, stale, altered or mismatched contexts.
- The v1 contract has NACA codes, not custom foil coordinates or deflected
  controls. Default swept-family reflex and any nonzero control deflection are
  rejected at the export boundary rather than approximated away. Zero-reflex
  variants can be represented, but are not admitted by that fact.
- `admit` preserves explicit missing family qualification, control authority,
  dynamics, interaction/stall and construction gates. A's production lifting
  solver likewise continues rejecting unqualified family identities.

`structural_case` transfers revision-bound rigid steady loads into A/B's actual
`StructuralLoadCase`. `family_reactions` dispatches every installed lifting
member through B's vector/couple consumer. The new
`structural_adapter.consume_lifting_surface` preserves the real role, actual
member span, both physical half-wing loads and explicit root references. B's
existing `consume_loads` retains its conventional-wing checks and behavior.

Each member retains diagnostics for omitted surfaces; the family wrapper
accounts for all members and translates their moments back to the common CG to
check total force/moment conservation. Section couples, drag and lateral forces
remain intact. Load factors are applied once. Roots are load-transfer reference
points, not claimed measured attachments. Parent-style replay reconstructs
reaction reports and rejects promoted states or changed evidence. A valid replay
still returns false for physical admission.

The first focused C/A test run passed **20 tests**, with one native skip. The
focused C/B adapter and material/support replay suite passed **28 tests** before
the additional parent-style family replay assertion. All of these integration
fixtures are analytical and explicitly distinct from native family qualification.

B's actual construction identity is
`bonded_shaped_wing_riveted_keel_prototype_v2`. It is a narrow flat, unswept,
untwisted five-station NACA 0012 prototype with a 20 mm root cutback, not a
construction that can be copied onto swept, canard or tandem aircraft. None of
its material/support exports establish correspondence to C's aerodynamic
geometry. Family attachment paths, body/inertial/launch/gust/landing cases,
multi-axis strength/sizing, measured full-aircraft properties and fixed-revision
delivered trim remain required.

Read the imported `cem-task-a-handoff.md` and
`cem-task-b-qualification-handoff.md` before attempting qualification. This
instance has no selected native XFOIL/AVL binaries and lacks Fortran/CMake/X11
build prerequisites. No fresh independent native A/B qualification is claimed.
The earlier AeroSandbox C diagnostics remain available with their stated scope.

Final integrated validation: **450 passed, 8 skipped** in 233.45 seconds using
the combined CEM/glider/UI/AVL/XFOIL command above, with one existing Altair UI
deprecation warning. The eight skips require native XFOIL/AVL binaries.
Dependency compatibility, staged/unstaged diff checks, restarted HTTP metadata,
HTTP 422 unqualified-family gates and UI health passed. Geometry export and
reaction replay tests ran against the actual merged implementations; native
family qualification, physical construction admission, publication and
fresh-task restoration remain unperformed. Commits remain local and unpushed.

Reproduce the reference reports outside the checkout:

```sh
cd /workspace/Mirum
PYTHONPATH=backend OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  MPLCONFIGDIR=/tmp/mirum-matplotlib /workspace/mirum-venv/bin/python - <<'PY'
from pathlib import Path
from cem.core import canonical_json
from cem.glider.families import archive, build
from cem.glider.families.diagnostics import Conditions, diagnose
out = Path('/tmp/mirum-family-native')
out.mkdir(exist_ok=True)
conditions = Conditions(coarse_resolution=6, fine_resolution=9)
(out/'conditions.json').write_text(canonical_json(conditions))
for family, x in [('tailless_swept', .22), ('canard', .30), ('tandem', .10)]:
    params = build(family).parameters
    params['inventory']['equipment_x_m'] = x
    record = archive(build(family, params))
    (out/(family+'-archive.json')).write_text(canonical_json(record))
    (out/(family+'-report.json')).write_text(canonical_json(diagnose(record, conditions)))
PY
```

The CLI also accepts exact archives and Conditions JSON. Its output must be a
new file so existing evidence is not overwritten:

```sh
PYTHONPATH=backend OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  MPLCONFIGDIR=/tmp/mirum-matplotlib /workspace/mirum-venv/bin/python \
  -m cem.glider.families.diagnostics tailless_swept \
  --archive /tmp/mirum-family-native/tailless_swept-archive.json \
  --conditions /tmp/mirum-family-native/conditions.json \
  --output /tmp/mirum-family-native/tailless-cli.json
```

The CLI was executed and its full JSON matched the direct invocation exactly.
The focused native/family/API test run completed **71 passed**. Native
diagnostics strengthen development evidence; Task A/B qualification and final
Task C release acceptance remain outstanding.

The expanded combined regression run completed **342 passed, 7 skipped** in
141.22 seconds, with the same existing UI deprecation warning. Dependency
compatibility and `git diff --check` passed. After restarting the API, HTTP
metadata returned 200 and retained all unqualified production gates. Neither
deployment nor publication/fresh-task restoration was performed. The commits
are local; remote reproduction of unpublished commits is not established.
